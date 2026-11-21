"""Persist the local bookshelf and prepare uploaded editions with Geppetto."""

from __future__ import annotations

import asyncio
import fcntl
import io
import json
import logging
import os
import sqlite3
from collections.abc import Iterator
from contextlib import asynccontextmanager, closing, contextmanager, suppress
from pathlib import Path
from threading import Lock
from zipfile import BadZipFile, ZipFile

from PIL import Image, ImageOps

from diorama.agent.harness import IngestionResult
from diorama.agent.preparation import (
    PreparationUpdate,
    opening_update,
    section_preview,
    source_previews,
)
from diorama.agent.tools import (
    MAX_DOCUMENT,
    InvalidCandidate,
    SourceIndex,
    inspect_epub,
    read_passage,
)
from diorama.agent.trace import TraceEvent
from diorama.agent.worker import SignInRequired, WorkerError
from diorama.backend import events
from diorama.backend.events import record_update
from diorama.backend.ingestion import BookIngestion
from diorama.schemas.book import Book, Passage, Section

logger = logging.getLogger(__name__)


class BookPreparing(Exception):
    pass


def sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def reading_pages(
    section: Section, index: SourceIndex, trail: tuple[str, ...] = ()
) -> Iterator[tuple[tuple[str, ...], str]]:
    """Yield consecutive passages together, preserving text on either side of a subsection."""
    path = (*trail, section.title or section.label)
    passages = []
    for child in section.children:
        if isinstance(child, Passage):
            passages.append(read_passage(index, child.source))
        else:
            if passages:
                yield path, "".join(passages)
                passages.clear()
            yield from reading_pages(child, index, path)
    if passages:
        yield path, "".join(passages)


def edition_cover(source: Path, index: SourceIndex) -> bytes | None:
    """Decode an optional EPUB cover into a small, browser-safe raster image."""
    if not index.cover_resource:
        return None
    try:
        with ZipFile(source) as archive:
            info = archive.getinfo(index.cover_resource)
            if info.file_size > MAX_DOCUMENT:
                return None
            with Image.open(
                io.BytesIO(archive.read(info)), formats=("JPEG", "PNG", "WEBP", "GIF")
            ) as cover:
                if cover.width * cover.height > 24_000_000:
                    return None
                cover.thumbnail((1000, 1000))
                result = io.BytesIO()
                ImageOps.exif_transpose(cover).convert("RGB").save(
                    result, "JPEG", quality=84
                )
                return result.getvalue()
    except (BadZipFile, KeyError, OSError, ValueError, Image.DecompressionBombError):
        return None


class Library:
    def __init__(self, ingestion: BookIngestion):
        self.ingestion = ingestion
        self.root = ingestion.harness.publication_root
        self.changed = asyncio.Event()
        self.account_generation = 0
        self.edition_lock = Lock()

    @contextmanager
    def database(self) -> Iterator[sqlite3.Connection]:
        with (
            closing(sqlite3.connect(self.root / "library.sqlite3", timeout=20)) as db,
            db,
        ):
            db.row_factory = sqlite3.Row
            yield db

    def initialize(self) -> None:
        (self.root / "editions").mkdir(exist_ok=True)
        with self.database() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS books (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, authors TEXT NOT NULL,
                    status TEXT NOT NULL, message TEXT NOT NULL DEFAULT '',
                    cover BLOB, manifest TEXT, created INTEGER NOT NULL DEFAULT (unixepoch())
                );
                CREATE TABLE IF NOT EXISTS pages (
                    book TEXT NOT NULL, number INTEGER NOT NULL, trail TEXT NOT NULL,
                    body TEXT NOT NULL, PRIMARY KEY(book, number)
                );
            """)
            db.execute("BEGIN IMMEDIATE")
            events.migrate(db)
            self.requeue(
                db,
                "preparing",
                "Preparation was interrupted. Returned the EPUB to the queue.",
            )
            pending = db.execute(
                "SELECT id FROM books WHERE status='removing'"
            ).fetchall()
        for row in pending:
            try:
                self.finish_removal(row["id"])
            except OSError:
                logger.exception("Removal of edition %s needs a retry", row["id"])
        with self.database() as db:
            known = {
                row["manifest"]: row
                for row in db.execute(
                    """SELECT id,manifest,status,
                       EXISTS(SELECT 1 FROM ingestion_events WHERE book=books.id AND origin='library') AS has_notes
                       FROM books WHERE manifest IS NOT NULL"""
                )
            }
        # Editions previously published by the Python API also belong on the shelf.
        for manifest in sorted((self.root / "manifests").glob("*.json")):
            if manifest.name in known:
                row = known[manifest.name]
                if row["status"] == "ready" and not row["has_notes"]:
                    try:
                        existing = Book.model_validate_json(manifest.read_bytes())
                    except (OSError, ValueError):
                        logger.warning(
                            "Could not load existing contents preview for %s", row["id"]
                        )
                        continue
                    self.report(
                        row["id"],
                        PreparationUpdate(
                            "ready",
                            "This edition was already available in your Reader.",
                            section_preview(existing.root),
                        ),
                    )
                continue
            book = Book.model_validate_json(manifest.read_bytes())
            source = self.edition(book.source_sha256)
            with self.database() as db:
                ready = db.execute(
                    "SELECT 1 FROM books WHERE id=? AND status IN ('ready','removing')",
                    (book.source_sha256,),
                ).fetchone()
            if not ready and source.is_file():
                index = inspect_epub(source)
                self.register(source, index)
                self.publish(
                    IngestionResult(
                        manifest, source, book.source_sha256, tuple(book.uncertainties)
                    ),
                    index,
                )

    @asynccontextmanager
    async def running(self):
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / "library.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError(
                    "Another Diorama server is using this library"
                ) from exc
            await asyncio.to_thread(self.initialize)
            runner = asyncio.create_task(self.process_queue())
            try:
                yield
            finally:
                runner.cancel()
                with suppress(asyncio.CancelledError):
                    await runner

    def edition(self, digest: str) -> Path:
        return self.root / "editions" / f"{digest}.epub"

    def register(self, source: Path, index: SourceIndex) -> str:
        digest = index.source_sha256
        with self.database() as db:
            existing = db.execute(
                "SELECT status FROM books WHERE id=?", (digest,)
            ).fetchone()
        if existing:
            if existing["status"] != "removing":
                return digest
            self.finish_removal(digest)
        target = self.edition(digest)
        if source != target:
            with suppress(FileExistsError):
                os.link(source, target)
            sync_directory(target.parent)
        cover = edition_cover(source, index)
        with self.database() as db:
            inserted = db.execute(
                """INSERT OR IGNORE INTO books (id,title,authors,status,cover)
                        VALUES (?,?,?,'queued',?)""",
                (digest, index.title or "Untitled", json.dumps(index.authors), cover),
            )
            if inserted.rowcount:
                record_update(db, digest, opening_update(index))
        return digest

    def ingest_upload(self, source: Path) -> str:
        index = inspect_epub(source)
        with self.edition_lock:
            return self.register(source, index)

    def remove(self, digest: str) -> None:
        with self.edition_lock:
            with self.database() as db:
                row = db.execute(
                    """UPDATE books SET status='removing'
                       WHERE id=? AND status != 'preparing' RETURNING id""",
                    (digest,),
                ).fetchone()
                if row is None:
                    if db.execute(
                        "SELECT 1 FROM books WHERE id=?", (digest,)
                    ).fetchone():
                        raise BookPreparing
                    return
            self.finish_removal(digest)

    def finish_removal(self, digest: str) -> None:
        with self.database() as db:
            pending = db.execute(
                "SELECT manifest FROM books WHERE id=? AND status='removing'", (digest,)
            ).fetchone()
        if pending is None:
            return
        # Several manifest versions or caller-supplied IDs can refer to one edition.
        directory = self.root / "manifests"
        for path in directory.glob("*.json"):
            if path.name != pending["manifest"]:
                try:
                    manifest = json.loads(path.read_bytes())
                except ValueError:
                    continue
                if (
                    not isinstance(manifest, dict)
                    or manifest.get("source_sha256") != digest
                ):
                    continue
            path.unlink(missing_ok=True)
        if directory.exists():
            sync_directory(directory)
        edition = self.edition(digest)
        edition.unlink(missing_ok=True)
        sync_directory(edition.parent)
        with self.database() as db:
            db.execute("DELETE FROM ingestion_events WHERE book=?", (digest,))
            db.execute("DELETE FROM pages WHERE book=?", (digest,))
            db.execute("DELETE FROM books WHERE id=? AND status='removing'", (digest,))

    def publish(
        self,
        result: IngestionResult,
        index: SourceIndex | None = None,
        run: int | None = None,
    ) -> None:
        book = result.open_book()
        if index is None:
            index = inspect_epub(result.source_epub)
        with self.database() as db:
            db.execute("DELETE FROM pages WHERE book=?", (result.source_sha256,))
            pages = db.executemany(
                "INSERT INTO pages VALUES (?,?,?,?)",
                (
                    (
                        result.source_sha256,
                        number,
                        json.dumps(trail, ensure_ascii=False),
                        body,
                    )
                    for number, (trail, body) in enumerate(
                        reading_pages(book.root, index)
                    )
                ),
            )
            db.execute(
                "UPDATE books SET status='ready',message='',manifest=? WHERE id=?",
                (result.manifest_path.name, result.source_sha256),
            )
            outline = section_preview(book.root)
            record_update(
                db,
                result.source_sha256,
                PreparationUpdate(
                    "ready",
                    f"Prepared {pages.rowcount} reading section{'s' if pages.rowcount != 1 else ''} with the original text in order.",
                    outline,
                    source_previews(index, outline),
                ),
                run,
            )

    def report(
        self, digest: str, update: PreparationUpdate, run: int | None = None
    ) -> None:
        with self.database() as db:
            record_update(db, digest, update, run)

    def preparation(self, digest: str) -> dict:
        with self.database() as db:
            db.execute("BEGIN")
            snapshot = events.activity(db, digest) or {}
            notes = db.execute(
                """SELECT json_extract(payload,'$.note') FROM ingestion_events
                WHERE book=? AND origin='library' ORDER BY seq DESC LIMIT 32""",
                (digest,),
            ).fetchall()
        return {
            "phase": snapshot.get("phase"),
            "outline": snapshot.get("outline", []),
            "notes": [row[0] for row in reversed(notes)],
        }

    def capture(self, digest: str, run: int, event: TraceEvent, gap: bool) -> None:
        with self.database() as db:
            if gap:
                events.write_event(
                    db,
                    digest,
                    "sdk",
                    "capture_gap",
                    {},
                    run=run,
                    observed_at_ms=event.observed_at_ms,
                )
            events.record_sdk(db, digest, run, event)

    def requeue(self, db: sqlite3.Connection, status: str, note: str) -> None:
        rows = db.execute(
            "UPDATE books SET status='queued',message='' WHERE status=? RETURNING id",
            (status,),
        ).fetchall()
        for row in rows:
            record_update(db, row["id"], PreparationUpdate("queued", note))

    async def process_queue(self) -> None:
        while True:
            self.changed.clear()
            with self.database() as db:
                job = db.execute(
                    """UPDATE books SET status='preparing',message='' WHERE id=(
                           SELECT id FROM books WHERE status='queued' ORDER BY rowid LIMIT 1
                       ) RETURNING id"""
                ).fetchone()
                if job:
                    run = record_update(
                        db,
                        job["id"],
                        PreparationUpdate(
                            "opening", "Opened the saved edition for preparation."
                        ),
                    )
            if not job:
                await self.changed.wait()
                continue
            digest = job["id"]
            generation = self.account_generation
            gap = False

            async def capture(event: TraceEvent) -> None:
                nonlocal gap
                try:
                    await asyncio.to_thread(self.capture, digest, run, event, gap)
                    gap = False
                except Exception:
                    gap = True
                    logger.warning("Trace storage unavailable run=%s", run)

            try:
                try:
                    result = await self.ingestion.add_book(
                        digest,
                        self.edition(digest),
                        on_progress=lambda update: self.report(digest, update, run),
                        on_trace=capture,
                    )
                finally:
                    if gap:
                        try:
                            with self.database() as db:
                                events.write_event(
                                    db,
                                    digest,
                                    "sdk",
                                    "capture_gap",
                                    {},
                                    run=run,
                                    observed_at_ms=None,
                                )
                        except sqlite3.Error:
                            logger.warning("Could not persist trace gap run=%s", run)
                self.report(
                    digest,
                    PreparationUpdate(
                        "publishing",
                        "The reading order and original text passed verification.",
                    ),
                    run,
                )
                await asyncio.to_thread(self.publish, result, run=run)
                continue
            except SignInRequired:
                status, message = (
                    "needs_account",
                    "Connect ChatGPT in Settings to prepare this book.",
                )
                if generation != self.account_generation:
                    status, message = "queued", ""
            except InvalidCandidate:
                status, message = (
                    "failed",
                    "Geppetto could not verify the structure against the original text.",
                )
            except TimeoutError:
                status, message = (
                    "failed",
                    "Geppetto ran out of time preparing this edition.",
                )
            except WorkerError:
                status, message = (
                    "failed",
                    "Geppetto could not finish. Check the Docker worker and account, then retry.",
                )
            except Exception:
                logger.exception("Failed to prepare edition %s", digest)
                status, message = "failed", "This edition could not be prepared."
            with self.database() as db:
                db.execute(
                    "UPDATE books SET status=?,message=? WHERE id=?",
                    (status, message, digest),
                )
                record_update(
                    db,
                    digest,
                    PreparationUpdate(
                        status,
                        message or "Returned the EPUB to the queue after sign-in.",
                    ),
                    run,
                )

    def resume_waiting(self) -> None:
        self.account_generation += 1
        with self.database() as db:
            self.requeue(
                db,
                "needs_account",
                "ChatGPT connected. Returned the EPUB to the queue.",
            )
        self.changed.set()

    def retry(self, digest: str) -> None:
        with self.database() as db:
            retried = db.execute(
                "UPDATE books SET status='queued',message='' WHERE id=? AND status IN ('needs_account','failed')",
                (digest,),
            )
            if retried.rowcount:
                record_update(
                    db,
                    digest,
                    PreparationUpdate(
                        "queued", "Queued another attempt using the saved EPUB."
                    ),
                )
        self.changed.set()

    def books(self, digest: str | None = None) -> list[dict]:
        sql = """SELECT id,title,authors,status,message,cover IS NOT NULL AS has_cover,
                 (SELECT MAX(seq) FROM ingestion_events WHERE book=books.id AND origin='library') AS preparation_revision,
                 (SELECT MIN(seq) FROM ingestion_events WHERE book=books.id) AS registration_seq,
                 (SELECT substr(body,1,170) FROM pages WHERE book=books.id AND number=0) AS excerpt
                 FROM books"""
        with self.database() as db:
            rows = db.execute(
                sql + (" WHERE id=?" if digest is not None else " ORDER BY rowid DESC"),
                (digest,) if digest is not None else (),
            )
            return [
                dict(row)
                | {
                    "authors": json.loads(row["authors"]),
                    "has_cover": bool(row["has_cover"]),
                }
                for row in rows
            ]
