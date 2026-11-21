import asyncio
import hashlib
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from conftest import as_book
from fastapi.testclient import TestClient
from PIL import Image

from diorama.agent.tools import inspect_epub
from diorama.agent.worker import SignInRequired, WorkerError
from diorama.backend.app import create_app
from diorama.backend.ingestion import BookIngestion

WRITE = {"X-Diorama-Request": "1"}
UPLOAD = {**WRITE, "Content-Type": "application/epub+zip"}


def wait_for_book(client, status):
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        books = client.get("/api/books").json()
        if books and books[0]["status"] == status:
            return books[0]
        time.sleep(0.015)
    pytest.fail(f"Expected {status}, got {books}")


@pytest.mark.geppetto_live
def test_live_upload_reaches_reader(epub_factory, tmp_path):
    source = epub_factory("novel")
    service = BookIngestion.local(tmp_path / "live-library")
    with TestClient(create_app(service), base_url="http://localhost") as client:
        response = client.post(
            "/api/books", content=source.read_bytes(), headers=UPLOAD
        )
        assert response.status_code == 202
        digest = response.json()["id"]
        deadline = time.monotonic() + service.harness.timeout_seconds + 30
        while time.monotonic() < deadline:
            row = client.get("/api/books").json()[0]
            if row["status"] == "ready":
                break
            assert row["status"] in {"queued", "preparing"}, row["message"]
            time.sleep(0.5)
        else:
            pytest.fail("The live upload did not reach the reader")
        base = "/api/books/" + digest
        pages = client.get(base + "/contents").json()["pages"]
        text = "".join(
            client.get(base + f"/pages/{p['number']}").json()["text"] for p in pages
        )
        assert text == "".join(
            doc.text for doc in inspect_epub(source).spine if doc.linear
        )


@pytest.mark.parametrize("edition", ["novel", "play", "anthology", "epic"])
def test_upload_prepare_read_and_reopen(epub_factory, harness_factory, edition):
    source = epub_factory(edition)
    data = source.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    harness, calls = harness_factory(book_id=digest)
    app = create_app(BookIngestion(harness))
    with TestClient(app, base_url="http://localhost") as client:
        assert client.get("/").status_code == 200
        assert client.get("/api/books").json() == []
        upload = client.post("/api/books", content=data, headers=UPLOAD)
        assert upload.status_code == 202
        root = "/api/books/" + upload.json()["id"]
        book = wait_for_book(client, "ready")
        toc = client.get(root + "/contents").json()
        assert toc["book"]["title"] == book["title"]
        pages = [
            client.get(root + f"/pages/{p['number']}").json()["text"]
            for p in toc["pages"]
        ]
        index = inspect_epub(source)
        assert "".join(pages) == "".join(d.text for d in index.spine if d.linear)
        if edition == "play":
            assert [p["trail"] for p in toc["pages"]] == [
                ["book", "act"],
                ["book", "act", "scene"],
                ["book", "act", "scene"],
            ]
        assert client.get(root + "/pages/999").status_code == 404
        assert (
            client.post("/api/books", content=data, headers=UPLOAD).json()["status"]
            == "ready"
        )
    with TestClient(
        create_app(BookIngestion(harness)), base_url="http://localhost"
    ) as client:
        assert client.get(root + "/contents").json() == toc
        assert len(calls) == 1


def test_rejects_invalid_upload_and_recovers_failed_job(
    epub_factory, harness_factory, monkeypatch
):
    epub = epub_factory()
    digest = hashlib.sha256(epub.read_bytes()).hexdigest()

    async def behavior(source, scratch, calls):
        if len(calls) == 1:
            raise WorkerError("temporary failure")
        (scratch / "candidate.json").write_text(
            as_book(inspect_epub(source), digest).model_dump_json()
        )

    harness, calls = harness_factory(behavior)
    with TestClient(
        create_app(BookIngestion(harness)), base_url="http://localhost"
    ) as client:
        assert client.post("/api/books", content=b"bad").status_code == 403
        assert (
            client.post("/api/books", content=b"bad", headers=WRITE).status_code == 415
        )
        assert (
            client.post("/api/books", content=b"bad", headers=UPLOAD).status_code == 422
        )
        with monkeypatch.context() as m:
            m.setattr("diorama.backend.app.MAX_ARCHIVE", 2)
            assert (
                client.post("/api/books", content=b"bad", headers=UPLOAD).status_code
                == 413
            )
        assert client.get("/api/books").json() == []
        client.post("/api/books", content=epub.read_bytes(), headers=UPLOAD)
        wait_for_book(client, "failed")
        root = "/api/books/" + digest
        assert client.get(root + "/contents").status_code == 409
        assert client.post(root + "/retry", headers=WRITE).status_code == 200
        wait_for_book(client, "ready")
        assert len(calls) == 2


def test_waiting_upload_resumes_after_device_signin(epub_factory, harness_factory):
    data = epub_factory().read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    sdk = AsyncMock()
    sdk.account.return_value = SimpleNamespace(account=None)
    attempt = AsyncMock(
        user_code="TEST-ONLY", verification_url="https://example.test/verify"
    )

    async def wait():
        while sdk.account.return_value.account is None:
            await asyncio.sleep(0.01)
        return SimpleNamespace(success=True)

    attempt.wait.side_effect = wait
    sdk.login_chatgpt_device_code.return_value = attempt

    async def behavior(source, scratch, calls):
        if sdk.account.return_value.account is None:
            raise SignInRequired()
        (scratch / "candidate.json").write_text(
            as_book(inspect_epub(source), digest).model_dump_json()
        )

    harness, calls = harness_factory(behavior)
    with TestClient(
        create_app(BookIngestion(harness), lambda: sdk), base_url="http://localhost"
    ) as client:
        client.post("/api/books", content=data, headers=UPLOAD)
        wait_for_book(client, "needs_account")
        assert client.get("/api/account").json()["status"] == "disconnected"
        client.post("/api/account/connect", headers=WRITE)
        assert (
            client.post("/api/account/cancel", headers=WRITE).json()["status"]
            == "disconnected"
        )
        sdk.logout.assert_not_awaited()
        attempt.cancel.assert_awaited_once()
        pending = client.post("/api/account/connect", headers=WRITE).json()
        assert pending["user_code"] == "TEST-ONLY"
        assert client.post("/api/account/connect", headers=WRITE).json() == pending
        sdk.account.return_value = SimpleNamespace(
            account=SimpleNamespace(root=SimpleNamespace(type="chatgpt"))
        )
        wait_for_book(client, "ready")
        assert client.get("/api/account").json() == {"status": "connected"}
        assert client.post("/api/account/disconnect", headers=WRITE).status_code == 200
        sdk.logout.assert_awaited_once()
        assert len(calls) == 2


def test_existing_python_book_on_shelf(epub_factory, harness_factory):
    harness, calls = harness_factory(book_id="existing-id")
    service = BookIngestion(harness)
    result = asyncio.run(service.add_book("existing-id", epub_factory()))
    with TestClient(create_app(service), base_url="http://localhost") as client:
        book = client.get("/api/books").json()[0]
        assert book["id"] == result.source_sha256 and book["status"] == "ready"
        assert len(calls) == 1


def test_interrupted_job_recovers_without_reupload(epub_factory, harness_factory):
    data = epub_factory().read_bytes()
    digest = hashlib.sha256(data).hexdigest()

    async def behavior(source, scratch, calls):
        if len(calls) == 1:
            await asyncio.Future()
        (scratch / "candidate.json").write_text(
            as_book(inspect_epub(source), digest).model_dump_json()
        )

    harness, calls = harness_factory(behavior)
    service = BookIngestion(harness)
    with TestClient(create_app(service), base_url="http://localhost") as client:
        client.post("/api/books", content=data, headers=UPLOAD)
        wait_for_book(client, "preparing")
        assert (
            client.post("/api/books", content=data, headers=UPLOAD).json()["id"]
            == digest
        )
        # Wait for the first worker to enter its turn before stopping the server.
        for _ in range(100):
            if calls:
                break
            time.sleep(0.01)
        assert len(calls) == 1
    with TestClient(create_app(service), base_url="http://localhost") as client:
        wait_for_book(client, "ready")
        assert len(calls) == 2


def test_embedded_cover_is_rendered_as_jpeg(epub_factory, harness_factory, tmp_path):
    source = epub_factory()
    import io

    image = io.BytesIO()
    Image.new("RGB", (3, 4)).save(image, "PNG")
    with (
        ZipFile(source) as original,
        ZipFile(tmp_path / "cover.epub", "w", ZIP_DEFLATED) as edited,
    ):
        for info in original.infolist():
            data = original.read(info)
            if info.filename == "OPS/book.opf":
                data = data.replace(
                    b"</manifest>",
                    b'<item id="art" href="cover.png" media-type="image/png" properties="cover-image"/></manifest>',
                )
            edited.writestr(info, data)
        edited.writestr("OPS/cover.png", image.getvalue())
    data = (tmp_path / "cover.epub").read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    harness, _ = harness_factory(book_id=digest)
    with TestClient(
        create_app(BookIngestion(harness)), base_url="http://localhost"
    ) as client:
        assert client.post("/api/books", content=data, headers=UPLOAD).json()[
            "has_cover"
        ]
        cover = client.get("/api/books/" + digest + "/cover")
        assert cover.headers["content-type"] == "image/jpeg"
        assert Image.open(io.BytesIO(cover.content)).size == (3, 4)
