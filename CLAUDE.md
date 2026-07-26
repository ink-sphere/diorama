# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Diorama** is an ebook reader that extracts structured "world models" from ebook content using LLMs (v0.0.1). The codebase is a working vertical slice, not a stub: a Next.js **shelf UI** (`frontend/`) talks to a FastAPI **backend** (`diorama/backend/`) that, on upload, runs an EPUB through a ReAct **agent** (`diorama/agents/`) built on a UI-agnostic **agent framework** (`diorama/core/`) and a deterministic **EPUB parser** (`diorama/ebook/`), streaming the agent's live trace back to the browser over SSE while the book lands on the shelf.

`pyproject.toml` still declares `sqlalchemy`, `asyncpg`, `alembic`, `pillow`, and `typer`, none of which are imported anywhere — there is no database (the backend is a JSON-file store) and no CLI entrypoint (no `[project.scripts]`, no `__main__`). `books/` (sample `.epub` files) and `.diorama_data/` (`library.json`, `uploads/`, `structures/`, `covers/`) are gitignored runtime data — `.diorama_data/sessions/` is also where `JsonlSessionStore` writes agent session logs when configured, so that directory name is dual-purpose. The frontend has two pages: the shelf (`/`) and the reader (`/read/[id]`); there is no book-detail page, and illustrations are a reserved slot rather than something the backend generates.

## Architecture

### `diorama/core/` — the ReAct agent framework

`ReactAgent` (`react.py`) is the orchestrator. It owns the message history (OpenAI chat format), a `ToolRouter`, a `LiteLLMModel`, and optionally a `JsonlSessionStore` and a `ContextCompactor`. Its loop is an **async generator that yields typed events** rather than printing or returning directly — presentation is fully decoupled from the agent loop:

- **Tools**: `Tool`/`ToolParameter` (`tool.py`) are pydantic models a tool subclasses, overriding only `forward()`; `to_json_schema()` builds the OpenAI function-calling schema. `ToolRouter` (`router.py`) is a pure registry — it tracks which tools are `active` (visible to the model) vs deferred, builds the tool-spec payload sent to the LLM, and dispatches calls via reflection on `forward`'s signature (injecting `tool_call_id`, `signal`, `on_update` only if the tool declares them).
- **Deferred tool discovery**: tools can register with `active=False` and stay hidden from the model's schema until another tool's result returns `added_tool_names`, which activates them for the next turn — used to keep a large toolset out of the prompt until relevant.
- **Results**: `Tool.forward` returns a `ToolResult` (or a plain value, auto-coerced) — see `results.py`. `ToolResult` carries `is_error`, `details` (never sent to the model, only surfaced in events/logs), `added_tool_names`, `images` (attached as a separate follow-up `user` message with `image_url` parts, since the Chat Completions tool-result slot is text-only), and `terminate` (ends the run from inside a tool).
- **Events**: `events.py` defines the full typed union (`AgentStart/End`, `TurnStart/End`, `MessageStart/Update/End`, `ToolExecutionStart/Update/End`, `CompactionStart/End`, `RetryEvent`). Subscribe via `ReactAgent.subscribe()` (sync or async listeners), or consume `stream_events()`/`agent.last_result` directly (the pattern `EbookLoaderAgent.stream_load()` mirrors — see below). `ConsoleRenderer` (`rendering.py`) is the built-in Rich-based subscriber; the loop itself never touches a console or an HTTP response.
- **Sessions**: `JsonlSessionStore` (`session.py`) is an append-only JSONL **tree**, not a list — every entry has a `parent_id`, so branching/resume is just moving the "active leaf" pointer. `reset()` starts a new root without deleting history; `branch(entry_id)` rewinds the leaf without deleting the abandoned path.
- **Context management**: `ContextCompactor` (`context.py`) is consulted before every turn. When a deterministic token estimate crosses a threshold, it summarizes the older half of history via an LLM call and replaces it with a synthetic message, keeping a verbatim tail that never starts on a `role: tool` message (to avoid orphaning a tool result from its assistant call).
- **Cancellation**: `CancellationToken` (`cancellation.py`) is **cooperative, not preemptive** — polled mid-stream, between tool calls, and at turn boundaries. On stop, every orphaned tool call is answered with a synthetic interrupted-tool-result so history stays valid for the next request; this repair also runs proactively at the start of every run in case a previous run crashed mid-flight.
- **Hooks**: `before_tool_call`/`after_tool_call` run around every tool dispatch; `after_tool_call` sees every outcome, including blocked or failed calls.
- `answer.py` (`FinalAnswerTool`) and `demo_tools.py` (`CalculatorTool`, `CurrentTimeTool`) are example/utility tools. `prompts.py` holds `SYSTEM_PROMPT`.

**Design invariants worth knowing before touching `react.py`:**
- A turn ends when the model returns **no tool calls**; `final_answer` is an optional convenience tool the system prompt encourages, never a required termination signal.
- Anthropic's **signed `thinking_blocks`** must be stored on the assistant message and replayed verbatim on subsequent requests, or the provider rejects the conversation once extended thinking is engaged. Plain `reasoning_content` text is output-only and always stripped before sending. `preserve_reasoning=False` strips thinking blocks too (smaller context, but breaks continuing extended thinking).
- A streaming completion can only be retried if it produced **zero** output so far — once any delta has arrived, a retry would duplicate it, so the exception is re-raised instead.
- `finish_reason` of `length`/`max_tokens`/`content_filter` stops the run without executing that turn's tool calls, since their arguments may be truncated.
- Error classification for retries prefers exception type (e.g. `litellm.RateLimitError`) over string-matching `str(error)`, to avoid false positives from quoted status codes in error text.

### `diorama/models/` — LLM wrapper

- **`litellm_model.py`** — `LiteLLMModel`: async chat completion wrapper (`acompletion`) used by `ReactAgent`. Tracks cumulative usage (prompt/completion/cache tokens) and cost per call (`record_usage`, `cumulative`).
- **`pricing.py`** — `PricingTable` / `get_pricing()`: fetches live per-model pricing from OpenRouter with a 24-hour disk cache; falls back to litellm's static pricing if the API is unavailable. `litellm` is treated as a hard dependency here (no optional-import fallback).
- **`prompt_cache.py`** — Anthropic prompt-cache breakpoints (`apply_prompt_caching`): marks the end of tool specs and a rolling message-window boundary. Never mutates input objects — always returns shallow copies with cache markers added. No-op for non-Anthropic providers; extracts cache tokens from both Anthropic- and OpenAI-shaped usage payloads.

### `diorama/ebook/` — deterministic EPUB parsing & structure slicing

No LLM/agent dependency anywhere in this package — everything here is pure, testable logic.

- **`parser.py`** — `EbookContext.parse()` flattens an EPUB's spine documents into numbered `Block` objects (paragraphs/headings/list items, in reading order) via BeautifulSoup; this block-id sequence is the coordinate system every tool and the agent itself operate on. Also builds a block-anchored table of contents from the EPUB's own `book.toc`, resolving anchors exactly where possible and falling back to fuzzy title matching (`thefuzz`) against headings.
- **`models.py`** — `Block`, `TocEntry`, `StructureNode` (a leaf carries `text`/`segments`; a branch's range must exactly span the union of its children's ranges), `Coverage`, `EbookStructure`.
- **`slicer.py`** — `validate_tree()` / `build_structure()` deterministically turn an agent-submitted tree of block-id boundaries into an `EbookStructure`. A single internal tree walk (`_walk`) backs both validation and coverage computation, so they can't drift apart. `child_pattern` auto-expansion: a regex matched against the start of block text generates repeating children (e.g. "SCENE I", "SCENE II") without the agent listing each one by hand; any blocks before the first match become that node's `preamble_text`.

### `diorama/agents/` — `EbookLoaderAgent`

`ebook_loader.py` builds a **fresh `ReactAgent` per book** (`_build_agent`), bound to read-only tools over that book's parsed `EbookContext` (`get_overview`, `get_toc`, `list_headings`, `read_blocks`, `search_blocks`) plus `submit_structure`, which validates via `diorama.ebook.slicer.validate_tree` and only terminates the run (`ToolResult(terminate=True)`) on success — a rejected submission returns validation errors and the agent must retry.

Two entry points share `_build_agent`/`_finalize`:
- **`load()`** blocks to completion and returns the `EbookStructure` (raises `EbookLoaderError` if the run ends without one).
- **`stream_load()`** returns `(events, finalize)`, mirroring `ReactAgent.stream_events()` + `last_result` — iterate `events` fully, then call `finalize()`. This is what `diorama/backend/processing.py` consumes to fan the run's live events out over SSE instead of blocking.

The compaction reserve is bumped to 48k tokens (see the comment above `_COMPACTION_RESERVE_TOKENS`) because the default chars/4 token estimate undercounts these transcripts, which are dense with `[Block N]` markers. The default model is `openrouter/openai/gpt-4o-mini`; running it for real costs tokens/money and is never exercised by the offline test suite, which drives `ReactAgent` via `tests/fakes.py`'s scripted fakes instead.

### `diorama/backend/` — FastAPI app (the library API)

- **`main.py`** — FastAPI app; CORS restricted to `http://localhost:3000`. `load_dotenv()` runs before the router import so `OPENROUTER_API_KEY` is set before any agent code loads.
- **`store.py`** — a JSON-file library store, not a database (despite `sqlalchemy`/`asyncpg`/`alembic` sitting unused in `pyproject.toml`). A single `asyncio.Lock` guards read-modify-write of `.diorama_data/library.json`; `uploads/{id}.epub` and `structures/{id}.json` live alongside it. This reuses the exact directory layout a removed prior implementation left behind.
- **`trace.py`** — translates `diorama.core.events.AgentEvent` into small `TraceLine` rows (`kind`: status/thinking/tool/done/error; `status`: pending/done/error) for the frontend. Deliberately hides raw tool JSON (block/TOC dumps) behind short human phrasing (`_TOOL_DONE_VERBS`) rather than showing it verbatim — a past iteration of this file did dump raw JSON to the trace pane, which is the failure mode to avoid if extending it.
- **`processing.py`** — one `_BookRun` per `book_id`, **in-memory only** (`ensure_started()` starts a background task the first time anyone opens that book's stream). Each `_BookRun` holds the accumulated log plus a list of subscriber `asyncio.Queue`s, so a late subscriber (page refresh, second tab) replays the whole log then joins the live tail. Restarting the server loses in-flight runs, but the *next* `/stream` request for that book re-triggers processing from the saved upload file — self-healing, not a bug. Level-type counts are tallied and singularized across the tree (`_tally_levels`/`_singularize`) before display, because the agent doesn't always pick a consistent singular/plural `level_type` across nodes. Raw `EbookLoaderError` text is stripped of its class-name/agent-speak preamble (`_user_facing_error`) before reaching the user.
- **`routes/books.py`** — `GET/POST /api/books`, `GET /api/books/{id}`, `POST /api/books/{id}/retry`, `DELETE /api/books/{id}`, `GET /api/books/{id}/stream` (SSE, `text/event-stream`), plus the three the reader depends on: `GET /api/books/{id}/structure` (the saved `EbookStructure`; **409** — not 404 — when the book exists but hasn't finished processing), `GET /api/books/{id}/cover`, and `PATCH /api/books/{id}/progress`.
- **Covers** — `diorama/ebook/cover.py`'s `extract_cover()` is a cascade of heuristics (EPUB 3 `properties="cover-image"`, the EPUB 2 `<meta name="cover">` pointer, ebooklib's `ITEM_COVER`, then a filename guess) because EPUBs declare covers four incompatible ways. Results are cached under `.diorama_data/covers/{id}.{ext}` — including *absence*, as a `.none` marker, since the shelf requests every book's cover on every load and re-parsing a coverless EPUB each time is the failure mode that caching exists to prevent.
- **Reading position** — `BookRecord.progress` (`ReadingProgress`) stores `section_index` (an index into the structure's leaves in depth-first order — the frontend's `readBook()` walk defines that order, so changing it invalidates saved positions), plus `page`/`pages`, which are only meaningful relative to the type size and viewport that produced them; the reader scales them into its current pagination rather than trusting the page number verbatim.

Run from the repo root: `uv run uvicorn diorama.backend.main:app --reload --port 8000`.

### `frontend/` — Next.js library + reader

Next 16 App Router, TypeScript, **Tailwind v4** (`@theme` tokens in `src/app/globals.css`, OKLCH, `@custom-variant dark` keyed to next-themes' class) and **framer-motion**. Two routes: `/` (the shelf) and `/read/[id]` (the reader). Newsreader sets the book, Inter is confined to chrome.

- **`src/lib/`** — `api.ts` (the only place that talks to the backend), `types.ts` (hand-kept mirrors of the pydantic models), `structure.ts` (`readBook()` flattens an `EbookStructure` into linear `Section`s plus a `TocNode` tree), `usePagination.ts`, `useReaderPrefs.ts`, `useMediaQuery.ts`.
- **`components/library/LibraryView.tsx`** is the shelf orchestrator and the only owner of SSE: one `EventSource` per in-flight book, and trace lines merged **by id** rather than appended — a tool call's pending and done events share one id (the `tool_call_id`) so the row updates in place. (A prior version appended, which caused a React duplicate-key collision; watch for this if changing the trace payload shape.) It also optimistically flips a book to `"processing"` on its first streamed event instead of waiting for a refetch.
- **`components/reader/`** — `ReaderView.tsx` (position, restore, progress saving, keyboard nav), `Spread.tsx` (the sheet: text verso, plate recto), `TocSidebar.tsx`, `TypeMenu.tsx`, `ReaderChrome.tsx`.
- **Pagination is measured, not estimated** (`usePagination.ts`): the section's text is laid out in a CSS multi-column box whose column width is the page width and whose height is the page height, so the browser does the line breaking; one column is one page and a page turn is a translate on `x`. The column width is applied to the element **imperatively, in the frame it's measured in** — routing it through React state would need a second render before `scrollWidth` meant anything. The measurement signature must include the section's own identity (`startBlockId`), or the first measure runs against an unmounted spread and never re-runs, leaving one over-long page.
- **`structure.ts` quirks worth knowing**: `stripRepeatedHeading()` drops leading paragraphs that merely restate the heading, because a section's block range starts *at* the chapter's own `<h1>` and would otherwise print it twice and steal the drop cap; `headingFor()` avoids "Chapter II: CHAPTER II. The Pool of Tears" by checking whether the title is already labelled; `titleCase()` turns the agent's snake_case `level_type`s ("front_matter") into display labels.
- **Illustrations**: nothing generates images yet, so the recto is a designed, reserved plate slot (`PlatePage` in `Spread.tsx`) rather than a picture. When images exist, only the inside of the frame changes.
- No locked/gated chapters exist anywhere in the product — every TOC entry is navigable.
- **`frontend/AGENTS.md`** warns that this Next.js version (16.x) has breaking changes versus training data — read `node_modules/next/dist/docs/` before writing frontend code that touches routing, fonts, metadata, or other framework conventions. Note `params` is a `Promise` and `PageProps<'/read/[id]'>` is a global helper. ESLint runs the React Compiler rules: **no synchronous `setState` inside an effect** — browser-only facts (media queries, mount state, localStorage prefs) are modelled with `useSyncExternalStore` instead.
- Needs `frontend/.env.local` with `NEXT_PUBLIC_API_BASE` (defaults to `http://localhost:8000` if unset). Run with `npm run dev` inside `frontend/`.

### How the pieces connect: upload → shelf → reader

1. Frontend uploads an `.epub` → backend saves it to `uploads/{id}.epub`, creates a `BookRecord` (`status="queued"`), returns a `stream_url`.
2. Frontend opens an `EventSource` on that URL. The backend's `ensure_started()` starts a background task that calls `EbookLoaderAgent.stream_load()`.
3. Each `AgentEvent` is translated (`trace.py`) into a `TraceLine` and published to every subscriber of that book's `_BookRun`.
4. On success, the backend writes the `EbookStructure` to `structures/{id}.json` and updates the `BookRecord` (title/author/level-type breakdown/coverage/cost); on failure, it records a user-facing error and the frontend offers retry, which calls `POST /retry` then reopens the stream.
5. A ready book links to `/read/{id}`, which fetches the record and the structure, flattens the structure into sections, paginates the current one by measurement, and writes position back with `PATCH /progress` (debounced). Reopening the book restores that position, scaled into whatever pagination the current type size and window produce.

## Development Commands

### Setup & Dependencies

```bash
# Install all Python dependencies (including dev & build groups)
uv sync --all-groups

# Install just the runtime + dev
uv sync --group dev

# Add a new dependency
uv add package_name

# Frontend
cd frontend && npm install
```

### Code Quality

```bash
# Python: format + lint (use this, not black/isort — see Code Style below)
uv run ruff format diorama/ tests/
uv run ruff check diorama/ tests/ --fix

# Frontend: lint and typecheck
cd frontend && npm run lint
cd frontend && npx tsc --noEmit
```

### Testing

The pytest suite runs fully offline against fakes in `tests/fakes.py` (`FakeModel`, `StreamModel`, `ScriptedStreamModel`, `FlakyModel` — drop-in `LiteLLMModel` stand-ins driven by scripted `acompletion` responses) — no network access or API keys required.

```bash
# Run all tests
pytest

# Run a specific test file
pytest tests/test_react_agent.py        # core loop mechanics: turn termination, tool-call flow, approval, streaming, schema shape
pytest tests/test_agent_loop.py         # cancellation, event delivery, sessions, compaction, steering/follow-up queues
pytest tests/test_tools_and_model.py    # ToolResult coercion, images, hooks, reasoning/thinking-block replay, retries
pytest tests/test_ebook_parser.py       # EPUB parsing, tree validation/slicing, child_pattern expansion
pytest tests/test_ebook_loader_agent.py # EbookLoaderAgent against a scripted model
pytest tests/test_backend_routes.py     # library API: structure/cover/progress/delete, store redirected to tmp_path

# Run a specific test function, or by pattern
pytest tests/test_react_agent.py::test_litellm_model
pytest -k "test_pricing"
```

There is also `scripts/smoke_test_ebook_loader.py` — a manual, opt-in **live** smoke test (not part of the pytest suite) that runs the real agent against a real EPUB and costs real tokens: `uv run python scripts/smoke_test_ebook_loader.py books/as-you-like-it.epub [--model ...] [--stream] [--output structure.json]`.

### Running the app locally

Two servers, both from the repo root unless noted:

```bash
uv run uvicorn diorama.backend.main:app --reload --port 8000
cd frontend && npm run dev   # http://localhost:3000
```

## Key Configuration

### Environment Variables

`.env` at the repo root (loaded via `python-dotenv`/pydantic-settings):
- `OPENROUTER_API_KEY` — the one actually used, by `LiteLLMModel` and thus every agent run (default models are `openrouter/...`).
- `DIORAMA_LOADER_MODEL_ID` — optional; overrides the litellm model id `EbookLoaderAgent` runs with (see `_loader_model_id()` in `diorama/backend/processing.py`). Defaults to `openrouter/openai/gpt-4o-mini`, which is noticeably less reliable at structure extraction than `openrouter/openai/gpt-4o` — set this to trade cost for reliability.
- `DATABASE_URL`, `POSTGRES_PORT` — present in `.env` but currently unused; no database code exists.

`frontend/.env.local`:
- `NEXT_PUBLIC_API_BASE` — the backend origin the shelf UI calls; defaults to `http://localhost:8000`.

### Code Style

- **Target Python:** 3.12 per ruff config, though `requires-python` allows `>=3.10` — be mindful of this mismatch if using 3.12-only syntax.
- **Formatter & import sorting:** `ruff format` + `ruff check --fix` (`select = ["I", "F401"]`), first-party section is `diorama` (includes `diorama.backend`, `diorama.agents`, `diorama.ebook`). `pyproject.toml` still lists `black`/`isort` as dev dependencies, but there's no `[tool.black]`/`[tool.isort]` config — running them produces a different style than `ruff format` and fights it. Use ruff only.
- **Async:** pytest auto-detects async tests via `pytest-asyncio` (`asyncio_mode = "auto"`, function-scoped event loop).
- **Frontend:** `eslint-config-next` (core-web-vitals + typescript) plus the React Compiler's `react-hooks/set-state-in-effect` rule — a synchronous `setState` inside a bare `useEffect` fails lint. Browser-only facts with no natural effect (media queries, mount state, localStorage-backed preferences) are modelled with `useSyncExternalStore` instead (see `useMediaQuery.ts`, `useReaderPrefs.ts`).
