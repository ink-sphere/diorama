# Implement the ingestion experience and Agent trace

**Deliverable.** From one EPUB upload, the reader can watch the edition take shape, explore a source-backed opening excerpt, browse another book while Geppetto works, and inspect the actual execution history. **Agent trace** is the only reader-facing history. **Start Reading** becomes available when the Library has committed the verified reading sections.

## 1. Establish the behavior and the boundary

| Moment | What the reader sees | What makes it happen |
| --- | --- | --- |
| File selected | The filename and upload activity appear immediately in the Library. | A user-initiated upload is in flight. |
| Edition accepted | The book takes its place on the shelf. Its cover, title and available opening material appear together. | The host inspected the EPUB, retained it and registered the digest. |
| Structure discovered | A new heading appears in place. Selecting a supported heading shows a short passage from that edition. | The host accepted a worker outline and resolved the heading against the source index. |
| Reader browses elsewhere | The reader keeps their place. A compact bar names the followed book, gives its current activity and opens **Agent trace**. | The app tracks the followed ingestion independently of the selected shelf book and Reader route. |
| Work pauses or fails | The current activity explains what happened and offers the existing sign-in or retry action. The trace stays available. | The queue recorded the corresponding outcome. |
| Publication succeeds | **Start Reading** opens the first section. **Continue reading** returns to an existing bookmark. | The transaction writing the reading pages and marking the edition ready committed. |

**Proposed interaction default.** After an accepted upload, show the opening preview inline if the reader is still on the Library and has kept that upload selected. Offer **Back to shelf** immediately. If the reader has selected something else or navigated away, leave them there. Opening the full Reader always takes an explicit action. The mockup’s mode selector, pause and replay controls were tools for comparing designs.

The production starting points are the [browser client](../frontend/app.js), [Library](../diorama/backend/library.py), [harness](../diorama/agent/harness.py) and [worker](../diorama/agent/worker.py). Today the worker awaits a completed turn, the harness observes a bounded outline file, and the browser polls the shelf roughly every 1.6 seconds. The preparation endpoint returns up to 32 recent notes. It cannot reconstruct commands that were never captured.

## 2. Capture real execution events at the worker

**Files.** Extend [worker.py](../diorama/agent/worker.py), introduce a small public event type and projector in `diorama/agent/trace.py`, and extend [test_worker.py](../tests/test_worker.py).

1. Add an optional tracing capability to the concrete SDK worker, for example `run_traced(task, emit)` with an asynchronous per-run listener. Keep the public `run(task)` entry point and the existing three-argument worker factory usable. The installed SDK is `openai-codex==0.171.0`. Its public `AsyncTurnHandle.stream()` yields notifications for the turn. Consume the handle exactly once. Share turn creation, interruption and cleanup between the traced and ordinary paths.
2. Stamp each retained notification with host receipt time. Carry the actual thread, turn and item IDs where available. Preserve SDK timestamps, duration, status and exit codes as distinct fields. A missing field stays absent. Keep the ordered start and completion observations so an open drawer can show a command in progress.
3. Project a small, explicit set of records before persistence.

   | Source | Persist and display |
   | --- | --- |
   | SDK `item/started`, `item/completed` for `commandExecution` | Method, item type/ID, command, working directory, status, and reported start/end times, duration, exit code and combined output when present. |
   | Same methods for `fileChange` | Reported file paths, change kinds, item status and IDs. |
   | SDK `turn/completed` | Turn ID and actual terminal status. Preserve existing usage accounting in the worker diagnostics. |
   | Library / harness | Named milestones such as indexed, discovered, checking, repairing, cached, paused and published. Label their origin **Library** in the drawer. Keep verified outline data with the corresponding discovery. |

4. The projector processes approved fields individually. Preserve displayed command and output text verbatim, including whitespace. Omit setup instructions, authentication material, sensitive command arguments or output, and internal model reasoning. Mark withheld fields explicitly. For example, the recorded worker setup command read its skill file. Keep its status and timing and mark that output as omitted. Use a proposed 16 KiB UTF-8 display cap per command/output field and indicate truncation and original size. Keep transcript content in local edition storage and render it as text. A shell session with only a start event remains an observed start. Show Python helper calls individually only if the runtime actually reports them as separate events.
5. Preserve the execution contract. On cancellation, interrupt the active handle, close its stream and propagate cancellation. Treat a missing terminal notification or unsuccessful turn as worker failure. A single command exit code describes that command. In the two-chapter design recording a command exited with `127`, the turn completed successfully, and the host subsequently verified the edition.

**Acceptance.** Feed a scripted public SDK event stream through the production projector. Assert field fidelity, marked omissions, missing completion, nonzero command exit followed by a successful turn, and exactly one interrupt on cancellation. A partial trace must never authorize publication.

## 3. Wire tracing and source previews through the harness

**Files.** [harness.py](../diorama/agent/harness.py), [preparation.py](../diorama/agent/preparation.py), [ingestion.py](../diorama/backend/ingestion.py), [test_harness.py](../tests/test_harness.py) and [test_backend_ingestion.py](../tests/test_backend_ingestion.py).

- Add a keyword-only `on_trace=None` to `BookIngestion.add_book` and `GeppettoHarness.ingest_epub`. Keep `on_progress`, its existing fields, and `IngestionResult` compatible. Bind the observer to the current invocation. Custom workers that implement only `run(task)` continue to work and produce host milestones. Opt-in workers supply `run_traced` through a small public protocol.
- In the harness, use the same turn invocation path for initial ingestion and repair. Enrich events with the attempt number and turn purpose. A repair gets another turn on the same thread. A lost worker gets a new attempt and thread under the existing overall deadline. Record cache reuse as a Library event. Keep the original validation and atomic manifest publication order. Trace display failures must not be classified as model failures or trigger another model turn.
- Use the publisher’s navigation and indexed linear XHTML for the opening preview. At registration, select the first usable source heading or the start of the first nonempty linear document. A front-matter opening can be labeled **Opening of this edition**. During discovery, use the existing ten-entry `preview.json` and resolve each supported title to an unambiguous source heading. Give a preview a stable key derived from its spine occurrence and offset.
- Build the snippet on the host using the existing `SourceIndex` and `read_passage`. Bound it to the original document, the next heading and a proposed 320 Unicode code points. Store the exact source range alongside the text. For duplicate headings or an unresolved navigation target, display the outline label alone. Extend `PreparationUpdate` with an optional preview field with a default. Handle genuine corrections of equal-length snapshots as well as additions, and retain the last valid preview across malformed or partial writes.
- This design uses Geppetto’s existing outline skill and helpers. All trace capture happens in the host worker adapter. The recorded two-chapter experiment already exercised public SDK streaming with the existing image. There is no dependency on a new skill, a broader container mount or a different approval mode.

**Acceptance.** Exercise a play with nested scenes, repeated chapter names, an EPUB with front matter, a heading-free document and a cache hit. Compare every displayed snippet to its indexed source range. Exercise a same-thread repair, worker loss and concurrent ingestions. Each invocation must receive only its own events. The full contents and page endpoints must still return `409` until publication.

## 4. Persist one ordered history and expose it to the browser

**Files.** [library.py](../diorama/backend/library.py), [app.py](../diorama/backend/app.py) and [test_web_ingestion.py](../tests/test_web_ingestion.py).

1. Migrate the existing `preparation` table transactionally to an `ingestion_events` table. Use `seq INTEGER PRIMARY KEY AUTOINCREMENT`, `book` (the EPUB digest), `origin` (`library` or `sdk`), `name`, nullable `observed_at_ms`, nullable run/attempt/thread/turn/item identifiers, and a bounded JSON payload. Index `(book, seq)` for cursor reads and `(book, origin, seq)` for current activity. Keep sequence numbers from the old rows. Map their phases, notes and outlines to historical Library records with unknown timestamps. Run the migration before interrupted-job recovery or existing-manifest import. A second startup must leave the history intact.
2. Use one writer for event insertion with an optional existing database connection. Write queue claims, sign-in pauses, requeues, failures and publication milestones in the same transaction as their book-state changes. Cover the bulk SQL in startup recovery and `resume_waiting`. Use the queue-claim event’s sequence as the run identifier for work on that claim. Thread a per-edition asynchronous sink into `add_book` for SDK events. Move blocking SQLite work off the SDK event loop, preserve delivery order, and flush outstanding writes before finishing the turn.
3. Drive the headline, outline and preview from the latest applicable Library records. SDK activity advances the trace cursor independently. Keep detailed output out of shelf responses. If a bounded trace payload cannot be saved, retain ingestion semantics, expose a capture-gap indicator when storage is available, and log only diagnostic metadata. A failure to commit actual book state follows the existing job error path.
4. Implement these same-origin read contracts. Names and defaults below are proposed implementation contracts.

   | Request | Response and behavior |
   | --- | --- |
   | `GET /api/books` | Keep the current shelf fields. `preparation_revision` tracks the latest Library milestone. |
   | `GET /api/books/{digest}/activity` | A consistent snapshot containing status, phase, current activity, keyed outline/preview entries, latest Library revision and latest trace sequence. Build it in one read snapshot. |
   | `GET /api/books/{digest}/trace?after=0&limit=50` | Return ordered event summaries, `next_after` and `has_more`. The cursor is exclusive and scoped to the requested book. Validate a nonnegative cursor and limit of 1–100. Empty pages keep the caller’s cursor. |
   | `GET /api/books/{digest}/trace/{seq}` | Return the stored, display-safe fields for a single event when expanded. Scope the lookup by both digest and sequence. Return `404` for an unknown book, removed book or mismatched event. |
   | Existing `/preparation`, `/contents` and `/pages/{number}` | Keep `/preparation` as a compatibility projection for existing clients. The new UI uses `/activity`. Keep the publication gate on full reading content. |

5. A duplicate upload resolves to the same edition and history. A retry adds a run to that history. Restart preserves recorded timestamps and appends a real recovery milestone. A legacy book can show known Library facts with “Execution trace was not recorded for this ingestion.” Remove an edition’s events and excerpts in the existing recoverable deletion flow. Reupload starts a fresh history. Update queries that currently read or delete directly from `preparation`.

For example, a trace page following cursor 216 could return the summary below. The sequence numbers are illustrative. Expanding event 217 fetches its permitted SDK fields, including the original command and combined output. Display elapsed time relative to the edition’s recorded upload receipt when both timestamps are known.

```json
{
  "events": [
    {
      "seq": 217,
      "origin": "sdk",
      "name": "item/completed",
      "item_type": "commandExecution",
      "status": "failed",
      "exit_code": 127
    }
  ],
  "next_after": 217,
  "has_more": false
}
```

Return the edition’s registration sequence in the activity snapshot so a client can discard stale cursors if the same EPUB was deleted and reuploaded. Read `limit + 1` records to compute `has_more` and advance to the last record actually returned. For an expanded event, keep the SDK field names (for example `exitCode`), with omission and truncation markers outside the captured fields.

**Acceptance.** Upgrade a database created with the current schema and compare existing pages, outlines and legacy API responses. Test pagination across interleaved books, empty pages, migration re-entry, failure/retry, deletion during an open drawer, restart after partial deletion and explicit reupload. Compare the published state and the ready event in the same database snapshot.

## 5. Replace the notebook in the shipping client

**Files.** [index.html](../frontend/index.html), [app.js](../frontend/app.js) and [style.css](../frontend/style.css). Keep the client dependency-free.

1. Remove the Preparation Notes disclosure, history list, reload button, rendering branch, notebook styles and notes-specific removal copy. Retain one inline activity sentence. Give its error state a retry action that reloads the relevant resource.
2. Keep separate state for `selectedBookId`, `followedBookId` and the Reader’s route/bookmark. A new accepted upload becomes the followed book. Selection or browsing another book must never silently switch an open trace. Place the compact, named activity bar outside the Library/Reader sections. Restore the followed edition on reload after checking it still exists. Clear its state when removed. Store trace contents in SQLite.
3. On a new upload, establish the shelf slot first. Once the cover and source preview are available, measure the jacket’s old and new rectangles and animate the translation and scale with the Web Animations API (about 350 ms). Keep existing headings mounted and append or update by stable key. Reveal a new heading once with a 3–4 px rise and a short fade (about 180 ms). Cancel an active transition on navigation or resize. Respect `prefers-reduced-motion` and skip motion when a cached result is already ready. Keep the last observation visible through quiet intervals.
4. Use cursor-based HTTP polling for this release. Reuse the shelf refresh for list membership. Poll the followed book’s activity independently while ingestion is active (proposed 750 ms while visible). Only an open trace drawer fetches event pages and only an expanded row fetches its fields. Drain `has_more` immediately. On a terminal state, catch up to the snapshot’s final cursor before stopping. Pause polling in hidden tabs, catch up on visibility or reconnect, and back off on failure. Keep the observer independent of the existing Settings timer. Abort or discard stale responses when the edition or view changes.
5. Use an accessible native dialog for **Agent trace**. Show a side sheet on desktop and a bottom sheet on mobile. List events by sequence with source, time when known, and concise action. Expansion reveals the recorded fields and any omission marker. Append rows without replacing existing disclosures, moving keyboard focus, announcing raw output or pulling someone away from an earlier entry. Provide **Jump to latest**, Escape/Close and focus restoration. Render all book text and command output through text nodes.
6. Keep **Start Reading** tied to the published edition and **Continue reading** tied to the existing bookmark. Previewing a chapter must not write reading progress. The trace remains accessible in the Reader and on returning to the shelf.

**Acceptance.** In a browser run, upload A, inspect an event, scroll back, then read B while A finishes. Check that A’s trace still identifies A, B’s passage stays in place, and the expanded row and scroll position survive incoming events. Refresh, reconnect, open A, resume B, and remove/reupload A. Repeat at mobile and desktop widths, by keyboard, and with reduced motion.

## 6. Land and verify in dependency order

| Change | What to run before proceeding |
| --- | --- |
| Worker event projection and harness propagation | `uv run pytest tests/test_worker.py tests/test_harness.py tests/test_backend_ingestion.py`. Add the faithful stream and failure cases from steps 2–3. |
| Preview extraction, migration and API | `uv run pytest tests/test_tools.py tests/test_web_ingestion.py`. Add real legacy-database, cursor isolation, preview-fidelity and deletion fixtures. |
| Browser presentation and handoff | `node --check frontend/app.js` plus the browser flow above against a temporary library with deterministic workers. Capture the opening preview and expanded trace at both viewport sizes. |
| Integrated release | `uv run ruff check .`, `uv run ruff format --check .`, `uv run pytest`, and `uv build`. Serve the built wheel from outside the checkout and confirm it contains the shipping frontend and resource skill. |
| Authenticated acceptance | `uv run pytest --geppetto-live tests/test_worker.py tests/test_ingestion_live.py tests/test_web_ingestion.py`. In an isolated library, ingest a synthetic two-chapter EPUB with the real worker, compare sampled displayed fields to captured SDK notifications, then verify original text via the Reader. Use a scripted stream to guarantee the exit-127 recovery case offline. |

Update the [README](../README.md) for the upload, preview, trace, removal and Reader interactions. Update the [Python API guide](python-api.md) for the optional trace listener and preview contract. Retain the lifecycle and packaging checks in the [existing harness plan](geppetto-harness-plan.md#execution-sequence-and-acceptance-checks).
