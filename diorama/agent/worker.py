"""Run Geppetto's ingestion skill in a disposable Linux worker via the Codex SDK."""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from openai_codex import (
    ApprovalMode,
    AsyncCodex,
    AsyncThread,
    CodexConfig,
    CodexError,
    SkillInput,
    TextInput,
    TransportClosedError,
)

from diorama.agent.geppetto import Geppetto

logger = logging.getLogger(__name__)
IMAGE = "diorama-geppetto:local"
PROFILE = "diorama-geppetto-profile"
SKILL = "/home/geppetto/.agents/skills/ingest-epub/SKILL.md"


class WorkerError(RuntimeError):
    """The worker could not finish the assigned task."""


class WorkerLost(WorkerError):
    """The SDK transport closed before the turn completed."""


class SignInRequired(WorkerError):
    """The dedicated worker profile needs a ChatGPT session."""


@dataclass
class Worker:
    """One SDK thread shared by the initial ingestion turn and any repair turn."""

    thread: AsyncThread

    async def run(self, task: str) -> None:
        """Run the task with the ingestion skill and await turn completion.

        On caller cancellation, request interruption of the active turn.
        The harness reads and validates the worker's candidate separately.
        """
        started = time.monotonic()
        handle = await self.thread.turn(
            [TextInput(task), SkillInput("ingest-epub", SKILL)]
        )
        try:
            result = await handle.run()
        except asyncio.CancelledError:
            logger.info(
                "Cancelled thread=%s turn=%s duration=%.2f",
                self.thread.id,
                handle.id,
                time.monotonic() - started,
            )
            try:
                async with asyncio.timeout(3):
                    await handle.interrupt()
            except Exception:
                logger.warning("Interrupt unavailable for turn %s", handle.id)
            raise
        except Exception:
            logger.warning(
                "Failed thread=%s turn=%s duration=%.2f",
                self.thread.id,
                handle.id,
                time.monotonic() - started,
            )
            raise
        logger.info(
            "Finished thread=%s turn=%s outcome=%s duration=%.2f usage=%s",
            self.thread.id,
            handle.id,
            result.status.value,
            time.monotonic() - started,
            result.usage,
        )
        if result.status.value != "completed":
            raise WorkerError(
                f"Turn {handle.id} ended with status {result.status.value}"
            )


async def _docker(*args: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        "docker", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        out, err = await proc.communicate()
    except asyncio.CancelledError:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()
        raise
    if proc.returncode:
        raise WorkerError(
            f"Docker {args[0]} failed: {err.decode(errors='replace').strip()}"
        )
    return out.decode().strip()


def _bind(source: Path, target: str, *, readonly: bool = False) -> str:
    # Docker parses --mount fields as CSV, including paths containing commas.
    fields = ["type=bind", f"source={source}", f"target={target}"]
    if readonly:
        fields.append("readonly")
    row = io.StringIO()
    csv.writer(row).writerow(fields)
    return row.getvalue().removesuffix("\r\n")


def _create_args(name: str, source: Path, scratch: Path, agent: Geppetto) -> list[str]:
    return [
        "create",
        "--interactive",
        "--name",
        name,
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--security-opt",
        f"seccomp={agent.skill_path.parents[2] / 'worker-seccomp.json'}",
        "--mount",
        _bind(source, "/input/book.epub", readonly=True),
        "--mount",
        _bind(scratch, "/work"),
        "--mount",
        _bind(agent.skill_path.parents[1], str(Path(SKILL).parents[1]), readonly=True),
        "--mount",
        f"type=volume,source={PROFILE},target=/var/lib/geppetto",
        # Codex also discovers skills in its profile and working directory.
        "--tmpfs",
        "/var/lib/geppetto/skills:ro",
        "--tmpfs",
        "/work/.agents:ro",
        "--tmpfs",
        "/work/.codex:ro",
        IMAGE,
        "-c",
        "skills.bundled.enabled=false",
        "-c",
        "cloud.skills.enabled=false",
        "-c",
        "features.plugins=false",
        "-c",
        'default_permissions="geppetto"',
        "-c",
        'permissions.geppetto.extends=":workspace"',
        "-c",
        'permissions.geppetto.filesystem={"/var/lib/geppetto"="deny"}',
        "-c",
        'shell_environment_policy.inherit="core"',
        "app-server",
        "--listen",
        "stdio://",
    ]


@asynccontextmanager
async def docker_worker(
    agent: Geppetto, source: Path, scratch: Path
) -> AsyncIterator[Worker]:
    """Yield a worker with read-only access to the EPUB and a writable workspace.

    The dedicated Docker profile must be signed in with ChatGPT. The container
    is removed when the context exits, including after a failed turn.
    """
    name = f"geppetto-{uuid4().hex}"
    try:
        await _docker(*_create_args(name, source.resolve(), scratch.resolve(), agent))
        config = CodexConfig(
            launch_args_override=("docker", "start", "--attach", "--interactive", name)
        )
        async with AsyncCodex(config) as sdk:
            account = (await sdk.account()).account
            if account is None or account.root.type != "chatgpt":
                raise SignInRequired(
                    "Sign in to the Geppetto worker with ChatGPT (see README)."
                )
            thread = await sdk.thread_start(
                cwd="/work",
                ephemeral=True,
                approval_mode=ApprovalMode.deny_all,
                developer_instructions=agent.instructions,
            )
            yield Worker(thread)
    except TransportClosedError as exc:
        raise WorkerLost("Lost connection to the Geppetto worker") from exc
    except (WorkerError, ValueError):
        raise
    except (CodexError, OSError, RuntimeError) as exc:
        raise WorkerError(
            "Geppetto worker failed to start or complete its turn"
        ) from exc
    finally:
        try:
            await _docker("rm", "--force", name)
        except FileNotFoundError as exc:
            raise WorkerError("The Docker CLI is required to start Geppetto") from exc
        except WorkerError as exc:
            if "No such container" not in str(exc):
                raise


def ingestion_task(agent: Geppetto, book_id: str) -> str:
    """Build the worker task with the book ID, container paths, and tool imports."""
    imports = ", ".join(f"{tool.__module__}.{tool.__name__}" for tool in agent.tools)
    return (
        f"Ingest /input/book.epub with ingest-epub. The assigned book ID as JSON is "
        f"{json.dumps(book_id)}. Write the validated Book to /work/candidate.json. "
        f"Use python and the installed helpers: {imports}."
    )
