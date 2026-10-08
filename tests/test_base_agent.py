from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, cast

import pytest
from tau_ai import FakeProvider

from diorama.agents import BaseDioramaAgent
from diorama.agents.ebook_structure_agent import EbookStructureAgent
from diorama.models.storybook import StoryBook


class EchoAgent(BaseDioramaAgent[str]):
    def configure(self, *, prefix: str = "") -> None:
        self.prefix = prefix

    async def run(self, text: str, *, suffix: str = "") -> str:
        return f"{self.prefix}{text}{suffix}"


def test_base_requires_concrete_configuration_and_execution():
    class MissingRun(BaseDioramaAgent[str]):
        def configure(self, **kwargs: Any) -> None:
            pass

    class MissingConfiguration(BaseDioramaAgent[str]):
        async def run(self, text: str) -> str:
            return text

    for agent_class in (BaseDioramaAgent, MissingRun, MissingConfiguration):
        with pytest.raises(TypeError, match="abstract"):
            cast(Any, agent_class)(provider=FakeProvider([]), model="fake")


@pytest.mark.parametrize("model", ["", " ", "\n\t"])
def test_base_rejects_empty_model_before_configuration(model):
    with pytest.raises(ValueError, match="model must be non-empty"):
        EchoAgent(provider=FakeProvider([]), model=model, unknown_option=True)


@pytest.mark.asyncio
async def test_constructor_configuration_and_run_arguments_are_separate():
    provider = FakeProvider([])
    agent = EchoAgent(provider=provider, model="fake", prefix="Hello ")
    assert agent.provider is provider
    assert agent.model == "fake"
    assert await agent.run("reader", suffix="!") == "Hello reader!"
    with pytest.raises(TypeError):
        EchoAgent(provider=provider, model="fake", unknown_option=True)


@pytest.mark.asyncio
async def test_agent_exceptions_and_cancellation_propagate():
    class FailingAgent(EchoAgent):
        async def run(self, text: str, *, suffix: str = "") -> str:
            if text == "cancel":
                raise asyncio.CancelledError
            raise ValueError("Task failed")

    agent = FailingAgent(provider=FakeProvider([]), model="fake")
    with pytest.raises(ValueError, match="Task failed"):
        await agent.run("fail")
    with pytest.raises(asyncio.CancelledError):
        await agent.run("cancel")


def test_ebook_agent_implements_the_shared_api():
    provider = FakeProvider([])
    agent: BaseDioramaAgent[StoryBook] = EbookStructureAgent(
        provider=provider,
        model="fake",
        max_turns=12,
        max_repair_attempts=1,
        workspace_root=".ebook-runs",
    )
    assert isinstance(agent, BaseDioramaAgent)
    assert agent.provider is provider
    assert agent.model == "fake"
    assert isinstance(agent, EbookStructureAgent)
    assert agent.max_turns == 12
    assert agent.max_repair_attempts == 1
    with pytest.raises(TypeError):
        EbookStructureAgent(provider=provider, model="fake", unknown_option=True)


async def test_shared_workspace_lifecycle_and_output_are_independent_of_checkout(
    tmp_path, monkeypatch
):
    class FileAgent(EchoAgent):
        async def run(self, text: str, *, suffix: str = "") -> str:
            with self.working_directory() as directory:
                self.scratch = directory
                (directory / "intermediate.txt").write_text(text)
                self.saved = await self.save_output("result.txt", text + suffix)
            return text + suffix

    caller = tmp_path / "caller"
    caller.mkdir()
    monkeypatch.chdir(caller)
    agent = FileAgent(provider=FakeProvider([]), model="fake", workspace_root="runs")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert await agent.run("content", suffix="!") == "content!"
    assert Path.cwd() == elsewhere
    assert agent.workspace_root == caller / "runs"
    assert not agent.scratch.exists()
    assert agent.saved is not None
    assert agent.saved.read_text() == "content!"
    assert list((caller / "runs").glob("*/*")) == [agent.saved]
    assert not (elsewhere / "runs").exists()


async def test_base_does_not_create_persistent_output_by_default(tmp_path):
    class FileAgent(EchoAgent):
        async def run(self, text: str, *, suffix: str = "") -> str:
            assert await self.save_output("result.txt", text) is None
            return text

    agent = FileAgent(provider=FakeProvider([]), model="fake")
    assert await agent.run("content") == "content"


@pytest.mark.parametrize(
    "filename",
    [
        "",
        ".",
        "..",
        "../outside.txt",
        "/outside.txt",
        "folder/output.txt",
        "..\\outside.txt",
    ],
)
async def test_shared_output_rejects_paths(filename, tmp_path):
    class FileAgent(EchoAgent):
        async def run(self, text: str, *, suffix: str = "") -> str:
            await self.save_output(filename, text)
            return text

    agent = FileAgent(
        provider=FakeProvider([]), model="fake", workspace_root=tmp_path / "runs"
    )
    with pytest.raises(ValueError, match="single path component"):
        await agent.run("content")
    assert not (tmp_path / "runs").exists()


async def test_cancelled_publication_waits_and_removes_its_artifact(tmp_path):
    import threading

    started = threading.Event()
    release = threading.Event()

    class FileAgent(EchoAgent):
        def _publish_output(self, destination, filename, content):
            started.set()
            release.wait(timeout=2)
            return super()._publish_output(destination, filename, content)

        async def run(self, text: str, *, suffix: str = "") -> str:
            await self.save_output("result.txt", text)
            return text

    root = tmp_path / "runs"
    agent = FileAgent(provider=FakeProvider([]), model="fake", workspace_root=root)
    task = asyncio.create_task(agent.run("content"))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
        assert not root.exists() or list(root.iterdir()) == []
    finally:
        release.set()
        if not task.done():
            task.cancel()
