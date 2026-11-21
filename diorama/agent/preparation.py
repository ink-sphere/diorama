"""Reader-facing observations from one EPUB ingestion."""

import asyncio
import json
import os
import stat
from collections.abc import Awaitable, Callable, Iterator
from contextlib import suppress
from dataclasses import dataclass
from itertools import islice
from pathlib import Path
from urllib.parse import unquote

from diorama.agent.tools import Document, SourceIndex, read_passage
from diorama.schemas.book import Section, SourceRange

PREVIEW_LIMIT = 10
type Outline = list[tuple[int, str]]


@dataclass(frozen=True)
class PreparationUpdate:
    """An observed milestone with optional replacement contents and source excerpts."""

    phase: str
    note: str
    outline: Outline | None = None
    previews: list[dict] | None = None


type ProgressListener = Callable[[PreparationUpdate], None]


def source_titles(index: SourceIndex) -> set[str]:
    titles = {entry.title for entry in index.navigation}
    titles.update(h.title for doc in index.spine if doc.linear for h in doc.headings)
    return titles


def excerpt(index: SourceIndex, doc: Document, start: int) -> dict:
    end = next((h.offset for h in doc.headings if h.offset > start), len(doc.text))
    source = SourceRange(
        occurrence=doc.occurrence,
        resource=doc.resource,
        start=start,
        end=min(end, start + 320),
    )
    return {
        "key": f"{doc.occurrence}:{start}",
        "source": source.model_dump(),
        "text": read_passage(index, source),
    }


def opening_update(index: SourceIndex) -> PreparationUpdate:
    entries = []
    for doc in index.spine:
        if not doc.linear or not doc.text.strip():
            continue
        first = next(
            (h for h in doc.headings if h.title and h.offset < len(doc.text)), None
        )
        if first and not doc.text[: first.offset].strip():
            title, start = first.title[:200], first.offset
        else:
            title, start = "Opening of this edition", 0
        entries.append({"title": title, "depth": 0, **excerpt(index, doc, start)})
        break
    return PreparationUpdate(
        "queued",
        "Saved the EPUB to your shelf.",
        [(0, row["title"]) for row in entries],
        entries,
    )


def source_previews(index: SourceIndex, outline: Outline) -> list[dict]:
    headings = {}
    for doc in index.spine:
        if doc.linear:
            for h in doc.headings:
                if h.offset < len(doc.text):
                    headings.setdefault(h.title, []).append((doc, h))
    entries = []
    for position, (depth, title) in enumerate(outline):
        matches = headings.get(title, [])
        entry = {"depth": depth, "title": title, "key": f"label:{position}:{title}"}
        if len(matches) == 1 and sum(t == title for _, t in outline) == 1:
            doc, heading = matches[0]
            targets = [
                n.target.partition("#") for n in index.navigation if n.title == title
            ]
            if all(
                path == doc.resource
                and (not fragment or unquote(fragment) == heading.anchor)
                for path, _, fragment in targets
            ):
                entry.update(excerpt(index, doc, heading.offset))
        entries.append(entry)
    return entries


def section_preview(root: Section) -> Outline:
    def sections(parent: Section, depth: int) -> Iterator[tuple[int, str]]:
        for child in parent.children:
            if isinstance(child, Section):
                yield depth, (child.title or child.label)[:200]
                yield from sections(child, depth + 1)

    return list(islice(sections(root, 0), PREVIEW_LIMIT)) or [
        (0, (root.title or root.label)[:200])
    ]


def read_preview(path: Path, titles: set[str]) -> Outline:
    """Accept a bounded outline containing only headings present in the EPUB."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size > 16384:
                return []
            rows = json.loads(os.read(fd, 16385))
        finally:
            os.close(fd)
        if not isinstance(rows, list) or not 0 < len(rows) <= PREVIEW_LIMIT:
            return []
        outline = []
        previous_depth = -1
        for row in rows:
            if not isinstance(row, list) or len(row) != 2:
                return []
            depth, title = row
            if (
                type(depth) is not int
                or not 0 <= depth <= previous_depth + 1
                or not isinstance(title, str)
                or not title.strip()
                or title not in titles
            ):
                return []
            outline.append((depth, title[:200]))
            previous_depth = depth
        return outline
    except (OSError, ValueError, RecursionError):
        return []


async def follow_outline(
    turn: Awaitable[None], scratch: Path, index: SourceIndex, report: ProgressListener
) -> None:
    """Observe growing outline snapshots while the worker runs its normal turn."""
    titles = source_titles(index)
    previous: Outline = []
    task = asyncio.ensure_future(turn)
    try:
        while not task.done():
            await asyncio.wait([task], timeout=0.5)
            outline = read_preview(scratch / "preview.json", titles)
            if outline and outline != previous and len(outline) >= len(previous):
                report(
                    PreparationUpdate(
                        "discovered",
                        f"Mapped “{outline[-1][1]}” in the contents.",
                        outline,
                        source_previews(index, outline),
                    )
                )
                previous = outline
        await task
    finally:
        if not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
