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
  - Optional streaming via Rich console output
  - Returns `ReactAgentResult` with `final_answer` and tool call history
- **tool.py** — `Tool` and `ToolParameter` base classes for extensibility
  - Tools are stateless by default; optional stateful context via `ConfigDict` references
- **router.py** — `ToolRouter` for dynamic tool dispatch
  - Registers and routes tool calls from the LLM
  - Enforces tool availability and parameter schemas
- **demo_tools.py** — Example tools (`CalculatorTool`, `CurrentTimeTool`, `FinalAnswerTool`)
- **prompts.py** — System prompt for the base ReAct agent

### 3. **diorama.ebook** — EPUB Structure Extraction
- **ebook_loader_agent.py** — `EbookLoaderAgent` subclass of `ReactAgent`
  - Specializes in hierarchical EPUB structure extraction
  - Reusable across multiple books (stateful context per `load()` call)
  - Configurable model and max-iterations; defaults to GPT-4o-mini via OpenRouter
  - Returns validated `EbookStructure` with coverage metrics
- **context.py** — `EbookContext` - deterministic EPUB parser
  - Reads EPUB metadata, NCX table of contents, text blocks
  - Anchors ToC entries to text positions for precise boundaries
  - Validates tree traversal and coverage
- **slicer.py** — Deterministic tree builder and validator
  - `build_structure()` turns agent decisions into final text-filled tree
  - `validate_tree()` ensures hierarchy is well-formed
- **tools.py** — EPUB-specific tools for the agent
  - `ListTocTool`, `GetBlockTool`, `SubmitStructureTool`, etc.
- **models.py** — Pydantic data models
  - `EbookStructure`, `StructureNode`, `Block`, `CoverageReport`, `TocEntry`
- **prompts.py** — Agent instructions and dynamic prompt rendering

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

The `test.py` script demonstrates the `EbookLoaderAgent` on real EPUB files:

```bash
# Ensure OPENROUTER_API_KEY is set in .env
python test.py /path/to/book.epub
```

This will extract the EPUB's hierarchical structure and print coverage metrics.

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

- **Agent Framework:** ReAct agent infrastructure complete with tool routing, approval gates, and streaming
- **Ebook Extraction:** EbookLoaderAgent fully implemented; tested on classic literature (Shakespeare, Alice in Wonderland)
- **Tests:** Comprehensive test suites for both `ReactAgent` (`tests/test_react_agent.py`) and `EbookLoaderAgent` (`tests/test_ebook_loader.py`)
- **Database:** AsyncPG + Alembic ready but no migrations or endpoints defined yet
- **API:** FastAPI + Uvicorn framework in place; no REST endpoints yet
- **Early Stage:** v0.0.1 with LLM infrastructure and EPUB extraction complete; remaining work is world model extraction and API endpoints

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
    model_id="openrouter/openai/gpt-4o",  # Stronger model for complex books
    max_iterations=50
)

structure = await agent.load("/path/to/book.epub")
print(structure.title)  # Book title
print(len(structure.root.children))  # Number of top-level chapters
print(structure.coverage)  # CoverageReport (blocks hit, coverage %)
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

## Version Control

- **Branch Model:** Feature branches off `main`
- **Recent Work (June 28, 2026):**
  - ReAct agent framework with tool routing and approval gates
  - EbookLoaderAgent for hierarchical EPUB structure extraction
  - Deterministic EPUB parser with ToC anchoring
  - Comprehensive test suites for agent and ebook modules
- **Lock File:** `uv.lock` is committed — always run `uv sync` after pulling

## Notes for Future Contributors

1. **Agent Extensions:** Subclass `ReactAgent` for domain-specific agents (as `EbookLoaderAgent` does). Override `run()` to customize the loop or wrap the result.
2. **Tool Registration:** Tools are registered via `ToolRouter` in the agent's `tools` parameter. Tools can be stateful by storing `EbookContext` or similar in `Tool.context` via `ConfigDict`.
3. **EPUB Structure:** The ebook module handles three steps:
   - **Parse** (deterministic): `EbookContext.parse()` reads EPUB metadata, NCX, and text blocks
   - **Decide** (agent loop): `EbookLoaderAgent.load()` uses an LLM to mark boundaries and classify levels
   - **Build** (deterministic): `build_structure()` fills the final tree with text and validates coverage
4. **Testing Ebook Agents:** Use real EPUB files in tests (sample books in `/books/` if available). `EbookContext` and agent runs are fully deterministic given the same EPUB + prompt.
5. **Database Migrations:** If adding new models, use Alembic: `alembic revision --autogenerate -m "description"`
6. **LLM Costs:** Always track costs via `LiteLLMModel` — the pricing module handles both static (litellm) and dynamic (OpenRouter) rates
7. **Async First:** All I/O is async; use `await` for database, LLM, and API calls
8. **Weave Tracing:** LiteLLM calls can be traced via Weave for debugging and cost auditing
