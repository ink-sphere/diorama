"""Manual, opt-in live smoke test for EbookLoaderAgent.

Not part of the pytest suite (offline-only per project convention) — this exercises
the real agent against a real model and a real EPUB, and costs real API tokens.
Requires OPENROUTER_API_KEY (or another litellm-recognised key) in .env.

Usage:
    uv run python scripts/smoke_test_ebook_loader.py books/as-you-like-it.epub
    uv run python scripts/smoke_test_ebook_loader.py books/iliad.epub \
        --model openrouter/openai/gpt-4o --stream --output structure.json
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

from diorama.agents import EbookLoaderAgent, EbookLoaderError
from diorama.ebook.models import StructureNode


def _print_tree(nodes: list[StructureNode], indent: int = 0) -> None:
    for node in nodes:
        label = " ".join(
            part
            for part in (node.level_type, node.number, node.title)
            if part is not None
        )
        span = f"[{node.start_block_id}-{node.end_block_id}]"
        extra = ""
        if node.is_leaf:
            length = (
                sum(len(s) for s in node.segments)
                if node.segments
                else len(node.text or "")
            )
            extra = f" ({length} chars{f', {len(node.segments)} segments' if node.segments else ''})"
        print(f"{'  ' * indent}- {label} {span}{extra}")
        if node.children:
            _print_tree(node.children, indent + 1)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("epub_path", type=Path, help="Path to the EPUB file to load.")
    parser.add_argument(
        "--model",
        dest="model_id",
        default=None,
        help="litellm model id (defaults to ReactAgent's own default).",
    )
    parser.add_argument(
        "--max-iterations", type=int, default=60, help="Turn ceiling for the run."
    )
    parser.add_argument(
        "--max-segment-length",
        type=int,
        default=1500,
        help="Leaf text pagination size.",
    )
    parser.add_argument(
        "--stream",
        action="store_true",
        help="Render agent activity to the console live.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write the full structure as JSON here.",
    )
    args = parser.parse_args()

    load_dotenv()

    agent = EbookLoaderAgent(model_id=args.model_id, max_iterations=args.max_iterations)
    try:
        structure = await agent.load(
            args.epub_path,
            stream=args.stream,
            max_segment_length=args.max_segment_length,
        )
    except EbookLoaderError as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 1

    print(f"\nTitle: {structure.title}")
    print(f"Author: {structure.author}")
    print(f"Level types: {', '.join(structure.level_types)}")
    print(
        f"Coverage: {structure.coverage.assigned_blocks}/{structure.coverage.total_blocks} "
        f"blocks (covered={structure.coverage.covered})"
    )
    print(f"Cost: ${structure.cost_usd:.4f}\n")
    _print_tree(structure.root)

    if args.output:
        args.output.write_text(structure.model_dump_json(indent=2))
        print(f"\nFull structure written to {args.output}")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
