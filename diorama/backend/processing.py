"""Runs a book's two processing agents and fans their progress out to SSE subscribers.

Processing is two phases against one book, sharing one run id in the cost ledger:

1. :class:`~diorama.agents.ebook_loader.EbookLoaderAgent` maps the book's hierarchy,
   its every move translated into a :class:`~diorama.backend.models.TraceLine` and
   published live. This phase is load-bearing — if it fails, the book fails.
2. :class:`~diorama.agents.ebook_scene_segmentation.EbookSceneSegmentationAgent` then
   runs **once per leaf section**, marking the scene boundaries a later illustration
   will hang on. That is dozens of agent runs on a long book, so it reports a single
   ``kind="progress"`` line that advances in place rather than a trace nobody would
   read. This phase is *not* load-bearing: the structure is already on disk and the
   book is readable without scenes, so a section that won't segment falls back to one
   whole-section scene and the book still lands as ready.

One book is processed at most once: the first client to open its event stream triggers
a background task; later subscribers (a page refresh, a second tab) join the same run
and immediately replay everything logged so far, then keep receiving the live tail.
This is in-memory only — restart the server and an in-flight run is gone, which is
fine for a personal single-process tool.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from diorama.agents.ebook_loader import EbookLoaderAgent, EbookLoaderError
from diorama.agents.ebook_scene_segmentation import EbookSceneSegmentationAgent
from diorama.backend.models import Coverage, TraceLine
from diorama.backend.settings import resolve_agent_runtime
from diorama.backend.store import (
    get_book,
    scenes_path,
    structure_path,
    upload_path,
    upsert_book,
)
from diorama.backend.trace import event_to_trace_line
from diorama.backend.usage_store import make_sink, run_cost
from diorama.ebook.models import EbookStructure
from diorama.ebook.scenes import (
    BookScenes,
    SceneSegmentation,
    iter_leaves,
    single_scene,
    split_paragraphs,
)
from diorama.models.usage import new_run_id

logger = logging.getLogger("diorama.backend")

#: The settings-registry keys for the two agents this module runs.
LOADER_AGENT_ID = "ebook_loader"
SEGMENTATION_AGENT_ID = "ebook_scene_segmentation"

#: How many sections are segmented at once. The sections are independent, so this is
#: pure wall-clock win on a long book; the cap is what keeps a 40-chapter novel from
#: opening 40 simultaneous requests and tripping a provider rate limit.
SCENE_CONCURRENCY = 4


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


def _scene_progress_line(
    book_id: str,
    done: int,
    total: int,
    failed: int,
    *,
    scene_count: int | None = None,
) -> TraceLine:
    """The single, re-published progress row for the whole segmentation phase.

    The id is stable, so every update replaces the previous row in the shelf's
    merge-by-id log rather than appending — one bar that fills, not N lines.

    ``scene_count`` is what marks the phase finished, rather than ``done == total``:
    the last section's completion and the phase's result are two different moments,
    and only the second one knows how many scenes came out of it.
    """
    finished = scene_count is not None
    if not finished:
        text = f"Marking scene boundaries — {done} of {total} sections"
    elif failed:
        text = (
            f"Marked {scene_count} scenes across {total - failed} of {total} sections "
            f"({failed} couldn't be split)"
        )
    else:
        text = f"Marked {scene_count} scenes across {total} sections"
    return TraceLine(
        id=f"scenes-{book_id}",
        kind="progress",
        status="done" if finished else "pending",
        text=text,
        done=done,
        total=total,
        at=time.time(),
    )


async def _segment_scenes(
    book_id: str, run_id: str, structure: EbookStructure, run: _BookRun
) -> BookScenes:
    """Segment every leaf section into scenes, publishing one live progress row.

    Runs :data:`SCENE_CONCURRENCY` sections at a time. A section whose run fails is
    logged and falls back to a single whole-section scene rather than taking the book
    down with it — by this point the structure is already saved and the book is
    readable, so trading a whole shelf entry for one stubborn chapter's scene cuts
    would be a bad deal. Results are written back by index, so out-of-order completions
    still come out in reading order.
    """
    leaves = list(iter_leaves(structure))
    total = len(leaves)
    if total == 0:
        return BookScenes(title=structure.title, author=structure.author)

    # Resolved per run (settings → env → default), independently of the loader's — the
    # segmentation agent runs once per section, so it is the one worth pointing at a
    # cheaper model.
    model_id, api_key = await resolve_agent_runtime(SEGMENTATION_AGENT_ID)
    agent = EbookSceneSegmentationAgent(
        model_id=model_id,
        api_key=api_key,
        usage_sink=make_sink(book_id),
        book_id=book_id,
        # Shares the loader's run id: one upload is one run in the ledger, with
        # agent_id telling the two phases apart on the cost page.
        run_id=run_id,
    )

    results: list[SceneSegmentation | None] = [None] * total
    semaphore = asyncio.Semaphore(SCENE_CONCURRENCY)
    done = 0
    failed = 0

    run.publish(_scene_progress_line(book_id, 0, total, 0))

    async def segment(index: int, node) -> None:
        nonlocal done, failed
        async with semaphore:
            try:
                results[index] = await agent.segment_node(
                    node, book_title=structure.title
                )
            except Exception:  # noqa: BLE001 — one section must not sink the book
                logger.exception(
                    "Scene segmentation failed for book %s section %s", book_id, index
                )
                failed += 1
                results[index] = single_scene(
                    split_paragraphs(node.text or ""), node=node
                )
            finally:
                done += 1
                run.publish(_scene_progress_line(book_id, done, total, failed))

    await asyncio.gather(*(segment(i, node) for i, node in enumerate(leaves)))

    segmentations = [s for s in results if s is not None]
    book_scenes = BookScenes(
        title=structure.title,
        author=structure.author,
        segmentations=segmentations,
        cost_usd=round(sum(s.cost_usd for s in segmentations), 6),
    )
    run.publish(
        _scene_progress_line(
            book_id, total, total, failed, scene_count=book_scenes.scene_count
        )
    )
    return book_scenes


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

    # Phase two. Written to disk before the record is updated, but deliberately outside
    # the failure path above: the structure is already saved, so nothing that happens
    # here can cost the reader their book.
    try:
        scenes = await _segment_scenes(book_id, run_id, structure, run)
        scenes_path(book_id).write_text(scenes.model_dump_json(indent=2))
        book.scene_count = scenes.scene_count
    except Exception:  # noqa: BLE001 — the book is readable with or without scenes
        logger.exception("Scene segmentation failed for book %s", book_id)
        run.publish(
            TraceLine(
                id=f"scenes-{book_id}",
                kind="progress",
                status="error",
                text="Couldn't mark scene boundaries — the book is still readable.",
                at=time.time(),
            )
        )

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
    # Read back from the ledger rather than off the structure, so the card's figure
    # covers both phases — the loader's turns *and* every section's segmentation run.
    # Falls back to the loader's own total if the ledger has nothing (no sink, or a
    # book being reprocessed by a caller that didn't install one).
    book.cost_usd = run_cost(book_id, run_id) or structure.cost_usd
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
