"""Data models for ebook structure extraction.

The whole pipeline is built around one idea: an EPUB is first flattened into a
list of numbered :class:`Block` s (the agent's coordinate system — *a boundary is
just a* ``block_id``), and the agent hands back a tree whose nodes are anchored to
those block ids. A deterministic slicer then turns that tree into the final
:class:`EbookStructure`.

See ``docs/ebook-loader-agent.md`` for the full design.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

# The four ways a top-level division can relate to the actual story. The agent
# classifies each node so a storybook reader can isolate the narrative without
# discarding prefaces, introductions, or Project Gutenberg boilerplate.
Kind = Literal["narrative", "front_matter", "back_matter", "supplementary"]
KINDS: tuple[str, ...] = ("narrative", "front_matter", "back_matter", "supplementary")


class Block(BaseModel):
    """One addressable unit of body content, in reading order.

    Attributes:
        block_id (int): Globally monotonic index across the whole book (0..N-1,
            contiguous). This is the agent's only coordinate — boundaries are
            expressed as block ids.
        spine_index (int): Which spine item (document) this block came from.
        tag (str): The originating HTML tag (``h1``..``h6``, ``p``, ``div``, ...).
        classes (list[str]): The element's CSS classes (a structural-marker hint).
        element_id (str | None): The element's ``id`` attribute, if any — used to
            resolve ToC anchors back to a block.
        text (str): Normalized plain text of the block.
        is_heading_candidate (bool): Whether this block looks like a structural
            heading/marker. Deliberately generous; the agent makes the final call.
    """

    block_id: int
    spine_index: int
    tag: str
    classes: list[str] = []
    element_id: str | None = None
    text: str
    is_heading_candidate: bool = False


class TocEntry(BaseModel):
    """One Table-of-Contents entry, resolved into the block coordinate system.

    Attributes:
        title (str): The ToC label as printed in the book.
        block_id (int | None): The block this entry points at, or None if the
            anchor could not be resolved to any block.
        href (str | None): The raw ``file#fragment`` href from the EPUB.
        children (list[TocEntry]): Nested ToC entries.
    """

    title: str
    block_id: int | None = None
    href: str | None = None
    children: list["TocEntry"] = []


class StructureNode(BaseModel):
    """One node of the final, sliced hierarchy.

    Attributes:
        level_type (str): The book's own name for this level (``chapter``, ``act``,
            ``scene``, ``parva``, ``adhyaya``, ...).
        kind (Kind): How this node relates to the narrative.
        title (str): The raw heading text.
        clean_title (str | None): A normalized title with the leading ordinal
            stripped, when available.
        number (int | None): The parsed ordinal of this unit, when present.
        start_block_id (int): The block where this unit begins (its boundary).
        end_block_id (int): One past the last block of this unit (exclusive).
        preamble_text (str | None): For internal nodes, the text between this
            node's start and its first child (e.g. the Iliad's ``ARGUMENT`` before
            the verse, or text under ``ACT I`` before ``SCENE 1``).
        text (str | None): For leaf nodes, the full text of the unit.
        children (list[StructureNode]): Child units, if any.
    """

    level_type: str
    kind: Kind = "narrative"
    title: str
    clean_title: str | None = None
    number: int | None = None
    start_block_id: int
    end_block_id: int
    preamble_text: str | None = None
    text: str | None = None
    children: list["StructureNode"] = []


class CoverageReport(BaseModel):
    """Whether every block landed in exactly one unit.

    Attributes:
        covered (bool): True when there are no gaps and no overlaps.
        total_blocks (int): The book's total block count.
        assigned_blocks (int): How many blocks ended up inside the tree.
        gaps (list[tuple[int, int]]): Half-open block ranges assigned to nothing
            (typically front/back matter the agent chose to exclude).
        overlaps (list[tuple[int, int]]): Block ranges claimed by more than one
            leaf (should be empty for a valid tree).
    """

    covered: bool
    total_blocks: int
    assigned_blocks: int
    gaps: list[tuple[int, int]] = []
    overlaps: list[tuple[int, int]] = []


class EbookStructure(BaseModel):
    """The end product of one :meth:`EbookLoaderAgent.load` call.

    Attributes:
        source_path (str): Path to the EPUB this was extracted from.
        title (str | None): The book title, if the EPUB declared one.
        level_types (list[str]): The discovered hierarchy vocabulary, in order of
            first appearance (e.g. ``["parva", "upa-parva", "adhyaya"]``).
        root (list[StructureNode]): The top-level nodes of the hierarchy.
        coverage (CoverageReport): Block-coverage diagnostics.
        usage (dict): Cumulative token/cost usage from the agent run.
        cost_usd (float): Total USD cost of the agent run.
    """

    source_path: str
    title: str | None = None
    level_types: list[str] = []
    root: list[StructureNode] = []
    coverage: CoverageReport
    usage: dict[str, Any] = {}
    cost_usd: float = 0.0


# --------------------------------------------------------------------------- #
# JSON Schema for the terminal ``submit_structure`` tool.
#
# Recursive via ``$defs``/``$ref``. Each node carries EITHER explicit ``children``
# OR a ``child_pattern`` (a repeating marker the deterministic expander applies) —
# never both. Only ``level_type``, ``title``, ``kind`` and ``start_block_id`` are
# required; the rest are light normalization the agent fills when it can.
# --------------------------------------------------------------------------- #
_KIND_SCHEMA = {"type": "string", "enum": list(KINDS)}

_CHILD_PATTERN_SCHEMA = {
    "type": "object",
    "description": (
        "Describe a repeating sub-level by the marker that starts each instance, "
        "instead of listing every child. The expander finds every block matching "
        "`regex` within this node's range and creates one child per match. Use for "
        "deep, regular structure (e.g. hundreds of adhyayas, or every SCENE)."
    ),
    "properties": {
        "level_type": {
            "type": "string",
            "description": "Semantic name for the generated level, e.g. 'scene'.",
        },
        "regex": {
            "type": "string",
            "description": (
                "Python regex matched (via re.search) against each block's text. "
                "An optional first capture group is parsed as the unit number, "
                "e.g. '^SCENE\\\\s+([IVXLC]+)' or '^अध्याय\\\\s+(\\\\d+)'."
            ),
        },
        "kind": _KIND_SCHEMA,
    },
    "required": ["level_type", "regex"],
    "additionalProperties": False,
}

_NODE_SCHEMA = {
    "type": "object",
    "properties": {
        "level_type": {
            "type": "string",
            "description": (
                "The book's own name for this level (chapter, act, scene, part, "
                "book, canto, letter, parva, adhyaya, ...). Prefer the book's "
                "vocabulary over generic names."
            ),
        },
        "title": {
            "type": "string",
            "description": "The raw heading text for this unit.",
        },
        "clean_title": {
            "type": ["string", "null"],
            "description": "Title with the leading ordinal/label stripped, if useful.",
        },
        "number": {
            "type": ["integer", "null"],
            "description": "The unit's ordinal, if it has one (e.g. 4 for 'CHAPTER IV').",
        },
        "kind": _KIND_SCHEMA,
        "start_block_id": {
            "type": "integer",
            "description": "The block id where this unit begins (its boundary).",
        },
        "children": {
            "type": "array",
            "description": "Explicit child units. Omit if using child_pattern.",
            "items": {"$ref": "#/$defs/node"},
        },
        "child_pattern": _CHILD_PATTERN_SCHEMA,
    },
    "required": ["level_type", "title", "kind", "start_block_id"],
    "additionalProperties": False,
}

SUBMIT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "nodes": {
            "type": "array",
            "description": "The ordered top-level nodes of the book's hierarchy.",
            "items": {"$ref": "#/$defs/node"},
        }
    },
    "required": ["nodes"],
    "additionalProperties": False,
    "$defs": {"node": _NODE_SCHEMA},
}
