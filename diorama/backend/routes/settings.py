"""Settings endpoints: which model each agent runs, and the provider credentials.

The GET/PUT pair is deliberately asymmetric. GET returns a *view* in which every API
key is masked, so a stored key can never be read back out of the API; PUT takes a
*partial* update, so the form can send everything it rendered without needing the
real keys to round-trip. See :mod:`diorama.backend.settings`.

The catalogue and connection-test endpoints are both **per provider**, because the
two providers answer different questions and answer them differently: OpenRouter's
model list is public and its key endpoint reports a balance, while Google's list is
itself the authentication check and reports no billing at all. Rather than flatten
that into a lowest-common-denominator response, each endpoint says which provider it
is talking about and the UI renders one control per provider.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from diorama.backend.settings import (
    AGENTS,
    ConnectionTest,
    DioramaSettings,
    Provider,
    SettingsUpdate,
    SettingsView,
    build_view,
    load_settings,
    resolve_api_key,
    resolve_model_id,
    save_settings,
)
from diorama.models.catalogue import ModelInfo, list_google_models, list_models
from diorama.models.providers import (
    GOOGLE,
    OPENROUTER,
    PROVIDERS,
    PROVIDERS_BY_ID,
    provider_id_for_model,
)

router = APIRouter(prefix="/api/settings", tags=["settings"])

_OPENROUTER_KEY_URL = "https://openrouter.ai/api/v1/key"
_GOOGLE_MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"
_TEST_TIMEOUT = 15.0


class CatalogueEntry(BaseModel):
    """One selectable model, as the picker renders it."""

    id: str
    provider: str
    provider_model_id: str
    name: str
    vendor: str
    context_length: int | None = None
    prompt_price: float = 0.0
    completion_price: float = 0.0
    pricing_known: bool = True
    supports_tools: bool = False


class CatalogueStatus(BaseModel):
    """Why one provider's section of the picker is (or isn't) populated.

    ``needs_key`` is the difference between "we couldn't reach them" and "you haven't
    connected this provider" — only the second is something the user can fix here, and
    conflating them would send someone to check their network over a missing key.
    """

    provider: str
    name: str
    available: bool
    needs_key: bool
    count: int


class ModelCatalogue(BaseModel):
    """The picker's options, plus per-provider notes on what's missing and why."""

    models: list[CatalogueEntry] = Field(default_factory=list)
    available: bool = True
    providers: list[CatalogueStatus] = Field(default_factory=list)


class TestRequest(BaseModel):
    """Test one provider's credential, optionally one typed but not yet saved."""

    provider: Provider = OPENROUTER
    api_key: str | None = None


def _entry(model: ModelInfo) -> CatalogueEntry:
    return CatalogueEntry(**model.__dict__)


@router.get("")
async def get_settings() -> SettingsView:
    return build_view(await load_settings())


@router.put("")
async def put_settings(update: SettingsUpdate) -> SettingsView:
    unknown_agents = set(update.agents or {}) - {a.id for a in AGENTS}
    if unknown_agents:
        raise HTTPException(
            400, f"Unknown agent(s): {', '.join(sorted(unknown_agents))}."
        )
    unknown_providers = set(update.api_keys or {}) - set(PROVIDERS_BY_ID)
    if unknown_providers:
        raise HTTPException(
            400, f"Unknown provider(s): {', '.join(sorted(unknown_providers))}."
        )
    return build_view(await save_settings(update))


async def _catalogue(
    provider_id: str, api_key: str | None, *, refresh: bool
) -> list[ModelInfo]:
    """One provider's models, or nothing at all when it isn't connected.

    **A provider with no key contributes no models**, whether or not its catalogue
    happens to be readable without one. OpenRouter's list is public, so this is a
    policy rather than a limitation — and the right one: a model Diorama cannot
    authenticate is a model that fails halfway through the first book. Offering it
    would make the picker a list of everything that exists rather than a list of what
    this install can actually run.
    """
    if not api_key:
        return []
    if provider_id == GOOGLE:
        return await run_in_threadpool(list_google_models, api_key, force=refresh)
    return await run_in_threadpool(list_models, force=refresh)


@router.get("/models")
async def get_models(refresh: bool = False) -> ModelCatalogue:
    """Every model the *connected* providers can serve, for the picker.

    Blocking HTTP behind 24h disk caches, so each fetch runs in a threadpool. An
    empty list is a normal outcome, not an error — no keys yet on a fresh install, or
    offline — and the picker degrades to free-text entry rather than failing the page.
    """
    settings = await load_settings()
    keys = {p.id: resolve_api_key(settings, p.id)[0] for p in PROVIDERS}
    by_provider = {
        p.id: await _catalogue(p.id, keys[p.id], refresh=refresh) for p in PROVIDERS
    }

    models = [m for provider in PROVIDERS for m in by_provider[provider.id]]
    return ModelCatalogue(
        models=[_entry(m) for m in models],
        available=bool(models),
        providers=[
            CatalogueStatus(
                provider=provider.id,
                name=provider.name,
                available=bool(by_provider[provider.id]),
                needs_key=keys[provider.id] is None,
                count=len(by_provider[provider.id]),
            )
            for provider in PROVIDERS
        ],
    )


@router.post("/test")
async def test_connection(body: TestRequest | None = None) -> ConnectionTest:
    """Check one provider's API key, and sanity-check the models pointed at it.

    Uses the key in the request body when present (so the user can validate what they
    just typed before saving) and the resolved stored/env key otherwise.
    """
    request = body or TestRequest()
    provider_id = request.provider
    provider = PROVIDERS_BY_ID[provider_id]

    settings = await load_settings()
    typed = (request.api_key or "").strip()
    api_key = typed or resolve_api_key(settings, provider_id)[0]
    if not api_key:
        return ConnectionTest(
            ok=False,
            provider=provider_id,
            message=f"No {provider.name} key is set. Add one above, then test again.",
        )

    if provider_id == GOOGLE:
        return await _test_google(settings, api_key)
    return await _test_openrouter(settings, api_key)


async def _test_openrouter(settings: DioramaSettings, api_key: str) -> ConnectionTest:
    """OpenRouter's key endpoint, which also reports spend against the key."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=_TEST_TIMEOUT) as http:
            response = await http.get(
                _OPENROUTER_KEY_URL, headers={"Authorization": f"Bearer {api_key}"}
            )
    except Exception:  # noqa: BLE001 — offline is the common case here
        return ConnectionTest(
            ok=False,
            provider=OPENROUTER,
            message="Couldn't reach OpenRouter. Check your connection.",
        )

    if response.status_code in (401, 403):
        return ConnectionTest(
            ok=False, provider=OPENROUTER, message="OpenRouter rejected that API key."
        )
    if response.status_code >= 400:
        return ConnectionTest(
            ok=False,
            provider=OPENROUTER,
            message=f"OpenRouter returned {response.status_code}. Try again shortly.",
        )

    try:
        data = response.json().get("data") or {}
    except Exception:  # noqa: BLE001
        data = {}

    catalogue = await run_in_threadpool(list_models)
    return ConnectionTest(
        ok=True,
        provider=OPENROUTER,
        message="Key accepted by OpenRouter.",
        label=data.get("label") or None,
        usage_usd=_maybe_float(data.get("usage")),
        limit_usd=_maybe_float(data.get("limit")),
        is_free_tier=data.get("is_free_tier"),
        warnings=_model_warnings(settings, OPENROUTER, catalogue),
    )


async def _test_google(settings: DioramaSettings, api_key: str) -> ConnectionTest:
    """Google has no key-info endpoint, so listing models *is* the check.

    That is not a workaround — the list is the only thing the app needs the key for
    besides generating, and a key that can list is a key that can be used. It reports
    no balance or spend limit, so those fields stay empty rather than showing zeros
    that would read as "no spend yet".
    """
    import httpx

    try:
        async with httpx.AsyncClient(timeout=_TEST_TIMEOUT) as http:
            response = await http.get(
                _GOOGLE_MODELS_URL,
                params={"pageSize": 1},
                headers={"x-goog-api-key": api_key},
            )
    except Exception:  # noqa: BLE001
        return ConnectionTest(
            ok=False,
            provider=GOOGLE,
            message="Couldn't reach Google AI Studio. Check your connection.",
        )

    if response.status_code in (400, 401, 403):
        return ConnectionTest(
            ok=False,
            provider=GOOGLE,
            message="Google AI Studio rejected that API key.",
        )
    if response.status_code >= 400:
        return ConnectionTest(
            ok=False,
            provider=GOOGLE,
            message=(
                f"Google AI Studio returned {response.status_code}. Try again shortly."
            ),
        )

    catalogue = await run_in_threadpool(list_google_models, api_key)
    return ConnectionTest(
        ok=True,
        provider=GOOGLE,
        message="Key accepted by Google AI Studio.",
        warnings=_model_warnings(settings, GOOGLE, catalogue),
    )


def _maybe_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _model_warnings(
    settings: DioramaSettings, provider_id: str, models: list[ModelInfo]
) -> list[str]:
    """Flag models this provider doesn't serve, or that can't call tools.

    Scoped to the agents actually routed through ``provider_id`` — testing an
    OpenRouter key says nothing about an agent pointed at Gemini. Best-effort and
    non-fatal: an unreachable catalogue produces no warnings rather than false ones,
    since "not in an empty list" says nothing about a model.
    """
    if not models:
        return []
    provider = PROVIDERS_BY_ID[provider_id]
    by_id = {m.id: m for m in models}
    warnings: list[str] = []
    for definition in AGENTS:
        model_id, _ = resolve_model_id(settings, definition.id)
        if provider_id_for_model(model_id) != provider_id:
            continue
        model = by_id.get(model_id)
        if model is None:
            warnings.append(
                f"{definition.name}: {provider.name} doesn't list “{model_id}”."
            )
        elif not model.supports_tools:
            warnings.append(
                f"{definition.name}: “{model.name}” doesn't support tool calling, "
                "which this agent needs."
            )
    return warnings


__all__ = ["router"]
