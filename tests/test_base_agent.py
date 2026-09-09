"""Shared lifecycle guarantees without EPUBs, credentials, or network calls."""

import asyncio
import warnings
from io import StringIO

import pytest
from rich.console import Console
from tau_agent.events import TurnStartEvent
from tau_ai.fake import FakeProvider

from diorama.agents import BaseDioramaAgent, EbookLoaderAgent
from diorama.utils.trace import TraceDisplayCallback
from diorama.utils.trace_events import DioramaAgentEvent, LoaderEvent


class ExampleAgent(BaseDioramaAgent):
    async def answer(self, value="answer"):
        async with self._run() as outcome:
            outcome.message = value
            return value


@pytest.mark.parametrize("model,max_turns", [(" ", 1), ("model", 0)])
def test_configuration_validation(model, max_turns):
    with pytest.raises(ValueError):
        ExampleAgent(FakeProvider([]), model, max_turns=max_turns)


async def test_repeated_runs_and_compatibility():
    events = []
    provider = FakeProvider([])
    agent = ExampleAgent(provider, "scripted", on_event=events.append)
    assert await agent.answer("first") == "first"
    assert await agent.answer("second") == "second"
    assert [event.phase for event in events] == ["start", "complete"] * 2
    assert events[-1].message == "second"
    assert agent.provider is provider
    assert not agent._running
    assert issubclass(EbookLoaderAgent, BaseDioramaAgent)
    assert isinstance(events[0], LoaderEvent)
    assert LoaderEvent is DioramaAgentEvent


async def test_callback_failure_resets_each_run_and_respects_warning_filters():
    events = []

    def broken(event):
        events.append(event)
        raise ValueError("private error")

    agent = ExampleAgent(FakeProvider([]), "scripted", on_event=broken)
    for _ in range(2):
        with pytest.warns(RuntimeWarning, match="tracing disabled"):
            assert await agent.answer() == "answer"
    assert len(events) == 2
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        assert await agent.answer() == "answer"


@pytest.mark.parametrize("error", [ValueError("failed"), asyncio.CancelledError()])
async def test_failure_preserves_exception_and_never_completes(error):
    events = []
    agent = ExampleAgent(FakeProvider([]), "scripted", on_event=events.append)
    with pytest.raises(type(error)) as caught:
        async with agent._run():
            raise error
    assert caught.value is error
    assert [event.phase for event in events] == [
        "start",
        "cancelled" if isinstance(error, asyncio.CancelledError) else "error",
    ]
    assert not agent._running
    assert await agent.answer() == "answer"


async def test_overlapping_runs_are_rejected_without_resetting_active_state():
    events = []
    agent = ExampleAgent(FakeProvider([]), "scripted", on_event=events.append)
    async with agent._run():
        with pytest.raises(RuntimeError, match="already running"):
            await agent.answer()
        assert agent._running
    assert [event.phase for event in events] == ["start", "complete"]


def test_harnesses_are_fresh():
    agent = ExampleAgent(FakeProvider([]), "scripted", max_turns=7)
    first = agent._create_harness(system="System")
    second = agent._create_harness(system="System")
    assert first is not second
    assert not first.messages and not second.messages


@pytest.mark.parametrize("mode", ["exhaust", "stop", "error", "cancel"])
async def test_stream_cleanup_and_forwarding(mode):
    events = []
    closed = []

    class Harness:
        async def prompt(self, prompt):
            try:
                yield TurnStartEvent()
                if mode == "error":
                    raise ValueError("provider failed")
                if mode == "cancel":
                    raise asyncio.CancelledError()
                yield TurnStartEvent()
            finally:
                closed.append(True)

    agent = ExampleAgent(FakeProvider([]), "scripted", on_event=events.append)
    if mode in {"error", "cancel"}:
        with pytest.raises(ValueError if mode == "error" else asyncio.CancelledError):
            await agent._consume(Harness(), "prompt")
    else:
        await agent._consume(Harness(), "prompt", stop_when=lambda: mode == "stop")
    assert closed == [True]
    assert len(events) == (2 if mode == "exhaust" else 1)


def test_display_handles_domain_independent_completion():
    output = StringIO()
    with TraceDisplayCallback(console=Console(file=output, width=60)) as trace:
        trace(DioramaAgentEvent("research_start", "Researching", phase="start"))
        trace(DioramaAgentEvent("research_done", "Finished", phase="complete"))
    assert "research_done" in output.getvalue()
    assert trace._status is None
