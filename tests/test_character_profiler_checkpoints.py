from __future__ import annotations

import asyncio
import json
import threading

import pytest
from tau_ai import FakeProvider
from test_character_profiler_agent import (
    RegistryProvider,
    WaitingProvider,
    book,
    chapter,
    update,
)

from diorama.agents.character_profiler_agent import (
    CharacterProfilerAgent,
    CharacterProfilerError,
    agent as agent_module,
    checkpoints,
)


def agent(provider, tmp_path, **kwargs):
    return CharacterProfilerAgent(
        provider=provider,
        model="fake",
        workspace_root=tmp_path / "runs",
        max_repair_attempts=0,
        **kwargs,
    )


async def test_resume_skips_completed_nodes_and_matches_uninterrupted_run(tmp_path):
    source = book(chapter("Alice."), chapter("Bob."))
    path = tmp_path / "checkpoint.json"
    broken = RegistryProvider([[update()], [update("Bob", ids=["bad"])]])
    with pytest.raises(CharacterProfilerError, match="unknown character reference"):
        await agent(broken, tmp_path).run(source, checkpoint_path=path)
    saved = checkpoints.load_checkpoint(path, source, 2)
    assert saved.completed_nodes == saved.batch == 1
    assert not saved.reconciled
    assert not list(tmp_path.rglob("character_profiles.json"))
    events = []
    provider = RegistryProvider([[update("Bob")], []])
    resumed = await agent(provider, tmp_path, callbacks=[events.append]).run(
        source, checkpoint_path=path, resume=True
    )
    assert provider.requests[0]["excerpt"]["text"] == "Bob."
    assert provider.requests[0]["registry"][0]["id"] == "c1"
    assert resumed[0].id == saved.profiles[0].id
    assert next(
        e.event.details for e in events if e.event.type == "profiling_start"
    ) == {
        "total_nodes": 2,
        "completed_nodes": 1,
    }
    uninterrupted = await agent(
        RegistryProvider([[update()], [update("Bob")], []]), tmp_path
    ).run(source)
    assert resumed == uninterrupted
    assert checkpoints.load_checkpoint(path, source, 2).reconciled


async def test_partial_split_node_is_replayed_without_partial_registry(tmp_path):
    source = book(chapter("Alice."), chapter("Bob arrived. Bob left."))
    path = tmp_path / "checkpoint.json"
    provider = RegistryProvider([[update()], [update("Bob")], [update(ids=["bad"])]])
    with pytest.raises(CharacterProfilerError):
        await agent(provider, tmp_path, max_chunk_characters=13).run(
            source, checkpoint_path=path
        )
    saved = checkpoints.load_checkpoint(path, source, 2)
    assert saved.batch == saved.completed_nodes == 1
    assert [p.character_name for p in saved.profiles] == ["Alice"]
    resumed_provider = RegistryProvider([[update("Bob")], [], []])
    result = await agent(resumed_provider, tmp_path, max_chunk_characters=13).run(
        source, checkpoint_path=path, resume=True
    )
    assert [r["excerpt"]["text"] for r in resumed_provider.requests[:-1]] == [
        "Bob arrived. ",
        "Bob left.",
    ]
    uninterrupted = await agent(
        RegistryProvider([[update()], [update("Bob")], [], []]),
        tmp_path,
        max_chunk_characters=13,
    ).run(source)
    assert result == uninterrupted


async def test_resume_retries_only_reconciliation_and_can_republish(tmp_path):
    source = book(chapter("Alice and Al."))
    path = tmp_path / "checkpoint.json"
    with pytest.raises(CharacterProfilerError):
        await agent(
            RegistryProvider([[update(), update("Al")], [update("New")]]), tmp_path
        ).run(source, checkpoint_path=path)
    saved = checkpoints.load_checkpoint(path, source, 1)
    assert saved.completed_nodes == 1 and not saved.reconciled
    provider = RegistryProvider([[update(ids=["c2", "c1"])]])
    profiles = await agent(provider, tmp_path).run(
        source, checkpoint_path=path, resume=True
    )
    assert [r["mode"] for r in provider.requests] == ["reconcile"]
    assert len(profiles) == 1
    assert profiles[0].id == saved.profiles[0].id
    assert checkpoints.load_checkpoint(path, source, 1).reconciled
    no_calls = FakeProvider([])
    assert (
        await agent(no_calls, tmp_path).run(source, checkpoint_path=path, resume=True)
        == profiles
    )
    assert not no_calls.calls


@pytest.mark.parametrize("version", [1, 2])
async def test_legacy_profiles_without_full_body_prompt_can_resume(tmp_path, version):
    source = book(chapter("Alice."))
    path = tmp_path / "checkpoint.json"
    await agent(RegistryProvider([[update()]]), tmp_path).run(
        source, checkpoint_path=path
    )
    data = json.loads(path.read_text())
    data["version"] = version
    del data["profiles"][0]["full_body_visual_prompt"]
    path.write_text(json.dumps(data))
    before = path.read_bytes()
    restored = checkpoints.load_checkpoint(path, source, 1)
    assert path.read_bytes() == before
    assert "not generated" in restored.profiles[0].full_body_visual_prompt
    provider = FakeProvider([])
    profiles = await agent(provider, tmp_path).run(
        source, checkpoint_path=path, resume=True
    )
    assert not provider.calls
    assert profiles == restored.profiles
    assert profiles[0].id == data["profiles"][0]["id"]
    assert (
        profiles[0].headshot_visual_prompt
        == data["profiles"][0]["headshot_visual_prompt"]
    )


@pytest.mark.parametrize(
    "corruption",
    [
        "missing",
        "truncated",
        "version",
        "content",
        "position",
        "batch",
        "duplicates",
        "book_id",
        "reconciled",
        "full_body_type",
    ],
)
async def test_invalid_checkpoint_rejected_before_model_call(tmp_path, corruption):
    source = book(chapter("Alice."), chapter("Next."))
    path = tmp_path / "checkpoint.json"
    with pytest.raises(CharacterProfilerError):
        await agent(
            RegistryProvider([[update()], [update(ids=["bad"])]]), tmp_path
        ).run(source, checkpoint_path=path)
    data = json.loads(path.read_text())
    if corruption == "missing":
        path.unlink()
    elif corruption == "truncated":
        path.write_text('{"version":')
    elif corruption == "content":
        source = book(chapter("Changed text with same book ID."), chapter("Next."))
    else:
        if corruption == "version":
            data["version"] = 999
        elif corruption == "position":
            data["completed_nodes"] = 3
        elif corruption == "batch":
            data["batch"] = 0
        elif corruption == "duplicates":
            data["profiles"].append(data["profiles"][0])
        elif corruption == "book_id":
            data["profiles"][0]["book_id"] = "other"
        elif corruption == "reconciled":
            data["reconciled"] = True
        elif corruption == "full_body_type":
            data["profiles"][0]["full_body_visual_prompt"] = None
        path.write_text(json.dumps(data))
    before = path.read_bytes() if path.exists() else None
    provider = FakeProvider([])
    with pytest.raises(CharacterProfilerError, match="Unable to prepare checkpoint"):
        await agent(provider, tmp_path).run(source, checkpoint_path=path, resume=True)
    assert not provider.calls
    assert (path.read_bytes() if path.exists() else None) == before


async def test_fresh_run_cannot_overwrite_existing_checkpoint(tmp_path):
    path = tmp_path / "checkpoint.json"
    path.write_text("existing state")
    provider = FakeProvider([])
    with pytest.raises(CharacterProfilerError, match="Unable to prepare checkpoint"):
        await agent(provider, tmp_path).run(book(), checkpoint_path=path)
    assert path.read_text() == "existing state"
    assert not list(tmp_path.glob(".checkpoint-*"))
    assert not provider.calls


async def test_resume_requires_path(tmp_path):
    with pytest.raises(CharacterProfilerError, match="requires checkpoint_path"):
        await agent(FakeProvider([]), tmp_path).run(book(), resume=True)


async def test_atomic_write_failure_preserves_previous_checkpoint(
    tmp_path, monkeypatch
):
    path = tmp_path / "checkpoint.json"
    source = book()
    await agent(FakeProvider([]), tmp_path).run(source, checkpoint_path=path)
    before = path.read_bytes()
    saved = checkpoints.load_checkpoint(path, source, 0)

    def fail(*args):
        raise OSError("Publication failed")

    monkeypatch.setattr(checkpoints.os, "replace", fail)
    with pytest.raises(OSError, match="Publication failed"):
        checkpoints.write_checkpoint(
            path, saved.model_copy(update={"reconciled": False})
        )
    assert path.read_bytes() == before
    assert not list(tmp_path.glob(".checkpoint-*"))


async def test_cancellation_preserves_completed_node_checkpoint(tmp_path):
    source = book(chapter("Alice."), chapter("Next."))
    path = tmp_path / "checkpoint.json"
    waiting = WaitingProvider()
    profiler = agent(RegistryProvider([[update()]]), tmp_path)

    def switch_provider(trace):
        if trace.event.type == "node_end":
            profiler.provider = waiting

    profiler.callbacks = (switch_provider,)
    task = asyncio.create_task(profiler.run(source, checkpoint_path=path))
    await asyncio.wait_for(waiting.started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 2)
    saved = checkpoints.load_checkpoint(path, source, 2)
    assert saved.completed_nodes == 1
    assert saved.profiles[0].character_name == "Alice"
    assert waiting.closed.is_set()


async def test_cancellation_waits_for_inflight_checkpoint_publication(
    tmp_path, monkeypatch
):
    source = book()
    path = tmp_path / "checkpoint.json"
    original_write = checkpoints.write_checkpoint
    started = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()

    def delayed_write(*args, **kwargs):
        loop.call_soon_threadsafe(started.set)
        assert release.wait(5)
        original_write(*args, **kwargs)

    monkeypatch.setattr(agent_module, "write_checkpoint", delayed_write)
    task = asyncio.create_task(
        agent(FakeProvider([]), tmp_path).run(source, checkpoint_path=path)
    )
    try:
        await asyncio.wait_for(started.wait(), 2)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 2)
    assert checkpoints.load_checkpoint(path, source, 0).completed_nodes == 0
    assert not list(tmp_path.glob(".checkpoint-*"))


async def test_group_checkpoint_counts_nodes_and_resume_skips_whole_completed_group(
    tmp_path,
):
    source = book(*(chapter(f"Scene {i}.") for i in range(5)))
    path = tmp_path / "checkpoint.json"
    events = []
    with pytest.raises(CharacterProfilerError):
        await agent(
            RegistryProvider([[update()], [update(ids=["invalid"])]]),
            tmp_path,
            max_nodes_group=2,
            callbacks=[events.append],
        ).run(source, checkpoint_path=path)
    saved = checkpoints.load_checkpoint(path, source, 5)
    assert saved.version == 2
    assert saved.completed_nodes == 2 and saved.batch == 1
    ends = [e.event for e in events if e.event.type == "group_end"]
    assert len(ends) == 1
    assert ends[0].details["first_node_index"] == 1
    assert ends[0].details["last_node_index"] == 2
    provider = RegistryProvider([[update("Bob")], [], []])
    profiles = await agent(provider, tmp_path, max_nodes_group=2).run(
        source, checkpoint_path=path, resume=True
    )
    assert [n["text"] for n in provider.requests[0]["excerpt"]["nodes"]] == [
        "Scene 2.",
        "Scene 3.",
    ]
    assert provider.requests[1]["excerpt"]["text"] == "Scene 4."
    assert profiles[0].id == saved.profiles[0].id
    final = checkpoints.load_checkpoint(path, source, 5)
    assert final.completed_nodes == 5 and final.batch == 3 and final.reconciled
    uninterrupted = await agent(
        RegistryProvider([[update()], [update("Bob")], [], []]),
        tmp_path,
        max_nodes_group=2,
    ).run(source)
    assert profiles == uninterrupted


async def test_legacy_checkpoint_can_resume_with_larger_groups(tmp_path):
    source = book(*(chapter(f"Scene {i}.") for i in range(4)))
    path = tmp_path / "checkpoint.json"
    with pytest.raises(CharacterProfilerError):
        await agent(
            RegistryProvider([[update()], [update(ids=["bad"])]]), tmp_path
        ).run(source, checkpoint_path=path)
    data = json.loads(path.read_text())
    data["version"] = 1
    path.write_text(json.dumps(data))
    provider = RegistryProvider([[]])
    profiles = await agent(provider, tmp_path, max_nodes_group=3).run(
        source, checkpoint_path=path, resume=True
    )
    assert len(provider.requests[0]["excerpt"]["nodes"]) == 3
    assert profiles[0].id == data["profiles"][0]["id"]
    saved = checkpoints.load_checkpoint(path, source, 4)
    assert saved.version == 2
    assert saved.completed_nodes == 4 and saved.batch == 2
