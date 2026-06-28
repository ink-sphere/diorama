# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Diorama** is an ebook reader that extracts structured "world models" from ebook content using Large Language Models. The project is currently in early prototype stage (v0.0.1).

**Key Goal:** Convert ebook content into rich, queryable world models through multi-step LLM processing.

## Tech Stack

- **Web Framework:** FastAPI + Uvicorn
- **Database:** PostgreSQL (asyncpg) with SQLAlchemy ORM and Alembic migrations
- **LLM Integration:** LiteLLM (multi-provider abstraction)
- **Ebook Processing:** ebooklib, BeautifulSoup4, html2text, Pillow
- **Data Validation:** Pydantic + pydantic-settings
- **LLM Tracing:** Weights & Biases Weave
- **Code Quality:** Ruff (lint + format), Black, isort
- **Testing:** Pytest + pytest-asyncio
- **Docs:** MkDocs with Material theme
- **Dependency Management:** UV (modern Python package manager)

## Architecture & Core Modules

The project is organized into three main layers:

### 1. **diorama.models** — LLM Infrastructure
- **litellm_model.py** — `LiteLLMModel` class for async chat completions
  - Cumulative usage tracking (prompt, completion, cache tokens)
  - Cost accounting per LLM call with provider-specific pricing
  - Anthropic prompt caching integration
  - OpenRouter dynamic pricing reconciliation
  - Primary interface for all LLM calls across the project
- **pricing.py** — OpenRouter dynamic pricing fetcher
  - Singleton `get_pricing()` returns live model rates
  - 24-hour disk cache to minimize API calls
  - Fallback to litellm static pricing if OpenRouter unavailable
  - Cost categories: prompt, completion, cache-read, cache-write, reasoning tokens
- **prompt_cache.py** — Anthropic prompt caching markers
  - Marks cache boundaries for Claude models
  - Two breakpoints: end of tools, rolling message window
  - Non-mutating (creates copies with cache markers)
  - Supports both Anthropic and OpenAI token extraction

### 2. **diorama.core** — ReAct Agent Framework
- **react.py** — `ReactAgent` base class
  - Native tool-calling loop with iterative refinement
  - Configurable max-iteration guard, LLM retries with backoff, per-tool approval gates
  - Returns `ReactAgentResult` typed model:
    - `final_answer: str | None` — text reply when the loop completes naturally; `None` if max-iterations fires
    - `completed: bool` — True when loop ended by choice, False on guard/timeout
    - `stop_reason` — "completed" or "max_iterations"
    - `messages`, `usage`, `cost_usd` — full conversation history and accounting
- **callback.py** — Event callbacks (for instrumentation / observability)
  - `Callback` base class; `Event` dataclass for event payloads
  - `RichLoggingCallback` — built-in console renderer for agent trace visualization
- **tool.py** — `Tool` and `ToolParameter` base classes for extensibility
  - Tools are stateless by default; optional stateful context via `ConfigDict` references
- **router.py** — `ToolRouter` for dynamic tool dispatch
  - Registers and routes tool calls from the LLM
  - Enforces tool availability and parameter schemas
- **demo_tools.py** — Example tools (`CalculatorTool`, `CurrentTimeTool`, `FinalAnswerTool`)
- **prompts.py** — System prompt for the base ReAct agent

### 3. **diorama.ebook** — EPUB Structure Extraction (full design in `docs/ebook-loader-agent.md`)
- **ebook_loader_agent.py** — `EbookLoaderAgent(ReactAgent)` specializes in hierarchical EPUB structure extraction
  - Single entry point: `await agent.load(epub_path)` returns an `EbookStructure`
  - Reusable across books (tools re-bound per call, no state pollution)
  - Configurable model (default: `gpt-4o-mini`); recommend stronger models (`gpt-4o`, `deepseek-v4-flash`) for complex books
  - Discovers arbitrary-depth hierarchies with semantic level names (e.g., `act` → `scene` for plays, `parva` → `adhyaya` for epics)
  - Six tools: `get_overview`, `get_toc`, `list_headings`, `read_blocks`, `search_blocks`, `submit_structure`
- **context.py** — `EbookContext` - deterministic EPUB parser
  - `EbookContext.parse(path)` walks the spine, flattens text into numbered blocks (coordinate system for boundaries)
  - Anchors ToC entries to block ids with fuzzy-title fallback (`thefuzz`)
  - Generous heading detection (heuristic-based; LLM makes final call)
- **slicer.py** — Deterministic tree builder and validator
  - `validate_tree()` returns human-readable errors for resubmission
  - Expands `child_pattern` (regex-based repeating levels: e.g., every "SCENE I/II/III", every "अध्याय 1/2/3")
  - `build_structure()` assigns ranges, slices text, computes coverage
- **tools.py** — EPUB-specific tools
  - `get_overview()`, `get_toc()`, `list_headings(start, end, tag_filter)`, `read_blocks(start, end)`, `search_blocks(regex, start, end)`, `submit_structure(nodes)`
  - Responses capped to fit context window
- **models.py** — Pydantic data models
  - `Block`, `TocEntry`, `StructureNode`, `CoverageReport`, `EbookStructure`
  - `SUBMIT_SCHEMA` — recursive JSON schema for the agent's output
- **prompts.py** — System instructions (`EBOOK_LOADER_INSTRUCTIONS`) and `render_load_prompt()` (pre-seeds overview + ToC)

## Development Commands

### Setup & Dependencies

```bash
# Install all dependencies (including dev & docs)
uv sync --all-groups

# Install just the runtime + dev
uv sync --group dev

# Add a new dependency
uv add package_name

# Add a dev-only dependency
uv add --group dev package_name
```

### Code Quality

```bash
# Format code (Black)
black diorama/

# Format with Ruff
ruff format diorama/

# Sort imports (isort)
isort diorama/

# Lint and check for issues
ruff check diorama/ --fix

# Run all together (one common workflow)
black diorama/ && isort diorama/ && ruff check diorama/ --fix
```

### Testing

```bash
# Run all tests
pytest

# Run with verbose output
pytest -v

# Run specific test file
pytest tests/test_react_agent.py
pytest tests/test_ebook_loader.py

# Run specific test function
pytest tests/test_react_agent.py::test_agent_basic

# Run tests matching a pattern
pytest -k "ebook"

# Run with detailed output on failures
pytest -vvs
```

### Live Testing with Real EPUBs

```python
import asyncio
from dotenv import load_dotenv
from diorama.ebook import EbookLoaderAgent

load_dotenv()
async def main():
    agent = EbookLoaderAgent(model_id="openrouter/deepseek/deepseek-v4-flash")
    structure = await agent.load("books/dracula.epub", stream=True)
    print(structure.title, structure.level_types, f"${structure.cost_usd:.4f}")
    
asyncio.run(main())
```

The `stream=True` flag prints agent reasoning + tool calls in real time. Returns `EbookStructure` with `.root` (tree of `StructureNode`s), `.coverage` (gaps/overlaps), and cost accounting.

### Documentation

```bash
# Serve docs locally (auto-reload)
mkdocs serve

# Build static docs
mkdocs build
```

### Building & Distribution

```bash
# Build the package
uv build

# Check package before upload
twine check dist/*
```

## Key Configuration

### Environment Variables

Use `.env` file (loaded via pydantic-settings) for:
- `OPENROUTER_API_KEY` — Required for EbookLoaderAgent and most agent models
- `LITELLM_API_KEY` or provider-specific keys (ANTHROPIC_API_KEY, OPENAI_API_KEY, etc.)
- Database connection strings (PostgreSQL) — when API endpoints are implemented
- Any model-specific configurations

**Note:** EbookLoaderAgent defaults to `openrouter/openai/gpt-4o-mini`; OpenRouter key is essential

### Code Style

- **Target Python:** 3.12 (via ruff config)
- **Formatter:** Black with double quotes, standard spacing
- **Import Sorting:** Ruff with custom first-party section for `diorama`
- **Line Length:** Black default (88 characters)
- **Async:** Pytest auto-detects async tests via pytest-asyncio

### Ruff Configuration

Located in `pyproject.toml`:
- `select = ["I", "F401"]` — Import sorting and unused imports only
- First-party section: `diorama`

## Project State & Considerations

- **Agent Framework:** ReAct agent infrastructure complete (native tool-calling loop, retries, approval gates, streaming)
  - `ReactAgentResult` now correctly types `final_answer: str | None` (None when loop stops before yielding text)
  - Streaming via `stream=True` prints agent trace to console
- **Ebook Extraction:** `EbookLoaderAgent` fully implemented, tested on Shakespeare (*As You Like It*), Alice in Wonderland, and Dracula
  - Discovers dynamic hierarchies (`act`→`scene`, flat `chapter`, `parva`→`adhyaya`, etc.)
  - Handles pattern-based deep levels (`child_pattern` with regex, 22 scenes discovered in *As You Like It*)
  - Full coverage validation (no gaps/overlaps) and cost accounting
  - Live runs: $0.015–0.02 per book via OpenRouter
- **Tests:** 29 passing (13 ebook-specific, 16 agent/core tests; all green after fixing dict-subscript → attribute access in `ReactAgentResult`)
- **Documentation:** Design doc at `docs/ebook-loader-agent.md` (full architecture, design decisions, worked examples)
- **Database:** AsyncPG + Alembic ready but no migrations or endpoints defined yet
- **API:** FastAPI + Uvicorn framework in place; no REST endpoints yet
- **Next:** World model extraction (entity/relationship recognition from narrative text) and API endpoints

## Common Development Patterns

### Using the ReAct Agent

```python
from diorama.core import ReactAgent, CalculatorTool, CurrentTimeTool

agent = ReactAgent(
    tools=[CalculatorTool(), CurrentTimeTool()],
    model_id="openrouter/openai/gpt-4o-mini",
    max_iterations=10
)

result = await agent.run("What is 42 * 2? What time is it?")
print(result.final_answer)
print(result.tool_calls)  # See the tool-calling history
```

### Loading an EPUB with EbookLoaderAgent

```python
from diorama.ebook import EbookLoaderAgent

agent = EbookLoaderAgent(
    model_id="openrouter/openai/gpt-4o",  # Stronger model for complex/irregular books
    max_iterations=50
)

structure = await agent.load("/path/to/book.epub")
print(structure.title)  # Book title
print(structure.level_types)  # Discovered level names (e.g., ["act", "scene"])
print(len(structure.root))  # Number of top-level nodes
print(structure.coverage.covered)  # True if all blocks assigned exactly once
print(structure.coverage.total_blocks)  # Total text blocks in the book
print(f"${structure.cost_usd:.4f}")  # OpenRouter cost
# structure.root is list[StructureNode]; each has .children, .text, .preamble_text
```

### Parsing EPUB Structure Without LLM

```python
from diorama.ebook import EbookContext

# Deterministic structure extraction (no LLM cost)
context = EbookContext.parse("/path/to/book.epub")
print(context.toc)  # List of ToC entries with hierarchy
print(context.blocks)  # Text blocks with byte ranges
```

### Adding an LLM Call

```python
from diorama.models.litellm_model import LiteLLMModel

model = LiteLLMModel(model="gpt-4", temperature=0.7)
response = await model.chat_completion(messages=[...])
print(model.usage_summary())  # See cost & token stats
```

### Using Prompt Caching (Anthropic)

```python
from diorama.models.prompt_cache import add_cache_control

messages = [...]
messages = add_cache_control(messages, model="claude-3-5-sonnet")
# Pass to LiteLLMModel for efficient caching
```

### Checking OpenRouter Pricing

```python
from diorama.models.pricing import get_pricing

pricing_table = get_pricing()  # Fetches + caches; 24-hour TTL
for model, rates in pricing_table.items():
    print(f"{model}: {rates['prompt']} per prompt token")
```

### Streaming Agent Execution

The `stream=True` flag on `agent.run()` or `agent.load()` prints agent reasoning and tool calls to the console in real time:

```python
result = await agent.run("complex task", stream=True)
```

Output shows assistant thinking, each tool call with arguments, results, and the final answer. Useful for debugging and understanding agent behavior.

## Version Control

- **Branch Model:** Feature branches off `main`
- **Recent Work (June 28, 2026):**
  - **Core Agent:** ReAct loop with tool routing, retries (backoff), approval gates; streaming output
  - **EbookLoaderAgent:** Full hierarchical structure extraction for arbitrary-depth, semantically-named hierarchies
    - Deterministic EPUB parser (block stream, anchor resolution, fuzzy ToC matching)
    - Six specialized tools (overview, ToC, heading search, block reading, regex-based pattern search, structure submission)
    - Validation + error feedback loop for self-correction
    - Pattern expansion for deep regular structures (e.g., hundreds of scenes/chapters enumerated from one regex rule)
  - **Tests:** 29 passing (13 ebook-specific, 16 core tests); fixed `ReactAgentResult` type bugs
  - **Design Doc:** `docs/ebook-loader-agent.md` (architecture, design decisions, worked examples from real books)
  - **Live Runs:** Shakespeare's *As You Like It* and *Alice in Wonderland* (Act→Scene nesting, 12-chapter flat structure, costs ~$0.015–0.02)
- **Lock File:** `uv.lock` is committed — always run `uv sync` after pulling

## Notes for Future Contributors

1. **Agent Extensions:** Subclass `ReactAgent` for domain-specific agents (as `EbookLoaderAgent` does). One agent instance can handle multiple tasks by re-binding tools and calling `run()` or `load()` multiple times. Tools can be stateful by holding a context object (e.g., `EbookContext`) via `model_config = ConfigDict(arbitrary_types_allowed=True)`.

2. **EPUB Structure Extraction (3-step pipeline):**
   - **Parse** (deterministic): `EbookContext.parse(path)` reads spine, flattens to block stream, anchors ToC with fuzzy fallback
   - **Decide** (agent loop): `EbookLoaderAgent.load(path)` uses LLM + 6 specialized tools to discover boundaries + level names + classification
   - **Build** (deterministic): `build_structure()` expands patterns, assigns ranges, slices text, validates coverage
   - **Pattern expansion:** For deep repetitive levels (100+ chapters/scenes/adhyayas), use `child_pattern` with a regex — agent describes the rule once, code enumerates all instances
   
3. **Tool Design for Ebook Agents:** Response caps (e.g., `_MAX_HEADINGS = 400`) keep outputs context-window-friendly. Validation errors are returned to the agent for self-correction, not raised as exceptions.

4. **Testing Ebook Code:** 
   - Pure Python tests (no LLM): `EbookContext` parsing, `slicer` range logic, pattern expansion, validation logic
   - FakeModel end-to-end: script tool calls to verify the agent loop and tool integration
   - Real EPUB runs: use `stream=True` to watch reasoning; sample books in `/books/`

5. **Prompt Design for Agents:**
   - `instructions` parameter appends to `SYSTEM_PROMPT` — use for domain-specific guidance without reimplementing the base loop
   - Pre-seed the first message with overview data (e.g., `render_load_prompt()`) to save a turn and orient the agent
   - Use system instructions to teach the agent to use `search_blocks()` / `read_blocks()` to avoid LLM guessing

6. **Database & Migrations:** If adding new models, use Alembic: `alembic revision --autogenerate -m "description"`

7. **LLM Costs:** Always track via `LiteLLMModel` — pricing module handles static (litellm) and dynamic (OpenRouter) rates. `EbookLoaderAgent` wraps cost tracking in the result.

8. **Async:** All I/O is async; use `await` for database, LLM, and API calls. Tests auto-detect async via pytest-asyncio.

9. **Streaming & Observability:** `stream=True` on `run()` / `load()` prints agent trace. For custom instrumentation, extend `Callback` (if event system is active) or call `agent.tool_router.calls` post-run.

## Behavioral guidelines for Agents

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

### 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

### 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

### 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

### 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.