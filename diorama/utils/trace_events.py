"""Presentation-independent events shared by agents and trace consumers."""

from collections.abc import Callable
from dataclasses import dataclass, field

from tau_agent.events import AgentEvent


@dataclass(frozen=True)
class LoaderEvent:
    """A loader lifecycle event; details must not contain credentials."""

    type: str
    message: str
    details: dict[str, object] = field(default_factory=dict)


type TraceEvent = AgentEvent | LoaderEvent
type TraceCallback = Callable[[TraceEvent], None]
