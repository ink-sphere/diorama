"""The per-book cost ledger, and the rollups the dashboard renders.

Every LLM call Diorama makes on a book's behalf is appended, as it completes, to
``.diorama_data/usage/{book_id}.jsonl`` — one JSON object per line, never rewritten.
That shape is deliberate on three counts:

* **Append-only** means a crash mid-run costs you the last line at worst, and a run
  that dies halfway still leaves behind an accurate account of what it already spent.
  A cost record that only lands if the run succeeds would systematically under-report
  exactly the runs that went wrong.
* **Per book** keeps the file next to everything else keyed by book id (``uploads/``,
  ``structures/``, ``covers/``), so deleting a book is still one sweep of that id, and
  a book's own cost page reads exactly one file.
* **JSONL, not JSON** means writing is an append, not a read-modify-write, so a sink
  called from the middle of an async agent loop never has to parse what's already there.

The cost of that choice is that aggregation is a full scan folded in Python rather than
a ``GROUP BY``. At the scale this store is built for — a personal shelf, tens of calls
per book — that is a few milliseconds; if the ledger ever outgrows it, the record shape
here is already the row shape a table would want.

Reprocessing a book **appends a second run** rather than replacing the first, so the
cost of a retry shows up as its own line item instead of quietly overwriting the
history of what the first attempt burned through.
"""

from __future__ import annotations

import json
import logging
import threading
from collections import defaultdict
from pathlib import Path
from typing import Iterable

from pydantic import BaseModel, Field

from diorama.backend.models import BookRecord
from diorama.backend.store import DATA_DIR, list_books
from diorama.models.usage import LLMCallRecord, provider_label

logger = logging.getLogger("diorama.backend")

USAGE_DIR = DATA_DIR / "usage"

# Guards appends. The sink is a sync callable invoked from the agent's async loop; one
# process, so a plain threading lock is enough to keep two concurrent book runs from
# interleaving partial lines.
_write_lock = threading.Lock()


# --------------------------------------------------------------------------- #
# Ledger I/O
# --------------------------------------------------------------------------- #
def usage_path(book_id: str) -> Path:
    """The ledger file for ``book_id`` (not guaranteed to exist)."""
    USAGE_DIR.mkdir(parents=True, exist_ok=True)
    return USAGE_DIR / f"{book_id}.jsonl"


def append_call(book_id: str, record: LLMCallRecord) -> None:
    """Append one call record to ``book_id``'s ledger."""
    line = json.dumps(record.model_dump(mode="json"), ensure_ascii=False)
    with _write_lock:
        with usage_path(book_id).open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def make_sink(book_id: str):
    """A :data:`~diorama.models.usage.UsageSink` that appends to ``book_id``'s ledger.

    Hand this to :class:`~diorama.agents.ebook_loader.EbookLoaderAgent` and every LLM
    call it makes — turns and compaction summaries alike — lands on disk as it happens.
    """

    def sink(record: LLMCallRecord) -> None:
        append_call(book_id, record)

    return sink


def read_calls(book_id: str) -> list[LLMCallRecord]:
    """Every recorded call for ``book_id``, in the order they were made.

    Unparseable lines are skipped rather than raising: a torn final line from a crash
    mid-append should cost you one call's accounting, not the whole book's page.
    """
    path = usage_path(book_id)
    if not path.exists():
        return []
    records: list[LLMCallRecord] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(LLMCallRecord.model_validate_json(line))
        except Exception:  # noqa: BLE001 — one bad line must not sink the ledger
            logger.warning("Skipping malformed usage line in %s", path.name)
    return records


def run_cost(book_id: str, run_id: str) -> float:
    """What one run of ``book_id`` spent, according to its ledger.

    Lets a run that *failed* still report its cost. The agent raises before anything
    can read its cumulative totals, but the ledger already has every call it made —
    so a book that died halfway can say what the attempt cost instead of showing
    nothing, which reads as free.
    """
    return _round_money(
        sum(r.cost_usd for r in read_calls(book_id) if r.run_id == run_id)
    )


def ledger_book_ids() -> list[str]:
    """Book ids that have a ledger on disk."""
    if not USAGE_DIR.exists():
        return []
    return sorted(p.stem for p in USAGE_DIR.glob("*.jsonl"))


def delete_usage(book_id: str) -> None:
    """Drop ``book_id``'s ledger — called when the book leaves the shelf."""
    usage_path(book_id).unlink(missing_ok=True)


# --------------------------------------------------------------------------- #
# API-facing rollup shapes
# --------------------------------------------------------------------------- #
class UsageTotals(BaseModel):
    """Summed tokens and spend over some set of calls.

    ``calls`` counts every recorded attempt while ``billed_calls`` counts only the ones
    that succeeded, because a retried call is a real event in the trace but usually not
    a real charge — reporting one number for both would either inflate the call count
    or hide the retries.

    Attributes:
        calls (int): Every recorded attempt, successful or not.
        billed_calls (int): Attempts that completed and returned usage.
        failed_calls (int): Attempts that errored or were retried.
        prompt_tokens (int): Input tokens, cache reads included.
        completion_tokens (int): Generated output tokens.
        total_tokens (int): Prompt plus completion.
        cache_read_tokens (int): Input tokens served from the prompt cache.
        cache_write_tokens (int): Tokens written into the prompt cache.
        reasoning_tokens (int): Thinking tokens, where the provider reports them.
        cost_usd (float): Authoritative spend.
        estimated_cost_usd (float): What the rate tables predicted, for comparison.
        cost_by_type (dict[str, float]): Spend split by token type.
        avg_duration_ms (int | None): Mean latency across timed calls.
    """

    calls: int = 0
    billed_calls: int = 0
    failed_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: float = 0.0
    estimated_cost_usd: float = 0.0
    cost_by_type: dict[str, float] = Field(default_factory=dict)
    avg_duration_ms: int | None = None


class GroupTotals(BaseModel):
    """One slice of spend — a model, a provider, an agent, a call kind."""

    key: str
    label: str
    calls: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    #: Present on provider groups: which models that provider served.
    detail: list[str] = Field(default_factory=list)


class DailyPoint(BaseModel):
    """Spend on one UTC day, for the trend chart."""

    date: str
    calls: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0


class BookUsageRow(BaseModel):
    """One book's line on the dashboard overview.

    ``title`` and ``status`` are joined from the library record. A ledger with no
    matching book means the shelf entry was removed without its ledger (hand-editing,
    an interrupted delete); the row is still shown rather than dropped, because
    silently swallowing spend is the one thing a cost dashboard must never do.
    """

    book_id: str
    title: str
    author: str | None = None
    status: str | None = None
    known_book: bool = True
    runs: int = 0
    totals: UsageTotals = Field(default_factory=UsageTotals)
    models: list[str] = Field(default_factory=list)
    providers: list[str] = Field(default_factory=list)
    first_call_at: str | None = None
    last_call_at: str | None = None


class UsageSummary(BaseModel):
    """Everything the ``/costs`` overview renders."""

    totals: UsageTotals = Field(default_factory=UsageTotals)
    by_model: list[GroupTotals] = Field(default_factory=list)
    by_provider: list[GroupTotals] = Field(default_factory=list)
    by_route: list[GroupTotals] = Field(default_factory=list)
    by_agent: list[GroupTotals] = Field(default_factory=list)
    by_kind: list[GroupTotals] = Field(default_factory=list)
    daily: list[DailyPoint] = Field(default_factory=list)
    books: list[BookUsageRow] = Field(default_factory=list)


class RunGroup(BaseModel):
    """One agent run within a book's ledger — a book reprocessed twice has two."""

    run_id: str
    agent_id: str | None = None
    model_ids: list[str] = Field(default_factory=list)
    started_at: str | None = None
    ended_at: str | None = None
    totals: UsageTotals = Field(default_factory=UsageTotals)


class BookUsage(BaseModel):
    """Everything a single book's cost page renders, down to each call."""

    book_id: str
    title: str
    author: str | None = None
    status: str | None = None
    known_book: bool = True
    totals: UsageTotals = Field(default_factory=UsageTotals)
    by_model: list[GroupTotals] = Field(default_factory=list)
    by_provider: list[GroupTotals] = Field(default_factory=list)
    by_kind: list[GroupTotals] = Field(default_factory=list)
    runs: list[RunGroup] = Field(default_factory=list)
    calls: list[LLMCallRecord] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #
def _round_money(value: float) -> float:
    """Round to a micro-dollar — below that, per-call figures are noise."""
    return round(value, 6)


def summarize(records: Iterable[LLMCallRecord]) -> UsageTotals:
    """Fold a set of call records into one :class:`UsageTotals`."""
    totals = UsageTotals()
    cost_by_type: dict[str, float] = defaultdict(float)
    durations: list[int] = []

    for record in records:
        totals.calls += 1
        if record.status == "ok":
            totals.billed_calls += 1
        else:
            totals.failed_calls += 1
        totals.prompt_tokens += record.prompt_tokens
        totals.completion_tokens += record.completion_tokens
        totals.total_tokens += record.total_tokens
        totals.cache_read_tokens += record.cache_read_tokens
        totals.cache_write_tokens += record.cache_write_tokens
        totals.reasoning_tokens += record.reasoning_tokens
        totals.cost_usd += record.cost_usd
        totals.estimated_cost_usd += record.estimated_cost_usd
        for token_type, amount in record.cost_by_type.items():
            cost_by_type[token_type] += amount
        if record.duration_ms is not None:
            durations.append(record.duration_ms)

    totals.cost_usd = _round_money(totals.cost_usd)
    totals.estimated_cost_usd = _round_money(totals.estimated_cost_usd)
    totals.cost_by_type = {
        token_type: _round_money(amount)
        for token_type, amount in sorted(
            cost_by_type.items(), key=lambda kv: kv[1], reverse=True
        )
        if amount
    }
    totals.avg_duration_ms = int(sum(durations) / len(durations)) if durations else None
    return totals


def _group(
    records: Iterable[LLMCallRecord],
    key_of,
    label_of,
    detail_of=None,
) -> list[GroupTotals]:
    """Bucket records by ``key_of`` and sum them, most expensive bucket first."""
    buckets: dict[str, GroupTotals] = {}
    details: dict[str, set[str]] = defaultdict(set)
    for record in records:
        key = key_of(record)
        if key is None:
            continue
        group = buckets.get(key)
        if group is None:
            group = buckets[key] = GroupTotals(key=key, label=label_of(record))
        group.calls += 1
        group.total_tokens += record.total_tokens
        group.cost_usd += record.cost_usd
        if detail_of is not None:
            value = detail_of(record)
            if value:
                details[key].add(value)
    for key, group in buckets.items():
        group.cost_usd = _round_money(group.cost_usd)
        group.detail = sorted(details.get(key, ()))
    return sorted(
        buckets.values(), key=lambda g: (-g.cost_usd, -g.total_tokens, g.label)
    )


def _by_model(records: list[LLMCallRecord]) -> list[GroupTotals]:
    return _group(
        records, lambda r: r.model_id or None, lambda r: r.model or r.model_id
    )


def _by_provider(records: list[LLMCallRecord]) -> list[GroupTotals]:
    """Group by the upstream that served each call, listing the models it ran.

    Keyed case-insensitively: OpenRouter reports ``"OpenAI"`` while a model id yields
    ``"openai"``, and those are the same provider spending the same money.
    """
    return _group(
        records,
        lambda r: (r.provider or "unknown").lower(),
        lambda r: provider_label(r.provider),
        detail_of=lambda r: r.model or r.model_id,
    )


def _by_kind(records: list[LLMCallRecord]) -> list[GroupTotals]:
    labels = {"turn": "Agent turns", "compaction": "Context compaction"}
    return _group(records, lambda r: r.kind, lambda r: labels.get(r.kind, r.kind))


def _daily(records: Iterable[LLMCallRecord]) -> list[DailyPoint]:
    """Per-UTC-day rollup, ascending by date, with no gap-filling.

    Gaps are the frontend's business: it knows the axis it is drawing and whether an
    empty day should be a zero-height bar or a break in a line.
    """
    points: dict[str, DailyPoint] = {}
    for record in records:
        point = points.setdefault(record.date, DailyPoint(date=record.date))
        point.calls += 1
        point.total_tokens += record.total_tokens
        point.cost_usd += record.cost_usd
    for point in points.values():
        point.cost_usd = _round_money(point.cost_usd)
    return [points[date] for date in sorted(points)]


def _timespan(records: list[LLMCallRecord]) -> tuple[str | None, str | None]:
    """The first and last ``started_at`` across ``records``."""
    stamps = sorted(r.started_at for r in records if r.started_at)
    return (stamps[0], stamps[-1]) if stamps else (None, None)


def _runs(records: list[LLMCallRecord]) -> list[RunGroup]:
    """Split a book's calls into its runs, newest run first."""
    grouped: dict[str, list[LLMCallRecord]] = defaultdict(list)
    for record in records:
        grouped[record.run_id or "unknown"].append(record)

    runs: list[RunGroup] = []
    for run_id, calls in grouped.items():
        started, ended = _timespan(calls)
        runs.append(
            RunGroup(
                run_id=run_id,
                agent_id=next((c.agent_id for c in calls if c.agent_id), None),
                model_ids=sorted({c.model_id for c in calls if c.model_id}),
                started_at=started,
                ended_at=ended,
                totals=summarize(calls),
            )
        )
    return sorted(runs, key=lambda r: r.started_at or "", reverse=True)


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #
async def build_summary() -> UsageSummary:
    """Read every ledger on disk and roll it up into the dashboard's overview.

    Only books that have a ledger appear. Books processed before cost tracking existed
    carry an aggregate ``cost_usd`` on their shelf record and nothing else — including
    them here would put a book in the totals whose spend cannot be attributed to any
    model, provider, or call, which is the opposite of what this page is for.
    """
    books = {book.id: book for book in await list_books()}
    all_records: list[LLMCallRecord] = []
    rows: list[BookUsageRow] = []

    for book_id in ledger_book_ids():
        records = read_calls(book_id)
        if not records:
            continue
        all_records.extend(records)
        book = books.get(book_id)
        started, ended = _timespan(records)
        rows.append(
            BookUsageRow(
                book_id=book_id,
                title=book.title if book else "Removed book",
                author=book.author if book else None,
                status=book.status if book else None,
                known_book=book is not None,
                runs=len({r.run_id or "unknown" for r in records}),
                totals=summarize(records),
                models=sorted({r.model or r.model_id for r in records if r.model_id}),
                providers=sorted({provider_label(r.provider) for r in records}),
                first_call_at=started,
                last_call_at=ended,
            )
        )

    rows.sort(key=lambda row: row.last_call_at or "", reverse=True)
    return UsageSummary(
        totals=summarize(all_records),
        by_model=_by_model(all_records),
        by_provider=_by_provider(all_records),
        by_route=_group(
            all_records, lambda r: r.route, lambda r: provider_label(r.route)
        ),
        by_agent=_group(
            all_records,
            lambda r: r.agent_id or "unknown",
            lambda r: (r.agent_id or "unknown").replace("_", " ").capitalize(),
        ),
        by_kind=_by_kind(all_records),
        daily=_daily(all_records),
        books=rows,
    )


def build_book_usage(book_id: str, book: BookRecord | None) -> BookUsage | None:
    """One book's full cost page, or None when it has no ledger."""
    records = read_calls(book_id)
    if not records:
        return None
    return BookUsage(
        book_id=book_id,
        title=book.title if book else "Removed book",
        author=book.author if book else None,
        status=book.status if book else None,
        known_book=book is not None,
        totals=summarize(records),
        by_model=_by_model(records),
        by_provider=_by_provider(records),
        by_kind=_by_kind(records),
        runs=_runs(records),
        calls=records,
    )


__all__ = [
    "USAGE_DIR",
    "BookUsage",
    "BookUsageRow",
    "DailyPoint",
    "GroupTotals",
    "RunGroup",
    "UsageSummary",
    "UsageTotals",
    "append_call",
    "build_book_usage",
    "build_summary",
    "delete_usage",
    "ledger_book_ids",
    "make_sink",
    "read_calls",
    "run_cost",
    "summarize",
    "usage_path",
]
