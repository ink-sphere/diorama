# Repository Guidelines

## Project Structure & Module Organization

Diorama is an AI-assisted ebook reading application. The app is suppossed to be primarily a web app that uses an agent called "Geppetto" to interact with books and the users to build immersion. 

- Implement Geppetto's harness in [`diorama/agent/`](diorama/agent/) using the [Codex Python SDK](https://learn.chatgpt.com/docs/codex-sdk). Define all custom agent tools in [`diorama/agent/tools.py`](diorama/agent/tools.py).
- Keep Markdown outside the `diorama/` Python package. Store Geppetto's runtime instructions and skills in top-level `resources/geppetto/` and project documentation in `docs/`.
- Geppetto’s only skill source is `resources/geppetto/skills/`. Install frontend design skills in the developer’s global Codex environment. Verify the worker’s full enabled skill catalog when changing its launch configuration.
- Implement the backend in [`diorama/backend/`](diorama/backend/) using [FastAPI](https://fastapi.tiangolo.com/).
- Keep the dependency-free browser client in [`frontend/`](frontend/). The FastAPI factory `diorama.backend.app:create_app` serves the client and its same-origin API. Use one server process for each local library.
- `books/`: ignored local ebook assets. Keep personal books out of commits.
- `pyproject.toml`: dependencies, dependency groups, Hatchling packaging, Ruff rules, and pytest configuration.
- `tests/`: synthetic EPUBs, deterministic ingestion tests, and opt-in Docker and model tests.
- `docs/philosophy.md`: product philosophy and intended reading experience. Read it before changing product behavior.
- `docs/python-api.md`: public package guide. Keep its examples and error descriptions in sync with the ingestion and reading APIs.

## Python Package Design

Design agents, tools, and harnesses in `diorama/` around composability and inheritance.

- Compose each agent from its instructions, skills, and tools. Give tools explicit inputs and outputs so they can be called independently. Pass the agent and its dependencies into the harness, which owns the execution lifecycle.
- Use inheritance when concrete agents, tools, or harnesses share behavior and one needs to specialize part of it. Extract the shared behavior into a small base class, keep the hierarchy shallow, and preserve the base class's public contract in each subclass.
- Use the `zensical-authoring` skill when writing or editing Python docstrings and documentation for `diorama/`.
- Avoid defining or relying on private or protected modules, classes, functions, methods, or variables. Use public interfaces in both Diorama and its dependencies.

## Product Direction

Diorama aims to deepen story immersion through an AI-assisted reading experience. The Library queues uploaded EPUBs for `diorama.backend.ingestion.BookIngestion`. The Reader serves passages from validated editions. Settings connects ChatGPT to Geppetto’s dedicated Docker profile. Exercise the full upload-to-reader flow with `uv run pytest tests/test_web_ingestion.py`.

Use “Start Reading” for a prepared edition’s first-read action. Keep the Library’s seascape and upload action above the shelf at every book count. Show reading availability and book actions on the shelf. Present live milestones, execution history and the preparation preview only inside the explicitly opened Agent trace drawer. Keep SDK output in lazy-loaded event details and scope trace listeners to each ingestion. Keep the followed edition independent of the active Reader bookmark. Open the Reader after verified passages have been published.

The [ingestion experience implementation plan](docs/ingestion-experience-plan.md) specifies the worker event contract, Library migration, and migration acceptance checks. The Library presentation follows the product direction above.

Keep **Remove from library** visible on each shelf entry. Disable it during active preparation and confirm the targeted edition by title. Library removal applies to one EPUB digest and its files inside the managed library. Verify that a restart cannot reimport a removed edition and that an explicit reupload can prepare it again.

Design Geppetto as a coding agent with custom instructions. Its first and only planned skill for layer 1 is automatic EPUB ingestion. The skill infers each book's hierarchy and preserves its original text.

Keep Geppetto's standing instructions about its work with books. Configure coding capabilities in the harness and verify them through execution. Follow the [Geppetto harness execution sequence](docs/geppetto-harness-plan.md#execution-sequence-and-acceptance-checks) for implementation order and per-change tests.

## Build, Test, and Development Commands

Run commands from the repository root using `uv`:

- `uv sync --group dev`: install the package and development dependencies.
- `uv run ruff check .`: check import ordering and unused imports.
- `uv run ruff format .`: apply the configured Python formatting.
- `uv run pytest`: run the offline suite. Use `--geppetto-live` for the Docker and ChatGPT integration tests.
- `uv build`: build source and wheel distributions using Hatchling.

See [README.md](README.md) for the backend adapter, Linux worker build, and ChatGPT device sign-in. The worker uses a dedicated Docker profile volume. Keep credentials and personal books out of the build context and tests.

## Coding Style & Naming Conventions

Use four-space indentation, double-quoted strings, and Python 3.12-compatible syntax. Use `snake_case` for modules, functions, and variables; `PascalCase` for classes; and `UPPER_SNAKE_CASE` for constants. Add type annotations to public interfaces. Prefer Ruff for consistent formatting and import organization; Black and isort are also listed as development dependencies.

## Testing Guidelines

Use pytest with files named `tests/test_<module>.py` and test functions named `test_<behavior>`. The configured `pytest-asyncio` mode is `auto`, with function-scoped event loops. Cover new behavior and regressions using small fixtures; mock external services where practical. No coverage threshold is currently configured.

## Commit & Pull Request Guidelines

History commonly uses short, imperative subjects with prefixes such as `add:`, `update:`, and `refactor:`. Follow that pattern and keep commits focused. Pull requests should explain the change, list validation commands and outcomes, and link relevant issues. Include screenshots for visual changes and call out configuration or dependency changes.

## Security & Configuration

Keep credentials in ignored `.env` files. Never commit API keys, local environments, generated distributions, or private ebook files. Document required environment variable names without including secret values.

## Behavioral Guidelines

Behavioral guidelines to reduce common LLM coding mistakes.

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

### 5. Update AGENTS.md

After every session, update `AGENTS.md` accordingly.
