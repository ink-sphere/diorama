"""OpenRouter's model list, as the settings UI's model picker needs it.

Deliberately separate from :mod:`diorama.models.pricing`, which reads the same
``/api/v1/models`` endpoint: that module caches only the per-token *rates* keyed by
model id and drops everything else, because pricing a call is all it is for. A
picker needs the parts it throws away — display name, context length, description —
so this module keeps its own 24h disk cache of the fuller shape. Two caches rather
than one shared cache means a stale settings page can never invalidate live pricing,
and neither module has to grow a field for the other's benefit.

Everything here is best-effort: if the fetch fails, :func:`list_models` returns an
empty list and the settings UI falls back to free-text entry.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_MODELS_URL = "https://openrouter.ai/api/v1/models"
_CACHE_PATH = Path.home() / ".cache" / "diorama" / "openrouter_catalogue.json"
_DEFAULT_TTL_SECONDS = 24 * 60 * 60
_FETCH_TIMEOUT = 10.0

#: litellm addresses OpenRouter models as ``openrouter/<openrouter-id>``.
LITELLM_PREFIX = "openrouter/"


@dataclass(frozen=True)
class ModelInfo:
    """One entry in the picker.

    Attributes:
        id (str): The litellm model id (``openrouter/openai/gpt-4o``) — the value
            actually stored in settings and handed to the agent.
        openrouter_id (str): The bare OpenRouter id (``openai/gpt-4o``).
        name (str): OpenRouter's display name, e.g. "OpenAI: GPT-4o".
        vendor (str): The id's first path segment, used to group the picker.
        context_length (int | None): Context window in tokens, when reported.
        prompt_price (float): USD per prompt token.
        completion_price (float): USD per completion token.
        supports_tools (bool): Whether OpenRouter lists tool/function calling for
            this model. The ebook loader is useless without it, so the picker warns
            when a tool-less model is chosen.
    """

    id: str
    openrouter_id: str
    name: str
    vendor: str
    context_length: int | None
    prompt_price: float
    completion_price: float
    supports_tools: bool


def to_litellm_id(openrouter_id: str) -> str:
    """Prefix a bare OpenRouter id for litellm, leaving already-prefixed ids alone."""
    oid = (openrouter_id or "").strip()
    return oid if oid.startswith(LITELLM_PREFIX) else f"{LITELLM_PREFIX}{oid}"


def _f(value: object) -> float:
    try:
        return float(value)  # OpenRouter sends rates as strings
    except (TypeError, ValueError):
        return 0.0


def _parse_entry(entry: dict) -> ModelInfo | None:
    """Build a :class:`ModelInfo` from one raw ``/api/v1/models`` entry, or None."""
    openrouter_id = entry.get("id")
    if not isinstance(openrouter_id, str) or not openrouter_id:
        return None
    pricing = entry.get("pricing") if isinstance(entry.get("pricing"), dict) else {}
    context = entry.get("context_length")
    params = entry.get("supported_parameters")
    return ModelInfo(
        id=to_litellm_id(openrouter_id),
        openrouter_id=openrouter_id,
        name=entry.get("name") or openrouter_id,
        vendor=openrouter_id.split("/", 1)[0] if "/" in openrouter_id else "other",
        context_length=int(context) if isinstance(context, (int, float)) else None,
        prompt_price=_f(pricing.get("prompt")),
        completion_price=_f(pricing.get("completion")),
        # OpenRouter reports capability as the parameters a model accepts; "tools"
        # in that list is the closest thing to a function-calling flag it exposes.
        supports_tools=isinstance(params, list) and "tools" in params,
    )


def _read_cache(cache_path: Path, ttl: int) -> list[ModelInfo] | None:
    """Return cached models if the cache exists and is within ``ttl``, else None."""
    try:
        if not cache_path.exists():
            return None
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        if time.time() - float(data.get("fetched_at") or 0) > ttl:
            return None
        return [ModelInfo(**m) for m in data.get("models") or []]
    except Exception:  # noqa: BLE001 — a corrupt cache is a cache miss
        logger.warning("Could not read model catalogue cache", exc_info=True)
        return None


def _write_cache(cache_path: Path, models: list[ModelInfo]) -> None:
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(
                {"fetched_at": time.time(), "models": [asdict(m) for m in models]}
            ),
            encoding="utf-8",
        )
    except Exception:  # noqa: BLE001
        logger.warning("Could not write model catalogue cache", exc_info=True)


def list_models(
    *,
    force: bool = False,
    cache_path: Path = _CACHE_PATH,
    ttl_seconds: int = _DEFAULT_TTL_SECONDS,
) -> list[ModelInfo]:
    """Every model OpenRouter currently serves, newest cache first.

    Makes a blocking HTTP request on a cache miss, so call it from a thread
    (``run_in_threadpool``) rather than directly on the event loop.

    Args:
        force (bool): Skip the disk cache and re-fetch.
        cache_path (Path): Where the JSON disk cache lives.
        ttl_seconds (int): Seconds before the cache is considered stale.

    Returns:
        list[ModelInfo]: Models sorted by vendor then name; empty if the catalogue
            could not be fetched and no usable cache exists.
    """
    if not force:
        cached = _read_cache(cache_path, ttl_seconds)
        if cached:
            return cached

    try:
        import httpx

        response = httpx.get(_MODELS_URL, timeout=_FETCH_TIMEOUT)
        response.raise_for_status()
        raw = response.json().get("data") or []
    except Exception:  # noqa: BLE001 — offline is a normal state for a local tool
        logger.warning("Could not fetch the OpenRouter model catalogue", exc_info=True)
        # A stale cache still beats an empty picker.
        return _read_cache(cache_path, ttl=2**31) or []

    models = [m for m in (_parse_entry(e) for e in raw if isinstance(e, dict)) if m]
    if not models:
        return _read_cache(cache_path, ttl=2**31) or []

    models.sort(key=lambda m: (m.vendor.lower(), m.name.lower()))
    _write_cache(cache_path, models)
    return models


__all__ = ["LITELLM_PREFIX", "ModelInfo", "list_models", "to_litellm_id"]
