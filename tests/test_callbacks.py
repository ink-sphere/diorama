"""Tests for the ReAct agent callback system."""

from __future__ import annotations

import pytest

from diorama.core import Callback, Event, ReactAgent, RichLoggingCallback
from diorama.core.demo_tools import CalculatorTool


class CaptureCallback(Callback):
    """Simple callback that captures all events for testing."""

    def __init__(self) -> None:
        """Initialise the capture callback."""
        self.events: list[Event] = []

    def on_event(self, event: Event) -> None:
        """Capture the event."""
        self.events.append(event)

    @property
    def event_types(self) -> list[str]:
        """Return list of event types that were captured."""
        return [e.event_type for e in self.events]


# ============================================================================ #
# Event Emission Tests
# ============================================================================ #


@pytest.mark.asyncio
async def test_ready_event_emitted():
    """Verify 'ready' event is emitted at start of run()."""
    capture = CaptureCallback()
    agent = ReactAgent(
        tools=[CalculatorTool()],
        callbacks=[capture],
    )
    await agent.run("What is 5 + 3?")

    assert "ready" in capture.event_types
    assert capture.events[0].event_type == "ready"
    assert capture.events[0].data["tool_count"] == 1


@pytest.mark.asyncio
async def test_processing_event_emitted():
    """Verify 'processing' event is emitted at each iteration."""
    capture = CaptureCallback()
    agent = ReactAgent(
        tools=[CalculatorTool()],
        callbacks=[capture],
    )
    await agent.run("What is 5 + 3?")

    processing_events = [e for e in capture.events if e.event_type == "processing"]
    assert len(processing_events) > 0
    assert processing_events[0].data["iteration"] == 1


@pytest.mark.asyncio
async def test_assistant_message_event():
    """Verify 'assistant_message' event is emitted."""
    capture = CaptureCallback()
    agent = ReactAgent(
        tools=[CalculatorTool()],
        callbacks=[capture],
    )
    await agent.run("What time is it?")

    message_events = [e for e in capture.events if e.event_type == "assistant_message"]
    assert len(message_events) > 0
    assert "content" in message_events[0].data
    assert "finish_reason" in message_events[0].data


@pytest.mark.asyncio
async def test_tool_call_and_output_events():
    """Verify 'tool_call' and 'tool_output' events are emitted in order."""
    capture = CaptureCallback()
    agent = ReactAgent(
        tools=[CalculatorTool()],
        callbacks=[capture],
    )
    await agent.run("What is 5 + 3?")

    event_types = capture.event_types
    assert "tool_call" in event_types
    assert "tool_output" in event_types

    # Verify order: tool_call comes before tool_output
    tool_call_idx = event_types.index("tool_call")
    tool_output_idx = event_types.index("tool_output")
    assert tool_call_idx < tool_output_idx


@pytest.mark.asyncio
async def test_tool_call_event_payload():
    """Verify 'tool_call' event contains correct data."""
    capture = CaptureCallback()
    agent = ReactAgent(
        tools=[CalculatorTool()],
        callbacks=[capture],
    )
    await agent.run("What is 5 + 3?")

    tool_call_events = [e for e in capture.events if e.event_type == "tool_call"]
    assert len(tool_call_events) > 0

    tc_event = tool_call_events[0]
    assert "tool_name" in tc_event.data
    assert "tool_call_id" in tc_event.data
    assert "arguments" in tc_event.data


@pytest.mark.asyncio
async def test_tool_output_event_payload():
    """Verify 'tool_output' event contains correct data."""
    capture = CaptureCallback()
    agent = ReactAgent(
        tools=[CalculatorTool()],
        callbacks=[capture],
    )
    await agent.run("What is 5 + 3?")

    tool_output_events = [e for e in capture.events if e.event_type == "tool_output"]
    assert len(tool_output_events) > 0

    output_event = tool_output_events[0]
    assert "tool_name" in output_event.data
    assert "tool_call_id" in output_event.data
    assert "output" in output_event.data
    assert "success" in output_event.data


@pytest.mark.asyncio
async def test_turn_complete_event():
    """Verify 'turn_complete' event is emitted at end."""
    capture = CaptureCallback()
    agent = ReactAgent(
        tools=[CalculatorTool()],
        callbacks=[capture],
    )
    await agent.run("What is 5 + 3?")

    assert capture.events[-1].event_type == "turn_complete"


@pytest.mark.asyncio
async def test_turn_complete_event_payload():
    """Verify 'turn_complete' event contains correct data."""
    capture = CaptureCallback()
    agent = ReactAgent(
        tools=[CalculatorTool()],
        callbacks=[capture],
    )
    await agent.run("What is 5 + 3?")

    turn_complete = capture.events[-1]
    assert "final_answer" in turn_complete.data
    assert "completed" in turn_complete.data
    assert "stop_reason" in turn_complete.data
    assert "iterations" in turn_complete.data
    assert "usage" in turn_complete.data
    assert "cost_usd" in turn_complete.data


# ============================================================================ #
# Callback Error Handling Tests
# ============================================================================ #


@pytest.mark.asyncio
async def test_callback_exception_does_not_break_agent():
    """Verify broken callback doesn't stop the agent."""

    class BadCallback(Callback):
        def on_event(self, event: Event) -> None:
            raise RuntimeError("Intentional error")

    agent = ReactAgent(
        tools=[CalculatorTool()],
        callbacks=[BadCallback()],
    )

    # Should not raise
    result = await agent.run("What is 5 + 3?")
    assert result.completed


@pytest.mark.asyncio
async def test_multiple_callbacks_one_fails():
    """Verify other callbacks execute even if one fails."""

    class BadCallback(Callback):
        def on_event(self, event: Event) -> None:
            raise RuntimeError("Intentional error")

    capture = CaptureCallback()

    agent = ReactAgent(
        tools=[CalculatorTool()],
        callbacks=[BadCallback(), capture],
    )

    result = await agent.run("What is 5 + 3?")
    assert result.completed
    assert len(capture.events) > 0


# ============================================================================ #
# Backward Compatibility Tests
# ============================================================================ #


@pytest.mark.asyncio
async def test_stream_parameter_still_works():
    """Verify stream=True parameter still works (backward compatibility)."""
    agent = ReactAgent(tools=[CalculatorTool()])
    result = await agent.run("What is 5 + 3?", stream=True)

    assert result.completed
    # stream=True should auto-add RichLoggingCallback
    assert len(agent.callbacks) > 0


@pytest.mark.asyncio
async def test_stream_parameter_adds_rich_callback():
    """Verify stream=True auto-adds RichLoggingCallback."""
    agent = ReactAgent(tools=[CalculatorTool()])
    assert len(agent.callbacks) == 0

    await agent.run("What is 5 + 3?", stream=True)

    assert len(agent.callbacks) > 0
    assert isinstance(agent.callbacks[0], RichLoggingCallback)


@pytest.mark.asyncio
async def test_no_callbacks_agent_works():
    """Verify agent works fine with no callbacks."""
    agent = ReactAgent(tools=[CalculatorTool()])
    result = await agent.run("What is 5 + 3?")

    assert result.completed
    assert result.stop_reason == "completed"


# ============================================================================ #
# Dynamic Handler Dispatch Tests
# ============================================================================ #


@pytest.mark.asyncio
async def test_dynamic_handler_dispatch():
    """Verify callbacks dispatch to _on_<event_type> methods."""

    class SelectiveCallback(Callback):
        def __init__(self) -> None:
            self.tool_calls_count = 0
            self.tool_outputs_count = 0

        def _on_tool_call(self, data: dict) -> None:
            self.tool_calls_count += 1

        def _on_tool_output(self, data: dict) -> None:
            self.tool_outputs_count += 1

    callback = SelectiveCallback()
    agent = ReactAgent(
        tools=[CalculatorTool()],
        callbacks=[callback],
    )

    await agent.run("What is 5 + 3?")

    assert callback.tool_calls_count > 0
    assert callback.tool_outputs_count > 0


# ============================================================================ #
# Event Data Integrity Tests
# ============================================================================ #


@pytest.mark.asyncio
async def test_tool_call_id_matches_output():
    """Verify tool_call_id in tool_call matches tool_output."""
    capture = CaptureCallback()
    agent = ReactAgent(
        tools=[CalculatorTool()],
        callbacks=[capture],
    )

    await agent.run("What is 5 + 3?")

    tool_calls = [e for e in capture.events if e.event_type == "tool_call"]
    tool_outputs = [e for e in capture.events if e.event_type == "tool_output"]

    if tool_calls and tool_outputs:
        call_id = tool_calls[0].data["tool_call_id"]
        output_id = tool_outputs[0].data["tool_call_id"]
        assert call_id == output_id


@pytest.mark.asyncio
async def test_tool_name_consistency():
    """Verify tool_name is consistent across tool_call and tool_output."""
    capture = CaptureCallback()
    agent = ReactAgent(
        tools=[CalculatorTool()],
        callbacks=[capture],
    )

    await agent.run("What is 5 + 3?")

    tool_calls = [e for e in capture.events if e.event_type == "tool_call"]
    tool_outputs = [e for e in capture.events if e.event_type == "tool_output"]

    if tool_calls and tool_outputs:
        call_name = tool_calls[0].data["tool_name"]
        output_name = tool_outputs[0].data["tool_name"]
        assert call_name == output_name


# ============================================================================ #
# RichLoggingCallback Tests
# ============================================================================ #


@pytest.mark.asyncio
async def test_rich_logging_callback_works():
    """Verify RichLoggingCallback works without errors."""
    agent = ReactAgent(
        tools=[CalculatorTool()],
        callbacks=[RichLoggingCallback()],
    )

    result = await agent.run("What is 5 + 3?")
    assert result.completed


@pytest.mark.asyncio
async def test_rich_logging_callback_truncation():
    """Verify RichLoggingCallback truncates long outputs."""
    callback = RichLoggingCallback(truncate=True, max_result_length=50)

    long_output = "x" * 100
    callback.on_event(
        Event(
            "tool_output",
            {
                "tool_name": "test",
                "tool_call_id": "1",
                "output": long_output,
                "success": True,
            },
        )
    )

    # Should not raise an error
    # (actual truncation happens in _truncated method)
    assert callback._truncated(long_output).startswith("x" * 45)
    assert "[truncated]" in callback._truncated(long_output)


# ============================================================================ #
# Multi-Callback Tests
# ============================================================================ #


@pytest.mark.asyncio
async def test_multiple_callbacks_all_receive_events():
    """Verify multiple callbacks all receive all events."""
    capture1 = CaptureCallback()
    capture2 = CaptureCallback()

    agent = ReactAgent(
        tools=[CalculatorTool()],
        callbacks=[capture1, capture2],
    )

    await agent.run("What is 5 + 3?")

    assert len(capture1.events) > 0
    assert len(capture2.events) > 0
    assert capture1.event_types == capture2.event_types
