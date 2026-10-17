from __future__ import annotations

import json
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from rich.console import Console
from tau_agent import AssistantMessage
from tau_ai import AssistantDoneEvent, FakeProvider
from typer.testing import CliRunner

from diorama.agents import AgentTraceEvent, RunEvent
from scripts import profile_characters as script


@pytest.mark.parametrize(
    "flags,trace", [([], False), (["--trace"], True), (["--no-trace"], False)]
)
def test_trace_cli_is_opt_in(monkeypatch, flags, trace):
    main = AsyncMock()
    monkeypatch.setattr(script, "main", main)
    result = CliRunner().invoke(script.app, [".ebook-runs/example", *flags])
    assert result.exit_code == 0, result.output
    main.assert_awaited_once_with(
        Path(".ebook-runs/example"),
        None,
        trace=trace,
        thinking_effort=None,
        resume=None,
        max_nodes_group=1,
    )


def emit(callback, event_type, **details):
    callback(
        AgentTraceEvent(
            "run", "CharacterProfilerAgent", "fake", 1, RunEvent(event_type, details)
        )
    )


@pytest.mark.parametrize("width", [60, 80])
def test_progress_renders_counts_and_safe_titles_at_small_widths(width):
    output = StringIO()
    console = Console(file=output, width=width, color_system=None)
    progress = script.create_progress(console)
    callback = script.ProfilingProgressCallback(progress)
    with progress:
        emit(callback, "profiling_start", total_nodes=3)
        emit(
            callback,
            "node_start",
            node_index=2,
            path=("Part One", "[red]A long chapter title[/red]" * 5),
        )
        task = progress.tasks[0]
        assert task.completed == 1
        assert task.total == 3
        assert "[red]" in task.description
    rendered = output.getvalue()
    assert "1/3 nodes" in rendered
    assert "[red]" in rendered
    assert "\x1b[" not in rendered
    assert all(len(line) <= width for line in rendered.splitlines())


@pytest.mark.parametrize(
    "event_type,label",
    [
        ("run_error", "failed"),
        ("run_cancelled", "cancelled"),
    ],
)
def test_progress_preserves_partial_count_on_failure(event_type, label):
    progress = script.create_progress(Console(file=StringIO()))
    callback = script.ProfilingProgressCallback(progress)
    emit(callback, "profiling_start", total_nodes=2)
    emit(callback, "node_end", node_index=1, path=("Chapter One",))
    emit(callback, event_type)
    assert progress.tasks[0].completed == 1
    assert label in progress.tasks[0].description


def test_progress_reports_reconciliation_separately_from_finished_nodes():
    progress = script.create_progress(Console(file=StringIO()))
    callback = script.ProfilingProgressCallback(progress)
    emit(callback, "profiling_start", total_nodes=1)
    emit(callback, "node_end", node_index=1, path=("Chapter One",))
    emit(callback, "reconciliation_start")
    assert progress.tasks[0].completed == 1
    assert progress.tasks[0].description == "Reconciling characters"
    emit(callback, "profiles_ready")
    assert progress.tasks[0].description == "Saving character profiles"
    emit(callback, "run_end")
    assert progress.tasks[0].description == "Character profiling complete"


@pytest.mark.parametrize("empty", [False, True])
async def test_main_shows_progress_without_traces_and_closes_provider(
    tmp_path, monkeypatch, capsys, empty
):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / ".ebook-runs" / "existing-run" / "storybook.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "id": "book",
                "metadata": {"title": "Demo"},
                "structure": []
                if empty
                else [
                    {
                        "structure_type": "chapter",
                        "structure_title": "One",
                        "is_part_of_narrative": True,
                        "content": [
                            {
                                "raw_text": "Private story text.",
                                "markdown_text": "Private story text.",
                            }
                        ],
                    }
                ],
            }
        )
    )
    provider = FakeProvider(
        [
            [
                AssistantDoneEvent(
                    reason="stop",
                    message=AssistantMessage(content='{"characters": []}'),
                )
            ]
        ]
    )
    provider.aclose = AsyncMock()
    monkeypatch.setattr(script, "ensure_login", AsyncMock(return_value=object()))
    monkeypatch.setattr(script, "load_provider_settings", lambda: object())
    monkeypatch.setattr(
        script,
        "resolve_provider_selection",
        lambda *args, **kwargs: SimpleNamespace(provider="fake", model="fake"),
    )
    monkeypatch.setattr(
        script, "create_model_provider", lambda *args, **kwargs: provider
    )
    rich_trace = Mock()
    monkeypatch.setattr(script, "RichTraceCallback", rich_trace)
    real_agent = script.CharacterProfilerAgent

    def create_agent(**options):
        assert not any(p.is_dir() for p in path.parent.iterdir())
        assert options.get("workspace_root") is None
        return real_agent(**options)

    agent_factory = Mock(side_effect=create_agent)
    monkeypatch.setattr(script, "CharacterProfilerAgent", agent_factory)

    await script.main(path.parent.relative_to(tmp_path), None, max_nodes_group=3)

    assert agent_factory.call_args.kwargs["max_nodes_group"] == 3
    rich_trace.assert_not_called()
    provider.aclose.assert_awaited_once()
    output = capsys.readouterr()
    assert "Profiled 0 characters in Demo" in output.out
    assert "Character profiling complete" in output.err
    assert ("0/0 nodes" if empty else "1/1 nodes") in output.err
    assert "Private story text" not in output.out + output.err
    assert "\x1b[" not in output.err
    output_path = path.with_name("character-profiles.json")
    assert json.loads(output_path.read_text()) == []
    assert str(output_path) in output.out
    assert not (tmp_path / ".character-runs").exists()
    checkpoint = next(path.parent.glob("character-profiles-checkpoint-*.json"))
    assert {p.name for p in path.parent.iterdir()} == {
        "storybook.json",
        "character-profiles.json",
        checkpoint.name,
    }
    assert "Checkpoint:" in output.err
    assert checkpoint.name in output.err.replace("\n", "")
    state = json.loads(checkpoint.read_text())
    assert state["reconciled"] is True
    assert state["completed_nodes"] == (0 if empty else 1)
    previous_calls = len(provider.calls)
    output_path.write_text("previous profiles")
    await script.main(path.parent, None, resume=checkpoint)
    assert len(provider.calls) == previous_calls
    assert json.loads(output_path.read_text()) == []
    assert path.exists()
    assert {p.name for p in path.parent.iterdir()} == {
        "storybook.json",
        "character-profiles.json",
        checkpoint.name,
    }
    monkeypatch.setattr(
        script,
        "CharacterProfilerAgent",
        Mock(
            return_value=SimpleNamespace(
                run=AsyncMock(side_effect=RuntimeError("failed"))
            )
        ),
    )
    with pytest.raises(RuntimeError, match="failed"):
        await script.main(path.parent, None)
    assert json.loads(output_path.read_text()) == []
    assert {p.name for p in path.parent.iterdir()} == {
        "storybook.json",
        "character-profiles.json",
        checkpoint.name,
    }
    assert not (tmp_path / ".character-runs").exists()


def test_resume_cli_passes_checkpoint_path(monkeypatch):
    main = AsyncMock()
    monkeypatch.setattr(script, "main", main)
    result = CliRunner().invoke(
        script.app, [".ebook-runs/example", "--resume", "saved.json"]
    )
    assert result.exit_code == 0, result.output
    main.assert_awaited_once_with(
        Path(".ebook-runs/example"),
        None,
        trace=False,
        thinking_effort=None,
        resume=Path("saved.json"),
        max_nodes_group=1,
    )


def test_resumed_progress_starts_at_saved_position():
    progress = script.create_progress(Console(file=StringIO()))
    callback = script.ProfilingProgressCallback(progress)
    emit(callback, "profiling_start", total_nodes=17, completed_nodes=9)
    assert progress.tasks[0].total == 17
    assert progress.tasks[0].completed == 9


@pytest.mark.parametrize("maximum", [1, 3])
def test_max_nodes_group_cli_passes_limit(monkeypatch, maximum):
    main = AsyncMock()
    monkeypatch.setattr(script, "main", main)
    result = CliRunner().invoke(
        script.app, [".ebook-runs/example", "--max-nodes-group", str(maximum)]
    )
    assert result.exit_code == 0, result.output
    assert main.await_args.kwargs["max_nodes_group"] == maximum


@pytest.mark.parametrize("maximum", ["0", "-1", "1.5", "many"])
def test_invalid_group_limit_rejected_before_main(monkeypatch, maximum):
    main = AsyncMock()
    monkeypatch.setattr(script, "main", main)
    result = CliRunner().invoke(
        script.app, [".ebook-runs/example", "--max-nodes-group", maximum]
    )
    assert result.exit_code == 2
    assert "--max-nodes-group" in result.output
    main.assert_not_awaited()


def test_group_progress_counts_only_successful_nodes():
    progress = script.create_progress(Console(file=StringIO()))
    callback = script.ProfilingProgressCallback(progress)
    emit(callback, "profiling_start", total_nodes=7, completed_nodes=2)
    emit(callback, "group_start", first_node_index=3, last_node_index=5)
    assert progress.tasks[0].completed == 2
    assert progress.tasks[0].description == "Reading nodes 3–5"
    emit(callback, "group_end", first_node_index=3, last_node_index=5)
    assert progress.tasks[0].completed == 5
    emit(callback, "group_start", first_node_index=6, last_node_index=7)
    emit(callback, "run_error")
    assert progress.tasks[0].completed == 5
