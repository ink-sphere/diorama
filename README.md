# diorama
From ebooks to world models

## Agent traces

All `BaseDioramaAgent` subclasses accept ordered synchronous or asynchronous callbacks:

```python
from diorama.agents import RichTraceCallback
from diorama.agents.ebook_structure_agent import EbookStructureAgent

agent = EbookStructureAgent(
    provider=provider,
    model=selection.model,
    callbacks=[RichTraceCallback()],
    workspace_root=".ebook-runs",
)
storybook = await agent.run("books/example.epub")
```

`workspace_root` is an optional output destination, resolved against the caller's
working directory when the agent is constructed. Each successful extraction saves
only `<workspace_root>/<run-id>/storybook.json`. Without it, the agent returns the
object without saving a persistent artifact. Input paths are resolved when `run()`
is called. Neither the caller's directory nor a repository checkout is needed for
extraction; the installed package and its dependencies are sufficient.

All agents share `working_directory()` for temporary scratch space and asynchronous
`save_output(filename, content)` for publishing a completed artifact. Temporary
input copies, scripts, and session state are cleaned up after each run. The ebook
agent receives its schema through the system prompt and uses standard coding tools;
no application modules or CLI utilities are copied into its working directory.
Independent validation runs before publication. Previous runs are not modified.

Through ordinary Python scripts and `bash`, the agent can use the installed
`parse_ebook()` inventory and `load_storybook()` preflight validator. This restores
recursive source parsing and validation during agent turns without staging a CLI
or importing from checkout-specific paths. EPUB wrapper elements are traversed;
the agent still chooses the hierarchy from the source evidence.

Extraction includes an evidence-backed outline of source sections before assembling
the tree. Host validation rejects flattened or misplaced nodes for clear structural
headings and corroborated EPUB TOC relationships, including Act → Scene nesting.
The agent receives the diagnostic and can repair the artifact before it is saved.
Hierarchy checks also cover supported HTML and JSON book export content; decorative
and quoted headings do not automatically become structural boundaries.

`StoryBook.model_post_init()` recursively removes reading nodes containing only
headings, empty layout or whitespace, prunes empty groups, and recomputes group
narrative flags. Source headings and anchors move into adjacent substantive content
in reading order. Media and original fragments in readable nodes are preserved.
Normalization runs on model construction and JSON loading; existing saved JSON is
cleaned when it is loaded through `StoryBook` and saved again.

The Rich callback writes to stderr and streams assistant text, provider-exposed
thinking, tool arguments and results, token usage, retries, compaction, validation
failures, and run status. Completed output remains in terminal scrollback; text is
not truncated by the renderer. Rich respects `NO_COLOR` and works with redirected
output. Images are identified by MIME type rather than printing binary payloads.
Tool output may already be bounded by the tool itself.

The extraction script enables traces by default:

```bash
uv run python scripts/process_ebook.py books/example.epub
uv run python scripts/process_ebook.py books/example.epub --no-trace
```

Profile characters from the resulting StoryBook with the same Codex login,
optional `--model` override, and a Rich progress bar. The bar counts completed
narrative leaf nodes in reading order, shows the current section, and reports
final reconciliation and saving. Detailed traces are disabled by default; enable
them with `--trace`:

```bash
uv run python scripts/profile_characters.py .ebook-runs/<run-id>
uv run python scripts/profile_characters.py .ebook-runs/<run-id> --trace
uv run python scripts/profile_characters.py .ebook-runs/<run-id> --max-nodes-group 3
```

Generate headshot images from the saved headshot visual prompts using FLUX 3
and your `FAL_KEY` environment variable. The script also loads the project-root
`.env` file, preserving variables already set in your environment:

```bash
uv run python scripts/generate_character_profile_images.py .ebook-runs/<run-id>
```

Images use 1k resolution, a 9:16 aspect ratio, and disabled prompt expansion.
The script shows a Rich progress bar counting saved images and writes
`character-profile-images/<character-id>-headshot.jpg` inside the run directory.
IDs are URL-encoded for safe filenames. Rerunning replaces matching images.
All prompts are submitted to fal's queue before results are collected. Images
are saved as they finish; a failed request is reported without discarding the
other results, and the script exits unsuccessfully if any request fails.

The profiling script reads `storybook.json` from the supplied run directory and saves profiles at
`.ebook-runs/<run-id>/character-profiles.json`. A successful rerun replaces that file.
Each run also prints a checkpoint path at
`.ebook-runs/<run-id>/character-profiles-checkpoint-<checkpoint-id>.json`.
Fresh runs use unique checkpoint filenames so previous progress is preserved.
No separate profiling directory is created.
Checkpoints are saved atomically after each completed narrative leaf or group and after
final reconciliation. Resume a failed or interrupted run with the same StoryBook:

```bash
uv run python scripts/profile_characters.py .ebook-runs/<run-id> --resume .ebook-runs/<run-id>/character-profiles-checkpoint-<checkpoint-id>.json
```

Resume restores the registry and progress count, skips completed leaves, and
restarts any unfinished leaf or group from its beginning. Changed book content is rejected.
Existing checkpoints remain usable, and the group limit can change when resuming.
The checkpoint contains story-derived profiles; keep it private like the other
generated runs. For API use, pass `checkpoint_path=...` to `agent.run()` and add
`resume=True` when continuing. Without a checkpoint path, API runs do not checkpoint.

Both scripts accept `--thinking-effort` to override the model's reasoning effort:

```bash
uv run python scripts/process_ebook.py books/example.epub --model gpt-5.6-sol --thinking-effort high
uv run python scripts/profile_characters.py .ebook-runs/<run-id> --model gpt-5.6-sol --thinking-effort high
```

Omitting the option leaves effort unspecified, using the model's default. Tau's
levels are `off`, `minimal`, `low`, `medium`, `high`, `xhigh`, and `max`; the selected
model's configured supported levels are checked before login. For the Codex
provider, `off` leaves effort unspecified rather than explicitly disabling reasoning.

The profiler processes each narrative leaf as a whole, carrying the accumulated
character registry between requests. `--max-nodes-group` sets the maximum number
of consecutive narrative leaves per extraction request (default `1`, minimum `1`).
For API use, pass `max_nodes_group=...` to `CharacterProfilerAgent(...)`. Groups
preserve each node's section path and text, and progress continues to count nodes.
The size budget may reduce a group below the requested maximum. If a leaf exceeds the configured input
budget after accounting for the system prompt, registry, JSON encoding, and repair
headroom, it splits the leaf at paragraph boundaries where possible. The default
input budget is 240,000 characters; this is a character guard, not automatic model
token-window detection. An optional `max_chunk_characters` constructor argument
can impose a smaller excerpt limit. The final pass reconciles duplicate identities.
Model requests use short references such as `c1` and `c2` from the current registry;
Python maps these to persistent character IDs. Validation feedback identifies the
offending update and valid references, and rejected responses apply no changes.

Custom callbacks receive an `AgentTraceEvent` with `run_id`, `agent_name`, `model`,
`sequence`, UTC `timestamp`, and the original Tau event or a `RunEvent`:

```python
async def capture(trace):
    await event_store.append(trace)
```

Run start, success (including the returned object), error, and cancellation events
are automatic, preserving each subclass's typed `run()` signature. Inside a new
agent, forward runtime events with `await self.emit_event(event)` and emit custom
progress with `await self.emit_event(RunEvent("progress", {"stage": "parsing"}))`.
Callbacks are awaited in registration order; a callback exception propagates after
the other callbacks receive that event. Reporting an error never replaces the
original run exception. Concurrent runs have separate IDs and event sequences.
`EbookStructureAgent.run(on_event=...)` continues to receive raw Tau events.
