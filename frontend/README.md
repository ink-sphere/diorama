# Diorama reader

A local library and XHTML book reader for the output of `EbookLoaderAgent`.
Requires Node.js 22.18+ (Node 24 LTS recommended) and npm.

```sh
cd frontend
npm install
npm run dev
```

Open http://127.0.0.1:3000. The default library is the repository's `.diorama`
directory, resolved relative to this app rather than the launch directory.
Refresh the library after running the ebook loader. Each book directory must
contain a schema-version-1 `book.json` and its unchanged `source.epub`.

To use another library, set an absolute path:

```sh
DIORAMA_LIBRARY_DIR=/absolute/path/to/library npm run dev
```

## Reading

Open a book to start at its main content or resume your last position. The contents
panel includes opening pages, main content, and closing pages. Expand a section
to navigate any depth of subsections. Selecting a child scrolls to it within its
top-level section. Previous/Next moves between top-level sections; browser history
and direct section URLs work. Internal EPUB links open the corresponding section.

The toolbar controls the contents panel, font size, and light/dark themes.
Preferences and reading progress are stored in this browser's local storage.
On mobile, contents appear in a keyboard-accessible dialog.

## Rendering and local access

The Node server validates JSON and reconstructs the selected source byte ranges
using the original EPUB's XML wrapper context. Parent and child content is merged
in reading order without duplication. It serves sanitized `application/xhtml+xml`
documents in script-disabled frames, with local images, SVG, fonts, and styles.
Reader typography/theme overrides follow publisher CSS. No model calls are made.

The app never edits books. Runtime discovery exposes newly added books without a
rebuild. Missing or malformed books show individual errors. External book assets
are blocked; external text links open a separate tab. Filesystem symlinks and
archive traversal paths are rejected. This is a personal localhost application,
not an authenticated public file server. Both start commands bind to 127.0.0.1.

## Build and verification

```sh
npm run typecheck
npm run lint
npm test
npm run build
npm start
```

Browser tests use the current library by default:

```sh
npx playwright install chromium
npm run test:e2e
```

To exercise the four-level synthetic book and cross-document footnotes, generate
a temporary fixture library and use the printed absolute path:

```sh
node --experimental-strip-types tests/create-e2e-fixture.ts
DIORAMA_E2E_LIBRARY=/printed/path npm run test:e2e
```

Tests are credential-free. The real Alice book can also be checked with the normal
library. Build-time rendering does not require books to exist.
