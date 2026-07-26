"""JSON-backed library store.

A single-user shelf doesn't need a database: ``library.json`` holds the list of
:class:`~diorama.backend.models.BookRecord` entries, ``uploads/`` holds the source
epubs, and ``structures/`` holds each book's extracted
:class:`~diorama.ebook.models.EbookStructure`. This reuses the exact directory
layout a prior implementation left behind in ``.diorama_data`` (see CLAUDE.md).

All reads/writes go through a single :class:`asyncio.Lock` — this is one process,
so that's sufficient to keep concurrent requests from tearing the JSON file.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from diorama.backend.models import BookRecord

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / ".diorama_data"
UPLOADS_DIR = DATA_DIR / "uploads"
STRUCTURES_DIR = DATA_DIR / "structures"
COVERS_DIR = DATA_DIR / "covers"
LIBRARY_FILE = DATA_DIR / "library.json"

_lock = asyncio.Lock()


def _ensure_dirs() -> None:
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    STRUCTURES_DIR.mkdir(parents=True, exist_ok=True)
    COVERS_DIR.mkdir(parents=True, exist_ok=True)


def _read_all() -> list[BookRecord]:
    _ensure_dirs()
    if not LIBRARY_FILE.exists():
        return []
    raw = json.loads(LIBRARY_FILE.read_text() or "[]")
    return [BookRecord.model_validate(entry) for entry in raw]


def _write_all(records: list[BookRecord]) -> None:
    _ensure_dirs()
    payload = [r.model_dump(mode="json") for r in records]
    LIBRARY_FILE.write_text(json.dumps(payload, indent=2, ensure_ascii=False))


async def list_books() -> list[BookRecord]:
    async with _lock:
        records = _read_all()
    return sorted(records, key=lambda r: r.created_at, reverse=True)


async def get_book(book_id: str) -> BookRecord | None:
    async with _lock:
        records = _read_all()
    return next((r for r in records if r.id == book_id), None)


async def upsert_book(record: BookRecord) -> BookRecord:
    async with _lock:
        records = _read_all()
        records = [r for r in records if r.id != record.id]
        records.append(record)
        _write_all(records)
    return record


def upload_path(book_id: str) -> Path:
    _ensure_dirs()
    return UPLOADS_DIR / f"{book_id}.epub"


def structure_path(book_id: str) -> Path:
    _ensure_dirs()
    return STRUCTURES_DIR / f"{book_id}.json"


def cached_cover(book_id: str) -> Path | None:
    """The cached cover file for ``book_id``, if one has been extracted before.

    Extraction re-parses the whole EPUB, so results are cached on disk — including
    the *absence* of a cover, as a ``.none`` marker, since the shelf asks for every
    book's cover on every load and a coverless book would otherwise re-parse forever.
    """
    _ensure_dirs()
    return next(iter(sorted(COVERS_DIR.glob(f"{book_id}.*"))), None)


def cover_path(book_id: str, suffix: str) -> Path:
    _ensure_dirs()
    return COVERS_DIR / f"{book_id}{suffix}"


async def delete_book(book_id: str) -> bool:
    """Remove a book from the shelf along with its upload, structure and cover."""
    async with _lock:
        records = _read_all()
        remaining = [r for r in records if r.id != book_id]
        if len(remaining) == len(records):
            return False
        _write_all(remaining)

    for path in (upload_path(book_id), structure_path(book_id)):
        path.unlink(missing_ok=True)
    for path in COVERS_DIR.glob(f"{book_id}.*"):
        path.unlink(missing_ok=True)
    return True


__all__ = [
    "COVERS_DIR",
    "DATA_DIR",
    "STRUCTURES_DIR",
    "UPLOADS_DIR",
    "cached_cover",
    "cover_path",
    "delete_book",
    "get_book",
    "list_books",
    "structure_path",
    "upload_path",
    "upsert_book",
]
