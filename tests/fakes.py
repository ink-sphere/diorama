"""Shared fakes for agent tests.

Everything here mimics the small slice of litellm / :class:`LiteLLMModel` the agent
depends on (``acompletion``, ``record_usage``, ``cumulative``), so the whole suite
runs offline with no API keys.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any


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
    """

    def __init__(self, responses: list[Any], *, loop_last: bool = False) -> None:
        self._responses = list(responses)
        self._loop_last = loop_last
        self.calls: list[dict] = []
        self.cumulative: dict[str, float] = {"cost_usd": 0.0, "total_tokens": 0.0}

    async def acompletion(self, messages, tools=None, stream: bool = False):
        self.calls.append(
            {"messages": list(messages), "tools": tools, "stream": stream}
        )
        if not self._responses:
            raise AssertionError("FakeModel ran out of scripted responses")
        if len(self._responses) == 1 and self._loop_last:
            return self._responses[0]
        return self._responses.pop(0)

    def record_usage(self, usage) -> dict:
        self.cumulative["cost_usd"] += 0.001
        self.cumulative["total_tokens"] += 2
        return {"total_tokens": 2, "cost_usd": 0.001}


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
