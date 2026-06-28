"""Tests for the basic ReAct agent.

These run entirely against a :class:`FakeModel` that mimics the small slice of
``LiteLLMModel`` the agent depends on (``acompletion``, ``record_usage``,
``cumulative``), so no network / API keys are required.
"""

from __future__ import annotations

import io
from types import SimpleNamespace
from typing import Any

import pytest

from diorama.core import (
    CalculatorTool,
    CurrentTimeTool,
    FinalAnswerTool,
    ReactAgent,
    Tool,
    ToolParameter,
    ToolRouter,
)


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
def _tool_call(call_id: str, name: str, arguments: str) -> SimpleNamespace:
    """Build a non-streaming tool-call object shaped like litellm's."""
    return SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def _response(content: str | None = None, tool_calls: list | None = None):
    """Build a non-streaming litellm-style ``ModelResponse`` stand-in."""
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    finish = "tool_calls" if tool_calls else "stop"
    choice = SimpleNamespace(message=message, finish_reason=finish)
    return SimpleNamespace(
        choices=[choice], usage={"prompt_tokens": 1, "completion_tokens": 1}
    )


class FakeModel:
    """Drop-in stand-in for ``LiteLLMModel`` driven by a scripted response list.

    Each item in ``responses`` is returned (in order) from successive ``acompletion``
    calls. If ``loop_last`` is True the final item is reused for any extra calls (handy
    for exercising the max-iteration guard).
    """

    def __init__(self, responses: list[Any], *, loop_last: bool = False) -> None:
        self._responses = list(responses)
        self._loop_last = loop_last
        self.calls: list[dict] = []
        self.cumulative: dict[str, float] = {"cost_usd": 0.0, "total_tokens": 0.0}

    async def acompletion(self, messages, tools=None, stream: bool = False):
        self.calls.append(
            {"messages": list(messages), "tools": tools, "stream": stream}
        )
        if not self._responses:
            raise AssertionError("FakeModel ran out of scripted responses")
        if len(self._responses) == 1 and self._loop_last:
            return self._responses[0]
        return self._responses.pop(0)

    def record_usage(self, usage) -> dict:
        self.cumulative["cost_usd"] += 0.001
        self.cumulative["total_tokens"] += 2
        return {"total_tokens": 2, "cost_usd": 0.001}


def _agent(model: FakeModel, tools=None, **kwargs) -> ReactAgent:
    return ReactAgent(tools if tools is not None else [], model=model, **kwargs)


# --------------------------------------------------------------------------- #
# Core loop
# --------------------------------------------------------------------------- #
async def test_single_turn_no_tools_terminates():
    model = FakeModel([_response(content="Hello there.")])
    result = await _agent(model).run("hi")

    assert result["final_answer"] == "Hello there."
    assert result["completed"] is True
    assert result["stop_reason"] == "completed"
    assert result["steps"] == 1


async def test_tool_call_then_final_answer():
    model = FakeModel(
        [
            _response(
                tool_calls=[
                    _tool_call("c1", "calculator", '{"expression": "2 + 2 * 3"}')
                ]
            ),
            _response(content="The answer is 8."),
        ]
    )
    result = await _agent(model, [CalculatorTool(), FinalAnswerTool()]).run(
        "compute 2+2*3"
    )

    assert result["final_answer"] == "The answer is 8."
    assert result["steps"] == 2
    # The calculator's result was fed back as a role:tool message.
    tool_msgs = [m for m in result["messages"] if m["role"] == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["content"] == "8"
    assert tool_msgs[0]["tool_call_id"] == "c1"
    assert tool_msgs[0]["name"] == "calculator"


async def test_assistant_tool_call_message_shape():
    model = FakeModel(
        [
            _response(
                tool_calls=[_tool_call("c1", "calculator", '{"expression": "1+1"}')]
            ),
            _response(content="done"),
        ]
    )
    result = await _agent(model, [CalculatorTool()]).run("go")
    assistant_with_tools = [
        m
        for m in result["messages"]
        if m["role"] == "assistant" and m.get("tool_calls")
    ]
    assert len(assistant_with_tools) == 1
    tc = assistant_with_tools[0]["tool_calls"][0]
    assert tc["id"] == "c1"
    assert tc["function"]["name"] == "calculator"


async def test_max_iterations_guard():
    # Model always wants another tool call → loop must stop at max_iterations.
    looping = _response(
        tool_calls=[_tool_call("c1", "calculator", '{"expression": "1+1"}')]
    )
    model = FakeModel([looping], loop_last=True)
    result = await _agent(model, [CalculatorTool()], max_iterations=3).run("loop")

    assert result["completed"] is False
    assert result["stop_reason"] == "max_iterations"
    assert result["steps"] == 3


async def test_malformed_json_arguments_surface_to_model():
    model = FakeModel(
        [
            _response(tool_calls=[_tool_call("c1", "calculator", "{not json}")]),
            _response(content="recovered"),
        ]
    )
    result = await _agent(model, [CalculatorTool()]).run("go")
    tool_msgs = [m for m in result["messages"] if m["role"] == "tool"]
    assert "valid JSON" in tool_msgs[0]["content"]
    assert result["final_answer"] == "recovered"


async def test_usage_and_cost_accumulate():
    model = FakeModel(
        [
            _response(
                tool_calls=[_tool_call("c1", "calculator", '{"expression": "1+1"}')]
            ),
            _response(content="2"),
        ]
    )
    result = await _agent(model, [CalculatorTool()]).run("go")
    # Two LLM calls were recorded.
    assert result["usage"]["total_tokens"] == 4
    assert result["cost_usd"] == pytest.approx(0.002)


# --------------------------------------------------------------------------- #
# Approval
# --------------------------------------------------------------------------- #
class _ApprovalTool(Tool):
    tool_name: str = "danger"
    description: str = "A tool that requires approval."
    parameters: list[ToolParameter] = []
    requires_approval: bool = True

    async def forward(self) -> Any:  # noqa: D102
        return "executed"


async def test_tool_requiring_approval_is_skipped_when_rejected():
    model = FakeModel(
        [
            _response(tool_calls=[_tool_call("c1", "danger", "{}")]),
            _response(content="ok"),
        ]
    )
    agent = _agent(
        model,
        [_ApprovalTool()],
        yolo_mode=False,
        approval_callback=lambda name, args: False,
    )
    result = await agent.run("go")
    tool_msgs = [m for m in result["messages"] if m["role"] == "tool"]
    assert "not approved" in tool_msgs[0]["content"]


async def test_tool_requiring_approval_runs_when_approved():
    model = FakeModel(
        [
            _response(tool_calls=[_tool_call("c1", "danger", "{}")]),
            _response(content="ok"),
        ]
    )
    agent = _agent(
        model,
        [_ApprovalTool()],
        yolo_mode=False,
        approval_callback=lambda name, args: True,
    )
    result = await agent.run("go")
    tool_msgs = [m for m in result["messages"] if m["role"] == "tool"]
    assert tool_msgs[0]["content"] == "executed"


async def test_yolo_mode_auto_approves():
    model = FakeModel(
        [
            _response(tool_calls=[_tool_call("c1", "danger", "{}")]),
            _response(content="ok"),
        ]
    )
    result = await _agent(model, [_ApprovalTool()], yolo_mode=True).run("go")
    tool_msgs = [m for m in result["messages"] if m["role"] == "tool"]
    assert tool_msgs[0]["content"] == "executed"


# --------------------------------------------------------------------------- #
# Streaming
# --------------------------------------------------------------------------- #
def _chunk(content: str | None = None, finish_reason: str | None = None, usage=None):
    delta = SimpleNamespace(content=content, tool_calls=None)
    choice = SimpleNamespace(delta=delta, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], usage=usage)


class _StreamModel(FakeModel):
    async def acompletion(self, messages, tools=None, stream: bool = False):
        self.calls.append({"stream": stream})
        assert stream is True

        async def gen():
            yield _chunk(content="Hel")
            yield _chunk(content="lo")
            yield _chunk(finish_reason="stop", usage={"prompt_tokens": 1})

        return gen()


async def test_streaming_accumulates_content():
    from rich.console import Console

    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False)
    model = _StreamModel([])
    result = await _agent(model).run("hi", stream=True, console=console)

    assert result["final_answer"] == "Hello"
    assert result["completed"] is True
    assert "Hello" in buffer.getvalue()


# --------------------------------------------------------------------------- #
# Tool framework / router unit tests
# --------------------------------------------------------------------------- #
def test_tool_json_schema_shape():
    schema = CalculatorTool().to_json_schema()
    assert schema["type"] == "function"
    fn = schema["function"]
    assert fn["name"] == "calculator"
    assert fn["parameters"]["properties"]["expression"]["type"] == "string"
    assert fn["parameters"]["required"] == ["expression"]


async def test_router_unknown_tool_returns_error():
    router = ToolRouter([CalculatorTool()])
    out, success = await router.call_tool("nope", {})
    assert success is False
    assert "Unknown tool" in out


async def test_router_catches_tool_exception():
    router = ToolRouter([CalculatorTool()])
    out, success = await router.call_tool("calculator", {"expression": "1/0"})
    assert success is False
    assert "error" in out.lower()


async def test_calculator_tool_direct():
    assert await CalculatorTool().forward(expression="2 * (3 + 4)") == 14


async def test_calculator_rejects_unsafe_expression():
    with pytest.raises(ValueError):
        await CalculatorTool().forward(expression="__import__('os').system('echo hi')")


async def test_current_time_tool_returns_iso_string():
    value = await CurrentTimeTool().forward()
    assert isinstance(value, str)
    # ISO-8601 timestamps contain a date/time separator.
    assert "T" in value
