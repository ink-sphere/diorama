# Repository Guidelines

## Project Structure & Module Organization

`diorama/` contains the Python package. Put agent orchestration in
`diorama/agents/`, Pydantic data contracts in `diorama/models/`, and deterministic
EPUB parsing or shared helpers in `diorama/utils/`. Tests live in `tests/` and
should mirror the behavior they cover, such as `test_ebook_loader_source.py`.
Design notes and longer technical documents belong in `docs/`. Runtime output is
written under `.diorama/` and must remain untracked. Keep sample ebooks and other
large generated assets outside the repository.

## Build, Test, and Development Commands

- `uv sync --group dev` installs the project and development dependencies from
  `uv.lock`.
- `uv run pytest` runs the complete test suite.
- `uv run pytest tests/test_ebook_loader_source.py -q` runs the EPUB loader tests.
- `uv run ruff check .` checks imports and lint rules.
- `uv run ruff format --check .` verifies formatting without modifying files;
  use `uv run ruff format .` to apply it.
- `uv build` creates source and wheel distributions.

Optional EPUB integration tests require
`DIORAMA_TEST_BOOKS=/path/to/books uv run pytest`. They use local fixtures and do
not validate the quality of a live model.

## Coding Style & Naming Conventions

Use four-space indentation, double quotes, and type annotations for public APIs.
Follow Ruff’s configuration in `pyproject.toml`; imports are grouped with
`diorama` treated as first-party. Name modules, functions, and variables in
`snake_case`, classes in `PascalCase`, and tests as `test_<behavior>`. Keep model
interpretation inside agents and source extraction deterministic and independently
testable.

## Testing Guidelines

Pytest and `pytest-asyncio` are configured for automatic async test handling.
Add focused regression tests for parsing boundaries, malformed input, provider
failures, and persistence behavior. Tests must not require API credentials or
network access by default. Prefer synthetic EPUB fixtures and Tau’s scripted fake
provider; mark local-book checks as optional.

## Commit & Pull Request Guidelines

Recent commits use short imperative prefixes such as `add:` and `update:`. Keep
each commit focused, for example `add: validate nested ebook sections`. Pull
requests should explain the user-visible behavior, note schema or compatibility
changes, list verification commands, and link relevant issues. Include screenshots
only for visual changes. Do not mix unrelated cleanup into a feature PR.

## Security & Configuration

Never commit API keys, `.env` files, ebooks, or `.diorama/` output. Treat EPUB
markup as untrusted and sanitize it before rendering. Provider configuration and
credentials belong to callers, not source code or tests.
