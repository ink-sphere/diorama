from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Iterable, Iterator
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import wraps
from inspect import isawaitable
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from typing import Any
from uuid import uuid4

from tau_agent.provider import ModelProvider
from tau_coding.events import CodingSessionEvent


@dataclass(frozen=True, slots=True)
class RunEvent:
    type: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AgentTraceEvent:
    run_id: str
    agent_name: str
    model: str
    sequence: int
    event: CodingSessionEvent | RunEvent
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


type AgentCallback = Callable[[AgentTraceEvent], Awaitable[None] | None]


@dataclass
class _RunContext:
    run_id: str
    task: asyncio.Task[Any] | None
    sequence: int = 0


class BaseDioramaAgent[OutputT](ABC):
    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        implementation = cls.__dict__.get("run")
        if implementation is None or getattr(
            implementation, "__isabstractmethod__", False
        ):
            return

        @wraps(implementation)
        async def traced_run(self, *args: Any, **kwargs: Any):
            context = self._trace_context.get()
            if context is not None and context.task is asyncio.current_task():
                return await implementation(self, *args, **kwargs)
            token = self._trace_context.set(
                _RunContext(uuid4().hex, asyncio.current_task())
            )
            started = monotonic()
            try:
                await self.emit_event(RunEvent("run_start"))
                output = await implementation(self, *args, **kwargs)
                await self.emit_event(
                    RunEvent(
                        "run_end", {"duration": monotonic() - started, "output": output}
                    )
                )
                return output
            except BaseException as exc:
                with suppress(BaseException):
                    await self.emit_event(
                        RunEvent(
                            "run_cancelled"
                            if isinstance(exc, asyncio.CancelledError)
                            else "run_error",
                            {
                                "duration": monotonic() - started,
                                "error": str(exc),
                                "error_type": type(exc).__name__,
                            },
                        )
                    )
                raise
            finally:
                self._trace_context.reset(token)

        cls.run = traced_run

    def __init__(
        self,
        *,
        provider: ModelProvider,
        model: str,
        callbacks: Iterable[AgentCallback] = (),
        workspace_root: str | Path | None = None,
        **kwargs: Any,
    ) -> None:
        if not model.strip():
            raise ValueError("model must be non-empty")
        self.provider = provider
        self.model = model
        self.callbacks = tuple(callbacks)
        self.workspace_root = (
            Path(workspace_root).expanduser().resolve()
            if workspace_root is not None
            else None
        )
        self._trace_context: ContextVar[_RunContext | None] = ContextVar(
            f"diorama_trace_{id(self)}", default=None
        )
        self.configure(**kwargs)

    @contextmanager
    def working_directory(self) -> Iterator[Path]:
        """Provide temporary scratch space without changing the process directory."""
        with TemporaryDirectory(prefix="diorama-") as directory:
            yield Path(directory).resolve()

    async def save_output(self, filename: str, content: str | bytes) -> Path | None:
        """Publish one artifact atomically to the configured output destination."""
        if (
            not filename
            or filename in {".", ".."}
            or "\\" in filename
            or Path(filename).name != filename
        ):
            raise ValueError("Output filename must be a single path component")
        context = self._trace_context.get()
        if context is None:
            raise RuntimeError("Outputs must be saved during run()")
        if self.workspace_root is None:
            return None
        destination = self.workspace_root / context.run_id
        publication = asyncio.create_task(
            asyncio.to_thread(self._publish_output, destination, filename, content)
        )
        try:
            output = await asyncio.shield(publication)
            await self.emit_event(RunEvent("output_saved", {"path": str(output)}))
            return output
        except asyncio.CancelledError:
            with suppress(Exception):
                output = await publication
                await asyncio.to_thread(output.unlink)
                await asyncio.to_thread(output.parent.rmdir)
            raise

    def _publish_output(
        self, destination: Path, filename: str, content: str | bytes
    ) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(
            dir=destination.parent, prefix=".publishing-"
        ) as directory:
            staged = Path(directory)
            artifact = staged / filename
            if isinstance(content, bytes):
                artifact.write_bytes(content)
            else:
                artifact.write_text(content, encoding="utf-8")
            staged.rename(destination)
        return destination / filename

    async def emit_event(self, event: CodingSessionEvent | RunEvent) -> None:
        """Dispatch in registration order; callback failures propagate to the caller."""
        context = self._trace_context.get()
        if context is None:
            raise RuntimeError("Trace events must be emitted during run()")
        context.sequence += 1
        trace = AgentTraceEvent(
            context.run_id, type(self).__name__, self.model, context.sequence, event
        )
        failure: Exception | None = None
        for callback in self.callbacks:
            try:
                result = callback(trace)
                if isawaitable(result):
                    await result
            except Exception as exc:
                if failure is None:
                    failure = exc
        if failure is not None:
            raise failure

    @abstractmethod
    def configure(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError

    @abstractmethod
    async def run(self, *args: Any, **kwargs: Any) -> OutputT:
        raise NotImplementedError
