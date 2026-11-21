"""Project public Codex execution notifications into the local edition history."""

import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from openai_codex.models import Notification

FIELD_LIMIT = 16 * 1024
SENSITIVE = re.compile(
    r"auth\.json|\.env\b|/var/lib/geppetto|\b(?:printenv|setenv|env)\b|"
    r"(?:api[_-]?key|token|passwd|password|secret|authorization|bearer|"
    r"device[_-]?code|user[_-]?code|cookie)\b|sk-[\w-]{10,}|-----BEGIN .*PRIVATE KEY|"
    r"(?:os\.environ|getenv|/proc/[^ ]*/environ)|(?:gh[pousr]_|github_pat_)[a-zA-Z0-9_]+|eyJ[\w-]+\.[\w-]+\.[\w-]+",
    re.I,
)
SETUP = re.compile(
    r"SKILL\.md|AGENTS\.md|instructions\.md|developer_instructions", re.I
)


@dataclass(frozen=True)
class TraceEvent:
    """One display-safe notification, stamped when received by the host."""

    name: str
    observed_at_ms: int
    thread_id: str
    turn_id: str
    item_id: str | None
    fields: dict
    markers: dict
    attempt: int | None = None
    purpose: str | None = None


type TraceListener = Callable[[TraceEvent], Awaitable[None]]


@runtime_checkable
class TracedWorker(Protocol):
    async def run_traced(self, task: str, emit: TraceListener) -> None: ...


def project(event: Notification) -> TraceEvent | None:
    method, payload = event.method, event.payload
    markers = {}
    now = time.time_ns() // 1_000_000
    if method == "turn/completed":
        turn = payload.turn
        fields = turn.model_dump(
            mode="json",
            by_alias=True,
            exclude_none=True,
            include={"id", "status", "started_at", "completed_at", "duration_ms"},
        )
        return TraceEvent(
            method, now, payload.thread_id, turn.id, None, fields, markers
        )
    if method not in {"item/started", "item/completed"}:
        return None
    item = payload.item.root
    if item.type not in {"commandExecution", "fileChange"}:
        return None
    fields = item.model_dump(
        mode="json",
        by_alias=True,
        exclude_none=True,
        include={
            "id",
            "type",
            "status",
            "started_at_ms",
            "completed_at_ms",
            "command",
            "cwd",
            "exit_code",
            "duration_ms",
            "aggregated_output",
        },
    )

    if method == "item/started":
        fields.setdefault("startedAtMs", payload.started_at_ms)
    else:
        fields.setdefault("completedAtMs", payload.completed_at_ms)

    def display(key: str, value: str, withheld: bool = False) -> str | None:
        if withheld or SENSITIVE.search(value):
            markers[key] = {"omitted": True}
            return None
        encoded = value.encode("utf-8", errors="replace")
        if len(encoded) > FIELD_LIMIT:
            markers[key] = {"truncated": True, "original_bytes": len(encoded)}
            return encoded[:FIELD_LIMIT].decode("utf-8", errors="ignore")
        return value

    if item.type == "commandExecution":
        sensitive_command = bool(SENSITIVE.search(item.command))
        setup = bool(SETUP.search(item.command))
        for key in ("command", "cwd", "aggregatedOutput"):
            if key in fields:
                value = display(
                    key,
                    fields.pop(key),
                    key == "aggregatedOutput" and (setup or sensitive_command),
                )
                if value is not None:
                    fields[key] = value
    else:
        changes = []
        for change in item.changes[:40]:
            key = f"changes[{len(changes)}].path"
            path = display(key, change.path)
            changes.append(
                {
                    "kind": change.kind.root.type,
                    **({"path": path} if path is not None else {}),
                }
            )
        fields["changes"] = changes
        if len(item.changes) > len(changes):
            markers["changes"] = {
                "truncated": True,
                "original_count": len(item.changes),
            }
    return TraceEvent(
        method, now, payload.thread_id, payload.turn_id, item.id, fields, markers
    )
