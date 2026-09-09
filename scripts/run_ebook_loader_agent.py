"""Run EbookLoaderAgent with OpenAI Codex subscription authentication."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from tau_coding.credentials import FileCredentialStore
from tau_coding.provider_config import (
    ProviderConfigError,
    load_provider_settings,
    resolve_provider_selection,
)
from tau_coding.provider_runtime import create_model_provider

from diorama.agents import EbookLoaderAgent
from diorama.utils.auth import login
from diorama.utils.ebook_source import EbookLoadError

PROVIDER_NAME = "openai-codex"
DEFAULT_MODEL = "gpt-5.6-sol"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Parse an EPUB with EbookLoaderAgent using your ChatGPT Codex allowance."
        )
    )
    parser.add_argument("epub", type=Path, help="Path to the EPUB file")
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Tau OpenAI Codex model (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(".diorama"),
        help="Directory for the parsed book and source EPUB (default: .diorama)",
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=30,
        help="Maximum Tau agent turns (default: 30)",
    )
    parser.add_argument(
        "--login",
        action="store_true",
        help="Run ChatGPT OAuth even when Tau already has stored credentials",
    )
    return parser.parse_args()


async def run(args: argparse.Namespace) -> Path:
    epub = args.epub.expanduser().resolve()
    if not epub.is_file():
        raise EbookLoadError(f"EPUB file does not exist: {epub}")
    if epub.suffix.casefold() != ".epub":
        raise EbookLoadError(f"Expected an .epub file: {epub}")

    credentials = FileCredentialStore()
    if args.login or credentials.get_oauth(PROVIDER_NAME) is None:
        await login()

    selection = resolve_provider_selection(
        load_provider_settings(),
        provider_name=PROVIDER_NAME,
        model=args.model,
    )
    provider = create_model_provider(
        selection.provider,
        model=selection.model,
        thinking_level="medium",
    )
    try:
        agent = EbookLoaderAgent(
            provider,
            selection.model,
            output_dir=args.output_dir,
            max_turns=args.max_turns,
        )
        document = await agent.load(epub)
        destination = agent._destination(epub, document.source_sha256)
    finally:
        await provider.aclose()

    author = f" by {document.author}" if document.author else ""
    print(f"Loaded {document.title}{author}")
    print(f"Saved to {destination.resolve()}")
    return destination


def main() -> int:
    args = parse_args()
    try:
        asyncio.run(run(args))
    except (EbookLoadError, ProviderConfigError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Cancelled.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
