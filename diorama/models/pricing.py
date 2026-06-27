"""OpenRouter dynamic pricing: fetch, cache, and compute per-token-type cost.

OpenRouter exposes live per-model pricing at `/api/v1/models` (USD per token for
`prompt`/`completion`, per request for `request`, plus cache read/write and
reasoning rates). litellm's static `cost_per_token` often can't price an
`openrouter/<vendor>/<model>` id and ignores the cheaper cache-read / pricier
cache-write rates, so we fetch the live table ourselves and price each token
type at its own rate.

The table is fetched once and cached to disk with a TTL (default 24h), so normal
runs pay no network cost. Everything here is best-effort: if the table can't be
loaded, :func:`PricingTable.get` returns `None` and callers fall back to
litellm. Pure in-memory lookups never block the async hot path; warm the table
explicitly at startup with :meth:`PricingTable.warm`.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_MODELS_URL = "https://openrouter.ai/api/v1/models"
_CACHE_PATH = Path.home() / ".cache" / "diorama" / "openrouter_pricing.json"
_DEFAULT_TTL_SECONDS = 24 * 60 * 60
_FETCH_TIMEOUT = 10.0


@dataclass(frozen=True)
class ModelPricing:
    """Per-unit USD pricing for one model (0.0 for any rate OpenRouter omits).

    Attributes:
        prompt (float): Cost per prompt token in USD.
        completion (float): Cost per completion token in USD.
        request (float): Fixed cost per request in USD.
        cache_read (float): Cost per cache-read input token in USD.
        cache_write (float): Cost per cache-creation (write) token in USD.
        reasoning (float): Cost per reasoning/thinking token in USD.
    """

    prompt: float = 0.0  # $/prompt token
    completion: float = 0.0  # $/completion token
    request: float = 0.0  # $/request
    cache_read: float = 0.0  # $/cached input token (read)
    cache_write: float = 0.0  # $/cache-creation token (write)
    reasoning: float = 0.0  # $/reasoning token

    def cost_breakdown(
        self,
        *,
        prompt_tokens: int,
        completion_tokens: int,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        reasoning_tokens: int = 0,
    ) -> dict[str, float]:
        """Estimated USD cost per token type for one call.

        `prompt` is the *non-cached* prompt portion: cache-read tokens are
        treated as a subset of the prompt and billed at the cheaper read rate
        (the common OpenAI/OpenRouter shape). Cache-write and reasoning are
        billed additively. When the caller reconciles against OpenRouter's
        actual cost, any provider-shape skew in these proportions is corrected.
        """
        billable_prompt = max(0, prompt_tokens - cache_read_tokens)
        return {
            "prompt": billable_prompt * self.prompt,
            "cache_read": cache_read_tokens * self.cache_read,
            "cache_write": cache_write_tokens * self.cache_write,
            "completion": completion_tokens * self.completion,
            "reasoning": reasoning_tokens * self.reasoning,
            "request": self.request,
        }


def _f(value: object) -> float:
    """Coerce an OpenRouter pricing value to float, returning 0.0 on failure.

    Args:
        value (object): A raw pricing rate value (may be a string, int, float, or None).

    Returns:
        float: The numeric rate, or 0.0 if conversion fails.
    """
    try:
        return float(value)  # OpenRouter sends rates as strings
    except (TypeError, ValueError):
        return 0.0


def _parse_pricing(pricing: dict) -> ModelPricing:
    """Build a :class:`ModelPricing` from a raw OpenRouter pricing dict.

    Args:
        pricing (dict): The `pricing` object from an OpenRouter `/api/v1/models` entry.

    Returns:
        ModelPricing: The parsed per-token-type pricing for the model.
    """
    return ModelPricing(
        prompt=_f(pricing.get("prompt")),
        completion=_f(pricing.get("completion")),
        request=_f(pricing.get("request")),
        cache_read=_f(pricing.get("input_cache_read")),
        cache_write=_f(pricing.get("input_cache_write")),
        reasoning=_f(pricing.get("internal_reasoning")),
    )


def normalize_model_id(model_id: str) -> str:
    """Map a litellm id to its OpenRouter id (strip the `openrouter/` prefix)."""
    mid = model_id or ""
    if mid.startswith("openrouter/"):
        return mid[len("openrouter/") :]
    return mid


class PricingTable:
    """Singleton cache of OpenRouter model pricing (disk-backed, TTL-refreshed).

    Fetches per-model pricing from the OpenRouter `/api/v1/models` endpoint and
    caches the result to disk. All lookups after warming are pure in-memory and
    never block the async event loop. Falls back gracefully to litellm pricing when
    the table is unavailable.
    """

    _instance: "PricingTable | None" = None

    def __init__(
        self, cache_path: Path = _CACHE_PATH, ttl_seconds: int = _DEFAULT_TTL_SECONDS
    ):
        """Initialise an empty (not yet loaded) pricing table.

        Args:
            cache_path (Path): Filesystem path for the JSON disk cache.
                Defaults to `~/.cache/diorama/openrouter_pricing.json`.
            ttl_seconds (int): Number of seconds before the disk cache is
                considered stale and re-fetched. Defaults to 86400 (24 hours).
        """
        self._models: dict[str, ModelPricing] = {}
        self._loaded = False
        self._cache_path = cache_path
        self._ttl = ttl_seconds

    @classmethod
    def instance(cls) -> "PricingTable":
        """Return the process-wide singleton, creating it on first call.

        Returns:
            PricingTable: The shared singleton instance.
        """
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # -- lookup (pure, never blocks) -----------------------------------
    def get(self, model_id: str) -> ModelPricing | None:
        """Return pricing for `model_id` if the table is warmed, else `None`."""
        if not self._loaded:
            # Cheap, non-network: try the disk cache once.
            self._load_from_disk()
        return self._models.get(normalize_model_id(model_id))

    # -- warming (may hit network / disk) ------------------------------
    def warm(self, model_ids: list[str] | None = None, *, force: bool = False) -> bool:
        """Ensure the table is loaded: fresh disk cache, else fetch from OpenRouter.

        Returns True if any pricing is available afterward. Best-effort, never
        raises. `model_ids` is advisory (used only for a debug log).
        """
        if self._loaded and not force:
            return bool(self._models)
        if not force and self._load_from_disk():
            return True
        return self._fetch_and_cache()

    def _load_from_disk(self) -> bool:
        """Attempt to populate the table from the on-disk JSON cache.

        Sets `_loaded` regardless of success so the disk is not hit on every
        `get()` call. Returns False when the cache is absent, stale, or corrupt.

        Returns:
            bool: True if at least one model's pricing was loaded, False otherwise.
        """
        self._loaded = True  # don't retry disk on every get()
        try:
            if not self._cache_path.exists():
                return False
            data = json.loads(self._cache_path.read_text(encoding="utf-8"))
            fetched_at = float(data.get("fetched_at") or 0)
            if time.time() - fetched_at > self._ttl:
                return False  # stale → caller may fetch
            models = data.get("models") or {}
            self._models = {
                mid: _parse_pricing(p)
                for mid, p in models.items()
                if isinstance(p, dict)
            }
            return bool(self._models)
        except Exception:  # noqa: BLE001
            logger.warning("Could not read pricing cache", exc_info=True)
            return False

    def _fetch_and_cache(self) -> bool:
        """Fetch live pricing from the OpenRouter API and write the disk cache.

        Makes a synchronous HTTP request (intended to be called from a thread via
        `asyncio.to_thread`). Silently returns False if the network is unavailable
        or the response is malformed.

        Returns:
            bool: True if at least one model's pricing was fetched and stored.
        """
        try:
            import httpx

            resp = httpx.get(_MODELS_URL, timeout=_FETCH_TIMEOUT)
            resp.raise_for_status()
            raw = resp.json().get("data") or []
        except Exception:  # noqa: BLE001
            logger.warning("Could not fetch OpenRouter pricing", exc_info=True)
            return False

        models_raw: dict[str, dict] = {}
        parsed: dict[str, ModelPricing] = {}
        for entry in raw:
            mid = entry.get("id")
            pricing = entry.get("pricing")
            if mid and isinstance(pricing, dict):
                models_raw[mid] = pricing
                parsed[mid] = _parse_pricing(pricing)
        if not parsed:
            return False

        self._models = parsed
        self._loaded = True
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._cache_path.write_text(
                json.dumps({"fetched_at": time.time(), "models": models_raw}),
                encoding="utf-8",
            )
        except Exception:  # noqa: BLE001
            logger.warning("Could not write pricing cache", exc_info=True)
        return True
