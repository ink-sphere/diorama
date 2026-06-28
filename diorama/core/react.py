"""A basic ReAct agent over diorama's :class:`LiteLLMModel`.

This is the trimmed-down sibling of diorama's actor-based agent: a single
``await agent.run(prompt)`` drives one native tool-calling loop. Per turn the agent
calls the model with the registered tool schemas (``tool_choice="auto"``), executes
any tool calls, feeds the results back, and repeats. **A turn ends when the model
replies with no tool calls** (faithful to diorama's loop) — ``final_answer`` is an
optional convenience tool, not a requirement.

Production features kept (per the basic-agent brief):

* **Max-iteration guard** — the loop is bounded by ``max_iterations`` (``-1`` =
  unbounded).
* **LLM-call retries** — transient errors (timeouts, 5xx, rate limits) are retried
  with backoff; other errors propagate.
* **Optional per-tool approval** — a tool with ``requires_approval=True`` pauses for
  confirmation unless approval is auto-granted (``yolo_mode`` / ``auto_approve``) or
  resolved by an ``approval_callback``.

Streaming: ``run(..., stream=True)`` prints assistant text deltas and tool activity
to a Rich console as the agent works; the default non-streaming path just returns
the result dict.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import BaseModel

from diorama.core.prompts import SYSTEM_PROMPT
from diorama.core.router import ToolRouter
from diorama.core.tool import Tool
from diorama.models.litellm_model import LiteLLMModel

logger = logging.getLogger(__name__)

_MAX_LLM_RETRIES = 3
_RETRY_DELAYS = [5, 15, 30]
_RATE_LIMIT_DELAYS = [30, 60]


class ReactAgentResult(BaseModel):
    # ``final_answer`` is None when the loop stops without the model producing a
    # plain-text answer (e.g. the max-iterations guard fires mid-tool-call).
    final_answer: str | None = None
    completed: bool
    stop_reason: str
    steps: int
    messages: list
    usage: dict
    cost_usd: float


def _is_rate_limit_error(error: Exception) -> bool:
    """Return True when the error looks like an API rate-limit response."""
    s = str(error).lower()
    return any(
        p in s
        for p in ("429", "rate limit", "rate_limit", "too many requests", "throttl")
    )


def _is_transient_error(error: Exception) -> bool:
    """Return True when the error is transient and safe to retry (5xx/timeout/conn)."""
    s = str(error).lower()
    patterns = (
        "timeout",
        "timed out",
        "503",
        "service unavailable",
        "502",
        "bad gateway",
        "500",
        "internal server error",
        "overloaded",
        "capacity",
        "connection reset",
        "connection refused",
        "connection error",
        "eof",
        "broken pipe",
    )
    return _is_rate_limit_error(error) or any(p in s for p in patterns)


def _retry_delay_for(error: Exception, attempt: int) -> int | None:
    """Return the backoff delay (seconds) for an error/attempt, or None if not retryable.

    Args:
        error (Exception): The exception that triggered a potential retry.
        attempt (int): Zero-based attempt index (0 = first failure).

    Returns:
        int | None: Seconds to wait before retrying, or None if the error should not
            be retried (or the schedule is exhausted).
    """
    schedule = (
        _RATE_LIMIT_DELAYS
        if _is_rate_limit_error(error)
        else (_RETRY_DELAYS if _is_transient_error(error) else None)
    )
    if schedule is None or attempt >= len(schedule):
        return None
    return schedule[attempt]


# --------------------------------------------------------------------------- #
# Message helpers + normalised LLM result
# --------------------------------------------------------------------------- #
def _assistant_message(
    content: str | None, tool_calls: list[dict] | None
) -> dict[str, Any]:
    """Build an assistant message, attaching ``tool_calls`` only when present."""
    msg: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return msg


def _tool_message(content: str, tool_call_id: str, name: str) -> dict[str, Any]:
    """Build a ``role: tool`` result message for one executed tool call."""
    return {
        "role": "tool",
        "content": content,
        "tool_call_id": tool_call_id,
        "name": name,
    }


@dataclass
class LLMResult:
    """Normalised result of one LLM call (streaming or non-streaming).

    Attributes:
        content (str | None): Assistant text, or None if only tool calls were emitted.
        tool_calls_acc (dict[int, dict]): Tool calls keyed by their index, each with
            ``id``, ``type``, and ``function`` (``name`` + ``arguments``) sub-keys.
        finish_reason (str | None): Model's stop reason, or None if not reported.
        usage (dict): Per-call usage slice as returned by ``model.record_usage``.
    """

    content: str | None
    tool_calls_acc: dict[int, dict]
    finish_reason: str | None
    usage: dict = field(default_factory=dict)


def _short(text: Any, limit: int = 200) -> str:
    """Truncate a value's string form for compact console logging."""
    s = text if isinstance(text, str) else json.dumps(text, default=str)
    return s if len(s) <= limit else s[: limit - 1] + "…"


class ReactAgent:
    """A basic ReAct agent over diorama's async :class:`LiteLLMModel`.

    Attributes:
        model (LiteLLMModel): The LLM wrapper used for every completion.
        tool_router (ToolRouter): Registry/dispatcher for the agent's tools.
        system_prompt (str): The base system prompt (plus any ``instructions``).
        max_iterations (int): Turn ceiling for one ``run`` (``-1`` = unbounded).
        yolo_mode (bool): When True, tools requiring approval are auto-approved.
    """

    def __init__(
        self,
        tools: list[Tool],
        *,
        model: LiteLLMModel | None = None,
        model_id: str = "openrouter/openai/gpt-4o-mini",
        system_prompt: str = SYSTEM_PROMPT,
        instructions: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        max_iterations: int = 25,
        yolo_mode: bool = True,
        enable_prompt_caching: bool = True,
        approval_callback: Callable[[str, dict], bool] | None = None,
        weave_project: str | None = None,
    ) -> None:
        """Initialise the agent with its tool set and configuration.

        Args:
            tools (list[Tool]): The tools made available to the agent.
            model (LiteLLMModel | None): LLM wrapper to use. Built from ``model_id`` and
                the sampling args when omitted.
            model_id (str): litellm model id used when ``model`` is not supplied.
            system_prompt (str): Base system prompt. Defaults to ``SYSTEM_PROMPT``.
            instructions (str | None): Extra instructions appended to the system prompt.
            temperature (float): Sampling temperature (when building the model).
            max_tokens (int | None): Completion token cap (when building the model).
            max_iterations (int): Turn ceiling per ``run`` (``-1`` disables it).
            yolo_mode (bool): Auto-approve tools that declare ``requires_approval``.
            enable_prompt_caching (bool): Pass-through to the model wrapper.
            approval_callback (Callable[[str, dict], bool] | None): Called as
                ``(tool_name, arguments) -> bool`` to resolve approval when a tool
                requires it and auto-approval is off.
            weave_project (str | None): When set, initialise W&B Weave tracing.
        """
        self.model = model or LiteLLMModel(
            model_id=model_id,
            temperature=temperature,
            max_tokens=max_tokens,
            enable_prompt_caching=enable_prompt_caching,
        )
        self.tools = list(tools)
        self.tool_router = ToolRouter(self.tools)
        self.system_prompt = system_prompt + (
            f"\n\n{instructions}" if instructions else ""
        )
        self.max_iterations = max_iterations
        self.yolo_mode = yolo_mode
        self.approval_callback = approval_callback
        self._maybe_init_weave(weave_project)

    @staticmethod
    def _maybe_init_weave(weave_project: str | None) -> None:
        """Initialise W&B Weave tracing if a project name is given (best-effort)."""
        if not weave_project:
            return
        try:
            import weave

            weave.init(weave_project)
        except Exception as e:  # noqa: BLE001
            logger.warning("weave.init failed (continuing without tracing): %s", e)

    # ----------------------------------------------------------------------- #
    # Public API
    # ----------------------------------------------------------------------- #
    async def run(
        self,
        prompt: str,
        *,
        stream: bool = False,
        auto_approve: bool | None = None,
        console: Any = None,
    ) -> ReactAgentResult:
        """Run one task to completion and return a result dict.

        Args:
            prompt (str): The task/question for the agent.
            stream (bool): When True, print assistant text deltas and tool activity to
                a Rich console as the agent works. Defaults to False.
            auto_approve (bool | None): Override ``yolo_mode`` for this run. None uses
                the agent's ``yolo_mode``.
            console (Any): Optional Rich ``Console`` for streaming output. One is
                created when omitted and ``stream`` is True.

        Returns:
            ReactAgentResult: ``final_answer``, ``completed``, ``stop_reason``,
                ``steps``, ``messages``, ``usage`` (cumulative), and ``cost_usd``.
        """
        if stream and console is None:
            from rich.console import Console

            console = Console()

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": prompt},
        ]
        tool_specs = self.tool_router.get_tool_specs_for_llm() or None

        final_answer: str | None = None
        completed = False
        stop_reason = "max_iterations"
        iteration = 0

        while self.max_iterations == -1 or iteration < self.max_iterations:
            iteration += 1
            result = await self._call_llm(messages, tool_specs, stream, console)
            tool_calls = [
                result.tool_calls_acc[idx] for idx in sorted(result.tool_calls_acc)
            ]
            messages.append(_assistant_message(result.content, tool_calls or None))
            if stream and console is not None and result.content:
                console.print()  # end the streamed assistant line

            if not tool_calls:
                final_answer = result.content
                completed = True
                stop_reason = "completed"
                break

            await self._run_tool_calls(
                tool_calls, messages, auto_approve, stream, console
            )

        return ReactAgentResult(
            final_answer=final_answer,
            completed=completed,
            stop_reason=stop_reason,
            steps=iteration,
            messages=messages,
            usage=dict(self.model.cumulative),
            cost_usd=round(self.model.cumulative.get("cost_usd", 0.0), 6),
        )

    def run_sync(self, prompt: str, **kwargs: Any) -> ReactAgentResult:
        """Blocking convenience wrapper around :meth:`run`."""
        return asyncio.run(self.run(prompt, **kwargs))

    # ----------------------------------------------------------------------- #
    # Tool execution + approval
    # ----------------------------------------------------------------------- #
    async def _run_tool_calls(
        self,
        tool_calls: list[dict],
        messages: list[dict[str, Any]],
        auto_approve: bool | None,
        stream: bool,
        console: Any,
    ) -> None:
        """Execute each tool call in order and append its ``role: tool`` result."""
        for tc in tool_calls:
            name = tc["function"]["name"]
            tc_id = tc["id"]
            raw_args = tc["function"].get("arguments") or "{}"
            try:
                args = json.loads(raw_args) if raw_args.strip() else {}
                if not isinstance(args, dict):
                    raise ValueError("arguments must be a JSON object")
            except (json.JSONDecodeError, ValueError, TypeError):
                messages.append(
                    _tool_message(
                        f"ERROR: arguments for '{name}' were not a valid JSON object.",
                        tc_id,
                        name,
                    )
                )
                continue

            tool = self.tool_router.get(name)
            if (
                tool is not None
                and tool.requires_approval
                and not self._approve(name, args, auto_approve, console)
            ):
                messages.append(
                    _tool_message(
                        f"Tool '{name}' was not approved by the user; it was skipped.",
                        tc_id,
                        name,
                    )
                )
                continue

            if stream and console is not None:
                console.print(f"[dim]→ {name}({_short(args)})[/dim]")
            output, success = await self.tool_router.call_tool(
                name, args, tool_call_id=tc_id
            )
            messages.append(_tool_message(output, tc_id, name))
            if stream and console is not None:
                tag = "ok" if success else "error"
                console.print(f"[dim]  {tag}: {_short(output)}[/dim]")

    def _approve(
        self, name: str, args: dict, auto_approve: bool | None, console: Any
    ) -> bool:
        """Decide whether a tool requiring approval may run.

        Resolution order: explicit ``auto_approve`` / ``yolo_mode`` →
        ``approval_callback`` → interactive Rich prompt (only on a TTY) → reject.

        Args:
            name (str): The tool name awaiting approval.
            args (dict): The parsed arguments the tool would be called with.
            auto_approve (bool | None): Per-run override of ``yolo_mode``.
            console (Any): Optional Rich console for the interactive prompt.

        Returns:
            bool: True to execute the tool, False to skip it.
        """
        effective = self.yolo_mode if auto_approve is None else auto_approve
        if effective:
            return True
        if self.approval_callback is not None:
            try:
                return bool(self.approval_callback(name, args))
            except Exception:  # noqa: BLE001 — a failing callback means "do not run"
                return False
        try:
            import sys

            if not sys.stdin.isatty():
                return False
            from rich.prompt import Confirm

            return Confirm.ask(
                f"Approve tool '{name}' with args {_short(args)}?",
                default=False,
                console=console,
            )
        except Exception:  # noqa: BLE001
            return False

    # ----------------------------------------------------------------------- #
    # LLM call (with retry) — streaming + non-streaming
    # ----------------------------------------------------------------------- #
    async def _call_llm(
        self, messages: list[dict], tools: list[dict] | None, stream: bool, console: Any
    ) -> LLMResult:
        """Call the model (with transient-error retries) and normalise the response."""
        response = await self._acompletion_with_retry(messages, tools, stream)
        if stream:
            return self._record(await self._consume_stream(response, console))
        return self._record(self._parse_response(response))

    def _record(self, pair: tuple[LLMResult, Any]) -> LLMResult:
        """Fold a call's raw usage into the model's cumulative totals."""
        result, raw_usage = pair
        result.usage = self.model.record_usage(raw_usage)
        return result

    async def _acompletion_with_retry(
        self, messages: list[dict], tools: list[dict] | None, stream: bool
    ) -> Any:
        """Call ``model.acompletion`` with transient-error retries and backoff.

        Raises:
            Exception: Re-raised once retries are exhausted or for non-retryable errors.
        """
        for attempt in range(_MAX_LLM_RETRIES):
            try:
                return await self.model.acompletion(
                    messages=messages, tools=tools, stream=stream
                )
            except Exception as e:  # noqa: BLE001
                delay = _retry_delay_for(e, attempt)
                if attempt < _MAX_LLM_RETRIES - 1 and delay is not None:
                    logger.warning(
                        "Transient LLM error (attempt %d): %s — retrying in %ds",
                        attempt + 1,
                        e,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                raise
        raise RuntimeError("unreachable")

    @staticmethod
    def _parse_response(response: Any) -> tuple[LLMResult, Any]:
        """Normalise a non-streaming litellm response into ``(LLMResult, raw_usage)``."""
        choice = response.choices[0]
        message = choice.message
        content = message.content or None
        finish_reason = choice.finish_reason

        tool_calls_acc: dict[int, dict] = {}
        if getattr(message, "tool_calls", None):
            for idx, tc in enumerate(message.tool_calls):
                tool_calls_acc[idx] = {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
        result = LLMResult(content, tool_calls_acc, finish_reason)
        return result, getattr(response, "usage", None)

    @staticmethod
    async def _consume_stream(response: Any, console: Any) -> tuple[LLMResult, Any]:
        """Drain a streaming response, accumulating content + tool calls.

        Prints assistant text deltas to ``console`` as they arrive. Returns the
        normalised result paired with the final usage object (if the provider sent one).
        """
        full_content = ""
        tool_calls_acc: dict[int, dict] = {}
        finish_reason: str | None = None
        final_usage = None

        async for chunk in response:
            choice = chunk.choices[0] if getattr(chunk, "choices", None) else None
            if choice is None:
                if getattr(chunk, "usage", None):
                    final_usage = chunk.usage
                continue
            delta = choice.delta
            if choice.finish_reason:
                finish_reason = choice.finish_reason
            if getattr(delta, "content", None):
                full_content += delta.content
                if console is not None:
                    console.print(delta.content, end="", markup=False, highlight=False)
            if getattr(delta, "tool_calls", None):
                for tc_delta in delta.tool_calls:
                    slot = tool_calls_acc.setdefault(
                        tc_delta.index,
                        {
                            "id": "",
                            "type": "function",
                            "function": {"name": "", "arguments": ""},
                        },
                    )
                    if tc_delta.id:
                        slot["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            slot["function"]["name"] += tc_delta.function.name
                        if tc_delta.function.arguments:
                            slot["function"]["arguments"] += tc_delta.function.arguments
            if getattr(chunk, "usage", None):
                final_usage = chunk.usage

        result = LLMResult(full_content or None, tool_calls_acc, finish_reason)
        return result, final_usage
