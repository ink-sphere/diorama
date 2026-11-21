import asyncio
import hashlib
import json
import sqlite3
import time
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from conftest import Edition, as_book, make_epub
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


def test_remove_edition_and_readd_after_restart(epub_factory, harness_factory):
    source = epub_factory("play")
    data = source.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    harness, _ = harness_factory(book_id=digest)
    service = BookIngestion(harness)
    root = "/api/books/" + digest
    other = epub_factory("novel")
    other_digest = hashlib.sha256(other.read_bytes()).hexdigest()
    other_harness, _ = harness_factory(book_id=other_digest)
    other_result = asyncio.run(
        BookIngestion(other_harness).add_book(other_digest, other)
    )
    with TestClient(create_app(service), base_url="http://localhost") as client:
        client.post("/api/books", headers=UPLOAD, content=data)
        wait_for_book(client, "ready")
        # A second manifest for the same edition must not resurrect the shelf entry.
        manifest = next(
            path
            for path in (harness.publication_root / "manifests").glob("*.json")
            if path != other_result.manifest_path
        )
        (manifest.parent / "older-version.json").write_bytes(manifest.read_bytes())
        assert client.delete(root).status_code == 403
        assert client.get(root + "/pages/0").status_code == 200
        assert client.delete(root, headers=WRITE).status_code == 204
        assert client.delete(root, headers=WRITE).status_code == 204
        for suffix in ["/contents", "/pages/0", "/preparation", "/cover"]:
            assert client.get(root + suffix).status_code == 404
        assert [row["id"] for row in client.get("/api/books").json()] == [other_digest]
        with sqlite3.connect(harness.publication_root / "library.sqlite3") as db:
            for table in ("ingestion_events", "pages"):
                assert (
                    db.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE book=?", (digest,)
                    ).fetchone()[0]
                    == 0
                )
        assert list(manifest.parent.glob("*.json")) == [other_result.manifest_path]
        assert not (harness.publication_root / "editions" / f"{digest}.epub").exists()
        assert source.read_bytes() == data
    with TestClient(create_app(service), base_url="http://localhost") as client:
        assert [row["id"] for row in client.get("/api/books").json()] == [other_digest]
        assert client.get(f"/api/books/{other_digest}/pages/0").status_code == 200
        assert (
            client.post("/api/books", headers=UPLOAD, content=data).status_code == 202
        )
        assert wait_for_book(client, "ready")["id"] == digest
        assert client.get(root + "/pages/0").status_code == 200


def test_interrupted_removal_resumes_at_startup(
    epub_factory, harness_factory, monkeypatch
):
    source = epub_factory()
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    harness, _ = harness_factory(book_id=digest)
    service = BookIngestion(harness)
    with TestClient(create_app(service), base_url="http://localhost") as client:
        client.post("/api/books", headers=UPLOAD, content=source.read_bytes())
        wait_for_book(client, "ready")
        edition = harness.publication_root / "editions" / f"{digest}.epub"
        unlink = Path.unlink

        def interrupt(path, *args, **kwargs):
            if path == edition:
                raise OSError("interrupted")
            return unlink(path, *args, **kwargs)

        with monkeypatch.context() as m:
            m.setattr(Path, "unlink", interrupt)
            assert (
                client.delete("/api/books/" + digest, headers=WRITE).status_code == 503
            )
            assert edition.exists()
            assert not list((harness.publication_root / "manifests").glob("*.json"))
    with TestClient(create_app(service), base_url="http://localhost") as client:
        assert client.get("/api/books").json() == []
        assert not list((harness.publication_root / "manifests").glob("*.json"))


@pytest.mark.geppetto_live
def test_live_upload_reaches_reader(tmp_path, monkeypatch):
    from diorama.agent import worker

    source = tmp_path / "two-chapters.epub"
    fixture = Edition(
        "The Lantern",
        (
            "<h1>Chapter I</h1><p>Ada lit the lamp.</p>",
            "<h1>Chapter II</h1><p>The harbour came into view.</p>",
        ),
        (),
        ("chapter", "chapter"),
        "Chapter I",
    )
    make_epub(source, fixture)
    observed = []
    project = worker.project

    def sample(notification):
        event = project(notification)
        if event and event.fields.get("type") == "commandExecution":
            item = notification.payload.item.root
            observed.append((event.name, item.id, item.command, item.aggregated_output))
        return event

    monkeypatch.setattr(worker, "project", sample)
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
        snapshot = client.get(base + "/activity").json()
        assert snapshot["status"] == "ready" and snapshot["sdk_count"] > 0
        records = client.get(base + "/trace?limit=100").json()["events"]
        terminal = next(row for row in records if row["name"] == "turn/completed")
        assert terminal["status"] == "completed"
        command = next(row for row in records if row["item_type"] == "commandExecution")
        detail = client.get(base + f"/trace/{command['seq']}").json()
        assert detail["turn_id"] and detail["thread_id"] and detail["attempt"] == 1
        assert "command" in detail["fields"] or "command" in detail["markers"]
        for name, item_id, cmd, output in observed:
            if detail["item_id"] == item_id and detail["name"] == name:
                for key, raw in [("command", cmd), ("aggregatedOutput", output)]:
                    if raw is not None and key not in detail["markers"]:
                        assert detail["fields"][key] == raw
                break
        else:
            pytest.fail("Stored command did not match any observed notification")
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
        assert client.get(root + "/preparation").json()["phase"] == "failed"
        assert client.get(root + "/contents").status_code == 409
        assert client.post(root + "/retry", headers=WRITE).status_code == 200
        wait_for_book(client, "ready")
        assert (
            "Queued another attempt using the saved EPUB."
            in client.get(root + "/preparation").json()["notes"]
        )
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
        progress_url = "/api/books/" + digest + "/preparation"
        paused = client.get(progress_url).json()
        assert paused["phase"] == "needs_account" and paused["outline"] == [
            [0, "Chapter I"]
        ]
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
        assert (
            client.get(progress_url).json()["notes"][: len(paused["notes"])]
            == paused["notes"]
        )
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


def test_growing_contents_and_notes_survive_reopen(epub_factory, harness_factory):
    data = epub_factory("play").read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    advance, finish = Event(), Event()
    opening = [[0, "Act I"], [1, "Scene I"]]
    full = [*opening, [1, "Scene II"]]

    async def map_play(source, scratch, calls):
        snapshot = scratch / "preview.json"
        snapshot.write_text(json.dumps(opening))
        while not advance.is_set():
            await asyncio.sleep(0.01)
        snapshot.write_text(json.dumps(full))
        while not finish.is_set():
            await asyncio.sleep(0.01)
        book = as_book(inspect_epub(source), digest)
        act = book.root.children[0]
        act.title = "Act I"
        act.children[1].title, act.children[2].title = "Scene I", "Scene II"
        (scratch / "candidate.json").write_text(book.model_dump_json())

    def snapshot(client, outline):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            progress = client.get(root + "/preparation").json()
            if progress["outline"] == outline:
                return progress
            time.sleep(0.025)
        pytest.fail(f"Missing outline {outline}: {progress}")

    harness, calls = harness_factory(map_play)
    service = BookIngestion(harness)
    root = "/api/books/" + digest
    with TestClient(create_app(service), base_url="http://localhost") as client:
        assert (
            client.post("/api/books", content=data, headers=UPLOAD).status_code == 202
        )
        early = snapshot(client, opening)
        assert early["phase"] == "discovered"
        advance.set()
        later = snapshot(client, full)
        assert later["notes"][:-1] == early["notes"]
        assert "Scene II" in later["notes"][-1]
        assert client.get(root + "/contents").status_code == 409
        finish.set()
        wait_for_book(client, "ready")
        prepared = client.get(root + "/preparation").json()
        assert prepared["phase"] == "ready" and prepared["outline"] == full
        assert any("passed verification" in note for note in prepared["notes"])
        assert client.get(root + "/pages/0").status_code == 200
    with TestClient(create_app(service), base_url="http://localhost") as client:
        assert client.get(root + "/preparation").json() == prepared
        assert client.get("/api/books/unknown/preparation").status_code == 404
        assert len(calls) == 1


def test_legacy_shelf_gets_verified_contents_without_reingestion(
    epub_factory, harness_factory
):
    harness, calls = harness_factory(book_id="existing")
    service = BookIngestion(harness)
    asyncio.run(service.add_book("existing", epub_factory()))
    with TestClient(create_app(service), base_url="http://localhost") as client:
        book = client.get("/api/books").json()[0]
    with sqlite3.connect(harness.publication_root / "library.sqlite3") as db:
        db.execute("DROP TABLE ingestion_events")
    with TestClient(create_app(service), base_url="http://localhost") as client:
        root = "/api/books/" + book["id"]
        assert client.get(root + "/preparation").json() == {
            "phase": "ready",
            "outline": [[0, "chapter"]],
            "notes": ["This edition was already available in your Reader."],
        }
        assert client.get(root + "/pages/0").status_code == 200
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
        assert client.delete("/api/books/" + digest, headers=WRITE).status_code == 409
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


def test_legacy_history_migrates_once_with_original_sequences(
    epub_factory, harness_factory
):
    source = epub_factory()
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    harness, calls = harness_factory(book_id=digest)
    service = BookIngestion(harness)
    root = "/api/books/" + digest
    with TestClient(create_app(service), base_url="http://localhost") as client:
        client.post("/api/books", headers=UPLOAD, content=source.read_bytes())
        wait_for_book(client, "ready")
        original = client.get(root + "/pages/0").json()
    with sqlite3.connect(harness.publication_root / "library.sqlite3") as db:
        db.execute("DROP TABLE ingestion_events")
        db.execute(
            "CREATE TABLE preparation(seq INTEGER PRIMARY KEY,book TEXT,phase TEXT,note TEXT,outline TEXT)"
        )
        db.executemany(
            "INSERT INTO preparation VALUES (?,?,?,?,?)",
            [
                (71, digest, "queued", "Saved", None),
                (104, digest, "ready", "Published", '[[0,"Chapter I"]]'),
            ],
        )
    for _ in range(2):
        with TestClient(create_app(service), base_url="http://localhost") as client:
            activity = client.get(root + "/activity").json()
            assert (
                activity["registration_seq"] == 71 and activity["trace_revision"] == 104
            )
            assert activity["registered_at_ms"] is None and activity["sdk_count"] == 0
            assert client.get(root + "/preparation").json() == dict(
                phase="ready", notes=["Saved", "Published"], outline=[[0, "Chapter I"]]
            )
            assert client.get(root + "/pages/0").json() == original
            page = client.get(root + "/trace?limit=1").json()
            assert page["has_more"] and page["next_after"] == 71
            assert page["events"][0]["observed_at_ms"] is None
    assert len(calls) == 1


def test_trace_cursors_are_scoped_and_registration_changes_on_reupload(
    epub_factory, harness_factory, monkeypatch
):
    from diorama.agent.trace import TraceEvent
    from diorama.backend.library import Library

    play, novel = epub_factory("play"), epub_factory("novel")
    digest = hashlib.sha256(play.read_bytes()).hexdigest()
    other = hashlib.sha256(novel.read_bytes()).hexdigest()
    harness, _ = harness_factory(book_id=digest)
    service = BookIngestion(harness)
    original = service.add_book

    async def traced(book_id, source, *, on_progress, on_trace):
        await on_trace(
            TraceEvent(
                "item/completed",
                500,
                "thread",
                "turn",
                "item",
                {
                    "type": "commandExecution",
                    "aggregatedOutput": "exact output\n",
                    "exitCode": 127,
                },
                {},
                1,
                "ingest",
            )
        )
        return await original(book_id, source, on_progress=on_progress)

    monkeypatch.setattr(service, "add_book", traced)
    lib = Library(service)
    harness.publication_root.mkdir(parents=True, exist_ok=True)
    lib.initialize()
    lib.ingest_upload(play)
    lib.ingest_upload(novel)
    base = "/api/books/" + digest
    with TestClient(create_app(service), base_url="http://localhost") as client:
        deadline = time.monotonic() + 5
        while client.get(base + "/activity").json()["status"] != "ready":
            assert time.monotonic() < deadline
            time.sleep(0.02)
        snap = client.get(base + "/activity").json()
        assert (
            snap["opening"]["text"] == "Act I\nEnter Ada.\n" and snap["sdk_count"] == 1
        )
        seq, records = 0, []
        while True:
            page = client.get(base + f"/trace?after={seq}&limit=2").json()
            assert page["registration_seq"] == snap["registration_seq"]
            records.extend(page["events"])
            seq = page["next_after"]
            if not page["has_more"]:
                break
        assert seq == snap["trace_revision"] == snap["preparation_revision"]
        assert [row["seq"] for row in records] == sorted(
            {row["seq"] for row in records}
        )
        sdk = next(r for r in records if r["origin"] == "sdk")
        claim = next(r for r in records if r["name"] == "opening")
        assert sdk["run"] == claim["seq"] == claim["run"]
        assert "exact output" not in json.dumps(records)
        assert "exact output" not in client.get("/api/books").text
        detail = client.get(base + f"/trace/{sdk['seq']}").json()
        assert detail["fields"]["aggregatedOutput"] == "exact output\n"
        assert client.get(f"/api/books/{other}/trace/{sdk['seq']}").status_code == 404
        assert client.get(base + f"/trace?after={seq}").json()["next_after"] == seq
        for query in ["limit=0", "limit=101", "after=-1"]:
            assert client.get(base + "/trace?" + query).status_code == 422
        assert client.delete(base, headers=WRITE).status_code == 204
        for suffix in ["/activity", "/trace", f"/trace/{sdk['seq']}"]:
            assert client.get(base + suffix).status_code == 404
        client.post("/api/books", headers=UPLOAD, content=play.read_bytes())
        assert client.get(base + "/activity").json()["registration_seq"] > seq
        assert client.get(base + f"/trace/{sdk['seq']}").status_code == 404
