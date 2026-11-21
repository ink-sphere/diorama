# Reading an EPUB with the Diorama Python API

Use `inspect_epub` to examine an edition on disk. Use `BookIngestion` when you want Geppetto to infer its structure and save it to your library. Both paths use the same source index, so a passage in a published book resolves to text from the supplied edition. For installation, worker setup, and ChatGPT sign-in, see the [README](../README.md#set-up-the-worker).

## Inspect an edition locally

The EPUB spine gives the publisher's reading order. A table of contents may list an act while omitting its scenes. To see what the edition supplies, inspect both its navigation entries and the headings in each spine document. This example runs without Docker or a model call.

```python
from pathlib import Path

from diorama.agent.tools import inspect_epub

index = inspect_epub(Path("books/my-book.epub"))
print(index.title, index.authors)
for entry in index.navigation:
    print("  " * entry.depth + entry.title, entry.target)
for document in index.spine:
    if document.linear:
        for heading in document.headings:
            print(document.occurrence, document.resource, heading.offset, heading.title)
```

`SourceIndex.spine` also includes nonlinear documents, such as a separately stored endnote. A resource can occur several times in the spine. Its zero-based occurrence number distinguishes each appearance.

`SourceIndex.cover_resource` gives the archive-relative path of an EPUB 3 cover image or an EPUB 2 metadata cover, when the edition declares one.

## Ingest and reopen a book

In an async application, create a library with `BookIngestion.local(data_root)` from `diorama.backend.ingestion` and call `await library.add_book(book_id, uploaded_epub)`. Supply a stable string ID containing at least one non-whitespace character and at most 512 characters. The default library directory is `.diorama_data/`. [Runnable ingestion example](../README.md#ingest-a-book)

| `IngestionResult` member | Use |
| --- | --- |
| `open_book()` | Read the published JSON as a `Book` with a root section, title, and authors. |
| `manifest_path` | Keep this path to reopen the structure in another process with `Book.model_validate_json(manifest_path.read_bytes())`. |
| `source_epub` | Open the retained copy to resolve passages or access original XHTML, illustrations, and styles. |
| `source_sha256` | Identify the exact EPUB bytes associated with the manifest. |
| `uncertainties` | Read the structural decisions Geppetto flagged as ambiguous. |

Diorama stores editions by digest under `editions/` and manifests under `manifests/`. A repeated request reuses the manifest when the book ID, edition digest, ingestion version, instructions, and skill text match. Concurrent successful attempts for the same key return the same published file. A changed EPUB gets its own edition and manifest, so saved paths still open the earlier version.

Removing an edition through the web Library deletes that digest’s archived EPUB and manifests from the shared data directory. Adding the EPUB again prepares a fresh library entry.

Pass a synchronous listener such as `on_progress=lambda update: print(update.note)` to `add_book` to observe indexing, outline discoveries, validation, and any repair attempts. A `PreparationUpdate` can carry an opening outline of `(depth, title)` pairs. Its optional `previews` entries carry a stable key, depth and title. An excerpt includes the exact `source` range and up to 320 Unicode code points of indexed text. Ambiguous headings have labels only.

Pass an asynchronous `on_trace` listener to receive display-safe `TraceEvent` instances in order. Each includes the SDK method, host receipt time, thread and turn IDs, attempt, purpose, approved fields, and any omission or truncation markers. A worker implementing only `run(task)` can still report preparation milestones. Cached ingestions have no new SDK turn.

```python
from pathlib import Path

from diorama.agent.trace import TraceEvent
from diorama.backend.ingestion import BookIngestion


async def observe(event: TraceEvent) -> None:
    print(event.attempt, event.purpose, event.name, event.fields.get("status"))


library = BookIngestion.local()
result = await library.add_book("my-edition", Path("book.epub"), on_trace=observe)
```

The web Library stores observations alongside each edition in SQLite. Read the current snapshot at `/api/books/{digest}/activity`, follow its ordered history at `/api/books/{digest}/trace?after=0&limit=50`, and fetch individual records at `/api/books/{digest}/trace/{seq}`. The legacy `/preparation` endpoint exposes the latest 32 Library notes.

## Resolve passages in reading order

A `Section` has a label, an optional title, and ordered children. A child can be another section or a `Passage`. For example, an act can contain a stage direction followed by two scenes. Visit children in their stored order to keep that stage direction in place.

```python
from collections.abc import Iterator

from diorama.agent.harness import IngestionResult
from diorama.agent.tools import inspect_epub, read_passage, validate_hierarchy
from diorama.schemas.book import Passage, Section


def passages(node: Section | Passage) -> Iterator[Passage]:
    if isinstance(node, Passage):
        yield node
    else:
        for child in node.children:
            yield from passages(child)


def reading_text(result: IngestionResult) -> Iterator[str]:
    book = result.open_book()
    index = inspect_epub(result.source_epub)
    validate_hierarchy(index, book)
    for passage in passages(book.root):
        yield read_passage(index, passage.source)
```

Each `SourceRange` identifies a spine occurrence, its archive-relative resource path, and a half-open interval `[start, end)` within `Document.text`. Offsets count Unicode code points. The index preserves element text and tails, emits a newline for `<br/>`, and terminates block elements with a newline when needed. To locate a passage in the XHTML, find the overlapping entries in `Document.positions`. Each supplies an XPath and a slot (`text`, `tail`, or `break`).

`validate_hierarchy` checks that the digest and metadata match, IDs are unique, and the passage ranges cover every nonempty linear spine entry exactly once in order. Whether a boundary marks a scene or a self-contained story is a separate inference expressed in the section label and flags.

## Worker lifecycle and errors

`BookIngestion.local()` composes a `GeppettoHarness` with the installed instructions, ingestion skill, tools, and Docker worker. To supply a scripted worker in a test, construct `GeppettoHarness(agent, worker_factory, publication_root)` and pass it to `BookIngestion(harness)`. The factory receives the agent, retained EPUB path, and scratch directory. Its async context manager yields a worker whose `run(task)` method writes `candidate.json` in scratch. The host checks the candidate against the retained edition before publication.

| Exception | Meaning for the caller |
| --- | --- |
| `ValueError` | The book ID is blank or exceeds 512 characters. |
| `diorama.agent.tools.InvalidEpub` | The source could not be read or indexed. Supported spine documents contain XML-readable XHTML. |
| `diorama.agent.tools.InvalidCandidate` | A range or hierarchy failed validation. During ingestion the worker gets one repair turn per attempt before this reaches the caller. |
| `diorama.agent.worker.SignInRequired` | The Docker worker needs a ChatGPT session. This subtype of `WorkerError` lets the web library keep the upload and resume it after device sign-in. |
| `diorama.agent.worker.WorkerError` | The worker could not start or complete a turn. Its `WorkerLost` subtype indicates a closed SDK transport and permits one fresh attempt. |
| `TimeoutError` | The harness exceeded its overall deadline, which defaults to 600 seconds and includes retries. Caller cancellation propagates as `asyncio.CancelledError`. |

`InvalidEpub` and `InvalidCandidate` both inherit from `ValueError`. Catch the specific exceptions first when reporting input and validation failures separately.
