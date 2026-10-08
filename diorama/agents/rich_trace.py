from __future__ import annotations

import json
from dataclasses import dataclass, field

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text
from tau_agent.events import (
    MessageEndEvent,
    MessageUpdateEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    TurnStartEvent,
)
from tau_agent.messages import (
    AssistantMessage,
    TextContent,
    ThinkingContent,
    ToolCall,
    UserMessage,
)
from tau_agent.provider_events import (
    TextDeltaEvent,
    TextEndEvent,
    ThinkingDeltaEvent,
    ThinkingEndEvent,
)

from diorama.agents.base import AgentTraceEvent, RunEvent


@dataclass
class _DisplayState:
    turn: int = 0
    blocks: dict[int, str] = field(default_factory=dict)
    tools: dict[str, str] = field(default_factory=dict)
    calls: set[str] = field(default_factory=set)


class RichTraceCallback:
    """Append-only trace preserving streamed text in scrollback and redirected logs."""

    def __init__(self, *, console: Console | None = None) -> None:
        self.console = console if console is not None else Console(stderr=True)
        self._runs: dict[str, _DisplayState] = {}
        self._open_stream: tuple[str, str] | None = None

    def _finish_stream(self) -> None:
        if self._open_stream is not None:
            self.console.print()
            self._open_stream = None

    def _heading(self, trace: AgentTraceEvent, label: str, style: str = "cyan") -> None:
        self._finish_stream()
        self.console.rule(
            Text(f"{trace.agent_name} · {trace.run_id[:8]} · {label}", style=style)
        )

    def _stream(self, trace: AgentTraceEvent, key: str, label: str, text: str) -> None:
        if not text:
            return
        identity = (trace.run_id, key)
        if self._open_stream != identity:
            self._heading(trace, label)
            self._open_stream = identity
        self.console.print(Text(text), end="", soft_wrap=True)

    def _block(
        self,
        trace: AgentTraceEvent,
        state: _DisplayState,
        index: int,
        label: str,
        text: str,
    ) -> None:
        previous = state.blocks.get(index, "")
        remainder = text[len(previous) :] if text.startswith(previous) else text
        self._stream(trace, f"message:{index}", label, remainder)
        state.blocks[index] = text

    def _tool_result(
        self,
        trace: AgentTraceEvent,
        state: _DisplayState,
        tool_id: str,
        name: str,
        text: str,
    ) -> None:
        previous = state.tools.get(tool_id, "")
        remainder = text[len(previous) :] if text.startswith(previous) else text
        self._stream(trace, f"tool:{tool_id}", f"{name} output · {tool_id}", remainder)
        state.tools[tool_id] = text

    def __call__(self, trace: AgentTraceEvent) -> None:
        event = trace.event
        state = self._runs.setdefault(trace.run_id, _DisplayState())
        if isinstance(event, RunEvent):
            if event.type == "run_start":
                self._heading(trace, f"Run started · {trace.model}")
            elif event.type in {"run_end", "run_error", "run_cancelled"}:
                self._heading(
                    trace,
                    event.type.replace("_", " ").title(),
                    "green" if event.type == "run_end" else "red",
                )
                error = event.details.get("error")
                if error:
                    self.console.print(
                        Text(f"{event.details['error_type']}: {error}", style="red")
                    )
                self.console.print(
                    Text(f"Elapsed: {event.details['duration']:.2f}s", style="dim")
                )
                self._runs.pop(trace.run_id, None)
            else:
                self._heading(trace, event.type.replace("_", " ").title(), "yellow")
                self.console.print(
                    Syntax(
                        json.dumps(
                            event.details, indent=2, default=str, ensure_ascii=False
                        ),
                        "json",
                        word_wrap=True,
                    )
                )
        elif isinstance(event, TurnStartEvent):
            state.turn += 1
            self._heading(trace, f"Turn {state.turn}")
        elif event.type == "message_start":
            state.blocks.clear()
        elif isinstance(event, MessageUpdateEvent):
            update = event.assistant_message_event
            if isinstance(update, (TextDeltaEvent, ThinkingDeltaEvent)):
                label = (
                    "Assistant" if isinstance(update, TextDeltaEvent) else "Thinking"
                )
                previous = state.blocks.get(update.content_index, "")
                self._block(
                    trace, state, update.content_index, label, previous + update.delta
                )
            elif isinstance(update, (TextEndEvent, ThinkingEndEvent)):
                self._block(
                    trace,
                    state,
                    update.content_index,
                    "Assistant" if isinstance(update, TextEndEvent) else "Thinking",
                    update.content,
                )
        elif isinstance(event, MessageEndEvent):
            message = event.message
            if isinstance(message, AssistantMessage):
                for index, block in enumerate(message.content):
                    if isinstance(block, TextContent):
                        self._block(trace, state, index, "Assistant", block.text)
                    elif isinstance(block, ThinkingContent):
                        self._block(
                            trace,
                            state,
                            index,
                            "Thinking",
                            "[redacted]" if block.redacted else block.thinking,
                        )
                    elif isinstance(block, ToolCall):
                        self._arguments(trace, block.name, block.id, block.arguments)
                        state.calls.add(block.id)
                self._finish_stream()
                self.console.print(
                    Text(
                        f"Response: {message.stop_reason} · tokens in {message.usage.input}, out {message.usage.output}, cached {message.usage.cache_read}",
                        style="dim",
                    )
                )
                if message.error_message:
                    self.console.print(Text(message.error_message, style="red"))
            elif isinstance(message, UserMessage):
                self._heading(trace, "User")
                self.console.print(Text(message.text))
            state.blocks.clear()
        elif isinstance(event, ToolExecutionStartEvent):
            if event.tool_call_id not in state.calls:
                self._arguments(trace, event.tool_name, event.tool_call_id, event.args)
            else:
                self._heading(
                    trace, f"Executing · {event.tool_name} · {event.tool_call_id}"
                )
            state.calls.discard(event.tool_call_id)
        elif isinstance(event, (ToolExecutionUpdateEvent, ToolExecutionEndEvent)):
            result = (
                event.partial_result
                if isinstance(event, ToolExecutionUpdateEvent)
                else event.result
            )
            self._tool_result(
                trace, state, event.tool_call_id, event.tool_name, result.text
            )
            if isinstance(event, ToolExecutionEndEvent):
                self._finish_stream()
                for block in result.content:
                    if block.type == "image":
                        self.console.print(
                            Text(
                                f"Image: {block.mime_type} (binary content omitted)",
                                style="dim",
                            )
                        )
                if result.details is not None:
                    self.console.print(
                        Syntax(
                            json.dumps(
                                result.details,
                                indent=2,
                                default=str,
                                ensure_ascii=False,
                            ),
                            "json",
                            word_wrap=True,
                        )
                    )
                self.console.print(
                    Text(
                        f"{event.tool_name}: {'ERROR' if event.is_error else 'OK'}",
                        style="red" if event.is_error else "green",
                    )
                )
                state.tools.pop(event.tool_call_id, None)
        elif event.type not in {
            "agent_start",
            "agent_end",
            "agent_settled",
            "turn_end",
            "entry_appended",
        }:
            self._heading(trace, event.type.replace("_", " ").title(), "yellow")
            self.console.print(
                Syntax(event.model_dump_json(indent=2), "json", word_wrap=True)
            )

    def _arguments(
        self, trace: AgentTraceEvent, name: str, call_id: str, arguments: object
    ) -> None:
        self._heading(trace, f"Tool · {name} · {call_id}")
        self.console.print(
            Panel(
                Syntax(
                    json.dumps(arguments, indent=2, ensure_ascii=False),
                    "json",
                    word_wrap=True,
                ),
                title="Arguments",
                border_style="dim",
            )
        )
