import asyncio
import os

import pytest
from conftest import as_book

from diorama.agent import harness as module
from diorama.agent.tools import InvalidCandidate, InvalidEpub, inspect_epub
from diorama.agent.worker import WorkerError, WorkerLost


async def test_reuses_validated_edition(harness_factory, epub_factory):
    harness, calls = harness_factory(book_id="../shelf")
    source = epub_factory()
    result = await harness.ingest_epub(book_id="../shelf", source_epub=source)
    assert result.open_book().id == "../shelf"
    assert result.source_epub.read_bytes() == source.read_bytes()
    assert result.manifest_path.is_relative_to(harness.publication_root)
    assert "diorama.agent.tools.inspect_epub" in calls[0][0]
    assert result == await harness.ingest_epub(book_id="../shelf", source_epub=source)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "fault", ["missing", "malformed", "wrong-id", "symlink", "fifo", "oversized"]
)
async def test_invalid_file_gets_one_repair_then_fails(
    harness_factory, epub_factory, fault
):
    async def broken(source, scratch, calls):
        path = scratch / "candidate.json"
        if len(calls[0]) > 1:
            return
        if fault == "malformed":
            path.write_bytes(b"{")
        elif fault == "wrong-id":
            path.write_text(as_book(inspect_epub(source), "wrong").model_dump_json())
        elif fault == "symlink":
            path.symlink_to(source)
        elif fault == "fifo":
            os.mkfifo(path)
        elif fault == "oversized":
            with path.open("wb") as f:
                f.truncate(module.MAX_MANIFEST + 1)

    harness, calls = harness_factory(broken)
    with pytest.raises(InvalidCandidate):
        await harness.ingest_epub(book_id="test", source_epub=epub_factory())
    assert [len(c) for c in calls] == [2]
    assert "Diagnostic" in calls[0][1]
    assert not list((harness.publication_root / "manifests").glob("*.json"))


async def test_repairs_in_same_worker(harness_factory, epub_factory):
    async def repair(source, scratch, calls):
        if len(calls[0]) == 2:
            (scratch / "candidate.json").write_text(
                as_book(inspect_epub(source)).model_dump_json()
            )

    harness, calls = harness_factory(repair)
    result = await harness.ingest_epub(book_id="test", source_epub=epub_factory())
    assert result.open_book().id == "test"
    assert [len(c) for c in calls] == [2]


@pytest.mark.parametrize("recover", [True, False])
async def test_transport_retries_fresh_workspace(
    harness_factory, epub_factory, recover
):
    paths = []

    async def disconnect(source, scratch, calls):
        assert all(not p.exists() for p in paths)
        paths.append(scratch)
        if len(calls) == 1 or not recover:
            raise WorkerLost("transport closed")
        (scratch / "candidate.json").write_text(
            as_book(inspect_epub(source)).model_dump_json()
        )

    harness, calls = harness_factory(disconnect)
    if recover:
        assert (
            await harness.ingest_epub(book_id="test", source_epub=epub_factory())
        ).open_book().id == "test"
    else:
        with pytest.raises(WorkerLost):
            await harness.ingest_epub(book_id="test", source_epub=epub_factory())
    assert [len(c) for c in calls] == [1, 1]


@pytest.mark.parametrize("cancel", [True, False])
async def test_cancellation_or_deadline_cleans_workspace(
    harness_factory, epub_factory, cancel
):
    started = asyncio.Event()
    stopped = asyncio.Event()
    paths = []

    async def block(source, scratch, calls):
        paths.append(scratch)
        started.set()
        try:
            await asyncio.Future()
        finally:
            stopped.set()

    harness, _ = harness_factory(block, timeout=3 if cancel else 0.2)
    task = asyncio.create_task(
        harness.ingest_epub(book_id="test", source_epub=epub_factory())
    )
    await started.wait()
    if cancel:
        task.cancel()
    with pytest.raises(asyncio.CancelledError if cancel else TimeoutError):
        await task
    assert stopped.is_set()
    assert not paths[0].exists()
    assert not list((harness.publication_root / "manifests").glob("*.json"))


async def test_concurrent_publish_returns_winner(harness_factory, epub_factory):
    barrier = asyncio.Barrier(2)

    async def race(source, scratch, calls):
        book = as_book(inspect_epub(source))
        book.uncertainties = [scratch.name]
        (scratch / "candidate.json").write_text(book.model_dump_json())
        await barrier.wait()

    harness, calls = harness_factory(race)
    source = epub_factory()
    results = await asyncio.gather(
        *(harness.ingest_epub(book_id="test", source_epub=source) for _ in range(2))
    )
    assert results[0] == results[1]
    assert results[0].open_book().uncertainties == list(results[0].uncertainties)
    assert len(calls) == 2
    assert len(list((harness.publication_root / "manifests").iterdir())) == 1


@pytest.mark.parametrize("after_publication", [True, False])
async def test_crash_recovery(
    harness_factory, epub_factory, monkeypatch, after_publication
):
    harness, calls = harness_factory()
    source = epub_factory()
    claim = module._claim

    def crash(staged, target):
        if target.parent.name == "manifests":
            if after_publication:
                claim(staged, target)
            raise OSError("simulated crash")
        claim(staged, target)

    with monkeypatch.context() as m:
        m.setattr(module, "_claim", crash)
        with pytest.raises(OSError, match="simulated"):
            await harness.ingest_epub(book_id="test", source_epub=source)
    assert (
        await harness.ingest_epub(book_id="test", source_epub=source)
    ).open_book().id == "test"
    assert len(calls) == (1 if after_publication else 2)


async def test_invalid_input_never_starts_worker(harness_factory, tmp_path):
    source = tmp_path / "invalid.epub"
    source.write_bytes(b"invalid")
    harness, calls = harness_factory()
    with pytest.raises(InvalidEpub):
        await harness.ingest_epub(book_id="test", source_epub=source)
    assert not calls


async def test_failed_turn_cannot_publish_valid_file(harness_factory, epub_factory):
    async def fail(source, scratch, calls):
        (scratch / "candidate.json").write_text(
            as_book(inspect_epub(source)).model_dump_json()
        )
        raise WorkerError("failed turn")

    harness, calls = harness_factory(fail)
    with pytest.raises(WorkerError):
        await harness.ingest_epub(book_id="test", source_epub=epub_factory())
    assert len(calls) == 1
    assert not list((harness.publication_root / "manifests").iterdir())
