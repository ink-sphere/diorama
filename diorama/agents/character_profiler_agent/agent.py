from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, Iterator
from contextlib import suppress
from pathlib import Path
from typing import Annotated
from unicodedata import normalize
from uuid import NAMESPACE_URL, uuid4, uuid5

from bs4 import BeautifulSoup
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter
from tau_agent import AssistantMessage, MessageEndEvent, UserMessage, message_text
from tau_agent.loop import run_agent_loop
from tau_agent.tools import ToolCancellationToken

from diorama.agents.base import BaseDioramaAgent, RunEvent
from diorama.agents.character_profiler_agent.checkpoints import (
    ProfilingCheckpoint,
    load_checkpoint,
    storybook_fingerprint,
    write_checkpoint,
)
from diorama.agents.character_profiler_agent.prompts import (
    build_extraction_prompt,
    build_repair_prompt,
    build_system_prompt,
)
from diorama.models.character_profile import CharacterProfile
from diorama.models.storybook import StoryBook, StructureNode, TextContent

type NonEmptyText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1)
]


class CharacterProfilerError(RuntimeError):
    """Character extraction failed to produce valid profiles."""


class _CharacterUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    existing_ids: list[NonEmptyText] = Field(
        description="Short references from the CURRENT registry (e.g. c1, c2); empty for a new character."
    )
    character_name: NonEmptyText
    character_alias: list[NonEmptyText]
    character_profile_description: NonEmptyText
    headshot_visual_prompt: NonEmptyText = Field(
        description=CharacterProfile.model_fields["headshot_visual_prompt"].description
    )
    full_body_visual_prompt: NonEmptyText = Field(
        description=CharacterProfile.model_fields["full_body_visual_prompt"].description
    )


class _Updates(BaseModel):
    model_config = ConfigDict(extra="forbid")

    characters: list[_CharacterUpdate]


_PROFILES = TypeAdapter(list[CharacterProfile])


def _name_key(name: str) -> str:
    return " ".join(normalize("NFKC", name).casefold().split())


def _narrative_leaves(
    nodes: list[StructureNode], path: tuple[str, ...] = ()
) -> Iterator[tuple[tuple[str, ...], str]]:
    """Walk narrative leaves in reading order, including nested chapters."""
    for node in nodes:
        location = (*path, node.structure_title)
        children = [item for item in node.content if isinstance(item, StructureNode)]
        if children:
            yield from _narrative_leaves(children, location)
        elif node.is_part_of_narrative:
            fragments = []
            for item in node.content:
                if isinstance(item, TextContent):
                    text = item.markdown_text.strip()
                    if not text:
                        text = BeautifulSoup(item.raw_text, "html.parser").get_text(
                            " ", strip=True
                        )
                    if text:
                        fragments.append(text)
            text = "\n\n".join(fragments)
            if text:
                yield location, text


def _apply_updates(
    profiles: list[CharacterProfile],
    updates: _Updates,
    book_id: str,
    batch: int,
    *,
    reconcile: bool,
) -> list[CharacterProfile]:
    known = {f"c{index}": profile.id for index, profile in enumerate(profiles, start=1)}
    replacements: dict[str, CharacterProfile] = {}
    removed: set[str] = set()
    additions: list[CharacterProfile] = []
    claimed: set[str] = set()
    for index, update in enumerate(updates.characters):
        references = update.existing_ids
        location = f"characters[{index}] ({update.character_name[:200]!r})"
        duplicates = {
            ref for ref in references if references.count(ref) > 1 or ref in claimed
        }
        if duplicates:
            raise ValueError(
                f"{location}: a reference was used more than once: {sorted(duplicates)!r}. "
                "Combine updates for the same identity into one update."
            )
        unknown = [ref for ref in references if ref not in known]
        if unknown:
            valid = ", ".join(known) or "none (the registry is empty)"
            raise ValueError(
                f"{location}: unknown character reference(s) {unknown!r}. "
                f"Valid references in this request: {valid}. "
                "Use existing_ids=[] for a new character in extract mode."
            )
        if reconcile and not references:
            raise ValueError(
                f"{location}: Reconciliation cannot introduce new characters"
            )
        claimed.update(references)
        ids = {known[ref] for ref in references}
        originals = [profile for profile in profiles if profile.id in ids]
        identity = (
            originals[0].id
            if originals
            else uuid5(NAMESPACE_URL, json.dumps([book_id, batch, index])).hex
        )
        aliases = list(update.character_alias)
        for original in originals:
            aliases.extend([original.character_name, *original.character_alias])
        seen = {_name_key(update.character_name)}
        unique_aliases = []
        for alias in aliases:
            key = _name_key(alias)
            if key not in seen:
                seen.add(key)
                unique_aliases.append(alias)
        profile = CharacterProfile(
            id=identity,
            book_id=book_id,
            **update.model_dump(exclude={"existing_ids", "character_alias"}),
            character_alias=unique_aliases,
        )
        if originals:
            replacements[identity] = profile
            removed.update(original.id for original in originals[1:])
        else:
            additions.append(profile)
    return [
        replacements.get(profile.id, profile)
        for profile in profiles
        if profile.id not in removed
    ] + additions


class CharacterProfilerAgent(BaseDioramaAgent[list[CharacterProfile]]):
    """Scan narrative text, enrich a cast registry, then reconcile identities.

    Identity resolution is model-based; IDs and book ownership are assigned here.
    No filesystem or shell tools are exposed to the model.
    """

    def configure(
        self,
        *,
        max_nodes_group: int = 1,
        max_chunk_characters: int | None = None,
        max_context_characters: int = 240_000,
        max_repair_attempts: int = 2,
        timeout_seconds: float | None = 600,
    ) -> None:
        """Group up to max_nodes_group consecutive leaves within the input budget.

        max_chunk_characters optionally imposes a smaller excerpt limit. The
        context budget includes serialized input and repair headroom; callers
        should size it to leave room for model output in their model's context.
        """
        if type(max_nodes_group) is not int or max_nodes_group < 1:
            raise ValueError("max_nodes_group must be a positive integer")
        if (
            max_chunk_characters is not None and max_chunk_characters < 1
        ) or max_context_characters < 1:
            raise ValueError("Character limits must be positive")
        if max_repair_attempts < 0:
            raise ValueError("max_repair_attempts must be non-negative")
        if timeout_seconds is not None and timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.max_nodes_group = max_nodes_group
        self.max_chunk_characters = max_chunk_characters
        self.max_context_characters = max_context_characters
        self.max_repair_attempts = max_repair_attempts
        self.timeout_seconds = timeout_seconds

    @property
    def _repair_headroom(self) -> int:
        return (
            min(4096, self.max_context_characters // 10)
            if self.max_repair_attempts
            else 0
        )

    def _next_excerpt(
        self,
        book: StoryBook,
        profiles: list[CharacterProfile],
        path: tuple[str, ...],
        text: str,
        start: int,
    ) -> tuple[dict[str, object], int]:
        """Fit the remaining leaf against the current registry, then split if needed."""
        overhead = len(build_system_prompt(_Updates.model_json_schema())) + len(
            build_extraction_prompt(
                book.metadata.title, profiles, {"path": path, "text": ""}
            )
        )
        available = self.max_context_characters - overhead - self._repair_headroom
        if available < 1:
            raise CharacterProfilerError(
                "Profiling exceeded max_context_characters: "
                "no room for narrative text after the system prompt and registry"
            )
        end = min(len(text), start + available)
        if self.max_chunk_characters is not None:
            end = min(end, start + self.max_chunk_characters)

        # JSON escaping can make the request longer than the source text.
        if len(json.dumps(text[start:end], ensure_ascii=False)) - 2 > available:
            low, high = start, end
            while low < high:
                middle = (low + high + 1) // 2
                size = len(json.dumps(text[start:middle], ensure_ascii=False)) - 2
                if size <= available:
                    low = middle
                else:
                    high = middle - 1
            end = low
        if end == start:
            raise CharacterProfilerError(
                "Profiling exceeded max_context_characters: "
                "no room for the next narrative character"
            )
        if end < len(text):
            # Prefer complete paragraphs, then lines, then words. A single
            # oversized word still advances without dropping any source text.
            for separator in ("\n\n", "\n", " "):
                boundary = text.rfind(separator, start, end)
                if boundary > start:
                    end = boundary + len(separator)
                    break
        return {"path": path, "text": text[start:end]}, end

    def _next_group_excerpt(
        self,
        book: StoryBook,
        profiles: list[CharacterProfile],
        leaves: list[tuple[tuple[str, ...], str]],
        start: int,
    ) -> tuple[dict[str, object] | None, int]:
        """Pack whole consecutive leaves; fall back to single-leaf splitting."""
        if self.max_nodes_group == 1:
            return None, 1
        nodes = []
        characters = 0
        selected: dict[str, object] | None = None
        count = 1
        system_size = len(build_system_prompt(_Updates.model_json_schema()))
        for path, text in leaves[start : start + self.max_nodes_group]:
            nodes.append({"path": path, "text": text})
            characters += len(text)
            if (
                self.max_chunk_characters is not None
                and characters > self.max_chunk_characters
            ):
                break
            if len(nodes) == 1:
                continue
            candidate: dict[str, object] = {"nodes": list(nodes)}
            request_size = system_size + len(
                build_extraction_prompt(book.metadata.title, profiles, candidate)
            )
            if request_size + self._repair_headroom > self.max_context_characters:
                break
            selected, count = candidate, len(nodes)
        return selected, count

    @staticmethod
    def _check_cancel(signal: ToolCancellationToken | None) -> None:
        if signal is not None and signal.is_cancelled():
            raise asyncio.CancelledError

    async def _watch_cancellation(
        self, signal: ToolCancellationToken, task: asyncio.Task
    ) -> None:
        while not signal.is_cancelled():
            await asyncio.sleep(0.05)
        task.cancel()

    async def _scan(
        self,
        book: StoryBook,
        profiles: list[CharacterProfile],
        excerpt: dict[str, object] | None,
        batch: int,
        session_id: str,
        signal: ToolCancellationToken | None,
    ) -> list[CharacterProfile]:
        system_prompt = build_system_prompt(_Updates.model_json_schema())
        prompt = build_extraction_prompt(book.metadata.title, profiles, excerpt)
        feedback = ""
        for attempt in range(self.max_repair_attempts + 1):
            self._check_cancel(signal)
            request = prompt + feedback
            if len(system_prompt) + len(request) > self.max_context_characters:
                raise CharacterProfilerError(
                    "Profiling exceeded max_context_characters"
                )
            response = None
            stream = run_agent_loop(
                provider=self.provider,
                model=self.model,
                system=system_prompt,
                messages=[],
                prompts=[UserMessage(content=request)],
                tools=[],
                max_turns=1,
                signal=signal,
                session_id=session_id,
            )
            try:
                async for event in stream:
                    self._check_cancel(signal)
                    await self.emit_event(event)
                    if isinstance(event, MessageEndEvent) and isinstance(
                        event.message, AssistantMessage
                    ):
                        response = event.message
            finally:
                if isinstance(stream, AsyncGenerator):
                    await stream.aclose()
            if response is None or response.stop_reason in {"error", "aborted"}:
                raise CharacterProfilerError(
                    response.error_message
                    if response and response.error_message
                    else "Model response failed"
                )
            try:
                if response.stop_reason != "stop":
                    raise ValueError("Model response was incomplete")
                updates = _Updates.model_validate_json(message_text(response))
                return _apply_updates(
                    profiles, updates, book.id, batch, reconcile=excerpt is None
                )
            except ValueError as exc:
                await self.emit_event(
                    RunEvent(
                        "validation_failed",
                        {"batch": batch, "attempt": attempt + 1, "error": str(exc)},
                    )
                )
                if attempt == self.max_repair_attempts:
                    raise CharacterProfilerError(
                        f"Character profile validation failed: {exc}"
                    ) from exc
                feedback = build_repair_prompt(str(exc))[: self._repair_headroom]
        raise CharacterProfilerError("Profiling ended without valid profiles")

    async def _save_checkpoint(
        self, path: Path, checkpoint: ProfilingCheckpoint, *, overwrite: bool = True
    ) -> None:
        publication = asyncio.create_task(
            asyncio.to_thread(write_checkpoint, path, checkpoint, overwrite=overwrite)
        )
        try:
            await asyncio.shield(publication)
        except asyncio.CancelledError:
            # Finish an in-flight atomic write before returning cancellation;
            # it contains only fully validated, completed-node state.
            with suppress(Exception):
                await publication
            raise
        await self.emit_event(
            RunEvent(
                "checkpoint_saved",
                {
                    "path": str(path),
                    "completed_nodes": checkpoint.completed_nodes,
                    "reconciled": checkpoint.reconciled,
                },
            )
        )

    async def run(
        self,
        storybook: StoryBook,
        *,
        signal: ToolCancellationToken | None = None,
        checkpoint_path: str | Path | None = None,
        resume: bool = False,
    ) -> list[CharacterProfile]:
        """Return the cast, optionally checkpointing and resuming completed leaves.

        Resume requires the same normalized StoryBook. An unfinished group or leaf
        is repeated from its beginning; completed leaves and reconciled results
        are reused. Persistent IDs and the extraction batch counter survive resume.
        """
        self._check_cancel(signal)
        checkpoint_file = (
            Path(checkpoint_path).expanduser().resolve()
            if checkpoint_path is not None
            else None
        )
        if resume and checkpoint_file is None:
            raise CharacterProfilerError("resume requires checkpoint_path")
        monitor = None
        task = asyncio.current_task()
        if signal is not None and task is not None:
            monitor = asyncio.create_task(self._watch_cancellation(signal, task))
        try:
            async with asyncio.timeout(self.timeout_seconds):
                session_id = uuid4().hex
                leaves = list(_narrative_leaves(storybook.structure))
                try:
                    if resume and checkpoint_file is not None:
                        state = await asyncio.to_thread(
                            load_checkpoint, checkpoint_file, storybook, len(leaves)
                        )
                    else:
                        state = ProfilingCheckpoint(
                            book_id=storybook.id,
                            storybook_sha256=storybook_fingerprint(storybook),
                            total_nodes=len(leaves),
                            completed_nodes=0,
                            batch=0,
                            profiles=[],
                        )
                        if checkpoint_file is not None:
                            await self._save_checkpoint(
                                checkpoint_file, state, overwrite=False
                            )
                except (ValueError, OSError) as exc:
                    raise CharacterProfilerError(
                        f"Unable to prepare checkpoint: {exc}"
                    ) from exc
                # Version 2 permits one extraction batch to complete several nodes.
                state = state.model_copy(update={"version": 2})
                profiles = state.profiles
                batch = state.batch
                await self.emit_event(
                    RunEvent(
                        "profiling_start",
                        {
                            "total_nodes": len(leaves),
                            "completed_nodes": state.completed_nodes,
                        },
                    )
                )
                while state.completed_nodes < len(leaves):
                    self._check_cancel(signal)
                    first = state.completed_nodes
                    group_excerpt, count = self._next_group_excerpt(
                        storybook, profiles, leaves, first
                    )
                    node_index = first + count
                    path, text = leaves[first]
                    node_details = {
                        "node_index": node_index,
                        "total_nodes": len(leaves),
                        "path": path,
                    }
                    if group_excerpt is not None:
                        node_details = {
                            "first_node_index": first + 1,
                            "last_node_index": node_index,
                            "total_nodes": len(leaves),
                            "paths": [path for path, _ in leaves[first:node_index]],
                        }
                    event_prefix = "group" if group_excerpt is not None else "node"
                    await self.emit_event(
                        RunEvent(f"{event_prefix}_start", node_details)
                    )
                    if group_excerpt is not None:
                        batch += 1
                        profiles = await self._scan(
                            storybook,
                            profiles,
                            group_excerpt,
                            batch,
                            session_id,
                            signal,
                        )
                    else:
                        start = 0
                        while start < len(text):
                            self._check_cancel(signal)
                            excerpt, start = self._next_excerpt(
                                storybook, profiles, path, text, start
                            )
                            batch += 1
                            profiles = await self._scan(
                                storybook, profiles, excerpt, batch, session_id, signal
                            )
                    self._check_cancel(signal)
                    state = state.model_copy(
                        update={
                            "profiles": profiles,
                            "completed_nodes": node_index,
                            "batch": batch,
                        }
                    )
                    if checkpoint_file is not None:
                        await self._save_checkpoint(checkpoint_file, state)
                    await self.emit_event(RunEvent(f"{event_prefix}_end", node_details))
                if len(profiles) > 1 and not state.reconciled:
                    await self.emit_event(RunEvent("reconciliation_start"))
                    profiles = await self._scan(
                        storybook, profiles, None, batch + 1, session_id, signal
                    )
                self._check_cancel(signal)
                if not state.reconciled:
                    state = state.model_copy(
                        update={"profiles": profiles, "reconciled": True}
                    )
                    if checkpoint_file is not None:
                        await self._save_checkpoint(checkpoint_file, state)
                await self.emit_event(RunEvent("profiles_ready"))
                await self.save_output(
                    "character_profiles.json", _PROFILES.dump_json(profiles, indent=2)
                )
                return profiles
        except TimeoutError as exc:
            raise CharacterProfilerError("Character profiling timed out") from exc
        finally:
            if monitor is not None:
                monitor.cancel()
                with suppress(asyncio.CancelledError):
                    await monitor
