# EbookLoaderAgent — Dynamic Structure Extraction

> **Status:** Design / pre-implementation
> **Audience:** contributors building the ebook-ingestion layer of Diorama
> **Scope:** how an EPUB is turned into a hierarchical, queryable structure of
> chapters / acts / scenes / parvas / adhyayas — dynamically, per book — using a
> specialized [`ReactAgent`](#71-ebookloaderagentreactagent).

---

## 1. Problem

Diorama's first ingestion step must take an arbitrary EPUB and discover **how the
book is divided**, then arrange every piece of text into that division.

The division is **not uniform across books**:

| Book | Native structure |
|---|---|
| *Dracula* | flat list of `CHAPTER I … CHAPTER XXVII` |
| *The Iliad* | `BOOK I … BOOK XXIV`, but each book internally has an `ARGUMENT` (prose) then verse |
| A Shakespeare play | `Act → Scene` — no "chapters" at all |
| *Mahabharata* | `parva → upa-parva → adhyaya → shloka` — a deep, repeating hierarchy |

A fixed "chapter / subchapter" model cannot represent these. The structure — its
**depth**, its **level names**, and its **boundaries** — has to be inferred from
each file at load time.

### 1.1 What makes it hard

Inspecting the sample EPUBs (`books/dracula.epub`, `books/iliad.epub`) shows two
realities the design must absorb:

1. **The Table of Contents is often flat and incomplete.** Dracula's NCX lists
   chapters cleanly, but the Iliad's ToC lists only `BOOK I … XXIV` — it does
   **not** capture the `ARGUMENT`/verse split *inside* each book. For plays and
   epics, the deepest levels (scenes, adhyayas) frequently appear **only as
   headings inside the HTML**, not in the ToC at all, and sometimes many levels
   deep inside a single spine file.
2. **Whole books do not fit in an LLM context window.** *Mahabharata*-scale texts
   have tens of thousands of paragraphs. The agent cannot "read the book and
   describe its structure" in one shot. It must navigate.

So the real task is: **reconcile the ToC against the in-content heading structure,
decide on a hierarchy schema that varies per book, and arrange text into it —
without ever holding the whole book in context.**

---

## 2. Goals & non-goals

**Goals (v1)**

- Discover an **arbitrary-depth** hierarchy with **semantic, book-native level
  names** (`act`, `scene`, `parva`, `adhyaya`, `canto`, `letter`, …).
- Resolve every level that appears as a **heading / structural marker**.
- Arrange **all** body text into the leaves of that hierarchy, faithfully (no
  paraphrasing, no dropped text).
- Separate the **narrative** from front/back matter (cover, preface, transcriber
  notes, Project Gutenberg license) without discarding it.
- Return an in-memory **Pydantic** model that serializes to JSON.

**Non-goals (v1, explicitly deferred)**

- **Verse / shloka-level** splitting (line-level leaves). The mechanism that will
  enable it later ([`child_pattern`](#42-the-submitted-tree--two-boundary-modes))
  is built in v1, but we stop at heading-level units.
- **Database persistence.** No SQLAlchemy models / Alembic migrations yet — output
  lives in memory and as JSON.
- **Generated content** (per-unit summaries, narrative roles). v1 carries only
  light, heading-derived normalization.

---

## 3. Decisions of record

These were settled during brainstorming and drive the rest of the design. Recorded
here so the rationale is not lost.

| # | Decision | Choice | Why |
|---|---|---|---|
| D1 | **Tree model** | Dynamic, arbitrary depth, **semantic** level names | A fixed 2-level model cannot represent Acts/Scenes or the 4-level Mahabharata hierarchy. |
| D2 | **Text arrangement** | Agent emits **boundaries**; deterministic code slices text | Cheap, no hallucination of book text, scales to huge books. The agent decides *structure*, not *content*. |
| D3 | **Granularity (v1)** | Down to **in-content heading units** | Achievable and faithful; verse-level deferred. |
| D4 | **Output target** | **Pydantic + JSON in memory** | Matches v0.0.1 state; keeps this work focused, no DB coupling. |
| D5 | **Deep repetition** | Support **pattern-generated levels** (`child_pattern`) | Mahabharata has hundreds of adhyayas per parva — the agent discovers the *rule* once; code enumerates instances. |
| D6 | **Non-narrative matter** | **Keep & label** with a `kind` field | Storybook reading wants the narrative isolated, but prefaces/introductions stay queryable. |
| D7 | **Node metadata** | **Light normalization** (level_type, raw + clean title, parsed ordinal) | Near-zero extra cost, derived from heading text; gives queryable structure. |

---

## 4. Core idea: a flat, addressable block stream

Everything rests on one abstraction. **The agent never sees raw HTML and never
holds the whole book.** A deterministic pre-pass converts the EPUB into a flat list
of numbered **blocks**, and:

> **A boundary is just a `block_id`.**
> A structural unit is "starts at block *N*"; its end is the next boundary in
> reading order.

This is deliberately *not* anchor-based (`file#fragment`). Anchors break exactly
where we need them most — Shakespeare scene headings and Mahabharata adhyaya
markers are frequently styled `<p>`/`<div>` elements with **no `id`**. A monotonic
block index is always available.

### 4.1 The data flow

```
epub ─▶ EbookContext.parse()  ─▶ flat BLOCK stream + anchored ToC      (deterministic, no LLM)
            │
            ▼
      EbookLoaderAgent.run(prompt)  ─▶ agent explores via tools, ends by
            │                          calling submit_structure(tree)
            ▼
      submit_structure validates tree vs real block_ids   (errors ─▶ agent self-corrects)
            │
            ▼
      slicer: expand child_patterns ─▶ assign [start,end) ranges ─▶ slice text ─▶ coverage check
            │
            ▼
      EbookStructure   (Pydantic; .model_dump_json())
```

---

## 5. Component: `EbookContext` (deterministic parser)

`EbookContext.parse(epub_path)` (in `diorama/ebook/context.py`) uses **ebooklib +
BeautifulSoup** to walk the **spine in reading order** and emit the block stream.
Pure Python, no LLM — independently unit-testable.

```python
class Block(BaseModel):
    block_id: int            # globally monotonic across the whole book
    spine_index: int         # which spine item this came from
    tag: str                 # h1..h6, p, div, blockquote, li, ...
    classes: list[str]
    element_id: str | None   # original element id, for ToC anchor resolution
    text: str                # normalized plain text
    is_heading_candidate: bool
```

### 5.1 Generous heading-candidate detection

`is_heading_candidate` is intentionally **generous** — this is what makes
Shakespeare and Mahabharata work, because their divisions are often styled
paragraphs, not `<h*>` tags. A block is a candidate if **any** of:

- `tag` ∈ `h1..h6`; **or**
- `class`/`id` contains a structural keyword
  (`chapter|act|scene|section|title|heading|part|book|canto|parva|adhyaya`); **or**
- a heuristic fires — short **ALL-CAPS** line, a leading numbering pattern
  (`^(chapter|act|scene|book)\s+[ivxlc\d]`), centered/bold single line.

The pre-pass only *surfaces candidates*. The **agent makes the final judgment**
(with `read_blocks` to verify); false positives are expected and tolerated.

### 5.2 The anchored ToC

`EbookContext` also builds the ToC tree (from `book.toc` / NCX) **resolved into the
block stream**: each entry's `file#fragment` →
`(spine_index, element_id)` → `block_id`.

When a fragment is missing or an entry has no usable anchor, fall back to:

1. first block of the referenced spine item; then
2. **fuzzy-match** the ToC title against nearby heading-candidate text using
   [`thefuzz`](https://pypi.org/project/thefuzz/) (already a project dependency).

The agent therefore receives a ToC **already anchored** into the same coordinate
system (`block_id`) it uses for everything else.

### 5.3 Responsibilities held on the context

`EbookContext` is the single source of truth for one `load()`:

- the **block stream** (`list[Block]`) and lookups by id / range;
- the **anchored ToC** tree;
- spine metadata and book title;
- `submitted_structure` — the raw tree stashed by the terminal tool (see §6).

---

## 6. Component: the agent's tools

In `diorama/ebook/tools.py`. `build_ebook_tools(ctx)` returns tool instances that
all share one `EbookContext`. One small framework extension is required: `Tool`
gains `model_config = ConfigDict(arbitrary_types_allowed=True)` so a tool can hold
a `context: EbookContext` reference (tools are currently stateless).

| Tool | Purpose | Returns |
|---|---|---|
| `get_overview` | book title, spine count, total blocks, heading-candidate count. *Also pre-injected into the opening prompt* to save a turn. | summary dict |
| `get_toc` | the anchored ToC tree | nested `{title, block_id, children}` |
| `list_headings(start_block, end_block, tag_filter?)` | scoped list of heading candidates — the agent's primary map. Scoping keeps huge books tractable. | `[{block_id, tag, classes, text}]` |
| `read_blocks(start_block, end_block)` | peek the actual content to disambiguate (real heading vs decoration; is `ARGUMENT` a sub-unit?; what marks a verse?) | `[{block_id, tag, text}]` |
| `search_blocks(regex, start_block?, end_block?)` | find a **repeating marker** (`^SCENE [IVX]`, `^अध्याय \d+`). Returns **total count + a sample**. | `{count, matches:[{block_id, text}]}` |
| `submit_structure(tree)` | **terminal**: hand back the hierarchy; validate, stash, return stats or errors. | `{ok, stats}` / `{errors:[…]}` |

`search_blocks` is the **scalability lever**: the returned `count` is how the agent
decides "this level is regular → describe it with a pattern instead of enumerating
hundreds of boundaries."

---

## 7. The submitted tree (agent → code contract)

`submit_structure` uses `Tool.parameters_schema` (raw nested JSON Schema) because
the tree is recursive. Each node has **two mutually-exclusive child modes**:
explicit `children` **or** a `child_pattern`.

### 7.1 Node schema

```jsonc
Node = {
  "level_type":  "scene",              // semantic, the book's own vocabulary
  "title":       "SCENE II. A street.",// raw heading text
  "clean_title": "A street",           // light normalization (D7)
  "number":      2,                    // parsed ordinal (light normalization, D7)
  "kind":        "narrative",          // narrative | front_matter | back_matter | supplementary (D6)
  "start_block_id": 412,               // THE BOUNDARY (D2)

  // EITHER explicit children …
  "children": [ Node, … ],

  // … OR a pattern that the deterministic expander applies (D5):
  "child_pattern": {
    "level_type": "adhyaya",
    "regex": "^अध्याय (\\d+)",          // group 1 (optional) → number
    "kind": "narrative"
  }
}
```

### 7.2 Validation (in `submit_structure`)

The tool rejects malformed trees and **returns the errors to the agent**, which
self-corrects on the next turn — reusing the existing "tool errors surface to the
model" loop in [`ToolRouter`](../diorama/core/router.py). Checks:

- every `start_block_id` exists in the block stream;
- siblings are strictly increasing by `start_block_id`;
- each child's start lies within its parent's `[start, end)` range;
- overall depth-first start order is monotonic (no interleaving);
- each `child_pattern.regex` compiles;
- `level_type` non-empty; `kind` ∈ the allowed set.

On success it stores the tree on `ctx.submitted_structure` and returns stats. This
is the agent's terminal action.

---

## 8. Component: the slicer (deterministic)

In `diorama/ebook/slicer.py`. Turns the validated tree into the final structure:

1. **Expand** every `child_pattern` over its node's `[start, end)` range into
   explicit child nodes — `number` parsed from regex group 1 (when present),
   `title` taken from the matched block's text.
2. **Assign ranges.** Each node's `end_block_id` = the start of the next boundary
   in reading order at the same-or-higher level. Internal nodes capture
   `preamble_text` = the blocks between the node's own start and its first child.
   *This is exactly the Iliad's `ARGUMENT` before the verse, or the text between an
   `ACT I` heading and `SCENE 1`.*
3. **Coverage check.** Produce a `CoverageReport{covered, gaps:[(a,b)], overlaps}`.
   Gaps usually mean front/back matter the agent didn't include; reported as
   **warnings** in v1, not a hard failure.

---

## 9. Output model

In `diorama/ebook/models.py`.

```python
class StructureNode(BaseModel):
    level_type: str
    kind: Literal["narrative", "front_matter", "back_matter", "supplementary"]
    title: str
    clean_title: str | None
    number: int | None
    start_block_id: int
    end_block_id: int
    preamble_text: str | None      # internal nodes: text before the first child
    text: str | None               # leaves: the unit's full text
    children: list["StructureNode"]

class CoverageReport(BaseModel):
    covered: bool
    gaps: list[tuple[int, int]]
    overlaps: list[tuple[int, int]]

class EbookStructure(BaseModel):
    source_path: str
    title: str | None
    level_types: list[str]         # discovered vocabulary, e.g. ["parva","upa-parva","adhyaya"]
    root: list[StructureNode]
    coverage: CoverageReport
    usage: dict
    cost_usd: float
```

---

## 10. The agent

### 10.1 `EbookLoaderAgent(ReactAgent)`

In `diorama/ebook/ebook_loader_agent.py`. A thin, **reusable** subclass: model
config in `__init__`; book-bound tools swapped in per `load()`.

```python
class EbookLoaderAgent(ReactAgent):
    def __init__(self, *, model_id: str = "openrouter/...", max_iterations: int = 40, **kw):
        super().__init__(
            tools=[],
            instructions=EBOOK_LOADER_INSTRUCTIONS,
            model_id=model_id,
            max_iterations=max_iterations,
            **kw,
        )

    async def load(self, epub_path: str) -> EbookStructure:
        ctx = EbookContext.parse(epub_path)
        self.tool_router = ToolRouter(build_ebook_tools(ctx))    # clean per-book override point
        result = await self.run(render_load_prompt(ctx))         # overview pre-injected
        if ctx.submitted_structure is None:
            raise EbookLoadError("agent finished without calling submit_structure")
        structure = build_structure(ctx, ctx.submitted_structure)  # expand + slice + coverage
        structure.usage, structure.cost_usd = result.usage, result.cost_usd
        return structure
```

Why this shape:

- [`ReactAgent.run`](../diorama/core/react.py) already returns a
  `ReactAgentResult` carrying cumulative `usage` / `cost_usd`, and dispatches via
  `self.tool_router` — so swapping the router before `run` is the only hook needed.
- One agent instance handles many books; tools are rebound per `load()`.

### 10.2 Prompt strategy

`EBOOK_LOADER_INSTRUCTIONS` (in `diorama/ebook/prompts.py`, appended to the base
[`SYSTEM_PROMPT`](../diorama/core/prompts.py) via the `instructions=` arg) tells the
agent:

- **World** — "The book is a numbered BLOCK stream plus an anchored ToC. A boundary
  is a `block_id`."
- **Goal** — a dynamic, arbitrary-depth tree with **semantic level names from the
  book's own vocabulary** (Act/Scene, Parva/Adhyaya, Canto, Letter, Journal
  Entry — *not* generic names).
- **Method** — ToC-first → **always verify against in-content headings** (the ToC
  misses nesting: the Iliad's `ARGUMENT`, scenes inside an act) → drill top-down
  with scoped `list_headings` → `read_blocks` to disambiguate → for repeating deep
  levels use `search_blocks` to find the marker and emit a `child_pattern` →
  classify each top node's `kind` (separate story from Gutenberg boilerplate) →
  light-normalize (`number`, `clean_title`).
- **Terminate** — call `submit_structure`; on validation errors, fix and resubmit.

`render_load_prompt(ctx)` seeds the first message with the overview, the ToC, and
the first screen of heading candidates so the agent starts oriented.

---

## 11. Worked examples

How each archetype flows through the design:

- **Dracula (flat, explicit).** ToC cleanly lists chapters; agent verifies against
  `<h*>` headings, classifies front matter (`title`, `contents`) as
  `front_matter` and the Gutenberg license as `back_matter`, emits ~27 explicit
  `chapter` nodes. No patterns needed.
- **Iliad (ToC misses a level).** ToC gives `BOOK I … XXIV`. The agent
  `read_blocks` into a book, finds the `ARGUMENT` then verse, and either nests an
  explicit sub-node or records the `ARGUMENT` as the book node's `preamble_text`
  vs. a `verse` child. Front matter (Introduction, Preface) labeled, not dropped.
- **Shakespeare (heading-only nesting).** No chapters. `search_blocks("^ACT ")`
  and `search_blocks("^SCENE ")` reveal regular markers; agent emits `act` nodes
  each with a `child_pattern` for `scene` (or explicit scenes if few).
- **Mahabharata (deep + repeating).** `search_blocks` returns large `count`s for
  adhyaya markers; the agent describes `parva → adhyaya` with `child_pattern`s
  rather than enumerating hundreds of boundaries. The deterministic expander
  produces every instance.

---

## 12. File layout

```
diorama/ebook/
  __init__.py             # exports EbookLoaderAgent, EbookStructure
  models.py               # Block, StructureNode, EbookStructure, CoverageReport, SUBMIT_SCHEMA
  context.py              # EbookContext.parse() → blocks + anchored ToC
  tools.py                # ebook tools + SubmitStructureTool + build_ebook_tools(ctx)
  slicer.py               # pattern expansion, range assignment, coverage
  prompts.py              # EBOOK_LOADER_INSTRUCTIONS + render_load_prompt
  ebook_loader_agent.py   # EbookLoaderAgent(ReactAgent).load()
```

---

## 13. Testing strategy

The deterministic core carries most of the test weight; the LLM loop is exercised
with a fake model so CI stays free and reproducible.

- **Pure Python, no LLM**
  - `EbookContext` over `books/dracula.epub` and `books/iliad.epub`: block counts,
    spine ordering, ToC → `block_id` resolution (including the fuzzy fallback).
  - `slicer`: range assignment + `preamble_text` + coverage on a hand-written tree.
  - pattern expander: synthetic `SCENE I / II / III` blocks expand correctly.
  - validation: malformed trees are rejected with actionable errors.
- **Agent end-to-end with a `FakeModel`** (the ReactAgent test-suite already
  provides this infrastructure): script a sequence of tool calls ending in
  `submit_structure`, assert `load()` yields the expected `EbookStructure` —
  deterministic, no API cost.
- **Manual integration script** (à la `test.py`): run a real model over
  `books/dracula.epub` and a Shakespeare EPUB; eyeball the tree and cost.

---

## 14. Risks & deferred work

| Item | Disposition |
|---|---|
| Decorative ALL-CAPS lines mis-flagged as headings | Mitigated by agent judgment + `read_blocks` verification. |
| Purely-visual structure (CSS only, no tags/classes/ids) | Fall back to ToC + numbering-regex `search_blocks`; worst case → shallower structure (accepted v1 degradation). |
| Verse / shloka leaves | Deferred. `child_pattern` is the exact mechanism that will later split leaves by verse markers — no rework needed. |
| Cost ceiling on irregular deep books | Guarded by `max_iterations` + per-run cost tracking. A model stronger than the `gpt-4o-mini` default is recommended for real runs. |
| DB persistence | Deferred (D4). `EbookStructure` is serialization-ready for a later SQLAlchemy mapping. |

---

## 15. Open questions for review

1. **`EbookContext` as single source of truth** — block stream, anchored ToC, *and*
   the agent's `submitted_structure` all live on it. Keep the submission on the
   context, or hold it on the agent instead?
2. **`preamble_text` on internal nodes** — the chosen home for "text under ACT I but
   before SCENE 1" / the Iliad `ARGUMENT`. Does that surface that text the way we
   want, or should it become an explicit child node instead?
3. **Default model** — bump `EbookLoaderAgent`'s default above `gpt-4o-mini` for
   structure reasoning?
