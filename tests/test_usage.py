"""Tests for per-call cost tracking: capture, ledger, aggregation, API.

Fully offline. The capture tests drive the real :class:`ReactAgent` loop against the
scripted fakes in :mod:`tests.fakes` and assert on the ledger rows the loop actually
emitted, rather than on hand-built records — the thing worth testing is that every
call site is wired up, and a hand-built record proves nothing about that.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from diorama.backend import store, usage_store
from diorama.backend.main import app
from diorama.backend.models import BookRecord
from diorama.backend.usage_store import (
    build_book_usage,
    build_summary,
    read_calls,
    summarize,
)
from diorama.core.context import ContextCompactor
from diorama.core.demo_tools import CalculatorTool
from diorama.core.react import ReactAgent
from diorama.models.litellm_model import LiteLLMModel
from diorama.models.pricing import ModelPricing, PricingTable
from diorama.models.usage import (
    LLMCallRecord,
    extract_provider,
    extract_route,
    provider_label,
    read_provider_field,
    split_model_id,
)
from tests.fakes import FakeModel, FlakyModel, response, tool_call

# --------------------------------------------------------------------------- #
# Provenance extraction
# --------------------------------------------------------------------------- #


def test_split_model_id_handles_all_three_litellm_shapes():
    assert split_model_id("openrouter/openai/gpt-4o-mini") == (
        "openrouter",
        "openai",
        "gpt-4o-mini",
    )
    # A direct provider call: the route and the vendor are the same party.
    assert split_model_id("anthropic/claude-sonnet-4") == (
        "anthropic",
        "anthropic",
        "claude-sonnet-4",
    )
    assert split_model_id("gpt-4o-mini") == ("", "", "gpt-4o-mini")


def test_provider_label_leaves_provider_supplied_names_alone():
    # OpenRouter already sends display-cased names; only slugs need the lookup.
    assert provider_label("openai") == "OpenAI"
    assert provider_label("Together") == "Together"
    assert provider_label("some-new-host") == "Some New Host"
    assert provider_label(None) == "Unknown"


def test_read_provider_field_checks_all_three_places_litellm_may_put_it():
    assert read_provider_field(SimpleNamespace(provider="Anthropic")) == "Anthropic"
    assert read_provider_field(SimpleNamespace(model_extra={"provider": "Groq"})) == (
        "Groq"
    )
    assert read_provider_field(SimpleNamespace(_hidden_params={"provider": "Fw"})) == (
        "Fw"
    )
    assert read_provider_field(SimpleNamespace()) is None


def test_provider_falls_back_to_the_model_vendor_when_unreported():
    """A direct provider call is served by whoever you called."""
    assert extract_provider("anthropic/claude-sonnet-4", SimpleNamespace()) == (
        "anthropic"
    )
    # But an explicit report always wins — OpenRouter load-balances per request.
    assert (
        extract_provider(
            "openrouter/openai/gpt-4o-mini", SimpleNamespace(provider="Azure")
        )
        == "Azure"
    )


def test_route_prefers_litellms_own_dispatch_over_the_model_id():
    assert extract_route("openrouter/openai/gpt-4o-mini") == "openrouter"
    assert (
        extract_route(
            "gpt-4o-mini",
            SimpleNamespace(_hidden_params={"custom_llm_provider": "openai"}),
        )
        == "openai"
    )
    assert extract_route("") == "unknown"


# --------------------------------------------------------------------------- #
# Capture: LiteLLMModel.record_usage
# --------------------------------------------------------------------------- #


def _model(sink) -> LiteLLMModel:
    return LiteLLMModel(
        model_id="openrouter/openai/gpt-4o-mini",
        usage_sink=sink,
        usage_labels={"run_id": "run1", "book_id": "book1", "agent_id": "ebook_loader"},
    )


def test_record_usage_emits_a_priced_attributed_row():
    rows: list[LLMCallRecord] = []
    model = _model(rows.append)

    model.record_usage(
        {"prompt_tokens": 1000, "completion_tokens": 200, "cost": 0.0042},
        response=SimpleNamespace(provider="OpenAI"),
        context={"kind": "turn", "turn": 3, "duration_ms": 1250, "streamed": True},
    )

    (row,) = rows
    assert (row.run_id, row.book_id, row.agent_id) == ("run1", "book1", "ebook_loader")
    assert (row.turn, row.kind, row.status) == (3, "turn", "ok")
    assert (row.route, row.provider) == ("openrouter", "OpenAI")
    assert (row.model_id, row.model) == (
        "openrouter/openai/gpt-4o-mini",
        "gpt-4o-mini",
    )
    assert (row.prompt_tokens, row.completion_tokens, row.total_tokens) == (
        1000,
        200,
        1200,
    )
    # OpenRouter reported a real charge, so that — not the estimate — is authoritative.
    assert row.actual_cost_usd == 0.0042
    assert row.cost_usd == 0.0042
    assert (row.duration_ms, row.streamed) == (1250, True)


def test_cost_by_type_is_reconciled_to_the_real_charge(
    monkeypatch: pytest.MonkeyPatch,
):
    """The per-type split must sum to what was actually billed, not to the estimate.

    Pinned to a synthetic rate table rather than whatever the machine has cached from
    OpenRouter, so the arithmetic is asserted rather than skipped when offline.
    """
    rates = ModelPricing(prompt=1e-6, completion=2e-6, cache_read=1e-7)
    monkeypatch.setattr(PricingTable, "get", lambda self, model_id: rates)

    rows: list[LLMCallRecord] = []
    model = _model(rows.append)
    # Estimate: 1000 * 1e-6 + 500 * 2e-6 = $0.002. Actual: $0.01 — 5x the estimate.
    model.record_usage({"prompt_tokens": 1000, "completion_tokens": 500, "cost": 0.01})

    (row,) = rows
    assert row.estimated_cost_usd == pytest.approx(0.002)
    assert row.cost_usd == 0.01
    assert row.pricing_source == "openrouter_live"
    assert sum(row.cost_by_type.values()) == pytest.approx(0.01, rel=1e-9)
    # Scaled proportionally, so the *shape* of the spend survives reconciliation.
    assert row.cost_by_type["prompt"] == pytest.approx(0.005)
    assert row.cost_by_type["completion"] == pytest.approx(0.005)


def test_cache_reads_are_priced_at_their_own_cheaper_rate(
    monkeypatch: pytest.MonkeyPatch,
):
    """A flat prompt rate would overcharge a cached prompt by an order of magnitude."""
    rates = ModelPricing(prompt=1e-6, completion=2e-6, cache_read=1e-7)
    monkeypatch.setattr(PricingTable, "get", lambda self, model_id: rates)

    rows: list[LLMCallRecord] = []
    model = _model(rows.append)
    model.record_usage(
        {
            "prompt_tokens": 1000,
            "completion_tokens": 0,
            "prompt_tokens_details": {"cached_tokens": 900},
        }
    )

    (row,) = rows
    assert row.cache_read_tokens == 900
    # 100 fresh @ 1e-6 + 900 cached @ 1e-7 = $0.00019, not 1000 @ 1e-6 = $0.001.
    assert row.cost_usd == pytest.approx(0.00019)
    assert row.cost_by_type["cache_read"] == pytest.approx(0.00009)


def test_a_direct_gemini_call_is_priced_by_googles_published_rates(
    monkeypatch: pytest.MonkeyPatch,
):
    """A ``gemini/`` id is billed by Google, so the OpenRouter table must not price it.

    Pinned against a rate table that *would* answer for any model, to prove the Google
    step is reached because of the route rather than because OpenRouter happened to
    have no entry.
    """
    monkeypatch.setattr(
        PricingTable, "get", lambda self, model_id: ModelPricing(prompt=1.0)
    )

    rows: list[LLMCallRecord] = []
    model = LiteLLMModel(
        model_id="gemini/gemini-2.5-flash",
        usage_sink=rows.append,
        usage_labels={"run_id": "run1", "book_id": "book1"},
    )
    model.record_usage(
        {
            "prompt_tokens": 1000,
            "completion_tokens": 100,
            "cache_read_input_tokens": 800,
        }
    )

    (row,) = rows
    assert row.route == "gemini"
    assert row.provider_name == "Google AI Studio"
    assert row.pricing_source == "google_static"
    # 200 fresh @ $0.30/M + 800 cached @ $0.075/M + 100 out @ $2.50/M.
    assert row.cost_usd == pytest.approx(200 * 0.30e-6 + 800 * 0.075e-6 + 100 * 2.50e-6)


def test_gemini_thinking_tokens_are_not_billed_twice(monkeypatch: pytest.MonkeyPatch):
    """litellm folds ``thoughtsTokenCount`` into completion_tokens before we see it.

    Pricing reasoning additively on top would charge for the same tokens a second
    time — so the Gemini table leaves that rate at zero, and this pins it.
    """
    rows: list[LLMCallRecord] = []
    model = LiteLLMModel(model_id="gemini/gemini-2.5-flash", usage_sink=rows.append)
    model.record_usage(
        {
            "prompt_tokens": 0,
            "completion_tokens": 500,  # already includes the 300 thinking tokens
            "completion_tokens_details": {"reasoning_tokens": 300},
        }
    )

    (row,) = rows
    assert row.reasoning_tokens == 300
    assert row.cost_usd == pytest.approx(500 * 2.50e-6)


def test_a_call_with_no_sink_still_accumulates_but_stores_nothing():
    """The default path is unchanged: cumulative totals, no ledger, no crash."""
    model = LiteLLMModel(model_id="openrouter/openai/gpt-4o-mini")
    model.record_usage({"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.001})
    assert model.cumulative["total_tokens"] == 15
    assert model.usage_sink is None


def test_a_failing_sink_never_breaks_the_run():
    """Cost tracking is observability; an unwritable ledger must not kill a book."""

    def explode(_record):
        raise OSError("disk full")

    model = _model(explode)
    model.record_usage({"prompt_tokens": 1, "completion_tokens": 1})  # must not raise
    assert model.cumulative["total_tokens"] == 2


def test_record_failure_writes_a_row_without_touching_the_totals():
    rows: list[LLMCallRecord] = []
    model = _model(rows.append)
    model.record_failure(
        TimeoutError("upstream timed out"), context={"turn": 2, "attempt": 1}
    )

    (row,) = rows
    assert row.status == "error"
    assert "timed out" in (row.error or "")
    assert row.total_tokens == 0
    assert model.cumulative["cost_usd"] == 0.0


# --------------------------------------------------------------------------- #
# Capture: the agent loop and the compactor
# --------------------------------------------------------------------------- #


def test_every_agent_turn_is_recorded_with_its_turn_number():
    """Three model calls in one run must produce three rows, numbered 1..3."""
    rows: list[LLMCallRecord] = []
    model = FakeModel(
        [
            response(
                tool_calls=[tool_call("c1", "calculator", '{"expression": "1+1"}')]
            ),
            response(
                tool_calls=[tool_call("c2", "calculator", '{"expression": "2+2"}')]
            ),
            response(content="done"),
        ],
        usage_sink=rows.append,
        usage_labels={"run_id": "r", "book_id": "b", "agent_id": "ebook_loader"},
    )
    agent = ReactAgent(tools=[CalculatorTool()], model=model)
    result = asyncio.run(agent.run("go"))

    assert result.steps == 3
    assert [r.turn for r in rows] == [1, 2, 3]
    assert all(r.kind == "turn" and r.status == "ok" for r in rows)
    assert all(r.duration_ms is not None for r in rows)
    assert {r.book_id for r in rows} == {"b"}
    assert {r.run_id for r in rows} == {"r"}


def test_retried_attempts_are_recorded_alongside_the_one_that_succeeded():
    """A run whose provider saw three requests must not report a single call."""
    import litellm

    rows: list[LLMCallRecord] = []
    model = FlakyModel(
        [response(content="ok")],
        failures=1,
        error=litellm.RateLimitError("rate limited", "openai", "gpt-4o-mini"),
    )
    model.usage_sink = rows.append
    agent = ReactAgent(tools=[], model=model)

    async def run():
        # Don't actually wait out the backoff.
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("diorama.core.react._RATE_LIMIT_DELAYS", [0])
            mp.setattr("diorama.core.react._RETRY_DELAYS", [0, 0, 0])
            await agent.run("go")

    asyncio.run(run())

    assert [r.status for r in rows] == ["retry", "ok"]
    assert rows[0].attempt == 1 and rows[1].attempt == 2
    # The failed attempt cost nothing but is still on the record.
    assert rows[0].total_tokens == 0
    assert rows[1].total_tokens == 2


def test_compaction_summaries_are_recorded_as_their_own_kind():
    """Compaction spend is real money and would otherwise hide inside turn totals."""
    rows: list[LLMCallRecord] = []
    model = FakeModel(
        [response(content="## Goal\nSummarised.")], usage_sink=rows.append
    )
    compactor = ContextCompactor(model, keep_recent_tokens=1)

    history = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "x" * 4000},
        {"role": "assistant", "content": "y" * 4000},
    ]
    result = asyncio.run(compactor.compact(history))

    assert result is not None
    (row,) = rows
    assert row.kind == "compaction"
    assert row.turn is None
    assert row.cost_usd == FakeModel.CALL_COST_USD


# --------------------------------------------------------------------------- #
# The ledger
# --------------------------------------------------------------------------- #


@pytest.fixture
def ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the ledger directory into tmp_path."""
    monkeypatch.setattr(usage_store, "USAGE_DIR", tmp_path / "usage")
    return tmp_path / "usage"


def _call(book_id: str = "book1", **overrides) -> LLMCallRecord:
    fields = {
        "run_id": "run1",
        "book_id": book_id,
        "agent_id": "ebook_loader",
        "model_id": "openrouter/openai/gpt-4o-mini",
        "model": "gpt-4o-mini",
        "route": "openrouter",
        "provider": "OpenAI",
        "started_at": "2026-07-28T10:00:00+00:00",
        "duration_ms": 1000,
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "total_tokens": 150,
        "cost_usd": 0.002,
        "estimated_cost_usd": 0.002,
        "cost_by_type": {"prompt": 0.001, "completion": 0.001},
        "pricing_source": "openrouter_live",
    }
    fields.update(overrides)
    return LLMCallRecord(**fields)


def test_ledger_round_trips_and_appends_rather_than_replacing(ledger: Path):
    usage_store.append_call("book1", _call())
    usage_store.append_call("book1", _call(turn=2))

    records = read_calls("book1")
    assert len(records) == 2
    assert usage_store.ledger_book_ids() == ["book1"]
    assert read_calls("nonexistent") == []


def test_a_torn_line_costs_one_call_not_the_whole_book(ledger: Path):
    usage_store.append_call("book1", _call())
    with usage_store.usage_path("book1").open("a", encoding="utf-8") as handle:
        handle.write('{"broken": \n')  # a crash mid-append
    usage_store.append_call("book1", _call(turn=2))

    records = read_calls("book1")
    assert [r.turn for r in records] == [None, 2]


def test_deleting_a_book_takes_its_ledger_with_it(ledger: Path):
    usage_store.append_call("book1", _call())
    usage_store.delete_usage("book1")
    assert usage_store.ledger_book_ids() == []


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #


def test_summarize_separates_recorded_attempts_from_billed_ones():
    totals = summarize(
        [
            _call(),
            _call(),
            _call(status="retry", total_tokens=0, cost_usd=0.0, cost_by_type={}),
        ]
    )
    assert totals.calls == 3
    assert totals.billed_calls == 2
    assert totals.failed_calls == 1
    assert totals.cost_usd == pytest.approx(0.004)
    assert totals.total_tokens == 300
    assert totals.cost_by_type == {"prompt": 0.002, "completion": 0.002}
    assert totals.avg_duration_ms == 1000


def test_providers_group_case_insensitively(ledger: Path):
    """`OpenAI` from OpenRouter and `openai` from a model id are the same payer."""
    usage_store.append_call("book1", _call(provider="OpenAI"))
    usage_store.append_call("book1", _call(provider="openai"))
    usage_store.append_call("book1", _call(provider="Anthropic", cost_usd=0.005))

    usage = build_book_usage("book1", None)
    assert usage is not None
    by_provider = {g.label: g.calls for g in usage.by_provider}
    assert by_provider == {"Anthropic": 1, "OpenAI": 2}
    # Sorted most expensive first.
    assert usage.by_provider[0].label == "Anthropic"


def test_book_usage_groups_reprocessing_into_separate_runs(ledger: Path):
    usage_store.append_call("book1", _call(run_id="run1", turn=1))
    usage_store.append_call(
        "book1", _call(run_id="run2", turn=1, started_at="2026-07-29T10:00:00+00:00")
    )

    usage = build_book_usage("book1", None)
    assert usage is not None
    assert [r.run_id for r in usage.runs] == ["run2", "run1"]  # newest first
    assert usage.totals.calls == 2
    assert len(usage.calls) == 2


def test_no_ledger_means_no_book_usage(ledger: Path):
    assert build_book_usage("never-processed", None) is None


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "UPLOADS_DIR", tmp_path / "uploads")
    monkeypatch.setattr(store, "STRUCTURES_DIR", tmp_path / "structures")
    monkeypatch.setattr(store, "COVERS_DIR", tmp_path / "covers")
    monkeypatch.setattr(store, "LIBRARY_FILE", tmp_path / "library.json")
    monkeypatch.setattr(usage_store, "USAGE_DIR", tmp_path / "usage")
    return TestClient(app)


def _shelve(book_id: str, title: str = "A Book") -> None:
    asyncio.run(
        store.upsert_book(
            BookRecord(
                id=book_id,
                title=title,
                source_filename="a.epub",
                status="ready",
                created_at=datetime.now(timezone.utc).isoformat(),
            )
        )
    )


def test_summary_is_empty_not_an_error_on_a_fresh_install(client: TestClient):
    body = client.get("/api/usage").json()
    assert body["totals"]["calls"] == 0
    assert body["books"] == []
    assert body["daily"] == []


def test_summary_joins_ledgers_to_the_shelf(client: TestClient):
    _shelve("book1", "Alice in Wonderland")
    usage_store.append_call("book1", _call("book1"))
    usage_store.append_call(
        "book1", _call("book1", provider="Anthropic", model="claude", cost_usd=0.01)
    )

    body = client.get("/api/usage").json()
    assert body["totals"]["calls"] == 2
    assert body["totals"]["cost_usd"] == pytest.approx(0.012)
    (row,) = body["books"]
    assert row["title"] == "Alice in Wonderland"
    assert row["known_book"] is True
    assert row["runs"] == 1
    assert sorted(row["providers"]) == ["Anthropic", "OpenAI"]
    assert [g["label"] for g in body["by_provider"]] == ["Anthropic", "OpenAI"]
    assert body["daily"][0]["date"] == "2026-07-28"


def test_books_without_a_ledger_are_absent_from_the_dashboard(client: TestClient):
    """A book processed before cost tracking has an aggregate but no attribution."""
    _shelve("old-book")
    assert client.get("/api/usage").json()["books"] == []
    assert client.get("/api/usage/books/old-book").status_code == 404


def test_book_usage_returns_every_call(client: TestClient):
    _shelve("book1")
    usage_store.append_call("book1", _call("book1", turn=1))
    usage_store.append_call("book1", _call("book1", turn=2, kind="compaction"))

    body = client.get("/api/usage/books/book1").json()
    assert body["title"] == "A Book"
    assert len(body["calls"]) == 2
    assert body["calls"][0]["turn"] == 1
    assert {g["key"] for g in body["by_kind"]} == {"turn", "compaction"}


def test_deleting_a_book_clears_it_from_the_dashboard(client: TestClient):
    _shelve("book1")
    usage_store.append_call("book1", _call("book1"))
    assert client.get("/api/usage").json()["books"] != []

    assert client.delete("/api/books/book1").status_code == 204
    assert client.get("/api/usage").json()["books"] == []
    assert client.get("/api/usage/books/book1").status_code == 404


def test_a_ledger_whose_book_vanished_is_still_reported(client: TestClient):
    """Never silently swallow spend, even when the shelf entry is gone."""
    usage_store.append_call("orphan", _call("orphan"))

    (row,) = client.get("/api/usage").json()["books"]
    assert row["known_book"] is False
    assert row["title"] == "Removed book"


def test_summary_survives_a_ledger_written_by_a_newer_version(client: TestClient):
    """Forward-compatibility: unknown fields must not 500 the whole dashboard."""
    _shelve("book1")
    path = usage_store.usage_path("book1")
    payload = _call("book1").model_dump(mode="json")
    payload["some_future_field"] = "hello"
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    assert client.get("/api/usage").json()["totals"]["calls"] == 1


async def test_build_summary_is_awaitable_directly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The aggregation entry point is usable outside the request path.

    Redirects the *library* store too, not just the ledger: ``build_summary`` joins
    against ``list_books()``, so a ledger-only fixture would read the developer's real
    ``.diorama_data``.
    """
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "LIBRARY_FILE", tmp_path / "library.json")
    monkeypatch.setattr(store, "UPLOADS_DIR", tmp_path / "uploads")
    monkeypatch.setattr(store, "STRUCTURES_DIR", tmp_path / "structures")
    monkeypatch.setattr(store, "COVERS_DIR", tmp_path / "covers")
    monkeypatch.setattr(usage_store, "USAGE_DIR", tmp_path / "usage")

    usage_store.append_call("book1", _call())
    summary = await build_summary()
    assert summary.totals.calls == 1
    assert summary.books[0].book_id == "book1"
