"""Tests for scene segmentation: the deterministic slicer and the agent that drives it.

Runs entirely against a :class:`FakeModel` (see tests/fakes.py) — no network, no API
keys. The property that matters most here is that the book's text survives the round
trip byte for byte, so several tests assert on exact string identity rather than on
"looks about right".
"""

from __future__ import annotations

import json

import pytest

from diorama.agents import EbookSceneSegmentationAgent, SceneSegmentationError
from diorama.agents.ebook_scene_segmentation import (
    ReadParagraphsTool,
    SubmitScenesTool,
    _SegmentState,
    render_paragraphs,
    render_segmentation_prompt,
)
from diorama.ebook.models import Coverage, EbookStructure, StructureNode
from diorama.ebook.scenes import (
    PARAGRAPH_SEPARATOR,
    build_scenes,
    iter_leaves,
    join_paragraphs,
    single_scene,
    split_paragraphs,
    validate_scenes,
)
from tests.fakes import FakeModel, response as _response, tool_call as _tool_call

PARAGRAPHS = [
    "The hall was long and low, and lit by a single lamp.",
    '"You are late," said the doctor, without looking up.',
    '"The roads were bad," said Maria.',
    "She sat down and warmed her hands at the fire.",
    "By morning the snow had stopped and the valley was white to the tree line.",
    "Maria walked out to the ridge alone.",
]
TEXT = PARAGRAPH_SEPARATOR.join(PARAGRAPHS)


def _leaf(
    text: str = TEXT,
    *,
    level_type: str = "chapter",
    number: str | None = "1",
    title: str | None = "The Arrival",
    start_block_id: int = 0,
) -> StructureNode:
    paragraphs = split_paragraphs(text)
    return StructureNode(
        level_type=level_type,
        number=number,
        title=title,
        start_block_id=start_block_id,
        end_block_id=start_block_id + max(len(paragraphs) - 1, 0),
        text=text,
    )


def _structure(*leaves: StructureNode) -> EbookStructure:
    total = sum(len(split_paragraphs(leaf.text or "")) for leaf in leaves)
    return EbookStructure(
        title="A Tale",
        author="Ann Onymous",
        level_types=["chapter"],
        root=list(leaves),
        coverage=Coverage(covered=True, total_blocks=total, assigned_blocks=total),
    )


def _submit(call_id: str, bounds: list[tuple[int, int]]):
    payload = {
        "scenes": [{"start_paragraph": s, "end_paragraph": e} for s, e in bounds]
    }
    return _tool_call(call_id, "submit_scenes", json.dumps(payload))


# --------------------------------------------------------------------------- #
# diorama.ebook.scenes — paragraphs
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "text",
    [
        TEXT,
        "",
        "one paragraph only",
        "a\n\nb\n\n\nc",  # a stray extra newline must still round-trip
        "trailing blank line\n\n",
        "  leading space kept  \n\nsecond",
    ],
)
def test_split_paragraphs_round_trips_exactly(text: str):
    """The whole design rests on this: splitting then joining changes nothing."""
    assert join_paragraphs(split_paragraphs(text)) == text


def test_split_paragraphs_on_empty_text_has_no_paragraphs():
    assert split_paragraphs("") == []


def test_iter_leaves_yields_depth_first_reading_order():
    inner = [
        _leaf("scene one", number="i", start_block_id=0),
        _leaf("scene two", number="ii", start_block_id=1),
    ]
    branch = StructureNode(
        level_type="act",
        number="I",
        start_block_id=0,
        end_block_id=1,
        children=inner,
    )
    tail = _leaf("epilogue", number=None, title="Epilogue", start_block_id=2)
    structure = _structure(branch, tail)

    assert [n.text for n in iter_leaves(structure)] == [
        "scene one",
        "scene two",
        "epilogue",
    ]


# --------------------------------------------------------------------------- #
# diorama.ebook.scenes — validation
# --------------------------------------------------------------------------- #
def test_validate_scenes_accepts_an_exact_partition():
    assert (
        validate_scenes(
            [
                {"start_paragraph": 0, "end_paragraph": 3},
                {"start_paragraph": 4, "end_paragraph": 5},
            ],
            6,
        )
        == []
    )


def test_validate_scenes_accepts_string_bounds():
    """Models routinely quote their integers; coerce rather than reject."""
    assert validate_scenes([{"start_paragraph": "0", "end_paragraph": "5"}], 6) == []


def test_validate_scenes_rejects_a_gap():
    errors = validate_scenes(
        [
            {"start_paragraph": 0, "end_paragraph": 2},
            {"start_paragraph": 4, "end_paragraph": 5},
        ],
        6,
    )
    assert any("expected start_paragraph 3" in e for e in errors)


def test_validate_scenes_rejects_an_overlap():
    errors = validate_scenes(
        [
            {"start_paragraph": 0, "end_paragraph": 3},
            {"start_paragraph": 3, "end_paragraph": 5},
        ],
        6,
    )
    assert any("gap/overlap" in e for e in errors)


def test_validate_scenes_rejects_not_starting_at_zero():
    errors = validate_scenes([{"start_paragraph": 1, "end_paragraph": 5}], 6)
    assert any("must start at paragraph 0" in e for e in errors)


def test_validate_scenes_rejects_not_covering_the_tail():
    errors = validate_scenes([{"start_paragraph": 0, "end_paragraph": 4}], 6)
    assert any("must end at paragraph 5" in e for e in errors)


def test_validate_scenes_rejects_out_of_bounds_and_inverted_ranges():
    assert any(
        "out of bounds" in e
        for e in validate_scenes([{"start_paragraph": 0, "end_paragraph": 9}], 6)
    )
    assert any(
        "start_paragraph > end_paragraph" in e
        for e in validate_scenes([{"start_paragraph": 4, "end_paragraph": 2}], 6)
    )


def test_validate_scenes_rejects_empty_and_malformed_input():
    assert validate_scenes([], 6) == ["submit at least one scene"]
    assert any("malformed" in e for e in validate_scenes([{"start_paragraph": 0}], 6))
    assert any("expected an object" in e for e in validate_scenes(["nope"], 6))
    assert validate_scenes([{"start_paragraph": 0, "end_paragraph": 0}], 0) == [
        "there is no text to segment (0 paragraphs)"
    ]


# --------------------------------------------------------------------------- #
# diorama.ebook.scenes — building
# --------------------------------------------------------------------------- #
def test_build_scenes_slices_verbatim_text():
    node = _leaf()
    segmentation = build_scenes(
        [
            {"start_paragraph": 0, "end_paragraph": 3},
            {"start_paragraph": 4, "end_paragraph": 5},
        ],
        split_paragraphs(TEXT),
        node=node,
    )

    assert [(s.start_paragraph, s.end_paragraph) for s in segmentation.scenes] == [
        (0, 3),
        (4, 5),
    ]
    # Every scene is a verbatim substring, and together they reconstruct the node.
    for scene in segmentation.scenes:
        assert scene.text in TEXT
    assert join_paragraphs(s.text for s in segmentation.scenes) == TEXT
    assert segmentation.scenes[0].paragraph_count == 4
    assert segmentation.paragraph_count == 6


def test_build_scenes_copies_the_nodes_identity():
    segmentation = build_scenes(
        [{"start_paragraph": 0, "end_paragraph": 5}],
        split_paragraphs(TEXT),
        node=_leaf(),
    )
    assert segmentation.level_type == "chapter"
    assert segmentation.number == "1"
    assert segmentation.title == "The Arrival"
    assert (segmentation.start_block_id, segmentation.end_block_id) == (0, 5)


def test_build_scenes_refuses_an_invalid_partition():
    with pytest.raises(ValueError, match="invalid scene boundaries"):
        build_scenes(
            [{"start_paragraph": 0, "end_paragraph": 2}], split_paragraphs(TEXT)
        )


def test_single_scene_covers_everything_and_tolerates_empty_text():
    whole = single_scene(split_paragraphs(TEXT), node=_leaf())
    assert len(whole.scenes) == 1
    assert whole.scenes[0].text == TEXT

    empty = single_scene([], node=_leaf(""))
    assert empty.scenes == []
    assert empty.paragraph_count == 0


# --------------------------------------------------------------------------- #
# Prompting
# --------------------------------------------------------------------------- #
def test_render_paragraphs_numbers_from_the_given_offset():
    rendered, count = render_paragraphs(PARAGRAPHS[2:4], start_index=2)
    assert count == 2
    assert rendered.startswith("[P 2] ")
    assert "[P 3] " in rendered


def test_render_paragraphs_truncates_to_the_char_budget_but_never_to_nothing():
    _, count = render_paragraphs(PARAGRAPHS, char_budget=80)
    assert 0 < count < len(PARAGRAPHS)

    _, one = render_paragraphs(PARAGRAPHS, char_budget=1)
    assert one == 1


def test_prompt_inlines_short_sections_whole():
    prompt = render_segmentation_prompt(PARAGRAPHS, node=_leaf(), book_title="A Tale")
    assert "chapter 1: The Arrival" in prompt
    assert 'from "A Tale"' in prompt
    assert "The full text follows." in prompt
    assert "[P 5] " in prompt


def test_prompt_points_a_long_section_at_read_paragraphs(monkeypatch):
    monkeypatch.setattr(
        "diorama.agents.ebook_scene_segmentation._INLINE_CHAR_BUDGET", 80
    )
    prompt = render_segmentation_prompt(PARAGRAPHS, node=_leaf())
    assert "read_paragraphs" in prompt
    assert "[P 5] " not in prompt
    assert "all 6 of them" in prompt


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #
async def test_read_paragraphs_tool_range_cap_and_bounds():
    state = _SegmentState(paragraphs=PARAGRAPHS)
    tool = ReadParagraphsTool(state=state)

    text = await tool.forward(start_paragraph=1, end_paragraph=2)
    assert text.startswith("[P 1] ")
    assert "[P 2] " in text
    assert "[P 0] " not in text

    out_of_range = await tool.forward(start_paragraph=0, end_paragraph=99)
    assert out_of_range.is_error is True
    assert "indices 0..5" in out_of_range.text

    inverted = await tool.forward(start_paragraph=3, end_paragraph=1)
    assert inverted.is_error is True

    long_state = _SegmentState(paragraphs=["p"] * 500)
    capped = await ReadParagraphsTool(state=long_state).forward(
        start_paragraph=0, end_paragraph=499
    )
    assert capped.is_error is True
    assert "cap" in capped.text


async def test_submit_scenes_tool_rejects_invalid_then_accepts_valid():
    state = _SegmentState(paragraphs=PARAGRAPHS, node=_leaf())
    tool = SubmitScenesTool(state=state)

    bad = await tool.forward(scenes=[{"start_paragraph": 0, "end_paragraph": 2}])
    assert bad.is_error is True
    assert bad.terminate is False
    assert state.result is None
    assert state.last_errors

    good = await tool.forward(
        scenes=[
            {"start_paragraph": 0, "end_paragraph": 3},
            {"start_paragraph": 4, "end_paragraph": 5},
        ]
    )
    assert good.is_error is False
    assert good.terminate is True
    assert state.result is not None
    assert len(state.result.scenes) == 2
    assert state.last_errors == []


# --------------------------------------------------------------------------- #
# EbookSceneSegmentationAgent end to end (scripted model)
# --------------------------------------------------------------------------- #
async def test_segment_node_happy_path_leaves_the_text_untouched():
    model = FakeModel([_response(tool_calls=[_submit("c1", [(0, 3), (4, 5)])])])
    node = _leaf()

    segmentation = await EbookSceneSegmentationAgent(model=model).segment_node(node)

    assert [(s.start_paragraph, s.end_paragraph) for s in segmentation.scenes] == [
        (0, 3),
        (4, 5),
    ]
    assert join_paragraphs(s.text for s in segmentation.scenes) == node.text
    assert segmentation.title == "The Arrival"
    assert segmentation.cost_usd > 0
    assert len(model.calls) == 1


async def test_segment_node_retries_after_rejected_boundaries():
    model = FakeModel(
        [
            _response(tool_calls=[_submit("c1", [(0, 2)])]),  # doesn't reach the end
            _response(tool_calls=[_submit("c2", [(0, 2), (3, 5)])]),
        ]
    )

    segmentation = await EbookSceneSegmentationAgent(model=model).segment_node(_leaf())

    assert len(segmentation.scenes) == 2
    assert len(model.calls) == 2


async def test_short_nodes_become_one_scene_without_calling_the_model():
    model = FakeModel([])  # any call would raise "ran out of scripted responses"
    node = _leaf("A single line of front matter.", title="Half-title")

    segmentation = await EbookSceneSegmentationAgent(model=model).segment_node(node)

    assert len(segmentation.scenes) == 1
    assert segmentation.scenes[0].text == node.text
    assert segmentation.cost_usd == 0.0
    assert model.calls == []


async def test_min_paragraphs_zero_always_runs_the_agent():
    model = FakeModel([_response(tool_calls=[_submit("c1", [(0, 0)])])])
    node = _leaf("Just one paragraph.", title="Colophon")

    segmentation = await EbookSceneSegmentationAgent(model=model).segment_node(
        node, min_paragraphs=0
    )

    assert len(model.calls) == 1
    assert segmentation.scenes[0].text == node.text


async def test_segment_node_raises_when_the_run_ends_without_a_submission():
    # Three replies: the first quiet turn, plus the two completion-guard nudges.
    refusal = "I think this is all one scene, really."
    model = FakeModel([_response(content=refusal) for _ in range(3)])

    with pytest.raises(SceneSegmentationError, match="chapter 1: The Arrival"):
        await EbookSceneSegmentationAgent(model=model).segment_node(_leaf())


async def test_segment_structure_walks_every_leaf_and_totals_the_cost():
    long_leaf = _leaf(number="2", title="The Ridge")
    short_leaf = _leaf("A one-paragraph interlude.", number="3", title="Interlude")
    model = FakeModel(
        [
            _response(tool_calls=[_submit("c1", [(0, 3), (4, 5)])]),
            _response(tool_calls=[_submit("c2", [(0, 5)])]),
        ]
    )

    book = await EbookSceneSegmentationAgent(model=model).segment_structure(
        _structure(_leaf(), long_leaf, short_leaf)
    )

    assert [s.title for s in book.segmentations] == [
        "The Arrival",
        "The Ridge",
        "Interlude",
    ]
    assert [len(s.scenes) for s in book.segmentations] == [2, 1, 1]
    assert book.scene_count == 4
    assert book.title == "A Tale"
    # Two model calls, two priced runs; the short leaf never reached the model.
    assert len(model.calls) == 2
    assert book.cost_usd == pytest.approx(2 * FakeModel.CALL_COST_USD)
    assert book.segmentations[2].cost_usd == 0.0


async def test_per_node_cost_is_a_difference_not_the_shared_models_running_total():
    """One model instance spans a book, so cost must be measured per run."""
    model = FakeModel(
        [
            _response(tool_calls=[_submit("c1", [(0, 5)])]),
            _response(tool_calls=[_submit("c2", [(0, 5)])]),
        ]
    )
    agent = EbookSceneSegmentationAgent(model=model)

    first = await agent.segment_node(_leaf())
    second = await agent.segment_node(_leaf(number="2"))

    assert first.cost_usd == pytest.approx(FakeModel.CALL_COST_USD)
    assert second.cost_usd == pytest.approx(FakeModel.CALL_COST_USD)
    assert model.cumulative["cost_usd"] == pytest.approx(2 * FakeModel.CALL_COST_USD)


async def test_segmentation_stamps_every_ledger_row():
    model = FakeModel([_response(tool_calls=[_submit("c1", [(0, 5)])])])
    rows: list = []

    await EbookSceneSegmentationAgent(
        model=model, usage_sink=rows.append, book_id="book1", run_id="run1"
    ).segment_node(_leaf())

    assert [(r.book_id, r.run_id, r.agent_id) for r in rows] == [
        ("book1", "run1", "ebook_scene_segmentation")
    ]


async def test_each_agent_gets_its_own_run_id_when_none_is_supplied():
    first = EbookSceneSegmentationAgent(model=FakeModel([]))
    second = EbookSceneSegmentationAgent(model=FakeModel([]))
    assert first._usage_labels["run_id"] != second._usage_labels["run_id"]


async def test_stream_segment_node_yields_events_then_finalizes():
    model = FakeModel([_response(tool_calls=[_submit("c1", [(0, 3), (4, 5)])])])
    agent = EbookSceneSegmentationAgent(model=model)

    events, finalize = agent.stream_segment_node(_leaf(), book_title="A Tale")
    seen = [event async for event in events]

    assert seen
    segmentation = finalize()
    assert len(segmentation.scenes) == 2
    assert join_paragraphs(s.text for s in segmentation.scenes) == TEXT


async def test_stream_segment_node_refuses_a_node_with_no_text():
    agent = EbookSceneSegmentationAgent(model=FakeModel([]))
    with pytest.raises(ValueError, match="no text to segment"):
        agent.stream_segment_node(_leaf("", title="Blank"))
