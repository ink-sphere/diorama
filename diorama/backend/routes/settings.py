"""Settings endpoints: which model each agent runs, and the provider credential.

The GET/PUT pair is deliberately asymmetric. GET returns a *view* in which the API
key is masked, so a stored key can never be read back out of the API; PUT takes a
*partial* update, so the form can send everything it rendered without needing the
real key to round-trip. See :mod:`diorama.backend.settings`.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from diorama.backend.settings import (
    AGENTS,
    ConnectionTest,
    SettingsUpdate,
    SettingsView,
    build_view,
    load_settings,
    resolve_api_key,
    resolve_model_id,
    save_settings,
)
from diorama.models.catalogue import ModelInfo, list_models

router = APIRouter(prefix="/api/settings", tags=["settings"])

_KEY_URL = "https://openrouter.ai/api/v1/key"
_TEST_TIMEOUT = 15.0


class CatalogueEntry(BaseModel):
    """One selectable model, as the picker renders it."""

    id: str
    openrouter_id: str
    name: str
    vendor: str
    context_length: int | None = None
    prompt_price: float = 0.0
    completion_price: float = 0.0
    supports_tools: bool = False


class ModelCatalogue(BaseModel):
    """The picker's options, plus whether they are real or a stale/empty fallback."""

    models: list[CatalogueEntry] = Field(default_factory=list)
    available: bool = True


class TestRequest(BaseModel):
    """Optionally test a key the user has typed but not yet saved."""

    api_key: str | None = None


def _entry(model: ModelInfo) -> CatalogueEntry:
    return CatalogueEntry(**model.__dict__)


@router.get("")
async def get_settings() -> SettingsView:
    return build_view(await load_settings())


@router.put("")
async def put_settings(update: SettingsUpdate) -> SettingsView:
    unknown = set(update.agents or {}) - {a.id for a in AGENTS}
    if unknown:
        raise HTTPException(400, f"Unknown agent(s): {', '.join(sorted(unknown))}.")
    return build_view(await save_settings(update))


@router.get("/models")
async def get_models(refresh: bool = False) -> ModelCatalogue:
    """OpenRouter's catalogue for the model picker.

    Blocking HTTP behind a 24h disk cache, so it runs in a threadpool. An empty
    list is a normal offline outcome, not an error — the picker degrades to
    free-text entry rather than failing the page.
    """
    models = await run_in_threadpool(list_models, force=refresh)
    return ModelCatalogue(models=[_entry(m) for m in models], available=bool(models))


@router.post("/test")
async def test_connection(body: TestRequest | None = None) -> ConnectionTest:
    """Check the API key against OpenRouter, and sanity-check each agent's model.

    Uses the key in the request body when present (so the user can validate what
    they just typed before saving) and the resolved stored/env key otherwise.
    """
    import httpx

    settings = await load_settings()
    typed = (body.api_key or "").strip() if body else ""
    api_key = typed or resolve_api_key(settings)[0]
    if not api_key:
        return ConnectionTest(
            ok=False,
            message="No API key is set. Add one above, then test again.",
        )

    try:
        async with httpx.AsyncClient(timeout=_TEST_TIMEOUT) as http:
            response = await http.get(
                _KEY_URL, headers={"Authorization": f"Bearer {api_key}"}
            )
    except Exception:  # noqa: BLE001 — offline is the common case here
        return ConnectionTest(
            ok=False, message="Couldn't reach OpenRouter. Check your connection."
        )

    if response.status_code in (401, 403):
        return ConnectionTest(ok=False, message="OpenRouter rejected that API key.")
    if response.status_code >= 400:
        return ConnectionTest(
            ok=False,
            message=f"OpenRouter returned {response.status_code}. Try again shortly.",
        )

    try:
        data = response.json().get("data") or {}
    except Exception:  # noqa: BLE001
        data = {}

    return ConnectionTest(
        ok=True,
        message="Key accepted by OpenRouter.",
        label=data.get("label") or None,
        usage_usd=_maybe_float(data.get("usage")),
        limit_usd=_maybe_float(data.get("limit")),
        is_free_tier=data.get("is_free_tier"),
        warnings=await _model_warnings(settings),
    )


def _maybe_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


async def _model_warnings(settings) -> list[str]:
    """Flag configured models OpenRouter doesn't serve, or that can't call tools.

    Best-effort and non-fatal: an unreachable catalogue produces no warnings rather
    than false ones, since "not in an empty list" says nothing about a model.
    """
    models = await run_in_threadpool(list_models)
    if not models:
        return []
    by_id = {m.id: m for m in models}
    warnings: list[str] = []
    for definition in AGENTS:
        model_id, _ = resolve_model_id(settings, definition.id)
        model = by_id.get(model_id)
        if model is None:
            warnings.append(f"{definition.name}: OpenRouter doesn't list “{model_id}”.")
        elif not model.supports_tools:
            warnings.append(
                f"{definition.name}: “{model.name}” doesn't support tool calling, "
                "which this agent needs."
            )
    return warnings


__all__ = ["router"]
