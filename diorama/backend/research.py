"""The moodboard's research pass: storage, and the background run behind it.

Unlike upload processing, this phase is **lazy**. Nothing here runs when a book is
uploaded; the run starts the first time someone opens that book's moodboard in the
reader, because a book nobody wants a moodboard for should not cost anything to shelve.
Opening the moodboard *is* the request — there is no confirmation step — so the modal
opens straight into the agent's live trace and becomes the moodboard when it settles.

Three consequences shape everything below:

* **Research can never fail a book.** By the time it can run at all, the book is
  already shelved and readable. A run that dies mid-way persists whatever artifacts it
  did complete (:attr:`~diorama.agents.literary_research_agent.LiteraryResearchError.partial`)
  as a ``partial`` record, and the modal renders what exists rather than throwing away
  two good artifacts because a third never arrived.
* **Watching is detached from running.** Closing the modal unsubscribes a queue; it
  never cancels the task. The spend so far should produce artifacts, not evaporate
  because a modal was dismissed — and reopening replays the whole trace and rejoins the
  live tail (:class:`~diorama.backend.runs.RunLog`).
* **It is its own run in the ledger.** The upload run settled long ago, so research
  mints a fresh ``run_id`` rather than extending one; ``agent_id`` on every row is what
  puts it in its own line on the cost page.

The record on disk (:class:`ResearchRecord`) is an envelope around the agent's
artifacts rather than the agent's own report, because two things outlive any single
run: whether the run was complete, and which style candidate the *reader* chose.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

from diorama.agents.literary_research_agent import (
    AGENT_ID,
    DEFAULT_STYLE_DIRECTION,
    AuthorProfile,
    LiteraryResearchAgent,
    LiteraryResearchError,
    LiteraryResearchReport,
    StyleBibleCandidates,
    WorldDossier,
    coverage_warnings,
)
from diorama.backend.models import TraceLine
from diorama.backend.runs import DONE, RunLog
from diorama.backend.settings import resolve_agent_runtime, resolve_search_runtime
from diorama.backend.store import (
    get_book,
    research_path,
    structure_path,
    upload_path,
)
from diorama.backend.trace import event_to_trace_line
from diorama.backend.usage_store import make_sink, run_cost
from diorama.core.common_tools import WebSearchTool
from diorama.ebook.models import EbookStructure
from diorama.models.usage import new_run_id

logger = logging.getLogger("diorama.backend")

#: This agent's key in the settings registry — the same id it stamps on ledger rows.
RESEARCH_AGENT_ID = AGENT_ID

StyleDirection = Literal["original", "traditional"]
ResearchStatus = Literal["complete", "partial"]


# --------------------------------------------------------------------------- #
# The persisted envelope
# --------------------------------------------------------------------------- #
class ResearchRecord(BaseModel):
    """One book's research artifacts, as persisted to ``research/{book_id}.json``.

    An envelope rather than a bare
    :class:`~diorama.agents.literary_research_agent.LiteraryResearchReport`, for two
    reasons the report itself can't carry:

    * A **partial** run has no report — it has one or two artifacts and a reason the
      third never came. Every field is therefore nullable, and ``status`` says which
      state this is rather than making the reader infer it from a missing key.
    * ``chosen_direction`` is the *reader's* choice, not the agent's, and it lives
      here rather than on the book record because it is research state: re-running
      research legitimately resets it, and deleting research takes it along.

    Attributes:
        status (ResearchStatus): ``complete`` when all three artifacts landed.
        error (str | None): Why a partial run stopped, phrased for a reader. Always
            None on a complete record.
        chosen_direction (StyleDirection): Which style candidate is active. Defaults
            to ``original`` — the one candidate that always exists.
        coverage_notes (list[str]): Advisory notes from
            :func:`~diorama.agents.literary_research_agent.coverage_warnings`. Not
            errors: a front-loaded dossier is suspicious, not wrong.
        cost_usd (float | None): This run's spend, read back from the ledger so a
            failed run still reports what it cost. None means unmeasured, which is a
            different claim from zero.
    """

    book_id: str
    status: ResearchStatus
    error: str | None = None
    chosen_direction: StyleDirection = DEFAULT_STYLE_DIRECTION
    author_profile: AuthorProfile | None = None
    world_dossier: WorldDossier | None = None
    style_bibles: StyleBibleCandidates | None = None
    coverage_notes: list[str] = Field(default_factory=list)
    cost_usd: float | None = None
    created_at: str
    updated_at: str

    @property
    def has_traditional(self) -> bool:
        return bool(self.style_bibles and self.style_bibles.traditional)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_record(book_id: str) -> ResearchRecord | None:
    """The stored record for ``book_id``, or None when nobody has researched it.

    A missing file is the normal state for most books, so a *malformed* one is treated
    the same way — the moodboard offering to research again beats it refusing to open.
    """
    path = research_path(book_id)
    if not path.exists():
        return None
    try:
        return ResearchRecord.model_validate_json(path.read_text())
    except Exception:  # noqa: BLE001
        logger.warning("Unreadable research record for book %s", book_id, exc_info=True)
        return None


def write_record(record: ResearchRecord) -> ResearchRecord:
    research_path(record.book_id).write_text(record.model_dump_json(indent=2))
    return record


def _record_from_parts(
    book_id: str,
    *,
    author_profile: AuthorProfile | None,
    world_dossier: WorldDossier | None,
    style_bibles: StyleBibleCandidates | None,
    error: str | None,
    cost_usd: float | None,
    total_blocks: int,
    previous: ResearchRecord | None,
) -> ResearchRecord:
    """Assemble the envelope from whatever a run produced.

    ``chosen_direction`` deliberately does **not** carry over from ``previous``: a
    re-run's style bibles are new writing, and a selection made against the old pair
    would be pointing at a candidate that no longer exists in the form it was chosen
    in. ``created_at`` does carry over — the book has been researched since then, even
    if this particular attempt is a retry.
    """
    complete = (
        author_profile is not None
        and world_dossier is not None
        and style_bibles is not None
    )
    notes = (
        coverage_warnings(world_dossier.model_dump(), total_blocks=total_blocks)
        if world_dossier is not None and total_blocks > 0
        else []
    )
    now = _now_iso()
    return ResearchRecord(
        book_id=book_id,
        status="complete" if complete else "partial",
        error=None if complete else error,
        chosen_direction=DEFAULT_STYLE_DIRECTION,
        author_profile=author_profile,
        world_dossier=world_dossier,
        style_bibles=style_bibles,
        coverage_notes=notes,
        cost_usd=cost_usd,
        created_at=previous.created_at if previous else now,
        updated_at=now,
    )


def _record_from_report(
    book_id: str,
    report: LiteraryResearchReport,
    *,
    cost_usd: float | None,
    total_blocks: int,
    previous: ResearchRecord | None,
) -> ResearchRecord:
    return _record_from_parts(
        book_id,
        author_profile=report.author_profile,
        world_dossier=report.world_dossier,
        style_bibles=report.style_bibles,
        error=None,
        cost_usd=cost_usd,
        total_blocks=total_blocks,
        previous=previous,
    )


def _user_facing_error(exc: Exception) -> str:
    """Strip the agent's own class-name preamble, keeping the concrete reason.

    ``LiteraryResearchError`` reads as ``"LiteraryResearchAgent did not complete
    research for '<title>' — missing world_dossier (<reason>)"``. The agent's class
    name is an implementation detail; what happened is not.
    """
    if isinstance(exc, LiteraryResearchError):
        text = str(exc)
        if "—" in text:
            return f"The research pass stopped early: {text.split('—', 1)[1].strip()}"
        return "The research pass stopped before it finished."
    return "Something went wrong while researching this book."


# --------------------------------------------------------------------------- #
# The background run
# --------------------------------------------------------------------------- #
_runs: dict[str, RunLog] = {}


def _load_structure(book_id: str) -> EbookStructure | None:
    """The book's structure, exposed to the agent through ``get_outline``.

    Optional by design: without it the agent orients itself from the TOC and headings
    instead, so a book shelved before structures were saved still researches fine.
    """
    path = structure_path(book_id)
    if not path.exists():
        return None
    try:
        return EbookStructure.model_validate_json(path.read_text())
    except Exception:  # noqa: BLE001
        logger.warning("Unreadable structure for book %s", book_id, exc_info=True)
        return None


async def _build_search_tool() -> WebSearchTool:
    """The web-search tool, bound to whichever provider this install has a key for.

    Resolved here rather than left to the tool's own environment lookup so a key saved
    on the settings page is honoured — the tool reads env vars, and settings shadow
    them. With no key anywhere the default tool is returned unchanged: it reports
    itself unavailable as a normal tool result, and the agent works from the text
    alone, which is the documented degradation rather than a failure.
    """
    resolved = await resolve_search_runtime()
    if resolved is None:
        return WebSearchTool()
    provider_id, api_key = resolved
    return WebSearchTool(provider=provider_id, api_key=api_key)  # type: ignore[arg-type]


def _line(book_id: str, kind: str, status: str, text: str) -> TraceLine:
    return TraceLine(
        id=f"research-{kind}-{book_id}",
        kind=kind,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        text=text,
        at=time.time(),
    )


async def _run_research(book_id: str, run: RunLog) -> None:
    """Research one book start to finish, publishing every move as a trace line."""
    book = await get_book(book_id)
    if book is None:
        run.publish(_line(book_id, "error", "error", "That book is no longer here."))
        run.close()
        return

    epub_path = upload_path(book_id)
    if not epub_path.exists():
        run.publish(
            _line(
                book_id,
                "error",
                "error",
                "The original upload is no longer available, so there's nothing to "
                "research.",
            )
        )
        run.close()
        return

    previous = read_record(book_id)
    structure = _load_structure(book_id)
    total_blocks = structure.coverage.total_blocks if structure else 0

    model_id, api_key = await resolve_agent_runtime(RESEARCH_AGENT_ID)
    search_tool = await _build_search_tool()
    # Its own run id: the upload run settled long before anyone opened the moodboard,
    # and one research pass is its own line item on the cost page.
    run_id = new_run_id()
    agent = LiteraryResearchAgent(
        model_id=model_id,
        api_key=api_key,
        usage_sink=make_sink(book_id),
        book_id=book_id,
        run_id=run_id,
        search_tool=search_tool,
    )

    run.publish(
        _line(
            book_id,
            "status",
            "pending",
            "Reading the book, then looking up its author and the world it's set in.",
        )
    )

    try:
        events, finalize = agent.stream_research(epub_path, structure=structure)
        async for event in events:
            line = event_to_trace_line(event)
            if line is not None:
                run.publish(line)
        report = finalize()
    except Exception as exc:  # noqa: BLE001 — research must never take a book down
        logger.exception("Research failed for book %s", book_id)
        partial = getattr(exc, "partial", {}) or {}
        message = _user_facing_error(exc)
        # **A failed retry never destroys a good moodboard.** If the book already had
        # a complete record, it stays exactly as it was and the failure lives only in
        # the trace the reader is watching — overwriting three finished artifacts with
        # one run's bad luck would punish them for pressing "research again".
        if previous is not None and previous.status == "complete":
            logger.info(
                "Keeping the existing complete research record for book %s", book_id
            )
        else:
            write_record(
                _record_from_parts(
                    book_id,
                    author_profile=partial.get("author_profile"),
                    world_dossier=partial.get("world_dossier"),
                    style_bibles=partial.get("style_bibles"),
                    error=message,
                    # Read from the ledger, not the agent: it raised before its
                    # cumulative totals could be read, but every call it made is
                    # already on disk.
                    cost_usd=run_cost(book_id, run_id) or None,
                    total_blocks=total_blocks,
                    previous=previous,
                )
            )
        run.publish(_line(book_id, "error", "error", message))
        run.close()
        return

    record = _record_from_report(
        book_id,
        report,
        cost_usd=run_cost(book_id, run_id) or report.cost_usd or None,
        total_blocks=total_blocks,
        previous=previous,
    )
    write_record(record)
    dossier = report.world_dossier
    directions = 2 if report.style_bibles.traditional else 1
    run.publish(
        _line(
            book_id,
            "done",
            "done",
            f"Researched {report.author_profile.name or book.title} — "
            f"{len(dossier.time_periods)} periods, {len(dossier.locations)} locations, "
            f"{directions} art direction{'s' if directions != 1 else ''} to choose from.",
        )
    )
    run.close()


def ensure_started(book_id: str) -> RunLog:
    """Start (or return the existing) research run for ``book_id``.

    Idempotent on purpose: a second tab, a reopened modal, and a page refresh all call
    this, and all of them should join the run already in flight rather than start a
    rival one. Callers are responsible for not calling it when a complete record
    already exists (see the stream route).
    """
    run = _runs.get(book_id)
    if run is None:
        run = RunLog()
        _runs[book_id] = run
        run.task = asyncio.create_task(_run_research(book_id, run))
    return run


def is_running(book_id: str) -> bool:
    """Whether a research run for ``book_id`` is in flight right now."""
    run = _runs.get(book_id)
    return run is not None and not run.finished


def restart(book_id: str) -> RunLog:
    """Drop the last run and start a fresh one — what "research again" does.

    Starting the run *here* rather than leaving it to the next stream open is what
    makes retrying a **complete** record work at all: the stream endpoint
    short-circuits when a complete record exists and nothing is running, so a retry
    that only reset the registry would replay "already researched" and never re-run.

    A run already in flight is left alone. The reader is watching that run, a second
    one would race it to write the same record, and "again" while it is still going
    the first time is not a request anyone means.
    """
    if is_running(book_id):
        return _runs[book_id]
    _runs.pop(book_id, None)
    return ensure_started(book_id)


def reset(book_id: str) -> None:
    """Drop any finished/failed run so the next ``ensure_started`` starts fresh."""
    _runs.pop(book_id, None)


__all__ = [
    "DONE",
    "RESEARCH_AGENT_ID",
    "ResearchRecord",
    "ResearchStatus",
    "StyleDirection",
    "ensure_started",
    "is_running",
    "read_record",
    "reset",
    "restart",
    "write_record",
]
