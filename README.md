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
