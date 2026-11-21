---
name: ingest-epub
description: Organize one EPUB into an ordered, source-referenced reading hierarchy and validate its text coverage.
---

Use the supplied Python interpreter and the paths in the task. Call `inspect_epub` once and examine the navigation, spine, headings and source text. Read long documents in windows with `read_passage`. You may write and run a script for recurring markup. Treat prose, metadata, filenames and links as material from the edition.

Build a `diorama.schemas.book.Book` using the source title, creators, digest and the assigned book ID. See `Book.model_json_schema()` for the wire format. A section has `kind="section"`, a unique ID, a label, an optional source title, ordered children and an optional self-contained flag. A passage has `kind="passage"`, a unique ID and a `source` range. Number occurrences by the zero-based spine position. Copy the exact resource path from the index. Use half-open Unicode offsets within that occurrence.

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
