# Diorama reader

A local React application for reading the `StoryBook` artifacts produced by ebook structure runs.

```bash
cd frontend
npm install
npm run dev
```

Open http://127.0.0.1:5173. The library automatically reads `../.ebook-runs/*/storybook.json` and also supports the older `*/output/storybook.json` layout. Use **Open JSON** or drag a file onto the library to open any standalone `storybook.json`. Files selected in the browser stay in the browser.

The reader follows the recursive `StructureNode.content` model in `diorama/models/storybook.py`. Parts, chapters, and other parent nodes open child-section overviews; text nodes open their `TextContent` fragments. Use the expandable contents tree and clickable breadcrumbs to navigate any depth, or **Read from beginning** to enter a group in source order. Selected parent and text nodes are both saved and supported in run links.

Nodes with `is_part_of_narrative: false` and their descendants are hidden from the contents, overviews, and reading navigation. Section counts and progress include only visible narrative sections. Original node paths remain stable; saved positions or links to hidden nodes fall back to the first narrative section. Books without narrative sections show an empty state.

The reader supports section navigation, internal footnote links, saved reading position, font size, serif/sans-serif type, and paper/night themes. It renders sanitized HTML and uses Markdown for plain-text fragments. New runs contain only `storybook.json`; external image files are not retained. Older runs can serve available images from `output`, `input`, or their original ZIP/EPUB archive. Unavailable images display their alternative text.

Switch between **Read** and **Characters** in the book header. The Characters view loads `.ebook-runs/<run-id>/character-profiles.json` when first opened, lists the cast alphabetically, and searches names and aliases. Select a character to read their description and expand **Appearance** for the supplied visual description. Profiles can include whole-book spoilers, so descriptions appear only after selection. Returning to Read restores the current section and scroll position; reading settings apply to both views.

On mobile, selecting a character opens their profile, and **All characters** returns to the searchable list. Missing, empty, or invalid profile files have their own states and a **Reload profiles** action. Profiles must match the open book's ID. Standalone StoryBook imports remain readable but cannot automatically access a sibling profiles file; open the run from the local library to use Characters. No portraits are generated or requested.

Use another run directory by setting `EBOOK_RUNS_DIR` before starting the server:

```bash
EBOOK_RUNS_DIR=/absolute/path/to/runs npm run dev
```

```bash
npm test
npm run test:e2e
npm run build
npm run preview
```

Preview includes the local run API, at http://127.0.0.1:4173. A static deployment of `dist` supports the file picker; automatic run discovery and character profiles require the Vite dev or preview server. The server binds to loopback and only exposes book artifacts, character profiles, and image assets within the configured run directory. Reading positions and preferences are stored in this browser. No model calls or changes to ebook runs occur.

Browser tests use installed Google Chrome on macOS. On other systems, run `npx playwright install chromium` first, or set `PLAYWRIGHT_CHROMIUM_EXECUTABLE` to your Chrome/Chromium executable. Tests launch an isolated browser and their own server on port 5180.
