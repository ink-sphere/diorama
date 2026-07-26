"""Tests for diorama.ebook: EPUB parsing and structure-tree slicing.

Fully offline and deterministic — no LLM involved. Parser tests run against the
real sample EPUBs in books/; slicer tests use small in-memory fixtures so edge
cases (gaps, overlaps, segmentation) are exact and independent of book content.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from diorama.ebook import EbookContext, build_structure, validate_tree
from diorama.ebook.models import Block

BOOKS_DIR = Path(__file__).resolve().parent.parent / "books"
ALICE = BOOKS_DIR / "alice-in-wonderland.epub"
AS_YOU_LIKE_IT = BOOKS_DIR / "as-you-like-it.epub"

pytestmark = pytest.mark.skipif(
    not ALICE.exists() or not AS_YOU_LIKE_IT.exists(),
    reason="sample EPUBs not present in books/",
)


def _make_context(specs: list[tuple[str, str | None]]) -> EbookContext:
    """Build a synthetic EbookContext from (text, tag) pairs, bypassing EPUB parsing."""
    blocks = [
        Block(id=i, text=text, tag=tag, spine_index=0)
        for i, (text, tag) in enumerate(specs)
    ]
    return EbookContext(
        path=Path("synthetic.epub"), title="Synthetic", author=None, blocks=blocks
    )


# --------------------------------------------------------------------------- #
# Parser — real EPUBs
# --------------------------------------------------------------------------- #
def test_parse_alice_basic():
    ctx = EbookContext.parse(ALICE)

    assert ctx.title == "Alice's Adventures in Wonderland"
    assert ctx.author == "Lewis Carroll"
    assert ctx.total_blocks > 500
    assert [b.id for b in ctx.blocks] == list(range(ctx.total_blocks))

    headings = ctx.headings()
    assert headings
    assert all(h.tag is not None for h in headings)
    assert any("CHAPTER I" in h.text for h in headings)


def test_parse_alice_toc_anchors_resolve():
    ctx = EbookContext.parse(ALICE)

    assert ctx.toc
    chapter_entries = [e for e in ctx.toc if e.title.startswith("CHAPTER")]
    assert chapter_entries
    for entry in chapter_entries:
        assert entry.block_id is not None
        assert entry.matched_by == "anchor"
        assert entry.title.split(".")[0] in ctx.blocks[entry.block_id].text


def test_parse_as_you_like_it_nested_headings():
    ctx = EbookContext.parse(AS_YOU_LIKE_IT)

    headings = ctx.headings()
    acts = [h for h in headings if h.text.startswith("ACT ")]
    scenes = [h for h in headings if h.text.startswith("SCENE ")]
    assert len(acts) == 5
    assert len(scenes) >= 20
    # Every scene heading appears after its enclosing act heading and before the next.
    assert acts[0].id < scenes[0].id < acts[1].id


def test_blocks_in_range():
    ctx = EbookContext.parse(ALICE)
    chunk = ctx.blocks_in_range(10, 12)
    assert [b.id for b in chunk] == [10, 11, 12]


def test_blocks_in_range_out_of_bounds_raises():
    ctx = EbookContext.parse(ALICE)
    with pytest.raises(ValueError):
        ctx.blocks_in_range(-1, 5)
    with pytest.raises(ValueError):
        ctx.blocks_in_range(0, ctx.total_blocks)
    with pytest.raises(ValueError):
        ctx.blocks_in_range(5, 2)


def test_search_substring_and_regex():
    ctx = EbookContext.parse(ALICE)

    substring_hits = ctx.search("Rabbit-Hole")
    assert substring_hits
    assert all("rabbit-hole" in b.text.lower() for b in substring_hits)

    regex_hits = ctx.search(r"CHAPTER [IVX]+\.", regex=True, limit=100)
    assert len(regex_hits) >= 10


# --------------------------------------------------------------------------- #
# Slicer — validate_tree
# --------------------------------------------------------------------------- #
def test_validate_tree_flat_coverage_valid():
    ctx = _make_context([("a", None), ("b", None), ("c", None), ("d", None)])
    tree = [
        {"level_type": "chapter", "start_block_id": 0, "end_block_id": 1},
        {"level_type": "chapter", "start_block_id": 2, "end_block_id": 3},
    ]
    assert validate_tree(tree, ctx) == []


def test_validate_tree_reports_gap():
    ctx = _make_context([("a", None)] * 5)
    tree = [{"level_type": "chapter", "start_block_id": 0, "end_block_id": 2}]
    errors = validate_tree(tree, ctx)
    assert any("not assigned" in e for e in errors)


def test_validate_tree_reports_overlap():
    ctx = _make_context([("a", None)] * 5)
    tree = [
        {"level_type": "chapter", "start_block_id": 0, "end_block_id": 3},
        {"level_type": "chapter", "start_block_id": 2, "end_block_id": 4},
    ]
    errors = validate_tree(tree, ctx)
    assert any("more than one node" in e for e in errors)


def test_validate_tree_out_of_bounds():
    ctx = _make_context([("a", None)] * 3)
    tree = [{"level_type": "chapter", "start_block_id": 0, "end_block_id": 10}]
    errors = validate_tree(tree, ctx)
    assert any("out of bounds" in e for e in errors)


def test_validate_tree_start_after_end():
    ctx = _make_context([("a", None)] * 3)
    tree = [{"level_type": "chapter", "start_block_id": 2, "end_block_id": 0}]
    errors = validate_tree(tree, ctx)
    assert any("start_block_id > end_block_id" in e for e in errors)


def test_validate_tree_children_must_span_parent_range():
    ctx = _make_context([("a", None)] * 6)
    tree = [
        {
            "level_type": "act",
            "start_block_id": 0,
            "end_block_id": 5,
            "children": [
                {"level_type": "scene", "start_block_id": 1, "end_block_id": 3},
                {"level_type": "scene", "start_block_id": 4, "end_block_id": 5},
            ],
        }
    ]
    errors = validate_tree(tree, ctx)
    assert any("first child does not start" in e for e in errors)


def test_validate_tree_children_gap_reported():
    ctx = _make_context([("a", None)] * 6)
    tree = [
        {
            "level_type": "act",
            "start_block_id": 0,
            "end_block_id": 5,
            "children": [
                {"level_type": "scene", "start_block_id": 0, "end_block_id": 2},
                {"level_type": "scene", "start_block_id": 4, "end_block_id": 5},
            ],
        }
    ]
    errors = validate_tree(tree, ctx)
    assert any("gap/overlap" in e for e in errors)


def test_validate_tree_malformed_node():
    ctx = _make_context([("a", None)] * 3)
    errors = validate_tree([{"level_type": "chapter"}], ctx)
    assert errors and "malformed node" in errors[0]


# --------------------------------------------------------------------------- #
# Slicer — child_pattern
# --------------------------------------------------------------------------- #
def _scene_context() -> EbookContext:
    return _make_context(
        [
            ("Stage directions before any scene.", None),  # 0 preamble
            ("SCENE I. An Orchard.", "h3"),  # 1
            ("Orlando speaks.", None),  # 2
            ("SCENE II. A Lawn.", "h3"),  # 3
            ("Rosalind speaks.", None),  # 4
            ("Celia speaks.", None),  # 5
        ]
    )


def test_child_pattern_expands_with_preamble():
    ctx = _scene_context()
    tree = [
        {
            "level_type": "act",
            "number": "I",
            "start_block_id": 0,
            "end_block_id": 5,
            "child_pattern": r"^SCENE\s+([IVXLCDM]+)\.",
            "child_level_type": "scene",
        }
    ]
    assert validate_tree(tree, ctx) == []
    structure = build_structure(tree, ctx)
    act = structure.root[0]
    assert act.preamble_text == "Stage directions before any scene."
    assert [c.number for c in act.children] == ["I", "II"]
    assert act.children[0].start_block_id == 1
    assert act.children[0].end_block_id == 2
    assert act.children[1].start_block_id == 3
    assert act.children[1].end_block_id == 5
    assert structure.coverage.covered is True
    assert structure.level_types == ["act", "scene"]


def test_child_pattern_no_preamble_when_first_match_at_start():
    ctx = _make_context(
        [("SCENE I. Start.", "h3"), ("text", None), ("SCENE II. Next.", "h3")]
    )
    tree = [
        {
            "level_type": "act",
            "start_block_id": 0,
            "end_block_id": 2,
            "child_pattern": r"^SCENE\s+([IVXLCDM]+)\.",
            "child_level_type": "scene",
        }
    ]
    structure = build_structure(tree, ctx)
    assert structure.root[0].preamble_text is None


def test_child_pattern_no_match_is_an_error():
    ctx = _make_context([("no markers here", None)] * 3)
    tree = [
        {
            "level_type": "act",
            "start_block_id": 0,
            "end_block_id": 2,
            "child_pattern": r"^SCENE\s+([IVXLCDM]+)\.",
            "child_level_type": "scene",
        }
    ]
    errors = validate_tree(tree, ctx)
    assert any("matched no blocks" in e for e in errors)


def test_child_pattern_requires_child_level_type():
    ctx = _scene_context()
    tree = [
        {
            "level_type": "act",
            "start_block_id": 0,
            "end_block_id": 5,
            "child_pattern": r"^SCENE\s+([IVXLCDM]+)\.",
        }
    ]
    errors = validate_tree(tree, ctx)
    assert any("child_level_type" in e for e in errors)


def test_child_pattern_and_children_mutually_exclusive():
    ctx = _scene_context()
    tree = [
        {
            "level_type": "act",
            "start_block_id": 0,
            "end_block_id": 5,
            "child_pattern": r"^SCENE\s+([IVXLCDM]+)\.",
            "child_level_type": "scene",
            "children": [
                {"level_type": "scene", "start_block_id": 0, "end_block_id": 5}
            ],
        }
    ]
    errors = validate_tree(tree, ctx)
    assert any("both 'children' and 'child_pattern'" in e for e in errors)


# --------------------------------------------------------------------------- #
# Slicer — build_structure / segmentation
# --------------------------------------------------------------------------- #
def test_build_structure_leaf_text_and_no_segments_by_default():
    ctx = _make_context([("Para one.", None), ("Para two.", None)])
    tree = [{"level_type": "chapter", "start_block_id": 0, "end_block_id": 1}]
    structure = build_structure(tree, ctx)
    leaf = structure.root[0]
    assert leaf.text == "Para one.\n\nPara two."
    assert leaf.segments is None


def test_build_structure_raises_on_invalid_tree():
    ctx = _make_context([("a", None), ("b", None)])
    tree = [{"level_type": "chapter", "start_block_id": 0, "end_block_id": 0}]
    with pytest.raises(ValueError, match="invalid structure tree"):
        build_structure(tree, ctx)


def test_segmentation_packs_paragraphs_under_limit():
    ctx = _make_context([("A" * 40, None), ("B" * 40, None), ("C" * 40, None)])
    tree = [{"level_type": "chapter", "start_block_id": 0, "end_block_id": 2}]
    structure = build_structure(tree, ctx, max_segment_length=50)
    leaf = structure.root[0]
    assert leaf.segments is not None
    assert all(len(seg) <= 50 for seg in leaf.segments)
    # Reassembling the segments loses only the paragraph separators.
    assert "".join(leaf.segments).replace("\n\n", "") == leaf.text.replace("\n\n", "")


def test_segmentation_splits_overlong_paragraph():
    long_paragraph = " ".join(f"word{i}" for i in range(200))
    ctx = _make_context([(long_paragraph, None)])
    tree = [{"level_type": "chapter", "start_block_id": 0, "end_block_id": 0}]
    structure = build_structure(tree, ctx, max_segment_length=100)
    segments = structure.root[0].segments
    assert segments is not None
    assert len(segments) > 1
    assert all(len(seg) <= 100 for seg in segments)
    assert " ".join(segments).split() == long_paragraph.split()


def test_coverage_stats_on_valid_tree():
    ctx = _make_context([("a", None)] * 10)
    tree = [
        {"level_type": "chapter", "start_block_id": 0, "end_block_id": 4},
        {"level_type": "chapter", "start_block_id": 5, "end_block_id": 9},
    ]
    structure = build_structure(tree, ctx)
    assert structure.coverage.covered is True
    assert structure.coverage.total_blocks == 10
    assert structure.coverage.assigned_blocks == 10
    assert structure.coverage.gaps == []
    assert structure.coverage.overlaps == []
