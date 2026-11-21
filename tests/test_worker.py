import asyncio
import json
from pathlib import Path

import pytest

from diorama.agent.worker import SKILL, _create_args, docker_worker


def test_installed_skills_match_repository_resources(agent):
    source = Path(__file__).resolve().parents[1] / "resources/geppetto/skills"
    installed = agent.skill_path.parents[1]
    assert {
        p.relative_to(installed): p.read_bytes() for p in installed.rglob("SKILL.md")
    } == {p.relative_to(source): p.read_bytes() for p in source.rglob("SKILL.md")}


def test_worker_mounts_only_assigned_input_and_resources(tmp_path, agent):
    args = _create_args("test", tmp_path / "book.epub", tmp_path / "work", agent)
    assert (
        f"type=bind,source={tmp_path}/book.epub,target=/input/book.epub,readonly"
        in args
    )
    assert (
        f"type=bind,source={agent.skill_path.parents[1]},target={Path(SKILL).parents[1]},readonly"
        in args
    )
    assert {
        "skills.bundled.enabled=false",
        "cloud.skills.enabled=false",
        "features.plugins=false",
    } <= set(args)
    assert 'permissions.geppetto.filesystem={"/var/lib/geppetto"="deny"}' in args
    assert "Codex" not in agent.instructions


@pytest.mark.geppetto_live
async def test_live_skill_writes_then_revises_program(
    epub_factory, agent, tmp_path, monkeypatch
):
    monkeypatch.setenv("DIORAMA_CHILD_SECRET_SENTINEL", "host-only")
    book = epub_factory()
    sibling = epub_factory("play")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    async with asyncio.timeout(360):
        async with docker_worker(agent, book, scratch) as worker:
            await worker.run(
                "Perform only the inspection portion of ingest-epub. Write inspect_book.py "
                "in /work using inspect_epub('/input/book.epub'), run it with python, and "
                "have the program write the source title to report.json as a JSON string."
            )
            assert json.loads((scratch / "report.json").read_text()) == "Novel"
            first = (scratch / "inspect_book.py").read_bytes()
            await worker.run(
                "The report needs the ordered heading titles too. Edit and rerun inspect_book.py. "
                "Write report.json as an object with title, headings, and isolated. Compute isolated "
                "in Python by checking that the DIORAMA_CHILD_SECRET_SENTINEL environment variable "
                "is absent, /input/book.epub cannot "
                f"be opened for update, and the sibling fixture {sibling} is inaccessible. Check "
                "permission by attempting the operations; do not read or print credential contents."
            )
            assert json.loads((scratch / "report.json").read_text()) == {
                "title": "Novel",
                "headings": ["Chapter I"],
                "isolated": True,
            }
            assert (scratch / "inspect_book.py").read_bytes() != first


@pytest.mark.geppetto_live
async def test_installed_skill_and_sandbox_without_account(
    epub_factory, tmp_path, agent, monkeypatch
):
    from uuid import uuid4

    from openai_codex.client import CodexClient, CodexConfig
    from openai_codex.generated.v2_all import CommandExecResponse, SkillsListResponse

    from diorama.agent.worker import PROFILE, _docker

    sibling = epub_factory("play")
    source = epub_factory()
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "canary").write_text("sentinel")
    for root in (
        profile / "skills",
        scratch / ".agents/skills",
        scratch / ".codex/skills",
    ):
        decoy = root / "unrelated" / "SKILL.md"
        decoy.parent.mkdir(parents=True)
        decoy.write_text(
            "---\nname: unrelated\ndescription: Unrelated test skill\n---\n"
        )
    monkeypatch.setenv("DIORAMA_CHILD_SECRET_SENTINEL", "host-only")
    name = f"geppetto-test-{uuid4().hex}"
    args = _create_args(name, source, scratch, agent)
    old = f"type=volume,source={PROFILE},target=/var/lib/geppetto"
    args[args.index(old)] = f"type=bind,source={profile},target=/var/lib/geppetto"
    # Use a disposable profile with a synthetic canary for the filesystem assertions.
    probe = """
import os, sys
from pathlib import Path
from diorama.agent.tools import inspect_epub
assert inspect_epub("/input/book.epub").title == "Novel"
assert "DIORAMA_CHILD_SECRET_SENTINEL" not in os.environ
assert not Path(sys.argv[1]).exists()
for path, mode in [("/var/lib/geppetto/canary", "rb"), ("/input/book.epub","r+b")]:
    try:
        with open(path, mode):
            raise AssertionError("unexpected access to " + path)
    except (PermissionError, FileNotFoundError):
        pass
    except OSError as e:
        import errno
        if e.errno != errno.EROFS:
            raise
Path("/work/proof").write_text("verified")
"""
    await _docker(*args)
    try:
        config = CodexConfig(
            launch_args_override=("docker", "start", "--attach", "--interactive", name)
        )
        with CodexClient(config) as client:
            client.initialize()
            listing = client.request(
                "skills/list", {"cwds": ["/work"]}, response_model=SkillsListResponse
            )
            skills = [
                (s.name, s.path.root)
                for entry in listing.data
                for s in entry.skills
                if s.enabled
            ]
            assert skills == [("ingest-epub", SKILL)]
            result = client.request(
                "command/exec",
                {"command": ["python", "-c", probe, str(sibling)], "cwd": "/work"},
                response_model=CommandExecResponse,
            )
            assert result.exit_code == 0, result.stderr
        assert (scratch / "proof").read_text() == "verified"
    finally:
        await _docker("rm", "--force", name)


async def test_cancel_interrupts_active_sdk_turn():
    from unittest.mock import AsyncMock, Mock

    from diorama.agent.worker import Worker

    started = asyncio.Event()

    async def run_until_cancelled():
        started.set()
        await asyncio.Future()

    handle = Mock(
        id="turn", run=AsyncMock(side_effect=run_until_cancelled), interrupt=AsyncMock()
    )
    thread = Mock(id="thread", turn=AsyncMock(return_value=handle))
    task = asyncio.create_task(Worker(thread).run("inspect the book"))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    handle.interrupt.assert_awaited_once()
    (inputs,) = thread.turn.call_args.args
    assert inputs[0].text == "inspect the book" and inputs[1].path == SKILL
