"""Serve Diorama's local Library, Reader, Settings, and ingestion API."""

import asyncio
import json
import os
import tempfile
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from sysconfig import get_path

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from openai_codex import AsyncCodex
from starlette.middleware.trustedhost import TrustedHostMiddleware

from diorama.agent.tools import MAX_ARCHIVE, InvalidEpub
from diorama.backend import events
from diorama.backend.account import AccountConnection, account_client
from diorama.backend.ingestion import BookIngestion
from diorama.backend.library import BookPreparing, Library


def create_app(
    ingestion: BookIngestion | None = None,
    client_factory: Callable[[], AsyncCodex] = account_client,
) -> FastAPI:
    """Create a single-user web app backed by a local Geppetto ingestion harness."""
    library = Library(ingestion or BookIngestion.local())
    connection = AccountConnection(library.resume_waiting, client_factory)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with library.running():
            try:
                yield
            finally:
                await connection.close()

    app = FastAPI(title="Diorama", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]"]
    )

    @app.middleware("http")
    async def local_browser(request: Request, call_next):
        if (
            request.method not in {"GET", "HEAD", "OPTIONS"}
            and request.headers.get("X-Diorama-Request") != "1"
        ):
            return JSONResponse(
                {"detail": "This request must come from the Diorama app."},
                status_code=403,
            )
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'; form-action 'self'"
        )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def book(digest: str) -> dict:
        rows = library.books(digest)
        if not rows:
            raise HTTPException(404, "Book not found.")
        return rows[0]

    def ready(digest: str) -> dict:
        row = book(digest)
        if row["status"] != "ready":
            raise HTTPException(409, "This book is still waiting to be prepared.")
        return row

    @app.get("/api/books")
    def list_books() -> list[dict]:
        return library.books()

    @app.post("/api/books", status_code=202)
    async def add_book(request: Request) -> dict:
        if (
            request.headers.get("content-type", "").partition(";")[0].lower()
            != "application/epub+zip"
        ):
            raise HTTPException(415, "Upload an EPUB using application/epub+zip.")
        with tempfile.NamedTemporaryFile(dir=library.root, prefix="upload-") as staged:
            size = 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_ARCHIVE:
                    raise HTTPException(
                        413, "The EPUB exceeds the 128 MiB upload limit."
                    )
                if chunk:
                    await asyncio.to_thread(staged.write, chunk)
            staged.flush()
            await asyncio.to_thread(os.fsync, staged.fileno())
            upload = asyncio.create_task(
                asyncio.to_thread(library.ingest_upload, Path(staged.name))
            )
            try:
                digest = await asyncio.shield(upload)
            except asyncio.CancelledError:
                # Keep the staged file available to the indexing thread until it finishes.
                await upload
                raise
            except InvalidEpub as exc:
                raise HTTPException(422, str(exc)) from exc
            finally:
                library.changed.set()
        return book(digest)

    @app.post("/api/books/{digest}/retry")
    async def retry(digest: str) -> dict:
        book(digest)
        library.retry(digest)
        return book(digest)

    @app.delete("/api/books/{digest}", status_code=204)
    def remove_book(digest: str) -> Response:
        try:
            library.remove(digest)
        except BookPreparing as exc:
            raise HTTPException(
                409,
                "Geppetto is preparing this edition. Try again once preparation finishes.",
            ) from exc
        except OSError as exc:
            raise HTTPException(
                503, "Removal was interrupted. Try removing this book again."
            ) from exc
        return Response(status_code=204)

    @app.get("/api/books/{digest}/cover")
    def cover(digest: str) -> Response:
        with library.database() as db:
            row = db.execute("SELECT cover FROM books WHERE id=?", (digest,)).fetchone()
        if not row or not row[0]:
            raise HTTPException(404, "This edition has no cover.")
        return Response(row[0], media_type="image/jpeg")

    @app.get("/api/books/{digest}/contents")
    def contents(digest: str) -> dict:
        row = ready(digest)
        with library.database() as db:
            pages = [
                {"number": p["number"], "trail": json.loads(p["trail"])}
                for p in db.execute(
                    "SELECT number,trail FROM pages WHERE book=? ORDER BY number",
                    (digest,),
                )
            ]
        return {"book": row, "pages": pages}

    @app.get("/api/books/{digest}/activity")
    def activity(digest: str) -> dict:
        with library.database() as db:
            db.execute("BEGIN")
            snapshot = events.activity(db, digest)
        if snapshot is None:
            raise HTTPException(404, "Book not found.")
        return snapshot

    @app.get("/api/books/{digest}/trace")
    def trace(
        digest: str,
        after: int = Query(0, ge=0, le=2**63 - 1),
        limit: int = Query(50, ge=1, le=100),
    ) -> dict:
        with library.database() as db:
            db.execute("BEGIN")
            row = db.execute(
                "SELECT id FROM books WHERE id=? AND status!='removing'", (digest,)
            ).fetchone()
            if not row:
                raise HTTPException(404, "Book not found.")
            registration = db.execute(
                "SELECT min(seq) FROM ingestion_events WHERE book=?", (digest,)
            ).fetchone()[0]
            return {
                "registration_seq": registration,
                **events.trace_page(db, digest, after, limit),
            }

    @app.get("/api/books/{digest}/trace/{seq}")
    def trace_detail(digest: str, seq: int) -> dict:
        with library.database() as db:
            db.execute("BEGIN")
            row = db.execute(
                "SELECT id FROM books WHERE id=? AND status!='removing'", (digest,)
            ).fetchone()
            detail = (
                events.trace_detail(db, digest, seq)
                if row and 0 < seq < 2**63
                else None
            )
        if detail is None:
            raise HTTPException(404, "Trace entry not found.")
        return detail

    @app.get("/api/books/{digest}/preparation")
    def preparation(digest: str) -> dict:
        book(digest)
        return library.preparation(digest)

    @app.get("/api/books/{digest}/pages/{number}")
    def read_page(digest: str, number: int) -> dict:
        ready(digest)
        with library.database() as db:
            page = db.execute(
                "SELECT body FROM pages WHERE book=? AND number=?", (digest, number)
            ).fetchone()
        if not page:
            raise HTTPException(404, "Reading section not found.")
        return {"number": number, "text": page[0]}

    async def account_request(action: Callable[[], Awaitable[dict]]) -> dict:
        try:
            async with asyncio.timeout(30):
                return await action()
        except Exception as exc:
            raise HTTPException(
                503,
                "Geppetto could not reach the account service. Check Docker and the worker image, then try again.",
            ) from exc

    @app.get("/api/account")
    async def account_status() -> dict:
        return await account_request(connection.status)

    @app.post("/api/account/connect")
    async def connect() -> dict:
        return await account_request(connection.connect)

    @app.post("/api/account/disconnect")
    async def disconnect() -> dict:
        return await account_request(connection.disconnect)

    @app.post("/api/account/cancel")
    async def cancel_login() -> dict:
        return await account_request(connection.cancel)

    frontend = Path(__file__).resolve().parents[2] / "frontend"
    if not (frontend / "index.html").is_file():
        frontend = Path(get_path("data")) / "share/diorama/frontend"
    app.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
    return app
