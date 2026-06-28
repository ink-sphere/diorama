"""litellm-backed model wrapper.

Diorama talks to every model through litellm (OpenRouter by default), per the
project's locked architecture. This class is intentionally thin: it issues the
``acompletion`` call and keeps cumulative token/cost accounting.
"""

from __future__ import annotations

from typing import Any

import litellm
import weave
from pydantic import BaseModel, Field

from diorama.models.pricing import PricingTable
from diorama.models.prompt_cache import apply_prompt_caching, extract_cache_tokens


def _cost_model_candidates(model_id: str) -> list[str]:
    """Model-id forms to try for pricing. litellm doesn't price the ``openrouter/``
    prefix, but does price the underlying ``openai/gpt-4o-mini`` / ``gpt-4o-mini``."""
    candidates = [model_id]
    if model_id.startswith("openrouter/"):
        candidates.append(model_id[len("openrouter/") :])
    if "/" in model_id:
        candidates.append(model_id.rsplit("/", 1)[1])
    # de-dupe, preserve order
    seen: set[str] = set()
    return [c for c in candidates if not (c in seen or seen.add(c))]


def _extract_token_counts(usage: Any) -> tuple[int, int]:
    """Return (prompt_tokens, completion_tokens) from a litellm usage object/dict."""
    if usage is None:
        return 0, 0
    if isinstance(usage, dict):
        return int(usage.get("prompt_tokens") or 0), int(
            usage.get("completion_tokens") or 0
        )
    return int(getattr(usage, "prompt_tokens", 0) or 0), int(
        getattr(usage, "completion_tokens", 0) or 0
    )


def _u(usage: Any, key: str) -> Any:
    """Extract a field from a usage object or dict, returning None if absent.

    Args:
        usage (Any): A litellm usage object, dict, or None.
        key (str): The attribute or key name to retrieve.

    Returns:
        Any: The field value, or None if usage is None or the key is missing.
    """
    if usage is None:
        return None
    if isinstance(usage, dict):
        return usage.get(key)
    return getattr(usage, key, None)


def _extract_reasoning_tokens(usage: Any) -> int:
    """Reasoning/thinking tokens, if the provider reports them (else 0)."""
    details = _u(usage, "completion_tokens_details")
    val = _u(details, "reasoning_tokens")
    return int(val or 0)


def _extract_actual_cost(usage: Any) -> float | None:
    """OpenRouter's real per-request USD cost when usage accounting is on, else None."""
    for key in ("cost", "total_cost"):
        val = _u(usage, key)
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                continue
    return None


class LiteLLMModel(BaseModel):
    """Async chat-completion wrapper with cumulative usage/cost tracking.

    Thin wrapper around litellm's ``acompletion`` that adds per-call token/cost
    accounting and Anthropic prompt-cache breakpoint injection. All mutable state
    is confined to the ``cumulative`` dict so the model object is safe to share
    across sub-agents.

    Attributes:
        model_id (str): The litellm model identifier (e.g. ``openrouter/openai/gpt-4o-mini``).
        temperature (float): Sampling temperature passed to the provider. Defaults to 0.7.
        max_tokens (int | None): Maximum completion tokens; None lets the provider decide.
        api_base (str | None): Override the provider base URL (e.g. for local inference).
        timeout (int): Request timeout in seconds. Defaults to 600.
        enable_prompt_caching (bool): Whether to inject Anthropic cache breakpoints. Defaults to True.
        cumulative (dict[str, float]): Accumulated token and cost counters across all calls.
    """

    model_id: str
    temperature: float = 0.7
    max_tokens: int | None = None
    api_base: str | None = None
    timeout: int = 600
    enable_prompt_caching: bool = True

    cumulative: dict[str, float] = Field(
        default_factory=lambda: {
            "input_tokens": 0.0,
            "output_tokens": 0.0,
            "total_tokens": 0.0,
            "cost_usd": 0.0,
            "cache_read_tokens": 0.0,
            "cache_write_tokens": 0.0,
            "reasoning_tokens": 0.0,
        }
    )

    @weave.op
    async def acompletion(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
    ) -> Any:
        """Issue a (possibly streaming) chat completion via litellm.

        Applies Anthropic prompt-cache breakpoints before sending (a no-op for
        non-Anthropic providers). For OpenRouter models, requests real per-request
        cost accounting via ``extra_body``.

        Args:
            messages (list[dict[str, Any]]): The conversation history in OpenAI
                Chat Completions format.
            tools (list[dict[str, Any]] | None): Tool schemas in OpenAI function-calling
                format. Defaults to None.
            stream (bool): Whether to request a streaming response. Defaults to False.

        Returns:
            Any: A litellm ``ModelResponse`` (non-streaming) or async generator
                (streaming).
        """
        # Mark cache breakpoints for Anthropic models (no-op otherwise). Operates
        # on copies, so persisted history / the tool router are never mutated.
        messages, tools = apply_prompt_caching(
            messages, tools, self.model_id, enabled=self.enable_prompt_caching
        )
        kwargs: dict[str, Any] = {
            "model": self.model_id,
            "messages": messages,
            "temperature": self.temperature,
            "timeout": self.timeout,
            "stream": stream,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        if self.max_tokens is not None:
            kwargs["max_tokens"] = self.max_tokens
        if self.api_base:
            kwargs["api_base"] = self.api_base
        if stream:
            kwargs["stream_options"] = {"include_usage": True}
        # Ask OpenRouter to include the real per-request cost in usage so we can
        # reconcile our pricing-table estimate against ground truth (no-op for
        # providers that ignore it).
        if "openrouter/" in self.model_id:
            kwargs["extra_body"] = {"usage": {"include": True}}
        return await litellm.acompletion(**kwargs)

    def cost_for(self, prompt_tokens: int, completion_tokens: int) -> float:
        """Best-effort USD cost for a call; returns 0.0 if litellm can't price it.

        Tries the model id and provider-stripped fallbacks so OpenRouter-prefixed
        models (which litellm doesn't price directly) still report real spend.
        """
        for model in _cost_model_candidates(self.model_id):
            try:
                prompt_cost, completion_cost = litellm.cost_per_token(
                    model=model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                )
            except Exception:
                continue
            total = float(prompt_cost or 0.0) + float(completion_cost or 0.0)
            if total:
                return total
        return 0.0

    def _price_call(
        self,
        prompt_tokens: int,
        completion_tokens: int,
        cache_read: int,
        cache_write: int,
        reasoning: int,
    ) -> tuple[float, dict[str, float]]:
        """Estimate (total_cost, cost_by_type) for one call.

        Prefers the live OpenRouter pricing table (per token type); falls back to
        litellm's flat prompt/completion pricing when the table isn't available.

        Args:
            prompt_tokens (int): Number of non-cached input tokens.
            completion_tokens (int): Number of generated output tokens.
            cache_read (int): Number of cache-read tokens (billed at the cheaper rate).
            cache_write (int): Number of cache-creation tokens.
            reasoning (int): Number of reasoning/thinking tokens.

        Returns:
            tuple[float, dict[str, float]]: A ``(total_cost, cost_by_type)`` pair
                where ``cost_by_type`` maps token-type labels to their USD cost.
        """
        pricing = PricingTable.instance().get(self.model_id)
        if pricing is not None:
            breakdown = pricing.cost_breakdown(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cache_read_tokens=cache_read,
                cache_write_tokens=cache_write,
                reasoning_tokens=reasoning,
            )
            return sum(breakdown.values()), breakdown
        # Fallback: litellm flat pricing, attributed to prompt/completion.
        flat = self.cost_for(prompt_tokens, completion_tokens)
        return flat, {"prompt": 0.0, "completion": flat, "request": 0.0}

    def record_usage(self, usage: Any) -> dict[str, float]:
        """Fold one call's usage into the cumulative totals; return that call's slice.

        Computes a per-token-type estimate from the OpenRouter pricing table and,
        when OpenRouter reports the real cost, reconciles the breakdown to it (the
        components are scaled so they sum to the actual charge). ``cost_usd`` is
        the authoritative figure (reconciled where known, else estimated).
        """
        prompt_tokens, completion_tokens = _extract_token_counts(usage)
        cache_read, cache_write = extract_cache_tokens(usage)
        reasoning = _extract_reasoning_tokens(usage)
        actual_cost = _extract_actual_cost(usage)

        estimated, cost_by_type = self._price_call(
            prompt_tokens, completion_tokens, cache_read, cache_write, reasoning
        )

        # Reconcile: scale the per-type breakdown so it sums to OpenRouter's actual.
        if actual_cost is not None and estimated > 0:
            factor = actual_cost / estimated
            cost_by_type = {k: v * factor for k, v in cost_by_type.items()}
        elif actual_cost is not None and estimated == 0:
            cost_by_type = {**cost_by_type, "completion": actual_cost}

        cost = actual_cost if actual_cost is not None else estimated

        self.cumulative["input_tokens"] += prompt_tokens
        self.cumulative["output_tokens"] += completion_tokens
        self.cumulative["total_tokens"] += prompt_tokens + completion_tokens
        self.cumulative["cost_usd"] += cost
        self.cumulative["cache_read_tokens"] += cache_read
        self.cumulative["cache_write_tokens"] += cache_write
        self.cumulative["reasoning_tokens"] += reasoning
        return {
            "input_tokens": prompt_tokens,
            "output_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "cost_usd": cost,
            "estimated_cost_usd": estimated,
            "actual_cost_usd": actual_cost,
            "cache_read_tokens": cache_read,
            "cache_write_tokens": cache_write,
            "reasoning_tokens": reasoning,
            "cost_by_type": cost_by_type,
        }
