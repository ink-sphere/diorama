"""Shared infrastructure for Diorama agents, independent of presentation and domain."""

from __future__ import annotations

import asyncio
import warnings
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from tau_agent import AgentHarness, AgentHarnessConfig, AgentTool
from tau_agent.provider import ModelProvider

from diorama.utils.trace_events import DioramaAgentEvent, TraceCallback, TraceEvent


@dataclass
class RunOutcome:
    """Completion details filled by a subclass before leaving its run scope."""

    message: str = "Run completed"
    details: dict[str, object] = field(default_factory=dict)


class BaseDioramaAgent:
    """Common configuration, lifecycle, and tracing for domain-specific agents.

    Subclasses keep their own public entry points and wrap each operation in
    ``_run``. Use separate instances for concurrent operations. The caller owns
    provider authentication and closure; the base never closes the provider.
    """

    def __init__(
        self,
        provider: ModelProvider,
        model: str,
        *,
        max_turns: int = 30,
        on_event: TraceCallback | None = None,
    ) -> None:
        if not model.strip():
            raise ValueError("model must be nonempty")
        if max_turns < 1:
            raise ValueError("max_turns must be at least 1")
        self.provider = provider
        self.model = model
        self.max_turns = max_turns
        self.on_event = on_event
        self._trace_failed = False
        self._running = False

    def _emit(self, event: TraceEvent) -> None:
        """Disable a faulty observer for this run, without exposing its exception."""
        if self.on_event is not None and not self._trace_failed:
            try:
                self.on_event(event)
            except Exception:
                self._trace_failed = True
                # Even warnings-as-errors must not let a display failure abort work.
                try:
                    warnings.warn(
                        "Trace callback failed; tracing disabled for this run",
                        RuntimeWarning,
                        stacklevel=2,
                    )
                except RuntimeWarning:
                    pass

    @asynccontextmanager
    async def _run(
        self,
        *,
        event_prefix: str = "run",
        message: str = "Starting agent",
        details: dict[str, object] | None = None,
    ) -> AsyncIterator[RunOutcome]:
        """Emit one terminal lifecycle event and reset state, preserving exceptions.

        Completion occurs only after the entire body succeeds, including any
        domain-specific validation and publication. Reentrant runs are rejected.
        """
        if self._running:
            raise RuntimeError("Agent is already running; use a separate instance")
        self._running = True
        self._trace_failed = False
        outcome = RunOutcome()
        try:
            self._emit(
                DioramaAgentEvent(
                    f"{event_prefix}_start", message, details or {}, phase="start"
                )
            )
            try:
                yield outcome
            except asyncio.CancelledError:
                self._emit(
                    DioramaAgentEvent(
                        f"{event_prefix}_cancelled", "Run cancelled", phase="cancelled"
                    )
                )
                raise
            except Exception as exc:
                self._emit(
                    DioramaAgentEvent(f"{event_prefix}_error", str(exc), phase="error")
                )
                raise
            else:
                self._emit(
                    DioramaAgentEvent(
                        f"{event_prefix}_complete",
                        outcome.message,
                        outcome.details,
                        phase="complete",
                    )
                )
        finally:
            self._running = False

    def _create_harness(
        self, *, system: str, tools: Sequence[AgentTool] = ()
    ) -> AgentHarness:
        """Create fresh conversational state; never reuse a prior run's messages."""
        return AgentHarness(
            AgentHarnessConfig(
                provider=self.provider,
                model=self.model,
                system=system,
                tools=list(tools),
                max_turns=self.max_turns,
            )
        )

    async def _consume(
        self,
        harness: AgentHarness,
        prompt: str,
        *,
        stop_when: Callable[[], bool] | None = None,
    ) -> None:
        """Forward Tau events and close the stream on exhaustion, stop, or failure.

        Stopping early is not run completion. Subclasses must still check their
        result and finish domain work inside ``_run``. Errors are not translated.
        """
        stream = harness.prompt(prompt)
        try:
            async for event in stream:
                self._emit(event)
                if stop_when is not None and stop_when():
                    break
        finally:
            await stream.aclose()
