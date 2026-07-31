"""Tests for the backend's literary-research phase and the moodboard's API.

The agent itself is covered by ``test_literary_research_agent.py``; what matters here
is everything *around* it — that research is lazy and never runs at upload, that its
trace reaches a watcher, that a run which dies partway still leaves usable artifacts
on disk, that a failed retry can't destroy a moodboard that already existed, and that
the four routes behave the way the modal assumes.

Fully offline: the store is redirected into a tmp_path and the agent is stubbed, so
nothing here touches the network or an LLM.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from diorama.agents.literary_research_agent import (
    AuthorProfile,
    Evidence,
    LiteraryResearchError,
    LiteraryResearchReport,
    LocationProfile,
    Milieu,
    PaletteColor,
    StyleBible,
    StyleBibleCandidates,
    TimePeriod,
    WorldDossier,
)
from diorama.backend import research, store, usage_store
from diorama.backend.main import app
from diorama.backend.models import BookRecord
from diorama.backend.runs import RunLog
from diorama.core.events import ToolExecutionEndEvent, ToolExecutionStartEvent
from diorama.ebook.models import Coverage, EbookStructure, StructureNode

BOOK_ID = "bk0001"


# --------------------------------------------------------------------------- #
# Fixtures & doubles
# --------------------------------------------------------------------------- #
@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "UPLOADS_DIR", tmp_path / "uploads")
    monkeypatch.setattr(store, "STRUCTURES_DIR", tmp_path / "structures")
    monkeypatch.setattr(store, "SCENES_DIR", tmp_path / "scenes")
    monkeypatch.setattr(store, "RESEARCH_DIR", tmp_path / "research")
    monkeypatch.setattr(store, "COVERS_DIR", tmp_path / "covers")
    monkeypatch.setattr(store, "LIBRARY_FILE", tmp_path / "library.json")
    monkeypatch.setattr(usage_store, "USAGE_DIR", tmp_path / "usage")
    return tmp_path


@pytest.fixture(autouse=True)
def offline_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never read real settings, a real API key, or a real search key."""

    async def resolve(agent_id: str) -> tuple[str, str | None]:
        return "openrouter/test/model", None

    async def no_search() -> None:
        return None

    monkeypatch.setattr(research, "resolve_agent_runtime", resolve)
    monkeypatch.setattr(research, "resolve_search_runtime", no_search)


@pytest.fixture(autouse=True)
def clear_runs() -> None:
    """Runs are a module-level registry; a leaked one would leak across tests."""
    research._runs.clear()
    yield
    research._runs.clear()


class StubResearcher:
    """Stands in for ``LiteraryResearchAgent``; its own behaviour is tested elsewhere.

    Emits two tool events so the trace path is exercised, then either returns
    ``report`` or raises ``error``.
    """

    report: LiteraryResearchReport | None = None
    error: Exception | None = None
    seen_kwargs: dict = {}

    def __init__(self, **kwargs) -> None:
        StubResearcher.seen_kwargs = kwargs

    def stream_research(self, epub_path, *, structure=None):
        async def events():
            yield ToolExecutionStartEvent(
                tool_call_id="c1", tool_name="web_search", args={"query": "Tenniel"}
            )
            yield ToolExecutionEndEvent(
                tool_call_id="c1", tool_name="web_search", output="{...raw json...}"
            )

        def finalize() -> LiteraryResearchReport:
            if StubResearcher.error is not None:
                raise StubResearcher.error
            assert StubResearcher.report is not None
            return StubResearcher.report

        return events(), finalize


@pytest.fixture(autouse=True)
def stub_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    StubResearcher.report = None
    StubResearcher.error = None
    monkeypatch.setattr(research, "LiteraryResearchAgent", StubResearcher)


def _author(name: str = "Lewis Carroll") -> AuthorProfile:
    return AuthorProfile(
        name=name,
        bio_prose="A don who wrote for a child and accidentally wrote for everyone. "
        * 4,
        work_context_prose="Told on a river outing, written down after. " * 6,
        publication_year=1865,
        authorship_period="Victorian England",
    )


def _dossier(*, block_ids: list[int] | None = None) -> WorldDossier:
    evidence = Evidence(block_ids=block_ids or [10, 400, 900])
    return WorldDossier(
        time_periods=[
            TimePeriod(label="Victorian England", kind="authorship", evidence=evidence)
        ],
        locations=[
            LocationProfile(
                name="The riverbank",
                existence="real_but_altered",
                description="Where it starts, on a hot afternoon.",
                periods=["Victorian England"],
                evidence=evidence,
            )
        ],
        milieus=[
            Milieu(
                name="Oxford gentry",
                wardrobe="Starched pinafores and buttoned boots.",
                evidence=evidence,
            )
        ],
    )


def _bible(direction: str, name: str) -> StyleBible:
    return StyleBible(
        direction=direction,
        name=name,
        rationale="Because the book is funnier when the line is exact.",
        mood_words=["precise", "absurd"],
        palette=[PaletteColor(name="Ink", hex="#1b1b1b")],
        lighting="Flat daylight, no drama.",
        influences=["John Tenniel"] if direction == "traditional" else [],
        style_prompt_block="A wood-engraved line drawing, cross-hatched. " * 4,
    )


def _report(*, traditional: bool = True, block_ids: list[int] | None = None):
    return LiteraryResearchReport(
        title="Alice",
        author="Lewis Carroll",
        author_profile=_author(),
        world_dossier=_dossier(block_ids=block_ids),
        style_bibles=StyleBibleCandidates(
            original=_bible("original", "Diorama's Alice"),
            traditional=_bible("traditional", "After Tenniel") if traditional else None,
        ),
        cost_usd=0.0123,
    )


def _structure() -> EbookStructure:
    return EbookStructure(
        title="Alice",
        author="Lewis Carroll",
        level_types=["chapter"],
        root=[
            StructureNode(
                level_type="chapter",
                number="I",
                title="Down the Rabbit-Hole",
                start_block_id=0,
                end_block_id=999,
                text="Alice was beginning to get very tired.",
            )
        ],
        coverage=Coverage(covered=True, total_blocks=1000, assigned_blocks=1000),
        cost_usd=0.004,
    )


def _shelve(book_id: str = BOOK_ID) -> BookRecord:
    record = BookRecord(
        id=book_id,
        title="Alice",
        source_filename="alice.epub",
        status="ready",
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    asyncio.run(store.upsert_book(record))
    return record


def _prepare(book_id: str = BOOK_ID, *, with_structure: bool = True) -> None:
    """A shelved, processed book with its upload and structure on disk."""
    _shelve(book_id)
    store.upload_path(book_id).write_bytes(b"not really an epub")
    if with_structure:
        store.structure_path(book_id).write_text(_structure().model_dump_json())


# --------------------------------------------------------------------------- #
# The run
# --------------------------------------------------------------------------- #
def test_a_successful_run_writes_a_complete_record(data_dir: Path) -> None:
    _prepare()
    StubResearcher.report = _report()
    run = RunLog()

    asyncio.run(research._run_research(BOOK_ID, run))

    record = research.read_record(BOOK_ID)
    assert record is not None
    assert record.status == "complete"
    assert record.error is None
    assert record.author_profile is not None
    assert record.style_bibles is not None
    assert record.style_bibles.traditional is not None
    # The reader hasn't chosen yet, so the always-present candidate is active.
    assert record.chosen_direction == "original"


def test_the_run_publishes_a_trace_and_a_done_line(data_dir: Path) -> None:
    _prepare()
    StubResearcher.report = _report()
    run = RunLog()

    asyncio.run(research._run_research(BOOK_ID, run))

    kinds = [line.kind for line in run.log]
    assert kinds[0] == "status"
    assert kinds[-1] == "done"
    assert run.finished
    # The search tool's raw JSON never reaches the reader — a phrase does.
    search_lines = [line for line in run.log if line.tool == "web_search"]
    assert [line.status for line in search_lines] == ["pending", "done"]
    assert 'Searching the web for "Tenniel"' in search_lines[0].text
    assert search_lines[1].text == "Searched the web"
    assert "{...raw json...}" not in " ".join(line.text for line in run.log)


def test_the_structure_is_handed_to_the_agent_when_one_exists(data_dir: Path) -> None:
    _prepare()
    StubResearcher.report = _report()

    asyncio.run(research._run_research(BOOK_ID, RunLog()))

    # Its own run id, not the upload's: one research pass is its own line item.
    assert StubResearcher.seen_kwargs["run_id"]
    assert StubResearcher.seen_kwargs["book_id"] == BOOK_ID


def test_a_book_with_no_structure_still_researches(data_dir: Path) -> None:
    """The outline is optional — the agent falls back to the TOC and headings."""
    _prepare(with_structure=False)
    StubResearcher.report = _report()

    asyncio.run(research._run_research(BOOK_ID, RunLog()))

    assert research.read_record(BOOK_ID) is not None


def test_a_partial_run_keeps_what_it_finished(data_dir: Path) -> None:
    _prepare()
    StubResearcher.error = LiteraryResearchError(
        "LiteraryResearchAgent did not complete research for 'Alice' — "
        "missing submit_style_bibles (ran out of turns)",
        partial={
            "author_profile": _author(),
            "world_dossier": _dossier(),
            "style_bibles": None,
        },
    )
    run = RunLog()

    asyncio.run(research._run_research(BOOK_ID, run))

    record = research.read_record(BOOK_ID)
    assert record is not None
    assert record.status == "partial"
    assert record.author_profile is not None
    assert record.world_dossier is not None
    assert record.style_bibles is None
    # The agent's class name is an implementation detail; the reason is not.
    assert "LiteraryResearchAgent" not in (record.error or "")
    assert "ran out of turns" in (record.error or "")
    assert run.log[-1].kind == "error"


def test_a_crash_before_any_artifact_records_the_failure(data_dir: Path) -> None:
    _prepare()
    StubResearcher.error = RuntimeError("the provider hung up")

    asyncio.run(research._run_research(BOOK_ID, RunLog()))

    record = research.read_record(BOOK_ID)
    assert record is not None
    assert record.status == "partial"
    assert record.author_profile is None
    assert record.error


def test_a_failed_retry_never_destroys_a_complete_moodboard(data_dir: Path) -> None:
    """The whole point of retry is that pressing it can't cost you what you had."""
    _prepare()
    StubResearcher.report = _report()
    asyncio.run(research._run_research(BOOK_ID, RunLog()))
    before = research.read_record(BOOK_ID)
    assert before is not None and before.status == "complete"

    StubResearcher.report = None
    StubResearcher.error = RuntimeError("this time it fell over")
    asyncio.run(research._run_research(BOOK_ID, RunLog()))

    after = research.read_record(BOOK_ID)
    assert after is not None
    assert after.status == "complete"
    assert after.model_dump() == before.model_dump()


def test_a_front_loaded_dossier_earns_an_advisory_note(data_dir: Path) -> None:
    """Advisory, not an error: some books really do establish everything up front."""
    _prepare()
    StubResearcher.report = _report(block_ids=[1, 2, 3, 4, 5, 6])

    asyncio.run(research._run_research(BOOK_ID, RunLog()))

    record = research.read_record(BOOK_ID)
    assert record is not None
    assert record.status == "complete"  # still accepted
    assert record.coverage_notes


def test_a_well_spread_dossier_earns_no_note(data_dir: Path) -> None:
    _prepare()
    StubResearcher.report = _report(block_ids=[10, 500, 950])

    asyncio.run(research._run_research(BOOK_ID, RunLog()))

    record = research.read_record(BOOK_ID)
    assert record is not None
    assert record.coverage_notes == []


def test_a_missing_upload_fails_gracefully(data_dir: Path) -> None:
    _shelve()  # shelved, but the epub is gone

    run = RunLog()
    asyncio.run(research._run_research(BOOK_ID, run))

    assert run.log[-1].kind == "error"
    assert research.read_record(BOOK_ID) is None


# --------------------------------------------------------------------------- #
# The API
# --------------------------------------------------------------------------- #
@pytest.fixture
def client(data_dir: Path) -> TestClient:
    return TestClient(app)


def test_research_is_404_until_someone_asks_for_it(client: TestClient) -> None:
    """Lazy by design: an uploaded book has no moodboard until one is opened."""
    _prepare()
    assert client.get(f"/api/books/{BOOK_ID}/research").status_code == 404


def test_research_is_404_for_an_unknown_book(client: TestClient) -> None:
    assert client.get("/api/books/nope/research").status_code == 404


def test_a_stored_record_is_served(client: TestClient) -> None:
    _prepare()
    StubResearcher.report = _report()
    asyncio.run(research._run_research(BOOK_ID, RunLog()))

    body = client.get(f"/api/books/{BOOK_ID}/research").json()
    assert body["status"] == "complete"
    assert body["author_profile"]["name"] == "Lewis Carroll"
    assert body["style_bibles"]["traditional"]["name"] == "After Tenniel"


def test_choosing_a_style_persists(client: TestClient) -> None:
    _prepare()
    StubResearcher.report = _report()
    asyncio.run(research._run_research(BOOK_ID, RunLog()))

    body = client.patch(
        f"/api/books/{BOOK_ID}/research/style", json={"direction": "traditional"}
    ).json()
    assert body["chosen_direction"] == "traditional"
    assert research.read_record(BOOK_ID).chosen_direction == "traditional"


def test_choosing_a_traditional_style_that_doesnt_exist_is_rejected(
    client: TestClient,
) -> None:
    _prepare()
    StubResearcher.report = _report(traditional=False)
    asyncio.run(research._run_research(BOOK_ID, RunLog()))

    response = client.patch(
        f"/api/books/{BOOK_ID}/research/style", json={"direction": "traditional"}
    )
    assert response.status_code == 422
    assert research.read_record(BOOK_ID).chosen_direction == "original"


def test_choosing_a_style_before_the_bibles_exist_is_a_conflict(
    client: TestClient,
) -> None:
    _prepare()
    StubResearcher.error = LiteraryResearchError(
        "LiteraryResearchAgent did not complete research for 'Alice' — missing x (y)",
        partial={
            "author_profile": _author(),
            "world_dossier": None,
            "style_bibles": None,
        },
    )
    asyncio.run(research._run_research(BOOK_ID, RunLog()))

    response = client.patch(
        f"/api/books/{BOOK_ID}/research/style", json={"direction": "original"}
    )
    assert response.status_code == 409


def test_deleting_a_book_takes_its_research_with_it(client: TestClient) -> None:
    _prepare()
    StubResearcher.report = _report()
    asyncio.run(research._run_research(BOOK_ID, RunLog()))
    assert store.research_path(BOOK_ID).exists()

    client.delete(f"/api/books/{BOOK_ID}")

    assert not store.research_path(BOOK_ID).exists()


def test_the_stream_short_circuits_for_an_already_researched_book(
    client: TestClient,
) -> None:
    """A complete record means there is nothing to watch — and nothing to re-run."""
    _prepare()
    StubResearcher.report = _report()
    asyncio.run(research._run_research(BOOK_ID, RunLog()))
    research._runs.clear()

    with client.stream("GET", f"/api/books/{BOOK_ID}/research/stream") as response:
        body = "".join(response.iter_text())

    assert "Already researched." in body
    assert BOOK_ID not in research._runs  # no rival run was started


def test_retrying_a_complete_record_actually_re_runs_it(client: TestClient) -> None:
    """The bug this guards: reset-only retry would replay "already researched"."""
    _prepare()
    StubResearcher.report = _report()
    asyncio.run(research._run_research(BOOK_ID, RunLog()))
    research._runs.clear()

    response = client.post(f"/api/books/{BOOK_ID}/research/retry")

    assert response.status_code == 202
    assert BOOK_ID in research._runs


def test_retrying_an_unknown_book_is_404(client: TestClient) -> None:
    assert client.post("/api/books/nope/research/retry").status_code == 404
