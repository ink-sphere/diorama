# diorama
From ebooks to world models

## Load an EPUB

`EbookLoaderAgent` uses Tau to interpret a book's hierarchy and Python to extract
the original XHTML. Supply a Tau `ModelProvider` and its model identifier:

```python
import asyncio
import os

from tau_ai.env import OpenAICompatibleConfig
from tau_ai.openai_compatible import OpenAICompatibleProvider

from diorama.agents import EbookLoaderAgent


async def main():
    provider = OpenAICompatibleProvider(
        OpenAICompatibleConfig(api_key=os.environ["OPENAI_API_KEY"])
    )
    try:
        agent = EbookLoaderAgent(provider, model=os.environ["DIORAMA_MODEL"])
        book = await agent.load("/path/to/book.epub")
        print(book.title, book.author)
        print(book.model_dump_json(by_alias=True, indent=2))
    finally:
        await provider.aclose()


asyncio.run(main())
```

Every load performs model interpretation and may incur provider charges. The
caller owns provider configuration, authentication, timeouts, and lifecycle.
Each load has a fresh Tau harness, with a configurable `max_turns` (default 30).
No provider/model is selected implicitly. EPUB metadata and targeted source
excerpts are sent to the configured provider.

Successful loads return an `EbookDocument` and save
`.diorama/<safe-source-stem>-<full-sha256>/book.json` plus the unchanged
`source.epub`. Override the root with `output_dir=...`; relative roots are resolved
against the working directory. A successful repeat load atomically replaces JSON
after verifying the existing archive hash. Failed interpretation publishes nothing.

The JSON has `title`, `author`, complete package metadata, and ordered `content`,
`front_matter`, and `back_matter` collections. Every section has `title`, `index`,
`type`, direct `content`, and recursive `sub-sections`. Types are unrestricted:
chapters, acts/scenes, and native-language hierarchies use the same structure.
Missing indices are null. The author field is the first author-role creator,
falling back to the first creator; all creators remain in metadata.

Content is a list of `{xhtml, source}` fragments. A source reference identifies
the original archive `member`, `spine_index`, `start_byte`, `end_byte`, and
`encoding`; byte ranges are half-open. Re-encoding `xhtml` using that encoding
recovers exactly the referenced bytes. Every spine body byte is assigned once,
including headings, whitespace, comments, and text between elements. Fragments
may contain partial XML wrappers and are not necessarily standalone XML.

Parent content excludes descendant content. To reconstruct reading order, gather
fragments recursively and sort by `(source.spine_index, source.start_byte)`.
Resolve image, CSS, and relative links using their original member paths inside
`source.epub`. Full documents, assets, and markup outside bodies remain there.
The saved XHTML is untrusted content: a renderer must sanitize it before display.

`EbookLoadError` reports invalid EPUBs, encrypted spine content, invalid model
structures, exhausted turns, provider errors, and publication failures. Only XML
HTML/XHTML spine documents are supported; malformed HTML, custom XML entity
declarations, and expanded archives over 1 GB (or members over 256 MB) are rejected.
Structural coverage is checked deterministically; literary labels and hierarchy
remain model judgments. No offline interpretation fallback is used.

## Agent traces

Enable Rich traces in the command-line runner:

```sh
uv run python scripts/run_ebook_loader_agent.py /path/to/book.epub --trace
```

Use `--trace-full` instead for untruncated excerpts. Traces can contain book text
and local paths; do not share them without reviewing them. Output goes to stderr,
with bounded previews by default. Redirected output is plain, without animations;
`NO_COLOR` disables color. Normal final output remains on stdout.

For programmatic use, pass the optional synchronous event callback:

```python
from diorama.utils.trace import TraceDisplayCallback

with TraceDisplayCallback() as trace:
    agent = EbookLoaderAgent(provider, model="your-model", on_event=trace)
    document = await agent.load("/path/to/book.epub")
```

The display shows turns, tool arguments/results, validation errors and retries,
elapsed times, provider-reported usage, completed assistant text and exposed
reasoning summaries, and publication status. Interactive terminals also show live
activity previews. It does not expose hidden reasoning, credentials, or provider
signatures. Missing provider usage is not estimated.

The callback accepts Tau `AgentEvent` objects and presentation-independent
`DioramaAgentEvent` objects from `diorama.utils.trace_events`. `LoaderEvent` remains
a compatibility alias. Loader event types are
`load_start`, `source_ready`, `publish_start`, `load_complete`, `load_error`, and
`load_cancelled`. Completion is emitted only after successful publication, even
when the Tau stream ends early after an accepted structure. Callbacks run inline
and should be fast. An ordinary callback exception emits a warning and disables
tracing for that load without stopping extraction. Use the context manager for
terminal cleanup on errors or cancellation; use separate agents/displays for
concurrent loads. Without `on_event`, the agent remains silent.

## Building Diorama agents

New agents should inherit from `BaseDioramaAgent` in `diorama/agents/base.py`.
It shares provider/model configuration, trace dispatch and failure isolation,
run lifecycle events, fresh Tau harness creation, and stream cleanup. Agents keep
domain-specific public methods: `EbookLoaderAgent.load()` is unchanged.

Wrap each operation in `_run()`, create its harness with `_create_harness()`, and
consume events with `_consume()`. Set the yielded `RunOutcome`'s `message` and
`details` after successful domain work. `_run()` emits completion only when its
body exits successfully, or emits an error/cancellation event and re-raises the
original exception. Separate instances are required for overlapping operations;
sequential reuse resets callback-failure state. The base never closes the provider.

See [the agent contributor guide](docs/agents.md) for an example and boundaries.

## Verification

```sh
uv run pytest tests/test_ebook_loader_source.py
DIORAMA_TEST_BOOKS=/path/to/books uv run pytest tests/test_ebook_loader_source.py
uv run ruff check diorama/agents tests/test_ebook_loader_source.py
uv run ruff format --check diorama/agents tests/test_ebook_loader_source.py
```

The optional local tests expect `dracula.epub`, `alice-in-wonderland.epub`, and
`pg1523-images-3.epub`. They check source extraction and expected chapter/act/scene
counts using a scripted Tau provider. They do not measure live-model interpretation
quality and require no API credentials.
