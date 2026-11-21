"""Assemble Geppetto's instructions, ingestion skill, and callable tools."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from sysconfig import get_path

from diorama.agent.tools import inspect_epub, read_passage, validate_hierarchy


@dataclass(frozen=True)
class Geppetto:
    """Resources used to instruct a worker and identify its Python tools."""

    instructions: str
    skill_path: Path
    tools: tuple[Callable, ...]

    @classmethod
    def load(cls, resources: Path | None = None) -> "Geppetto":
        """Load the installed resources and bind the three EPUB tools.

        Supply a resource directory containing `instructions.md` and
        `skills/ingest-epub/SKILL.md` to use a particular copy. By default
        these files come from the Python environment's shared data directory.
        """
        if resources is None:
            resources = Path(get_path("data")) / "share/diorama/geppetto"
        skill = resources / "skills/ingest-epub/SKILL.md"
        return cls(
            (resources / "instructions.md").read_text(encoding="utf-8").strip(),
            skill.resolve(strict=True),
            (inspect_epub, read_passage, validate_hierarchy),
        )
