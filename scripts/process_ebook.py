import asyncio
from pathlib import Path
from typing import Annotated

import typer
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

from diorama.agents import RichTraceCallback
from diorama.agents.ebook_structure_agent import EbookStructureAgent

app = typer.Typer(
    add_completion=False, context_settings={"help_option_names": ["-h", "--help"]}
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


async def main(
    ebook_path: Path,
    model: str | None,
    *,
    trace: bool = True,
    thinking_effort: ThinkingLevel | None = None,
) -> None:
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
        agent = EbookStructureAgent(
            provider=provider,
            model=selection.model,
            workspace_root=".ebook-runs",
            callbacks=[RichTraceCallback()] if trace else [],
        )
        storybook = await agent.run(ebook_path)
        print(f"Extracted {storybook.metadata.title} ({storybook.id})")
    finally:
        await provider.aclose()


@app.command()
def cli(
    ebook: Annotated[Path, typer.Argument(help="Path to the ebook to extract")] = Path(
        "book.epub"
    ),
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
    no_trace: Annotated[
        bool, typer.Option("--no-trace", help="Disable terminal agent traces")
    ] = False,
) -> None:
    """Extract an ebook into a StoryBook JSON file."""
    asyncio.run(main(ebook, model, trace=not no_trace, thinking_effort=thinking_effort))


if __name__ == "__main__":
    app()
