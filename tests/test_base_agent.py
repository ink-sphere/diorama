from __future__ import annotations

import asyncio
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
