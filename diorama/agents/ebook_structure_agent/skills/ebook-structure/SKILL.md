---
name: ebook-structure
description: Extract an ebook's content and inferred hierarchy into a validated StoryBook using coding tools. Use for ebook extraction, not literary summarization.
---

Produce `output/storybook.json` matching `reference/storybook_schema.json`. Read
`reference/task.json` for the input, source ID, interpreter, and validation scope.
Use `read`, `write`, `edit`, and `bash` to inspect the book, write extraction scripts,
run them, and repair the result. Choose a workflow appropriate to the actual format.

Inspect the format before parsing. For EPUB, compare metadata, spine order, TOC,
headings, section markup, and actual passages. XHTML files do not necessarily equal
chapters. Retain narrative and supplementary content, including non-linear notes.
Exclude navigation-only documents and account for exclusions. For other formats,
use available libraries or write an appropriate parser; report an unsupported
format or missing dependency rather than inventing content. Determine ZIP formats
from their entries, even when the filename ends in `.epub`. JSON book exports have
`manifest.json` and `book.json`; use `book.json`'s `chapters` array in stored order,
preserve each chapter's HTML `content`, and retain its source metadata. Profiles
and generated visualizations are supplementary application data, not book text.

Keep source text and intermediate results in files. Read bounded excerpts and
inspect uncertain boundaries. Maintain a durable working outline and scripts in
`work/` so context compaction does not lose progress. Decide how much context and
which passages are needed; there is no prescribed window size or number of passes.

Preserve source text, meaningful markup, images, tables, verse, and reading order.
Every in-scope source fragment must appear exactly once. Keep explicit titles;
use an empty title for untitled sections. Do not invent chapter or scene boundaries.
Classify narrative membership from content, including fictional prologues and
prefaces. A group's narrative flag is true if any child is narrative.

A node contains either text fragments or child structures. If a part has introductory
text as well as chapters, put the introductory text in a child section. Generate
`raw_text` from the source and `markdown_text` with `html2text.HTML2Text()` using
`body_width = 0`. Do not rewrite, summarize, or copy the whole book into model output.
Set `StoryBook.id` to the supplied SHA-256 `source_id`.

The following ordinary CLI utilities are available through `bash`. Substitute the
interpreter and input path from task data, quoting shell arguments with `shlex.quote`.

- `<python> reference/ebook_tools.py index <input> --output work/source.json`
  writes an EPUB source inventory with stable block IDs and original fragments.
- `<python> reference/ebook_tools.py build <input> --plan work/structure.json --output output/storybook.json`
  assembles an EPUB using a plan matching `reference/structure_plan_schema.json`.
  Groups have children; leaves have inclusive start/end block IDs. These utilities
  are optional; you can write your own extraction code and construct StoryBook directly.
- `<python> reference/ebook_tools.py validate <input> --book output/storybook.json`
  checks schema, structure, Markdown conversion, ID, and supported source comparisons.

Python helpers are also available under `reference/library`. Add that directory to
`sys.path` to import `diorama.models.storybook` or the ebook source helpers. Preserve
input and reference files. Fix your scripts and regenerate output when validation
fails. Independent host validation uses the original input and trusted validators,
so editing local helpers or staged input cannot change acceptance criteria.

Finish after writing and validating the complete artifact. Return a brief status;
the caller receives the StoryBook loaded from the artifact.
