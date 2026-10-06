from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from diorama.agents.ebook_structure_agent.source import (
    StructurePlan,
    materialize_storybook,
    parse_ebook,
)
from diorama.agents.ebook_structure_agent.validation import load_storybook


def main() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    index = commands.add_parser("index")
    index.add_argument("source", type=Path)
    index.add_argument("--output", type=Path, required=True)
    build = commands.add_parser("build")
    build.add_argument("source", type=Path)
    build.add_argument("--plan", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("source", type=Path)
    validate.add_argument("--book", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "index":
        source = parse_ebook(args.source)
        payload = {
            "id": source.id,
            "metadata": source.metadata.model_dump(mode="json"),
            "blocks": [asdict(block) for block in source.blocks],
            "documents": [
                {
                    key: value
                    for key, value in asdict(doc).items()
                    if key != "raw_content"
                }
                for doc in source.documents
            ],
            "toc": [asdict(entry) for entry in source.toc],
            "excluded_documents": source.excluded_documents,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(
            json.dumps(
                {"indexed_blocks": len(source.blocks), "output": str(args.output)}
            )
        )
    elif args.command == "build":
        plan = StructurePlan.model_validate_json(args.plan.read_bytes())
        book = materialize_storybook(plan, parse_ebook(args.source))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(book.model_dump_json(indent=2), encoding="utf-8")
        print(json.dumps({"book_id": book.id, "output": str(args.output)}))
    else:
        book = load_storybook(args.book, args.source.read_bytes(), args.source.name)
        print(json.dumps({"valid": True, "book_id": book.id}))


if __name__ == "__main__":
    main()
