"""EbookLoaderAgent: extracts an EPUB's hierarchical structure via a ReAct agent.

The agent never assembles book text itself. It explores a book's blocks (numbered
paragraphs/headings/list items, flattened by :class:`~diorama.ebook.parser.EbookContext`)
through read-only tools, then proposes a tree of block-id *boundaries* via
``submit_structure``. :func:`diorama.ebook.slicer.build_structure` deterministically
turns that tree into the final :class:`~diorama.ebook.models.EbookStructure` — this
two-phase split (agent finds boundaries, deterministic code fills in text) keeps the
agent's output small and makes the result exactly reproducible from the ids it chose.

Each :meth:`EbookLoaderAgent.load` call builds a *fresh* :class:`~diorama.core.react.ReactAgent`
bound to that book's own tools and parsed context, then discards it. Nothing about
one book's run — message history, tool state, streaming subscriptions — carries over
to the next, so the agent is safe to reuse across books without any of the
instance-state bugs that come from swapping tools on a long-lived agent.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import weave

from diorama.core.context import ContextCompactor
from diorama.core.events import AgentEvent
from diorama.core.react import ReactAgent, describe_stop_reason
from diorama.core.results import ToolResult
from diorama.core.tool import Tool, ToolParameter
from diorama.ebook.models import EbookStructure
from diorama.ebook.parser import EbookContext
from diorama.ebook.slicer import DEFAULT_SEGMENT_LENGTH, build_structure, validate_tree
from diorama.models.litellm_model import LiteLLMModel
from diorama.models.usage import UsageSink, new_run_id

#: This agent's key in the settings registry (:data:`diorama.backend.settings.AGENTS`)
#: and in every ledger row it produces. Defined here rather than imported from the
#: backend so the agent package keeps not depending on the web layer.
AGENT_ID = "ebook_loader"

_MAX_READ_BLOCKS = 300
_PREVIEW_CHARS = 200
#: Baseline for an agent constructed without a ``model_id`` (a script, a test).
#: Mirrors the OpenRouter entry in this agent's :data:`diorama.backend.settings.AGENTS`
#: defaults — the backend resolves per provider and passes an id explicitly, so this
#: is only ever the no-backend fallback.
_DEFAULT_MODEL_ID = "openrouter/google/gemini-3.6-flash"

# diorama.core.context estimates tokens as chars/4, which undercounts transcripts
# dense with "[Block N]" markers and archaic/foreign punctuation — exactly this
# agent's read_blocks/search_blocks output. A live run against a real play tripped
# the provider's context-length error with the default 16_384-token reserve (the
# estimate came in ~0.8% under the real tokenizer count, just enough to slip past
# the threshold). Tripling the reserve buys headroom well past that observed miss.
_COMPACTION_RESERVE_TOKENS = 48_000


class EbookLoaderError(RuntimeError):
    """Raised when a :meth:`EbookLoaderAgent.load` run ends without a valid structure."""


# --------------------------------------------------------------------------- #
# Prompting
# --------------------------------------------------------------------------- #
EBOOK_LOADER_INSTRUCTIONS = """You are extracting the hierarchical structure of an EPUB book.

The book has been flattened into numbered "blocks" (paragraphs, headings, list
items, in reading order) — these block ids are your only coordinate system. You do
not see the book's raw markup; you only see block text and ids through your tools.

Tools:
- get_overview: title, author, block count, and a peek at the first/last blocks.
- get_toc: the EPUB's own table of contents, each entry best-effort anchored to a
  block id (exact anchors are trustworthy; fuzzy-matched ones are a hint — verify
  them against the actual block text before relying on them).
- list_headings: every block the parser flagged as a heading — usually the fastest
  way to find chapter/act/section boundaries.
- read_blocks(start_block_id, end_block_id): read raw text in a range.
- search_blocks(query, regex=False): find blocks by substring or regex.
- submit_structure(root): submit your final tree. This is the only way to finish.

Your job:
1. Discover the book's own semantic hierarchy — it may be flat (just "chapter") or
   nested (e.g. "act" > "scene", or "parva" > "adhyaya" for an epic), and may use
   non-English terms. Choose level_type names in lowercase English that describe the
   level (e.g. "chapter", "act", "scene", "book"), not always "chapter".
2. Assign every block from 0 to N-1 to exactly one leaf node — no gaps, no overlaps.
   A leaf node's [start_block_id, end_block_id] range becomes its text verbatim.
3. A parent node's range must exactly span its children's combined range: the first
   child starts where the parent starts, the last child ends where the parent ends,
   and consecutive children must be contiguous.
4. When a level repeats with a detectable text marker at the start of a block (e.g.
   "SCENE I", "SCENE II", "अध्याय 1", "अध्याय 2"), you may set child_pattern to a
   regex matching that marker (capture the number/numeral in group 1) plus
   child_level_type for the label to apply to each generated child, instead of
   listing every child by hand. Any blocks before the first match become that
   node's preamble_text (e.g. stage directions before "SCENE I").
5. submit_structure validates your tree before accepting it. If it's rejected you
   get back the specific errors — fix them and resubmit. Do not give up after one
   rejection; keep iterating until it is accepted.
6. Prefer get_toc and list_headings over reading the whole book with read_blocks —
   only read ranges you're actually unsure about.
"""


def render_load_prompt(context: EbookContext) -> str:
    """Build the initial user message for a ``load()`` run."""
    by_author = f" by {context.author}" if context.author else ""
    return (
        f'Extract the hierarchical structure of the EPUB "{context.title}"{by_author}.\n\n'
        f"The book has been flattened into {context.total_blocks} numbered blocks, "
        f"indexed 0..{context.total_blocks - 1}. Start with get_overview and get_toc "
        "to orient yourself, then submit_structure once every block is mapped to "
        "exactly one node in the hierarchy."
    )


# --------------------------------------------------------------------------- #
# submit_structure's recursive parameter schema
# --------------------------------------------------------------------------- #
_NODE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "level_type": {
            "type": "string",
            "description": (
                "Semantic name of this level, lowercase English, e.g. 'chapter', "
                "'act', 'scene', 'parva'."
            ),
        },
        "number": {
            "type": ["string", "null"],
            "description": "The node's number/label as it appears in the book (e.g. '1', 'IV'). Null if unnumbered.",
        },
        "title": {
            "type": ["string", "null"],
            "description": "The node's title, if any. Null if untitled.",
        },
        "start_block_id": {
            "type": "integer",
            "description": "First block (inclusive) belonging to this node and everything under it.",
        },
        "end_block_id": {
            "type": "integer",
            "description": "Last block (inclusive) belonging to this node and everything under it.",
        },
        "children": {
            "type": "array",
            "items": {"$ref": "#/$defs/node"},
            "description": "Nested child nodes, in order. Omit or leave empty for leaf nodes.",
        },
        "child_pattern": {
            "type": ["string", "null"],
            "description": (
                "Regex matched against the START of a block's text to auto-detect "
                "repeating child boundaries within this node's range (e.g. "
                r"'^SCENE\s+([IVXLCDM]+)' for 'SCENE I', 'SCENE II', ...). Mutually "
                "exclusive with 'children'. Requires child_level_type."
            ),
        },
        "child_level_type": {
            "type": ["string", "null"],
            "description": "Required alongside child_pattern: the level_type label applied to each auto-generated child, e.g. 'scene'.",
        },
    },
    "required": ["level_type", "start_block_id", "end_block_id"],
    "additionalProperties": False,
}

SUBMIT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "root": {
            "type": "array",
            "items": {"$ref": "#/$defs/node"},
            "description": "The top-level nodes of the book's structure, in order.",
        },
    },
    "required": ["root"],
    "additionalProperties": False,
    "$defs": {"node": _NODE_SCHEMA},
}


# --------------------------------------------------------------------------- #
# Per-book state shared by one load()'s tools
# --------------------------------------------------------------------------- #
@dataclass
class _LoadState:
    """Mutable state shared by the tools bound to one ``load()`` call.

    Never touches ``ReactAgent`` internals — the tools only read the parsed
    ``context`` and write ``result``/``last_errors`` here, so nothing leaks between
    successive ``load()`` calls on the same :class:`EbookLoaderAgent`.
    """

    context: EbookContext
    max_segment_length: int | None
    result: EbookStructure | None = None
    last_errors: list[str] = field(default_factory=list)


def _block_preview(block: Any) -> dict[str, Any]:
    text = block.text
    if len(text) > _PREVIEW_CHARS:
        text = text[:_PREVIEW_CHARS] + "…"
    return {"id": block.id, "tag": block.tag, "text": text}


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #
class GetOverviewTool(Tool):
    """Orientation info about the book: title, author, block count, previews."""

    tool_name: str = "get_overview"
    description: str = (
        "Get orientation info about the book: title, author, block count, and "
        "previews of the first/last blocks. Always call this first."
    )
    parameters: list[ToolParameter] = []
    state: Any = None

    @weave.op
    async def forward(self) -> Any:
        ctx = self.state.context
        return {
            "title": ctx.title,
            "author": ctx.author,
            "total_blocks": ctx.total_blocks,
            "heading_count": len(ctx.headings()),
            "first_blocks": [_block_preview(b) for b in ctx.blocks[:5]],
            "last_blocks": [_block_preview(b) for b in ctx.blocks[-3:]],
        }


class GetTocTool(Tool):
    """The EPUB's own table of contents, block-anchored where possible."""

    tool_name: str = "get_toc"
    description: str = (
        "Get the EPUB's own embedded table of contents, with each entry "
        "best-effort anchored to a block id ('matched_by' tells you how: "
        "'anchor' is exact, 'fuzzy' is a best guess, 'unresolved' means no anchor "
        "could be found)."
    )
    parameters: list[ToolParameter] = []
    state: Any = None

    @weave.op
    async def forward(self) -> Any:
        ctx = self.state.context
        if not ctx.toc:
            return ToolResult.from_text("This EPUB has no embedded table of contents.")
        return [entry.model_dump() for entry in ctx.toc]


class ListHeadingsTool(Tool):
    """Every block the parser detected as a heading (h1-h6)."""

    tool_name: str = "list_headings"
    description: str = (
        "List every block the parser detected as a heading (h1-h6), with its "
        "block id and text. Usually the fastest way to spot chapter/act/scene "
        "boundaries."
    )
    parameters: list[ToolParameter] = []
    state: Any = None

    @weave.op
    async def forward(self) -> Any:
        return [
            {"block_id": b.id, "tag": b.tag, "text": b.text}
            for b in self.state.context.headings()
        ]


class ReadBlocksTool(Tool):
    """Raw text of a block range, each block id-prefixed."""

    tool_name: str = "read_blocks"
    description: str = (
        "Read the raw text of blocks in a range (inclusive), each prefixed with "
        f"its block id. Capped at {_MAX_READ_BLOCKS} blocks per call — narrow the "
        "range if you hit the limit."
    )
    parameters: list[ToolParameter] = [
        ToolParameter(
            param_name="start_block_id",
            tool_type="number",
            description="First block id to read (inclusive).",
        ),
        ToolParameter(
            param_name="end_block_id",
            tool_type="number",
            description="Last block id to read (inclusive).",
        ),
    ]
    state: Any = None

    @weave.op
    async def forward(self, start_block_id: float, end_block_id: float) -> Any:
        start, end = int(start_block_id), int(end_block_id)
        if end - start + 1 > _MAX_READ_BLOCKS:
            return ToolResult.error(
                f"Range spans {end - start + 1} blocks, exceeding the "
                f"{_MAX_READ_BLOCKS}-block cap. Narrow the range and call again."
            )
        try:
            blocks = self.state.context.blocks_in_range(start, end)
        except ValueError as e:
            return ToolResult.error(str(e))
        return "\n\n".join(f"[Block {b.id}] {b.text}" for b in blocks)


class SearchBlocksTool(Tool):
    """Substring or regex search over block text."""

    tool_name: str = "search_blocks"
    description: str = (
        "Search block text for a substring or regex pattern; returns matching "
        "block ids and text."
    )
    parameters: list[ToolParameter] = [
        ToolParameter(
            param_name="query",
            tool_type="string",
            description="Text or regex pattern to search for.",
        ),
        ToolParameter(
            param_name="regex",
            tool_type="boolean",
            description="Treat query as a regex pattern. Defaults to false.",
            required=False,
        ),
        ToolParameter(
            param_name="limit",
            tool_type="number",
            description="Maximum matches to return. Defaults to 20.",
            required=False,
        ),
    ]
    state: Any = None

    @weave.op
    async def forward(
        self, query: str, regex: bool = False, limit: float | None = None
    ) -> Any:
        try:
            matches = self.state.context.search(
                query, regex=regex, limit=int(limit) if limit else 20
            )
        except re.error as e:
            return ToolResult.error(f"Invalid regex: {e}")
        if not matches:
            return ToolResult.from_text("No matches.")
        return [{"block_id": b.id, "text": b.text} for b in matches]


class SubmitStructureTool(Tool):
    """Submit and validate the final structure tree; ends the run on success."""

    tool_name: str = "submit_structure"
    description: str = (
        "Submit the complete hierarchical structure of the book. Every block from "
        "0 to N-1 must be assigned to exactly one leaf node. If the tree is "
        "invalid (gaps, overlaps, out-of-range ids), you get back a list of "
        "errors to fix — this call does not end the task until it succeeds."
    )
    parameters: list[ToolParameter] = []
    parameters_schema: dict[str, Any] | None = SUBMIT_SCHEMA
    state: Any = None

    @weave.op
    async def forward(self, root: list[dict]) -> Any:
        context = self.state.context
        errors = validate_tree(root, context)
        if errors:
            self.state.last_errors = errors
            return ToolResult.error(
                "Structure rejected — fix these and resubmit:\n"
                + "\n".join(f"- {e}" for e in errors)
                + "\n\nCall submit_structure again with the corrected tree. Do not "
                "reply with only text — the task is not finished until a call to "
                "submit_structure succeeds."
            )
        structure = build_structure(
            root, context, max_segment_length=self.state.max_segment_length
        )
        self.state.result = structure
        self.state.last_errors = []
        return ToolResult.from_text(
            f"Structure accepted: {len(root)} top-level node(s), "
            f"{structure.coverage.assigned_blocks}/{structure.coverage.total_blocks} "
            f"blocks covered, level types: {', '.join(structure.level_types)}.",
            terminate=True,
        )


def _build_tools(state: _LoadState) -> list[Tool]:
    """Instantiate a fresh set of book-bound tools for one ``load()`` call."""
    return [
        GetOverviewTool(state=state),
        GetTocTool(state=state),
        ListHeadingsTool(state=state),
        ReadBlocksTool(state=state),
        SearchBlocksTool(state=state),
        SubmitStructureTool(state=state),
    ]


# --------------------------------------------------------------------------- #
# The agent
# --------------------------------------------------------------------------- #
class EbookLoaderAgent:
    """Extracts an EPUB's hierarchical structure using a ReAct agent.

    Construct once and call :meth:`load` per book. Each call parses the EPUB into
    an :class:`~diorama.ebook.parser.EbookContext`, builds a fresh
    :class:`~diorama.core.react.ReactAgent` bound to that book's tools, runs it to
    completion, and returns the :class:`~diorama.ebook.models.EbookStructure` the
    agent's ``submit_structure`` call produced.
    """

    def __init__(
        self,
        *,
        model: LiteLLMModel | None = None,
        model_id: str | None = None,
        api_key: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        max_iterations: int | None = 60,
        instructions: str | None = None,
        enable_prompt_caching: bool = True,
        weave_project: str | None = None,
        usage_sink: UsageSink | None = None,
        book_id: str | None = None,
        run_id: str | None = None,
    ) -> None:
        """Configure the agent used by every subsequent :meth:`load` call.

        Args:
            model (LiteLLMModel | None): A pre-built model to use for every ``load()``
                call (e.g. a :class:`~tests.fakes.FakeModel` in tests). Takes
                precedence over ``model_id`` when set.
            model_id (str | None): litellm model id to build a fresh
                :class:`LiteLLMModel` from. Defaults to
                :class:`~diorama.core.react.ReactAgent`'s own default model when
                both this and ``model`` are None.
            api_key (str | None): Provider credential, when building the model.
                None leaves litellm to read it from the environment.
            temperature (float): Sampling temperature, when building the model.
            max_tokens (int | None): Completion token cap, when building the model.
            max_iterations (int | None): Turn ceiling per ``load()`` call. EPUB
                structure discovery can take many tool calls on long books.
            instructions (str | None): Extra instructions appended after
                ``EBOOK_LOADER_INSTRUCTIONS``.
            enable_prompt_caching (bool): Pass-through to the model wrapper.
            weave_project (str | None): When set, initialise W&B Weave tracing.
            usage_sink (UsageSink | None): Receives one
                :class:`~diorama.models.usage.LLMCallRecord` per LLM call — every agent
                turn plus every context-compaction summary. None (the default) records
                nothing, which is what a script or a test that doesn't care about cost
                wants; the backend installs a sink that appends to the book's ledger.
            book_id (str | None): Stamped onto every emitted record, so the ledger rows
                can be attributed to a book on the shelf.
            run_id (str | None): Stamped onto every emitted record, grouping the calls
                of one run. A reprocessed book accumulates a second run in the same
                ledger rather than overwriting the first, so the cost of a retry is
                visible instead of silently replacing what it cost the first time.
        """
        self._model = model
        self._model_id = model_id
        self._api_key = api_key
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._max_iterations = max_iterations
        self._instructions = EBOOK_LOADER_INSTRUCTIONS + (
            f"\n\n{instructions}" if instructions else ""
        )
        self._enable_prompt_caching = enable_prompt_caching
        self._weave_project = weave_project
        self._usage_sink = usage_sink
        self._usage_labels: dict[str, Any] = {
            "book_id": book_id,
            "run_id": run_id or new_run_id(),
            "agent_id": AGENT_ID,
        }

    def _build_agent(
        self, epub_path: str | Path, *, max_segment_length: int | None
    ) -> tuple[ReactAgent, _LoadState, EbookContext]:
        """Parse ``epub_path`` and assemble the fresh agent/state/tools for one run."""
        context = EbookContext.parse(epub_path)
        state = _LoadState(context=context, max_segment_length=max_segment_length)
        tools = _build_tools(state)

        # Built explicitly (rather than left to ReactAgent) so the same instance can
        # be handed to both the agent and the compactor below — the compactor's
        # summarisation calls then fold into the same cumulative usage/cost totals
        # the run reports on the returned structure, and reach the same usage sink.
        model = self._model or LiteLLMModel(
            model_id=self._model_id or _DEFAULT_MODEL_ID,
            api_key=self._api_key,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
            enable_prompt_caching=self._enable_prompt_caching,
        )
        # Attached here rather than at construction so a caller-supplied ``model``
        # (a test fake, a shared instance) is instrumented too.
        if self._usage_sink is not None:
            model.usage_sink = self._usage_sink
            model.usage_labels = dict(self._usage_labels)
        compactor = ContextCompactor(model, reserve_tokens=_COMPACTION_RESERVE_TOKENS)

        agent = ReactAgent(
            tools=tools,
            model=model,
            instructions=self._instructions,
            max_iterations=self._max_iterations,
            compactor=compactor,
            weave_project=self._weave_project,
            # This agent's deliverable is a submitted structure, not a closing
            # message, so a turn that ends with no tool call is not evidence of
            # success — it is a run about to end with nothing to show. Say what is
            # missing and let it carry on.
            completion_guard=lambda: (
                None
                if state.result is not None
                else (
                    "You haven't submitted a structure yet, so nothing has been "
                    "saved. Call submit_structure now with the tree you've worked "
                    "out. If a previous submission was rejected, fix the errors it "
                    "reported and submit again."
                )
            ),
        )
        return agent, state, context

    @staticmethod
    def _finalize(
        agent: ReactAgent, state: _LoadState, context: EbookContext, stop_reason: str
    ) -> EbookStructure:
        """Resolve a finished run's state into its structure, or raise."""
        if state.result is None:
            reason = (
                "; ".join(state.last_errors)
                if state.last_errors
                else describe_stop_reason(stop_reason)
            )
            raise EbookLoaderError(
                f"EbookLoaderAgent did not submit a valid structure for "
                f"'{context.title}' ({reason})"
            )
        state.result.cost_usd = round(agent.model.cumulative.get("cost_usd", 0.0), 6)
        return state.result

    async def load(
        self,
        epub_path: str | Path,
        *,
        stream: bool = False,
        max_segment_length: int | None = DEFAULT_SEGMENT_LENGTH,
        console: Any = None,
    ) -> EbookStructure:
        """Parse ``epub_path`` and run the agent to extract its structure.

        Args:
            epub_path (str | Path): Path to the EPUB file.
            stream (bool): Render assistant text and tool activity to a Rich
                console as the agent works.
            max_segment_length (int | None): Paginate leaf node text into segments
                of at most this many characters. None disables segmentation.
            console (Any): Optional Rich ``Console`` for rendered output.

        Returns:
            EbookStructure: The extracted structure, with ``cost_usd`` set to this
                run's cumulative LLM spend.

        Raises:
            EbookLoaderError: If the run ends without a valid submitted structure
                (e.g. it hit ``max_iterations`` or was cancelled).
        """
        agent, state, context = self._build_agent(
            epub_path, max_segment_length=max_segment_length
        )
        result = await agent.run(
            render_load_prompt(context), stream=stream, console=console
        )
        return self._finalize(agent, state, context, result.stop_reason)

    def stream_load(
        self,
        epub_path: str | Path,
        *,
        max_segment_length: int | None = DEFAULT_SEGMENT_LENGTH,
    ) -> tuple[AsyncIterator[AgentEvent], Callable[[], EbookStructure]]:
        """Like :meth:`load`, but exposes the run's live events instead of blocking.

        Mirrors :meth:`~diorama.core.react.ReactAgent.stream_events` paired with
        ``last_result``: iterate the returned events fully (an SSE endpoint, a
        console renderer, a test — anything), then call ``finalize()`` to get the
        extracted structure. Calling ``finalize()`` before the iterator is exhausted
        raises, same as reading ``ReactAgent.last_result`` mid-run.

        Args:
            epub_path (str | Path): Path to the EPUB file.
            max_segment_length (int | None): Paginate leaf node text into segments
                of at most this many characters. None disables segmentation.

        Returns:
            tuple[AsyncIterator[AgentEvent], Callable[[], EbookStructure]]: The
                run's event stream, and a callable that resolves the final
                structure once that stream has been fully consumed. The callable
                raises :class:`EbookLoaderError` if the run ended without a valid
                submitted structure.
        """
        agent, state, context = self._build_agent(
            epub_path, max_segment_length=max_segment_length
        )
        events = agent.stream_events(render_load_prompt(context), provider_stream=False)

        def finalize() -> EbookStructure:
            assert agent.last_result is not None, (
                "finalize() called before the event stream was fully consumed"
            )
            return self._finalize(agent, state, context, agent.last_result.stop_reason)

        return events, finalize


__all__ = [
    "EBOOK_LOADER_INSTRUCTIONS",
    "SUBMIT_SCHEMA",
    "EbookLoaderAgent",
    "EbookLoaderError",
    "GetOverviewTool",
    "GetTocTool",
    "ListHeadingsTool",
    "ReadBlocksTool",
    "SearchBlocksTool",
    "SubmitStructureTool",
    "render_load_prompt",
]
