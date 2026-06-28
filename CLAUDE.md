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

**Source:** `/diorama/models/` (currently the only implementation)

### Core Modules

1. **litellm_model.py** - LLM Model Wrapper
   - `LiteLLMModel` class: Async chat completion interface
   - **Purpose:** Abstraction over litellm for cost tracking and token accounting
   - **Features:**
     - Cumulative usage tracking (prompt tokens, completion tokens, cache tokens)
     - Cost accounting for each LLM call
     - Anthropic prompt caching integration
     - OpenRouter cost reconciliation
   - **Usage:** Primary interface for all LLM calls in the project

2. **pricing.py** - OpenRouter Dynamic Pricing
   - Fetches live per-model pricing from OpenRouter API
   - **Cache Strategy:** 24-hour disk cache to avoid repeated API calls
   - **Cost Categories:** prompt, completion, cache-read, cache-write, reasoning tokens
   - **Pattern:** Singleton pricing table (call `get_pricing()` to access)
   - **Fallback:** Uses litellm static pricing if OpenRouter API is unavailable

3. **prompt_cache.py** - Anthropic Prompt Caching
   - **Purpose:** Optimize costs by marking cache breakpoints for Anthropic Claude models
   - **Strategy:** Two cache boundaries:
     - End of tool specifications (if present)
     - Rolling message window breakpoint for recent context
   - **Important:** Never mutates input objects — creates shallow copies with cache markers
   - **Multi-provider:** No-op for non-Anthropic providers; supports both Anthropic and OpenAI token extraction

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
pytest tests/test_models.py

# Run specific test function
pytest tests/test_models.py::test_litellm_model

# Run tests matching a pattern
pytest -k "test_pricing"

# Run with detailed output on failures
pytest -vvs
```

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
- `LITELLM_API_KEY` or provider-specific keys
- `OPENROUTER_API_KEY` (for OpenRouter models)
- Database connection strings (PostgreSQL)
- Any model-specific configurations

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

- **Tests:** Currently empty `/tests/` directory — tests will need to be written as features are added
- **Database:** AsyncPG + Alembic ready but no migrations defined yet
- **API:** FastAPI structure in place, endpoints not yet implemented
- **Early Stage:** v0.0.1 with core LLM infrastructure ready; main business logic to be built

## Common Development Patterns

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
- **Recent Commits:**
  - Prompt caching implementation
  - OpenRouter dynamic pricing tracking
  - Initial LLM model wrapper
- **Lock File:** `uv.lock` is committed — always run `uv sync` after pulling

## Notes for Future Contributors

1. **Database Migrations:** If adding new models, use Alembic: `alembic revision --autogenerate -m "description"`
2. **LLM Costs:** Always track costs via `LiteLLMModel` — the pricing module handles both static (litellm) and dynamic (OpenRouter) rates
3. **Async First:** All I/O is async; use `await` for database, LLM, and API calls
4. **Weave Tracing:** LiteLLM calls can be traced via Weave for debugging and cost auditing
