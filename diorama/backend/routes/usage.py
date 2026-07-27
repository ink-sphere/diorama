"""Cost-tracking endpoints: the dashboard overview and one book's call-level trace.

Both are pure reads over the append-only ledgers in
:mod:`diorama.backend.usage_store`. Aggregation is a full scan folded in Python, so
it runs in a threadpool rather than on the event loop — a shelf with a lot of history
would otherwise stall every other request while it counted.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from starlette.concurrency import run_in_threadpool

from diorama.backend.store import get_book
from diorama.backend.usage_store import (
    BookUsage,
    UsageSummary,
    build_book_usage,
    build_summary,
)

router = APIRouter(prefix="/api/usage", tags=["usage"])


@router.get("")
async def get_usage_summary() -> UsageSummary:
    """Totals, per-model/provider/agent breakdowns, the daily trend, and book rows.

    An empty summary is a normal answer, not an error: a fresh install has processed
    no books, and a shelf whose books all predate cost tracking has no ledgers to read.
    """
    return await build_summary()


@router.get("/books/{book_id}")
async def get_book_usage(book_id: str) -> BookUsage:
    """One book's cost page: its runs, its breakdowns, and every LLM call it made.

    Raises:
        HTTPException: 404 when the book has no ledger — either it does not exist, or
            it was processed before cost tracking and only has an aggregate on its
            shelf record. The frontend distinguishes those two by the shelf record it
            already holds; both mean "no call detail to show here".
    """
    book = await get_book(book_id)
    usage = await run_in_threadpool(build_book_usage, book_id, book)
    if usage is None:
        raise HTTPException(404, "No recorded LLM calls for this book.")
    return usage
