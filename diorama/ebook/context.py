"""Deterministic EPUB → block-stream parser.

:class:`EbookContext` is the single source of truth for one ``load`` call. It
reads an EPUB with ebooklib, walks the spine **in reading order**, and flattens
every document into a single list of numbered :class:`Block` s. It also resolves
the Table of Contents into that same coordinate system, so the agent navigates one
consistent space where *a boundary is just a* ``block_id``.

No LLM is involved here — this module is pure, deterministic, and independently
testable.
"""

from __future__ import annotations

import os
import re
from typing import Any

from bs4 import BeautifulSoup, NavigableString, Tag
from thefuzz import fuzz

from diorama.ebook.models import Block, TocEntry

# Tags whose text we treat as one atomic block (we do not descend into them).
_BLOCK_TAGS = {
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "p",
    "blockquote",
    "pre",
    "li",
    "figcaption",
    "dt",
    "dd",
}
# Containers we descend through looking for blocks.
_CONTAINER_TAGS = {
    "div",
    "section",
    "article",
    "main",
    "body",
    "html",
    "ul",
    "ol",
    "dl",
    "table",
    "tbody",
    "tr",
    "td",
    "header",
    "footer",
    "figure",
}
# Never emit content from these.
_SKIP_TAGS = {"script", "style", "head", "title", "nav"}

# Structural-marker keywords that flag a class/id as heading-ish.
_STRUCT_KEYWORDS = (
    "chapter",
    "act",
    "scene",
    "section",
    "title",
    "heading",
    "head",
    "part",
    "book",
    "canto",
    "parva",
    "adhyaya",
    "stanza",
    "poem",
)
# Heading-ish leading tokens for the text heuristic.
_HEADING_LEAD = re.compile(
    r"^\s*(chapter|act|scene|book|part|canto|letter|volume|stave|parva|adhyaya)\b",
    re.IGNORECASE,
)
_FUZZY_THRESHOLD = 80


def _normalize_ws(text: str) -> str:
    """Collapse runs of whitespace into single spaces and strip the ends."""
    return re.sub(r"\s+", " ", text).strip()


def _is_heading_candidate(
    tag: str, classes: list[str], element_id: str | None, text: str
) -> bool:
    """Heuristically decide whether a block looks like a structural heading.

    Deliberately generous — Shakespeare scenes and Mahabharata adhyaya markers are
    often styled ``<p>``/``<div>`` rather than ``<h*>``. The agent makes the final
    judgment; this only surfaces candidates.
    """
    if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
        return True
    haystack = " ".join(classes + ([element_id] if element_id else [])).lower()
    if any(kw in haystack for kw in _STRUCT_KEYWORDS):
        return True
    if not text:
        return False
    if _HEADING_LEAD.match(text):
        return True
    # Short, fully upper-case lines are very often headings.
    if len(text) <= 60 and text.upper() == text and any(c.isalpha() for c in text):
        return True
    return False


class EbookContext:
    """Flattened, navigable view of one EPUB.

    Attributes:
        source_path (str): Path to the EPUB.
        title (str | None): The book title from EPUB metadata, if any.
        blocks (list[Block]): The full block stream, ``block_id``-ordered (0..N-1).
        toc (list[TocEntry]): The Table of Contents, anchored to block ids.
        submitted_structure (Any): Filled by the terminal ``submit_structure`` tool
            with the agent's raw node list; read back by the agent after the run.
    """

    def __init__(
        self,
        *,
        source_path: str,
        title: str | None,
        blocks: list[Block],
        toc: list[TocEntry],
    ) -> None:
        self.source_path = source_path
        self.title = title
        self.blocks = blocks
        self.toc = toc
        self.submitted_structure: Any = None

    # ------------------------------------------------------------------ #
    # Construction
    # ------------------------------------------------------------------ #
    @classmethod
    def parse(cls, epub_path: str) -> "EbookContext":
        """Read an EPUB and flatten it into a block stream + anchored ToC."""
        from ebooklib import epub

        book = epub.read_epub(epub_path)
        title = None
        meta = book.get_metadata("DC", "title")
        if meta:
            title = _normalize_ws(str(meta[0][0])) or None

        blocks: list[Block] = []
        # (spine_index, element_id) -> block_id, and bare element_id -> block_id.
        anchor_index: dict[tuple[int, str], int] = {}
        global_id_index: dict[str, int] = {}
        # basename(file) -> spine_index, for ToC href resolution.
        file_to_spine: dict[str, int] = {}
        # spine_index -> first block_id in that document.
        spine_first_block: dict[int, int] = {}

        for spine_index, (idref, _linear) in enumerate(book.spine):
            item = book.get_item_with_id(idref)
            if item is None:
                continue
            file_to_spine[os.path.basename(item.file_name)] = spine_index
            try:
                soup = BeautifulSoup(item.get_content(), "html.parser")
            except Exception:  # noqa: BLE001 — a malformed doc shouldn't kill the load
                continue
            root = soup.body or soup

            builder = _BlockBuilder(
                blocks=blocks,
                spine_index=spine_index,
                anchor_index=anchor_index,
                global_id_index=global_id_index,
            )
            builder.walk(root)
            if builder.first_block_id is not None:
                spine_first_block[spine_index] = builder.first_block_id

        toc = _build_toc(
            getattr(book, "toc", []) or [],
            blocks=blocks,
            file_to_spine=file_to_spine,
            anchor_index=anchor_index,
            global_id_index=global_id_index,
            spine_first_block=spine_first_block,
        )
        return cls(source_path=epub_path, title=title, blocks=blocks, toc=toc)

    # ------------------------------------------------------------------ #
    # Lookups
    # ------------------------------------------------------------------ #
    @property
    def total_blocks(self) -> int:
        return len(self.blocks)

    def slice(self, start: int, end: int) -> list[Block]:
        """Return blocks with ``start <= block_id < end`` (clamped, contiguous)."""
        start = max(0, start)
        end = min(len(self.blocks), end)
        if start >= end:
            return []
        return self.blocks[start:end]

    def heading_candidates(self) -> list[Block]:
        return [b for b in self.blocks if b.is_heading_candidate]


class _BlockBuilder:
    """Walks one document's DOM and appends :class:`Block` s to a shared list."""

    def __init__(
        self,
        *,
        blocks: list[Block],
        spine_index: int,
        anchor_index: dict[tuple[int, str], int],
        global_id_index: dict[str, int],
    ) -> None:
        self.blocks = blocks
        self.spine_index = spine_index
        self.anchor_index = anchor_index
        self.global_id_index = global_id_index
        self.first_block_id: int | None = None
        # ids seen on ancestor/wrapper elements, waiting for the next block.
        self._pending_ids: list[str] = []

    def walk(self, element: Tag) -> None:
        for child in element.children:
            if isinstance(child, NavigableString):
                continue
            if not isinstance(child, Tag):
                continue
            name = (child.name or "").lower()
            if name in _SKIP_TAGS:
                continue

            child_id = child.get("id")
            if name in _BLOCK_TAGS:
                self._emit(child)
            elif name in _CONTAINER_TAGS:
                if child_id:
                    self._pending_ids.append(child_id)
                self.walk(child)
            else:
                # Unknown tag (e.g. a div-like heading). Treat as a block when it
                # is a leaf with text; otherwise keep descending.
                if child.find(list(_BLOCK_TAGS)) is None and _normalize_ws(
                    child.get_text()
                ):
                    self._emit(child)
                else:
                    if child_id:
                        self._pending_ids.append(child_id)
                    self.walk(child)

    def _emit(self, element: Tag) -> None:
        text = _normalize_ws(element.get_text())
        if not text:
            # Still consume any pending ids so they attach to the next real block.
            return
        block_id = len(self.blocks)
        element_id = element.get("id")
        classes = element.get("class") or []
        if isinstance(classes, str):
            classes = classes.split()
        tag = (element.name or "").lower()

        block = Block(
            block_id=block_id,
            spine_index=self.spine_index,
            tag=tag,
            classes=list(classes),
            element_id=element_id,
            text=text,
            is_heading_candidate=_is_heading_candidate(
                tag, list(classes), element_id, text
            ),
        )
        self.blocks.append(block)
        if self.first_block_id is None:
            self.first_block_id = block_id

        # Register anchors: this block's own id plus any pending wrapper ids.
        ids = list(self._pending_ids)
        if element_id:
            ids.append(element_id)
        for eid in ids:
            self.anchor_index[(self.spine_index, eid)] = block_id
            self.global_id_index.setdefault(eid, block_id)
        self._pending_ids.clear()


def _split_href(href: str | None) -> tuple[str | None, str | None]:
    """Split a ``file#fragment`` href into ``(basename, fragment)``."""
    if not href:
        return None, None
    raw = href.split("#", 1)
    file_part = os.path.basename(raw[0]) if raw[0] else None
    fragment = raw[1] if len(raw) > 1 else None
    return file_part, fragment


def _resolve_anchor(
    *,
    href: str | None,
    title: str,
    blocks: list[Block],
    file_to_spine: dict[str, int],
    anchor_index: dict[tuple[int, str], int],
    global_id_index: dict[str, int],
    spine_first_block: dict[int, int],
) -> int | None:
    """Resolve a ToC href/title to a block id, with a fuzzy-title fallback."""
    file_part, fragment = _split_href(href)
    spine_index = file_to_spine.get(file_part) if file_part else None

    if fragment is not None:
        if spine_index is not None and (spine_index, fragment) in anchor_index:
            return anchor_index[(spine_index, fragment)]
        if fragment in global_id_index:
            return global_id_index[fragment]

    # Fuzzy fallback: best heading-candidate match within the target document.
    if spine_index is not None:
        target = _normalize_ws(title).lower()
        best_id, best_score = None, 0
        for b in blocks:
            if b.spine_index != spine_index or not b.is_heading_candidate:
                continue
            score = fuzz.ratio(target, b.text.lower())
            if score > best_score:
                best_id, best_score = b.block_id, score
        if best_id is not None and best_score >= _FUZZY_THRESHOLD:
            return best_id
        if spine_index in spine_first_block:
            return spine_first_block[spine_index]

    return None


def _build_toc(
    raw_toc: list[Any],
    *,
    blocks: list[Block],
    file_to_spine: dict[str, int],
    anchor_index: dict[tuple[int, str], int],
    global_id_index: dict[str, int],
    spine_first_block: dict[int, int],
) -> list[TocEntry]:
    """Convert ebooklib's nested ToC into block-anchored :class:`TocEntry` s."""
    entries: list[TocEntry] = []
    for node in raw_toc:
        if isinstance(node, tuple):
            section, children = node[0], node[1]
            title = _normalize_ws(getattr(section, "title", "") or "")
            href = getattr(section, "href", None)
            sub = _build_toc(
                children,
                blocks=blocks,
                file_to_spine=file_to_spine,
                anchor_index=anchor_index,
                global_id_index=global_id_index,
                spine_first_block=spine_first_block,
            )
        else:
            title = _normalize_ws(getattr(node, "title", "") or "")
            href = getattr(node, "href", None)
            sub = []
        block_id = _resolve_anchor(
            href=href,
            title=title,
            blocks=blocks,
            file_to_spine=file_to_spine,
            anchor_index=anchor_index,
            global_id_index=global_id_index,
            spine_first_block=spine_first_block,
        )
        entries.append(
            TocEntry(title=title, block_id=block_id, href=href, children=sub)
        )
    return entries
