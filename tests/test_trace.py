"""Credential-free trace display and lifecycle checks."""

import asyncio
from io import StringIO

import pytest
from rich.console import Console
from tau_agent.events import (
    MessageEndEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
)
from tau_agent.messages import AssistantMessage, TextContent, Usage
from tau_agent.tools import AgentToolResult
from test_ebook_loader_source import epub_file, plan_for, provider_for

from diorama.agents import EbookLoaderAgent, EbookLoadError
from diorama.utils.ebook_source import EbookSource
from diorama.utils.trace import TraceDisplayCallback
from diorama.utils.trace_events import LoaderEvent


@pytest.mark.parametrize("width", [60, 80])
def test_display_bounded_plain_and_terminal_safe(width):
    output = StringIO()
    with TraceDisplayCallback(
        console=Console(file=output, width=width, color_system=None), preview_chars=40
    ) as trace:
        trace(LoaderEvent("load_start", "[bold]日本語\x1b[2J"))
        trace(
            ToolExecutionStartEvent(
                tool_call_id="1", tool_name="inspect_units", args={"unit": 2}
            )
        )
        trace(
            ToolExecutionEndEvent(
                tool_call_id="1",
                tool_name="inspect_units",
                result=AgentToolResult(content="x" * 100),
                is_error=False,
            )
        )
        trace(
            MessageEndEvent(
                message=AssistantMessage(
                    content=[TextContent(text="Hello")],
                    usage=Usage(input=4, output=3, total_tokens=7),
                )
            )
        )
        trace(LoaderEvent("load_complete", "Saved"))
    result = output.getvalue()
    assert "[bold]日本語" in result
    assert "\x1b" not in result
    assert "truncated" in result and "x" * 100 not in result
    assert "inspect_units" in result and "OK" in result
    assert "total=7" in result and result.count("Hello") == 1
    assert trace._status is None and not trace._tools


def test_full_excerpts_and_exception_cleanup():
    output = StringIO()
    trace = TraceDisplayCallback(
        console=Console(file=output, width=80), full_excerpts=True, preview_chars=2
    )
    with pytest.raises(ValueError), trace:
        trace(LoaderEvent("source_ready", "Book", {"text": "Complete excerpt"}))
        raise ValueError("failure")
    assert "Complete excerpt" in output.getvalue()
    assert trace._status is None


def test_interactive_cleanup_restores_cursor():
    output = StringIO()
    trace = TraceDisplayCallback(
        console=Console(file=output, width=60, force_terminal=True)
    )
    with pytest.raises(KeyboardInterrupt), trace:
        assert trace._status is not None
        raise KeyboardInterrupt()
    assert trace._status is None
    assert "\x1b[?25h" in output.getvalue()


async def test_loader_events_include_retry_and_publication(tmp_path):
    path = epub_file(tmp_path, ['<h1 id="chapter">Chapter</h1><p>Text</p>'])
    source = EbookSource(path)
    plan = plan_for(source)
    bad = {"front_matter": [], "content": [], "back_matter": []}
    events = []
    agent = EbookLoaderAgent(
        provider_for(bad, plan),
        "scripted",
        output_dir=tmp_path / "out",
        on_event=events.append,
    )
    await agent.load(path)
    kinds = [event.type for event in events]
    assert kinds[:2] == ["load_start", "source_ready"]
    assert kinds[-2:] == ["publish_start", "load_complete"]
    results = [event for event in events if event.type == "tool_execution_end"]
    assert [event.is_error for event in results] == [True, False]
    assert (agent._destination(path, source.sha256) / "book.json").exists()


async def test_callback_failure_does_not_break_loading(tmp_path):
    path = epub_file(tmp_path, ["<p>Text</p>"])
    source = EbookSource(path)
    calls = []

    def broken(event):
        calls.append(event)
        raise ValueError("private callback error")

    agent = EbookLoaderAgent(
        provider_for(plan_for(source)),
        "scripted",
        output_dir=tmp_path / "out",
        on_event=broken,
    )
    with pytest.warns(RuntimeWarning, match="tracing disabled"):
        await agent.load(path)
    assert len(calls) == 1


async def test_error_and_cancellation_events(tmp_path, monkeypatch):
    events = []
    agent = EbookLoaderAgent(provider_for(), "scripted", on_event=events.append)
    with pytest.raises(EbookLoadError):
        await agent.load(tmp_path / "missing.epub")
    assert events[-1].type == "load_error"

    async def cancelled(path):
        raise asyncio.CancelledError()

    monkeypatch.setattr(agent, "_load", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await agent.load("unused.epub")
    assert events[-1].type == "load_cancelled"
