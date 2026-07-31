# LiteraryResearchAgent — Outcomes & Integration Design

*Part 1 records the 2026-07-30 brainstorm between Soumik and Fable that settled what the
agent produces. Part 2 records the 2026-07-31 session that finalized how it integrates
into the app — trigger, surfaces, routes, storage, and failure posture. Part 1 settles
the artifacts; Part 2 settles the UX. Tool and prompt design live in the agent module
itself (`diorama/agents/literary_research_agent.py`), which implements Part 1.*

---

# Part 1 — What the agent produces (2026-07-30)

## Decisions taken

1. **The output is formally split into two artifacts plus a profile** — a style-free
   **world dossier** and a swappable **style bible** — with "moodboard" reserved for the
   reader-facing surface that renders both. This is the V0 "style-free world state"
   decision carried into the research layer: re-styling a book regenerates only the
   style bible.
2. **The book's existing visual tradition (original illustrators, famous editions,
   adaptations) is researched and recorded as facts in the dossier**, but whether the
   style bible follows that tradition or proposes an original direction is **the user's
   choice**, not the agent's.
3. **Style/mood is one global choice per book.** No per-arc modifiers; mood variation
   within the book comes from the scene text at render time.
4. **The author profile is prose plus a structured bridge**: the bio stays pure prose,
   while the work-specific section carries machine-readable fields that seed the dossier
   and style bible.

## The two author sections: their distinct natures

**"A generic description of the author and their overall body of works"** is
*book-independent*: the dust-jacket flap. Who the author was, their era, recurring
obsessions, how their style is usually characterized. Properties that follow:

- **Cacheable across books.** Three Dickens novels share one bio; the author is a cache
  key (eventually perhaps a first-class entity), and web research on the author should
  not re-run per upload.
- **Purely reader-facing.** Nothing downstream consumes it. Register: ~100–200 words of
  colophon prose, rendered as an "About the Author" page at the back of the reader —
  a real book's back matter, not a metadata panel.

**"A description stating the author and their work on the specific story"** is
*book-specific* and is **not garnish** — it is the bridge into the moodboard:

- **Publication/composition dates** give the *authorship period* as distinct from the
  *story period* (a novel written in 1861 set in 1810 has two time systems; the visual
  idiom readers associate with a book usually comes from the authorship side).
- **Circumstances of composition** (serialized? written for children? satire of
  something contemporary?) are tone facts.
- **The existing visual tradition** — original illustrator (Tenniel, Phiz), notable
  editions, adaptations — recorded here as facts, consumed by the style-direction choice.
- Place in the oeuvre and reception, as reader-facing color.

Both sections are written **spoiler-free** (critical sources love endings; the reader
sees this before/while reading).

**Robustness rule:** every field must be derivable from the text alone, with web research
as *enrichment*. For an obscure or recent book the web is nearly empty; the schema must
not have required fields only Wikipedia can fill. Unknown stays null — same "unmeasured
and free are different claims" discipline as the cost dashboard.

## The three artifacts

### 1. Author profile

- `name`, `birth_date`, `death_date` (nullable)
- `bio_prose` — oeuvre-level, cacheable per author
- `work_context_prose` — this book's story-behind-the-story
- Structured bridge: `publication_year`, `authorship_period`, `composition_context`,
  `visual_tradition: [{name, kind (illustrator/edition/adaptation), medium, description,
  sources}]`

### 2. World dossier (style-free — never touched by a re-style)

Everything described in **world terms** ("threadbare grey wool coat"), never rendering
terms ("soft watercolor wash").

- **Time periods**: label, rough span, story-time vs authorship-time, and *visual
  markers* — clothing silhouettes, technology, transport, **light sources** (candle vs
  gas vs electric changes every plate), architecture.
- **Location registry**: name, real/fictional, description, which periods it exists in,
  visual notes. Web research earns its keep on real places.
- **Milieu wardrobe**: per social group, not per character. The line between the three
  wardrobe owners: *milieu* dress lives here ("what a Victorian governess wears"),
  *character* dress belongs to the CastingDirectorAgent ("what Jane Eyre wears"),
  *scene* dress belongs to the MakeupArtistAgent ("what she wears in this scene").
- Every entry carries `evidence`: block ids from the text, URLs from the web.

### 3. Style bible (swappable; one global style per book)

- `direction`: `"traditional"` or `"original"` — which candidate the user chose (below)
- Mood vocabulary, palette, lighting language, named art direction + rationale
- **`style_prompt_block`** — the actual influence mechanism: a canonical paragraph of
  rendering language appended *verbatim* to every image call. Verbatim-stable is the
  cheapest consistency lever across 500 plates, and "swap the style" means regenerating
  exactly this artifact.
- `negative_constraints` — anachronisms to avoid; famous adaptation looks to avoid when
  `direction = "original"`.

## The style-direction choice (new mechanism, from decision 2)

This is the pipeline's first user-interactive decision. Resolution (confirmed in Part 2):

- The research phase always produces **both candidate style bibles** — tradition-informed
  and original — since each is one cheap text-only generation on top of shared research.
  (A book with no recorded visual tradition produces only the original candidate.)
- The **moodboard presents them side by side with a picker**; the chosen one becomes
  the active style bible.
- A provisional default keeps the pipeline non-blocking: **original** — Diorama's own
  voice, and the one candidate that always exists (`DEFAULT_STYLE_DIRECTION` in the
  agent module). Until image generation lands, flipping the choice is free; once
  rendering exists, the picker becomes the natural gate before the expensive phase.

---

# Part 2 — Integration & UX (finalized 2026-07-31)

## Decisions taken

1. **Research is lazy, not an upload phase.** Nothing runs at upload time; the pass
   starts the first time the reader opens a book's moodboard. Upload processing stays
   exactly two phases, and a book nobody researches costs nothing extra.
2. **The moodboard is a floating modal that lives exclusively in the reader**
   (`/read/[id]`). There is still no book-detail page, and the shelf is untouched —
   the shelf card keeps linking straight to the reader.
3. **While research runs, the modal shows the agent's live trace** — the same
   `TraceLine` treatment as upload phase one, not a bare progress bar. This is one
   agent run doing legible, interesting work (searching the web, studying Tenniel
   plates, submitting artifacts one by one); watching it *is* the loading state.
4. **Closing the modal does not cancel the run.** The background task keeps going; a
   subtle indicator in the reader chrome shows research is in flight, and reopening
   the modal replays the trace log so far, then joins the live tail — the same
   subscriber semantics as the shelf's processing stream.
5. **Partial results are kept and rendered.** A run that dies after two good artifacts
   persists them (`LiteraryResearchError.partial`); the modal renders what exists and
   offers retry for the rest. All-or-nothing would throw away exactly the runs the
   staged-submission design was built to salvage.
6. **The reader gains an "About the Author" back-matter section now** — the author
   profile's prose rendered as a real book page after the last section, once research
   has produced it.
7. **The per-author bio cache (`.diorama_data/authors/`) is deferred.** Each book's
   research is self-contained in its own record; the cache only pays off once the same
   author appears twice on a shelf. Recorded as a follow-up below.
8. **The settings page grows a "Web search" card** for the Exa/Tavily keys
   `WebSearchTool` consumes, with the same masked-key mechanics as the LLM provider
   cards. Research without any search key still runs — text-only, gracefully.

## Lifecycle

### Trigger and run mechanics

The unit of orchestration mirrors upload processing: an in-memory run registry with
`ensure_started()` semantics, one run per book, subscribers joining via replay-then-tail.
`processing.py`'s `_BookRun` class is already agent-agnostic (a log, subscribers, a
close sentinel) — research reuses it in its own registry (`_research_runs`, separate
from upload's `_runs`, since a book can be re-processed and researched independently).

Flow, end to end:

1. The reader mounts and fetches the research record alongside structure and scenes
   (`GET /api/books/{id}/research`; **404 is the normal answer** for a never-researched
   book, exactly like `/scenes` for a pre-segmentation book).
2. The user opens the moodboard modal. If a complete record exists, it renders
   immediately — no network beyond the fetch already done.
3. Otherwise the modal opens an `EventSource` on `GET /api/books/{id}/research/stream`.
   The stream's `ensure_started()` starts a background research run **only if no
   complete record exists** — so the stream endpoint is idempotent, and a second tab
   or a reopened modal joins the same run rather than starting another.
4. The run drives `LiteraryResearchAgent.stream_research()`, translating each
   `AgentEvent` into a `TraceLine` (see *Trace treatment* below) and publishing to all
   subscribers. Same consumption pattern as the loader: iterate events fully, then
   `finalize()`.
5. On settle (success, partial, or failure) the record is written to disk, the book's
   run closes with a `done`/`error` line, and the modal flips from trace view to
   moodboard view in place.

Model and key resolve per run through the existing chain: `literary_research` joins
`AGENTS` in `diorama/backend/settings.py` (env override `DIORAMA_RESEARCH_MODEL_ID`,
per-provider defaults like the other two agents). The agent already defines
`AGENT_ID = "literary_research"` for exactly this registration. One caveat the settings
test endpoint should carry: this agent uses `ViewImageTool`, so its configured model
should be **vision-capable** — every provider default is, but a hand-typed model id
may not be, and that's a warn-at-test, not a hard block, same as the tool-calling
warning for the loader.

### Server restart mid-run

Same posture as upload processing: the in-memory run is lost, nothing partial is on
disk (partials are only captured when the run *fails*, not when the process dies), and
the next modal open simply starts a fresh run. Self-healing, not a bug; not worth
checkpointing artifacts mid-run for a personal single-process tool.

### Cancellation

There is no user-facing cancel in this iteration. Closing the modal detaches the
viewer, never the run (decision 4) — the spend so far should produce artifacts, not
evaporate because a modal was dismissed. The core's `CancellationToken` remains
available if a "stop research" affordance is ever wanted.

## Storage

`.diorama_data/research/{book_id}.json`, alongside `structures/` and `scenes/`. The
file is a small envelope around the agent's artifacts rather than a bare
`LiteraryResearchReport`, because partials and the user's style choice need a home:

```jsonc
{
  "status": "complete" | "partial",
  "error": null | "user-facing reason (partial runs only)",
  "chosen_direction": "original" | "traditional",   // default: "original"
  "author_profile": { ... } | null,
  "world_dossier": { ... } | null,
  "style_bibles": { ... } | null,                    // { original, traditional|null }
  "coverage_notes": [ ... ],                          // advisory warnings, verbatim
  "created_at": "...", "updated_at": "..."
}
```

- `status: "partial"` means at least one artifact is missing; each present artifact is
  fully validated (the agent's submit tools reject invalid ones), so a partial record
  never contains a half-checked artifact.
- `chosen_direction` lives here, not in the book record — it is research state, and
  deleting/re-running research legitimately resets it. It defaults to `"original"`
  (the always-present candidate) and is only meaningful once `style_bibles` exists.
- `coverage_notes` carries `coverage_warnings()` output (the front-loaded-dossier
  advisory) so the modal can surface it as a caption, not an error.
- Deleting a book deletes its research record, same sweep as structures/scenes/ledger.

## Backend API

New routes on the books router:

- **`GET /api/books/{id}/research`** — the persisted envelope. 404 when no record
  exists (never researched, or research never settled) — the normal answer, not a
  fault. Returns the record whether `complete` or `partial`; the frontend
  distinguishes by `status`. No 409: "in flight" is a stream-side fact, and a record
  only exists once a run settles.
- **`GET /api/books/{id}/research/stream`** — SSE, `text/event-stream`. Starts the
  run if none exists and no complete record is on disk; replays the trace log so far,
  then the live tail; closes after the `done`/`error` sentinel. A stream opened for a
  book with a complete record replays a single already-done line and closes — harmless,
  and it keeps the endpoint idempotent.
- **`POST /api/books/{id}/research/retry`** — drops the in-memory run (mirroring
  `processing.reset`) so the next stream open starts fresh. A retry is a **full fresh
  run** — the agent has no mid-run resume, and seeding a new run with old artifacts is
  a follow-up, not this iteration. On success the new record replaces the old envelope
  wholesale (a fresh dossier and fresh bibles are internally consistent with each
  other; mixing runs' artifacts isn't). The retry appends a new `run_id` to the ledger;
  the old run's spend stays on the books, exactly like upload retries.
- **`PATCH /api/books/{id}/research/style`** — body `{"direction": "original" |
  "traditional"}`. Persists `chosen_direction`. 409 if the record isn't complete;
  422 if `"traditional"` is chosen for a book whose record has no traditional
  candidate.

## Cost tracking

The research run mints its **own `run_id`** — the upload run is long settled by the
time research starts, and "one upload" and "one research pass" are different line
items. Every call lands in the same per-book ledger (`usage/{book_id}.jsonl`) tagged
`agent_id = "literary_research"`, so the cost dashboard picks it up with zero new
plumbing: the book's runs list grows a research run, and the per-agent breakdown grows
a row.

`book.cost_usd` (the shelf-card figure) keeps its current meaning — the upload
processing run — and is *not* rewritten by research. Instead the modal's footer shows
the research run's own cost read back via `run_cost()`, including for partial and
failed runs (the ledger's whole reason for being append-only). Unmeasured and free
remain different claims: a pre-ledger book's modal shows no figure rather than $0.

## Trace treatment

`trace.py` already translates `AgentEvent`s generically; what it needs is verbs for
the new tools in `_TOOL_DONE_VERBS`, keeping raw JSON hidden behind short human
phrasing (the file's standing discipline):

- `web_search` → pending "Searching the web…", done "Searched the web for ⟨query⟩"
  (the query is the one argument worth surfacing verbatim).
- `view_image` → "Studying an illustration…" / "Studied an illustration".
- `get_outline` → "Reviewing the book's structure".
- `submit_author_profile` / `submit_world_dossier` / `submit_style_bibles` →
  "Writing the author profile…" / "Wrote the author profile", etc. A rejected
  submission (validation errors) surfaces as the existing error-status line and the
  agent visibly retries — that loop is worth watching, not hiding.

The three submissions double as the run's coarse progress: the modal can render the
trace list with three milestone rows pinned (profile → dossier → style bibles) that
check off as each acceptance lands, giving the bar-like at-a-glance state *and* the
live trace without choosing between them.

## The moodboard modal

### Entry point

A new icon in the reader chrome (palette glyph, alongside the TOC and type-menu
affordances). Three visual states: plain (no record — opening it starts research),
in-flight (a subtle pulse/dot while a run is live, visible even with the modal
closed — decision 4's "still working" indicator), and plain-again once complete.
No badge for "complete"; the moodboard is a place you visit, not a notification.

The shelf shows nothing about research — it is reader-exclusive surface area.

### Modal shell

A floating modal over the spread: framer-motion scale/fade entrance, backdrop
scrim, closable via ✕, Esc, and backdrop click. Wide enough for the side-by-side
style candidates on desktop; single-column stack on the phone breakpoint. Book
typography inside (Newsreader for prose, Inter for chrome labels) — this is a page
*about* the book, and it should feel like part of it, not like the settings page.

### States

1. **First open, no record** — the modal opens directly into the trace view and the
   stream starts. No confirmation step: opening the moodboard *is* the request (the
   lazy-trigger decision). The header says what's happening ("Researching this book —
   its author, its world, and how it might look") over the live trace and the three
   milestone rows.
2. **In flight** — same view, trace scrolling, milestones checking off. Closing and
   reopening lands back here mid-run (replay + tail).
3. **Complete** — the moodboard proper (below).
4. **Partial** — the sections whose artifacts exist render normally; each missing
   section renders as an inline empty state carrying the run's user-facing error and
   one "Retry research" button (one button for the whole modal, not per section —
   retry is a whole-run action). Retry flips the modal back to state 1's trace view.
5. **Failed with nothing** — a run that died before any artifact: the error, the
   retry button, and nothing else pretending otherwise.

### Moodboard layout (complete state)

Zero generated images in v1 — the board is a designed rendering of dossier + style
bible, in this order:

- **About the Author** — `bio_prose` + `work_context_prose` as set prose; birth/death
  dates and publication year as a quiet colophon line. Spoiler-free by construction.
- **Timeline** — the dossier's time periods as a horizontal band: label, span, and a
  story-time vs authorship-time distinction made visually (two rails when they
  differ). Each period's visual markers (clothing, technology, transport, light
  sources, architecture) as compact tags.
- **Locations** — the location registry as cards: name, real/fictional badge,
  description, which periods it exists in.
- **Milieu wardrobe** — per social group, compact rows.
- **Style direction** — the two candidate cards side by side (or one, when no
  tradition exists): named art direction, rationale, mood vocabulary given
  typographic treatment, the palette as real color swatches (the artifact names hex
  colors — render them), lighting language, and the influences list on the
  traditional card. The active candidate is visibly selected; clicking the other
  fires the PATCH and re-marks. A caption notes the default ("Diorama's own
  direction, until you choose"). `style_prompt_block` and `negative_constraints`
  stay collapsed behind a disclosure — they are the machine's contract, interesting
  but not the lead.
- **Footer** — the research run's cost, and `coverage_notes` if any, phrased as the
  advisory it is ("Most of this dossier cites the book's opening — later chapters
  may hold more"), with the retry button doubling as "research again" for a user who
  wants a wider pass.

### Evidence

Every dossier entry carries evidence (block ids, URLs). The modal keeps them behind
a small disclosure per card rather than inline — block ids become "from Chapter N"
links that close the modal and jump the reader to that section (the block-id →
leaf-section mapping already exists in `structure.ts`), URLs open in a new tab.
This is the moodboard earning trust without reading like a bibliography.

## Reader back matter: About the Author

When the research record has an `author_profile`, `readBook()` appends one synthetic
**"About the Author"** section after the last leaf: `bio_prose` then
`work_context_prose`, Newsreader, drop-cap-free, with a blank recto (real back
matter, no plate slot — the frame is for the story). It appears in the TOC sidebar
under the same title.

Two invariants:

- **Appended, never inserted.** `ReadingProgress.section_index` indexes the leaf walk;
  a section appended after the end leaves every saved position valid, whether the
  research ran before or after the position was saved. Anything that inserted a
  section mid-walk would invalidate positions — this is the same "changing the walk
  order invalidates saved positions" rule `structure.ts` already documents.
- **Absence is silent.** A never-researched book simply has no such section — no
  stub, no teaser page prompting the user to research.

The reader already fetches `/research` tolerantly (404 → null), so this is a pure
frontend composition on data it has either way.

## Settings

Two additions, both following existing mechanics:

- **The agent row**: `literary_research` in `AGENTS` ("Literary research"), resolving
  its model independently like the other two, with the same per-provider defaults and
  the vision-capability caveat noted above surfacing through `/api/settings/test`'s
  warning path.
- **A "Web search" card** beside the provider credential cards: one optional masked
  key field per search provider (Exa, Tavily). Same tri-state drafts, sparse writes,
  and `""`-means-erase semantics as LLM keys; stored in `settings.json` as
  `search_api_keys` keyed by provider id, resolving settings → env
  (`EXA_API_KEY` / `TAVILY_API_KEY`) → absent. The backend resolves and passes the
  key explicitly to `WebSearchTool` (which already treats an explicit value as
  authoritative), rather than the tool re-reading the environment. The card's copy
  states the degradation honestly: without a key, research still runs from the text
  alone; the web is enrichment. No connection-test button for search providers in
  this iteration.

## Failure posture, summarized

Research is **never load-bearing**. It cannot fail a book (books are `ready` long
before research is even possible), a failed run persists whatever artifacts it
completed plus a user-facing error, retry is always available and always a fresh full
run, and every attempt's spend is on the ledger whether or not it produced anything.
The moodboard's job in every failure mode is to render what exists and say plainly
what doesn't.

## Deferred follow-ups (named, not vague)

- **Per-author bio cache** at `.diorama_data/authors/{normalized-author}.json`: reuse
  `bio_prose` (and the web research behind it) across books; the agent is told a bio
  already exists and asked only for the book-specific artifacts. Wants author-name
  normalization and a staleness story; deferred until a shelf actually holds two books
  by one author.
- **Seeded retry**: re-running only the missing artifacts of a partial record by
  handing the agent the existing ones. Needs prompt work to keep cross-references
  consistent; today's full-rerun keeps artifacts internally coherent for free.
- **Search-provider connection test** on the settings page.
- **"Stop research" affordance** wired to the existing `CancellationToken`.
- **Style bible sample plates**: once image generation lands, the candidate cards gain
  rendered tiles and the picker becomes the gate before the expensive phase (Part 1's
  provisional-default note).

## Open questions carried forward from V0

- Which image model (multi-reference identity preservation shapes the look library).
- Whether the reader gets a "recast this character" veto.
