# Callback System Architecture & Data Flow

This document provides visual diagrams and data flow descriptions for the callback system integration in Diorama's `ReactAgent`.

---

## 1. High-Level Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                        ReactAgent                             │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ Public Interface: async def run(prompt, callbacks=...) │  │
│  └────────────────────────────────────────────────────────┘  │
│                          │                                    │
│                          ▼                                    │
│  ┌────────────────────────────────────────────────────────┐  │
│  │            Main Agent Loop                             │  │
│  │  ┌──────────────────────────────────────────────────┐  │  │
│  │  │ 1. Emit "ready" event                            │  │  │
│  │  │ 2. Loop: Call LLM → Execute Tools → Repeat       │  │  │
│  │  │ 3. Emit "turn_complete" event                    │  │  │
│  │  └──────────────────────────────────────────────────┘  │  │
│  └────────────────────────────────────────────────────────┘  │
│                          │                                    │
│                          ▼                                    │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  _emit(event_type: str, data: dict)                   │  │
│  │  ├─ Create Event object                              │  │
│  │  └─ Dispatch to all callbacks (synchronously)        │  │
│  └────────────────────────────────────────────────────────┘  │
│                          │                                    │
└──────────────────────────┼────────────────────────────────────┘
                           │
                           ▼
        ┌──────────────────────────────────────────┐
        │   Registered Callbacks (User-Provided)   │
        │                                          │
        │  ┌──────────────────────────────────┐   │
        │  │ RichLoggingCallback              │   │
        │  │  ├─ _on_ready()                  │   │
        │  │  ├─ _on_tool_call()              │   │
        │  │  ├─ _on_tool_output()            │   │
        │  │  ├─ _on_assistant_chunk()        │   │
        │  │  └─ _on_turn_complete()          │   │
        │  └──────────────────────────────────┘   │
        │                                          │
        │  ┌──────────────────────────────────┐   │
        │  │ Custom Callback #1               │   │
        │  │  ├─ _on_tool_call()              │   │
        │  │  └─ _on_error()                  │   │
        │  └──────────────────────────────────┘   │
        │                                          │
        │  ┌──────────────────────────────────┐   │
        │  │ Custom Callback #2 (async IO)    │   │
        │  │  └─ _on_turn_complete()          │   │
        │  └──────────────────────────────────┘   │
        └──────────────────────────────────────────┘
                           │
                           ▼
        ┌──────────────────────────────────────────┐
        │         Side Effects (Per Callback)      │
        │                                          │
        │  • Print to console (Rich)               │
        │  • Write to log file (JSON)              │
        │  • Send to metrics system                │
        │  • Update UI via WebSocket               │
        │  • Store in database                     │
        └──────────────────────────────────────────┘
```

---

## 2. Event Emission & Dispatch Flow

### 2.1 Single Turn Execution Timeline

```
Time ──────────────────────────────────────────────────────────►

 │
 ├─► ready
 │    ├─► RichLoggingCallback._on_ready()
 │    ├─► MetricsCallback._on_ready()
 │    └─► CustomCallback._on_ready()
 │
 ├─► processing (iteration 1)
 │
 ├─► assistant_chunk ("I'll")
 │    ├─► RichLoggingCallback._on_assistant_chunk()
 │    └─► CustomCallback.on_event() [ignored]
 │
 ├─► assistant_chunk (" use")
 │
 ├─► assistant_chunk (" the")
 │
 ├─► assistant_stream_end
 │
 ├─► tool_call (name="calculator", args={...})
 │    ├─► RichLoggingCallback._on_tool_call()
 │    └─► MetricsCallback.tool_calls += 1
 │
 ├─► tool_output (tool="calculator", output="8", success=True)
 │    ├─► RichLoggingCallback._on_tool_output()
 │    └─► MetricsCallback._on_tool_output()
 │
 ├─► processing (iteration 2)
 │
 ├─► assistant_message (content="The answer is 8.", finish_reason="stop")
 │
 ├─► turn_complete
 │    ├─► RichLoggingCallback._on_turn_complete()
 │    ├─► MetricsCallback._on_turn_complete()
 │    │    └─► Print metrics to stdout
 │    └─► JSONLoggingCallback.dump_to_file()
 │
 └─► Return ReactAgentResult to caller
```

---

## 3. Callback Registration & Initialization

```
┌─────────────────────────────────────────────────────┐
│ User Code                                            │
└─────────────────────────────────────────────────────┘
  │
  ├─► agent = ReactAgent(tools=[...])
  │
  ├─► agent.callbacks = [
  │     RichLoggingCallback(truncate=True),
  │     MetricsCallback(),
  │     JSONLoggingCallback("trace.json")
  │   ]
  │
  └─► await agent.run("Do X")
       │
       └─► ReactAgent.run()
            │
            ├─► self.callbacks is [RichLoggingCallback, ...]
            │
            ├─► Iteration: For each event emitted
            │    │
            │    └─► self._emit(event_type, data)
            │         │
            │         ├─► event = Event(event_type, data)
            │         │
            │         └─► for callback in self.callbacks:
            │              try:
            │                  callback.on_event(event)
            │              except Exception as e:
            │                  logger.warning(...)
            │
            └─► Return result
```

---

## 4. Event Data Schema by Event Type

### 4.1 LLM Interaction Events

```
ready
├─ tool_count: int

processing
├─ iteration: int

assistant_chunk
├─ content: str

assistant_stream_end
├─ (empty)

assistant_message
├─ content: str | None
└─ finish_reason: str | None
```

### 4.2 Tool Interaction Events

```
tool_call
├─ tool_name: str
├─ tool_call_id: str
└─ arguments: dict[str, Any]

tool_output
├─ tool_name: str
├─ tool_call_id: str
├─ output: str
└─ success: bool

approval_required
├─ tool_name: str
├─ tool_call_id: str
└─ arguments: dict[str, Any]
```

### 4.3 Terminal Events

```
error
├─ error_type: str          # "llm_call", "tool_execution", "parsing", etc.
├─ error: str               # Exception message
├─ tool_name: str | None    # (if tool-related)
└─ iteration: int

turn_complete
├─ final_answer: str | None
├─ completed: bool
├─ stop_reason: str         # "completed" | "max_iterations" | "error"
├─ iterations: int
├─ usage: dict              # {"prompt_tokens": ..., "completion_tokens": ...}
└─ cost_usd: float
```

---

## 5. Callback Execution Model

### 5.1 Synchronous Dispatch (No Blocking)

```
Agent Loop (Thread/Coroutine)
│
├─► LLM Call
│   │
│   ├─► Stream token
│   │
│   ├─► _emit("assistant_chunk", {...})
│   │    │
│   │    └─► for callback in callbacks:
│   │         callback.on_event(event)   ◄─── Synchronous (blocking)
│   │
│   └─► More tokens...
│
└─► Return result
```

**Key property:** If a callback performs blocking I/O (e.g., database write), it **will** stall the agent loop. This is documented; users must keep callbacks non-blocking or use thread pools.

### 5.2 Fault Tolerance

```
_emit(event_type, data)
│
└─► for callback in self.callbacks:
     try:
         callback.on_event(event)
     except Exception as e:
         logger.warning("Callback error on %s: %s", event_type, e)
         # Continue to next callback (do NOT re-raise)
```

**Guarantee:** A callback that crashes does not break the agent or subsequent callbacks.

---

## 6. Integration Points in ReactAgent Code

### 6.1 In `run()` method

```python
async def run(self, prompt: str, *, stream: bool = False, auto_approve: bool | None = None) -> ReactAgentResult:
    self._emit("ready", {"tool_count": len(self.tools)})
    
    messages = [...]
    iteration = 0
    
    while ...:
        iteration += 1
        self._emit("processing", {"iteration": iteration})
        
        result = await self._call_llm(messages, ..., stream)
        
        # (tool execution happens here, emitting tool_call, tool_output)
        
    self._emit("turn_complete", {
        "final_answer": final_answer,
        "completed": completed,
        "stop_reason": stop_reason,
        "iterations": iteration,
        "usage": dict(self.model.cumulative),
        "cost_usd": ...,
    })
    
    return ReactAgentResult(...)
```

### 6.2 In `_call_llm()` method

```python
async def _call_llm(self, messages, tools, stream, console):
    response = await self._acompletion_with_retry(messages, tools, stream)
    
    if stream:
        return self._record(await self._consume_stream(response, console))
    else:
        return self._record(self._parse_response(response))

# Inside _consume_stream():
async def _consume_stream(self, response, console):
    for chunk in response:
        if content_token:
            self._emit("assistant_chunk", {"content": chunk_text})
    
    self._emit("assistant_stream_end", {})
    return LLMResult(...)
```

### 6.3 In `_run_tool_calls()` method

```python
for tool_call in tool_calls:
    name = tool_call["function"]["name"]
    args = ...
    
    if requires_approval and not auto_approved:
        self._emit("approval_required", {
            "tool_name": name,
            "tool_call_id": tool_call["id"],
            "arguments": args,
        })
    
    self._emit("tool_call", {
        "tool_name": name,
        "tool_call_id": tool_call["id"],
        "arguments": args,
    })
    
    try:
        output, success = await self.tool_router.call_tool(name, args, ...)
    except Exception as e:
        self._emit("error", {
            "error_type": "tool_execution",
            "error": str(e),
            "tool_name": name,
            "iteration": iteration,
        })
        success = False
        output = str(e)
    
    self._emit("tool_output", {
        "tool_name": name,
        "tool_call_id": tool_call["id"],
        "output": output,
        "success": success,
    })
```

---

## 7. Backward Compatibility Strategy

### Current behavior (with `stream=True`)

```python
result = await agent.run(prompt, stream=True)
# Prints to console directly
```

### After callback integration

**Option A: Keep `stream=True` (recommended)**

```python
result = await agent.run(prompt, stream=True)
# Internally: if stream=True and no RichLoggingCallback registered:
#   agent.callbacks.append(RichLoggingCallback())
# Same behavior as before
```

**Option B: Deprecate `stream=True`**

```python
result = await agent.run(prompt, stream=True)
# Issues deprecation warning
# Users should use: agent.callbacks = [RichLoggingCallback()]
```

**Recommendation:** Go with Option A for now (keep `stream=True` functional) and deprecate it later.

---

## 8. Sequence Numbers for Distributed Logging

When `Event.seq` is set, events can be ordered even if they arrive out-of-sequence in a distributed system:

```python
class SequencedCallback(Callback):
    def __init__(self, remote_logger):
        self.remote_logger = remote_logger
    
    def on_event(self, event: Event) -> None:
        # Even if events arrive out of order, seq allows reordering
        self.remote_logger.send({
            "seq": event.seq,
            "type": event.event_type,
            "data": event.data,
        })

# In agent._emit():
seq_counter = 0
def _emit(self, event_type, data):
    self.seq_counter += 1
    event = Event(event_type, data, seq=self.seq_counter)
    for callback in self.callbacks:
        try:
            callback.on_event(event)
        except Exception as e:
            logger.warning(...)
```

---

## 9. Performance Considerations

| Aspect | Impact | Mitigation |
|--------|--------|-----------|
| **Callback count** | More callbacks = more dispatch overhead | Keep callback count small; consider combining logic |
| **Event frequency** | `assistant_chunk` fires per token (high) | Batch events in callbacks if needed (e.g., print every 10 chunks) |
| **Callback blocking I/O** | Stalls the agent loop | Document: keep callbacks non-blocking; use thread pool for I/O |
| **Memory** | Event data is discarded after dispatch (no queue) | No memory buildup (unlike async queue in Coursify) |

**Baseline:** With 3-4 typical callbacks and standard event frequency, overhead is negligible (< 1% agent latency).

---

## 10. Testing Strategy

### 10.1 Unit Tests for Event Emission

```python
def test_ready_event_emitted():
    """Verify 'ready' event is emitted at start of run()."""
    captured_events = []
    
    class CaptureCallback(Callback):
        def on_event(self, event):
            captured_events.append(event)
    
    agent = ReactAgent(tools=[DummyTool()], callbacks=[CaptureCallback()])
    await agent.run("test")
    
    assert captured_events[0].event_type == "ready"
    assert captured_events[0].data["tool_count"] == 1

def test_tool_call_output_events():
    """Verify tool_call and tool_output events are emitted in order."""
    captured = []
    
    class CaptureCallback(Callback):
        def on_event(self, event):
            captured.append(event.event_type)
    
    agent = ReactAgent(tools=[CalculatorTool()], callbacks=[CaptureCallback()])
    await agent.run("What is 1+1?")
    
    assert "tool_call" in captured
    assert "tool_output" in captured
    idx_call = captured.index("tool_call")
    idx_output = captured.index("tool_output")
    assert idx_call < idx_output  # tool_call comes before tool_output
```

### 10.2 Integration Tests

```python
def test_callback_error_does_not_break_agent():
    """Verify broken callback doesn't stop the agent."""
    
    class BadCallback(Callback):
        def on_event(self, event):
            raise RuntimeError("Intentional error")
    
    agent = ReactAgent(
        tools=[CalculatorTool()],
        callbacks=[BadCallback(), RichLoggingCallback()]
    )
    
    result = await agent.run("What is 1+1?")
    
    assert result.completed  # Agent completes despite broken callback
```

### 10.3 Rich Logging Callback Tests

```python
def test_rich_logging_truncates_long_outputs():
    """Verify RichLoggingCallback truncates when enabled."""
    callback = RichLoggingCallback(truncate=True, max_result_length=50)
    
    long_output = "x" * 100
    callback.on_event(Event("tool_output", {
        "tool_name": "test",
        "tool_call_id": "1",
        "output": long_output,
        "success": True,
    }))
    
    # Verify console output was truncated (captured via capsys or StringIO)
```

---

## 11. Example Walkthrough: Running an Agent

### Setup

```python
from diorama.core import ReactAgent, RichLoggingCallback
from diorama.core.callback import Callback, Event

class DebugCallback(Callback):
    def on_event(self, event: Event) -> None:
        print(f"[DEBUG] {event.event_type}", file=sys.stderr)

agent = ReactAgent(
    tools=[CalculatorTool()],
    callbacks=[
        RichLoggingCallback(),
        DebugCallback(),
    ]
)
```

### Execution

```python
result = await agent.run("What is 42 * 2?")
```

### Console Output

**stdout (from RichLoggingCallback):**
```
Agent ready with 1 tools

--- Turn 1 ---

Reasoning: I'll use the calculator tool to compute 42 * 2.

Tool Call: multiply
├─ a: 42
└─ b: 2

Tool Result: multiply
├─ 84

Final Answer
└─ The result is 84.

Cost: $0.0042
```

**stderr (from DebugCallback):**
```
[DEBUG] ready
[DEBUG] processing
[DEBUG] assistant_chunk
[DEBUG] assistant_chunk
[DEBUG] assistant_chunk
[DEBUG] assistant_stream_end
[DEBUG] tool_call
[DEBUG] tool_output
[DEBUG] processing
[DEBUG] assistant_message
[DEBUG] turn_complete
```

---

## 12. Extension Points

Future enhancements could include:

1. **Async callbacks:** Support `async def on_event()` by awaiting them in a separate task.
2. **Event filtering:** Allow `callback.subscribe_to(["tool_call", "error"])` to reduce overhead.
3. **Callback priorities:** Run callbacks in order (e.g., metrics before logging).
4. **Context managers:** Support `async with agent.run(...) as result:` for resource cleanup.
5. **Sub-agent events:** Propagate events from nested agents (e.g., EbookLoaderAgent).

