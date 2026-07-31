"""EbookSceneSegmentationAgent: cuts a leaf node's text into illustratable scenes.

Once :class:`~diorama.agents.ebook_loader.EbookLoaderAgent` has worked out a book's
hierarchy, every leaf of that structure carries the verbatim text of its block range.
This agent takes one such leaf at a time and decides where, *within* that text, the
picture would change — each resulting scene is a stretch of continuous narrative a
single illustration could depict.

**The agent never emits text.** It sees the node's text as numbered paragraphs and
submits only ``[start_paragraph, end_paragraph]`` boundaries;
:func:`diorama.ebook.scenes.build_scenes` slices the paragraphs back out and joins
them with the same separator they were split on, so a scene's text is always a
byte-identical substring of the node's. That is the same two-phase split the loader
uses (agent proposes boundaries, deterministic code fills in text), and it is what
makes "the core text must not change" a property of the code rather than a hope about
the model. ``submit_scenes`` also *validates* the partition before accepting it — a
gap, an overlap, or an off-the-end index comes back as errors for the agent to fix,
and only a valid submission ends the run.

Like the loader, each call builds a *fresh* :class:`~diorama.core.react.ReactAgent`
bound to that one node's tools and discards it afterwards, so nothing — message
history, tool state, streaming subscriptions — carries between nodes.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

import weave

from diorama.core.context import ContextCompactor
from diorama.core.events import AgentEvent
from diorama.core.react import ReactAgent, describe_stop_reason
from diorama.core.results import ToolResult
from diorama.core.tool import Tool, ToolParameter
from diorama.ebook.models import EbookStructure, StructureNode
from diorama.ebook.scenes import (
    BookScenes,
    SceneSegmentation,
    build_scenes,
    iter_leaves,
    single_scene,
    split_paragraphs,
    validate_scenes,
)
from diorama.models.litellm_model import LiteLLMModel
from diorama.models.usage import UsageSink, new_run_id

#: This agent's key in every ledger row it produces (and the key it would take in
#: :data:`diorama.backend.settings.AGENTS` when it becomes configurable). Defined here
#: rather than imported from the backend so the agent package keeps not depending on
#: the web layer.
AGENT_ID = "ebook_scene_segmentation"

_MAX_READ_PARAGRAPHS = 200
#: Baseline for an agent constructed without a ``model_id`` (a script, a test).
#: Mirrors the OpenRouter entry in this agent's :data:`diorama.backend.settings.AGENTS`
#: defaults — the backend resolves per provider and passes an id explicitly, so this
#: is only ever the no-backend fallback.
_DEFAULT_MODEL_ID = "openrouter/google/gemini-3.6-flash"

#: How much of the node's text is inlined into the opening prompt. Most leaf nodes are
#: a chapter or a scene and fit comfortably, which saves the agent a read round-trip;
#: anything longer is truncated and the agent pages through the rest with
#: ``read_paragraphs`` rather than having a whole part of a book dropped on it at once.
_INLINE_CHAR_BUDGET = 24_000

#: Nodes with fewer paragraphs than this are one scene by definition — see
#: :func:`diorama.ebook.scenes.single_scene`. Paying for an LLM call to be told that a
#: two-paragraph node is a single picture buys nothing.
DEFAULT_MIN_PARAGRAPHS = 3

# Same reserve, for the same reason, as diorama.agents.ebook_loader: the chars/4 token
# estimate in diorama.core.context undercounts transcripts dense with short bracketed
# markers ("[P 12]" here, "[Block N]" there), and a live loader run slipped past the
# default 16_384-token threshold into a provider context-length error.
_COMPACTION_RESERVE_TOKENS = 48_000


class SceneSegmentationError(RuntimeError):
    """Raised when a segmentation run ends without a valid set of scene boundaries."""


# --------------------------------------------------------------------------- #
# Prompting
# --------------------------------------------------------------------------- #
SCENE_SEGMENTATION_INSTRUCTIONS = """
You are splitting one section of a book into scenes for illustration.

The section's text has been split into numbered paragraphs — these paragraph indices
are your only coordinate system. You cannot rewrite, summarise, reorder or edit the
text in any way, and you never emit any of it. You only decide where one scene ends
and the next begins.

A scene is a stretch of continuous narrative that a single picture could depict: one
place, one continuous moment or action, one set of people present. Start a new scene
when:
- the location changes,
- time jumps (later that day, years afterwards, a flashback begins or ends),
- who is present changes materially (someone arrives, everyone leaves),
- or the mode changes (running narration gives way to an inset letter, song, poem,
  dream, or a long digression the narrator addresses to the reader).

Do NOT start a new scene merely because a paragraph ended, a different character
speaks, or the topic of conversation moved on. Two people talking in one room for
thirty paragraphs is one scene, not thirty.

As a rule of thumb a scene runs a few paragraphs to a couple of dozen; a short section
may legitimately be a single scene covering everything.

Tools:
- read_paragraphs(start_paragraph, end_paragraph): read the numbered text in a range.
- submit_scenes(scenes): submit your final list of boundaries. This is the only way to
  finish.

Rules submit_scenes enforces:
1. The scenes must cover every paragraph exactly once: the first scene starts at
   paragraph 0, the last ends at the final paragraph, and each scene starts exactly
   one paragraph after the previous one ends. No gaps, no overlaps.
2. start_paragraph <= end_paragraph for every scene.
3. If your submission is rejected you get back the specific errors — fix them and call
   submit_scenes again. Do not reply with only text; the task is not finished until a
   submit_scenes call succeeds.
""".strip()


def render_paragraphs(
    paragraphs: list[str], *, start_index: int = 0, char_budget: int | None = None
) -> tuple[str, int]:
    """Render paragraphs as ``[P n] text`` lines, optionally truncated.

    Args:
        paragraphs (list[str]): The paragraphs to render.
        start_index (int): The index to label the first one with, so a slice read out
            of the middle of a section keeps the section's own numbering.
        char_budget (int | None): Stop once the rendered text would exceed this many
            characters. At least one paragraph is always rendered. None renders all.

    Returns:
        tuple[str, int]: The rendered text, and how many paragraphs it covers.
    """
    lines: list[str] = []
    used = 0
    for offset, paragraph in enumerate(paragraphs):
        line = f"[P {start_index + offset}] {paragraph}"
        if char_budget is not None and lines and used + len(line) > char_budget:
            break
        lines.append(line)
        used += len(line) + 2
    return "\n\n".join(lines), len(lines)


def _describe_node(node: StructureNode | None, title: str | None) -> str:
    """A short human label for what is being segmented, for the opening prompt."""
    if node is None:
        return title or "this section"
    parts = [node.level_type]
    if node.number:
        parts.append(str(node.number))
    label = " ".join(parts)
    if node.title:
        label = f"{label}: {node.title}"
    return label


def render_segmentation_prompt(
    paragraphs: list[str],
    *,
    node: StructureNode | None = None,
    title: str | None = None,
    book_title: str | None = None,
) -> str:
    """Build the initial user message for one node's segmentation run."""
    rendered, shown = render_paragraphs(paragraphs, char_budget=_INLINE_CHAR_BUDGET)
    total = len(paragraphs)
    where = f' from "{book_title}"' if book_title else ""
    header = (
        f"Split {_describe_node(node, title)}{where} into scenes.\n\n"
        f"It has {total} paragraphs, indexed 0..{total - 1}.\n\n"
    )
    if shown >= total:
        body = f"The full text follows.\n\n{rendered}"
    else:
        body = (
            f"Paragraphs 0..{shown - 1} follow; read paragraphs {shown}..{total - 1} "
            f"with read_paragraphs before you submit — you must account for all "
            f"{total} of them.\n\n{rendered}"
        )
    return header + body + "\n\nThen call submit_scenes with your scene boundaries."


# --------------------------------------------------------------------------- #
# submit_scenes' parameter schema
# --------------------------------------------------------------------------- #
SUBMIT_SCENES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "scenes": {
            "type": "array",
            "description": (
                "The scenes, in reading order, covering every paragraph exactly once."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "start_paragraph": {
                        "type": "integer",
                        "description": "First paragraph (inclusive) of this scene.",
                    },
                    "end_paragraph": {
                        "type": "integer",
                        "description": "Last paragraph (inclusive) of this scene.",
                    },
                },
                "required": ["start_paragraph", "end_paragraph"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["scenes"],
    "additionalProperties": False,
}


# --------------------------------------------------------------------------- #
# Per-node state shared by one run's tools
# --------------------------------------------------------------------------- #
@dataclass
class _SegmentState:
    """Mutable state shared by the tools bound to one segmentation run.

    Never touches ``ReactAgent`` internals — the tools only read ``paragraphs`` and
    write ``result``/``last_errors`` here, so nothing leaks between successive runs on
    the same :class:`EbookSceneSegmentationAgent`.
    """

    paragraphs: list[str]
    node: StructureNode | None = None
    result: SceneSegmentation | None = None
    last_errors: list[str] = field(default_factory=list)
    #: The model's cumulative spend when this run started, so a per-node cost can be
    #: reported even when one model instance is shared across every node of a book.
    cost_before_usd: float = 0.0


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #
class ReadParagraphsTool(Tool):
    """Numbered paragraph text within a range."""

    tool_name: str = "read_paragraphs"
    description: str = (
        "Read the section's paragraphs in a range (inclusive), each prefixed with its "
        f"paragraph index. Capped at {_MAX_READ_PARAGRAPHS} paragraphs per call — "
        "narrow the range if you hit the limit."
    )
    parameters: list[ToolParameter] = [
        ToolParameter(
            param_name="start_paragraph",
            tool_type="number",
            description="First paragraph index to read (inclusive).",
        ),
        ToolParameter(
            param_name="end_paragraph",
            tool_type="number",
            description="Last paragraph index to read (inclusive).",
        ),
    ]
    state: Any = None

    @weave.op
    async def forward(self, start_paragraph: float, end_paragraph: float) -> Any:
        paragraphs = self.state.paragraphs
        start, end = int(start_paragraph), int(end_paragraph)
        last = len(paragraphs) - 1
        if start < 0 or end > last or start > end:
            return ToolResult.error(
                f"Invalid range [{start}, {end}] — this section has "
                f"{len(paragraphs)} paragraphs, indices 0..{last}."
            )
        if end - start + 1 > _MAX_READ_PARAGRAPHS:
            return ToolResult.error(
                f"Range spans {end - start + 1} paragraphs, exceeding the "
                f"{_MAX_READ_PARAGRAPHS}-paragraph cap. Narrow the range and call again."
            )
        rendered, _ = render_paragraphs(paragraphs[start : end + 1], start_index=start)
        return rendered


class SubmitScenesTool(Tool):
    """Validate and accept the final scene boundaries; ends the run on success."""

    tool_name: str = "submit_scenes"
    description: str = (
        "Submit the scene boundaries for this section. Every paragraph must belong to "
        "exactly one scene, with no gaps or overlaps. If the boundaries are invalid "
        "you get back a list of errors to fix — this call does not end the task until "
        "it succeeds."
    )
    parameters: list[ToolParameter] = []
    parameters_schema: dict[str, Any] | None = SUBMIT_SCENES_SCHEMA
    state: Any = None

    @weave.op
    async def forward(self, scenes: list[dict]) -> Any:
        paragraphs = self.state.paragraphs
        errors = validate_scenes(scenes, len(paragraphs))
        if errors:
            self.state.last_errors = errors
            return ToolResult.error(
                "Scenes rejected — fix these and resubmit:\n"
                + "\n".join(f"- {e}" for e in errors)
                + "\n\nCall submit_scenes again with corrected boundaries. Do not reply "
                "with only text — the task is not finished until a call to "
                "submit_scenes succeeds."
            )
        segmentation = build_scenes(scenes, paragraphs, node=self.state.node)
        self.state.result = segmentation
        self.state.last_errors = []
        return ToolResult.from_text(
            f"Scenes accepted: {len(segmentation.scenes)} scene(s) covering all "
            f"{len(paragraphs)} paragraphs.",
            terminate=True,
        )


def _build_tools(state: _SegmentState) -> list[Tool]:
    """Instantiate a fresh set of node-bound tools for one run."""
    return [ReadParagraphsTool(state=state), SubmitScenesTool(state=state)]


# --------------------------------------------------------------------------- #
# The agent
# --------------------------------------------------------------------------- #
class EbookSceneSegmentationAgent:
    """Splits a book's leaf sections into illustratable scenes using a ReAct agent.

    Construct once, then call :meth:`segment_node` per leaf node or
    :meth:`segment_structure` to walk a whole book. Each node gets its own fresh
    :class:`~diorama.core.react.ReactAgent`; the agent object itself holds only
    configuration.
    """

    def __init__(
        self,
        *,
        model: LiteLLMModel | None = None,
        model_id: str | None = None,
        api_key: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        max_iterations: int | None = 30,
        instructions: str | None = None,
        enable_prompt_caching: bool = True,
        weave_project: str | None = None,
        usage_sink: UsageSink | None = None,
        book_id: str | None = None,
        run_id: str | None = None,
    ) -> None:
        """Configure the agent used by every subsequent segmentation call.

        Args:
            model (LiteLLMModel | None): A pre-built model to use for every run (e.g. a
                :class:`~tests.fakes.FakeModel` in tests). Takes precedence over
                ``model_id``. Shared across nodes when segmenting a whole book, so its
                cumulative totals span the book — per-node cost is reported as a
                difference rather than read off the model directly.
            model_id (str | None): litellm model id to build a fresh
                :class:`LiteLLMModel` from, once per node.
            api_key (str | None): Provider credential, when building the model. None
                leaves litellm to read it from the environment.
            temperature (float): Sampling temperature, when building the model.
            max_tokens (int | None): Completion token cap, when building the model.
            max_iterations (int | None): Turn ceiling per node. Lower than the loader's
                — one section's boundaries take far fewer tool calls than a whole
                book's hierarchy.
            instructions (str | None): Extra instructions appended after
                :data:`SCENE_SEGMENTATION_INSTRUCTIONS`.
            enable_prompt_caching (bool): Pass-through to the model wrapper.
            weave_project (str | None): When set, initialise W&B Weave tracing.
            usage_sink (UsageSink | None): Receives one
                :class:`~diorama.models.usage.LLMCallRecord` per LLM call. None records
                nothing.
            book_id (str | None): Stamped onto every emitted record.
            run_id (str | None): Stamped onto every emitted record, grouping one run's
                calls. A whole-book :meth:`segment_structure` pass shares a single run
                id across its nodes, so the book's segmentation reads as one line item.
        """
        self._model = model
        self._model_id = model_id
        self._api_key = api_key
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._max_iterations = max_iterations
        self._instructions = SCENE_SEGMENTATION_INSTRUCTIONS + (
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
        self, paragraphs: list[str], node: StructureNode | None
    ) -> tuple[ReactAgent, _SegmentState]:
        """Assemble the fresh agent/state/tools for one node's run."""
        state = _SegmentState(paragraphs=paragraphs, node=node)

        # Built explicitly (rather than left to ReactAgent) so the same instance can be
        # handed to both the agent and the compactor below — the compactor's
        # summarisation calls then fold into the same cumulative usage/cost totals and
        # reach the same usage sink.
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
        state.cost_before_usd = float(model.cumulative.get("cost_usd", 0.0))
        compactor = ContextCompactor(model, reserve_tokens=_COMPACTION_RESERVE_TOKENS)

        agent = ReactAgent(
            tools=_build_tools(state),
            model=model,
            instructions=self._instructions,
            max_iterations=self._max_iterations,
            compactor=compactor,
            weave_project=self._weave_project,
            # As with the loader: the deliverable is a submitted partition, so a
            # quiet turn means the run is about to end having marked nothing.
            completion_guard=lambda: (
                None
                if state.result is not None
                else (
                    "You haven't submitted any scene boundaries yet. Call "
                    "submit_scenes now with a partition covering every paragraph. "
                    "If a previous submission was rejected, fix the errors it "
                    "reported and submit again."
                )
            ),
        )
        return agent, state

    @staticmethod
    def _finalize(
        agent: ReactAgent, state: _SegmentState, label: str, stop_reason: str
    ) -> SceneSegmentation:
        """Resolve a finished run's state into its segmentation, or raise."""
        if state.result is None:
            reason = (
                "; ".join(state.last_errors)
                if state.last_errors
                else describe_stop_reason(stop_reason)
            )
            raise SceneSegmentationError(
                f"EbookSceneSegmentationAgent did not submit valid scenes for "
                f"'{label}' ({reason})"
            )
        spent = (
            float(agent.model.cumulative.get("cost_usd", 0.0)) - state.cost_before_usd
        )
        state.result.cost_usd = round(max(spent, 0.0), 6)
        return state.result

    async def segment_text(
        self,
        text: str,
        *,
        node: StructureNode | None = None,
        title: str | None = None,
        book_title: str | None = None,
        stream: bool = False,
        console: Any = None,
    ) -> SceneSegmentation:
        """Segment one piece of text into scenes.

        Args:
            text (str): The text to segment — a leaf node's ``text``, or any text whose
                paragraphs are separated by blank lines.
            node (StructureNode | None): The node this text came from; its identifying
                fields are copied onto the result and used in the prompt's label.
            title (str | None): A label for the section, when there is no ``node``.
            book_title (str | None): The book's title, mentioned in the prompt for
                context.
            stream (bool): Render assistant text and tool activity to a Rich console.
            console (Any): Optional Rich ``Console`` for rendered output.

        Returns:
            SceneSegmentation: The scenes, each carrying its verbatim text slice and
                ``cost_usd`` set to this run's spend.

        Raises:
            SceneSegmentationError: If the run ends without valid submitted scenes
                (e.g. it hit ``max_iterations`` or was cancelled).
        """
        paragraphs = split_paragraphs(text)
        if not paragraphs:
            return single_scene(paragraphs, node=node)
        agent, state = self._build_agent(paragraphs, node)
        prompt = render_segmentation_prompt(
            paragraphs, node=node, title=title, book_title=book_title
        )
        result = await agent.run(prompt, stream=stream, console=console)
        return self._finalize(
            agent, state, _describe_node(node, title), result.stop_reason
        )

    async def segment_node(
        self,
        node: StructureNode,
        *,
        book_title: str | None = None,
        min_paragraphs: int = DEFAULT_MIN_PARAGRAPHS,
        stream: bool = False,
        console: Any = None,
    ) -> SceneSegmentation:
        """Segment one leaf :class:`StructureNode` into scenes.

        Args:
            node (StructureNode): The leaf node to segment. Its ``text`` is what gets
                split; a branch node (which carries no text) yields an empty result.
            book_title (str | None): The book's title, mentioned in the prompt.
            min_paragraphs (int): Nodes shorter than this become a single scene with no
                LLM call at all. Pass 0 to always run the agent.
            stream (bool): Render assistant text and tool activity to a Rich console.
            console (Any): Optional Rich ``Console`` for rendered output.

        Returns:
            SceneSegmentation: The node's scenes.

        Raises:
            SceneSegmentationError: If the run ends without valid submitted scenes.
        """
        paragraphs = split_paragraphs(node.text or "")
        if len(paragraphs) < max(min_paragraphs, 1):
            return single_scene(paragraphs, node=node)
        return await self.segment_text(
            node.text or "",
            node=node,
            book_title=book_title,
            stream=stream,
            console=console,
        )

    def stream_segment_node(
        self, node: StructureNode, *, book_title: str | None = None
    ) -> tuple[AsyncIterator[AgentEvent], Callable[[], SceneSegmentation]]:
        """Like :meth:`segment_node`, but exposes the run's live events.

        Mirrors :meth:`~diorama.core.react.ReactAgent.stream_events` paired with
        ``last_result``, exactly as
        :meth:`~diorama.agents.ebook_loader.EbookLoaderAgent.stream_load` does: iterate
        the returned events fully, then call ``finalize()``. Unlike
        :meth:`segment_node` this always runs the agent — a caller that wants the
        short-node shortcut should check the paragraph count itself, since there is no
        event stream to hand back when no run happens.

        Args:
            node (StructureNode): The leaf node to segment.
            book_title (str | None): The book's title, mentioned in the prompt.

        Returns:
            tuple[AsyncIterator[AgentEvent], Callable[[], SceneSegmentation]]: The run's
                event stream, and a callable resolving the segmentation once that stream
                has been fully consumed. The callable raises
                :class:`SceneSegmentationError` if the run ended without valid scenes.

        Raises:
            ValueError: If ``node`` carries no text — there is nothing to stream, and a
                prompt about a section of zero paragraphs is worse than an early failure.
        """
        paragraphs = split_paragraphs(node.text or "")
        if not paragraphs:
            raise ValueError(
                f"{_describe_node(node, None)} has no text to segment into scenes"
            )
        agent, state = self._build_agent(paragraphs, node)
        prompt = render_segmentation_prompt(
            paragraphs, node=node, book_title=book_title
        )
        events = agent.stream_events(prompt, provider_stream=False)

        def finalize() -> SceneSegmentation:
            assert agent.last_result is not None, (
                "finalize() called before the event stream was fully consumed"
            )
            return self._finalize(
                agent,
                state,
                _describe_node(node, None),
                agent.last_result.stop_reason,
            )

        return events, finalize

    async def segment_structure(
        self,
        structure: EbookStructure,
        *,
        min_paragraphs: int = DEFAULT_MIN_PARAGRAPHS,
        stream: bool = False,
        console: Any = None,
    ) -> BookScenes:
        """Segment every leaf of an extracted structure, in reading order.

        One agent run per leaf node, sequentially — the nodes are independent, but a
        book's worth of concurrent LLM calls is a rate-limit problem, not a feature, so
        parallelism is left to a caller that wants it.

        Args:
            structure (EbookStructure): The structure the loader agent produced.
            min_paragraphs (int): Nodes shorter than this become a single scene with no
                LLM call. Pass 0 to always run the agent.
            stream (bool): Render assistant text and tool activity to a Rich console.
            console (Any): Optional Rich ``Console`` for rendered output.

        Returns:
            BookScenes: One :class:`SceneSegmentation` per leaf, plus the total spend.

        Raises:
            SceneSegmentationError: If any node's run ends without valid submitted
                scenes. Nodes already segmented before the failure are lost — a caller
                that wants to salvage a partial pass should drive :meth:`segment_node`
                itself.
        """
        segmentations: list[SceneSegmentation] = []
        for node in iter_leaves(structure):
            segmentations.append(
                await self.segment_node(
                    node,
                    book_title=structure.title,
                    min_paragraphs=min_paragraphs,
                    stream=stream,
                    console=console,
                )
            )
        return BookScenes(
            title=structure.title,
            author=structure.author,
            segmentations=segmentations,
            cost_usd=round(sum(s.cost_usd for s in segmentations), 6),
        )


__all__ = [
    "AGENT_ID",
    "DEFAULT_MIN_PARAGRAPHS",
    "SCENE_SEGMENTATION_INSTRUCTIONS",
    "SUBMIT_SCENES_SCHEMA",
    "EbookSceneSegmentationAgent",
    "ReadParagraphsTool",
    "SceneSegmentationError",
    "SubmitScenesTool",
    "render_paragraphs",
    "render_segmentation_prompt",
]
