import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from unittest.mock import AsyncMock, Mock

import pytest
from conftest import as_book
from openai_codex.generated.v2_all import (
    ItemCompletedNotification,
    ItemStartedNotification,
    TurnCompletedNotification,
)
from openai_codex.models import Notification

from diorama.agent.harness import GeppettoHarness
from diorama.agent.preparation import follow_outline, opening_update, source_previews
from diorama.agent.tools import inspect_epub, read_passage
from diorama.agent.trace import FIELD_LIMIT, project
from diorama.agent.worker import Worker, WorkerError, WorkerLost
from diorama.schemas.book import SourceRange


def sdk_item(method="item/completed", **overrides):
    item = dict(
        type="commandExecution",
        command="python inspect.py",
        commandActions=[],
        cwd="/work",
        status="completed",
        id="cmd",
        aggregatedOutput="  a\nb\n",
        exitCode=0,
    )
    item.update(overrides)
    model = (
        ItemCompletedNotification
        if method == "item/completed"
        else ItemStartedNotification
    )
    return Notification(
        method,
        model.model_validate(
            dict(
                threadId="thread",
                turnId="turn",
                item=item,
                startedAtMs=1000,
                completedAtMs=1234,
            )
        ),
    )


def terminal(status="completed"):
    return Notification(
        "turn/completed",
        TurnCompletedNotification.model_validate(
            dict(
                threadId="thread",
                turn=dict(id="turn", status=status, items=[], durationMs=400),
            )
        ),
    )


def worker_for(stream):
    handle = Mock(id="turn", stream=stream, interrupt=AsyncMock())
    return Worker(Mock(id="thread", turn=AsyncMock(return_value=handle))), handle


def test_project_preserves_only_observed_display_fields():
    raw = sdk_item(status="failed", exitCode=127)
    row = project(raw)
    assert row.fields == {
        "command": "python inspect.py",
        "cwd": "/work",
        "status": "failed",
        "type": "commandExecution",
        "id": "cmd",
        "aggregatedOutput": "  a\nb\n",
        "exitCode": 127,
        "completedAtMs": 1234,
    }
    assert row.observed_at_ms > 1234 and row.item_id == "cmd"
    setup = project(
        sdk_item(command="cat /installed/SKILL.md", aggregatedOutput="private setup")
    )
    assert setup.markers == {"aggregatedOutput": {"omitted": True}}
    assert "private setup" not in str(setup)
    redacted = project(
        sdk_item(command="cat auth.json", aggregatedOutput="hidden value")
    )
    assert set(redacted.markers) == {"command", "aggregatedOutput"}
    assert "hidden value" not in str(redacted)
    output = "αβγ" * 5000
    truncated = project(sdk_item(aggregatedOutput=output))
    assert output.startswith(truncated.fields["aggregatedOutput"])
    assert len(truncated.fields["aggregatedOutput"].encode()) <= FIELD_LIMIT
    assert truncated.markers["aggregatedOutput"]["original_bytes"] == len(
        output.encode()
    )
    assert project(Notification("item/reasoning/textDelta", None)) is None
    raw = sdk_item(
        type="fileChange",
        changes=[dict(path="/work/run.py", diff="private diff", kind={"type": "add"})],
    )
    file = project(raw)
    assert file.fields["changes"] == [{"path": "/work/run.py", "kind": "add"}]
    assert "private diff" not in str(file)


@pytest.mark.parametrize("end", ["completed", "failed", None])
async def test_worker_validates_terminal_independently_of_command_exit(end):
    async def stream():
        yield sdk_item("item/started", status="inProgress")
        yield sdk_item(status="failed", exitCode=127)
        if end:
            yield terminal(end)

    worker, _ = worker_for(stream)
    listener = AsyncMock()
    if end != "completed":
        with pytest.raises(WorkerError):
            await worker.run_traced("read EPUB", listener)
    else:
        await worker.run_traced("read EPUB", listener)
        # A listener failure cannot launch a second model turn.
        await worker.run_traced("read again", AsyncMock(side_effect=ValueError()))
        assert worker.thread.turn.await_count == 2
    assert listener.await_args_list[1].args[0].fields["exitCode"] == 127


async def test_cancel_traced_turn_interrupts_and_closes_stream():
    entered, closed = asyncio.Event(), asyncio.Event()

    async def stream():
        try:
            entered.set()
            yield sdk_item("item/started")
            await asyncio.Future()
        finally:
            closed.set()

    worker, handle = worker_for(stream)
    task = asyncio.create_task(worker.run_traced("read", AsyncMock()))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set()
    handle.interrupt.assert_awaited_once()


async def test_observers_remain_scoped_through_worker_loss_repair_and_cache(
    agent, epub_factory, tmp_path
):
    async def ingest(edition):
        book = epub_factory(edition)
        emissions = []
        calls = 0

        @asynccontextmanager
        async def factory(agent, source, scratch):
            class Scripted:
                async def run_traced(self, task, emit):
                    nonlocal calls
                    calls += 1
                    await emit(
                        replace(
                            project(terminal()),
                            thread_id=f"{edition}-{int(calls > 1)}",
                            turn_id=str(calls),
                        )
                    )
                    if calls == 1:
                        raise WorkerLost()
                    if calls == 3:
                        (scratch / "candidate.json").write_text(
                            as_book(inspect_epub(source), edition).model_dump_json()
                        )

            yield Scripted()

        async def listen(event):
            emissions.append(
                (event.attempt, event.purpose, event.thread_id, event.turn_id)
            )

        harness = GeppettoHarness(agent, factory, tmp_path / "library")
        await harness.ingest_epub(book_id=edition, source_epub=book, on_trace=listen)
        await harness.ingest_epub(book_id=edition, source_epub=book, on_trace=listen)
        assert emissions == [
            (1, "ingest", edition + "-0", "1"),
            (2, "ingest", edition + "-1", "2"),
            (2, "repair", edition + "-1", "3"),
        ]

    await asyncio.gather(ingest("play"), ingest("novel"))


async def test_excerpt_ranges_and_equal_length_outline_corrections(
    epub_factory, tmp_path
):
    index = inspect_epub(epub_factory("play"))
    opened = opening_update(index).previews[0]
    assert opened["text"] == "Act I\nEnter Ada.\n"
    snapshots = []
    updates = asyncio.Queue()

    def report(update):
        snapshots.append(update)
        updates.put_nowait(update)

    async def turn():
        path = tmp_path / "preview.json"
        for label in ("Scene II", "Scene I"):
            path.write_text(json.dumps([[0, "Act I"], [1, label]]))
            await asyncio.wait_for(updates.get(), timeout=2)
        path.write_text("[")

    await follow_outline(turn(), tmp_path, index, report)
    assert [s.outline[-1][1] for s in snapshots] == ["Scene II", "Scene I"]
    for snap in snapshots:
        for entry in snap.previews:
            assert entry["text"] == read_passage(index, SourceRange(**entry["source"]))
    doc = index.spine[0]
    index.spine[0] = replace(doc, headings=[*doc.headings, doc.headings[0]])
    assert "text" not in source_previews(index, [(0, "Act I")])[0]
    index.spine[0] = replace(doc, headings=[])
    assert opening_update(index).previews[0]["title"] == "Opening of this edition"
    index.spine[0] = replace(doc, headings=doc.headings[1:])
    assert opening_update(index).previews[0]["text"] == opened["text"]
