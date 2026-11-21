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
from zipfile import BadZipFile, ZipFile

from PIL import Image, ImageOps

from diorama.agent.harness import IngestionResult
from diorama.agent.tools import (
    MAX_DOCUMENT,
    InvalidCandidate,
    SourceIndex,
    inspect_epub,
    read_passage,
)
from diorama.agent.worker import SignInRequired, WorkerError
from diorama.backend.ingestion import BookIngestion
from diorama.schemas.book import Book, Passage, Section

logger = logging.getLogger(__name__)


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
                UPDATE books SET status='queued' WHERE status='preparing';
            """)
            known = {
                row[0]
                for row in db.execute(
                    "SELECT manifest FROM books WHERE manifest IS NOT NULL"
                )
            }
        # Editions previously published by the Python API also belong on the shelf.
        for manifest in sorted((self.root / "manifests").glob("*.json")):
            if manifest.name in known:
                continue
            book = Book.model_validate_json(manifest.read_bytes())
            source = self.edition(book.source_sha256)
            with self.database() as db:
                ready = db.execute(
                    "SELECT 1 FROM books WHERE id=? AND status='ready'",
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
            if db.execute("SELECT 1 FROM books WHERE id=?", (digest,)).fetchone():
                return digest
        target = self.edition(digest)
        if source != target:
            with suppress(FileExistsError):
                os.link(source, target)
            directory = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        cover = edition_cover(source, index)
        with self.database() as db:
            db.execute(
                """INSERT OR IGNORE INTO books (id,title,authors,status,cover)
                        VALUES (?,?,?,'queued',?)""",
                (digest, index.title or "Untitled", json.dumps(index.authors), cover),
            )
        return digest

    def ingest_upload(self, source: Path) -> str:
        return self.register(source, inspect_epub(source))

    def publish(
        self, result: IngestionResult, index: SourceIndex | None = None
    ) -> None:
        book = result.open_book()
        if index is None:
            index = inspect_epub(result.source_epub)
        with self.database() as db:
            db.execute("DELETE FROM pages WHERE book=?", (result.source_sha256,))
            db.executemany(
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

    async def process_queue(self) -> None:
        while True:
            self.changed.clear()
            with self.database() as db:
                job = db.execute(
                    "SELECT id FROM books WHERE status='queued' ORDER BY rowid LIMIT 1"
                ).fetchone()
                if job:
                    db.execute(
                        "UPDATE books SET status='preparing',message='' WHERE id=?",
                        (job["id"],),
                    )
            if not job:
                await self.changed.wait()
                continue
            digest = job["id"]
            generation = self.account_generation
            try:
                result = await self.ingestion.add_book(digest, self.edition(digest))
                await asyncio.to_thread(self.publish, result)
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

    def resume_waiting(self) -> None:
        self.account_generation += 1
        with self.database() as db:
            db.execute(
                "UPDATE books SET status='queued',message='' WHERE status='needs_account'"
            )
        self.changed.set()

    def retry(self, digest: str) -> None:
        with self.database() as db:
            db.execute(
                "UPDATE books SET status='queued',message='' WHERE id=? AND status IN ('needs_account','failed')",
                (digest,),
            )
        self.changed.set()

    def books(self, digest: str | None = None) -> list[dict]:
        sql = """SELECT id,title,authors,status,message,cover IS NOT NULL AS has_cover,
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
