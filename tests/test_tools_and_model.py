"""Tests for the tool layer (rich results, hooks, progress, dynamic tools) and the
model layer (reasoning round-trip, finish reasons, retry classification)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from diorama.core import (
    CalculatorTool,
    ImageBlock,
    ReactAgent,
    TextBlock,
    Tool,
    ToolParameter,
    ToolResult,
    ToolRouter,
)
from diorama.core.context import IMAGE_TOKENS, estimate_message_tokens
from diorama.core.react import _is_transient_error
from tests.fakes import (
    FakeModel,
    FlakyModel,
    ScriptedStreamModel,
    chunk,
    response,
    stream_of,
    tool_call,
)

PNG = "iVBORw0KGgoAAAANSUhEUg=="


def _agent(model: Any, tools=None, **kwargs) -> ReactAgent:
    kwargs.setdefault("auto_compact", False)
    return ReactAgent(tools if tools is not None else [], model=model, **kwargs)


# --------------------------------------------------------------------------- #
# Tools used below
# --------------------------------------------------------------------------- #
class RichTool(Tool):
    """Returns a full ToolResult with structured details."""

    tool_name: str = "rich"
    description: str = "Return a rich result."
    parameters: list[ToolParameter] = []

    async def forward(self) -> Any:  # noqa: D102
        return ToolResult(
            content=[TextBlock(text="visible output")],
            details={"rows": 3, "source": "db"},
        )


class FailingTool(Tool):
    """Reports failure without raising."""

    tool_name: str = "flaky"
    description: str = "Fail on purpose."
    parameters: list[ToolParameter] = []

    async def forward(self) -> Any:  # noqa: D102
        return ToolResult.error("could not reach the service")


class ScreenshotTool(Tool):
    """Returns an image alongside text."""

    tool_name: str = "screenshot"
    description: str = "Return an image."
    parameters: list[ToolParameter] = []

    async def forward(self) -> Any:  # noqa: D102
        return ToolResult(
            content=[
                TextBlock(text="captured"),
                ImageBlock(data=PNG, mime_type="image/png"),
            ]
        )


class ImageOnlyTool(Tool):
    tool_name: str = "image_only"
    description: str = "Return only an image."
    parameters: list[ToolParameter] = []

    async def forward(self) -> Any:  # noqa: D102
        return ToolResult(content=[ImageBlock(data=PNG)])


class DiscoveryTool(Tool):
    """Unlocks a deferred tool."""

    tool_name: str = "discover"
    description: str = "Unlock the calculator."
    parameters: list[ToolParameter] = []

    async def forward(self) -> Any:  # noqa: D102
        return ToolResult.from_text(
            "calculator is now available", added_tool_names=["calculator"]
        )


class TerminatingTool(Tool):
    """Ends the run itself."""

    tool_name: str = "submit"
    description: str = "Submit the answer and stop."
    parameters: list[ToolParameter] = []

    async def forward(self) -> Any:  # noqa: D102
        return ToolResult.from_text("submitted: 42", terminate=True)


class ProgressTool(Tool):
    """Reports progress, then finishes."""

    tool_name: str = "progress"
    description: str = "Report progress while working."
    parameters: list[ToolParameter] = []

    async def forward(self, on_update) -> Any:  # noqa: D102
        for step in ("step 1", "step 2"):
            on_update(ToolResult.from_text(step))
            await asyncio.sleep(0)
        return "finished"


def _call(name: str, call_id: str = "c1", args: str = "{}"):
    return tool_call(call_id, name, args)


# --------------------------------------------------------------------------- #
# ToolResult basics
# --------------------------------------------------------------------------- #
def test_coerce_wraps_plain_values():
    assert ToolResult.coerce("hi").text == "hi"
    assert ToolResult.coerce(42).text == "42"
    assert ToolResult.coerce({"a": 1}).text == '{"a": 1}'
    assert ToolResult.coerce(TextBlock(text="block")).text == "block"
    assert ToolResult.coerce(ToolResult.error("x")).is_error is True


def test_image_only_result_still_produces_model_text():
    result = ToolResult(content=[ImageBlock(data=PNG)])
    assert result.model_text() == "[image]"
    assert result.text == ""


def test_image_tokens_are_flat_not_base64_length():
    message = {
        "role": "user",
        "content": [
            {"type": "text", "text": "look"},
            {
                "type": "image_url",
                "image_url": {"url": "data:image/png;base64," + "A" * 50_000},
            },
        ],
    }
    tokens = estimate_message_tokens(message)
    assert IMAGE_TOKENS <= tokens < IMAGE_TOKENS + 100


# --------------------------------------------------------------------------- #
# Rich results in the loop
# --------------------------------------------------------------------------- #
async def test_details_reach_events_but_not_the_model():
    model = FakeModel([response(tool_calls=[_call("rich")]), response(content="ok")])
    agent = _agent(model, [RichTool()])
    seen: list[Any] = []
    agent.subscribe(seen.append)

    await agent.run("go")

    end = next(e for e in seen if e.type == "tool_execution_end")
    assert end.details == {"rows": 3, "source": "db"}
    tool_msg = next(m for m in agent.messages if m["role"] == "tool")
    assert tool_msg["content"] == "visible output"
    assert "details" not in tool_msg


async def test_tool_error_is_recorded_in_history_and_stripped_before_sending():
    model = FakeModel([response(tool_calls=[_call("flaky")]), response(content="ok")])
    agent = _agent(model, [FailingTool()])

    await agent.run("go")

    tool_msg = next(m for m in agent.messages if m["role"] == "tool")
    assert tool_msg["is_error"] is True
    # The provider never sees the flag — Chat Completions has no field for it.
    sent = model.calls[-1]["messages"]
    assert all("is_error" not in m for m in sent)
    assert any(m.get("content") == "could not reach the service" for m in sent)


async def test_tool_images_are_attached_as_a_follow_up_user_message():
    model = FakeModel(
        [response(tool_calls=[_call("screenshot")]), response(content="I see it")]
    )
    agent = _agent(model, [ScreenshotTool()])

    await agent.run("go")

    tool_msg = next(m for m in agent.messages if m["role"] == "tool")
    assert tool_msg["content"] == "captured"
    followup = agent.messages[agent.messages.index(tool_msg) + 1]
    assert followup["role"] == "user"
    parts = followup["content"]
    assert parts[1]["type"] == "image_url"
    assert parts[1]["image_url"]["url"] == f"data:image/png;base64,{PNG}"


async def test_image_only_tool_still_answers_its_tool_call():
    model = FakeModel(
        [response(tool_calls=[_call("image_only")]), response(content="ok")]
    )
    agent = _agent(model, [ImageOnlyTool()])

    await agent.run("go")

    tool_msg = next(m for m in agent.messages if m["role"] == "tool")
    assert tool_msg["content"]  # never empty — providers reject empty tool results
    assert tool_msg["tool_call_id"] == "c1"


# --------------------------------------------------------------------------- #
# Deferred tools
# --------------------------------------------------------------------------- #
def test_deferred_tools_are_registered_but_hidden():
    router = ToolRouter([RichTool()], [CalculatorTool()])
    names = [s["function"]["name"] for s in router.get_tool_specs_for_llm()]

    assert names == ["rich"]
    assert router.get("calculator") is not None  # registered, just not exposed


async def test_a_tool_can_unlock_deferred_tools():
    model = FakeModel(
        [response(tool_calls=[_call("discover")]), response(content="ok")]
    )
    agent = _agent(model, [DiscoveryTool()], deferred_tools=[CalculatorTool()])
    seen: list[Any] = []
    agent.subscribe(seen.append)

    first_specs = [s["function"]["name"] for s in (agent._tool_specs() or [])]
    await agent.run("go")

    assert first_specs == ["discover"]
    end = next(e for e in seen if e.type == "tool_execution_end")
    assert end.added_tool_names == ["calculator"]
    # The very next request carried the newly unlocked schema.
    sent_tools = [t["function"]["name"] for t in model.calls[-1]["tools"]]
    assert sorted(sent_tools) == ["calculator", "discover"]


async def test_activating_an_unknown_tool_is_ignored():
    router = ToolRouter([RichTool()])
    assert router.activate("nope") == []


# --------------------------------------------------------------------------- #
# terminate
# --------------------------------------------------------------------------- #
async def test_tool_can_terminate_the_run():
    model = FakeModel([response(tool_calls=[_call("submit")])], loop_last=True)
    agent = _agent(model, [TerminatingTool()])

    result = await agent.run("go")

    assert result.stop_reason == "tool_terminated"
    assert result.completed is True
    assert result.final_answer == "submitted: 42"
    assert result.steps == 1


# --------------------------------------------------------------------------- #
# Progress streaming
# --------------------------------------------------------------------------- #
async def test_tool_progress_is_emitted_before_the_tool_finishes():
    model = FakeModel(
        [response(tool_calls=[_call("progress")]), response(content="ok")]
    )
    agent = _agent(model, [ProgressTool()])
    seen: list[Any] = []
    agent.subscribe(seen.append)

    await agent.run("go")

    types = [e.type for e in seen]
    updates = [e for e in seen if e.type == "tool_execution_update"]
    assert [u.output for u in updates] == ["step 1", "step 2"]
    # Updates land between start and end, not batched after the tool returned.
    assert (
        types.index("tool_execution_start")
        < types.index("tool_execution_update")
        < types.index("tool_execution_end")
    )
    assert (
        next(m for m in agent.messages if m["role"] == "tool")["content"] == "finished"
    )


async def test_progress_reaches_subscribers_while_the_tool_is_still_running():
    """The tool cannot finish until a subscriber has *seen* its progress report.

    If updates were buffered until the tool returned, the gate would never open and
    the tool would time out — so this deadlocks-by-design under a batching
    implementation and only passes when delivery is genuinely live.
    """
    gate = asyncio.Event()

    class GatedTool(Tool):
        tool_name: str = "gated"
        description: str = "Wait for its own progress to be observed."
        parameters: list[ToolParameter] = []

        async def forward(self, on_update) -> Any:
            on_update("working")
            await asyncio.wait_for(gate.wait(), timeout=2)
            return "done"

    model = FakeModel([response(tool_calls=[_call("gated")]), response(content="ok")])
    agent = _agent(model, [GatedTool()])
    agent.subscribe(lambda e: gate.set() if e.type == "tool_execution_update" else None)

    await asyncio.wait_for(agent.run("go"), timeout=5)

    assert next(m for m in agent.messages if m["role"] == "tool")["content"] == "done"


# --------------------------------------------------------------------------- #
# before/after hooks
# --------------------------------------------------------------------------- #
async def test_before_hook_blocks_with_a_reason_the_model_can_read():
    calls: list[str] = []

    async def gate(invocation):
        calls.append(invocation.name)
        return True, "calculator is disabled in this environment"

    model = FakeModel(
        [
            response(tool_calls=[_call("calculator", args='{"expression":"1+1"}')]),
            response(content="understood"),
        ]
    )
    agent = _agent(model, [CalculatorTool()], before_tool_call=gate)

    await agent.run("go")

    assert calls == ["calculator"]
    tool_msg = next(m for m in agent.messages if m["role"] == "tool")
    assert tool_msg["content"] == "calculator is disabled in this environment"
    assert tool_msg["is_error"] is True


async def test_before_hook_may_be_sync_and_receives_arguments():
    seen: list[dict] = []
    model = FakeModel(
        [
            response(tool_calls=[_call("calculator", args='{"expression":"2+2"}')]),
            response(content="ok"),
        ]
    )

    def gate(invocation):
        seen.append(invocation.arguments)
        return False, None

    agent = _agent(model, [CalculatorTool()], before_tool_call=gate)
    await agent.run("go")

    assert seen == [{"expression": "2+2"}]
    assert next(m for m in agent.messages if m["role"] == "tool")["content"] == "4"


async def test_after_hook_can_rewrite_the_result():
    async def redact(invocation, result):
        return ToolResult.from_text(result.text.replace("4", "[redacted]"))

    model = FakeModel(
        [
            response(tool_calls=[_call("calculator", args='{"expression":"2+2"}')]),
            response(content="ok"),
        ]
    )
    agent = _agent(model, [CalculatorTool()], after_tool_call=redact)
    await agent.run("go")

    assert (
        next(m for m in agent.messages if m["role"] == "tool")["content"]
        == "[redacted]"
    )


async def test_after_hook_also_sees_blocked_calls():
    seen: list[bool] = []

    def gate(invocation):
        return True, "nope"

    def observe(invocation, result):
        seen.append(result.is_error)
        return result

    model = FakeModel(
        [response(tool_calls=[_call("calculator")]), response(content="ok")]
    )
    agent = _agent(
        model, [CalculatorTool()], before_tool_call=gate, after_tool_call=observe
    )
    await agent.run("go")

    assert seen == [True]


# --------------------------------------------------------------------------- #
# Reasoning round-trip
# --------------------------------------------------------------------------- #
BLOCKS = [{"type": "thinking", "thinking": "let me think", "signature": "sig-abc"}]


async def test_reasoning_is_kept_in_history_and_signed_blocks_are_replayed():
    model = FakeModel(
        [
            response(
                content="a", reasoning_content="thinking...", thinking_blocks=BLOCKS
            ),
            response(content="b"),
        ]
    )
    agent = _agent(model)

    await agent.run("one")
    await agent.run("two")

    assistant = next(m for m in agent.messages if m["role"] == "assistant")
    assert assistant["reasoning_content"] == "thinking..."
    assert assistant["thinking_blocks"] == BLOCKS

    sent = model.calls[-1]["messages"]
    replayed = next(m for m in sent if m["role"] == "assistant")
    # Signed blocks go back (Anthropic requires it); the plain text does not.
    assert replayed["thinking_blocks"] == BLOCKS
    assert "reasoning_content" not in replayed


def test_thinking_blocks_are_found_in_provider_specific_fields():
    from types import SimpleNamespace

    from diorama.models.litellm_model import extract_reasoning

    message = SimpleNamespace(
        reasoning_content="thought",
        thinking_blocks=None,
        provider_specific_fields={"thinking_blocks": BLOCKS},
    )
    reasoning, blocks = extract_reasoning(message)

    assert reasoning == "thought"
    assert blocks == BLOCKS


async def test_preserve_reasoning_false_drops_everything():
    model = FakeModel(
        [
            response(
                content="a", reasoning_content="thinking...", thinking_blocks=BLOCKS
            ),
            response(content="b"),
        ]
    )
    agent = _agent(model, preserve_reasoning=False)

    await agent.run("one")
    await agent.run("two")

    assistant = next(m for m in agent.messages if m["role"] == "assistant")
    assert "thinking_blocks" not in assistant
    assert "reasoning_content" not in assistant


async def test_thinking_only_assistant_turn_is_not_filtered_out():
    agent = _agent(FakeModel([response(content="next")]))
    agent.messages.append(
        {"role": "assistant", "content": None, "thinking_blocks": BLOCKS}
    )

    await agent.run("go")

    sent = agent.model.calls[-1]["messages"]
    assert any(m.get("thinking_blocks") == BLOCKS for m in sent)


async def test_streamed_reasoning_arrives_as_its_own_delta():
    model = ScriptedStreamModel(
        [
            stream_of(
                [
                    chunk(reasoning_content="think "),
                    chunk(reasoning_content="harder"),
                    chunk(content="Answer"),
                    chunk(finish_reason="stop", usage={"prompt_tokens": 1}),
                ]
            )
        ]
    )
    agent = _agent(model)
    seen: list[Any] = []
    agent.subscribe(seen.append)

    result = await agent.run("go", stream=True)

    updates = [e for e in seen if e.type == "message_update"]
    assert "".join(u.reasoning_delta for u in updates) == "think harder"
    assert "".join(u.delta for u in updates) == "Answer"
    assert result.final_answer == "Answer"
    assistant = next(m for m in agent.messages if m["role"] == "assistant")
    assert assistant["reasoning_content"] == "think harder"


# --------------------------------------------------------------------------- #
# Finish reasons
# --------------------------------------------------------------------------- #
async def test_content_filter_stops_the_run():
    model = FakeModel([response(content="par", finish_reason="content_filter")])
    result = await _agent(model).run("go")

    assert result.stop_reason == "content_filter"
    assert result.completed is False


async def test_truncated_tool_calls_are_not_executed():
    model = FakeModel(
        [
            response(
                tool_calls=[_call("calculator", args='{"expression": "1+')],
                finish_reason="length",
            )
        ],
        loop_last=True,
    )
    agent = _agent(model, [CalculatorTool()])

    result = await agent.run("go")

    assert result.stop_reason == "length"
    assert result.completed is False
    # The half-written call was never run, so no tool message exists for it.
    assert not [m for m in agent.messages if m["role"] == "tool"]


# --------------------------------------------------------------------------- #
# Retry
# --------------------------------------------------------------------------- #
def test_transient_classification_uses_status_codes():
    class Err(Exception):
        def __init__(self, status):
            super().__init__("boom")
            self.status_code = status

    assert _is_transient_error(Err(503)) is True
    assert _is_transient_error(Err(500)) is True
    assert _is_transient_error(Err(429)) is True
    # Client errors are permanent: retrying a bad request just burns quota.
    assert _is_transient_error(Err(400)) is False
    assert _is_transient_error(Err(401)) is False
    assert _is_transient_error(Err(404)) is False


def test_message_mentioning_a_code_does_not_override_the_status():
    class Err(Exception):
        def __init__(self):
            super().__init__("model returned 500 tokens; invalid request")
            self.status_code = 400

    assert _is_transient_error(Err()) is False


@pytest.fixture
def instant_backoff(monkeypatch):
    """Make retry backoff instant so tests do not sleep for real."""

    async def no_sleep(delay, signal):
        return signal is None or not signal.is_cancelled()

    monkeypatch.setattr("diorama.core.react._sleep_unless_cancelled", no_sleep)


async def test_transient_failure_is_retried_and_reported(instant_backoff):
    model = FlakyModel(
        [response(content="recovered")], failures=1, error=TimeoutError("timed out")
    )
    agent = _agent(model)
    seen: list[Any] = []
    agent.subscribe(seen.append)

    result = await agent.run("go")

    assert result.final_answer == "recovered"
    assert model.attempts == 2
    retries = [e for e in seen if e.type == "retry"]
    assert len(retries) == 1
    assert retries[0].attempt == 2


async def test_stream_failing_before_any_output_is_retried(instant_backoff):
    model = ScriptedStreamModel(
        [
            stream_of([chunk(content="partial")], fail_after=0),
            stream_of(
                [chunk(content="second try"), chunk(finish_reason="stop", usage=None)]
            ),
        ]
    )
    agent = _agent(model)
    seen: list[Any] = []
    agent.subscribe(seen.append)

    result = await agent.run("go", stream=True)

    assert result.final_answer == "second try"
    assert len([e for e in seen if e.type == "retry"]) == 1


async def test_stream_failing_mid_output_is_not_retried(instant_backoff):
    model = ScriptedStreamModel(
        [
            stream_of([chunk(content="half an ans")], fail_after=1),
            stream_of([chunk(content="never reached")]),
        ]
    )
    agent = _agent(model)

    with pytest.raises(TimeoutError):
        await agent.run("go", stream=True)
    # Only one attempt: replaying would duplicate the deltas already emitted.
    assert len(model.calls) == 1


async def test_permanent_error_is_not_retried(instant_backoff):
    class BadRequest(Exception):
        status_code = 400

    model = FlakyModel([response(content="never")], failures=1, error=BadRequest("bad"))
    agent = _agent(model)

    with pytest.raises(BadRequest):
        await agent.run("go")
    assert model.attempts == 1
