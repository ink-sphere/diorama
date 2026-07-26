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
TraceKind = Literal["status", "thinking", "tool", "done", "error"]
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
    """

    section_index: int = 0
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
    error: str | None = None
    progress: ReadingProgress | None = None


class TraceLine(BaseModel):
    """One line of the live agent trace, streamed to the shelf card."""

    id: str
    kind: TraceKind
    status: TraceStatus = "done"
    text: str
    tool: str | None = None
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
