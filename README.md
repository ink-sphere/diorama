# Diorama

From ebooks to world models.

## Run the reading app

From the repository root, install the package and build the Geppetto worker using the commands below. Start the local, single-user server with one worker.

```sh
uv run uvicorn diorama.backend.app:create_app --factory --host 127.0.0.1 --port 8000
```

Open [Diorama](http://127.0.0.1:8000). In **Settings**, select **Connect ChatGPT** and complete device sign-in on the verification page. In **Library**, choose **Add EPUB**. You can leave the page while Geppetto works. An upload that needs sign-in stays on the shelf and resumes after the connection succeeds.

Choose **Start reading** once the edition is ready. **Contents** follows the hierarchy Geppetto inferred, including nested sections. The reader preserves the original indexed text and remembers the last section in this browser. The library uses cover artwork from the EPUB when available.

The server keeps editions, validated manifests, and its SQLite shelf under `.diorama_data/` in the launch directory. Identical EPUB bytes share a shelf entry. Failed jobs support retry, and interrupted jobs resume when the server restarts. Previously published Python API manifests are imported from the same directory. Uploads accept EPUBs up to 128 MiB. Settings and the ingestion worker share the dedicated Docker profile described below.

The library background uses Albert Pinkham Ryder’s *The Flying Dutchman*, from the [Standard Ebooks edition of Moby-Dick](https://standardebooks.org/ebooks/herman-melville/moby-dick). Font licenses accompany the local assets in `frontend/assets/`.

## Geppetto EPUB ingestion

Geppetto reads an EPUB in an isolated worker and organizes its passages into a hierarchy. The backend retains the original edition and exposes a manifest after the host verifies its digest, reading order, and text coverage. The first integration is a Python adapter for a local, single-user library.

For offline inspection, passage traversal, and the ingestion API, see the [Python package guide](docs/python-api.md).

### Set up the worker

Use Python 3.12 or newer, uv, and a local Docker engine running Linux containers. From the repository root run the following commands.

```sh
uv sync --group dev
uv build
docker build -f resources/geppetto/Dockerfile -t diorama-geppetto:local dist
```

Build from a `dist/` containing the current Diorama wheel. Rebuild the image after changing the tools or schema. The build context contains distribution files only. The wheel installs the instructions, skill, and worker policy under `share/diorama/geppetto/` in the Python environment.

Sign in once with the ChatGPT account whose Codex allowance you want to use. Complete the browser step displayed in your terminal. Device sign-in may need to be enabled in the account’s security settings. Usage limits depend on the plan. [Codex authentication](https://learn.chatgpt.com/docs/auth) · [Codex with a ChatGPT plan](https://help.openai.com/en/articles/11369540-using-codex-with-your-chatgpt-plan)

```sh
docker run --rm -it --user "$(id -u):$(id -g)" \
  -v diorama-geppetto-profile:/var/lib/geppetto \
  diorama-geppetto:local login --device-auth
docker run --rm --user "$(id -u):$(id -g)" \
  -v diorama-geppetto-profile:/var/lib/geppetto \
  diorama-geppetto:local login status
```

The SDK app-server uses this dedicated Docker volume for its ChatGPT session. A worker sees one EPUB mounted read-only and one writable workspace. A Codex workspace permission profile denies tool commands access to the sign-in directory. The non-root container drops Linux capabilities and uses a seccomp profile derived from Docker 28.4's Moby baseline with the namespace and mount operations required by bubblewrap. The upstream license accompanies the profile. [Moby seccomp baseline](https://github.com/moby/profiles/blob/seccomp/v0.1.0/seccomp/default.json)

### Ingest a book

```python
import asyncio
from pathlib import Path

from diorama.backend.ingestion import BookIngestion


async def main():
    library = BookIngestion.local()
    result = await library.add_book("my-book", Path("books/my-book.epub"))
    print(result.manifest_path)
    print(result.open_book().root)
    print(result.source_epub)


asyncio.run(main())
```

`BookIngestion.local()` uses `.diorama_data/`. Supply a different local directory with `BookIngestion.local(data_root)`. Use a filesystem supporting hard links and directory fsync. Repeating an ingestion for the same book ID, edition and ingestion version returns the published manifest. Changed editions get independent files. An invalid EPUB raises `InvalidEpub`, failed host validation raises `InvalidCandidate`, a worker failure raises `WorkerError`, and the overall deadline raises `TimeoutError`.

Each passage points to a spine occurrence and a half-open range of Unicode code points. The index retains XHTML text and tails, emits a newline for each explicit line break, and terminates block elements with a newline when needed. It leaves Unicode sequences intact. Linked notes, illustrations, and formatting remain in the archived EPUB. The current index handles XML-readable XHTML spine documents and reports unsupported editions explicitly.

### Verification

```sh
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv build
```

The default suite uses synthetic EPUBs and scripted workers. With the image built, the first command below checks the installed skill and sandbox without an account. The second runs the real model revision and hierarchy tests using the worker’s ChatGPT sign-in.

```sh
uv run pytest --geppetto-live tests/test_worker.py -k without_account
uv run pytest --geppetto-live tests/test_worker.py tests/test_ingestion_live.py
uv run pytest --geppetto-live tests/test_web_ingestion.py -k live_upload_reaches_reader
```
