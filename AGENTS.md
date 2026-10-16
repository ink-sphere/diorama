# Repository Guidelines

## Project Structure & Module Organization

- `diorama/agents/` contains the shared API and Tau-based agents. `ebook_structure_agent/` separates prompts, tools, parsing, and validation. `character_profiler_agent/` separates orchestration, prompts, and resumable checkpoints.
- `diorama/models/storybook.py` defines the Pydantic book and recursive structure models; `character_profile.py` defines character identities, aliases, descriptions, and headshot/full-body prompts.
- `tests/` contains Python tests. The Typer CLIs in `scripts/` run ebook extraction (`process_ebook.py`) and character profiling (`profile_characters.py`).
- `frontend/` is the React/TypeScript reader: `src/` holds components and utilities, `server/runs.mjs` exposes local run files, `tests/` contains unit tests, and `e2e/` contains browser tests. Fonts are bundled through Fontsource.
- `books/` and `.ebook-runs/` are ignored local data. Extraction produces `.ebook-runs/<run-id>/storybook.json`; profiling adds `character-profiles.json` and `character-profiles-checkpoint-<checkpoint-id>.json` directly beside it. Do not create a separate `.character-runs/` directory or profiling subdirectory.

## Build, Test, and Development Commands

Use Python 3.12+ and Node.js 22.12+. Run Python commands from the repository root:

```bash
uv sync --group dev                    # Install Python dependencies
uv run pytest -q                      # Run Python tests
uv run ruff check diorama tests scripts
uv run ruff format diorama tests scripts
uv build                              # Build Python distributions
uv run python scripts/process_ebook.py books/example.epub
uv run python scripts/profile_characters.py .ebook-runs/<run-id>
uv run python scripts/profile_characters.py .ebook-runs/<run-id> --max-nodes-group 3
uv run python scripts/profile_characters.py .ebook-runs/<run-id> --resume .ebook-runs/<run-id>/character-profiles-checkpoint-<checkpoint-id>.json
```

The profiling CLI takes a run directory containing `storybook.json`. Both scripts support `--model` and `--thinking-effort`; omitted effort uses the provider/model default. Character profiling shows Rich progress with traces disabled by default (`--trace` enables them); extraction enables traces by default (`--no-trace` disables them).

Run frontend commands inside `frontend/`:

```bash
npm ci                 # Install locked dependencies
npm run dev            # Reader at http://127.0.0.1:5173
npm test               # Vitest unit tests
npm run test:e2e       # Playwright browser tests
npm run build          # Type-check and build
npm run format:check   # Check Prettier formatting
npm run format         # Apply Prettier formatting
```

Browser tests use installed Chrome on macOS; otherwise install Chromium with `npx playwright install chromium`.

## Coding Style & Naming Conventions

Python uses four-space indentation, double quotes, type annotations, and Ruff formatting/import sorting. Use `snake_case` for modules/functions and `PascalCase` for classes. Frontend code uses Prettier's two-space indentation, single quotes, and no semicolons. Components use `PascalCase.tsx`; utilities use lowercase `.ts` filenames.

## Architecture & Testing Guidelines

New agents inherit `BaseDioramaAgent[OutputT]`, implement `configure()`, and expose typed asynchronous `run()` methods. Use shared `working_directory()` for temporary files and `await save_output()` for finished artifacts. Resolve caller paths without changing the process directory or depending on a checkout. Keep domain rules in concrete agents. Match frontend types and hierarchy navigation to the Python models.

The character profiler processes narrative leaves in reading order, carrying a shared registry across requests. `max_nodes_group` defaults to `1`; larger groups retain node boundaries and must fit the input budget. Prefer whole nodes, splitting oversized leaves only as needed. Model-facing references (`c1`, `c2`, etc.) are request-local; Python assigns persistent IDs, applies validated updates atomically, and preserves identity during merges. Keep prompts in `prompts.py`; no tools are exposed to the profiling model.

Both visual prompts are required in model updates. Write natural descriptive prose without headings, section labels, lists, character names, aliases, or name placeholders; identify the subject through physical appearance alone. Make them standalone, detailed, and consistent about identity and story state; retain supported visual details across updates and merges, distinguish contextual variants in prose, and explicitly identify unknowns without inventing appearance. Legacy checkpoints lacking the full-body prompt load with an explicit not-generated marker; resume does not reread completed nodes to backfill it. A fresh profiling run is needed to regenerate all visual prompts from the source.

Checkpoint only completed nodes/groups and final reconciliation. Resume validates the StoryBook content fingerprint, skips completed work, and replays unfinished work. Preserve compatibility with version 1 and version 2 checkpoints. The agent API optionally saves `character_profiles.json` under its configured workspace; the CLI instead atomically publishes `character-profiles.json` in the supplied ebook run directory without creating a staging subdirectory there. Keep this filename distinction explicit.

The frontend offers dedicated Read and Characters views. Characters loads the run's profiles lazily through `/api/runs/<run-id>/characters`, validates the schema and book ownership, and searches names and aliases. Show descriptions only after selection because profiles may contain whole-book spoilers. Render profile strings as text; appearance text is not a portrait image. Preserve the section, scroll position, and reading settings when switching views, including typeface changes while the reading pane is hidden. Standalone StoryBook imports cannot automatically load sibling profile files.

Follow `frontend/PRODUCT.md` and `frontend/DESIGN.md` for product and visual conventions. Preserve the paper/night palettes, bundled fonts, responsive cast list/profile navigation, keyboard focus, and missing/loading/error/empty states.

Use pytest/pytest-asyncio with `test_*.py`, Vitest with `*.test.ts`, and Playwright with `*.spec.ts`. Prefer fake providers and synthetic books/profiles for automated tests. Cover validation, content order, identity updates, grouped processing, checkpoint/resume, cancellation, hierarchy, file confinement, character search, and reading-position restoration with focused regressions. No coverage percentage is configured.

## Commit & Pull Request Guidelines

Existing commits use short action prefixes such as `add:`, `update:`, and `refactor:`. Describe the affected feature. PRs should explain the problem, resulting behavior, and validation performed; link relevant issues and include screenshots for interface changes.

## Security & Configuration

Keep credentials, private ebooks, generated profiles, and checkpoints out of commits. `EBOOK_RUNS_DIR` overrides the reader's run directory; `PLAYWRIGHT_CHROMIUM_EXECUTABLE` selects a test browser. Preserve HTML sanitization, file-size limits, and filesystem confinement, including symlink checks, when extending the reader. The frontend reads local artifacts without model calls or writes to ebook runs.
