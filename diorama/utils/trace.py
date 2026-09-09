"""Optional, terminal-safe Rich presentation of agent events."""

import json
import re
from time import monotonic

from rich.console import Console
from rich.status import Status
from rich.text import Text
from tau_agent.messages import AssistantMessage

from diorama.utils.trace_events import DioramaAgentEvent, TraceEvent


def _safe(value: str) -> str:
    # Book text is untrusted, including ANSI/OSC terminal control sequences.
    return re.sub(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]", "", value)


class TraceDisplayCallback:
    """Synchronous event sink. Use one context per sequential run.

    Defaults to stderr, bounded previews, and no animation on redirected output.
    Only provider-exposed text is displayed; signatures and raw messages are not.
    """

    def __init__(
        self,
        *,
        console: Console | None = None,
        full_excerpts: bool = False,
        preview_chars: int = 800,
    ):
        if preview_chars < 1:
            raise ValueError("preview_chars must be positive")
        self.console = console or Console(stderr=True)
        self.full_excerpts = full_excerpts
        self.preview_chars = preview_chars
        self._status: Status | None = None
        self._started = monotonic()
        self._turn = 0
        self._tools: dict[str, float] = {}

    def __enter__(self):
        self._started = monotonic()
        self._turn = 0
        self._tools.clear()
        if self.console.is_terminal and not self.console.is_dumb_terminal:
            self._status = self.console.status("Waiting for agent", spinner="dots")
            self._status.start()
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()
        return False

    def close(self) -> None:
        if self._status is not None:
            self._status.stop()
            self._status = None
        self._tools.clear()

    def _line(self, label: str, value: str = "", style: str = "cyan") -> None:
        text = Text(f"{monotonic() - self._started:7.1f}s  ", style="dim")
        text.append(label, style=style)
        if value:
            text.append("  " + _safe(value))
        self.console.print(text)
        if self._status:
            self._status.update(Text(_safe(label)))

    def _preview(self, value: object) -> None:
        text = (
            value
            if isinstance(value, str)
            else json.dumps(value, ensure_ascii=False, indent=2, default=str)
        )
        text = _safe(text)
        if not self.full_excerpts and len(text) > self.preview_chars:
            text = (
                text[: self.preview_chars]
                + "\n... [preview truncated; use --trace-full]"
            )
        self.console.print(Text(text, style="dim"))

    def __call__(self, event: TraceEvent) -> None:
        kind = event.type
        if isinstance(event, DioramaAgentEvent):
            style = (
                "red"
                if event.phase == "error" or kind == "load_error"
                else "yellow"
                if event.phase == "cancelled" or kind == "load_cancelled"
                else "cyan"
            )
            self._line(kind, event.message, style)
            if event.details:
                self._preview(event.details)
            if event.phase in {"complete", "error", "cancelled"} or kind in {
                "load_complete",
                "load_error",
                "load_cancelled",
            }:
                self.close()
        elif kind == "turn_start":
            self._turn += 1
            self._line(f"Turn {self._turn}")
        elif kind == "message_update" and self._status:
            update = event.assistant_message_event
            if update.type == "text_delta":
                # Ephemeral preview; print the complete bounded response only once.
                self._status.update(Text("Assistant: " + _safe(update.delta)[-120:]))
            elif update.type == "thinking_delta":
                self._status.update(Text("Receiving provider reasoning summary"))
            elif update.type == "toolcall_delta":
                self._status.update(Text("Preparing tool call"))
        elif kind == "tool_execution_start":
            self._tools[event.tool_call_id] = monotonic()
            self._line("Tool", event.tool_name)
            self._preview(event.args)
        elif kind == "tool_execution_end":
            elapsed = monotonic() - self._tools.pop(event.tool_call_id, monotonic())
            self._line(
                "ERROR" if event.is_error else "OK",
                f"{event.tool_name} ({elapsed:.2f}s)",
                "red" if event.is_error else "green",
            )
            content = event.result.content
            if isinstance(content, list):
                content = "\n".join(
                    block.text
                    for block in content
                    if getattr(block, "type", None) == "text"
                )
            self._preview(content)
        elif kind == "message_end" and isinstance(event.message, AssistantMessage):
            for block in event.message.content:
                if block.type == "text":
                    self._line("Assistant")
                    self._preview(block.text)
                elif block.type == "thinking" and not block.redacted:
                    self._line("Provider reasoning summary")
                    self._preview(block.thinking)
            usage = event.message.usage
            if usage.total_tokens or usage.input or usage.output:
                self._line(
                    "Usage",
                    f"input={usage.input} output={usage.output} cache_read={usage.cache_read} total={usage.total_tokens}",
                )
            if event.message.error_message:
                self._line("ERROR", event.message.error_message, "red")
