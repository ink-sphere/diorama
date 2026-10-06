from __future__ import annotations

import html
import io
from collections.abc import Iterator
from dataclasses import dataclass
from hashlib import sha256
from math import isfinite
from pathlib import Path
from posixpath import normpath
from typing import Literal, Self
from urllib.parse import unquote, urldefrag
from zipfile import ZipFile

import ebooklib
import html2text
from bs4 import BeautifulSoup
from bs4.element import Comment, NavigableString, Tag
from ebooklib import epub
from pydantic import BaseModel, ConfigDict, Field, model_validator

from diorama.models.storybook import (
    EbookMetadata,
    StoryBook,
    StructureNode,
    TextContent,
)


class EbookStructureError(RuntimeError):
    pass


class _PlanInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class StructurePlanNode(_PlanInput):
    structure_type: Literal[
        "part",
        "chapter",
        "section",
        "prologue",
        "epilogue",
        "preface",
        "introduction",
        "appendix",
        "notes",
        "acknowledgments",
        "dedication",
        "title_page",
        "copyright",
        "contents",
        "cover",
        "other",
    ]
    structure_title: str
    is_part_of_narrative: bool
    start_block_id: str | None = Field(
        default=None, description="Inclusive leaf start."
    )
    end_block_id: str | None = Field(default=None, description="Inclusive leaf end.")
    children: list[StructurePlanNode] = Field(default_factory=list)
    evidence_block_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_content(self) -> Self:
        if self.children:
            if self.start_block_id is not None or self.end_block_id is not None:
                raise ValueError("Group nodes have children and no source range")
        elif self.start_block_id is None or self.end_block_id is None:
            raise ValueError("Leaf nodes require both source range endpoints")
        return self


class StructurePlan(_PlanInput):
    nodes: list[StructurePlanNode] = Field(min_length=1)


class _ExportChapter(BaseModel):
    model_config = ConfigDict(strict=True)
    content: str


class _ExportBook(BaseModel):
    model_config = ConfigDict(strict=True)
    title: str | None = None
    metadata: dict[str, dict[str, list[tuple[str | None, dict[str, str]]]]] = Field(
        default_factory=dict
    )
    chapters: list[_ExportChapter] = Field(min_length=1)


class _ExportManifest(BaseModel):
    model_config = ConfigDict(strict=True)
    title: str | None = None
    author: str | None = None


@dataclass(frozen=True, slots=True)
class SourceBlock:
    id: str
    href: str
    raw_text: str
    text: str
    tag: str
    fragment_ids: tuple[str, ...]
    epub_types: tuple[str, ...]
    heading_level: int | None


@dataclass(frozen=True, slots=True)
class SourceDocument:
    href: str
    title: str
    in_spine: bool
    linear: bool
    start: int
    stop: int
    raw_content: bytes


@dataclass(frozen=True, slots=True)
class TocEntry:
    title: str
    href: str
    depth: int
    block_id: str | None


@dataclass(frozen=True, slots=True)
class EbookSource:
    id: str
    metadata: EbookMetadata
    blocks: tuple[SourceBlock, ...]
    documents: tuple[SourceDocument, ...]
    toc: tuple[TocEntry, ...]
    excluded_documents: tuple[dict[str, str], ...]


_ATOMIC_TAGS = {
    "p",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "pre",
    "table",
    "ul",
    "ol",
    "dl",
    "blockquote",
    "figure",
    "img",
    "svg",
    "math",
    "hr",
    "br",
    "audio",
    "video",
    "iframe",
    "object",
}
_IGNORED_TAGS = {"script", "style", "head"}
_CONTAINER_TAGS = {
    "div",
    "section",
    "article",
    "aside",
    "header",
    "footer",
    "main",
    "nav",
}


def _name(tag: Tag) -> str:
    return tag.name.rsplit(":", 1)[-1].lower()


def _fragments(element: Tag) -> Iterator[Tag | NavigableString]:
    for child in element.children:
        if isinstance(child, Comment):
            continue
        if isinstance(child, NavigableString):
            if child.strip():
                yield child
        elif isinstance(child, Tag):
            name = _name(child)
            if name in _IGNORED_TAGS:
                continue
            types = str(child.get("epub:type", "")).split()
            if name == "nav" and set(types) & {"toc", "page-list", "landmarks"}:
                continue
            if name in _ATOMIC_TAGS or not child.find(
                lambda tag: _name(tag) in _ATOMIC_TAGS | _CONTAINER_TAGS
            ):
                yield child
            else:
                yield from _fragments(child)


def _metadata(book: epub.EpubBook, path: Path) -> EbookMetadata:
    def values(key: str) -> list[str]:
        return [
            str(value).strip()
            for value, _ in book.get_metadata("DC", key)
            if value is not None and str(value).strip()
        ]

    def first(key: str) -> str | None:
        return next(iter(values(key)), None)

    series = None
    position = None
    for entries in book.metadata.values():
        for value, attrs in entries.get("meta", []):
            name = attrs.get("name") or attrs.get("property")
            content = attrs.get("content", value)
            if name in {"calibre:series", "belongs-to-collection"} and content:
                series = str(content)
            elif name in {"calibre:series_index", "group-position"} and content:
                try:
                    candidate = float(content)
                    if isfinite(candidate):
                        position = candidate
                except (ValueError, TypeError):
                    pass
    cover = next(
        (
            item.get_name()
            for item in book.get_items()
            if isinstance(item, epub.EpubCover)
        ),
        None,
    )
    if cover is None:
        for _, attrs in book.get_metadata("OPF", "cover"):
            item = book.get_item_with_id(attrs.get("content", ""))
            if item is not None:
                cover = item.get_name()
                break
    return EbookMetadata(
        title=first("title") or path.stem,
        authors=values("creator"),
        language=first("language"),
        identifiers=values("identifier"),
        publisher=first("publisher"),
        publication_date=first("date"),
        description=first("description"),
        subjects=values("subject"),
        contributors=values("contributor"),
        rights=first("rights"),
        series=series,
        series_position=position,
        cover_href=cover,
    )


def parse_json_book_export(
    archive: ZipFile, *, filename: str
) -> tuple[EbookMetadata, tuple[str, ...]]:
    payload = _ExportBook.model_validate_json(archive.read("book.json"))
    manifest = _ExportManifest.model_validate_json(archive.read("manifest.json"))
    book = epub.EpubBook()
    book.metadata = payload.metadata
    for namespace in ("DC", "OPF"):
        book.metadata.setdefault(epub.NAMESPACES[namespace], {})
    metadata = _metadata(book, Path(filename))
    if not any(
        value and value.strip() for value, _ in book.get_metadata("DC", "title")
    ):
        metadata.title = payload.title or manifest.title or metadata.title
    if not metadata.authors and manifest.author:
        metadata.authors = [manifest.author]
    return metadata, tuple(chapter.content for chapter in payload.chapters)


def parse_ebook(
    path: str | Path | bytes, *, filename: str = "book.epub"
) -> EbookSource:
    if isinstance(path, bytes):
        data = path
        path = Path(filename)
    else:
        path = Path(path)
        data = path.read_bytes()
    try:
        book = epub.read_epub(io.BytesIO(data))
    except Exception as exc:
        raise EbookStructureError(f"Unable to read EPUB {path.name}: {exc}") from exc

    ordered: list[tuple[epub.EpubItem, bool, bool]] = []
    seen: set[str] = set()
    for item_id, linear in book.spine:
        item = book.get_item_with_id(item_id)
        if item is None:
            raise EbookStructureError(f"Spine references missing item {item_id!r}")
        if item_id not in seen:
            ordered.append((item, True, linear != "no"))
            seen.add(item_id)
    for item in sorted(
        book.get_items_of_type(ebooklib.ITEM_DOCUMENT), key=lambda item: item.get_name()
    ):
        if item.get_id() not in seen:
            ordered.append((item, False, False))
            seen.add(item.get_id())

    blocks: list[SourceBlock] = []
    documents: list[SourceDocument] = []
    excluded: list[dict[str, str]] = []
    targets: dict[tuple[str, str], str] = {}
    for item, in_spine, linear in ordered:
        if item.get_type() != ebooklib.ITEM_DOCUMENT:
            continue
        href = item.get_name()
        if isinstance(item, epub.EpubNav) or "nav" in getattr(item, "properties", []):
            excluded.append({"href": href, "reason": "EPUB navigation document"})
            continue
        raw = item.content
        soup = BeautifulSoup(raw, "html.parser")
        body = soup.find(lambda tag: _name(tag) == "body")
        if body is None:
            raise EbookStructureError(f"Document {href!r} has no body")
        start = len(blocks)
        for fragment in _fragments(body):
            ancestors = [fragment] if isinstance(fragment, Tag) else []
            ancestors.extend(
                parent for parent in fragment.parents if isinstance(parent, Tag)
            )
            ids = tuple(
                dict.fromkeys(str(tag["id"]) for tag in ancestors if tag.get("id"))
            )
            types = tuple(
                dict.fromkeys(
                    value
                    for tag in ancestors
                    for value in str(tag.get("epub:type", "")).split()
                )
            )
            tag = _name(fragment) if isinstance(fragment, Tag) else "text"
            text = (
                fragment.get_text(" ", strip=True)
                if isinstance(fragment, Tag)
                else str(fragment).strip()
            )
            block_id = f"b{len(blocks) + 1:06d}"
            blocks.append(
                SourceBlock(
                    id=block_id,
                    href=href,
                    raw_text=str(fragment)
                    if isinstance(fragment, Tag)
                    else html.escape(str(fragment)),
                    text=text,
                    tag=tag,
                    fragment_ids=ids,
                    epub_types=types,
                    heading_level=int(tag[1])
                    if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}
                    else None,
                )
            )
            targets.setdefault((href, ""), block_id)
            for anchor in ids:
                targets.setdefault((href, anchor), block_id)
            if isinstance(fragment, Tag):
                for descendant in fragment.find_all(id=True):
                    targets.setdefault((href, str(descendant["id"])), block_id)
        if len(blocks) == start:
            excluded.append({"href": href, "reason": "No content blocks"})
        documents.append(
            SourceDocument(
                href=href,
                title=str(getattr(item, "title", "") or ""),
                in_spine=in_spine,
                linear=linear,
                start=start,
                stop=len(blocks),
                raw_content=raw,
            )
        )

    def toc_entries(items: list | tuple, depth: int = 0) -> Iterator[TocEntry]:
        for entry in items:
            children = ()
            if isinstance(entry, (tuple, list)):
                entry, children = entry
            href = str(getattr(entry, "href", getattr(entry, "file_name", "")))
            file, anchor = urldefrag(href)
            yield TocEntry(
                str(getattr(entry, "title", "")),
                href,
                depth,
                targets.get((normpath(unquote(file)), unquote(anchor))),
            )
            yield from toc_entries(children, depth + 1)

    if not blocks:
        raise EbookStructureError("EPUB contains no readable content blocks")
    return EbookSource(
        id=sha256(data).hexdigest(),
        metadata=_metadata(book, path),
        blocks=tuple(blocks),
        documents=tuple(documents),
        toc=tuple(toc_entries(book.toc)),
        excluded_documents=tuple(excluded),
    )


def validate_plan(
    plan: StructurePlan, source: EbookSource, *, start: int = 0, stop: int | None = None
) -> None:
    stop = len(source.blocks) if stop is None else stop
    if not 0 <= start < stop <= len(source.blocks):
        raise ValueError("Invalid extraction window")
    positions = {block.id: position for position, block in enumerate(source.blocks)}
    cursor = start

    def visit(node: StructurePlanNode) -> None:
        nonlocal cursor
        for evidence_id in node.evidence_block_ids:
            if evidence_id not in positions:
                raise ValueError(f"Unknown evidence block {evidence_id!r}")
        if node.children:
            if node.is_part_of_narrative != any(
                child.is_part_of_narrative for child in node.children
            ):
                raise ValueError(
                    "A group's narrative flag must match any narrative child"
                )
            for child in node.children:
                visit(child)
            return
        if node.start_block_id not in positions or node.end_block_id not in positions:
            raise ValueError(
                f"Unknown source range {node.start_block_id!r}..{node.end_block_id!r}"
            )
        first = positions[node.start_block_id]
        last = positions[node.end_block_id]
        if last < first:
            raise ValueError("Source range endpoints are reversed")
        if first < start or last >= stop:
            raise ValueError("Source range lies outside this extraction window")
        if first != cursor:
            reason = (
                "overlap or incorrect order" if first < cursor else "missing content"
            )
            expected = source.blocks[cursor].id if cursor < stop else "end of window"
            raise ValueError(f"{reason}: expected range to start at {expected}")
        cursor = last + 1

    for node in plan.nodes:
        visit(node)
    if cursor != stop:
        raise ValueError(
            f"Missing content from {source.blocks[cursor].id} through {source.blocks[stop - 1].id}"
        )


def materialize_storybook(plan: StructurePlan, source: EbookSource) -> StoryBook:
    validate_plan(plan, source)
    positions = {block.id: index for index, block in enumerate(source.blocks)}
    converter = html2text.HTML2Text()
    converter.body_width = 0
    converter.ignore_links = False
    converter.ignore_images = False

    def build(node: StructurePlanNode) -> StructureNode:
        if node.children:
            content = [build(child) for child in node.children]
        else:
            if node.start_block_id is None or node.end_block_id is None:
                raise ValueError("Leaf nodes require both source range endpoints")
            first = positions[node.start_block_id]
            last = positions[node.end_block_id]
            content = [
                TextContent(
                    raw_text=block.raw_text,
                    markdown_text=converter.handle(block.raw_text).strip(),
                )
                for block in source.blocks[first : last + 1]
            ]
        return StructureNode(
            structure_type=node.structure_type,
            structure_title=node.structure_title,
            is_part_of_narrative=node.is_part_of_narrative,
            content=content,
        )

    return StoryBook(
        id=source.id,
        metadata=source.metadata.model_copy(deep=True),
        structure=[build(node) for node in plan.nodes],
    )
