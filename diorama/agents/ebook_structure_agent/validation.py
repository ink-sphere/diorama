from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Callable, Iterable, Iterator
from hashlib import sha256
from itertools import zip_longest
from pathlib import Path

import html2text
from bs4 import BeautifulSoup

from diorama.agents.ebook_structure_agent.hierarchy import validate_hierarchy
from diorama.agents.ebook_structure_agent.markup import markup_tokens, normalize
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


def _located_tokens(
    fragments: Iterable[str], locations: Iterable[str] | None = None
) -> Iterator[tuple[tuple, str]]:
    labels = iter(locations) if locations is not None else None
    for index, fragment in enumerate(fragments, 1):
        label = next(labels) if labels is not None else f"text fragment {index}"
        for token in markup_tokens([fragment]):
            yield token, label


def _describe_token(item: tuple[tuple, str] | None) -> str:
    if item is None:
        return "end of content"
    token, location = item
    return f"{location}: {json.dumps(token, ensure_ascii=False)[:1500]}"


def _validate_markup(
    expected: Iterable[str],
    actual: Iterable[str],
    *,
    source_locations: Iterable[str] | None = None,
    description: str = "Source content changed, duplicated, omitted, or reordered",
) -> None:
    for index, (left, right) in enumerate(
        zip_longest(
            _located_tokens(expected, source_locations), _located_tokens(actual)
        )
    ):
        if left is None or right is None or left[0] != right[0]:
            raise ValueError(
                f"{description} at token {index}. "
                f"Expected {_describe_token(left)}; actual {_describe_token(right)}. "
                "Compare this source location with the corresponding output fragment; "
                "preserve its text, markup, meaningful attributes, and reading order."
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
                    source_locations=(
                        f"{block.href} (block {block.id})" for block in source.blocks
                    ),
                )
                validate_hierarchy(
                    book,
                    ((block.raw_text, block.href, block.id) for block in source.blocks),
                    toc=source.toc,
                )
                return
            if {"manifest.json", "book.json"}.issubset(archive.namelist()):
                metadata, fragments = parse_json_book_export(archive, filename=filename)
                _validate_metadata(book, metadata)
                _validate_markup(
                    fragments,
                    (item.raw_text for item in content),
                    source_locations=(
                        f"book.json chapter {index}" for index in range(len(fragments))
                    ),
                )
                validate_hierarchy(
                    book,
                    (
                        (fragment, str(index), None)
                        for index, fragment in enumerate(fragments)
                    ),
                )
                return
    if Path(filename).suffix.lower() == ".epub":
        raise ValueError(
            "Input named .epub contains neither an EPUB container nor a supported JSON book export"
        )
    elif Path(filename).suffix.lower() in {".txt", ".md"}:
        actual = " ".join(
            BeautifulSoup(item.raw_text, "html.parser").get_text() for item in content
        )
        if normalize(actual) != normalize(data.decode("utf-8-sig")):
            raise ValueError("Source text changed, duplicated, omitted, or reordered")
    elif Path(filename).suffix.lower() in {".html", ".htm", ".xhtml"}:
        _validate_markup(
            [data.decode("utf-8-sig")],
            (item.raw_text for item in content),
            source_locations=[filename],
            description="Source markup changed, duplicated, omitted, or reordered",
        )
        validate_hierarchy(book, [(data.decode("utf-8-sig"), filename, None)])


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
        if normalize(content.markdown_text) != normalize(expected):
            raise ValueError(
                "Markdown content does not match its source fragment; use html2text with body_width=0"
            )
    source_validator(book, data, filename)
    return book
