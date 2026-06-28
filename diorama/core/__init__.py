"""Diorama agents: a basic ReAct agent and its tool framework."""

from diorama.core.answer import FinalAnswerTool
from diorama.core.demo_tools import CalculatorTool, CurrentTimeTool
from diorama.core.prompts import SYSTEM_PROMPT
from diorama.core.react import LLMResult, ReactAgent
from diorama.core.router import ToolRouter
from diorama.core.tool import Tool, ToolParameter

__all__ = [
    "ReactAgent",
    "LLMResult",
    "Tool",
    "ToolParameter",
    "ToolRouter",
    "FinalAnswerTool",
    "CalculatorTool",
    "CurrentTimeTool",
    "SYSTEM_PROMPT",
]
