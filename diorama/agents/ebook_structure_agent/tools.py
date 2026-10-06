from __future__ import annotations

from pathlib import Path

from tau_agent import AgentTool
from tau_coding.tools import create_coding_tools


def create_ebook_coding_tools(
    workspace: Path, *, shell_command_prefix: str | None = None
) -> list[AgentTool]:
    return create_coding_tools(cwd=workspace, shell_command_prefix=shell_command_prefix)
