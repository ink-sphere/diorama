"""Presentation-independent events shared by agents and trace consumers."""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from tau_agent.events import AgentEvent


@dataclass(frozen=True)
class DioramaAgentEvent:
    """Domain or lifecycle event; messages/details must not contain credentials."""

    type: str
    message: str
    details: dict[str, object] = field(default_factory=dict)
    phase: Literal["start", "complete", "error", "cancelled"] | None = None


# Backward-compatible constructor and isinstance checks for existing consumers.
LoaderEvent = DioramaAgentEvent

type TraceEvent = AgentEvent | DioramaAgentEvent
type TraceCallback = Callable[[TraceEvent], None]
