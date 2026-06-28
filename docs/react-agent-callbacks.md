# ReAct Agent Callbacks — Streaming Agent Execution Traces

> **Status:** Design / ready for implementation
> **Audience:** contributors building UI/streaming features for Diorama; users instrumenting agent runs for logging/monitoring
> **Scope:** how to observe and react to agent execution events in real-time via a callback system similar to Coursify's event-driven architecture

---

## 1. Problem

### 1.1 Current Streaming Limitations

Diorama's `ReactAgent` currently supports streaming via a `stream` parameter that prints to a Rich console:

```python
result = await agent.run(prompt, stream=True)
```

This approach has three limitations:

1. **Monolithic:** Streaming is hard-coded into the agent. Adding new output formats (JSON, websockets, file logging) requires modifying core agent logic.
2. **Not observable:** External systems (UI, logging, monitoring) cannot instrument the agent's execution without patching or wrapping the entire `run` method.
3. **Not extensible:** There is no way to plugin custom behavior (e.g., "log tool calls to a database", "emit metrics on approval gates", "stream to a websocket").

### 1.2 How Coursify Solves This

Coursify uses an **event-driven callback system**: the agent loop emits a stream of typed `Event` objects (e.g., `tool_call`, `assistant_chunk`, `error`), and users can register multiple callbacks to observe/react to those events in real-time.

Benefits:
- **Decoupled:** core agent logic is separate from rendering/logging
- **Composable:** run multiple callbacks simultaneously (e.g., Rich logging + JSON file logging + metrics emission)
- **Extensible:** add custom callback subclasses without touching the agent

### 1.3 Goal for Diorama

Adopt a similar callback pattern for `ReactAgent`, where:
1. The agent emits `Event` objects at key points in execution (LLM calls, tool execution, approval gates, errors).
2. Users can register `Callback` subclasses to observe/react to those events.
3. A built-in `RichLoggingCallback` replaces the current `stream=True` behavior.
4. The system is backward-compatible: existing code works unchanged; new code opts into callbacks.

---

## 2. Architecture

### 2.1 Event Model

An `Event` represents a single observable moment in agent execution. It is a simple dataclass:

```python
@dataclass
class Event:
    """An event emitted by the agent loop for callbacks to consume."""
    event_type: str          # Event kind (e.g., "tool_call", "assistant_chunk")
    data: dict[str, Any]     # Event payload (schema varies by event_type)
    seq: int | None = None   # Optional monotonic sequence number for ordering
```

**Event types are strings (not enums)** for extensibility: new event types can be added without breaking existing callbacks.

### 2.2 Callback Model

A callback is a user-defined class that reacts to events:

```python
class MyCallback:
    def on_event(self, event: Event) -> None:
        """Called synchronously when the agent emits an event."""
        if event.event_type == "tool_call":
            print(f"Tool: {event.data['tool_name']}")
        elif event.event_type == "error":
            log_error(event.data['error'])
```

Callbacks are:
- **Synchronous:** invoked immediately when an event is emitted (not queued).
- **Chainable:** multiple callbacks can be registered on a single agent.
- **Fault-tolerant:** if a callback raises an exception, the agent logs a warning and continues (a failing callback does not break the run).

### 2.3 Integration Points

The `ReactAgent.run()` method accepts an optional `callbacks` parameter:

```python
agent = ReactAgent(tools=[...])
agent.callbacks = [RichLoggingCallback(), MyCustomCallback()]

result = await agent.run("Do X", stream=False)  # Callbacks observe, no stream param needed
```

Events are emitted:
1. **Before** entering the main loop (`ready` event).
2. **During each LLM call**: `assistant_chunk` (if streaming), `assistant_stream_end`, or `assistant_message` (if non-streaming).
3. **During tool execution**: `tool_call`, `tool_output`, and `approval_required` (if the tool needs approval).
4. **On error**: `error` event (LLM failure, tool execution failure).
5. **On completion**: `turn_complete` event with the final answer.

The agent itself does **not** print or log; callbacks are responsible for all side effects (console rendering, logging, metrics).

---

## 3. Event Reference

This section defines all event types emitted by `ReactAgent` and the schema of each event's `data` field.

### 3.1 `ready`

**When:** Agent enters `run()`, before the main loop starts.

**Payload:**
```python
{
    "tool_count": int,        # Number of registered tools
}
```

**Example callback handler:**
```python
def _on_ready(self, data):
    count = data.get("tool_count", 0)
    print(f"Agent ready with {count} tools")
```

---

### 3.2 `processing`

**When:** Agent begins processing a new turn (iteration of the main loop).

**Payload:**
```python
{
    "iteration": int,         # 1-indexed turn number
}
```

**Example callback handler:**
```python
def _on_processing(self, data):
    turn = data.get("iteration", 0)
    print(f"--- Turn {turn} ---")
```

---

### 3.3 `assistant_chunk`

**When:** A single token arrives from the model during streaming (only if `stream=True` in `_call_llm`).

**Payload:**
```python
{
    "content": str,           # The token/chunk text
}
```

**Example callback handler:**
```python
def _on_assistant_chunk(self, data):
    # Accumulate chunks for display or logging
    self.accumulated_content += data.get("content", "")
```

**Note:** This event is only emitted during streaming LLM responses. For simplicity, `ReactAgent` should expose a `stream` parameter to `run()` that controls whether to stream and emit these events.

---

### 3.4 `assistant_stream_end`

**When:** Streaming of an assistant message ends (no more tokens).

**Payload:**
```python
{
    "content": str | None,    # (Optional) full accumulated content
}
```

**Example callback handler:**
```python
def _on_assistant_stream_end(self, data):
    print()  # Newline after streaming output
```

---

### 3.5 `assistant_message`

**When:** A complete assistant message is received (non-streaming, or after streaming ends).

**Payload:**
```python
{
    "content": str | None,    # The assistant's text response (or None if only tool calls)
    "finish_reason": str | None,  # Model's stop reason (e.g., "stop", "tool_calls")
}
```

**Example callback handler:**
```python
def _on_assistant_message(self, data):
    content = data.get("content", "")
    if content:
        print(f"Reasoning:\n{content}")
```

---

### 3.6 `tool_call`

**When:** Agent decides to call a tool (before execution).

**Payload:**
```python
{
    "tool_name": str,         # Name of the tool being called
    "tool_call_id": str,      # Unique ID for this call (from LLM)
    "arguments": dict,        # Parsed tool arguments
}
```

**Example callback handler:**
```python
def _on_tool_call(self, data):
    name = data.get("tool_name", "?")
    args = data.get("arguments", {})
    print(f"Calling {name}({args})")
```

---

### 3.7 `tool_output`

**When:** Tool execution completes (success or failure).

**Payload:**
```python
{
    "tool_name": str,         # Name of the tool that was called
    "tool_call_id": str,      # The ID from the tool_call event
    "output": str,            # The tool's return value (stringified)
    "success": bool,          # True if the tool succeeded, False if it raised an exception
}
```

**Example callback handler:**
```python
def _on_tool_output(self, data):
    tool = data.get("tool_name", "?")
    success = data.get("success", False)
    output = data.get("output", "")
    status = "✓" if success else "✗"
    print(f"  {status} {tool}: {output[:100]}")
```

---

### 3.8 `approval_required`

**When:** A tool marked `requires_approval=True` is about to run, and approval is not auto-granted.

**Payload:**
```python
{
    "tool_name": str,         # Name of the tool awaiting approval
    "tool_call_id": str,      # The ID from the pending tool_call
    "arguments": dict,        # The tool's arguments
}
```

**Example callback handler:**
```python
def _on_approval_required(self, data):
    tool = data.get("tool_name", "?")
    print(f"⚠ Approval required for {tool}")
```

**Note:** In the current design, `ReactAgent._approve()` resolves approval synchronously and the callback is informational. (Unlike Coursify, where callbacks would include a way to provide approval decisions.)

---

### 3.9 `error`

**When:** An error occurs (LLM call failure, tool execution failure, parsing error, etc.).

**Payload:**
```python
{
    "error_type": str,        # Category: "llm_call", "tool_execution", "parsing", etc.
    "error": str,             # Error message or exception string
    "tool_name": str | None,  # (Optional) name of the tool if the error is tool-related
    "iteration": int,         # The turn number when the error occurred
}
```

**Example callback handler:**
```python
def _on_error(self, data):
    error_type = data.get("error_type", "unknown")
    error = data.get("error", "")
    print(f"ERROR [{error_type}]: {error}")
    # Optionally: propagate to error tracking system
```

---

### 3.10 `turn_complete`

**When:** Agent completes a turn (loop exits, whether by success or max-iterations).

**Payload:**
```python
{
    "final_answer": str | None,    # The agent's final text response (None if max-iterations reached)
    "completed": bool,             # True if the agent finished gracefully
    "stop_reason": str,            # "completed", "max_iterations", or "error"
    "iterations": int,             # Total number of iterations this turn
    "usage": dict,                 # Cumulative token usage {"prompt_tokens", "completion_tokens", ...}
    "cost_usd": float,             # Estimated cost in USD
}
```

**Example callback handler:**
```python
def _on_turn_complete(self, data):
    answer = data.get("final_answer", "")
    reason = data.get("stop_reason", "")
    cost = data.get("cost_usd", 0)
    print(f"Done [{reason}]: {answer[:50]}... (cost: ${cost:.4f})")
```

---

## 4. Built-in Callbacks

### 4.1 `RichLoggingCallback`

A Rich-styled terminal renderer modeled after Coursify's `CoursifyLoggingCallback`.

**Features:**
- Pretty-printed tool calls and results using syntax highlighting.
- Colored panels for errors and final answers.
- Streaming token accumulation and line endings.
- Optional truncation of long outputs.

**API:**
```python
class RichLoggingCallback(Callback):
    def __init__(
        self,
        truncate: bool = False,
        max_result_length: int = 1000,
        console: Console | None = None,
    ) -> None:
        """
        Args:
            truncate: Clip long tool outputs to max_result_length characters.
            max_result_length: Max chars per truncated output.
            console: Optional Rich Console; created if omitted.
        """
```

**Usage:**
```python
from diorama.core.callback import RichLoggingCallback

agent = ReactAgent(tools=[...])
agent.callbacks = [RichLoggingCallback(truncate=True, max_result_length=500)]

result = await agent.run("Do X")
```

### 4.2 `Callback` (Base Class)

All callbacks should inherit from `Callback`:

```python
from diorama.core.callback import Callback, Event

class MyCallback(Callback):
    def on_event(self, event: Event) -> None:
        """Handle a single event."""
        if event.event_type == "tool_call":
            self._on_tool_call(event.data)
        elif event.event_type == "error":
            self._on_error(event.data)
    
    def _on_tool_call(self, data: dict) -> None:
        # Custom logic here
        pass
    
    def _on_error(self, data: dict) -> None:
        # Custom logic here
        pass
```

**Best practice:** Use the dynamic dispatch pattern (`getattr(self, f"_on_{event.event_type}", None)`) like Coursify does, so you only define handlers for events you care about.

---

## 5. Implementation Details

### 5.1 Module Structure

Create `diorama/core/callback.py` with:

```
callback.py
├── Event (dataclass)
├── Callback (base class)
└── RichLoggingCallback (concrete callback)
```

### 5.2 Integration into `ReactAgent`

**Changes to `react.py`:**

1. Add `callbacks: list[Callback] | None = None` parameter to `__init__`.
2. Store as `self.callbacks: list[Callback] = callbacks or []`.
3. Add a `_emit(event_type: str, data: dict)` helper method that:
   - Creates an `Event` object.
   - Dispatches to all callbacks synchronously.
   - Catches and logs any callback exceptions (does not re-raise).
4. Emit events at key points:
   - `_emit("ready", {"tool_count": len(self.tools)})` at the start of `run()`.
   - `_emit("processing", {"iteration": iteration})` at the start of each loop iteration.
   - `_emit("assistant_chunk", {"content": chunk_text})` during `_consume_stream()`.
   - `_emit("assistant_stream_end", {})` after streaming ends.
   - `_emit("assistant_message", {"content": ..., "finish_reason": ...})` after non-streaming calls.
   - `_emit("tool_call", {...})` before calling `tool_router.call_tool()`.
   - `_emit("tool_output", {...})` after tool execution.
   - `_emit("approval_required", {...})` when `requires_approval=True` and not auto-approved.
   - `_emit("error", {...})` on exceptions.
   - `_emit("turn_complete", {...})` at the end of `run()`.

### 5.3 Backward Compatibility

- The `stream` parameter in `run()` is **kept** and continues to work (or can be deprecated in favor of callbacks).
- If `stream=True` and no `RichLoggingCallback` is registered, a default one is added automatically.
- Alternatively: remove the `stream` parameter entirely and document that users should use `RichLoggingCallback` instead.

**Recommendation:** Keep `stream=True` for now; add a deprecation warning encouraging users to use `callbacks=[RichLoggingCallback()]`.

### 5.4 Error Handling

- Callback exceptions are caught and logged (not re-raised).
- This ensures that a broken callback does not break the agent run.

**Example:**
```python
def _emit(self, event_type: str, data: dict) -> None:
    event = Event(event_type, data)
    for callback in self.callbacks:
        try:
            callback.on_event(event)
        except Exception as e:
            logger.warning("Callback error on %s: %s", event_type, e)
```

### 5.5 Thread Safety

- Callbacks are synchronous and run on the same thread/coroutine as the agent.
- No locking is needed for single-threaded async code.
- If a callback performs blocking I/O, it will block the agent; document this clearly.

### 5.6 Sequence Numbers

- The `Event.seq` field is optional and set by the agent to enable event ordering.
- Start at 1 and increment for each event emitted.
- Useful for distributed logging where events might arrive out of order.

---

## 6. Usage Examples

### 6.1 Basic Logging with Rich

```python
from diorama.core import ReactAgent, RichLoggingCallback
from diorama.core.tool import Tool

class CalculatorTool(Tool):
    name = "add"
    def execute(self, a: int, b: int) -> str:
        return str(a + b)

agent = ReactAgent(
    tools=[CalculatorTool()],
    callbacks=[RichLoggingCallback(truncate=True)]
)

result = await agent.run("What is 5 + 3?")
print(result.final_answer)
```

**Console output** (example):
```
Agent ready with 1 tools
--- Turn 1 ---
Reasoning: I'll use the add tool to calculate 5 + 3.
Tool Call: add
├─ a: 5
└─ b: 3
Tool Result: add
├─ 8

Final Answer
└─ The answer is 8.
```

### 6.2 Custom Callback for Metrics

```python
from diorama.core.callback import Callback, Event

class MetricsCallback(Callback):
    def __init__(self):
        self.tool_calls = 0
        self.errors = 0
    
    def on_event(self, event: Event) -> None:
        if event.event_type == "tool_call":
            self.tool_calls += 1
        elif event.event_type == "error":
            self.errors += 1
        elif event.event_type == "turn_complete":
            print(f"Metrics: {self.tool_calls} tool calls, {self.errors} errors")

agent = ReactAgent(tools=[...], callbacks=[MetricsCallback()])
result = await agent.run("Do something")
```

### 6.3 Multiple Callbacks (Logging + Metrics)

```python
from diorama.core.callback import RichLoggingCallback

agent = ReactAgent(
    tools=[...],
    callbacks=[
        RichLoggingCallback(truncate=True),
        MetricsCallback(),
    ]
)

result = await agent.run("Complex task")
```

### 6.4 File Logging Callback

```python
import json
from diorama.core.callback import Callback, Event

class JSONLoggingCallback(Callback):
    def __init__(self, filepath: str):
        self.filepath = filepath
        self.events = []
    
    def on_event(self, event: Event) -> None:
        self.events.append({
            "type": event.event_type,
            "data": event.data,
        })
    
    def __del__(self):
        if self.events:
            with open(self.filepath, "w") as f:
                json.dump(self.events, f, indent=2)

agent = ReactAgent(
    tools=[...],
    callbacks=[
        RichLoggingCallback(),
        JSONLoggingCallback("agent_run.json"),
    ]
)

result = await agent.run("Task")
```

---

## 7. Implementation Checklist

- [ ] Create `diorama/core/callback.py` with `Event`, `Callback`, `RichLoggingCallback`.
- [ ] Update `diorama/core/react.py`:
  - [ ] Add `callbacks` parameter to `__init__`.
  - [ ] Add `_emit()` helper method.
  - [ ] Emit all event types at appropriate points.
- [ ] Update `diorama/core/__init__.py` to export `Event`, `Callback`, `RichLoggingCallback`.
- [ ] Write unit tests for callback emission and error handling.
- [ ] Update `CLAUDE.md` with callback usage examples.
- [ ] Update existing tests to ensure backward compatibility.
- [ ] Consider: deprecation warning for `stream=True` (or keep for now).

---

## 8. Comparison to Coursify

| Aspect | Coursify | Diorama (Proposed) |
|--------|----------|-----------------|
| **Event Type** | String (open-ended) | String (open-ended) |
| **Callback API** | `on_event(event: Event)` | `on_event(event: Event)` |
| **Dispatch** | Sync + async queue | Sync only |
| **Built-in Callback** | `CoursifyLoggingCallback` | `RichLoggingCallback` |
| **Approval Flow** | Callbacks can provide approval decisions | Approval is sync; callbacks are informational |
| **Error Handling** | Callbacks that fail are logged | Callbacks that fail are logged |

**Key difference:** Coursify's event queue is async (for TUI responsiveness), while Diorama's callback system is simpler and purely synchronous. This is appropriate for Diorama's current use case (library + CLI, not a full TUI).

---

## 9. Future Enhancements

1. **Event filtering:** Allow callbacks to register interest in specific event types only (optimization).
2. **Async callbacks:** Support `async def on_event()` for callbacks that perform async I/O.
3. **Approval gateway:** Extend callbacks to provide approval decisions (not just observe).
4. **Sub-agent events:** Propagate events from nested agents (e.g., research sub-agents in Coursify).
5. **Weave integration:** Emit events that can be captured by W&B Weave for cost/quality tracing.

---

## 10. References

- **Coursify callback system:** `https://github.com/soumik12345/coursify/blob/main/coursify/callback.py`
- **Coursify event model:** `https://github.com/soumik12345/coursify/blob/main/coursify/core/events.py`
- **Coursify agent integration:** `https://github.com/soumik12345/coursify/blob/main/coursify/core/session.py#L82` (send_event)
