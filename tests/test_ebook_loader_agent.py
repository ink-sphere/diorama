"""Tests for diorama.agents.ebook_loader.EbookLoaderAgent.

Runs entirely against a :class:`FakeModel` (see tests/fakes.py) so no network / API
keys are required. EPUB parsing itself is exercised against the real sample books in
books/, but every LLM turn is scripted.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from diorama.agents import EbookLoaderAgent, EbookLoaderError
from diorama.agents.ebook_loader import (
    GetOverviewTool,
    GetTocTool,
    ListHeadingsTool,
    ReadBlocksTool,
    SearchBlocksTool,
    SubmitStructureTool,
    _LoadState,
    render_load_prompt,
)
from diorama.ebook import EbookContext
from diorama.ebook.models import Block
from tests.fakes import FakeModel, response as _response, tool_call as _tool_call

BOOKS_DIR = Path(__file__).resolve().parent.parent / "books"
ALICE = BOOKS_DIR / "alice-in-wonderland.epub"

pytestmark = pytest.mark.skipif(
    not ALICE.exists(), reason="sample EPUB not present in books/"
)


def _synthetic_context() -> EbookContext:
    specs = [
        ("CHAPTER ONE", "h1"),
        ("It was a dark and stormy night.", None),
        ("The wind howled.", None),
        ("CHAPTER TWO", "h1"),
        ("The next morning was calm.", None),
    ]
    blocks = [
        Block(id=i, text=t, tag=g, spine_index=0) for i, (t, g) in enumerate(specs)
    ]
    return EbookContext(
        path=Path("synthetic.epub"), title="A Tale", author="Ann Onymous", blocks=blocks
    )


def _full_coverage_call(call_id: str, total_blocks: int, level_type: str = "book"):
    tree = {
        "root": [
            {
                "level_type": level_type,
                "start_block_id": 0,
                "end_block_id": total_blocks - 1,
            }
        ]
    }
    return _tool_call(call_id, "submit_structure", json.dumps(tree))


# --------------------------------------------------------------------------- #
# Individual tools
# --------------------------------------------------------------------------- #
async def test_get_overview_tool():
    ctx = _synthetic_context()
    state = _LoadState(context=ctx, max_segment_length=None)
    result = await GetOverviewTool(state=state).forward()
    assert result["title"] == "A Tale"
    assert result["author"] == "Ann Onymous"
    assert result["total_blocks"] == 5
    assert result["heading_count"] == 2


async def test_get_toc_tool_empty_toc():
    ctx = _synthetic_context()
    state = _LoadState(context=ctx, max_segment_length=None)
    result = await GetTocTool(state=state).forward()
    assert result.is_error is False
    assert "no embedded table of contents" in result.text


async def test_list_headings_tool():
    ctx = _synthetic_context()
    state = _LoadState(context=ctx, max_segment_length=None)
    result = await ListHeadingsTool(state=state).forward()
    assert [h["block_id"] for h in result] == [0, 3]


async def test_read_blocks_tool_range_and_cap():
    ctx = _synthetic_context()
    state = _LoadState(context=ctx, max_segment_length=None)
    tool = ReadBlocksTool(state=state)

    text = await tool.forward(start_block_id=1, end_block_id=2)
    assert "[Block 1]" in text and "[Block 2]" in text

    capped = await tool.forward(start_block_id=0, end_block_id=999)
    assert capped.is_error is True
    assert "cap" in capped.text


async def test_search_blocks_tool():
    ctx = _synthetic_context()
    state = _LoadState(context=ctx, max_segment_length=None)
    tool = SearchBlocksTool(state=state)

    result = await tool.forward(query="stormy")
    assert result == [{"block_id": 1, "text": "It was a dark and stormy night."}]

    bad = await tool.forward(query="[", regex=True)
    assert bad.is_error is True


async def test_submit_structure_tool_rejects_invalid_and_accepts_valid():
    ctx = _synthetic_context()
    state = _LoadState(context=ctx, max_segment_length=None)
    tool = SubmitStructureTool(state=state)

    bad = await tool.forward(
        root=[{"level_type": "chapter", "start_block_id": 0, "end_block_id": 1}]
    )
    assert bad.is_error is True
    assert state.result is None
    assert state.last_errors

    good = await tool.forward(
        root=[{"level_type": "chapter", "start_block_id": 0, "end_block_id": 4}]
    )
    assert good.is_error is False
    assert good.terminate is True
    assert state.result is not None
    assert state.result.coverage.covered is True
    assert state.last_errors == []


# --------------------------------------------------------------------------- #
# EbookLoaderAgent.load() end to end (scripted model)
# --------------------------------------------------------------------------- #
async def test_load_happy_path_single_submit():
    total = EbookContext.parse(ALICE).total_blocks
    model = FakeModel([_response(tool_calls=[_full_coverage_call("c1", total)])])

    agent = EbookLoaderAgent(model=model)
    structure = await agent.load(ALICE)

    assert structure.title == "Alice's Adventures in Wonderland"
    assert structure.coverage.covered is True
    assert structure.root[0].level_type == "book"
    assert structure.cost_usd > 0
    assert len(model.calls) == 1


async def test_load_records_every_call_against_the_book_and_run():
    """The loader stamps its ledger rows so the dashboard can attribute the spend."""
    total = EbookContext.parse(ALICE).total_blocks
    model = FakeModel([_response(tool_calls=[_full_coverage_call("c1", total)])])
    rows: list = []

    agent = EbookLoaderAgent(
        model=model, usage_sink=rows.append, book_id="book1", run_id="run1"
    )
    await agent.load(ALICE)

    assert [(r.book_id, r.run_id, r.agent_id) for r in rows] == [
        ("book1", "run1", "ebook_loader")
    ]


async def test_load_without_a_sink_records_nothing():
    """Cost tracking is opt-in; a script or test that doesn't want it gets no ledger."""
    total = EbookContext.parse(ALICE).total_blocks
    model = FakeModel([_response(tool_calls=[_full_coverage_call("c1", total)])])

    await EbookLoaderAgent(model=model).load(ALICE)
    assert model.usage_sink is None


async def test_each_loader_gets_its_own_run_id_when_none_is_supplied():
    """Two runs over the same book must be distinguishable in the ledger."""
    first = EbookLoaderAgent(model=FakeModel([]))
    second = EbookLoaderAgent(model=FakeModel([]))
    assert first._usage_labels["run_id"] != second._usage_labels["run_id"]


async def test_load_retries_after_rejected_structure():
    total = EbookContext.parse(ALICE).total_blocks
    bad_tree = {
        "root": [{"level_type": "chapter", "start_block_id": 0, "end_block_id": 1}]
    }
    model = FakeModel(
        [
            _response(
                tool_calls=[_tool_call("c1", "submit_structure", json.dumps(bad_tree))]
            ),
            _response(tool_calls=[_full_coverage_call("c2", total)]),
        ]
    )

    agent = EbookLoaderAgent(model=model)
    structure = await agent.load(ALICE)

    assert structure.coverage.covered is True
    assert len(model.calls) == 2
    # The second call's tool result must carry the rejection, so the agent can see why.
    second_call_messages = model.calls[1]["messages"]
    tool_messages = [m for m in second_call_messages if m.get("role") == "tool"]
    assert any("rejected" in m["content"] for m in tool_messages)


async def test_load_raises_when_model_never_submits():
    model = FakeModel(
        [_response(content="I looked around but found nothing to report.")]
    )

    agent = EbookLoaderAgent(model=model)
    with pytest.raises(EbookLoaderError, match="did not submit a valid structure"):
        await agent.load(ALICE)


async def test_load_raises_on_max_iterations_without_submission():
    model = FakeModel(
        [_response(tool_calls=[_tool_call("c1", "get_overview", "{}")])], loop_last=True
    )

    agent = EbookLoaderAgent(model=model, max_iterations=2)
    with pytest.raises(EbookLoaderError, match="max_iterations"):
        await agent.load(ALICE)


async def test_load_passes_max_segment_length_through_to_structure():
    total = EbookContext.parse(ALICE).total_blocks
    model = FakeModel([_response(tool_calls=[_full_coverage_call("c1", total)])])

    agent = EbookLoaderAgent(model=model)
    structure = await agent.load(ALICE, max_segment_length=500)

    assert structure.root[0].segments is not None
    assert all(len(seg) <= 500 for seg in structure.root[0].segments)


def test_render_load_prompt_mentions_block_range():
    ctx = _synthetic_context()
    prompt = render_load_prompt(ctx)
    assert "A Tale" in prompt
    assert "Ann Onymous" in prompt
    assert "0..4" in prompt
