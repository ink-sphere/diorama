"""Tool registry + dispatcher (adapted from diorama's ``ToolRouter``).

Holds the available tools, exports their OpenAI schemas for the LLM, and routes a
parsed tool call to the right ``async forward``. It introspects each tool's
``forward`` signature and injects ``tool_call_id`` when the tool declares it, so
most tools stay pure while a few can learn which call they belong to.

Tool execution errors are caught and returned to the agent as a JSON error string
(with ``success=False``) rather than crashing the loop — this is what lets the model
observe a failure and adapt on the next turn.
"""

from __future__ import annotations

import inspect
import json
from typing import Any

from diorama.core.tool import Tool


def _stringify(result: Any) -> str:
    """Coerce a tool result into a string suitable for a ``role: tool`` message.

    Strings pass through unchanged. Anything else is JSON-serialised with
    ``default=str`` so non-serialisable objects fall back to their ``str()``
    representation; if JSON serialisation itself raises, the raw ``str()`` is used.

    Args:
        result (Any): The raw value returned by a tool's ``forward`` method.

    Returns:
        str: A string representation of ``result``.
    """
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(result)


class ToolRouter:
    """Registry and async dispatcher for :class:`Tool` instances.

    Attributes:
        tools (dict[str, Tool]): Mapping from tool name to tool instance, populated
            via :meth:`register`.
    """

    def __init__(self, tools: list[Tool] | None = None) -> None:
        """Initialise the router and optionally pre-register a list of tools.

        Args:
            tools (list[Tool] | None): Initial set of tools to register. Defaults to
                None (empty registry).
        """
        self.tools: dict[str, Tool] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        """Add a tool to the registry, keyed by its ``tool_name``.

        Args:
            tool (Tool): The tool to register. If a tool with the same name already
                exists it is silently replaced.
        """
        self.tools[tool.tool_name] = tool

    def get(self, name: str) -> Tool | None:
        """Look up a tool by name.

        Args:
            name (str): The ``tool_name`` to look up.

        Returns:
            Tool | None: The registered tool, or ``None`` if not found.
        """
        return self.tools.get(name)

    def get_tool_specs_for_llm(self) -> list[dict[str, Any]]:
        """Return all registered tool schemas in OpenAI function-calling format."""
        return [tool.to_json_schema() for tool in self.tools.values()]

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        tool_call_id: str | None = None,
    ) -> tuple[str, bool]:
        """Execute ``tool_name`` and return ``(output_string, success)``.

        ``tool_call_id`` is injected only when the tool's ``forward`` declares it.
        Unknown tools and exceptions raised by ``forward`` are returned as JSON error
        strings with ``success=False`` so the agent can observe and adapt.

        Args:
            tool_name (str): The name of the tool to invoke.
            arguments (dict[str, Any]): Parsed arguments from the model's tool call.
            tool_call_id (str | None): The id of the originating tool call, injected
                into ``forward`` when declared. Defaults to None.

        Returns:
            tuple[str, bool]: The stringified output and whether the call succeeded.
        """
        tool = self.tools.get(tool_name)
        if tool is None:
            return _stringify({"error": f"Unknown tool: {tool_name}"}), False

        # Inject tool_call_id only when the tool's forward accepts it.
        try:
            params = inspect.signature(tool.forward).parameters
        except (TypeError, ValueError):
            params = {}

        call_kwargs = dict(arguments)
        if "tool_call_id" in params:
            call_kwargs["tool_call_id"] = tool_call_id

        try:
            result = await tool.forward(**call_kwargs)
            return _stringify(result), True
        except Exception as e:  # noqa: BLE001 — surfaced to the agent, not fatal
            return _stringify({"error": str(e)}), False
