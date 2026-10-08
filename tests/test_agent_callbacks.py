from __future__ import annotations

import asyncio
import inspect
from io import StringIO

import pytest
from rich.console import Console
from tau_agent.events import (
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
)
from tau_agent.messages import AssistantMessage, TextContent, ThinkingContent
from tau_agent.provider_events import TextDeltaEvent, ThinkingDeltaEvent
from tau_agent.tools import AgentToolResult
from tau_ai import FakeProvider

from diorama.agents import (
    AgentTraceEvent,
    BaseDioramaAgent,
    RichTraceCallback,
    RunEvent,
)


class TraceAgent(BaseDioramaAgent[str]):
    def configure(self, *, prefix: str = "") -> None:
        self.prefix = prefix

    async def run(self, text: str) -> str:
        await self.emit_event(RunEvent("progress", {"text": text}))
        await asyncio.sleep(0)
        if text == "error":
            raise ValueError("Extraction failed")
        if text == "cancel":
            raise asyncio.CancelledError
        return self.prefix + text


async def test_callbacks_are_awaited_in_order_and_preserve_typed_run():
    events = []
    order = []

    async def first(event):
        await asyncio.sleep(0)
        events.append(event)
        order.append((event.sequence, "first"))

    def second(event):
        order.append((event.sequence, "second"))

    agent = TraceAgent(
        provider=FakeProvider([]),
        model="fake",
        callbacks=[first, second],
        prefix="book:",
    )
    assert await agent.run("test") == "book:test"
    assert [event.event.type for event in events] == [
        "run_start",
        "progress",
        "run_end",
    ]
    assert order == [
        (sequence, listener)
        for sequence in range(1, 4)
        for listener in ("first", "second")
    ]
    assert len({event.run_id for event in events}) == 1
    assert events[-1].event.details["output"] == "book:test"
    assert str(inspect.signature(agent.run)) == "(text: 'str') -> 'str'"


@pytest.mark.parametrize(
    "text, exception, terminal",
    [
        ("error", ValueError, "run_error"),
        ("cancel", asyncio.CancelledError, "run_cancelled"),
    ],
)
async def test_failure_and_cancellation_close_trace(text, exception, terminal):
    events = []
    output = StringIO()
    renderer = RichTraceCallback(console=Console(file=output, width=80))
    agent = TraceAgent(
        provider=FakeProvider([]), model="fake", callbacks=[events.append, renderer]
    )
    with pytest.raises(exception):
        await agent.run(text)
    assert events[-1].event.type == terminal
    assert renderer._runs == {}
    assert renderer._open_stream is None
    assert await agent.run("next") == "next"
    assert events[-1].run_id != events[0].run_id


async def test_concurrent_runs_have_independent_ids_and_sequences():
    events = []
    agent = TraceAgent(
        provider=FakeProvider([]), model="fake", callbacks=[events.append]
    )
    assert await asyncio.gather(agent.run("one"), agent.run("two")) == ["one", "two"]
    run_ids = {event.run_id for event in events}
    assert len(run_ids) == 2
    for run_id in run_ids:
        run = [event for event in events if event.run_id == run_id]
        assert [event.sequence for event in run] == [1, 2, 3]
        assert [event.event.type for event in run] == [
            "run_start",
            "progress",
            "run_end",
        ]


async def test_super_run_does_not_duplicate_lifecycle():
    class ChildAgent(TraceAgent):
        async def run(self, text: str) -> str:
            return await super().run(text)

    events = []
    agent = ChildAgent(
        provider=FakeProvider([]), model="fake", callbacks=[events.append]
    )
    await agent.run("book")
    assert [event.event.type for event in events] == [
        "run_start",
        "progress",
        "run_end",
    ]
    assert all(event.agent_name == "ChildAgent" for event in events)


async def test_child_task_run_does_not_inherit_parent_identity():
    class RecursiveAgent(TraceAgent):
        async def run(self, text: str) -> str:
            if text == "parent":
                return await asyncio.create_task(self.run("child"))
            return await super().run(text)

    events = []
    agent = RecursiveAgent(
        provider=FakeProvider([]), model="fake", callbacks=[events.append]
    )
    assert await agent.run("parent") == "child"
    assert len({event.run_id for event in events}) == 2
    assert sum(event.event.type == "run_start" for event in events) == 2


async def test_callback_failure_propagates_without_replacing_task_error():
    def broken(event):
        if event.event.type in {"progress", "run_error"}:
            raise LookupError("Callback failed")

    renderer = RichTraceCallback(console=Console(file=StringIO()))
    agent = TraceAgent(
        provider=FakeProvider([]), model="fake", callbacks=[broken, renderer]
    )
    with pytest.raises(LookupError, match="Callback failed"):
        await agent.run("book")
    assert agent._trace_context.get() is None
    assert renderer._runs == {}


async def test_callback_error_does_not_replace_original_agent_failure():
    def broken(event):
        if event.event.type == "run_error":
            raise LookupError("Reporting failed")

    agent = TraceAgent(provider=FakeProvider([]), model="fake", callbacks=[broken])
    with pytest.raises(ValueError, match="Extraction failed"):
        await agent.run("error")


@pytest.mark.parametrize("width", [40, 80, 120])
def test_rich_streams_text_thinking_and_tool_outputs_once(width):
    output = StringIO()
    renderer = RichTraceCallback(
        console=Console(file=output, width=width, color_system=None)
    )
    sequence = 0

    def emit(event):
        nonlocal sequence
        sequence += 1
        renderer(AgentTraceEvent("run12345", "TraceAgent", "fake", sequence, event))

    emit(RunEvent("run_start"))
    message = AssistantMessage(
        content=[
            ThinkingContent(thinking="Inspect source."),
            TextContent(text="[bold]日本語[/bold] complete."),
        ]
    )
    emit(MessageStartEvent(message=AssistantMessage()))
    emit(
        MessageUpdateEvent(
            message=message,
            assistant_message_event=ThinkingDeltaEvent(
                content_index=0, delta="Inspect source.", partial=message
            ),
        )
    )
    assert "Inspect source." in output.getvalue()
    emit(
        MessageUpdateEvent(
            message=message,
            assistant_message_event=TextDeltaEvent(
                content_index=1, delta="[bold]日本語[/bold]", partial=message
            ),
        )
    )
    assert "[bold]日本語[/bold]" in output.getvalue()
    emit(MessageEndEvent(message=message))
    emit(
        ToolExecutionStartEvent(
            tool_call_id="call1",
            tool_name="bash",
            args={"command": "python extract.py"},
        )
    )
    emit(
        ToolExecutionUpdateEvent(
            tool_call_id="call1",
            tool_name="bash",
            partial_result=AgentToolResult(content=[TextContent(text="first line\n")]),
        )
    )
    assert "first line" in output.getvalue()
    emit(
        ToolExecutionEndEvent(
            tool_call_id="call1",
            tool_name="bash",
            result=AgentToolResult(
                content=[TextContent(text="first line\nlast line\n")]
            ),
            is_error=True,
        )
    )
    emit(RunEvent("run_end", {"duration": 0.5}))
    transcript = output.getvalue()
    assert transcript.count("Inspect source.") == 1
    assert transcript.count("[bold]日本語[/bold]") == 1
    assert "complete." in transcript
    assert transcript.count("first line") == transcript.count("last line") == 1
    assert "bash: ERROR" in transcript
    assert "\x1b" not in transcript
    assert renderer._runs == {}


def test_rich_final_only_and_long_output_are_not_truncated():
    output = StringIO()
    renderer = RichTraceCallback(console=Console(file=output, width=80))
    content = "\n".join(f"paragraph {index}" for index in range(500))
    renderer(
        AgentTraceEvent(
            "test",
            "Agent",
            "fake",
            1,
            MessageEndEvent(
                message=AssistantMessage(content=[TextContent(text=content)])
            ),
        )
    )
    transcript = output.getvalue()
    assert "paragraph 0\n" in transcript
    assert "paragraph 499" in transcript
