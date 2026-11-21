"""Coordinate worker attempts and publish validated manifests on the host."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import stat
import tempfile
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from diorama.agent.geppetto import Geppetto
from diorama.agent.tools import (
    MAX_ARCHIVE,
    InvalidCandidate,
    InvalidEpub,
    SourceIndex,
    inspect_epub,
    validate_hierarchy,
)
from diorama.agent.worker import Worker, WorkerLost, ingestion_task
from diorama.schemas.book import Book

logger = logging.getLogger(__name__)
# Bump when schema, reading-stream rules, or worker behavior invalidate prior manifests.
INGESTION_VERSION = "1"
MAX_MANIFEST = 16 * 1024 * 1024
type WorkerFactory = Callable[
    [Geppetto, Path, Path], AbstractAsyncContextManager[Worker]
]


@dataclass(frozen=True)
class IngestionResult:
    """Paths to a published manifest and its archived EPUB, with digest and uncertainties."""

    manifest_path: Path
    source_epub: Path
    source_sha256: str
    uncertainties: tuple[str, ...]

    def open_book(self) -> Book:
        """Read the saved JSON manifest and parse it as a `Book`."""
        return Book.model_validate_json(self.manifest_path.read_bytes())


def _claim(staged: Path, target: Path) -> None:
    """Publish a synced file on the same filesystem. The first complete file wins."""
    try:
        os.link(staged, target)
    except FileExistsError:
        pass
    directory = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _retain_edition(source: Path, directory: Path) -> tuple[str, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    size = 0
    with tempfile.NamedTemporaryFile(dir=directory) as snapshot:
        try:
            with source.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_ARCHIVE:
                        raise InvalidEpub("Oversized EPUB")
                    snapshot.write(chunk)
                    digest.update(chunk)
        except OSError as exc:
            raise InvalidEpub("Cannot read source EPUB") from exc
        snapshot.flush()
        os.fsync(snapshot.fileno())
        target = directory / f"{digest.hexdigest()}.epub"
        _claim(Path(snapshot.name), target)
    return digest.hexdigest(), target


def _candidate(scratch: Path, index: SourceIndex, book_id: str) -> bytes:
    try:
        fd = os.open(
            scratch / "candidate.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        )
    except OSError as exc:
        raise InvalidCandidate(
            "Write candidate.json as a regular file in the workspace"
        ) from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_MANIFEST:
            raise InvalidCandidate("candidate.json must be a size-bounded regular file")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            data = stream.read(MAX_MANIFEST + 1)
    finally:
        os.close(fd)
    if len(data) > MAX_MANIFEST:
        raise InvalidCandidate("candidate.json exceeds the size limit")
    try:
        book = Book.model_validate_json(data)
    except ValidationError as exc:
        errors = exc.errors(
            include_input=False, include_context=False, include_url=False
        )
        raise InvalidCandidate(f"Invalid Book: {errors[:5]}") from exc
    if book.id != book_id:
        raise InvalidCandidate("Use the assigned book ID")
    validate_hierarchy(index, book)
    return data


@dataclass
class GeppettoHarness:
    """Run ingestion with an injected agent, worker factory, and publication root.

    The factory receives the agent, archived edition path, and scratch directory.
    Its async context manager supplies one worker for the duration of an attempt.
    `timeout_seconds` sets the overall deadline, including any retries.
    """

    agent: Geppetto
    worker_factory: WorkerFactory
    publication_root: Path
    timeout_seconds: float = 600

    async def ingest_epub(self, *, book_id: str, source_epub: Path) -> IngestionResult:
        """Retain the edition and return its validated manifest for this book ID.

        A matching published manifest is reused. An invalid candidate gets one
        repair turn. A lost worker can trigger one fresh attempt. Concurrent
        callers for the same key receive the first successfully published result.

        Raises `ValueError` for an invalid book ID, `InvalidEpub` for an
        unreadable or unsupported edition, and `InvalidCandidate` if the repair
        fails validation. Worker failures propagate as `WorkerError`. Expiry
        of the overall deadline raises `TimeoutError`.
        """
        if not book_id.strip() or len(book_id) > 512:
            raise ValueError("Supply a nonempty book ID up to 512 characters")
        async with asyncio.timeout(self.timeout_seconds):
            digest, edition = await asyncio.to_thread(
                _retain_edition, source_epub, self.publication_root / "editions"
            )
            version = [
                INGESTION_VERSION,
                self.agent.instructions,
                self.agent.skill_path.read_text(),
                book_id,
                digest,
            ]
            key = hashlib.sha256(json.dumps(version).encode()).hexdigest()
            directory = self.publication_root / "manifests"
            directory.mkdir(parents=True, exist_ok=True)
            manifest = directory / f"{key}.json"
            if not manifest.exists():
                index = await asyncio.to_thread(inspect_epub, edition)
                for attempt in range(2):
                    try:
                        data = await self._attempt(book_id, edition, index)
                        break
                    except WorkerLost:
                        if attempt:
                            raise
                        logger.warning("Retrying lost worker for edition %s", digest)
                with tempfile.NamedTemporaryFile(dir=directory) as staged:
                    staged.write(data)
                    staged.flush()
                    os.fsync(staged.fileno())
                    _claim(Path(staged.name), manifest)
            winner = Book.model_validate_json(manifest.read_bytes())
            return IngestionResult(
                manifest, edition, digest, tuple(winner.uncertainties)
            )

    async def _attempt(self, book_id: str, edition: Path, index: SourceIndex) -> bytes:
        with tempfile.TemporaryDirectory(prefix="geppetto-") as workspace:
            scratch = Path(workspace)
            logger.info("Starting ingestion attempt=%s", scratch.name)
            async with self.worker_factory(self.agent, edition, scratch) as worker:
                await worker.run(ingestion_task(self.agent, book_id))
                try:
                    return await asyncio.to_thread(_candidate, scratch, index, book_id)
                except InvalidCandidate as exc:
                    await worker.run(
                        f"Repair /work/candidate.json and run the checker again. Diagnostic: {exc}"
                    )
                return await asyncio.to_thread(_candidate, scratch, index, book_id)
