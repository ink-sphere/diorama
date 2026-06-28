"""Tests for the ebook structure-extraction pipeline.

The deterministic core (parser, slicer, validation, pattern expansion) is tested
directly. The agent loop is exercised with a scripted ``FakeModel`` so no network
or API keys are needed.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any

import pytest

from diorama.ebook import (
    Block,
    EbookContext,
    EbookLoaderAgent,
    EbookStructure,
    build_ebook_tools,
    build_structure,
    validate_tree,
)
from diorama.ebook.tools import SubmitStructureTool

_BOOKS = os.path.join(os.path.dirname(os.path.dirname(__file__)), "books")
DRACULA = os.path.join(_BOOKS, "dracula.epub")
ILIAD = os.path.join(_BOOKS, "iliad.epub")


def _ctx_from_texts(texts: list[tuple[str, str]]) -> EbookContext:
    """Build a synthetic context from ``(tag, text)`` pairs (heading auto-detected)."""
    from diorama.ebook.context import _is_heading_candidate

    blocks = [
        Block(
            block_id=i,
            spine_index=0,
            tag=tag,
            text=text,
            is_heading_candidate=_is_heading_candidate(tag, [], None, text),
        )
        for i, (tag, text) in enumerate(texts)
    ]
    return EbookContext(source_path="synthetic", title="T", blocks=blocks, toc=[])


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not os.path.exists(DRACULA), reason="sample epub missing")
def test_parse_dracula_blocks_and_toc():
    ctx = EbookContext.parse(DRACULA)
    assert ctx.title == "Dracula"
    assert ctx.total_blocks > 1000
    # block ids are contiguous 0..N-1
    assert [b.block_id for b in ctx.blocks] == list(range(ctx.total_blocks))
    # ToC chapter entries resolved to real blocks
    titles = {e.title: e.block_id for e in ctx.toc}
    chapter_i = next(v for k, v in titles.items() if k.startswith("CHAPTER I "))
    assert isinstance(chapter_i, int) and 0 < chapter_i < ctx.total_blocks
    # that block really is the chapter heading
    assert "CHAPTER I" in ctx.blocks[chapter_i].text


@pytest.mark.skipif(not os.path.exists(ILIAD), reason="sample epub missing")
def test_parse_iliad_has_book_headings():
    ctx = EbookContext.parse(ILIAD)
    assert ctx.total_blocks > 1000
    book_entries = [e for e in ctx.toc if e.title.upper().startswith("BOOK ")]
    assert len(book_entries) >= 20  # BOOK I .. BOOK XXIV
    assert all(e.block_id is not None for e in book_entries)


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
def test_validate_rejects_disordered_and_bad_blocks():
    ctx = _ctx_from_texts([("h2", "A"), ("p", "x"), ("h2", "B"), ("p", "y")])
    bad = [
        {"level_type": "c", "title": "B", "kind": "narrative", "start_block_id": 2},
        {"level_type": "c", "title": "A", "kind": "narrative", "start_block_id": 0},
    ]
    errors = validate_tree(bad, ctx)
    assert any("strictly increase" in e for e in errors)


def test_validate_rejects_bad_kind_and_range():
    ctx = _ctx_from_texts([("h2", "A"), ("p", "x")])
    bad = [{"level_type": "c", "title": "A", "kind": "nope", "start_block_id": 99}]
    errors = validate_tree(bad, ctx)
    assert any("out of range" in e for e in errors)
    assert any("kind" in e for e in errors)


def test_validate_rejects_children_and_pattern_together():
    ctx = _ctx_from_texts([("h2", "A"), ("p", "x")])
    bad = [
        {
            "level_type": "c",
            "title": "A",
            "kind": "narrative",
            "start_block_id": 0,
            "children": [
                {
                    "level_type": "d",
                    "title": "x",
                    "kind": "narrative",
                    "start_block_id": 1,
                }
            ],
            "child_pattern": {"level_type": "d", "regex": "x"},
        }
    ]
    errors = validate_tree(bad, ctx)
    assert any("not both" in e for e in errors)


def test_validate_rejects_bad_regex():
    ctx = _ctx_from_texts([("h2", "A"), ("p", "x")])
    bad = [
        {
            "level_type": "c",
            "title": "A",
            "kind": "narrative",
            "start_block_id": 0,
            "child_pattern": {"level_type": "d", "regex": "([unclosed"},
        }
    ]
    assert any("invalid regex" in e for e in validate_tree(bad, ctx))


# --------------------------------------------------------------------------- #
# Slicing + coverage
# --------------------------------------------------------------------------- #
def test_build_structure_full_coverage_and_preamble():
    ctx = _ctx_from_texts(
        [
            ("h1", "ACT I"),  # 0  internal node start
            ("p", "intro line"),  # 1  preamble of ACT I
            ("h2", "SCENE 1"),  # 2  child
            ("p", "scene one text"),  # 3
            ("h2", "SCENE 2"),  # 4  child
            ("p", "scene two text"),  # 5
        ]
    )
    nodes = [
        {
            "level_type": "act",
            "title": "ACT I",
            "kind": "narrative",
            "start_block_id": 0,
            "number": 1,
            "children": [
                {
                    "level_type": "scene",
                    "title": "SCENE 1",
                    "kind": "narrative",
                    "start_block_id": 2,
                },
                {
                    "level_type": "scene",
                    "title": "SCENE 2",
                    "kind": "narrative",
                    "start_block_id": 4,
                },
            ],
        }
    ]
    assert validate_tree(nodes, ctx) == []
    st = build_structure(ctx, nodes)
    assert st.coverage.covered is True
    assert st.coverage.assigned_blocks == 6
    act = st.root[0]
    assert act.level_type == "act" and act.number == 1
    # preamble is the ACT heading + intro line, before the first scene
    assert "intro line" in act.preamble_text
    assert act.children[0].text and "scene one text" in act.children[0].text
    assert st.level_types == ["act", "scene"]


def test_child_pattern_expands_to_instances_with_numbers():
    ctx = _ctx_from_texts(
        [
            ("h1", "Parva One"),  # 0
            ("p", "अध्याय 1"),  # 1
            ("p", "verse a"),  # 2
            ("p", "अध्याय 2"),  # 3
            ("p", "verse b"),  # 4
            ("p", "अध्याय 3"),  # 5
            ("p", "verse c"),  # 6
        ]
    )
    nodes = [
        {
            "level_type": "parva",
            "title": "Parva One",
            "kind": "narrative",
            "start_block_id": 0,
            "child_pattern": {"level_type": "adhyaya", "regex": r"^अध्याय\s+(\d+)"},
        }
    ]
    st = build_structure(ctx, nodes)
    parva = st.root[0]
    assert [c.level_type for c in parva.children] == ["adhyaya"] * 3
    assert [c.number for c in parva.children] == [1, 2, 3]
    assert st.coverage.covered is True
    # the parva's own heading does not become a child
    assert parva.children[0].start_block_id == 1


def test_gap_reported_when_front_matter_excluded():
    ctx = _ctx_from_texts(
        [("p", "cover"), ("p", "more cover"), ("h2", "CHAPTER I"), ("p", "story")]
    )
    nodes = [
        {
            "level_type": "chapter",
            "title": "CHAPTER I",
            "kind": "narrative",
            "start_block_id": 2,
        }
    ]
    st = build_structure(ctx, nodes)
    assert st.coverage.covered is False
    assert (0, 2) in st.coverage.gaps


# --------------------------------------------------------------------------- #
# Tool schema
# --------------------------------------------------------------------------- #
def test_submit_tool_schema_is_recursive_and_hides_context():
    ctx = _ctx_from_texts([("h2", "A")])
    schema = SubmitStructureTool(context=ctx).to_json_schema()
    params = schema["function"]["parameters"]
    assert "$defs" in params and "node" in params["$defs"]
    # the bound context must not leak into the LLM-facing schema
    assert "context" not in params["properties"]


async def test_overview_tool_runs():
    ctx = _ctx_from_texts([("h2", "A"), ("p", "x")])
    tools = {t.tool_name: t for t in build_ebook_tools(ctx)}
    out = await tools["get_overview"].forward()
    assert out["total_blocks"] == 2
    assert out["heading_candidate_count"] == 1


# --------------------------------------------------------------------------- #
# End-to-end agent loop (scripted FakeModel, no network)
# --------------------------------------------------------------------------- #
def _tool_call(call_id: str, name: str, arguments: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def _response(content: str | None = None, tool_calls: list | None = None):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    finish = "tool_calls" if tool_calls else "stop"
    choice = SimpleNamespace(message=message, finish_reason=finish)
    return SimpleNamespace(
        choices=[choice], usage={"prompt_tokens": 1, "completion_tokens": 1}
    )


class FakeModel:
    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.cumulative: dict[str, float] = {"cost_usd": 0.0, "total_tokens": 0.0}

    async def acompletion(self, messages, tools=None, stream: bool = False):
        assert self._responses, "FakeModel ran out of scripted responses"
        return self._responses.pop(0)

    def record_usage(self, usage) -> dict:
        self.cumulative["cost_usd"] += 0.001
        self.cumulative["total_tokens"] += 2
        return {"total_tokens": 2, "cost_usd": 0.001}


@pytest.mark.skipif(not os.path.exists(DRACULA), reason="sample epub missing")
async def test_load_end_to_end_with_fake_model():
    # Script: peek overview, then submit a minimal valid one-node structure.
    nodes_json = (
        '{"nodes": [{"level_type": "book", "title": "Dracula", '
        '"kind": "narrative", "start_block_id": 0}]}'
    )
    model = FakeModel(
        [
            _response(tool_calls=[_tool_call("c1", "get_overview", "{}")]),
            _response(tool_calls=[_tool_call("c2", "submit_structure", nodes_json)]),
            _response(content="Done."),
        ]
    )
    agent = EbookLoaderAgent(model=model)
    structure = await agent.load(DRACULA)

    assert isinstance(structure, EbookStructure)
    assert structure.title == "Dracula"
    assert structure.root[0].level_type == "book"
    # one node from block 0 covers the whole book
    assert structure.coverage.covered is True
    assert structure.cost_usd == pytest.approx(0.003)


@pytest.mark.skipif(not os.path.exists(DRACULA), reason="sample epub missing")
async def test_load_raises_when_no_structure_submitted():
    from diorama.ebook import EbookLoadError

    model = FakeModel([_response(content="I give up.")])
    agent = EbookLoaderAgent(model=model)
    with pytest.raises(EbookLoadError):
        await agent.load(DRACULA)
