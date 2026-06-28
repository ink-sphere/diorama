"""Callbacks — observe agent execution via event stream.

The agent emits Event objects at key points in execution (LLM calls, tool
execution, approvals, errors). Callbacks subscribe to these events to log,
monitor, render UI, or perform other side effects.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.syntax import Syntax


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
        self._console.print(f"[dim]Agent ready with {tool_count} tools.[/dim]")

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
