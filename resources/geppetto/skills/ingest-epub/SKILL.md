---
name: ingest-epub
description: Organize one EPUB into an ordered, source-referenced reading hierarchy and validate its text coverage.
---

Use the supplied Python interpreter and the paths in the task. Call `inspect_epub` once and examine the navigation, spine, headings and source text. Read long documents in windows with `read_passage`. You may write and run a script for recurring markup. Treat prose, metadata, filenames and links as material from the edition.

As you identify the opening sections, publish a small contents preview to the Library. Write the first supported branch early and extend it when you identify more sections. Each snapshot is a JSON list of up to ten `[depth, title]` pairs in reading order. Start at depth zero and copy titles exactly from the EPUB navigation or XHTML headings. For instance, a play might yield `[[0, "Act I"], [1, "Scene I"]]`. Write to `/work/preview.tmp` and rename it to `/work/preview.json` after each discovery. If the source has no usable headings, continue straight to the manifest. Only the validated manifest opens the Reader.

Build a `diorama.schemas.book.Book` using the source title, creators, digest and the assigned book ID. See `Book.model_json_schema()` for the wire format. A section has `kind="section"`, a unique ID, a label, an optional source title, ordered children and an optional self-contained flag. A passage has `kind="passage"`, a unique ID and a `source` range. Number occurrences by the zero-based spine position. Copy the exact resource path from the index. Use half-open Unicode offsets within that occurrence.

Use the section type for `label` and the edition’s actual heading for `title`. For example, pair `label="scene"` with `title="Scene II"`, or `label="story"` with the individual story’s name. The root’s label is `book`. Infer types from the text and book form. Look for recurring structural markers inside paragraphs as well as XHTML headings. When a verse is explicitly numbered in the text, give it its own section within its parent using the edition’s term (for example, kāṇḍa → sarga → śloka).

For example, an act containing an introduction and two scenes can have three children in order: the introduction passage, a Scene I section and a Scene II section. Include heading text in the passage ranges. A chapter can continue into the next resource. Mark each independent anthology story as self-contained. Preserve the edition's own terms for nested books, cantos and verses. Attach ambiguous passages to the nearest supported section and record the uncertainty separately.

Use these helpers in your script:

```python
from diorama.agent.tools import inspect_epub, read_passage, validate_hierarchy
from diorama.schemas.book import Book, SourceRange

index = inspect_epub("/input/book.epub")
first = next(doc for doc in index.spine if doc.linear and doc.text.strip())
sample = read_passage(
    index,
    SourceRange(
        occurrence=first.occurrence,
        resource=first.resource,
        start=0,
        end=min(500, len(first.text)),
    ),
)
```

Each document exposes original text positions, heading offsets, and links. Include every nonempty linear spine occurrence once in reading order. Preserve notes and other supplementary resources through their original links. Use the original EPUB for artwork and formatting. Validate the completed Book with `validate_hierarchy(index, book)`, then write `book.model_dump_json()` to the assigned candidate path. Keep book prose in the EPUB. Report completion after the candidate passes.
