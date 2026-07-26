"""Tests for the library API's read endpoints.

These cover the contract the reader UI depends on: fetching a processed book's
structure, its cover image, and persisting reading position. Fully offline — the
store is redirected into a tmp_path and no agent is ever run, so nothing here
touches the network or an LLM.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest
from ebooklib import epub
from fastapi.testclient import TestClient

from diorama.backend import store
from diorama.backend.main import app
from diorama.backend.models import BookRecord

# A 1x1 PNG — the smallest thing that is unambiguously an image.
PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """A client whose store writes into tmp_path instead of the repo's data dir."""
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "UPLOADS_DIR", tmp_path / "uploads")
    monkeypatch.setattr(store, "STRUCTURES_DIR", tmp_path / "structures")
    monkeypatch.setattr(store, "COVERS_DIR", tmp_path / "covers")
    monkeypatch.setattr(store, "LIBRARY_FILE", tmp_path / "library.json")
    return TestClient(app)


def _shelve(record: BookRecord) -> None:
    """Put a record on the shelf; the store is async, these tests are not."""
    asyncio.run(store.upsert_book(record))


def _record(book_id: str = "abc123", **overrides) -> BookRecord:
    return BookRecord(
        id=book_id,
        title="A Book",
        source_filename="a-book.epub",
        created_at=datetime.now(timezone.utc).isoformat(),
        **overrides,
    )


def _write_epub(path: Path, *, with_cover: bool) -> None:
    book = epub.EpubBook()
    book.set_identifier("id")
    book.set_title("A Book")
    chapter = epub.EpubHtml(title="One", file_name="one.xhtml")
    chapter.content = "<html><body><p>Hello.</p></body></html>"
    book.add_item(chapter)
    book.spine = [chapter]
    book.toc = [chapter]
    # NCX only: ebooklib's EpubNav ships with empty content, which its own writer
    # then fails to parse.
    book.add_item(epub.EpubNcx())
    if with_cover:
        book.set_cover("cover.png", PNG_BYTES)
    epub.write_epub(str(path), book)


STRUCTURE_JSON = """
{
  "title": "A Book",
  "author": "An Author",
  "level_types": ["chapter"],
  "root": [
    {
      "level_type": "chapter",
      "number": "I",
      "title": "The First",
      "start_block_id": 0,
      "end_block_id": 1,
      "text": "Hello.",
      "children": []
    }
  ],
  "toc": [],
  "coverage": {"covered": true, "total_blocks": 2, "assigned_blocks": 2},
  "cost_usd": 0.01
}
"""


def test_structure_requires_a_finished_run(client: TestClient) -> None:
    """A book that hasn't been processed has no structure to serve — 409, not 404."""
    _shelve(_record())

    response = client.get("/api/books/abc123/structure")
    assert response.status_code == 409

    assert client.get("/api/books/missing/structure").status_code == 404


def test_structure_is_served_once_extracted(client: TestClient) -> None:
    _shelve(_record(status="ready"))
    store.structure_path("abc123").write_text(STRUCTURE_JSON)

    body = client.get("/api/books/abc123/structure").json()
    assert body["title"] == "A Book"
    assert body["root"][0]["title"] == "The First"
    assert body["coverage"]["covered"] is True


def test_cover_is_extracted_then_cached(client: TestClient) -> None:
    _shelve(_record())
    _write_epub(store.upload_path("abc123"), with_cover=True)

    response = client.get("/api/books/abc123/cover")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/")
    assert response.content

    # The second request is served from the cache, not by re-parsing the epub.
    cached = store.cached_cover("abc123")
    assert cached is not None and cached.suffix != ".none"
    store.upload_path("abc123").unlink()
    assert client.get("/api/books/abc123/cover").status_code == 200


def test_missing_cover_is_remembered_as_missing(client: TestClient) -> None:
    """A coverless book must not re-parse its epub on every shelf render."""
    _shelve(_record())
    _write_epub(store.upload_path("abc123"), with_cover=False)

    assert client.get("/api/books/abc123/cover").status_code == 404
    cached = store.cached_cover("abc123")
    assert cached is not None and cached.suffix == ".none"


def test_progress_round_trips(client: TestClient) -> None:
    _shelve(_record())

    response = client.patch(
        "/api/books/abc123/progress",
        json={"section_index": 3, "page": 2, "pages": 9, "percent": 0.42},
    )
    assert response.status_code == 200
    progress = response.json()["progress"]
    assert progress["section_index"] == 3
    assert progress["page"] == 2
    # The server stamps the time; the client never sends it.
    assert progress["updated_at"]

    stored = client.get("/api/books/abc123").json()
    assert stored["progress"]["percent"] == pytest.approx(0.42)


def test_delete_removes_the_record_and_its_files(client: TestClient) -> None:
    _shelve(_record())
    _write_epub(store.upload_path("abc123"), with_cover=True)
    store.structure_path("abc123").write_text(STRUCTURE_JSON)
    client.get("/api/books/abc123/cover")

    assert client.delete("/api/books/abc123").status_code == 204
    assert client.get("/api/books/abc123").status_code == 404
    assert not store.upload_path("abc123").exists()
    assert not store.structure_path("abc123").exists()
    assert store.cached_cover("abc123") is None

    assert client.delete("/api/books/abc123").status_code == 404
