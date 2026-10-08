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
