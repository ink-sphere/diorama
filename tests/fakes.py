"""Shared fakes for agent tests.

Everything here mimics the small slice of litellm / :class:`LiteLLMModel` the agent
depends on (``acompletion``, ``record_usage``, ``record_failure``, ``cumulative``,
``usage_sink``), so the whole suite runs offline with no API keys.

The usage side is modelled rather than stubbed out: :meth:`FakeModel.record_usage`
emits a real :class:`~diorama.models.usage.LLMCallRecord` to whatever sink is
installed, at a fixed synthetic price. That lets the cost-ledger tests drive the
actual agent loop and assert on what it recorded, instead of asserting on a
hand-built record the loop never produced.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from diorama.models.usage import LLMCallRecord, split_model_id, utc_now_iso


def tool_call(call_id: str, name: str, arguments: str) -> SimpleNamespace:
    """Build a non-streaming tool-call object shaped like litellm's."""
    return SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def response(
    content: str | None = None,
    tool_calls: list | None = None,
    finish_reason: str | None = None,
    reasoning_content: str | None = None,
    thinking_blocks: list | None = None,
) -> SimpleNamespace:
    """Build a non-streaming litellm-style ``ModelResponse`` stand-in."""
    message = SimpleNamespace(
        content=content,
        tool_calls=tool_calls,
        reasoning_content=reasoning_content,
        thinking_blocks=thinking_blocks,
    )
    finish = finish_reason or ("tool_calls" if tool_calls else "stop")
    choice = SimpleNamespace(message=message, finish_reason=finish)
    return SimpleNamespace(
        choices=[choice], usage={"prompt_tokens": 1, "completion_tokens": 1}
    )


def chunk(
    content: str | None = None,
    finish_reason: str | None = None,
    usage: Any = None,
    reasoning_content: str | None = None,
    thinking_blocks: list | None = None,
) -> SimpleNamespace:
    """Build a streaming chunk stand-in carrying a text or reasoning delta."""
    delta = SimpleNamespace(
        content=content,
        tool_calls=None,
        reasoning_content=reasoning_content,
        thinking_blocks=thinking_blocks,
    )
    choice = SimpleNamespace(delta=delta, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], usage=usage)


def stream_of(chunks: list[SimpleNamespace], *, fail_after: int | None = None):
    """Return a factory producing an async generator over ``chunks``.

    When ``fail_after`` is set, the generator raises a transient-looking error after
    yielding that many chunks — used to exercise mid-stream failure handling.
    """

    def factory():
        async def gen():
            for index, item in enumerate(chunks):
                if fail_after is not None and index >= fail_after:
                    raise TimeoutError("stream died")
                yield item
            if fail_after is not None and fail_after >= len(chunks):
                raise TimeoutError("stream died")

        return gen()

    return factory


class FakeModel:
    """Drop-in stand-in for ``LiteLLMModel`` driven by a scripted response list.

    Each item in ``responses`` is returned (in order) from successive ``acompletion``
    calls. If ``loop_last`` is True the final item is reused for any extra calls (handy
    for exercising the max-iteration guard).

    Every call is priced at a flat $0.001 (1 prompt + 1 completion token), so a test can
    assert on exact totals without a rate table or a network round trip.
    """

    #: What one scripted call "costs", in USD.
    CALL_COST_USD = 0.001

    def __init__(
        self,
        responses: list[Any],
        *,
        loop_last: bool = False,
        model_id: str = "openrouter/openai/gpt-4o-mini",
        usage_sink: Any = None,
        usage_labels: dict[str, Any] | None = None,
    ) -> None:
        self._responses = list(responses)
        self._loop_last = loop_last
        self.calls: list[dict] = []
        self.cumulative: dict[str, float] = {"cost_usd": 0.0, "total_tokens": 0.0}
        self.model_id = model_id
        self.usage_sink = usage_sink
        self.usage_labels: dict[str, Any] = dict(usage_labels or {})

    async def acompletion(self, messages, tools=None, stream: bool = False):
        self.calls.append(
            {"messages": list(messages), "tools": tools, "stream": stream}
        )
        if not self._responses:
            raise AssertionError("FakeModel ran out of scripted responses")
        if len(self._responses) == 1 and self._loop_last:
            return self._responses[0]
        return self._responses.pop(0)

    def record_usage(self, usage, *, response=None, context=None) -> dict:
        self.cumulative["cost_usd"] += self.CALL_COST_USD
        self.cumulative["total_tokens"] += 2
        ctx = dict(context or {})
        self._emit(
            ctx,
            status=ctx.get("status", "ok"),
            prompt_tokens=1,
            completion_tokens=1,
            total_tokens=2,
            cost_usd=self.CALL_COST_USD,
            estimated_cost_usd=self.CALL_COST_USD,
            cost_by_type={"prompt": 0.0004, "completion": 0.0006},
            pricing_source="openrouter_live",
            finish_reason=ctx.get("finish_reason"),
        )
        return {"total_tokens": 2, "cost_usd": self.CALL_COST_USD}

    def record_failure(self, error, *, context=None) -> None:
        ctx = dict(context or {})
        self._emit(ctx, status=ctx.get("status", "error"), error=str(error))

    def _emit(self, ctx: dict, **fields: Any) -> None:
        """Build and deliver one ledger row, mirroring ``LiteLLMModel._emit``."""
        if self.usage_sink is None:
            return
        _, vendor, bare = split_model_id(self.model_id)
        self.usage_sink(
            LLMCallRecord(
                run_id=self.usage_labels.get("run_id"),
                book_id=self.usage_labels.get("book_id"),
                agent_id=self.usage_labels.get("agent_id"),
                model_id=self.model_id,
                model=bare,
                route=self.model_id.split("/")[0] or "unknown",
                provider=ctx.get("provider") or vendor or None,
                kind=ctx.get("kind", "turn"),
                turn=ctx.get("turn"),
                attempt=int(ctx.get("attempt", 1)),
                started_at=ctx.get("started_at") or utc_now_iso(),
                duration_ms=ctx.get("duration_ms"),
                streamed=bool(ctx.get("streamed", False)),
                **fields,
            )
        )


class StreamModel(FakeModel):
    """A model whose ``acompletion`` always returns a streaming generator."""

    async def acompletion(self, messages, tools=None, stream: bool = False):
        self.calls.append({"stream": stream})
        assert stream is True

        async def gen():
            yield chunk(content="Hel")
            yield chunk(content="lo")
            yield chunk(finish_reason="stop", usage={"prompt_tokens": 1})

        return gen()


class ScriptedStreamModel(FakeModel):
    """Returns a streaming generator per scripted entry.

    Each entry is a factory (see :func:`stream_of`) so a retried call gets a fresh
    generator rather than an exhausted one.
    """

    async def acompletion(self, messages, tools=None, stream: bool = False):
        self.calls.append(
            {"messages": list(messages), "tools": tools, "stream": stream}
        )
        if not self._responses:
            raise AssertionError("ScriptedStreamModel ran out of scripted streams")
        factory = (
            self._responses[0]
            if (len(self._responses) == 1 and self._loop_last)
            else self._responses.pop(0)
        )
        return factory()


class FlakyModel(FakeModel):
    """Raises ``error`` for the first ``failures`` calls, then behaves normally."""

    def __init__(
        self, responses: list[Any], *, failures: int, error: Exception
    ) -> None:
        super().__init__(responses)
        self.failures = failures
        self.error = error
        self.attempts = 0

    async def acompletion(self, messages, tools=None, stream: bool = False):
        self.attempts += 1
        if self.attempts <= self.failures:
            raise self.error
        return await super().acompletion(messages, tools=tools, stream=stream)
