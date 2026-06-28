# Callback System — Implementation Guide

This document provides detailed pseudo-code, code snippets, and step-by-step guidance for implementing the callback system in Diorama.

---

## 1. File Structure

Create one new file and modify one existing file:

```
diorama/
├── core/
│   ├── __init__.py          (MODIFY: export Callback, Event, RichLoggingCallback)
│   ├── react.py             (MODIFY: integrate callbacks)
│   └── callback.py           (CREATE: Event, Callback, RichLoggingCallback)
└── ...
```

---

## 2. Create `diorama/core/callback.py`

### 2.1 Event Dataclass

```python
"""Callbacks — observe agent execution via event stream.

The agent emits Event objects at key points in execution (LLM calls, tool
execution, approvals, errors). Callbacks subscribe to these events to log,
monitor, render UI, or perform other side effects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Event:
    """A single event emitted by the agent loop for callbacks to consume.

    Attributes:
        event_type (str): Open-ended event identifier (e.g., "tool_call",
            "assistant_chunk"). Callbacks can observe any subset of events.
        data (dict[str, Any]): Event payload, whose schema depends on
            ``event_type``. See the event reference for each type's schema.
        seq (int | None): Optional monotonic sequence number for event ordering
            in distributed scenarios. Starts at 1 and increments per event.
    """

    event_type: str
    data: dict[str, Any] = field(default_factory=dict)
    seq: int | None = None
```

### 2.2 Base Callback Class

```python
class Callback:
    """Base class for agent event callbacks.

    Override :meth:`on_event` (or dynamic ``_on_<event_type>`` methods) to
    observe and react to agent execution events. Callbacks are invoked
    synchronously; keep them non-blocking.

    Callbacks that raise exceptions are caught and logged; they do not
    interrupt the agent loop.
    """

    def on_event(self, event: Event) -> None:
        """Handle a single event emitted by the agent.

        The default implementation uses dynamic dispatch: it looks for a method
        named ``_on_<event_type>`` and calls it if found. Otherwise, the event
        is ignored.

        Subclasses can override this method directly for custom logic, or
        define ``_on_<event_type>`` methods for specific events.

        Args:
            event (Event): The event to handle.
        """
        handler = getattr(self, f"_on_{event.event_type}", None)
        if handler is not None:
            handler(event.data or {})
```

### 2.3 RichLoggingCallback

```python
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.syntax import Syntax
import json


class RichLoggingCallback(Callback):
    """Rich console renderer for the agent's event stream.

    Renders agent events to a Rich Console with colors, panels, and syntax
    highlighting. Modeled after Coursify's CoursifyLoggingCallback.

    Attributes:
        truncate (bool): When True, long tool outputs are clipped to
            ``max_result_length`` characters before display.
        max_result_length (int): Maximum characters shown for a tool output
            when ``truncate`` is True.
    """

    def __init__(
        self,
        truncate: bool = False,
        max_result_length: int = 1000,
        console: Console | None = None,
    ) -> None:
        """Initialise the logging callback.

        Args:
            truncate (bool): Clip tool outputs to ``max_result_length``
                characters. Defaults to False.
            max_result_length (int): Maximum characters shown per tool output
                when ``truncate`` is True. Defaults to 1000.
            console (Console | None): Optional Rich Console. A new one is
                created if omitted.
        """
        self._console = console or Console()
        self.truncate = truncate
        self.max_result_length = max_result_length
        self._streaming = False

    def _truncated(self, text: str) -> str:
        """Return ``text`` clipped to ``max_result_length`` when enabled.

        Args:
            text (str): The raw text to optionally clip.

        Returns:
            str: The original text, or the first ``max_result_length``
                characters followed by "\\n... [truncated]" when truncation
                is enabled and the text exceeds the limit.
        """
        if self.truncate and len(text) > self.max_result_length:
            return text[: self.max_result_length] + "\n... [truncated]"
        return text

    # ---- Event Handlers ----

    def _on_ready(self, data: dict[str, Any]) -> None:
        """Render the ``ready`` event showing the number of registered tools."""
        tool_count = data.get("tool_count", 0)
        self._console.print(
            f"[dim]Agent ready with {tool_count} tools.[/dim]"
        )

    def _on_processing(self, data: dict[str, Any]) -> None:
        """Render the ``processing`` event as a turn-separator rule."""
        iteration = data.get("iteration", 0)
        self._console.print(
            Rule(
                title=f"[bold]Turn {iteration}[/bold]",
                align="right",
                style="yellow",
            )
        )

    def _on_assistant_chunk(self, data: dict[str, Any]) -> None:
        """Render a streaming ``assistant_chunk`` token to the console."""
        if not self._streaming:
            self._console.print("[bold cyan]Reasoning:[/bold cyan] ", end="")
            self._streaming = True
        content = data.get("content", "")
        self._console.print(content, end="", markup=False, highlight=False)

    def _on_assistant_stream_end(self, data: dict[str, Any]) -> None:
        """Finalise an in-progress streaming block."""
        if self._streaming:
            self._console.print()
            self._streaming = False

    def _on_assistant_message(self, data: dict[str, Any]) -> None:
        """Render a complete ``assistant_message`` in a cyan Markdown panel."""
        content = data.get("content", "")
        if content:
            self._console.print(
                Panel(
                    Markdown(content),
                    title="[bold cyan]Reasoning[/bold cyan]",
                    title_align="left",
                    border_style="cyan",
                )
            )

    def _on_tool_call(self, data: dict[str, Any]) -> None:
        """Render a ``tool_call`` event showing the tool and arguments."""
        tool_name = data.get("tool_name", "?")
        arguments = data.get("arguments", {})
        args_json = json.dumps(arguments, indent=2)
        self._console.print(
            Panel(
                Syntax(args_json, "json", theme="monokai", word_wrap=True),
                title=f"[bold green]Tool Call: {tool_name}[/bold green]",
                title_align="left",
                border_style="green",
            )
        )

    def _on_tool_output(self, data: dict[str, Any]) -> None:
        """Render a ``tool_output`` event (success or failure)."""
        tool_name = data.get("tool_name", "?")
        output = self._truncated(str(data.get("output", "")))
        success = data.get("success", False)
        color = "magenta" if success else "red"
        self._console.print(
            Panel(
                output,
                title=f"[bold {color}]Tool Result: {tool_name}[/bold {color}]",
                title_align="left",
                border_style=color,
            )
        )

    def _on_approval_required(self, data: dict[str, Any]) -> None:
        """Render an ``approval_required`` notice."""
        tool_name = data.get("tool_name", "?")
        self._console.print(
            Panel(
                f"Approval required for: {tool_name}",
                title="[bold yellow]Approval[/bold yellow]",
                border_style="yellow",
            )
        )

    def _on_error(self, data: dict[str, Any]) -> None:
        """Render an ``error`` event in a red panel."""
        error_type = data.get("error_type", "unknown")
        error_msg = data.get("error", "")
        self._console.print(
            Panel(
                f"[{error_type}] {error_msg}",
                title="[bold red]Error[/bold red]",
                border_style="red",
            )
        )

    def _on_turn_complete(self, data: dict[str, Any]) -> None:
        """Render the ``turn_complete`` event with the final answer."""
        final_answer = data.get("final_answer", "")
        cost = data.get("cost_usd", 0.0)
        if final_answer:
            self._console.print(
                Panel(
                    Markdown(str(final_answer)),
                    title="[bold yellow]Final Answer[/bold yellow]",
                    border_style="yellow",
                )
            )
        if cost > 0:
            self._console.print(f"[dim]Cost: ${cost:.4f}[/dim]")
```

---

## 3. Modify `diorama/core/react.py`

### 3.1 Add Imports

Add these to the top of `react.py`:

```python
from diorama.core.callback import Callback, Event, RichLoggingCallback
```

### 3.2 Add Attributes to `__init__`

Modify the `ReactAgent.__init__` method:

```python
def __init__(
    self,
    tools: list[Tool],
    *,
    model: LiteLLMModel | None = None,
    model_id: str = "openrouter/openai/gpt-4o-mini",
    system_prompt: str = SYSTEM_PROMPT,
    instructions: str | None = None,
    temperature: float = 0.7,
    max_tokens: int | None = None,
    max_iterations: int = 25,
    yolo_mode: bool = True,
    enable_prompt_caching: bool = True,
    approval_callback: Callable[[str, dict], bool] | None = None,
    weave_project: str | None = None,
    callbacks: list[Callback] | None = None,  # NEW
) -> None:
    """Initialise the agent with its tool set and configuration.

    Args:
        ... (existing docstring)
        callbacks (list[Callback] | None): Optional callbacks to observe
            agent execution events. Defaults to None.
    """
    # ... existing initialization ...
    self.callbacks: list[Callback] = callbacks or []
```

### 3.3 Add `_emit` Helper Method

Add this method to the `ReactAgent` class (after `__init__`):

```python
def _emit(self, event_type: str, data: dict[str, Any] | None = None) -> None:
    """Emit an event to all registered callbacks.

    Args:
        event_type (str): The event type identifier.
        data (dict[str, Any] | None): Optional event payload. Defaults to {}.
    """
    event = Event(event_type, data or {})
    for callback in self.callbacks:
        try:
            callback.on_event(event)
        except Exception as e:  # noqa: BLE001
            logger.warning("Callback error on %s: %s", event_type, e)
```

### 3.4 Emit Events in `run()` Method

Modify the `run()` method to emit events:

```python
async def run(
    self,
    prompt: str,
    *,
    stream: bool = False,
    auto_approve: bool | None = None,
    console: Any = None,
) -> ReactAgentResult:
    """Run one task to completion and return a result dict.

    Args:
        prompt (str): The task/question for the agent.
        stream (bool): When True, print assistant text deltas and tool
            activity to a Rich console. Deprecated in favor of
            callbacks=[RichLoggingCallback()]. Defaults to False.
        auto_approve (bool | None): Override ``yolo_mode`` for this run.
        console (Any): Optional Rich Console for streaming output.

    Returns:
        ReactAgentResult: Execution result.
    """
    # Auto-add RichLoggingCallback if stream=True and no callbacks registered
    if stream and not self.callbacks:
        if console is None:
            from rich.console import Console
            console = Console()
        self.callbacks = [RichLoggingCallback(console=console)]

    # Emit ready event
    self._emit("ready", {"tool_count": len(self.tools)})

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": self.system_prompt},
        {"role": "user", "content": prompt},
    ]
    tool_specs = self.tool_router.get_tool_specs_for_llm() or None

    final_answer: str | None = None
    completed = False
    stop_reason = "max_iterations"
    iteration = 0

    try:
        while self.max_iterations == -1 or iteration < self.max_iterations:
            iteration += 1

            # Emit processing event
            self._emit("processing", {"iteration": iteration})

            result = await self._call_llm(
                messages, tool_specs, stream=bool(self.callbacks), console
            )
            tool_calls = [
                result.tool_calls_acc[idx] for idx in sorted(result.tool_calls_acc)
            ]
            messages.append(_assistant_message(result.content, tool_calls or None))

            # Emit assistant_message event
            self._emit(
                "assistant_message",
                {
                    "content": result.content,
                    "finish_reason": result.finish_reason,
                },
            )

            if not tool_calls:
                final_answer = result.content
                completed = True
                stop_reason = "completed"
                break

            await self._run_tool_calls(
                tool_calls, messages, auto_approve, stream, console
            )

    except Exception as e:
        self._emit(
            "error",
            {
                "error_type": "agent_loop",
                "error": str(e),
                "iteration": iteration,
            },
        )
        raise

    finally:
        # Emit turn_complete event
        self._emit(
            "turn_complete",
            {
                "final_answer": final_answer,
                "completed": completed,
                "stop_reason": stop_reason,
                "iterations": iteration,
                "usage": dict(self.model.cumulative),
                "cost_usd": round(self.model.cumulative.get("cost_usd", 0.0), 6),
            },
        )

    return ReactAgentResult(
        final_answer=final_answer,
        completed=completed,
        stop_reason=stop_reason,
        steps=iteration,
        messages=messages,
        usage=dict(self.model.cumulative),
        cost_usd=round(self.model.cumulative.get("cost_usd", 0.0), 6),
    )
```

### 3.5 Emit Events in `_call_llm()` Method

Modify `_call_llm()` to pass information for event emission:

```python
async def _call_llm(
    self, messages: list[dict], tools: list[dict] | None, stream: bool, console: Any
) -> LLMResult:
    """Call the model (with transient-error retries) and normalise the response."""
    try:
        response = await self._acompletion_with_retry(messages, tools, stream)
        if stream:
            return self._record(await self._consume_stream(response, console))
        return self._record(self._parse_response(response))
    except Exception as e:
        self._emit(
            "error",
            {
                "error_type": "llm_call",
                "error": str(e),
            },
        )
        raise
```

### 3.6 Emit Events in `_consume_stream()` Method

Modify `_consume_stream()` to emit chunk and stream-end events:

```python
async def _consume_stream(self, response: Any, console: Any) -> tuple[LLMResult, Any]:
    """Drain a streaming response, accumulating content + tool calls."""
    full_content = ""
    tool_calls_acc: dict[int, dict] = {}
    finish_reason: str | None = None
    final_usage = None

    async for chunk in response:
        choice = chunk.choices[0] if getattr(chunk, "choices", None) else None
        if choice is None:
            if getattr(chunk, "usage", None):
                final_usage = chunk.usage
            continue
        delta = choice.delta
        if choice.finish_reason:
            finish_reason = choice.finish_reason
        if getattr(delta, "content", None):
            full_content += delta.content
            # Emit assistant_chunk event
            self._emit("assistant_chunk", {"content": delta.content})
            if console is not None:
                console.print(
                    delta.content, end="", markup=False, highlight=False
                )
        if getattr(delta, "tool_calls", None):
            for tc_delta in delta.tool_calls:
                slot = tool_calls_acc.setdefault(
                    tc_delta.index,
                    {
                        "id": "",
                        "type": "function",
                        "function": {"name": "", "arguments": ""},
                    },
                )
                if tc_delta.id:
                    slot["id"] = tc_delta.id
                if tc_delta.function:
                    if tc_delta.function.name:
                        slot["function"]["name"] += tc_delta.function.name
                    if tc_delta.function.arguments:
                        slot["function"]["arguments"] += (
                            tc_delta.function.arguments
                        )
        if getattr(chunk, "usage", None):
            final_usage = chunk.usage

    # Emit assistant_stream_end event
    self._emit("assistant_stream_end", {})

    result = LLMResult(full_content or None, tool_calls_acc, finish_reason)
    return result, final_usage
```

### 3.7 Emit Events in `_run_tool_calls()` Method

Modify `_run_tool_calls()` to emit tool-related events:

```python
async def _run_tool_calls(
    self,
    tool_calls: list[dict],
    messages: list[dict[str, Any]],
    auto_approve: bool | None,
    stream: bool,
    console: Any,
) -> None:
    """Execute each tool call in order and append its ``role: tool`` result."""
    for tc in tool_calls:
        name = tc["function"]["name"]
        tc_id = tc["id"]
        raw_args = tc["function"].get("arguments") or "{}"
        try:
            args = json.loads(raw_args) if raw_args.strip() else {}
            if not isinstance(args, dict):
                raise ValueError("arguments must be a JSON object")
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            self._emit(
                "error",
                {
                    "error_type": "parsing",
                    "error": f"Failed to parse arguments for '{name}': {e}",
                    "tool_name": name,
                },
            )
            messages.append(
                _tool_message(
                    f"ERROR: arguments for '{name}' were not a valid JSON object.",
                    tc_id,
                    name,
                )
            )
            continue

        tool = self.tool_router.get(name)
        if (
            tool is not None
            and tool.requires_approval
            and not self._approve(name, args, auto_approve, console)
        ):
            # Emit approval_required event
            self._emit(
                "approval_required",
                {
                    "tool_name": name,
                    "tool_call_id": tc_id,
                    "arguments": args,
                },
            )
            messages.append(
                _tool_message(
                    f"Tool '{name}' was not approved by the user; it was skipped.",
                    tc_id,
                    name,
                )
            )
            continue

        # Emit tool_call event
        self._emit(
            "tool_call",
            {
                "tool_name": name,
                "tool_call_id": tc_id,
                "arguments": args,
            },
        )

        try:
            output, success = await self.tool_router.call_tool(
                name, args, tool_call_id=tc_id
            )
        except Exception as e:
            self._emit(
                "error",
                {
                    "error_type": "tool_execution",
                    "error": str(e),
                    "tool_name": name,
                },
            )
            success = False
            output = str(e)

        # Emit tool_output event
        self._emit(
            "tool_output",
            {
                "tool_name": name,
                "tool_call_id": tc_id,
                "output": output,
                "success": success,
            },
        )

        messages.append(_tool_message(output, tc_id, name))
        if stream and console is not None:
            tag = "ok" if success else "error"
            console.print(f"[dim]  {tag}: {_short(output)}[/dim]")
```

---

## 4. Modify `diorama/core/__init__.py`

Add exports for the new callback system:

```python
"""Diorama core agent framework."""

from diorama.core.callback import Callback, Event, RichLoggingCallback
from diorama.core.react import ReactAgent, ReactAgentResult
from diorama.core.router import ToolRouter
from diorama.core.tool import Tool

__all__ = [
    "Callback",
    "Event",
    "RichLoggingCallback",
    "ReactAgent",
    "ReactAgentResult",
    "ToolRouter",
    "Tool",
]
```

---

## 5. Test Strategy

### 5.1 Unit Test: Event Emission

Create `tests/test_callbacks.py`:

```python
import pytest
from diorama.core import ReactAgent, Callback, Event
from diorama.core.demo_tools import CalculatorTool


class CaptureCallback(Callback):
    def __init__(self):
        self.events: list[Event] = []

    def on_event(self, event: Event) -> None:
        self.events.append(event)


@pytest.mark.asyncio
async def test_ready_event():
    capture = CaptureCallback()
    agent = ReactAgent(
        tools=[CalculatorTool()],
        callbacks=[capture],
    )
    await agent.run("What is 5?")

    assert capture.events[0].event_type == "ready"
    assert capture.events[0].data["tool_count"] == 1


@pytest.mark.asyncio
async def test_processing_event():
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
async def test_tool_call_and_output():
    capture = CaptureCallback()
    agent = ReactAgent(
        tools=[CalculatorTool()],
        callbacks=[capture],
    )
    await agent.run("What is 5 + 3?")

    event_types = [e.event_type for e in capture.events]
    assert "tool_call" in event_types
    assert "tool_output" in event_types

    # Verify order
    tool_call_idx = event_types.index("tool_call")
    tool_output_idx = event_types.index("tool_output")
    assert tool_call_idx < tool_output_idx


@pytest.mark.asyncio
async def test_callback_exception_does_not_break_agent():
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
```

### 5.2 Backward Compatibility Test

```python
@pytest.mark.asyncio
async def test_stream_true_still_works(capsys):
    """Verify stream=True still works (backward compatibility)."""
    agent = ReactAgent(tools=[CalculatorTool()])
    result = await agent.run("What is 5 + 3?", stream=True)

    # Should print to console
    captured = capsys.readouterr()
    assert "ready" in captured.out.lower() or len(captured.out) > 0
    assert result.completed
```

---

## 6. Integration Checklist

- [ ] Create `diorama/core/callback.py` with `Event`, `Callback`, `RichLoggingCallback`
- [ ] Add `callbacks` parameter to `ReactAgent.__init__`
- [ ] Add `_emit()` helper method to `ReactAgent`
- [ ] Emit `ready` event in `run()`
- [ ] Emit `processing` event in main loop
- [ ] Emit `assistant_chunk` and `assistant_stream_end` in `_consume_stream()`
- [ ] Emit `assistant_message` in `run()`
- [ ] Emit `tool_call` in `_run_tool_calls()`
- [ ] Emit `tool_output` in `_run_tool_calls()`
- [ ] Emit `approval_required` in `_run_tool_calls()`
- [ ] Emit `error` events in exception handlers
- [ ] Emit `turn_complete` in `finally` block of `run()`
- [ ] Update `diorama/core/__init__.py` to export new classes
- [ ] Write unit tests in `tests/test_callbacks.py`
- [ ] Verify backward compatibility with `stream=True`
- [ ] Update `CLAUDE.md` with callback usage examples
- [ ] Verify existing tests still pass

---

## 7. Edge Cases & Error Handling

### 7.1 Callback Raises Exception

```python
def _emit(self, event_type: str, data: dict[str, Any] | None = None) -> None:
    event = Event(event_type, data or {})
    for callback in self.callbacks:
        try:
            callback.on_event(event)
        except Exception as e:  # noqa: BLE001
            # Log and continue (never re-raise)
            logger.warning("Callback error on %s: %s", event_type, e)
```

### 7.2 No Callbacks Registered

The agent should work fine with an empty `self.callbacks` list. No performance penalty.

### 7.3 Multiple Callbacks, One Fails

Other callbacks still execute (see 7.1).

### 7.4 Stream=True with Callbacks

```python
# Case 1: stream=True, callbacks=[]
# → Auto-add RichLoggingCallback

# Case 2: stream=True, callbacks=[CustomCallback()]
# → Use CustomCallback, also add RichLoggingCallback (or warn?)
# Recommendation: just add it; user can unregister if needed

# Case 3: stream=False, callbacks=[RichLoggingCallback()]
# → Use RichLoggingCallback (no auto-console.print from stream=True)
```

---

## 8. Performance Notes

- **Event creation:** O(1) per event (just allocate a dict and dataclass)
- **Callback dispatch:** O(n) where n = number of callbacks (typically 1–3)
- **Per-turn overhead:** ~1% (negligible) with typical 3 callbacks
- **Memory:** Events are not queued; they are discarded after dispatch (unlike Coursify)

---

## 9. Documentation Updates

After implementation, update:

1. **CLAUDE.md:** Add callback usage examples to "Common Development Patterns"
2. **README.md:** Mention callback system as an observability feature
3. **Docstrings:** Ensure all callback-related methods have clear docstrings

Example for CLAUDE.md:

```markdown
### Observing Agent Execution with Callbacks

```python
from diorama.core import ReactAgent, RichLoggingCallback, Callback, Event

# Built-in rich logging
agent = ReactAgent(
    tools=[...],
    callbacks=[RichLoggingCallback(truncate=True)]
)
result = await agent.run("Do X")

# Custom callback
class MetricsCallback(Callback):
    def on_event(self, event: Event) -> None:
        if event.event_type == "tool_call":
            print(f"Tool called: {event.data['tool_name']}")

agent.callbacks = [RichLoggingCallback(), MetricsCallback()]
result = await agent.run("Do Y")
```
```

---

## 10. Rollout Plan

1. **Phase 1:** Implement callback.py and wire into react.py
2. **Phase 2:** Write unit tests and verify backward compatibility
3. **Phase 3:** Update documentation (CLAUDE.md, docstrings)
4. **Phase 4:** Optionally deprecate `stream=True` (future release)
5. **Phase 5:** Extensions (async callbacks, event filtering, etc.)

