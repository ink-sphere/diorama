from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from itertools import islice
from posixpath import normpath
from urllib.parse import unquote, urldefrag

from bs4 import BeautifulSoup
from bs4.element import Tag

from diorama.agents.ebook_structure_agent.markup import markup_tokens
from diorama.agents.ebook_structure_agent.source import TocEntry
from diorama.models.storybook import StoryBook, StructureNode, TextContent
from diorama.utils.structure_node_utils import _BODY_MEDIA, has_text as _has_text

_NUMBERED_SECTION = re.compile(
    r"^(act|scene|part|book|chapter|section)\s+"
    r"(?:[ivxlcdm]+|\d+|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\b",
    re.IGNORECASE,
)
_FRONT_MATTER = re.compile(
    r"^(prologue|epilogue|preface|introduction|appendix)(?:$|[\s:.—-])",
    re.IGNORECASE,
)
_NON_STRUCTURAL = {"head", "nav", "blockquote", "table", "figure", "script", "style"}


def _title_key(title: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", title).casefold().split())


def _headings(raw: str) -> Iterator[Tag]:
    if re.search(r"<(?:[\w.-]+:)?h[1-6](?:\s|>)", raw, re.IGNORECASE) is None:
        return
    soup = BeautifulSoup(raw, "html.parser")
    for heading in soup.find_all(
        lambda tag: re.fullmatch(r"h[1-6]", tag.name.rsplit(":", 1)[-1]) is not None
    ):
        if not any(
            parent.name.rsplit(":", 1)[-1] in _NON_STRUCTURAL
            for parent in heading.parents
            if isinstance(parent, Tag)
        ):
            yield heading


def _heading_offsets(tokens: list[tuple]) -> Iterator[int]:
    ancestors: list[str] = []
    for offset, token in enumerate(tokens):
        if token[0] == "open":
            name = token[1]
            if re.fullmatch(r"h[1-6]", name) and not _NON_STRUCTURAL.intersection(
                ancestors
            ):
                yield offset
            ancestors.append(name)
        elif token[0] == "close":
            ancestors.pop()


@dataclass
class _Heading:
    title: str
    level: int
    href: str
    block_id: str | None
    kind: str | None
    start: int
    required: bool = False
    parent: int | None = None


def validate_hierarchy(
    book: StoryBook,
    fragments: Iterable[tuple[str, str, str | None]],
    *,
    toc: Iterable[TocEntry] = (),
) -> None:
    """Check source-backed boundaries after ordered content validation has passed."""
    headings = []
    offset = 0
    document_ends: dict[str, int] = {}
    payload_offsets: list[int] = []
    for raw, href, block_id in fragments:
        tokens = list(markup_tokens([raw]))
        ancestors: list[str] = []
        for position, token in enumerate(tokens):
            if token[0] == "open":
                if token[1] in _BODY_MEDIA:
                    payload_offsets.append(offset + position)
                ancestors.append(token[1])
            elif token[0] == "close":
                ancestors.pop()
            elif _has_text(token[1]) and not any(
                re.fullmatch(r"h[1-6]", name) for name in ancestors
            ):
                payload_offsets.append(offset + position)
        for tag, start in zip(_headings(raw), _heading_offsets(tokens), strict=True):
            title = tag.get_text(" ", strip=True)
            semantic = _NUMBERED_SECTION.match(
                _title_key(title)
            ) or _FRONT_MATTER.match(_title_key(title))
            headings.append(
                _Heading(
                    title,
                    int(tag.name.rsplit(":", 1)[-1][1]),
                    href,
                    block_id,
                    semantic.group(1) if semantic else None,
                    offset + start,
                    required=semantic is not None,
                )
            )
        offset += len(tokens)
        document_ends[href] = offset

    targets: dict[tuple[str, str], list[int]] = {}
    for index, heading in enumerate(headings):
        key = (normpath(unquote(heading.href)), _title_key(heading.title))
        targets.setdefault(key, []).append(index)

    toc_parents: dict[int, int | None] = {}
    toc_stack: list[tuple[int, int | None]] = []
    for entry in toc:
        while toc_stack and toc_stack[-1][0] >= entry.depth:
            toc_stack.pop()
        file, _ = urldefrag(entry.href)
        file = normpath(unquote(file))
        candidates = targets.get((file, _title_key(entry.title)), [])
        target = next(
            (
                index
                for index in candidates
                if headings[index].block_id == entry.block_id
            ),
            None,
        )
        if target is None and len(candidates) == 1:
            target = candidates[0]
        if target is not None:
            headings[target].required = True
            if not toc_stack or toc_stack[-1][1] is not None:
                parent = toc_stack[-1][1] if toc_stack else None
                if parent != target:
                    toc_parents.setdefault(target, parent)
        toc_stack.append((entry.depth, target))

    stack: list[int] = []
    document: str | None = None
    for index, heading in enumerate(headings):
        if heading.href != document:
            stack.clear()
            document = heading.href
        if not heading.required:
            continue
        while stack and headings[stack[-1]].level >= heading.level:
            stack.pop()
        heading.parent = stack[-1] if stack else None
        if index in toc_parents:
            parent = toc_parents[index]
            if (
                parent is not None
                or heading.parent is None
                or headings[heading.parent].kind is None
            ):
                heading.parent = parent
        stack.append(index)

    body: set[int] = set()
    cursor = 0
    for index, heading in enumerate(headings):
        end = next(
            (
                following.start
                for following in islice(headings, index + 1, None)
                if following.required or following.level <= heading.level
            ),
            document_ends[heading.href],
        )
        end = min(end, document_ends[heading.href])
        while cursor < len(payload_offsets) and payload_offsets[cursor] < heading.start:
            cursor += 1
        if cursor < len(payload_offsets) and payload_offsets[cursor] < end:
            parent: int | None = index
            while parent is not None and parent not in body:
                body.add(parent)
                parent = headings[parent].parent
    for index, heading in enumerate(headings):
        heading.required = heading.required and index in body

    if not any(heading.required for heading in headings):
        return

    paths: list[tuple[StructureNode, ...]] = []
    spans: list[tuple[int, int, tuple[StructureNode, ...]]] = []

    def visit(
        nodes: Iterable[StructureNode], ancestors: tuple[StructureNode, ...] = ()
    ) -> None:
        for node in nodes:
            path = (*ancestors, node)
            for item in node.content:
                if isinstance(item, StructureNode):
                    visit([item], path)
                elif isinstance(item, TextContent):
                    paths.extend(path for _ in _headings(item.raw_text))
                    start = spans[-1][1] if spans else 0
                    spans.append(
                        (
                            start,
                            start + sum(1 for _ in markup_tokens([item.raw_text])),
                            path,
                        )
                    )

    visit(book.structure)
    if len(paths) != len(headings):
        raise ValueError(
            "Source hierarchy heading boundaries changed; preserve heading markup and order"
        )

    resolved: dict[int, tuple[StructureNode, ...]] = {}
    problems: list[str] = []
    for index, heading in enumerate(headings):
        if not heading.required:
            continue
        matches = [
            depth
            for depth, node in enumerate(paths[index])
            if _title_key(node.structure_title) == _title_key(heading.title)
        ]
        if not matches:
            parent = (
                f" beneath {headings[heading.parent].title!r}"
                if heading.parent is not None
                else ""
            )
            problems.append(
                f"heading {heading.title!r} needs its own {heading.kind or 'structure'} node{parent}"
            )
            continue
        depth = next(
            (
                depth
                for depth in matches
                if isinstance(paths[index][depth].content[0], StructureNode)
            ),
            matches[-1],
        )
        resolved[index] = paths[index][: depth + 1]
        if (
            heading.kind is not None
            and _title_key(paths[index][depth].structure_type) != heading.kind
        ):
            problems.append(
                f"heading {heading.title!r} must use structure_type={heading.kind!r}"
            )

    structural_nodes = {id(path[-1]) for path in resolved.values()}
    for index, path in resolved.items():
        parent = headings[index].parent
        if parent is None or parent not in resolved:
            continue
        ancestor = next(
            (node for node in reversed(path[:-1]) if id(node) in structural_nodes), None
        )
        if ancestor is not resolved[parent][-1]:
            problems.append(
                f"{headings[index].title!r} must be a child of {headings[parent].title!r}, preserving the source hierarchy"
            )

    cursor = 0
    for index, path in resolved.items():
        heading = headings[index]
        end = next(
            (
                following.start
                for following in islice(headings, index + 1, None)
                if following.required or following.level <= heading.level
            ),
            document_ends[heading.href],
        )
        end = min(end, document_ends[heading.href])
        while cursor < len(spans) and spans[cursor][1] <= heading.start:
            cursor += 1
        current = cursor
        while current < len(spans) and spans[current][0] < end:
            if not any(node is path[-1] for node in spans[current][2]):
                problems.append(
                    f"content following {heading.title!r} must remain inside its structure node"
                )
                break
            current += 1
    if problems:
        diagnostic = "; ".join(problems[:10])
        if len(problems) > 10:
            diagnostic += f"; and {len(problems) - 10} more hierarchy issues"
        raise ValueError(f"Source hierarchy flattened or mislabeled: {diagnostic}")
