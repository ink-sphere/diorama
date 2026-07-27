"""Runs EbookLoaderAgent per book and fans its trace out to SSE subscribers.

One book is processed at most once: the first client to open its event stream
triggers a background task; later subscribers (a page refresh, a second tab) join
the same run and immediately replay everything logged so far, then keep receiving
the live tail. This is in-memory only — restart the server and an in-flight run is
gone, which is fine for a personal single-process tool.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from diorama.agents.ebook_loader import EbookLoaderAgent, EbookLoaderError
from diorama.backend.models import Coverage, TraceLine
from diorama.backend.settings import resolve_agent_runtime
from diorama.backend.store import get_book, structure_path, upload_path, upsert_book
from diorama.backend.trace import event_to_trace_line
from diorama.backend.usage_store import make_sink, run_cost
from diorama.models.usage import new_run_id

logger = logging.getLogger("diorama.backend")

#: The settings-registry key for the agent this module runs.
LOADER_AGENT_ID = "ebook_loader"


def _user_facing_error(exc: Exception) -> str:
    """Strip the agent's own class name/preamble, keeping the concrete reason.

    ``EbookLoaderError`` reads as
    ``"EbookLoaderAgent did not submit a valid structure for '<title>' (<reason>)"``
    — useful in logs, but ``EbookLoaderAgent`` is an implementation detail no reader
    of the shelf should see. The parenthesised reason is the part worth keeping.
    """
    if isinstance(exc, EbookLoaderError):
        text = str(exc)
        if "(" in text and text.endswith(")"):
            reason = text[text.index("(") + 1 : -1]
            return f"Diorama couldn't map this book's structure: {reason}"
        return "Diorama couldn't map this book's structure."
    return "Something went wrong while reading this book."


# End-of-stream sentinel pushed to every subscriber queue once a run settles.
DONE = object()


class _BookRun:
    """Shared state for one book's in-flight (or finished) processing run."""

    def __init__(self) -> None:
        self.log: list[TraceLine] = []
        self.subscribers: list[asyncio.Queue] = []
        self.finished = False
        self.task: asyncio.Task | None = None

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        for line in self.log:
            queue.put_nowait(line)
        if self.finished:
            queue.put_nowait(DONE)
        else:
            self.subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        if queue in self.subscribers:
            self.subscribers.remove(queue)

    def publish(self, line: TraceLine) -> None:
        self.log.append(line)
        for queue in self.subscribers:
            queue.put_nowait(line)

    def close(self) -> None:
        self.finished = True
        for queue in self.subscribers:
            queue.put_nowait(DONE)
        self.subscribers.clear()


_runs: dict[str, _BookRun] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _run_book(book_id: str, run: _BookRun) -> None:
    book = await get_book(book_id)
    if book is None:
        run.close()
        return

    book.status = "processing"
    await upsert_book(book)

    # Resolved per run (settings → env → default), so changing the model or key on
    # the settings page takes effect on the next book without a server restart.
    model_id, api_key = await resolve_agent_runtime(LOADER_AGENT_ID)
    # Every LLM call this run makes is appended to the book's cost ledger as it
    # completes — so a run that fails halfway still leaves an accurate account of what
    # it spent getting there, and a retry appends a second run rather than erasing the
    # first. The failure path below deliberately doesn't clean any of it up.
    run_id = new_run_id()
    loader = EbookLoaderAgent(
        model_id=model_id,
        api_key=api_key,
        usage_sink=make_sink(book_id),
        book_id=book_id,
        run_id=run_id,
    )
    epub_path = upload_path(book_id)
    try:
        events, finalize = loader.stream_load(epub_path)
        async for event in events:
            line = event_to_trace_line(event)
            if line is not None:
                run.publish(line)
        structure = finalize()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Processing failed for book %s", book_id)
        message = _user_facing_error(exc)
        book.status = "failed"
        book.error = message
        book.finished_at = _now_iso()
        # A run that died still spent money getting there. The agent raised before
        # its cumulative totals could be read, but the ledger already has every call
        # it made — so read the cost back from there rather than leaving the card
        # blank, which would read as "this cost nothing".
        book.cost_usd = run_cost(book_id, run_id) or None
        await upsert_book(book)
        run.publish(
            TraceLine(
                id=f"error-{book_id}",
                kind="error",
                status="error",
                text=message,
                at=time.time(),
            )
        )
        run.close()
        return

    structure_path(book_id).write_text(structure.model_dump_json(indent=2))

    breakdown = _tally_levels(structure.root)

    book.title = structure.title or book.title
    book.author = structure.author
    book.status = "ready"
    book.finished_at = _now_iso()
    book.level_types = structure.level_types
    book.top_level_count = len(structure.root)
    book.breakdown = breakdown
    book.structure_line = _structure_line(breakdown)
    book.coverage = Coverage(
        covered=structure.coverage.covered,
        total_blocks=structure.coverage.total_blocks,
        assigned_blocks=structure.coverage.assigned_blocks,
    )
    book.cost_usd = structure.cost_usd
    book.error = None
    await upsert_book(book)

    run.publish(
        TraceLine(
            id=f"done-{book_id}",
            kind="done",
            status="done",
            text=book.structure_line or "Structure extracted.",
            at=time.time(),
        )
    )
    run.close()


def _singularize(level_type: str) -> str:
    """Best-effort singular form, so "chapter" and "chapters" tally together.

    The agent picks its own ``level_type`` label per node and isn't always
    consistent about number across a tree (observed: mostly "chapter" nodes plus
    one stray "chapters"). ``_structure_line`` re-pluralizes from the count, so
    counting under the singular form is enough to make the display grammatical.
    """
    lt = level_type.strip().lower()
    return lt[:-1] if lt.endswith("s") and len(lt) > 1 else lt


def _tally_levels(nodes: list, counts: dict[str, int] | None = None) -> dict[str, int]:
    counts = {} if counts is None else counts
    for node in nodes:
        key = _singularize(node.level_type)
        counts[key] = counts.get(key, 0) + 1
        _tally_levels(node.children, counts)
    return counts


def _structure_line(breakdown: dict[str, int]) -> str:
    """Summarise a book's level breakdown, e.g. "5 acts · 22 scenes".

    Singleton levels (count == 1) are almost always just the whole-book wrapper
    node ("1 book", "1 preamble") rather than a fact worth reporting, so they're
    dropped whenever at least one other level has more than one — falling back to
    showing everything only if every level happens to be a singleton.
    """
    informative = {level: count for level, count in breakdown.items() if count > 1}
    parts = [
        f"{count} {level}{'s' if count != 1 else ''}"
        for level, count in (informative or breakdown).items()
    ]
    return " · ".join(parts) if parts else "Structure extracted"


def ensure_started(book_id: str) -> _BookRun:
    """Start (or return the existing) background run for ``book_id``."""
    run = _runs.get(book_id)
    if run is None:
        run = _BookRun()
        _runs[book_id] = run
        run.task = asyncio.create_task(_run_book(book_id, run))
    return run


def reset(book_id: str) -> None:
    """Drop any finished/failed run so the next ``ensure_started`` starts fresh."""
    _runs.pop(book_id, None)


__all__ = ["DONE", "ensure_started", "reset"]
