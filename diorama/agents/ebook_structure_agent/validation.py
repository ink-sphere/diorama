from __future__ import annotations

import io
import re
import zipfile
from collections.abc import Callable, Iterable, Iterator
from hashlib import sha256
from itertools import zip_longest
from pathlib import Path

import html2text
from bs4 import BeautifulSoup
from bs4.element import Comment, Doctype, NavigableString, ProcessingInstruction, Tag

from diorama.agents.ebook_structure_agent.source import (
    EbookStructureError,
    parse_ebook,
    parse_json_book_export,
)
from diorama.models.storybook import (
    EbookMetadata,
    StoryBook,
    StructureNode,
    TextContent,
)

type SourceValidator = Callable[[StoryBook, bytes, str], None]


def iter_text_content(nodes: Iterable[StructureNode]) -> Iterator[TextContent]:
    for node in nodes:
        if not node.structure_type.strip() or not node.content:
            raise ValueError("Structure nodes require a type and non-empty content")
        if isinstance(node.content[0], StructureNode):
            children = [
                child for child in node.content if isinstance(child, StructureNode)
            ]
            if len(children) != len(node.content):
                raise ValueError("A node cannot mix text and child structures")
            if node.is_part_of_narrative != any(
                child.is_part_of_narrative for child in children
            ):
                raise ValueError(
                    "A group's narrative flag must match any narrative child"
                )
            yield from iter_text_content(children)
        else:
            for content in node.content:
                if not isinstance(content, TextContent):
                    raise ValueError("A node cannot mix text and child structures")
                if not content.raw_text.strip():
                    raise ValueError("Text content requires a source fragment")
                yield content


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _markup_tokens(fragments: Iterable[str]) -> Iterator[tuple]:
    containers = {
        "html",
        "body",
        "section",
        "div",
        "article",
        "aside",
        "main",
        "header",
        "footer",
    }

    def visit(element) -> Iterator[tuple]:
        if isinstance(element, (Comment, Doctype, ProcessingInstruction)):
            return
        if isinstance(element, NavigableString):
            text = _normalize(str(element))
            if text:
                yield ("text", text)
        elif isinstance(element, Tag):
            name = element.name.rsplit(":", 1)[-1].lower()
            if name in {"head", "script", "style"}:
                return
            if name not in containers:
                attrs = tuple(
                    sorted(
                        (key, tuple(value) if isinstance(value, list) else value)
                        for key, value in element.attrs.items()
                    )
                )
                yield ("open", name, attrs)
            for child in element.children:
                yield from visit(child)
            if name not in containers:
                yield ("close", name)

    for fragment in fragments:
        for element in BeautifulSoup(fragment, "html.parser").contents:
            yield from visit(element)


def _is_epub(archive: zipfile.ZipFile) -> bool:
    names = archive.namelist()
    return "META-INF/container.xml" in names or (
        "mimetype" in names
        and archive.read("mimetype").strip() == b"application/epub+zip"
    )


def _validate_metadata(book: StoryBook, metadata: EbookMetadata) -> None:
    actual = book.metadata.model_dump()
    for field, value in metadata.model_dump().items():
        if value is not None and value != [] and actual[field] != value:
            raise ValueError(f"Source metadata changed: {field}")


def _validate_markup(expected: Iterable[str], actual: Iterable[str]) -> None:
    for index, (left, right) in enumerate(
        zip_longest(_markup_tokens(expected), _markup_tokens(actual))
    ):
        if left != right:
            raise ValueError(
                f"Source content changed, duplicated, omitted, or reordered at token {index}"
            )


def validate_source(book: StoryBook, data: bytes, filename: str) -> None:
    content = list(iter_text_content(book.structure))
    if zipfile.is_zipfile(io.BytesIO(data)):
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if _is_epub(archive):
                try:
                    source = parse_ebook(data, filename=filename)
                except EbookStructureError as exc:
                    raise ValueError(str(exc)) from exc
                _validate_metadata(book, source.metadata)
                _validate_markup(
                    (block.raw_text for block in source.blocks),
                    (item.raw_text for item in content),
                )
                return
            if {"manifest.json", "book.json"}.issubset(archive.namelist()):
                metadata, fragments = parse_json_book_export(archive, filename=filename)
                _validate_metadata(book, metadata)
                _validate_markup(fragments, (item.raw_text for item in content))
                return
    if Path(filename).suffix.lower() == ".epub":
        raise ValueError(
            "Input named .epub contains neither an EPUB container nor a supported JSON book export"
        )
    elif Path(filename).suffix.lower() in {".txt", ".md"}:
        actual = " ".join(
            BeautifulSoup(item.raw_text, "html.parser").get_text() for item in content
        )
        if _normalize(actual) != _normalize(data.decode("utf-8-sig")):
            raise ValueError("Source text changed, duplicated, omitted, or reordered")
    elif Path(filename).suffix.lower() in {".html", ".htm", ".xhtml"}:
        expected = _markup_tokens([data.decode("utf-8-sig")])
        actual = _markup_tokens(item.raw_text for item in content)
        if any(left != right for left, right in zip_longest(expected, actual)):
            raise ValueError("Source markup changed, duplicated, omitted, or reordered")


def load_storybook(
    output_path: Path,
    data: bytes,
    filename: str,
    *,
    source_validator: SourceValidator = validate_source,
    max_output_bytes: int = 128 * 1024 * 1024,
) -> StoryBook:
    if not output_path.is_file():
        raise ValueError("Missing output/storybook.json")
    if output_path.stat().st_size > max_output_bytes:
        raise ValueError("StoryBook artifact exceeds max_output_bytes")
    book = StoryBook.model_validate_json(
        output_path.read_bytes(), strict=True, extra="forbid"
    )
    if book.id != sha256(data).hexdigest():
        raise ValueError("StoryBook.id must equal the supplied SHA-256 source_id")
    if not book.metadata.title.strip() or not book.structure:
        raise ValueError("StoryBook requires a title and non-empty structure")
    converter = html2text.HTML2Text()
    converter.body_width = 0
    for content in iter_text_content(book.structure):
        expected = converter.handle(content.raw_text).strip()
        if _normalize(content.markdown_text) != _normalize(expected):
            raise ValueError(
                "Markdown content does not match its source fragment; use html2text with body_width=0"
            )
    source_validator(book, data, filename)
    return book
