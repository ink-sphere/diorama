"""Data models for extracted ebook structure.

:class:`Block` is the coordinate system the loader agent reasons over — every tool
it calls speaks in block ids. :class:`StructureNode` / :class:`EbookStructure` are
the deterministic output the slicer (``diorama.ebook.slicer``) builds once the agent
submits a tree of block-id boundaries; nothing here is agent- or LLM-aware.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Block(BaseModel):
    """One paragraph/heading/list-item-sized unit of extracted EPUB text.

    Attributes:
        id (int): Position in reading order; the agent's coordinate system.
        text (str): Cleaned, whitespace-normalised text of the source element.
        tag (str | None): The source HTML tag when it was a heading (``h1``..``h6``),
            else None.
        spine_index (int): Index of the spine document this block came from.
        element_id (str | None): The source element's ``id`` attribute, if any —
            used to anchor the EPUB's own table of contents to a block.
    """

    id: int
    text: str
    tag: str | None = None
    spine_index: int
    element_id: str | None = None


class TocEntry(BaseModel):
    """One entry from the EPUB's own (publisher-authored) table of contents.

    Attributes:
        title (str): The entry's display title.
        block_id (int | None): Best-effort anchor into ``blocks``. None when no
            block could be resolved at all.
        matched_by (Literal): How ``block_id`` was resolved — an exact element-id
            anchor, a fuzzy title match against headings in the target document, or
            unresolved (falls back to the target document's first block, if known).
        children (list[TocEntry]): Nested entries, in order.
    """

    title: str
    block_id: int | None = None
    matched_by: Literal["anchor", "fuzzy", "unresolved"] = "unresolved"
    children: list["TocEntry"] = Field(default_factory=list)


TocEntry.model_rebuild()


class StructureNode(BaseModel):
    """One node of the hierarchical structure the loader agent discovered.

    A node is either a leaf (``children`` empty; carries ``text``/``segments``) or a
    branch (``children`` non-empty; ``[start_block_id, end_block_id]`` spans exactly
    the union of its children's ranges).

    Attributes:
        level_type (str): Semantic level name chosen by the agent, e.g. ``"chapter"``,
            ``"act"``, ``"scene"``, ``"parva"``.
        number (str | None): The node's number/label as it appears in the book.
        title (str | None): The node's title, if any.
        start_block_id (int): First block (inclusive) under this node.
        end_block_id (int): Last block (inclusive) under this node.
        text (str | None): Leaf nodes only — the joined text of its block range.
        segments (list[str] | None): Leaf nodes only — ``text`` paginated to
            ``max_segment_length``, when segmentation was requested.
        preamble_text (str | None): Text preceding the first auto-detected child when
            this node was expanded via ``child_pattern`` (e.g. stage directions
            before "SCENE I"). None otherwise.
        children (list[StructureNode]): Nested child nodes, in order.
    """

    level_type: str
    number: str | None = None
    title: str | None = None
    start_block_id: int
    end_block_id: int
    text: str | None = None
    segments: list[str] | None = None
    preamble_text: str | None = None
    children: list["StructureNode"] = Field(default_factory=list)

    @property
    def is_leaf(self) -> bool:
        """Whether this node has no children (and therefore carries ``text``)."""
        return not self.children


StructureNode.model_rebuild()


class Coverage(BaseModel):
    """Block-assignment statistics for a submitted structure tree.

    Attributes:
        covered (bool): True when every block from 0..total_blocks-1 is assigned to
            exactly one leaf node (no gaps, no overlaps).
        total_blocks (int): Total number of blocks in the book.
        assigned_blocks (int): Number of blocks assigned to at least one node.
        gaps (list[tuple[int, int]]): Inclusive block-id ranges assigned to nothing.
        overlaps (list[tuple[int, int]]): Inclusive block-id ranges assigned more
            than once.
    """

    covered: bool
    total_blocks: int
    assigned_blocks: int
    gaps: list[tuple[int, int]] = Field(default_factory=list)
    overlaps: list[tuple[int, int]] = Field(default_factory=list)


class EbookStructure(BaseModel):
    """The complete result of loading one EPUB.

    Attributes:
        title (str): Book title (from EPUB metadata, or the filename as a fallback).
        author (str | None): Book author, if declared in metadata.
        level_types (list[str]): Every distinct ``level_type`` used in ``root``.
        root (list[StructureNode]): Top-level nodes of the discovered hierarchy.
        toc (list[TocEntry]): The EPUB's own table of contents, block-anchored.
        coverage (Coverage): Block-assignment statistics for ``root``.
        cost_usd (float): Cumulative LLM spend for the run that produced this
            structure. Set by :class:`~diorama.agents.ebook_loader.EbookLoaderAgent`
            after the agent run completes.
    """

    title: str
    author: str | None = None
    level_types: list[str] = Field(default_factory=list)
    root: list[StructureNode] = Field(default_factory=list)
    toc: list[TocEntry] = Field(default_factory=list)
    coverage: Coverage
    cost_usd: float = 0.0


__all__ = ["Block", "Coverage", "EbookStructure", "StructureNode", "TocEntry"]
