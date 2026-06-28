"""The EbookLoaderAgent: a ReAct agent specialized for structure extraction.

A thin, reusable subclass of :class:`~diorama.core.react.ReactAgent`. Model config
lives on the instance; the book-specific tools are bound per :meth:`load` call, so
one agent can process many books in sequence.

The agent decides *structure* (boundaries, level names, classification); a
deterministic slicer turns its submitted tree into the final, text-filled
:class:`EbookStructure`. See ``docs/ebook-loader-agent.md``.
"""

from __future__ import annotations

from typing import Any

from diorama.core.react import ReactAgent
from diorama.core.router import ToolRouter
from diorama.ebook.context import EbookContext
from diorama.ebook.models import EbookStructure
from diorama.ebook.prompts import EBOOK_LOADER_INSTRUCTIONS, render_load_prompt
from diorama.ebook.slicer import build_structure
from diorama.ebook.tools import build_ebook_tools

DEFAULT_MODEL_ID = "openrouter/openai/gpt-4o-mini"


class EbookLoadError(RuntimeError):
    """Raised when the agent finishes a run without submitting a valid structure."""


class EbookLoaderAgent(ReactAgent):
    """A ReAct agent that extracts an EPUB's hierarchical structure.

    Construct once, then call :meth:`load` per book. ``model_id`` defaults to
    :data:`DEFAULT_MODEL_ID`; pass a stronger model for tougher structures.
    """

    def __init__(
        self,
        *,
        model_id: str = DEFAULT_MODEL_ID,
        max_iterations: int = 40,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            tools=[],
            instructions=EBOOK_LOADER_INSTRUCTIONS,
            model_id=model_id,
            max_iterations=max_iterations,
            **kwargs,
        )

    async def load(
        self, epub_path: str, *, stream: bool = False, **run_kwargs: Any
    ) -> EbookStructure:
        """Parse, analyze, and structure an EPUB.

        Args:
            epub_path (str): Path to the ``.epub`` file.
            stream (bool): Stream the agent's reasoning/tool activity to a console.
            **run_kwargs: Forwarded to :meth:`ReactAgent.run`.

        Returns:
            EbookStructure: The extracted hierarchy with text sliced into its leaves.

        Raises:
            EbookLoadError: If the agent never submitted a valid structure.
        """
        ctx = EbookContext.parse(epub_path)
        # Bind the book-specific tools for this run (clean per-book override point).
        self.tool_router = ToolRouter(build_ebook_tools(ctx))

        result = await self.run(render_load_prompt(ctx), stream=stream, **run_kwargs)

        if ctx.submitted_structure is None:
            raise EbookLoadError(
                "the agent finished without a valid submit_structure "
                f"(stop_reason={result.stop_reason!r}, steps={result.steps})."
            )

        structure = build_structure(ctx, ctx.submitted_structure)
        structure.usage = dict(result.usage)
        structure.cost_usd = result.cost_usd
        return structure
