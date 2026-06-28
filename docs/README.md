# Diorama Documentation Index

This directory contains design and implementation documentation for Diorama's ebook-to-world-models system.

## Core Documentation

### Architecture & Design

- **[React Agent Callbacks](./react-agent-callbacks.md)** — Complete design specification for the callback system
  - Problem motivation and goals
  - Event model and types
  - API reference (all events with examples)
  - Built-in callbacks
  - Implementation details
  - Usage examples

- **[Callback Architecture](./callback-architecture.md)** — Visual diagrams and data flow
  - High-level system architecture
  - Event emission timeline
  - Callback registration and initialization
  - Event schema documentation
  - Callback execution model
  - Integration points in ReactAgent
  - Performance and testing considerations

- **[Callback Implementation Guide](./callback-implementation-guide.md)** — Step-by-step implementation instructions
  - File structure
  - Complete pseudo-code for `callback.py`
  - All modifications needed to `react.py`
  - Test strategy and examples
  - Integration checklist
  - Edge cases and error handling
  - Rollout plan

### Feature-Specific Documentation

- **[EbookLoaderAgent — Dynamic Structure Extraction](./ebook-loader-agent.md)**
  - How EPUB files are parsed and structured
  - Three-step pipeline: parse → decide → build
  - Design of the EbookLoaderAgent
  - Tools and prompts for structure extraction

---

## Reading Guide

### For Contributors Implementing the Callback System

1. Start with **React Agent Callbacks** (sections 1–3) to understand the problem and design
2. Skim **Callback Architecture** (sections 1–2) for a visual overview
3. Use **Callback Implementation Guide** as your step-by-step blueprint
4. Refer back to **React Agent Callbacks** (section 3) for event type details

### For Users Instrumenting Agent Runs

1. Read **React Agent Callbacks** (sections 4–6) for API and built-in callbacks
2. Review **Callback Architecture** (section 5) for data flow examples
3. Use **Callback Implementation Guide** (section 6) for usage patterns

### For Understanding EbookLoaderAgent

- Read **EbookLoaderAgent — Dynamic Structure Extraction** in full

---

## Key Concepts

### Event-Driven Architecture

The callback system models agent execution as a stream of **events** (e.g., `tool_call`, `assistant_chunk`, `error`). Callbacks observe these events synchronously to log, monitor, render UI, or perform custom side effects.

**Benefits:**
- Decoupled: core agent logic independent of rendering/logging
- Composable: multiple callbacks can run simultaneously
- Extensible: add custom behavior without patching the agent

### Callback Model

A callback is a simple class that implements `on_event(event: Event)`:

```python
class MyCallback(Callback):
    def _on_tool_call(self, data: dict) -> None:
        print(f"Tool: {data['tool_name']}")

agent = ReactAgent(tools=[...], callbacks=[MyCallback()])
result = await agent.run("Do X")
```

### Event Types

Core event types emitted by `ReactAgent`:

- **Initialization:** `ready`
- **Loop control:** `processing`, `turn_complete`
- **LLM interaction:** `assistant_chunk`, `assistant_stream_end`, `assistant_message`
- **Tool interaction:** `tool_call`, `tool_output`, `approval_required`
- **Errors:** `error`

See [React Agent Callbacks § 3](./react-agent-callbacks.md#3-event-reference) for detailed schema.

---

## Implementation Status

- **Status:** Design complete, ready for implementation
- **Files to create:** `diorama/core/callback.py`
- **Files to modify:** `diorama/core/react.py`, `diorama/core/__init__.py`
- **Tests:** `tests/test_callbacks.py`

See [Callback Implementation Guide § 7](./callback-implementation-guide.md#7-integration-checklist) for the full checklist.

---

## Related Files (Outside `/docs`)

- **`diorama/core/react.py`** — ReactAgent implementation (where callbacks will be integrated)
- **`diorama/core/tool.py`** — Tool base class
- **`diorama/core/router.py`** — ToolRouter (dispatches tool calls)
- **`CLAUDE.md`** — Project-level documentation (will be updated with callback usage)

---

## Design Decisions

### Why Callbacks (Not Queues)?

Coursify uses an async event queue (actor model). Diorama uses synchronous callbacks because:
- Simpler: no need for queue drain logic
- Appropriate: Diorama is a library + CLI, not a full TUI
- Flexibility: users can wrap callbacks in threads if they need async I/O

### Why Strings (Not Enums) for Event Types?

Event type is a string (e.g., `"tool_call"`) rather than an enum because:
- Extensible: new event types can be added without breaking existing callbacks
- Loosely coupled: callbacks can observe new events without code changes
- Follows Coursify's design (proven in production)

### Backward Compatibility Strategy

The existing `stream=True` parameter in `run()` is kept and continues to work:
- If `stream=True` and no callbacks are registered, a `RichLoggingCallback` is auto-added
- Existing code works unchanged
- New code can migrate to callbacks at their own pace

---

## Future Enhancements

1. **Async callbacks:** Support `async def on_event()` for non-blocking I/O
2. **Event filtering:** Allow callbacks to subscribe to specific event types only
3. **Callback priorities:** Run callbacks in a defined order
4. **Approval gateway:** Let callbacks provide approval decisions (not just observe)
5. **Sub-agent events:** Propagate events from nested agents (e.g., research agents)

---

## References

### Internal

- [CLAUDE.md](../CLAUDE.md) — Project overview and development guide
- [pyproject.toml](../pyproject.toml) — Project configuration and dependencies

### External

- [Coursify's Callback System](https://github.com/soumik12345/coursify/blob/main/coursify/callback.py) — Reference implementation
- [Coursify's Event Model](https://github.com/soumik12345/coursify/blob/main/coursify/core/events.py) — Event design
- [Rich Library](https://rich.readthedocs.io/) — Console rendering (used by RichLoggingCallback)

---

## Contributing

When implementing the callback system:

1. Review all three design documents in order (Callbacks → Architecture → Implementation Guide)
2. Follow the step-by-step checklist in the Implementation Guide
3. Write tests as you go (unit tests for each event type)
4. Ensure backward compatibility with existing code
5. Update CLAUDE.md with usage examples

Questions? Refer to the detailed documentation or the [Coursify codebase](https://github.com/soumik12345/coursify/) for reference.
