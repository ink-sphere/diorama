"""Library endpoints: list the shelf, upload an epub, stream a book's live trace."""

from __future__ import annotations

import json
import mimetypes
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Response, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from diorama.backend import research as research_runs
from diorama.backend.models import BookRecord, ReadingProgress, UploadResponse
from diorama.backend.processing import DONE, ensure_started, reset
from diorama.backend.research import ResearchRecord, StyleDirection
from diorama.backend.store import (
    cached_cover,
    cover_path,
    delete_book,
    get_book,
    list_books,
    scenes_path,
    structure_path,
    upload_path,
    upsert_book,
)
from diorama.ebook.cover import extract_cover
from diorama.ebook.models import EbookStructure
from diorama.ebook.scenes import BookScenes

router = APIRouter(prefix="/api/books", tags=["books"])

_MAX_UPLOAD_BYTES = 100 * 1024 * 1024


@router.get("")
async def get_library() -> list[BookRecord]:
    return await list_books()


@router.post("", status_code=202)
async def upload_book(file: UploadFile) -> UploadResponse:
    filename = file.filename or "book.epub"
    if not filename.lower().endswith(".epub"):
        raise HTTPException(400, "Only .epub files are supported.")

    body = await file.read()
    if not body:
        raise HTTPException(400, "The uploaded file is empty.")
    if len(body) > _MAX_UPLOAD_BYTES:
        raise HTTPException(413, "That file is larger than the 100MB limit.")

    book_id = uuid.uuid4().hex[:12]
    upload_path(book_id).write_bytes(body)

    record = BookRecord(
        id=book_id,
        title=filename.rsplit(".", 1)[0],
        source_filename=filename,
        status="queued",
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    await upsert_book(record)
    return UploadResponse(book=record, stream_url=f"/api/books/{book_id}/stream")


@router.get("/{book_id}")
async def get_book_record(book_id: str) -> BookRecord:
    book = await get_book(book_id)
    if book is None:
        raise HTTPException(404, "Book not found.")
    return book


@router.get("/{book_id}/structure")
async def get_structure(book_id: str) -> EbookStructure:
    """The extracted structure the reader paginates — only exists once ready."""
    book = await get_book(book_id)
    if book is None:
        raise HTTPException(404, "Book not found.")
    path = structure_path(book_id)
    if not path.exists():
        raise HTTPException(409, "This book hasn't finished processing yet.")
    return EbookStructure.model_validate_json(path.read_text())


@router.get("/{book_id}/scenes")
async def get_scenes(book_id: str) -> BookScenes:
    """The book's scene boundaries, one entry per leaf section.

    **404 is a normal answer**, not a fault: a book shelved before scene segmentation
    existed has none, and so does one whose segmentation pass failed — in both cases
    the book is complete and readable, it just has no scenes yet. 409 is reserved for
    a book that is still being processed, where asking again later will work.
    """
    book = await get_book(book_id)
    if book is None:
        raise HTTPException(404, "Book not found.")
    path = scenes_path(book_id)
    if not path.exists():
        if book.status in ("queued", "processing"):
            raise HTTPException(409, "This book hasn't finished processing yet.")
        raise HTTPException(404, "This book has no scene segmentation.")
    return BookScenes.model_validate_json(path.read_text())


class StyleChoice(BaseModel):
    """Which of the two candidate art directions the reader picked."""

    direction: StyleDirection


@router.get("/{book_id}/research")
async def get_research(book_id: str) -> ResearchRecord:
    """A book's moodboard artifacts, if anyone has researched it.

    **404 is the normal answer**, not a fault: research is lazy, so most books have
    none until someone opens their moodboard. There is no 409 here — "in flight" is a
    fact about the stream, not the record, and a record only exists once a run settles.
    """
    book = await get_book(book_id)
    if book is None:
        raise HTTPException(404, "Book not found.")
    record = research_runs.read_record(book_id)
    if record is None:
        raise HTTPException(404, "This book hasn't been researched yet.")
    return record


@router.post("/{book_id}/research/retry", status_code=202)
async def retry_research(book_id: str) -> Response:
    """Research this book again, from scratch.

    A retry is always a **full** re-run rather than a resume of the missing artifacts:
    the agent has no mid-run resume, and a dossier from one run with style bibles from
    another are not written to fit each other.

    The run starts here rather than on the next stream open, because the stream
    short-circuits for a book that already has a complete record — retrying a finished
    moodboard would otherwise do nothing at all. A run already in flight is left to
    finish. The old record survives a failed retry, and the old run's ledger entries
    stay either way, exactly like an upload retry.
    """
    book = await get_book(book_id)
    if book is None:
        raise HTTPException(404, "Book not found.")
    if not upload_path(book_id).exists():
        raise HTTPException(409, "The original upload is no longer available.")
    research_runs.restart(book_id)
    return Response(status_code=202)


@router.patch("/{book_id}/research/style")
async def choose_style(book_id: str, choice: StyleChoice) -> ResearchRecord:
    """Set the active style bible. A metadata edit, never a re-run."""
    record = research_runs.read_record(book_id)
    if record is None:
        raise HTTPException(404, "This book hasn't been researched yet.")
    if record.style_bibles is None:
        raise HTTPException(409, "This book's research has no art directions yet.")
    if choice.direction == "traditional" and not record.has_traditional:
        raise HTTPException(
            422,
            "This book has no traditional art direction — nothing in its "
            "illustration history to draw one from.",
        )
    record.chosen_direction = choice.direction
    return research_runs.write_record(record)


@router.get("/{book_id}/research/stream")
async def stream_research(book_id: str) -> StreamingResponse:
    """The research run's live trace, starting the run if it isn't going yet.

    Opening the moodboard *is* the request to research, so this endpoint starts work
    as a side effect — but only when there is work to do. A book with a complete
    record replays a single already-done line and closes, which keeps a reopened
    modal, a second tab and a page refresh from ever racing to start rival runs.
    """
    book = await get_book(book_id)
    if book is None:
        raise HTTPException(404, "Book not found.")
    if not upload_path(book_id).exists():
        raise HTTPException(409, "The original upload is no longer available.")

    record = research_runs.read_record(book_id)

    async def event_source():
        # A complete record and no live run means there is nothing to watch. A
        # *partial* record does not short-circuit: the reader may have just asked to
        # retry, and the run is what they are here to see.
        if (
            record is not None
            and record.status == "complete"
            and not research_runs.is_running(book_id)
        ):
            payload = {
                "id": f"research-replay-{book_id}",
                "kind": "done",
                "status": "done",
                "text": "Already researched.",
                "at": 0,
            }
            yield f"data: {json.dumps(payload)}\n\n"
            return

        run = research_runs.ensure_started(book_id)
        queue = run.subscribe()
        try:
            while True:
                item = await queue.get()
                if item is research_runs.DONE:
                    break
                yield f"data: {item.model_dump_json()}\n\n"
        finally:
            run.unsubscribe(queue)

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/{book_id}/cover")
async def get_cover(book_id: str) -> Response:
    """The EPUB's own cover image, extracted on first request and then cached.

    A book without a cover is a normal outcome, not an error the shelf should retry:
    it's cached as a ``.none`` marker and answered with 404 so the frontend can fall
    back to its typographic cover.
    """
    book = await get_book(book_id)
    if book is None:
        raise HTTPException(404, "Book not found.")

    cache_headers = {"Cache-Control": "public, max-age=86400"}
    cached = cached_cover(book_id)
    if cached is None:
        source = upload_path(book_id)
        if not source.exists():
            raise HTTPException(404, "The original upload is no longer available.")
        try:
            found = await run_in_threadpool(extract_cover, source)
        except Exception:  # noqa: BLE001 — a malformed epub is a missing cover
            found = None
        if found is None:
            cover_path(book_id, ".none").write_bytes(b"")
            raise HTTPException(404, "This book has no cover image.")
        content, media_type = found
        suffix = mimetypes.guess_extension(media_type) or ".img"
        cached = cover_path(book_id, suffix)
        cached.write_bytes(content)
        return Response(content, media_type=media_type, headers=cache_headers)

    if cached.suffix == ".none":
        raise HTTPException(404, "This book has no cover image.")
    media_type = mimetypes.guess_type(cached.name)[0] or "application/octet-stream"
    return Response(cached.read_bytes(), media_type=media_type, headers=cache_headers)


@router.patch("/{book_id}/progress")
async def update_progress(book_id: str, progress: ReadingProgress) -> BookRecord:
    book = await get_book(book_id)
    if book is None:
        raise HTTPException(404, "Book not found.")
    progress.updated_at = datetime.now(timezone.utc).isoformat()
    book.progress = progress
    await upsert_book(book)
    return book


@router.delete("/{book_id}", status_code=204)
async def remove_book(book_id: str) -> Response:
    reset(book_id)
    research_runs.reset(book_id)
    if not await delete_book(book_id):
        raise HTTPException(404, "Book not found.")
    return Response(status_code=204)


@router.post("/{book_id}/retry", status_code=202)
async def retry_book(book_id: str) -> BookRecord:
    book = await get_book(book_id)
    if book is None:
        raise HTTPException(404, "Book not found.")
    if not upload_path(book_id).exists():
        raise HTTPException(409, "The original upload is no longer available.")
    reset(book_id)
    book.status = "queued"
    book.error = None
    await upsert_book(book)
    return book


@router.get("/{book_id}/stream")
async def stream_book(book_id: str) -> StreamingResponse:
    book = await get_book(book_id)
    if book is None:
        raise HTTPException(404, "Book not found.")

    async def event_source():
        if book.status in ("ready", "failed"):
            payload = {
                "id": f"replay-{book_id}",
                "kind": "done" if book.status == "ready" else "error",
                "status": "done" if book.status == "ready" else "error",
                "text": book.structure_line or book.error or "Already processed.",
                "at": 0,
            }
            yield f"data: {json.dumps(payload)}\n\n"
            return

        run = ensure_started(book_id)
        queue = run.subscribe()
        try:
            while True:
                item = await queue.get()
                if item is DONE:
                    break
                yield f"data: {item.model_dump_json()}\n\n"
        finally:
            run.unsubscribe(queue)

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


__all__ = ["router"]
