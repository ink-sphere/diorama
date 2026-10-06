import argparse
import asyncio
from pathlib import Path

from tau_coding import (
    FileCredentialStore,
    load_provider_settings,
    resolve_provider_selection,
)
from tau_coding.oauth import login_openai_codex
from tau_coding.oauth_types import OAuthPrompt
from tau_coding.provider_runtime import create_model_provider

from diorama.agents.ebook_structure_agent import EbookStructureAgent


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


async def main(ebook_path: Path, model: str | None) -> None:
    credentials = await ensure_login()
    selection = resolve_provider_selection(
        load_provider_settings(), provider_name="openai-codex", model=model
    )
    provider = create_model_provider(
        selection.provider, credential_store=credentials, model=selection.model
    )
    try:
        agent = EbookStructureAgent(
            provider=provider,
            model=selection.model,
            workspace_root=".ebook-runs",
        )
        storybook = await agent.run(ebook_path)
        print(f"Extracted {storybook.metadata.title} ({storybook.id})")
    finally:
        await provider.aclose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("ebook", type=Path, nargs="?", default=Path("book.epub"))
    parser.add_argument("--model", help="Override Tau's configured Codex model")
    args = parser.parse_args()
    asyncio.run(main(args.ebook, args.model))
