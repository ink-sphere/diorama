"""Deterministic paragraph-boundary -> scene slicing and validation.

The scene-segmentation agent (:mod:`diorama.agents.ebook_scene_segmentation`) never
writes book text. It reads a leaf node's text as *numbered paragraphs* and submits a
list of ``[start_paragraph, end_paragraph]`` boundaries; everything here is the pure,
agent-free half that validates those boundaries and reassembles the exact text they
select. This is the same two-phase split :mod:`diorama.ebook.slicer` uses for the
structure tree — the agent proposes boundaries, deterministic code fills in text — and
it is what makes "the core text must not change" a property of the code rather than a
hope about the model's behaviour.

**Round-trip identity.** :func:`split_paragraphs` is a plain ``str.split`` on a blank
line: it never strips, normalises, or drops empties, so
``PARAGRAPH_SEPARATOR.join(split_paragraphs(text)) == text`` holds for *any* input, and
a scene's ``text`` is always a verbatim substring of the node's. A tempting
"smarter" splitter (a ``\\n\\s*\\n`` regex, stripping each paragraph) would quietly
break that guarantee.

Paragraph indices, not block ids, are the coordinate system here — a leaf node's text
is the only thing a scene is defined against, so scenes stay meaningful for any text
handed to :func:`build_scenes`. For text that came from
:func:`diorama.ebook.slicer.build_structure`, the two happen to line up exactly
(``block_id == node.start_block_id + paragraph_index``), because leaf text is
``"\\n\\n".join(block.text)`` and the parser whitespace-collapses every block — but
nothing here depends on that.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from pydantic import BaseModel, Field

from diorama.ebook.models import EbookStructure, StructureNode

#: What :func:`diorama.ebook.slicer.build_structure` joins block text with, and
#: therefore what a leaf node's paragraphs are separated by.
PARAGRAPH_SEPARATOR = "\n\n"


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class Scene(BaseModel):
    """One stretch of a leaf node's text that a single illustration could depict.

    Attributes:
        start_paragraph (int): First paragraph (inclusive) of the node's text.
        end_paragraph (int): Last paragraph (inclusive) of the node's text.
        text (str): The selected paragraphs, joined back with
            :data:`PARAGRAPH_SEPARATOR` — byte-identical to that slice of the node's
            text. Filled in by :func:`build_scenes`, never by the agent.
    """

    start_paragraph: int
    end_paragraph: int
    text: str

    @property
    def paragraph_count(self) -> int:
        """How many paragraphs this scene spans."""
        return self.end_paragraph - self.start_paragraph + 1


class SceneSegmentation(BaseModel):
    """The scenes of one leaf node, in reading order.

    ``scenes`` always partitions the node's paragraphs exactly: the first starts at 0,
    the last ends at ``paragraph_count - 1``, and consecutive scenes are contiguous.
    :func:`validate_scenes` is what enforces that, and :func:`build_scenes` refuses to
    build anything that fails it.

    The node-identifying fields are copied from the :class:`StructureNode` this was
    built for, and are None when :func:`build_scenes` was handed bare text.

    Attributes:
        scenes (list[Scene]): The scenes, in order.
        paragraph_count (int): Total paragraphs in the segmented text.
        start_block_id (int | None): The source node's first block.
        end_block_id (int | None): The source node's last block.
        level_type (str | None): The source node's semantic level, e.g. ``"chapter"``.
        number (str | None): The source node's number/label.
        title (str | None): The source node's title.
        cost_usd (float): LLM spend for the run that produced these boundaries. 0.0
            when they were derived without a model call.
    """

    scenes: list[Scene] = Field(default_factory=list)
    paragraph_count: int = 0
    start_block_id: int | None = None
    end_block_id: int | None = None
    level_type: str | None = None
    number: str | None = None
    title: str | None = None
    cost_usd: float = 0.0


class BookScenes(BaseModel):
    """Every leaf node of one book, segmented into scenes.

    Attributes:
        title (str): Book title, carried over from the structure.
        author (str | None): Book author, if known.
        segmentations (list[SceneSegmentation]): One entry per leaf node, in the same
            depth-first order :func:`iter_leaves` yields (which is the reading order
            the frontend's ``readBook()`` flattening also uses).
        cost_usd (float): Total LLM spend across every segmented node.
    """

    title: str
    author: str | None = None
    segmentations: list[SceneSegmentation] = Field(default_factory=list)
    cost_usd: float = 0.0

    @property
    def scene_count(self) -> int:
        """Total scenes across every node."""
        return sum(len(s.scenes) for s in self.segmentations)


# --------------------------------------------------------------------------- #
# Paragraphs
# --------------------------------------------------------------------------- #
def split_paragraphs(text: str) -> list[str]:
    """Split ``text`` into the paragraphs the agent addresses by index.

    A plain split on a blank line — deliberately not a normalising one, so the result
    always rejoins to exactly ``text`` (see the module docstring).

    Args:
        text (str): A leaf node's text.

    Returns:
        list[str]: The paragraphs, in order. Empty for empty text.
    """
    return text.split(PARAGRAPH_SEPARATOR) if text else []


def join_paragraphs(paragraphs: Iterable[str]) -> str:
    """Inverse of :func:`split_paragraphs`."""
    return PARAGRAPH_SEPARATOR.join(paragraphs)


def iter_leaves(structure: EbookStructure) -> Iterator[StructureNode]:
    """Yield every leaf node of ``structure``, depth-first, in reading order."""

    def walk(nodes: list[StructureNode]) -> Iterator[StructureNode]:
        for node in nodes:
            if node.is_leaf:
                yield node
            else:
                yield from walk(node.children)

    yield from walk(structure.root)


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
def _parse_scene(payload: object, index: int) -> tuple[int, int]:
    """Coerce one submitted scene dict into ``(start, end)``.

    Raises:
        ValueError: If the payload is not a dict of two integer-ish bounds.
    """
    if not isinstance(payload, dict):
        raise ValueError(
            f"scenes[{index}]: expected an object, got {type(payload).__name__}"
        )
    try:
        return int(payload["start_paragraph"]), int(payload["end_paragraph"])
    except (KeyError, TypeError, ValueError) as e:
        raise ValueError(f"scenes[{index}]: malformed scene ({e})") from e


def validate_scenes(scenes: list[dict], paragraph_count: int) -> list[str]:
    """Return human-readable errors, or ``[]`` when ``scenes`` is a valid partition.

    Valid means: at least one scene, every bound within ``0..paragraph_count - 1``,
    ``start <= end``, the first scene starting at 0, the last ending at the final
    paragraph, and each scene starting exactly one paragraph after the previous one
    ends. Anything else leaves paragraphs orphaned or duplicated across illustrations.

    Args:
        scenes (list[dict]): The agent's submitted boundaries.
        paragraph_count (int): Number of paragraphs in the text being segmented.

    Returns:
        list[str]: One message per problem, phrased for the agent to act on.
    """
    if paragraph_count <= 0:
        return ["there is no text to segment (0 paragraphs)"]
    if not isinstance(scenes, list) or not scenes:
        return ["submit at least one scene"]

    errors: list[str] = []
    bounds: list[tuple[int, int]] = []
    for index, payload in enumerate(scenes):
        try:
            bounds.append(_parse_scene(payload, index))
        except ValueError as e:
            errors.append(str(e))
    if errors:
        return errors

    last = paragraph_count - 1
    for index, (start, end) in enumerate(bounds):
        if start > end:
            errors.append(f"scenes[{index}]: start_paragraph > end_paragraph")
        if start < 0 or end > last:
            errors.append(
                f"scenes[{index}]: range [{start}, {end}] out of bounds "
                f"(this text has {paragraph_count} paragraphs, indices 0..{last})"
            )
    if errors:
        return errors

    if bounds[0][0] != 0:
        errors.append(
            f"scenes[0]: must start at paragraph 0, got {bounds[0][0]} — "
            "every paragraph belongs to exactly one scene"
        )
    for index in range(1, len(bounds)):
        expected = bounds[index - 1][1] + 1
        if bounds[index][0] != expected:
            errors.append(
                f"scenes[{index}]: gap/overlap — expected start_paragraph "
                f"{expected}, got {bounds[index][0]}"
            )
    if bounds[-1][1] != last:
        errors.append(
            f"scenes[{len(bounds) - 1}]: must end at paragraph {last} (the last one), "
            f"got {bounds[-1][1]}"
        )
    return errors


# --------------------------------------------------------------------------- #
# Building
# --------------------------------------------------------------------------- #
def build_scenes(
    scenes: list[dict],
    paragraphs: list[str],
    *,
    node: StructureNode | None = None,
) -> SceneSegmentation:
    """Build a :class:`SceneSegmentation` from validated boundaries.

    Args:
        scenes (list[dict]): The agent's submitted boundaries.
        paragraphs (list[str]): The text being segmented, as returned by
            :func:`split_paragraphs`.
        node (StructureNode | None): The leaf node the text came from; its identifying
            fields are copied onto the result.

    Returns:
        SceneSegmentation: The scenes, each carrying its verbatim text slice.

    Raises:
        ValueError: If ``scenes`` fails :func:`validate_scenes`.
    """
    errors = validate_scenes(scenes, len(paragraphs))
    if errors:
        raise ValueError("invalid scene boundaries:\n" + "\n".join(errors))

    built: list[Scene] = []
    for index, payload in enumerate(scenes):
        start, end = _parse_scene(payload, index)
        built.append(
            Scene(
                start_paragraph=start,
                end_paragraph=end,
                text=join_paragraphs(paragraphs[start : end + 1]),
            )
        )
    return SceneSegmentation(
        scenes=built,
        paragraph_count=len(paragraphs),
        start_block_id=node.start_block_id if node else None,
        end_block_id=node.end_block_id if node else None,
        level_type=node.level_type if node else None,
        number=node.number if node else None,
        title=node.title if node else None,
    )


def single_scene(
    paragraphs: list[str], *, node: StructureNode | None = None
) -> SceneSegmentation:
    """The whole text as one scene, without consulting a model.

    Used for nodes too short to be worth an LLM call — asking a model to confirm that
    a two-paragraph node is a single scene costs money to learn nothing.
    """
    if not paragraphs:
        return SceneSegmentation(
            scenes=[],
            paragraph_count=0,
            start_block_id=node.start_block_id if node else None,
            end_block_id=node.end_block_id if node else None,
            level_type=node.level_type if node else None,
            number=node.number if node else None,
            title=node.title if node else None,
        )
    return build_scenes(
        [{"start_paragraph": 0, "end_paragraph": len(paragraphs) - 1}],
        paragraphs,
        node=node,
    )


__all__ = [
    "PARAGRAPH_SEPARATOR",
    "BookScenes",
    "Scene",
    "SceneSegmentation",
    "build_scenes",
    "iter_leaves",
    "join_paragraphs",
    "single_scene",
    "split_paragraphs",
    "validate_scenes",
]
