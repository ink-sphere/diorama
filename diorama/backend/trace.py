"""Translate ReactAgent's typed events into shelf-card trace lines.

The agent loop (``diorama.core.react.ReactAgent``) speaks in
:mod:`diorama.core.events` — plain, UI-agnostic dataclasses. This module is the one
place that turns those into the small, human-legible :class:`~diorama.backend.models.TraceLine`
rows the shelf card renders, so the frontend never has to understand tool schemas or
event ordering.
"""

from __future__ import annotations

import time
from typing import Any

from diorama.backend.models import TraceLine
from diorama.core.events import (
    AgentEvent,
    CompactionEndEvent,
    CompactionStartEvent,
    MessageEndEvent,
    RetryEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
)

_MAX_LEN = 220

_TOOL_VERBS: dict[str, str] = {
    "get_overview": "Getting the book's overview",
    "get_toc": "Reading the table of contents",
    "list_headings": "Scanning headings for chapter boundaries",
    "search_blocks": "Searching the text",
    "read_blocks": "Reading blocks",
    "submit_structure": "Submitting the discovered structure",
    # Literary research. The three submissions read as milestones rather than moves,
    # because that is what they are — the modal pins them as a checklist beside the
    # scrolling trace, so the same rows carry both the story and the progress.
    "get_outline": "Reviewing the book's structure",
    "web_search": "Searching the web",
    "view_image": "Studying an illustration",
    "submit_author_profile": "Writing the author profile",
    "submit_world_dossier": "Compiling the world dossier",
    "submit_style_bibles": "Proposing the art directions",
}

# These tools return raw JSON (block lists, TOC entries) that the model reads but a
# human never should — the "done" line echoes the call itself, past tense, rather
# than dumping the payload. submit_structure is absent on purpose: its own result
# text is already a human sentence ("Structure accepted: ...") worth showing verbatim.
_TOOL_DONE_VERBS: dict[str, str] = {
    "get_overview": "Got the book's overview",
    "get_toc": "Read the table of contents",
    "list_headings": "Found the chapter headings",
    "search_blocks": "Searched the text",
    "read_blocks": "Read blocks",
    "get_outline": "Reviewed the book's structure",
    # A search result is a wall of scraped page text and an image fetch returns the
    # picture itself — neither is a trace row. The submissions stay absent for the
    # same reason submit_structure does: their own result text is already a human
    # sentence ("Style bibles accepted (2); …") worth showing verbatim.
    "web_search": "Searched the web",
    "view_image": "Studied an illustration",
}


def _truncate(text: str, limit: int = _MAX_LEN) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _describe_call(tool_name: str, args: dict[str, Any]) -> str:
    verb = _TOOL_VERBS.get(tool_name, tool_name)
    if (
        tool_name == "read_blocks"
        and "start_block_id" in args
        and "end_block_id" in args
    ):
        return f"{verb} {int(args['start_block_id'])}–{int(args['end_block_id'])}"
    if tool_name in ("search_blocks", "web_search") and args.get("query"):
        return f'{verb} for "{_truncate(str(args["query"]), 60)}"'
    return verb


def event_to_trace_line(event: AgentEvent) -> TraceLine | None:
    """Return the trace line ``event`` produces, or None when it's not shown.

    Most events (turn boundaries, message-start bookkeeping, streamed deltas) are
    intentionally silent — the shelf card shows a quiet, discrete log of *moves* the
    agent made, not a token-by-token console.
    """
    now = time.time()

    if isinstance(event, ToolExecutionStartEvent):
        return TraceLine(
            id=event.tool_call_id,
            kind="tool",
            status="pending",
            tool=event.tool_name,
            text=_describe_call(event.tool_name, event.args),
            at=now,
        )

    if isinstance(event, ToolExecutionEndEvent):
        if event.is_error:
            text = _truncate(event.output) or f"{event.tool_name} failed"
            return TraceLine(
                id=event.tool_call_id,
                kind="tool",
                status="error",
                tool=event.tool_name,
                text=text,
                at=now,
            )
        if event.tool_name in _TOOL_DONE_VERBS:
            text = _TOOL_DONE_VERBS[event.tool_name]
        else:
            text = _truncate(event.output) or _TOOL_VERBS.get(
                event.tool_name, event.tool_name
            )
        return TraceLine(
            id=event.tool_call_id,
            kind="tool",
            status="done",
            tool=event.tool_name,
            text=text,
            at=now,
        )

    if isinstance(event, MessageEndEvent):
        message = event.message
        reasoning = message.get("reasoning_content")
        if message.get("role") == "assistant" and reasoning:
            return TraceLine(
                id=f"thinking-{id(message)}-{now}",
                kind="thinking",
                status="done",
                text=_truncate(reasoning),
                at=now,
            )
        return None

    if isinstance(event, RetryEvent):
        # A zero delay is the empty-reply retry, not a backoff: the provider answered
        # fine, it just answered with nothing, so there is nothing to wait out and
        # "retrying in 0s" would read as a stall rather than an immediate second ask.
        if event.delay_seconds <= 0:
            text = (
                f"{event.reason.capitalize()} — asking again "
                f"({event.attempt}/{event.max_attempts})"
            )
        else:
            text = (
                f"A request hiccupped — retrying in {event.delay_seconds:.0f}s "
                f"(attempt {event.attempt}/{event.max_attempts})"
            )
        return TraceLine(
            id=f"retry-{event.attempt}-{now}",
            kind="status",
            status="pending",
            text=text,
            at=now,
        )

    if isinstance(event, CompactionStartEvent):
        return TraceLine(
            id=f"compaction-{now}",
            kind="status",
            status="pending",
            text="Tidying up its notes to keep reading",
            at=now,
        )

    if isinstance(event, CompactionEndEvent):
        return TraceLine(
            id=f"compaction-{now}",
            kind="status",
            status="done",
            text="Notes tidied — back to reading",
            at=now,
        )

    return None


__all__ = ["event_to_trace_line"]
