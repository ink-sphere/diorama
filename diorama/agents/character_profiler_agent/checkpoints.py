from __future__ import annotations

import os
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from diorama.models.character_profile import CharacterProfile
from diorama.models.storybook import StoryBook


class ProfilingCheckpoint(BaseModel):
    """Validated registry at a completed narrative-leaf boundary."""

    model_config = ConfigDict(extra="forbid", strict=True)

    version: Literal[1, 2] = 2
    book_id: str
    storybook_sha256: str
    total_nodes: int = Field(ge=0)
    completed_nodes: int = Field(ge=0)
    batch: int = Field(ge=0)
    profiles: list[CharacterProfile]
    reconciled: bool = False

    @model_validator(mode="before")
    @classmethod
    def upgrade_legacy_profiles(cls, value: Any) -> Any:
        """Keep older snapshots readable without inventing full-body evidence."""
        if not isinstance(value, dict) or not isinstance(value.get("profiles"), list):
            return value
        return {
            **value,
            "profiles": [
                {
                    **profile,
                    "full_body_visual_prompt": (
                        "Full-body visual prompt was not generated for this legacy "
                        "profile. Rerun profiling from the beginning to extract it "
                        "from the book; this does not mean appearance is unspecified "
                        "in the source."
                    ),
                }
                if isinstance(profile, dict)
                and "full_body_visual_prompt" not in profile
                else profile
                for profile in value["profiles"]
            ],
        }

    @model_validator(mode="after")
    def validate_state(self) -> Self:
        minimum_batches = (
            self.completed_nodes if self.version == 1 else int(self.completed_nodes > 0)
        )
        if self.completed_nodes > self.total_nodes or self.batch < minimum_batches:
            raise ValueError("Invalid checkpoint reading position")
        if self.reconciled and self.completed_nodes != self.total_nodes:
            raise ValueError(
                "Reconciliation requires all narrative nodes to be completed"
            )
        if self.completed_nodes == 0 and (self.profiles or self.batch):
            raise ValueError(
                "An initial checkpoint must have an empty registry and batch"
            )
        ids = [profile.id for profile in self.profiles]
        if len(ids) != len(set(ids)) or any(not identity.strip() for identity in ids):
            raise ValueError("Checkpoint character IDs must be unique and non-empty")
        if any(profile.book_id != self.book_id for profile in self.profiles):
            raise ValueError("Checkpoint profiles belong to another book")
        return self


def storybook_fingerprint(book: StoryBook) -> str:
    """Bind a checkpoint to all normalized book content, not just its supplied ID."""
    return sha256(book.model_dump_json().encode("utf-8")).hexdigest()


def load_checkpoint(
    path: Path, book: StoryBook, total_nodes: int
) -> ProfilingCheckpoint:
    checkpoint = ProfilingCheckpoint.model_validate_json(path.read_bytes())
    if (
        checkpoint.book_id != book.id
        or checkpoint.storybook_sha256 != storybook_fingerprint(book)
        or checkpoint.total_nodes != total_nodes
    ):
        raise ValueError(
            "Checkpoint does not match this StoryBook's content and structure"
        )
    return checkpoint


def write_checkpoint(
    path: Path, checkpoint: ProfilingCheckpoint, *, overwrite: bool = True
) -> None:
    """Publish complete JSON atomically; an interrupted write keeps the last snapshot."""
    path.parent.mkdir(parents=True, exist_ok=True)
    staged: Path | None = None
    try:
        with NamedTemporaryFile(
            dir=path.parent, prefix=".checkpoint-", delete=False
        ) as stream:
            staged = Path(stream.name)
            stream.write(checkpoint.model_dump_json(indent=2).encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        if overwrite:
            os.replace(staged, path)
        else:
            # Atomic exclusive publication: a fresh run cannot overwrite an
            # existing checkpoint, including one created concurrently.
            os.link(staged, path)
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)
