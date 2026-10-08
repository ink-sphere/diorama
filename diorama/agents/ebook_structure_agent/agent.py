from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import suppress
from hashlib import sha256
from inspect import isawaitable
from pathlib import Path

from tau_agent import AssistantMessage, MessageEndEvent, TurnStartEvent
from tau_agent.session import JsonlSessionStorage
from tau_agent.tools import ToolCancellationToken
from tau_coding import CodingSession, CodingSessionConfig
from tau_coding.events import CodingSessionEvent
from tau_coding.paths import TauPaths
from tau_coding.resources import TauResourcePaths

from diorama.agents.base import BaseDioramaAgent, RunEvent
from diorama.agents.ebook_structure_agent.prompts import (
    build_extraction_prompt,
    build_repair_prompt,
    build_system_prompt,
)
from diorama.agents.ebook_structure_agent.source import (
    EbookStructureError,
)
from diorama.agents.ebook_structure_agent.tools import create_ebook_coding_tools
from diorama.agents.ebook_structure_agent.validation import (
    SourceValidator,
    load_storybook,
    validate_source,
)
from diorama.models.storybook import StoryBook

type EventListener = Callable[[CodingSessionEvent], Awaitable[None] | None]


class EbookStructureAgent(BaseDioramaAgent[StoryBook]):
    def configure(
        self,
        *,
        max_turns: int = 60,
        max_repair_attempts: int = 2,
        timeout_seconds: float | None = 300,
        max_context_characters: int | None = None,
        max_output_bytes: int = 128 * 1024 * 1024,
        auto_compact_token_threshold: int | None = None,
        shell_command_prefix: str | None = None,
        source_validator: SourceValidator = validate_source,
    ) -> None:
        if max_turns < 1 or max_output_bytes < 1 or max_repair_attempts < 0:
            raise ValueError("Invalid execution limits")
        if timeout_seconds is not None and timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_context_characters is not None and max_context_characters < 1:
            raise ValueError("max_context_characters must be positive")
        if (
            auto_compact_token_threshold is not None
            and auto_compact_token_threshold < 1
        ):
            raise ValueError("auto_compact_token_threshold must be positive")
        self.max_turns = max_turns
        self.max_repair_attempts = max_repair_attempts
        self.timeout_seconds = timeout_seconds
        self.max_context_characters = max_context_characters
        self.max_output_bytes = max_output_bytes
        self.auto_compact_token_threshold = auto_compact_token_threshold
        self.shell_command_prefix = shell_command_prefix
        self.source_validator = source_validator

    @staticmethod
    def _prepare_workspace(workspace: Path, data: bytes, filename: str) -> Path:
        for directory in ("input", "work", "output", "runtime"):
            (workspace / directory).mkdir()
        input_path = workspace / "input" / filename
        input_path.write_bytes(data)
        return input_path

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

    async def _run_session(
        self,
        workspace: Path,
        input_path: Path,
        data: bytes,
        *,
        on_event: EventListener | None,
        signal: ToolCancellationToken | None,
    ) -> StoryBook:
        session = await CodingSession.load(
            CodingSessionConfig(
                provider=self.provider,
                model=self.model,
                storage=JsonlSessionStorage(workspace / "runtime" / "session.jsonl"),
                cwd=workspace,
                append_system_prompt=build_system_prompt(),
                tools=create_ebook_coding_tools(
                    workspace, shell_command_prefix=self.shell_command_prefix
                ),
                resource_paths=TauResourcePaths(
                    root=workspace / "runtime",
                    cwd=workspace,
                    agents_root=None,
                    paths=TauPaths(
                        home=workspace / "runtime",
                        agents_home=workspace / "runtime" / "agents",
                    ),
                    project_resources_enabled=False,
                ),
                skills_enabled=False,
                extensions_enabled=False,
                project_extensions_enabled=False,
                trust_override="decline",
                auto_compact_token_threshold=self.auto_compact_token_threshold,
            )
        )
        turns = 0
        prompt = build_extraction_prompt(
            input_path.relative_to(workspace), sys.executable, sha256(data).hexdigest()
        )
        try:
            for attempt in range(self.max_repair_attempts + 1):
                self._check_cancel(signal)
                last_error = None
                stream = session.prompt(prompt)
                try:
                    async for event in stream:
                        self._check_cancel(signal)
                        await self.emit_event(event)
                        if isinstance(event, TurnStartEvent):
                            turns += 1
                            if turns > self.max_turns:
                                raise EbookStructureError(
                                    f"Agent stopped after max_turns={self.max_turns}"
                                )
                            if self.max_context_characters is not None:
                                size = len(session.system_prompt) + sum(
                                    len(message.model_dump_json())
                                    for message in session.messages
                                )
                                if size > self.max_context_characters:
                                    raise EbookStructureError(
                                        "Extraction exceeded max_context_characters"
                                    )
                        if on_event is not None:
                            result = on_event(event)
                            if isawaitable(result):
                                await result
                        if isinstance(event, MessageEndEvent) and isinstance(
                            event.message, AssistantMessage
                        ):
                            last_error = (
                                event.message.error_message or "Model response failed"
                                if event.message.stop_reason in {"error", "aborted"}
                                else None
                            )
                finally:
                    if isinstance(stream, AsyncGenerator):
                        await stream.aclose()
                if last_error is not None:
                    raise EbookStructureError(last_error)
                output = workspace / "output" / "storybook.json"
                try:
                    if not output.resolve().is_relative_to(workspace):
                        raise ValueError(
                            "StoryBook output must remain inside the workspace"
                        )
                    return await asyncio.to_thread(
                        load_storybook,
                        output,
                        data,
                        input_path.name,
                        source_validator=self.source_validator,
                        max_output_bytes=self.max_output_bytes,
                    )
                except (ValueError, OSError) as exc:
                    await self.emit_event(
                        RunEvent(
                            "validation_failed",
                            {"attempt": attempt + 1, "error": str(exc)},
                        )
                    )
                    if attempt == self.max_repair_attempts:
                        raise EbookStructureError(
                            f"StoryBook validation failed: {exc}"
                        ) from exc
                    prompt = build_repair_prompt(str(exc))
            raise EbookStructureError("Extraction ended without a valid StoryBook")
        finally:
            session.cancel()
            await session.aclose()

    async def run(
        self,
        ebook_path: str | Path,
        *,
        on_event: EventListener | None = None,
        signal: ToolCancellationToken | None = None,
    ) -> StoryBook:
        self._check_cancel(signal)
        path = Path(ebook_path).expanduser().resolve()
        data = await asyncio.to_thread(path.read_bytes)
        self._check_cancel(signal)
        monitor = None
        task = asyncio.current_task()
        if signal is not None and task is not None:
            monitor = asyncio.create_task(self._watch_cancellation(signal, task))
        try:
            async with asyncio.timeout(self.timeout_seconds):
                with self.working_directory() as workspace:
                    preparation = asyncio.create_task(
                        asyncio.to_thread(
                            self._prepare_workspace, workspace, data, path.name
                        )
                    )
                    try:
                        input_path = await asyncio.shield(preparation)
                    except asyncio.CancelledError:
                        with suppress(Exception):
                            await preparation
                        raise
                    await self.emit_event(
                        RunEvent("workspace_ready", {"path": str(workspace)})
                    )
                    storybook = await self._run_session(
                        workspace, input_path, data, on_event=on_event, signal=signal
                    )
                    self._check_cancel(signal)
                    if self.workspace_root is not None:
                        serialized = await asyncio.to_thread(
                            storybook.model_dump_json, indent=2
                        )
                        self._check_cancel(signal)
                        await self.save_output("storybook.json", serialized)
                    return storybook
        except TimeoutError as exc:
            raise EbookStructureError("Structure extraction timed out") from exc
        finally:
            if monitor is not None:
                monitor.cancel()
                with suppress(asyncio.CancelledError):
                    await monitor
