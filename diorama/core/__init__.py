"""Diorama agents: a stateful ReAct agent, its event stream, and its tool framework."""

from diorama.core.answer import FinalAnswerTool
from diorama.core.cancellation import CancellationToken, SimpleCancellationToken
from diorama.core.context import (
    ContextCompactor,
    ContextUsageEstimate,
    estimate_context_tokens,
    estimate_context_usage,
)
from diorama.core.demo_tools import CalculatorTool, CurrentTimeTool
from diorama.core.events import (
    AgentEndEvent,
    AgentEvent,
    AgentStartEvent,
    CompactionEndEvent,
    CompactionStartEvent,
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    RetryEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    TurnEndEvent,
    TurnStartEvent,
)
from diorama.core.prompts import SYSTEM_PROMPT
from diorama.core.react import (
    LLMResult,
    QueuedMessages,
    ReactAgent,
    ReactAgentResult,
    ToolInvocation,
)
from diorama.core.rendering import ConsoleRenderer
from diorama.core.results import ImageBlock, TextBlock, ToolResult
from diorama.core.router import ToolRouter
from diorama.core.session import JsonlSessionStore, SessionEntry, SessionState
from diorama.core.tool import Tool, ToolParameter

__all__ = [
    # Agent
    "ReactAgent",
    "ReactAgentResult",
    "QueuedMessages",
    "LLMResult",
    "SYSTEM_PROMPT",
    # Tools
    "Tool",
    "ToolParameter",
    "ToolRouter",
    "ToolResult",
    "ToolInvocation",
    "TextBlock",
    "ImageBlock",
    "FinalAnswerTool",
    "CalculatorTool",
    "CurrentTimeTool",
    # Cancellation
    "CancellationToken",
    "SimpleCancellationToken",
    # Events + rendering
    "AgentEvent",
    "AgentStartEvent",
    "AgentEndEvent",
    "TurnStartEvent",
    "TurnEndEvent",
    "MessageStartEvent",
    "MessageUpdateEvent",
    "MessageEndEvent",
    "ToolExecutionStartEvent",
    "ToolExecutionUpdateEvent",
    "ToolExecutionEndEvent",
    "CompactionStartEvent",
    "CompactionEndEvent",
    "RetryEvent",
    "ConsoleRenderer",
    # Context management
    "ContextCompactor",
    "ContextUsageEstimate",
    "estimate_context_tokens",
    "estimate_context_usage",
    # Sessions
    "JsonlSessionStore",
    "SessionEntry",
    "SessionState",
]
