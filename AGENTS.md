# Repository Guidelines

## Project Structure & Module Organization

- `diorama/agents/` contains the shared API and Tau-based agents. `ebook_structure_agent/` separates prompts, tools, parsing, and validation.
- `diorama/models/storybook.py` defines the Pydantic book and recursive structure models.
- `tests/` contains Python tests; `scripts/process_ebook.py` runs extraction.
- `frontend/` is the React/TypeScript reader: `src/` holds components and utilities, `server/runs.mjs` exposes local run files, `tests/` contains unit tests, and `e2e/` contains browser tests. Fonts are bundled through Fontsource.
- `books/` and `.ebook-runs/` are ignored local data. Completed runs contain `output/storybook.json`.

## Build, Test, and Development Commands

Use Python 3.12+ and Node.js 22.12+. Run Python commands from the repository root:

```bash
uv sync --group dev                    # Install Python dependencies
uv run pytest -q                      # Run Python tests
uv run ruff check diorama tests scripts
uv run ruff format diorama tests scripts
uv build                              # Build Python distributions
uv run python scripts/process_ebook.py books/example.epub
```

Run frontend commands inside `frontend/`:

```bash
npm ci                 # Install locked dependencies
npm run dev            # Reader at http://127.0.0.1:5173
npm test               # Vitest unit tests
npm run test:e2e       # Playwright browser tests
npm run build          # Type-check and build
npm run format:check   # Check Prettier formatting
```

Browser tests use installed Chrome on macOS; otherwise install Chromium with `npx playwright install chromium`.

## Coding Style & Naming Conventions

Python uses four-space indentation, double quotes, type annotations, and Ruff formatting/import sorting. Use `snake_case` for modules/functions and `PascalCase` for classes. Frontend code uses Prettier's two-space indentation, single quotes, and no semicolons. Components use `PascalCase.tsx`; utilities use lowercase `.ts` filenames.

## Architecture & Testing Guidelines

New agents inherit `BaseDioramaAgent[OutputT]`, implement `configure()`, and expose typed asynchronous `run()` methods. Keep domain rules in concrete agents. Match frontend types and hierarchy navigation to the Python models.

Use pytest/pytest-asyncio with `test_*.py`, Vitest with `*.test.ts`, and Playwright with `*.spec.ts`. Prefer fake providers and synthetic books for automated tests. Cover validation, content order, cancellation, hierarchy, and file confinement with focused regressions. No coverage percentage is configured.

## Commit & Pull Request Guidelines

Existing commits use short action prefixes such as `add:`, `update:`, and `refactor:`. Describe the affected feature. PRs should explain the problem, resulting behavior, and validation performed; link relevant issues and include screenshots for interface changes.

## Security & Configuration

Keep credentials, private ebooks, and generated runs out of commits. `EBOOK_RUNS_DIR` overrides the reader's run directory; `PLAYWRIGHT_CHROMIUM_EXECUTABLE` selects a test browser. Preserve HTML sanitization and filesystem confinement when extending the reader.
