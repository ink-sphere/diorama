"""Pydantic models for the library API.

``BookRecord`` is the durable shape persisted to ``.diorama_data/library.json``.
``TraceLine`` is the ephemeral shape streamed over SSE while a book is being
processed by the :class:`~diorama.agents.ebook_loader.EbookLoaderAgent` — it is
never written to disk, only broadcast to whichever browser tab is watching.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

BookStatus = Literal["queued", "processing", "ready", "failed"]
TraceKind = Literal["status", "thinking", "tool", "progress", "done", "error"]
TraceStatus = Literal["pending", "done", "error"]


class Coverage(BaseModel):
    covered: bool
    total_blocks: int
    assigned_blocks: int


class ReadingProgress(BaseModel):
    """Where the reader left off in a book.

    ``section_index`` indexes the book's leaf sections in depth-first reading order —
    the same order :func:`~diorama.backend.routes.books.get_structure` serialises the
    tree in, so it stays valid as long as the structure isn't re-extracted. ``page`` is
    the page within that section, which depends on the reader's current type size and
    viewport, hence ``pages`` alongside it: a client whose pagination no longer matches
    can scale the position instead of jumping to the wrong page.

    ``scene_index`` is the *stable* half of the position. Since a scene always begins a
    new page, a reader is always inside exactly one of them, and which one doesn't
    change when the type size does — so reopening a book at a different size can land
    on the right scene instead of a ratio-scaled guess at the right page. Optional: it
    is None for a book with no scene segmentation, and for positions saved before
    scenes existed.
    """

    section_index: int = 0
    scene_index: int | None = None
    page: int = 0
    pages: int = 1
    percent: float = 0.0
    updated_at: str | None = None


class BookRecord(BaseModel):
    """One entry on the shelf, persisted in ``library.json``."""

    id: str
    title: str
    author: str | None = None
    source_filename: str
    status: BookStatus = "queued"
    created_at: str
    finished_at: str | None = None
    level_types: list[str] = Field(default_factory=list)
    structure_line: str | None = None
    breakdown: dict[str, int] = Field(default_factory=dict)
    top_level_count: int | None = None
    coverage: Coverage | None = None
    cost_usd: float | None = None
    #: Total scenes the segmentation pass found across every section. None when the
    #: book predates scene segmentation or its pass produced nothing — which is not
    #: the same claim as zero scenes.
    scene_count: int | None = None
    error: str | None = None
    progress: ReadingProgress | None = None


class TraceLine(BaseModel):
    """One line of the live agent trace, streamed to the shelf card.

    ``done``/``total`` are set only on ``kind="progress"`` rows, where the frontend
    draws a bar instead of a log line. Scene segmentation runs once per section — a
    long book's worth of tool calls that nobody wants to read — so it reports a count
    rather than a trace, and re-publishing the same ``id`` advances the bar in place.
    """

    id: str
    kind: TraceKind
    status: TraceStatus = "done"
    text: str
    tool: str | None = None
    done: int | None = None
    total: int | None = None
    at: float


class UploadResponse(BaseModel):
    book: BookRecord
    stream_url: str


__all__ = [
    "BookRecord",
    "BookStatus",
    "Coverage",
    "ReadingProgress",
    "TraceLine",
    "UploadResponse",
]
