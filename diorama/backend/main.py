"""FastAPI app for the Diorama library: list/upload books, stream agent traces.

Run from the repo root with:

    uv run uvicorn diorama.backend.main:app --reload --port 8000
"""

from __future__ import annotations

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

from diorama.backend.routes.books import router as books_router  # noqa: E402
from diorama.backend.routes.settings import router as settings_router  # noqa: E402
from diorama.backend.routes.usage import router as usage_router  # noqa: E402

app = FastAPI(title="Diorama Library API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(books_router)
app.include_router(settings_router)
app.include_router(usage_router)


@app.get("/api/health")
async def health() -> dict[str, bool]:
    return {"ok": True}
