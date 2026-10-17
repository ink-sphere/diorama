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

Include a character only when the supplied narrative explicitly describes at least one concrete appearance detail (including clothing or accessories). Mere mentions, names, roles, and narrative importance do not qualify. Partial descriptions qualify; do not create profiles containing only unspecified-appearance prompts. A later excerpt can establish eligibility, and profiles with established appearance details still receive narrative-only updates. Appearance eligibility is model-based, not enforced by schema validation. Existing checkpoints are not retroactively filtered; use a fresh run to apply this rule throughout a book.

Use the fullest name explicitly established in the storybook for `CharacterProfile.character_name`, even when a nickname or short form is more common. Keep other attested forms in `character_alias`. Later full-name revelations update the existing identity; subsequent updates and merges retain that full name unless the story corrects it. Never invent missing name parts or expand initials without evidence. Partial names and grounded labels for unnamed individuals remain valid. Name selection is model-based; keep the update schema description in sync with `CharacterProfile`.

Collect all explicit, non-trivial aliases in `CharacterProfile.character_alias`, including alternate names, meaningful short names, nicknames, pseudonyms, disguise names, and identifying titles or epithets used even once. Exclude the canonical name, duplicates, pronouns, generic references, passing descriptions, and trivial case, whitespace, punctuation, or possessive variants. Never invent aliases from parts of a full name or from inferred titles. Continue collecting aliases for established profiles even without new appearance details. Alias selection is model-based; keep the update schema description in sync with `CharacterProfile`. Existing checkpoints are not retroactively cleaned or backfilled.

Keep `CharacterProfile.character_profile_description` to one or two concise sentences introducing who the character is through their story-supported identity, role, key relationships, and distinguishing traits as needed. Do not recount their journey, plot events, character arc, or eventual fate. Enrichment and merges rewrite this introduction within the same limit rather than appending developments. Sparse evidence warrants one short sentence without filler. This is model guidance, not sentence-count schema validation; keep the update schema description in sync with `CharacterProfile`. Existing artifacts are not automatically rewritten.

Every model update must include non-empty `headshot_visual_prompt` and `full_body_visual_prompt` strings. Updates replace both complete strings; preserve all still-valid visual details during enrichment and merges. Keep their schema descriptions in sync with `CharacterProfile`.

Write both visual prompts as natural descriptive prose, starting directly with the physical depiction. Omit headings, section labels, lists, all characters' names and aliases, registry IDs, and name placeholders. Identify the subject through physical appearance alone, integrating composition instructions and necessary qualifications into the prose. Keep names and narrative exposition in the identity and narrative fields.

Make each visual prompt independently usable and exhaustive about supported details. The headshot covers the head, face, and visible shoulder attire; the full-body prompt repeats identifying facial features and covers the entire figure, proportions, clothing, footwear, and accessories. Keep appearance and the depicted story state consistent across both. Distinguish contextual variants in prose and explicitly identify unknowns without inventing appearance or adding filler. These are prompt instructions; non-empty schema validation does not verify generated prose quality.

Checkpoint only completed nodes/groups and final reconciliation. Fresh CLI runs create unique checkpoint files directly in the supplied ebook run directory and retain them after completion; `--resume` updates the explicitly supplied checkpoint. Resume validates the StoryBook content fingerprint, skips completed work, and replays unfinished work. Preserve compatibility with version 1 and version 2 checkpoints, including explicitly supplied files from older locations. Legacy profiles lacking the full-body prompt load with an explicit not-generated marker.

Resume does not reread completed nodes to backfill appearance or apply revised prompts across the book. Regenerating all profiles requires a fresh run without `--resume`. The agent API checkpoints only when `checkpoint_path` is supplied and optionally saves `character_profiles.json` under its configured workspace. The CLI atomically publishes `character-profiles.json` in the ebook run directory; failed runs preserve the previous final file. Keep this filename distinction explicit and do not create staging subdirectories there.

The frontend offers dedicated Read and Characters views. Characters loads the run's profiles lazily through `/api/runs/<run-id>/characters`, validates the schema and book ownership, and searches names and aliases. Show descriptions only after selection because profiles may contain whole-book spoilers. Render profile strings as text; appearance text is not a portrait image. Preserve the section, scroll position, and reading settings when switching views, including typeface changes while the reading pane is hidden. Standalone StoryBook imports cannot automatically load sibling profile files.

Follow `frontend/PRODUCT.md` and `frontend/DESIGN.md` for product and visual conventions. Preserve the paper/night palettes, bundled fonts, responsive cast list/profile navigation, keyboard focus, and missing/loading/error/empty states.

Use pytest/pytest-asyncio with `test_*.py`, Vitest with `*.test.ts`, and Playwright with `*.spec.ts`. Prefer fake providers and synthetic books/profiles for automated tests. Cover validation, content order, identity updates, grouped processing, checkpoint/resume, cancellation, hierarchy, file confinement, character search, and reading-position restoration with focused regressions. No coverage percentage is configured.

## Commit & Pull Request Guidelines

Existing commits use short action prefixes such as `add:`, `update:`, and `refactor:`. Describe the affected feature. PRs should explain the problem, resulting behavior, and validation performed; link relevant issues and include screenshots for interface changes.

## Security & Configuration

Keep credentials, private ebooks, generated profiles, and checkpoints out of commits. `EBOOK_RUNS_DIR` overrides the reader's run directory; `PLAYWRIGHT_CHROMIUM_EXECUTABLE` selects a test browser. Preserve HTML sanitization, file-size limits, and filesystem confinement, including symlink checks, when extending the reader. The frontend reads local artifacts without model calls or writes to ebook runs.
