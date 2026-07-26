"""EPUB parsing: flatten spine documents into numbered :class:`Block` objects.

Deterministic and agent-free — :class:`EbookContext` is built once per book and then
only *read* by the loader agent's tools; nothing here calls an LLM. Block-level HTML
elements (paragraphs, headings, list items, ...) become blocks in reading order,
which is the coordinate system the agent submits its structure tree against.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import ebooklib
from bs4 import BeautifulSoup, Tag
from ebooklib import epub
from thefuzz import fuzz

from diorama.ebook.models import Block, TocEntry

_BLOCK_TAGS = ["p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "td"]
_HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_WHITESPACE_RE = re.compile(r"\s+")
_FUZZY_MATCH_THRESHOLD = 60


def _clean_text(text: str) -> str:
    """Collapse runs of whitespace and strip the result."""
    return _WHITESPACE_RE.sub(" ", text).strip()


def _first_metadata_value(values: list[Any]) -> str | None:
    """Return the first non-empty value from an ebooklib ``get_metadata`` result."""
    for value in values:
        candidate = value[0] if isinstance(value, tuple) else value
        if candidate:
            return str(candidate)
    return None


def _basename(href: str) -> str:
    """Strip any path prefix and return an href's file component."""
    return href.split("/")[-1]


@dataclass
class EbookContext:
    """Parsed EPUB: metadata, flattened blocks, and the book's own table of contents.

    Attributes:
        path (Path): Source EPUB path.
        title (str): Book title.
        author (str | None): Book author, if declared.
        blocks (list[Block]): All blocks, in reading order.
        toc (list[TocEntry]): The EPUB's own table of contents, block-anchored.
    """

    path: Path
    title: str
    author: str | None
    blocks: list[Block] = field(default_factory=list)
    toc: list[TocEntry] = field(default_factory=list)

    @property
    def total_blocks(self) -> int:
        """Total number of blocks in the book."""
        return len(self.blocks)

    def block(self, block_id: int) -> Block:
        """Return the block at ``block_id``.

        Raises:
            ValueError: If ``block_id`` is out of range.
        """
        if not (0 <= block_id < len(self.blocks)):
            raise ValueError(
                f"block_id {block_id} out of range [0, {len(self.blocks)})"
            )
        return self.blocks[block_id]

    def blocks_in_range(self, start: int, end: int) -> list[Block]:
        """Return blocks in ``[start, end]`` inclusive.

        Raises:
            ValueError: If the range is empty/reversed or out of bounds.
        """
        if start > end:
            raise ValueError(
                f"start_block_id ({start}) must be <= end_block_id ({end})"
            )
        if start < 0 or end >= len(self.blocks):
            raise ValueError(
                f"range [{start}, {end}] out of bounds [0, {len(self.blocks) - 1}]"
            )
        return self.blocks[start : end + 1]

    def headings(self) -> list[Block]:
        """All blocks the parser flagged as headings, in reading order."""
        return [b for b in self.blocks if b.tag is not None]

    def search(
        self, query: str, *, regex: bool = False, limit: int = 20
    ) -> list[Block]:
        """Return up to ``limit`` blocks whose text matches ``query``.

        Raises:
            re.error: If ``regex`` is True and ``query`` is not a valid pattern.
        """
        if regex:
            test = re.compile(query, re.IGNORECASE).search
        else:
            needle = query.lower()
            test = lambda text: needle in text.lower()  # noqa: E731
        matches: list[Block] = []
        for b in self.blocks:
            if test(b.text):
                matches.append(b)
                if len(matches) >= limit:
                    break
        return matches

    @classmethod
    def parse(cls, epub_path: str | Path) -> EbookContext:
        """Parse an EPUB into blocks and a block-anchored table of contents."""
        path = Path(epub_path)
        book = epub.read_epub(str(path))
        title = _first_metadata_value(book.get_metadata("DC", "title")) or path.stem
        author = _first_metadata_value(book.get_metadata("DC", "creator"))

        blocks: list[Block] = []
        file_first_block: dict[str, int] = {}
        file_heading_blocks: dict[str, list[int]] = {}
        element_lookup: dict[tuple[str, str], int] = {}

        for spine_index, (item_id, _linear) in enumerate(book.spine):
            item = book.get_item_with_id(item_id)
            if item is None or item.get_type() != ebooklib.ITEM_DOCUMENT:
                continue
            name = item.get_name()
            soup = BeautifulSoup(item.get_content(), "html.parser")

            elem_object_to_block: dict[int, int] = {}
            for element in soup.find_all(_BLOCK_TAGS):
                # Skip containers whose text is already captured by a nested
                # block-level descendant (e.g. <li><p>...</p></li>) to avoid
                # double-counting the same text as two blocks.
                if element.find(_BLOCK_TAGS) is not None:
                    continue
                text = _clean_text(element.get_text())
                if not text:
                    continue
                block = Block(
                    id=len(blocks),
                    text=text,
                    tag=element.name if element.name in _HEADING_TAGS else None,
                    spine_index=spine_index,
                    element_id=element.get("id"),
                )
                blocks.append(block)
                elem_object_to_block[id(element)] = block.id
                file_first_block.setdefault(name, block.id)
                if block.element_id:
                    element_lookup[(name, block.element_id)] = block.id
                if block.tag:
                    file_heading_blocks.setdefault(name, []).append(block.id)

            # Anchors sometimes target a container (e.g. <div id="chap1">) rather
            # than a block-level element directly; resolve those to the first
            # block-level element inside (or immediately following) the anchor.
            for tag_with_id in soup.find_all(id=True):
                tid = tag_with_id.get("id")
                if not tid or (name, tid) in element_lookup:
                    continue
                target: Tag | None = (
                    tag_with_id
                    if tag_with_id.name in _BLOCK_TAGS
                    else (
                        tag_with_id.find(_BLOCK_TAGS)
                        or tag_with_id.find_next(_BLOCK_TAGS)
                    )
                )
                if target is not None and id(target) in elem_object_to_block:
                    element_lookup[(name, tid)] = elem_object_to_block[id(target)]

        toc = _build_toc(
            getattr(book, "toc", []) or [],
            file_first_block=file_first_block,
            file_heading_blocks=file_heading_blocks,
            element_lookup=element_lookup,
            blocks=blocks,
        )
        return cls(path=path, title=title, author=author, blocks=blocks, toc=toc)


def _anchor_href(
    href: str,
    title: str,
    *,
    file_first_block: dict[str, int],
    file_heading_blocks: dict[str, list[int]],
    element_lookup: dict[tuple[str, str], int],
    blocks: list[Block],
) -> tuple[int | None, str]:
    """Resolve a table-of-contents ``href`` to a block id, best-effort.

    Tries an exact element-id anchor first, then falls back to fuzzy-matching
    ``title`` against the target document's headings, then that document's first
    block. Returns ``(None, "unresolved")`` when the target document isn't found at
    all (e.g. an external link).
    """
    if not href:
        return None, "unresolved"
    file_part, _, fragment = href.partition("#")
    file_part = _basename(file_part)

    file_name = next((n for n in file_first_block if _basename(n) == file_part), None)
    if file_name is None:
        return None, "unresolved"

    if fragment:
        block_id = element_lookup.get((file_name, fragment))
        if block_id is not None:
            return block_id, "anchor"

    heading_ids = file_heading_blocks.get(file_name, [])
    if heading_ids and title:
        best_id, best_score = None, -1
        for hid in heading_ids:
            score = fuzz.ratio(title.strip().lower(), blocks[hid].text.lower())
            if score > best_score:
                best_id, best_score = hid, score
        if best_id is not None and best_score >= _FUZZY_MATCH_THRESHOLD:
            return best_id, "fuzzy"

    return file_first_block.get(file_name), "anchor"


def _build_toc(
    nodes: Any,
    *,
    file_first_block: dict[str, int],
    file_heading_blocks: dict[str, list[int]],
    element_lookup: dict[tuple[str, str], int],
    blocks: list[Block],
) -> list[TocEntry]:
    """Recursively convert ebooklib's ``book.toc`` into block-anchored entries."""
    entries: list[TocEntry] = []
    for node in nodes:
        if isinstance(node, (list, tuple)) and not hasattr(node, "href"):
            section, children = node[0], node[1] if len(node) > 1 else []
            title = getattr(section, "title", None) or str(section)
            entries.append(
                TocEntry(
                    title=title,
                    block_id=None,
                    matched_by="unresolved",
                    children=_build_toc(
                        children,
                        file_first_block=file_first_block,
                        file_heading_blocks=file_heading_blocks,
                        element_lookup=element_lookup,
                        blocks=blocks,
                    ),
                )
            )
            continue

        title = getattr(node, "title", None) or str(node)
        href = getattr(node, "href", "") or ""
        block_id, matched_by = _anchor_href(
            href,
            title,
            file_first_block=file_first_block,
            file_heading_blocks=file_heading_blocks,
            element_lookup=element_lookup,
            blocks=blocks,
        )
        entries.append(TocEntry(title=title, block_id=block_id, matched_by=matched_by))
    return entries


__all__ = ["EbookContext"]
