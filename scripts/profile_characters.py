import asyncio
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Annotated
from uuid import uuid4

import typer
from pydantic import TypeAdapter
from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn
from rich.table import Column
from tau_coding import (
    FileCredentialStore,
    load_provider_settings,
    provider_thinking_levels,
    resolve_provider_selection,
)
from tau_coding.oauth import login_openai_codex
from tau_coding.oauth_types import OAuthPrompt
from tau_coding.provider_runtime import create_model_provider
from tau_coding.thinking import ThinkingLevel

from diorama.agents import AgentCallback, AgentTraceEvent, RichTraceCallback, RunEvent
from diorama.agents.character_profiler_agent import CharacterProfilerAgent
from diorama.models.character_profile import CharacterProfile
from diorama.models.storybook import StoryBook

app = typer.Typer(
    add_completion=False, context_settings={"help_option_names": ["-h", "--help"]}
)


class ProfilingProgressCallback:
    """Display completed narrative leaves, independently of model turns and repairs."""

    def __init__(self, progress: Progress) -> None:
        self.progress = progress
        self.task = progress.add_task("Preparing character profiles", total=0)

    def __call__(self, trace: AgentTraceEvent) -> None:
        event = trace.event
        if not isinstance(event, RunEvent):
            return
        if event.type == "profiling_start":
            total = event.details["total_nodes"]
            self.progress.update(
                self.task,
                total=total,
                completed=event.details.get("completed_nodes", 0),
                description="Profiling characters" if total else "No narrative nodes",
                refresh=True,
            )
        elif event.type in {"group_start", "group_end"}:
            first = event.details["first_node_index"]
            last = event.details["last_node_index"]
            finished = event.type == "group_end"
            self.progress.update(
                self.task,
                completed=last if finished else first - 1,
                description=f"{'Read' if finished else 'Reading'} nodes {first}–{last}",
                refresh=True,
            )
        elif event.type in {"node_start", "node_end"}:
            index = event.details["node_index"]
            path = event.details["path"]
            label = path[-1] if path and path[-1] else f"Untitled node {index}"
            finished = event.type == "node_end"
            self.progress.update(
                self.task,
                completed=index if finished else index - 1,
                description=f"{'Read' if finished else 'Reading'} {label}",
                refresh=True,
            )
        else:
            descriptions = {
                "reconciliation_start": "Reconciling characters",
                "profiles_ready": "Saving character profiles",
                "run_end": "Character profiling complete",
                "run_error": "Character profiling failed",
                "run_cancelled": "Character profiling cancelled",
            }
            if description := descriptions.get(event.type):
                self.progress.update(self.task, description=description, refresh=True)


def create_progress(console: Console) -> Progress:
    return Progress(
        TextColumn(
            "{task.description}",
            markup=False,
            table_column=Column(
                ratio=1, max_width=45, no_wrap=True, overflow="ellipsis"
            ),
        ),
        BarColumn(bar_width=None, table_column=Column(ratio=1)),
        TextColumn("{task.completed:.0f}/{task.total:.0f} nodes"),
        TimeElapsedColumn(),
        console=console,
        expand=True,
        auto_refresh=console.is_terminal,
        refresh_per_second=4,
        redirect_stdout=False,
        redirect_stderr=False,
    )


async def prompt_login(prompt: OAuthPrompt) -> str:
    return await asyncio.to_thread(input, prompt.message + " ")


async def ensure_login() -> FileCredentialStore:
    store = FileCredentialStore()
    if store.get_oauth("openai-codex") is None:
        credential = await login_openai_codex(
            on_auth=lambda info: print(f"Sign in: {info.url}"),
            on_prompt=prompt_login,
        )
        store.set_oauth("openai-codex", credential)
    return store


def save_profiles(path: Path, profiles: list[CharacterProfile]) -> None:
    """Replace the JSON atomically without creating an output directory."""
    content = TypeAdapter(list[CharacterProfile]).dump_json(profiles, indent=2)
    staged: Path | None = None
    try:
        with NamedTemporaryFile(
            dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as stream:
            staged = Path(stream.name)
            stream.write(content)
        staged.replace(path)
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)


async def main(
    run_directory: Path,
    model: str | None,
    *,
    trace: bool = False,
    thinking_effort: ThinkingLevel | None = None,
    resume: Path | None = None,
    max_nodes_group: int = 1,
) -> None:
    run_directory = run_directory.expanduser().resolve()
    if not run_directory.is_dir():
        raise typer.BadParameter(
            f"Expected an ebook run directory: {run_directory}",
            param_hint="RUN_DIRECTORY",
        )
    storybook_path = run_directory / "storybook.json"
    if not storybook_path.is_file():
        raise typer.BadParameter(
            f"Run directory must contain storybook.json: {run_directory}",
            param_hint="RUN_DIRECTORY",
        )
    output_path = run_directory / "character-profiles.json"
    data = await asyncio.to_thread(storybook_path.read_bytes)
    storybook = StoryBook.model_validate_json(data)
    selection = resolve_provider_selection(
        load_provider_settings(), provider_name="openai-codex", model=model
    )
    if thinking_effort is not None:
        available = provider_thinking_levels(selection.provider, model=selection.model)
        if thinking_effort not in available:
            raise typer.BadParameter(
                f"{selection.model} supports: {', '.join(available) or 'no thinking levels'}",
                param_hint="--thinking-effort",
            )
    credentials = await ensure_login()
    provider = create_model_provider(
        selection.provider,
        credential_store=credentials,
        model=selection.model,
        thinking_level=thinking_effort,
    )
    try:
        console = Console(stderr=True)
        checkpoint = (
            resume.expanduser().resolve()
            if resume is not None
            else run_directory / f"character-profiles-checkpoint-{uuid4().hex}.json"
        )
        console.print(f"Checkpoint: {checkpoint}", markup=False)
        with create_progress(console) as progress:
            callbacks: list[AgentCallback] = [ProfilingProgressCallback(progress)]
            if trace:
                callbacks.append(RichTraceCallback(console=console))
            agent = CharacterProfilerAgent(
                provider=provider,
                model=selection.model,
                callbacks=callbacks,
                max_nodes_group=max_nodes_group,
            )
            profiles = await agent.run(
                storybook, checkpoint_path=checkpoint, resume=resume is not None
            )
            await asyncio.to_thread(save_profiles, output_path, profiles)
        print(
            f"Profiled {len(profiles)} characters in "
            f"{storybook.metadata.title} ({storybook.id}). Saved to {output_path}"
        )
    finally:
        await provider.aclose()


@app.command()
def cli(
    run_directory: Annotated[
        Path,
        typer.Argument(
            help="Ebook run directory containing storybook.json (e.g. .ebook-runs/<run-id>)"
        ),
    ],
    model: Annotated[
        str | None,
        typer.Option("--model", help="Override Tau's configured Codex model"),
    ] = None,
    thinking_effort: Annotated[
        ThinkingLevel | None,
        typer.Option(
            "--thinking-effort",
            help="Thinking effort supported by the selected model; omitted uses its default. "
            "For Codex, off leaves effort unspecified.",
        ),
    ] = None,
    trace: Annotated[
        bool,
        typer.Option("--trace/--no-trace", help="Show detailed terminal agent traces"),
    ] = False,
    resume: Annotated[
        Path | None,
        typer.Option(
            "--resume", help="Resume from a character-profiling checkpoint JSON file."
        ),
    ] = None,
    max_nodes_group: Annotated[
        int,
        typer.Option(
            "--max-nodes-group",
            min=1,
            help="Maximum consecutive narrative nodes per model request, within the input size limit.",
        ),
    ] = 1,
) -> None:
    """Read storybook.json and save character-profiles.json in the run directory."""
    asyncio.run(
        main(
            run_directory,
            model,
            trace=trace,
            thinking_effort=thinking_effort,
            resume=resume,
            max_nodes_group=max_nodes_group,
        )
    )


if __name__ == "__main__":
    app()
