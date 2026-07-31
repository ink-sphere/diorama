"""LiteraryResearchAgent: the pre-production research pass over a book.

Where :class:`~diorama.agents.ebook_loader.EbookLoaderAgent` maps a book's
hierarchy and :class:`~diorama.agents.ebook_scene_segmentation.EbookSceneSegmentationAgent`
cuts each section into illustratable scenes, this agent answers the questions that
have to be settled *before* a single picture is drawn: who wrote this and when,
what world does it take place in, and what should the pictures look like.

It produces **three artifacts**, submitted in order through three separate tools
(see :mod:`docs/01_literary_research_agent.md` for the reasoning behind the split):

1. :class:`AuthorProfile` — prose for the reader, plus a structured bridge
   (publication year, authorship period, the book's existing visual tradition)
   that the later artifacts are built on.
2. :class:`WorldDossier` — **style-free**. Time periods with their visual markers,
   a location registry, and milieu-level wardrobe, all described in *world* terms
   ("threadbare grey wool coat"), never rendering terms ("soft watercolour wash").
   This is the artifact a re-style must never touch, which is what makes swapping
   the art style cheap instead of a rewrite. :func:`validate_world_dossier`
   enforces the invariant mechanically by rejecting rendering vocabulary.
3. :class:`StyleBibleCandidates` — the swappable half, and always **two
   candidates**: one drawing on the book's own illustration tradition, one
   proposing an original direction. Which becomes active is the *user's* choice,
   not the agent's; the agent's job is to make both cases well. A book with no
   recorded visual tradition has no traditional candidate to make, and only the
   original one is required.

**This agent authors its output rather than pointing at it.** The loader and the
segmenter submit only coordinates, so deterministic code can guarantee the text
never changes; there is no equivalent guarantee available here, because a mood
and a palette are written, not sliced. Validation is therefore the only guard,
and it does the work it can: required prose is checked for substance, hex colours
for well-formedness, cited block ids for existence, and cross-references (a
location's periods, a traditional bible's influences) for consistency.

One check is deliberately **not** an error. :func:`coverage_warnings` notices a
dossier whose every citation falls in the opening third of the book — the shape
of a world built from the first few chapters, which leaves the second half with
nothing to draw from — and appends a note to the acceptance inviting a wider
resubmission. It stays advisory because some books really do establish their
whole world up front, so a front-loaded dossier is suspicious rather than wrong.

**Submission is staged rather than one giant call**, for two reasons: the full
report is a lot of JSON to emit correctly in one shot, and research is designed
to be a *non-fatal* phase — a run that dies after the dossier still leaves two
usable artifacts behind, carried on :attr:`LiteraryResearchError.partial`.

Like the other two agents, each :meth:`LiteraryResearchAgent.research` call builds
a fresh :class:`~diorama.core.react.ReactAgent` bound to that book's own tools and
discards it, so nothing carries between books.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import weave
from pydantic import BaseModel, Field, ValidationError

# The book-reading tools are reused verbatim from the loader rather than
# reimplemented: they depend on nothing but ``state.context``, an EbookContext, so
# any state object exposing that attribute can host them. Keeping one
# implementation means block ids mean exactly the same thing to both agents.
from diorama.agents.ebook_loader import (
    GetOverviewTool,
    GetTocTool,
    ListHeadingsTool,
    ReadBlocksTool,
    SearchBlocksTool,
)
from diorama.core.common_tools import ViewImageTool, WebSearchTool
from diorama.core.context import ContextCompactor
from diorama.core.events import AgentEvent
from diorama.core.react import ReactAgent, describe_stop_reason
from diorama.core.results import ToolResult
from diorama.core.tool import Tool, ToolParameter
from diorama.ebook.models import EbookStructure, StructureNode
from diorama.ebook.parser import EbookContext
from diorama.models.litellm_model import LiteLLMModel
from diorama.models.usage import UsageSink, new_run_id

#: This agent's key in every ledger row it produces (and the key it would take in
#: :data:`diorama.backend.settings.AGENTS` when it becomes configurable). Defined here
#: rather than imported from the backend so the agent package keeps not depending on
#: the web layer.
AGENT_ID = "literary_research"

#: Baseline for an agent constructed without a ``model_id`` (a script, a test).
#: Mirrors the other agents' OpenRouter defaults — the backend resolves per provider
#: and passes an id explicitly, so this is only ever the no-backend fallback.
_DEFAULT_MODEL_ID = "openrouter/google/gemini-3.6-flash"

# Same reserve, for the same reason, as the other two agents: the chars/4 token
# estimate in diorama.core.context undercounts transcripts dense with short bracketed
# markers ("[Block N]"), and a live loader run slipped past the default
# 16_384-token threshold into a provider context-length error.
_COMPACTION_RESERVE_TOKENS = 48_000

#: Which candidate is active until the reader chooses otherwise. "Original" is the
#: provisional default because it is the one candidate that always exists — a book
#: with no illustration tradition has no traditional bible to fall back to — and
#: because Diorama's own voice is the safer thing to ship unasked.
DEFAULT_STYLE_DIRECTION: Literal["original", "traditional"] = "original"

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
#: Minimum word count for the two prose sections. Not a style rule — a floor that
#: catches a placeholder or a one-line stub without dictating length.
_MIN_PROSE_WORDS = 40
_MIN_PALETTE_COLORS = 3
#: The style prompt block is appended verbatim to every image call, so a two-word
#: answer here would quietly hollow out every plate in the book.
_MIN_PROMPT_BLOCK_CHARS = 120
_MAX_OUTLINE_NODES = 400

#: A dossier whose every citation lands inside this opening fraction of the book
#: was almost certainly written from the first few chapters alone.
_COVERAGE_HEAD_FRACTION = 1 / 3
#: Below this many citations there is no cluster to detect — two block ids near
#: the front say nothing about how much of the book was read.
_MIN_CITATIONS_FOR_COVERAGE = 3

#: Phrases that describe *rendering* rather than the world, rejected in the world
#: dossier to keep it style-free. Deliberately narrow: unambiguous art-direction
#: speak only, so a book that genuinely contains an oil painting or a candle-lit
#: room can still say so.
_RENDERING_TERMS: tuple[str, ...] = (
    "art style",
    "artstyle",
    "illustration style",
    "in the style of",
    "rendered in",
    "watercolour wash",
    "watercolor wash",
    "cel-shaded",
    "cel shaded",
    "line art",
    "linework",
    "digital painting",
    "concept art",
    "photorealistic",
    "colour palette",
    "color palette",
    "brushwork",
    "cross-hatching",
)


class LiteraryResearchError(RuntimeError):
    """Raised when a research run ends without all three artifacts submitted.

    Attributes:
        partial (dict[str, Any]): Whatever *was* accepted before the run ended,
            keyed ``author_profile`` / ``world_dossier`` / ``style_bibles``, with
            None for the artifacts never submitted. Research is a non-fatal phase:
            a caller that wants to shelve a book with a half-finished moodboard
            reads this rather than throwing the whole run away.
    """

    def __init__(self, message: str, *, partial: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.partial: dict[str, Any] = partial or {}


# --------------------------------------------------------------------------- #
# Artifact models
# --------------------------------------------------------------------------- #
class Evidence(BaseModel):
    """Where an entry came from.

    Attributes:
        block_ids (list[int]): Blocks of the book that support this entry.
        urls (list[str]): Web sources that support this entry.
    """

    block_ids: list[int] = Field(default_factory=list)
    urls: list[str] = Field(default_factory=list)


class VisualTraditionEntry(BaseModel):
    """One piece of the book's existing visual tradition.

    Recorded as *fact* in the author profile regardless of which style direction
    the reader eventually picks — it is the evidence for the traditional
    candidate, and the list of looks to avoid for the original one.

    Attributes:
        name (str): Who or what, e.g. "John Tenniel", "the 1951 Disney film".
        kind (str): ``illustrator`` | ``edition`` | ``adaptation`` | ``other``.
        medium (str | None): What it was made in, e.g. "wood engraving".
        description (str): What it looks like, in a sentence or two.
        sources (list[str]): URLs backing the entry.
    """

    name: str
    kind: Literal["illustrator", "edition", "adaptation", "other"]
    medium: str | None = None
    description: str
    sources: list[str] = Field(default_factory=list)


class AuthorProfile(BaseModel):
    """The author, their body of work, and this book's place in it.

    ``bio_prose`` is book-independent and cacheable per author; everything else is
    about this particular book. Every web-derived field is nullable, because the
    research must degrade gracefully on a book the web has never heard of.

    Attributes:
        name (str): The author's name.
        birth_date (str | None): Year or full date, as text.
        death_date (str | None): Year or full date, as text. None if living or unknown.
        bio_prose (str): Reader-facing prose about the author and their oeuvre.
        work_context_prose (str): Reader-facing prose about this book specifically.
        publication_year (int | None): First publication year.
        authorship_period (str | None): The era the book was *written* in, which is
            often not the era it is *set* in and usually drives the visual idiom
            readers associate with it.
        composition_context (str | None): Circumstances of writing — serialised,
            written for children, a satire of something contemporary.
        visual_tradition (list[VisualTraditionEntry]): Illustrators, notable
            editions, adaptations. Empty is a normal answer.
        evidence (Evidence): Supporting blocks and URLs.
    """

    name: str
    birth_date: str | None = None
    death_date: str | None = None
    bio_prose: str
    work_context_prose: str
    publication_year: int | None = None
    authorship_period: str | None = None
    composition_context: str | None = None
    visual_tradition: list[VisualTraditionEntry] = Field(default_factory=list)
    evidence: Evidence = Field(default_factory=Evidence)


class VisualMarkers(BaseModel):
    """What a period *looks* like — the fields a picture actually needs.

    ``light_sources`` earns its own field rather than sitting in a general
    description because it changes every plate: candle, oil lamp, gas, and
    electric light are four different pictures of the same room.
    """

    clothing: str | None = None
    technology: str | None = None
    transport: str | None = None
    light_sources: str | None = None
    architecture: str | None = None


class TimePeriod(BaseModel):
    """One time period the book operates in.

    Attributes:
        label (str): Short name, referenced by locations, e.g. "Regency England".
        kind (str): ``story`` (when the book is set) or ``authorship`` (when it was
            written). A book usually has both, and they are frequently different.
        span (str | None): Rough dates, as text.
        summary (str | None): What is going on in this period, in world terms.
        visual_markers (VisualMarkers): The renderable specifics.
        evidence (Evidence): Supporting blocks and URLs.
    """

    label: str
    kind: Literal["story", "authorship"]
    span: str | None = None
    summary: str | None = None
    visual_markers: VisualMarkers = Field(default_factory=VisualMarkers)
    evidence: Evidence = Field(default_factory=Evidence)


class LocationProfile(BaseModel):
    """One place in the book, real or invented.

    Attributes:
        name (str): The place as the book names it.
        existence (str): ``real`` | ``fictional`` | ``real_but_altered``.
        description (str): What happens here and what it is, in world terms.
        visual_notes (str | None): What it looks like — materials, scale, weather,
            light. Still world terms, never rendering terms.
        periods (list[str]): Labels of the :class:`TimePeriod` entries this place
            appears in. Validated against the submitted periods.
        evidence (Evidence): Supporting blocks and URLs. Web research earns its
            keep here: a real place has photographs.
    """

    name: str
    existence: Literal["real", "fictional", "real_but_altered"]
    description: str
    visual_notes: str | None = None
    periods: list[str] = Field(default_factory=list)
    evidence: Evidence = Field(default_factory=Evidence)


class Milieu(BaseModel):
    """A social group and how it dresses.

    Wardrobe is split three ways across the pipeline, and this is the widest of
    the three: *milieu* dress lives here ("what a Victorian governess wears"),
    *character* dress belongs to the casting pass ("what Jane Eyre wears"), and
    *scene* dress belongs to the render pass ("what she wears in this scene").

    Attributes:
        name (str): The group, e.g. "London street children", "naval officers".
        description (str | None): Who they are and where they appear.
        wardrobe (str): What they wear — fabrics, silhouettes, condition, colour
            as the world contains it (not as the picture styles it).
        evidence (Evidence): Supporting blocks and URLs.
    """

    name: str
    description: str | None = None
    wardrobe: str
    evidence: Evidence = Field(default_factory=Evidence)


class WorldDossier(BaseModel):
    """The style-free half of the research: what the world *is*.

    Nothing here may describe how a picture should be made. That separation is
    what makes the art style swappable — re-styling regenerates the style bible
    and leaves this untouched.
    """

    time_periods: list[TimePeriod] = Field(default_factory=list)
    locations: list[LocationProfile] = Field(default_factory=list)
    milieus: list[Milieu] = Field(default_factory=list)


class PaletteColor(BaseModel):
    """One colour of a style bible's palette.

    ``hex`` is required and validated because the moodboard renders real swatches;
    a palette that only names colours ("dusty rose") cannot be shown, only read.

    Attributes:
        name (str): What to call it.
        hex (str): ``#rrggbb``.
        role (str | None): What it is for, e.g. "shadows", "skin", "accent".
    """

    name: str
    hex: str
    role: str | None = None


class StyleBible(BaseModel):
    """One candidate art direction for the whole book.

    One global style per book — mood variation within the book comes from the
    scene text at render time, not from per-chapter modifiers.

    Attributes:
        direction (str): ``traditional`` (drawn from the book's own illustration
            tradition) or ``original`` (Diorama's own proposal).
        name (str): A short name for the direction, e.g. "Sooty Georgian Ink".
        rationale (str): Why this suits *this* book.
        mood_words (list[str]): The mood vocabulary, for the moodboard's
            typographic treatment.
        palette (list[PaletteColor]): Renderable swatches.
        lighting (str): How light behaves in the pictures.
        influences (list[str]): For a traditional candidate, the
            :class:`VisualTraditionEntry` names it draws on. Required there,
            optional for an original one.
        style_prompt_block (str): **The actual influence mechanism.** A canonical
            paragraph of rendering language appended verbatim to every image call.
            Keeping it byte-stable is the cheapest consistency lever across
            hundreds of plates, and swapping the style means regenerating exactly
            this field.
        negative_constraints (list[str]): What must never appear — anachronisms,
            and (for an original direction) the famous adaptation looks to avoid.
    """

    direction: Literal["traditional", "original"]
    name: str
    rationale: str
    mood_words: list[str] = Field(default_factory=list)
    palette: list[PaletteColor] = Field(default_factory=list)
    lighting: str
    influences: list[str] = Field(default_factory=list)
    style_prompt_block: str
    negative_constraints: list[str] = Field(default_factory=list)


class StyleBibleCandidates(BaseModel):
    """The two art directions offered to the reader.

    ``traditional`` is None when the book has no visual tradition worth drawing
    on, which is the normal case for anything recent or obscure.
    """

    original: StyleBible
    traditional: StyleBible | None = None


class LiteraryResearchReport(BaseModel):
    """Everything one research run produced for one book.

    Attributes:
        title (str): The book's title.
        author (str | None): The book's author, from EPUB metadata.
        author_profile (AuthorProfile): The reader-facing profile plus its bridge.
        world_dossier (WorldDossier): The style-free world facts.
        style_bibles (StyleBibleCandidates): Both candidate art directions.
        selected_direction (str): Which candidate is active. Set to
            :data:`DEFAULT_STYLE_DIRECTION` by the agent; the reader changes it
            from the moodboard page, which is a metadata edit rather than a
            re-run.
        cost_usd (float): This run's LLM spend.
    """

    title: str
    author: str | None = None
    author_profile: AuthorProfile
    world_dossier: WorldDossier
    style_bibles: StyleBibleCandidates
    selected_direction: Literal["original", "traditional"] = DEFAULT_STYLE_DIRECTION
    cost_usd: float = 0.0

    @property
    def active_style_bible(self) -> StyleBible:
        """The style bible downstream agents should actually render with.

        Falls back to the original candidate when ``traditional`` is selected but
        absent, so a stale selection can never leave the render stage with no
        style at all.
        """
        if self.selected_direction == "traditional" and self.style_bibles.traditional:
            return self.style_bibles.traditional
        return self.style_bibles.original


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
def _missing_prose(value: Any, label: str, errors: list[str]) -> None:
    """Record an error unless ``value`` is prose of at least a minimal length."""
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{label} is required and must not be empty.")
        return
    words = len(value.split())
    if words < _MIN_PROSE_WORDS:
        errors.append(
            f"{label} is only {words} words — write at least {_MIN_PROSE_WORDS}; "
            "this is prose a reader will see, not a label."
        )


def _check_evidence(
    evidence: Any, label: str, total_blocks: int, errors: list[str]
) -> None:
    """Verify an entry's cited block ids actually exist in the book."""
    if not isinstance(evidence, dict):
        return
    for block_id in evidence.get("block_ids") or []:
        if not isinstance(block_id, int) or not 0 <= block_id < total_blocks:
            errors.append(
                f"{label} cites block {block_id!r}, which is not a block in this "
                f"book (valid ids are 0..{total_blocks - 1})."
            )


def _rendering_terms_in(value: Any) -> list[str]:
    """Return the rendering-vocabulary phrases present in ``value``."""
    if not isinstance(value, str):
        return []
    lowered = value.lower()
    return [term for term in _RENDERING_TERMS if term in lowered]


def validate_author_profile(profile: Any) -> list[str]:
    """Check a submitted author profile, returning human-readable errors.

    Args:
        profile (Any): The raw ``profile`` object from the tool call.

    Returns:
        list[str]: Every problem found. Empty means the profile is acceptable.
    """
    errors: list[str] = []
    if not isinstance(profile, dict):
        return ["profile must be an object."]
    if not str(profile.get("name") or "").strip():
        errors.append("name is required — the author's name, or 'Unknown'.")
    _missing_prose(profile.get("bio_prose"), "bio_prose", errors)
    _missing_prose(profile.get("work_context_prose"), "work_context_prose", errors)

    year = profile.get("publication_year")
    if year is not None and (not isinstance(year, int) or not -3000 < year < 2200):
        errors.append(f"publication_year {year!r} is not a plausible year.")

    for index, entry in enumerate(profile.get("visual_tradition") or []):
        where = f"visual_tradition[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{where} must be an object.")
            continue
        if not str(entry.get("name") or "").strip():
            errors.append(f"{where}.name is required.")
        if entry.get("kind") not in {"illustrator", "edition", "adaptation", "other"}:
            errors.append(
                f"{where}.kind must be one of illustrator, edition, adaptation, other."
            )
        if not str(entry.get("description") or "").strip():
            errors.append(
                f"{where}.description is required — say what it looks like, since "
                "this is what a traditional style direction would be built on."
            )
    return errors


def validate_world_dossier(dossier: Any, *, total_blocks: int) -> list[str]:
    """Check a submitted world dossier, returning human-readable errors.

    Beyond shape checks this enforces the two invariants the dossier exists for:
    it must be **style-free** (no rendering vocabulary anywhere), and its
    cross-references must resolve (a location's ``periods`` must name periods that
    were actually submitted).

    Args:
        dossier (Any): The raw ``dossier`` object from the tool call.
        total_blocks (int): Block count of the book, for evidence range checks.

    Returns:
        list[str]: Every problem found. Empty means the dossier is acceptable.
    """
    errors: list[str] = []
    if not isinstance(dossier, dict):
        return ["dossier must be an object."]

    periods = dossier.get("time_periods") or []
    locations = dossier.get("locations") or []
    milieus = dossier.get("milieus") or []
    if not periods:
        errors.append(
            "time_periods is empty — every book has at least an authorship period."
        )
    if not locations:
        errors.append("locations is empty — record at least the principal setting.")
    if not milieus:
        errors.append(
            "milieus is empty — record at least the social group the main "
            "characters belong to, and what it wears."
        )

    labels: set[str] = set()
    for index, period in enumerate(periods):
        where = f"time_periods[{index}]"
        if not isinstance(period, dict):
            errors.append(f"{where} must be an object.")
            continue
        label = str(period.get("label") or "").strip()
        if not label:
            errors.append(f"{where}.label is required.")
        else:
            labels.add(label)
        if period.get("kind") not in {"story", "authorship"}:
            errors.append(f"{where}.kind must be 'story' or 'authorship'.")
        markers = period.get("visual_markers") or {}
        if isinstance(markers, dict) and not any(
            str(v or "").strip() for v in markers.values()
        ):
            errors.append(
                f"{where}.visual_markers is empty — fill in what can be seen "
                "(clothing, technology, transport, light_sources, architecture); "
                "these are what the pictures are actually built from."
            )
        _check_evidence(period.get("evidence"), where, total_blocks, errors)

    for index, location in enumerate(locations):
        where = f"locations[{index}]"
        if not isinstance(location, dict):
            errors.append(f"{where} must be an object.")
            continue
        if not str(location.get("name") or "").strip():
            errors.append(f"{where}.name is required.")
        if location.get("existence") not in {"real", "fictional", "real_but_altered"}:
            errors.append(
                f"{where}.existence must be one of real, fictional, real_but_altered."
            )
        if not str(location.get("description") or "").strip():
            errors.append(f"{where}.description is required.")
        for period_label in location.get("periods") or []:
            if labels and period_label not in labels:
                errors.append(
                    f"{where}.periods names '{period_label}', which is not one of "
                    f"the time_periods you submitted ({', '.join(sorted(labels))})."
                )
        _check_evidence(location.get("evidence"), where, total_blocks, errors)

    for index, milieu in enumerate(milieus):
        where = f"milieus[{index}]"
        if not isinstance(milieu, dict):
            errors.append(f"{where} must be an object.")
            continue
        if not str(milieu.get("name") or "").strip():
            errors.append(f"{where}.name is required.")
        if not str(milieu.get("wardrobe") or "").strip():
            errors.append(
                f"{where}.wardrobe is required — this entry exists to say what "
                "these people wear."
            )
        _check_evidence(milieu.get("evidence"), where, total_blocks, errors)

    errors.extend(_style_leaks(dossier))
    return errors


def _style_leaks(dossier: dict) -> list[str]:
    """Find rendering vocabulary that has leaked into the style-free dossier."""
    errors: list[str] = []
    scan: list[tuple[str, Any]] = []
    for index, period in enumerate(dossier.get("time_periods") or []):
        if isinstance(period, dict):
            scan.append((f"time_periods[{index}].summary", period.get("summary")))
            markers = period.get("visual_markers")
            if isinstance(markers, dict):
                for key, value in markers.items():
                    scan.append((f"time_periods[{index}].visual_markers.{key}", value))
    for index, location in enumerate(dossier.get("locations") or []):
        if isinstance(location, dict):
            scan.append(
                (f"locations[{index}].description", location.get("description"))
            )
            scan.append(
                (f"locations[{index}].visual_notes", location.get("visual_notes"))
            )
    for index, milieu in enumerate(dossier.get("milieus") or []):
        if isinstance(milieu, dict):
            scan.append((f"milieus[{index}].wardrobe", milieu.get("wardrobe")))
            scan.append((f"milieus[{index}].description", milieu.get("description")))

    for where, value in scan:
        for term in _rendering_terms_in(value):
            errors.append(
                f"{where} contains '{term}', which describes how a picture is made "
                "rather than what the world is like. The dossier must stay "
                "style-free — put rendering language in the style bible instead."
            )
    return errors


def _cited_block_ids(dossier: dict, total_blocks: int) -> list[int]:
    """Every in-range block id the dossier cites, across all three registries."""
    ids: list[int] = []
    for registry in ("time_periods", "locations", "milieus"):
        for entry in dossier.get(registry) or []:
            if not isinstance(entry, dict):
                continue
            evidence = entry.get("evidence")
            if not isinstance(evidence, dict):
                continue
            for block_id in evidence.get("block_ids") or []:
                if isinstance(block_id, int) and 0 <= block_id < total_blocks:
                    ids.append(block_id)
    return ids


def coverage_warnings(dossier: Any, *, total_blocks: int) -> list[str]:
    """Advisory notes about how much of the book a dossier's evidence covers.

    Deliberately **not** part of :func:`validate_world_dossier`, because these are
    not errors: a few books really do establish their whole world in the opening
    chapters, so front-loaded evidence is suspicious rather than wrong. The
    submission is accepted either way and the note rides along with the
    acceptance, inviting a resubmission rather than demanding one — the agent can
    always overwrite an accepted dossier by calling the tool again.

    Args:
        dossier (Any): The raw ``dossier`` object from the tool call.
        total_blocks (int): Block count of the book.

    Returns:
        list[str]: Notes to append to the acceptance. Empty means nothing to say.
    """
    if not isinstance(dossier, dict) or total_blocks <= 0:
        return []
    cited = _cited_block_ids(dossier, total_blocks)
    # With almost no citations there is no distribution to judge; a dossier that
    # cites nothing at all makes no coverage claim to contradict.
    if len(cited) < _MIN_CITATIONS_FOR_COVERAGE:
        return []
    head_end = int(total_blocks * _COVERAGE_HEAD_FRACTION)
    if max(cited) >= head_end:
        return []
    return [
        f"Every block you cited falls in blocks {min(cited)}-{max(cited)}, inside "
        f"the first third of the book (0..{head_end - 1} of 0..{total_blocks - 1}). "
        "If you have not looked at the middle and late chapters yet, do that now "
        "and call submit_world_dossier again with what you find. A book's world "
        "usually widens as it goes — new places, a jump in time, characters who "
        "change station and so change dress — and anything that arrivesf late "
        "would otherwise be missing from every picture in the second half."
    ]


def _validate_one_bible(
    bible: Any, expected_direction: str, tradition_names: list[str]
) -> list[str]:
    """Check a single style-bible candidate."""
    errors: list[str] = []
    where = f"{expected_direction} candidate"
    if not isinstance(bible, dict):
        return [f"{where} must be an object."]

    direction = bible.get("direction")
    if direction != expected_direction:
        errors.append(
            f"{where}.direction is {direction!r} but must be "
            f"'{expected_direction}' — it names the slot it was submitted in."
        )
    if not str(bible.get("name") or "").strip():
        errors.append(f"{where}.name is required — give the direction a short name.")
    if not str(bible.get("rationale") or "").strip():
        errors.append(f"{where}.rationale is required.")
    if not str(bible.get("lighting") or "").strip():
        errors.append(f"{where}.lighting is required.")
    if not (bible.get("mood_words") or []):
        errors.append(f"{where}.mood_words is empty — give at least a few.")

    palette = bible.get("palette") or []
    if len(palette) < _MIN_PALETTE_COLORS:
        errors.append(
            f"{where}.palette has {len(palette)} colour(s); give at least "
            f"{_MIN_PALETTE_COLORS} — the moodboard renders these as real swatches."
        )
    for index, colour in enumerate(palette):
        if not isinstance(colour, dict):
            errors.append(f"{where}.palette[{index}] must be an object.")
            continue
        if not str(colour.get("name") or "").strip():
            errors.append(f"{where}.palette[{index}].name is required.")
        if not _HEX_RE.match(str(colour.get("hex") or "")):
            errors.append(
                f"{where}.palette[{index}].hex is {colour.get('hex')!r}; it must be "
                "a '#rrggbb' hex colour so it can be drawn."
            )

    block = str(bible.get("style_prompt_block") or "").strip()
    if len(block) < _MIN_PROMPT_BLOCK_CHARS:
        errors.append(
            f"{where}.style_prompt_block is {len(block)} characters; write at least "
            f"{_MIN_PROMPT_BLOCK_CHARS}. This paragraph is appended verbatim to "
            "every image the book will ever generate."
        )
    if not (bible.get("negative_constraints") or []):
        errors.append(
            f"{where}.negative_constraints is empty — at minimum, list the "
            "anachronisms that must never appear."
        )

    influences = [
        str(i).strip() for i in (bible.get("influences") or []) if str(i).strip()
    ]
    if expected_direction == "traditional" and not influences:
        errors.append(
            "traditional candidate.influences is empty — a traditional direction "
            "must say which recorded visual tradition it draws on "
            f"({', '.join(tradition_names) or 'none were recorded'})."
        )
    return errors


def validate_style_bibles(
    original: Any, traditional: Any, *, tradition_names: list[str]
) -> list[str]:
    """Check the submitted style-bible candidates.

    Args:
        original (Any): The required original-direction candidate.
        traditional (Any): The tradition-informed candidate, or None.
        tradition_names (list[str]): Names recorded in the author profile's
            ``visual_tradition``. A non-empty list makes the traditional candidate
            mandatory — the reader is owed the choice whenever there is a real
            tradition to choose.

    Returns:
        list[str]: Every problem found. Empty means the candidates are acceptable.
    """
    errors = _validate_one_bible(original, "original", tradition_names)
    if traditional is None:
        if tradition_names:
            errors.append(
                "A traditional candidate is required: the author profile recorded a "
                f"visual tradition ({', '.join(tradition_names)}), so the reader must "
                "be offered a direction built on it as well as an original one."
            )
    else:
        errors.extend(_validate_one_bible(traditional, "traditional", tradition_names))
    return errors


# --------------------------------------------------------------------------- #
# Prompting
# --------------------------------------------------------------------------- #
LITERARY_RESEARCH_INSTRUCTIONS = """
You are the research pass that happens before a book is illustrated. Nothing has
been drawn yet. Your findings decide what every picture in this book will look
like, so they must be specific and visual rather than literary-critical.

The book has been flattened into numbered "blocks" (paragraphs, headings, list
items, in reading order). Block ids are your coordinate system: whenever you record
something you learned from the text, cite the block ids it came from.

Tools:
- get_overview / get_toc / list_headings / get_outline: orient yourself in the book.
- read_blocks(start_block_id, end_block_id): read the text of a range.
- search_blocks(query, regex=False): find blocks by substring or regex.
- web_search(query, num_results): research the author, the book, real places, and
  the book's illustration history.
- view_image(url): look at an image you found through web_search — an original
  plate, a period photograph, a film still. Prefer seeing a thing to reading a
  description of it.
- submit_author_profile, submit_world_dossier, submit_style_bibles: your three
  deliverables, submitted in that order.

How to read: do NOT read the whole book. Read the opening, then sample — use
list_headings and get_outline to jump around, and search_blocks for the concrete
things you need (place names, times of day, clothing, weather, light, vehicles,
meals). Sample the beginning, the middle AND the late chapters. A book's world
almost always widens as it goes: new places, a jump in time, characters who
change station and therefore change dress. A dossier built from the opening
chapters alone leaves the whole second half of the book with nothing to draw
from. You are building a picture of the world, not a summary of the plot.

The web is enrichment, never a requirement. If searches come back empty or the
tool reports no key, work from the text alone and leave web-only fields null.
Never invent a birth date, a publication year, or an illustrator. Null is a
correct answer; a plausible guess is not.

Write everything spoiler-free. The reader sees this before and during reading.

=== 1. submit_author_profile ===
Two pieces of prose, in different registers:
- bio_prose: the dust-jacket flap. Who the author was, their era, their recurring
  preoccupations, how their work is usually characterised. About this author in
  general, NOT about this book.
- work_context_prose: this book's story-behind-the-story. When and how it was
  written, how it was first published, where it sits in the author's work.
Then the structured fields. authorship_period (when it was WRITTEN) matters
independently of when the story is SET — they are often centuries apart, and the
look readers associate with a book usually comes from the authorship side.
visual_tradition records the book's existing illustration history: original
illustrators, notable illustrated editions, well-known adaptations. Search for it,
and use view_image on what you find — a plate you have looked at is described far
better than one you have only read about. An empty list is fine for a book that
has none.

=== 2. submit_world_dossier ===
This artifact must be STYLE-FREE. Describe what the world *is*, never how a
picture of it should be made. "A threadbare grey wool coat, patched at the elbows"
belongs here. "Rendered in soft watercolour" does not, and will be rejected. This
separation is what lets the art style be changed later without redoing the world.

- time_periods: at minimum the story period and the authorship period. Fill in
  visual_markers — clothing silhouettes, technology, transport, light sources,
  architecture. Light sources matter more than they look: candle, oil lamp, gas
  and electric light are four different pictures of the same room.
- locations: every place that matters, real or invented. For real places, search
  the web — what a real street actually looked like in that decade is knowable.
- milieus: social groups and what they wear, NOT individual characters. "London
  crossing-sweepers", "country gentry", "naval officers". Someone else will dress
  the individual characters; you establish what their world dresses like.

Cite block ids from across the whole book in `evidence`, not only from the
opening. Your citations are the record of how much of the book you actually
looked at, and they are checked.

=== 3. submit_style_bibles ===
Two candidate art directions. The reader picks between them; your job is to make
both cases honestly, not to pick a winner.
- original: your own proposal, suited to this book's period, mood, and subject.
  Its negative_constraints should include the famous adaptation looks to steer
  away from, so it stays genuinely its own.
- traditional: built on the book's recorded visual tradition, naming in
  `influences` which entries it draws on. Required whenever you recorded any
  visual tradition; submit only `original` when you recorded none. Look at the
  actual plates with view_image before writing this one — a style bible written
  from a caption describes the subject of a picture rather than its technique,
  and technique is the whole artifact.

For each: a named direction with a rationale, mood words, a palette of at least
three colours WITH #rrggbb hex values (they are rendered as real swatches), how
light behaves, and negative constraints.

style_prompt_block is the important one. It is a single paragraph of rendering
language that will be appended, word for word and unchanged, to every image
generated for this book — hundreds of them. Write it as instructions to an image
model: medium, technique, palette, light, level of detail, composition habits. It
must describe STYLE ONLY. Do not name characters, places, or events in it, and do
not include anything specific to one scene.

Submissions are validated. A rejection comes back with the exact problems — fix
them and call the tool again. Do not reply with only text; the task is not
finished until all three submissions have been accepted.
""".strip()


def render_research_prompt(context: EbookContext, *, has_outline: bool = False) -> str:
    """Build the initial user message for a research run."""
    by_author = f" by {context.author}" if context.author else ""
    outline = (
        " The structure discovered by an earlier pass is available via get_outline."
        if has_outline
        else ""
    )
    return (
        f'Research the book "{context.title}"{by_author} so it can be illustrated.\n\n'
        f"It has been flattened into {context.total_blocks} numbered blocks, indexed "
        f"0..{context.total_blocks - 1}.{outline}\n\n"
        "Start with get_overview, then sample the text and search the web. Deliver "
        "submit_author_profile, then submit_world_dossier, then submit_style_bibles."
    )


# --------------------------------------------------------------------------- #
# Submit-tool parameter schemas
# --------------------------------------------------------------------------- #
_EVIDENCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "Where this entry came from.",
    "properties": {
        "block_ids": {
            "type": "array",
            "items": {"type": "integer"},
            "description": "Block ids in this book that support the entry.",
        },
        "urls": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Web sources that support the entry.",
        },
    },
    "additionalProperties": False,
}

AUTHOR_PROFILE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "profile": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "The author's name."},
                "birth_date": {
                    "type": ["string", "null"],
                    "description": "Year or full date, as text. Null if unknown.",
                },
                "death_date": {
                    "type": ["string", "null"],
                    "description": "Year or full date. Null if living or unknown.",
                },
                "bio_prose": {
                    "type": "string",
                    "description": (
                        "Reader-facing prose about the author and their body of "
                        "work overall — not about this book. Roughly 100-200 words."
                    ),
                },
                "work_context_prose": {
                    "type": "string",
                    "description": (
                        "Reader-facing prose about this book specifically: how and "
                        "when it was written and published, and its place in the "
                        "author's work. Spoiler-free."
                    ),
                },
                "publication_year": {
                    "type": ["integer", "null"],
                    "description": "First publication year. Null if unknown.",
                },
                "authorship_period": {
                    "type": ["string", "null"],
                    "description": "The era the book was written in, e.g. 'late Victorian'.",
                },
                "composition_context": {
                    "type": ["string", "null"],
                    "description": (
                        "Circumstances of writing — serialised, written for "
                        "children, a response to contemporary events."
                    ),
                },
                "visual_tradition": {
                    "type": "array",
                    "description": (
                        "The book's existing illustration history. Empty is fine "
                        "for a book that has none."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "description": "e.g. 'John Tenniel', 'the 1951 Disney film'.",
                            },
                            "kind": {
                                "type": "string",
                                "enum": [
                                    "illustrator",
                                    "edition",
                                    "adaptation",
                                    "other",
                                ],
                                "description": "What sort of visual tradition this is.",
                            },
                            "medium": {
                                "type": ["string", "null"],
                                "description": "e.g. 'wood engraving', 'cel animation'.",
                            },
                            "description": {
                                "type": "string",
                                "description": "What it actually looks like.",
                            },
                            "sources": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "URLs backing this entry.",
                            },
                        },
                        "required": ["name", "kind", "description"],
                        "additionalProperties": False,
                    },
                },
                "evidence": _EVIDENCE_SCHEMA,
            },
            "required": ["name", "bio_prose", "work_context_prose"],
            "additionalProperties": False,
        }
    },
    "required": ["profile"],
    "additionalProperties": False,
}

WORLD_DOSSIER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "dossier": {
            "type": "object",
            "properties": {
                "time_periods": {
                    "type": "array",
                    "description": (
                        "The periods this book operates in — at minimum the story "
                        "period and the authorship period."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {
                                "type": "string",
                                "description": (
                                    "Short name, referenced by locations, e.g. "
                                    "'Regency England'."
                                ),
                            },
                            "kind": {
                                "type": "string",
                                "enum": ["story", "authorship"],
                                "description": (
                                    "'story' = when the book is set; 'authorship' = "
                                    "when it was written."
                                ),
                            },
                            "span": {
                                "type": ["string", "null"],
                                "description": "Rough dates, as text.",
                            },
                            "summary": {
                                "type": ["string", "null"],
                                "description": "What this period is like, in world terms.",
                            },
                            "visual_markers": {
                                "type": "object",
                                "description": "What can actually be seen in this period.",
                                "properties": {
                                    "clothing": {"type": ["string", "null"]},
                                    "technology": {"type": ["string", "null"]},
                                    "transport": {"type": ["string", "null"]},
                                    "light_sources": {
                                        "type": ["string", "null"],
                                        "description": (
                                            "Candle, oil lamp, gas, electric — this "
                                            "changes every picture."
                                        ),
                                    },
                                    "architecture": {"type": ["string", "null"]},
                                },
                                "additionalProperties": False,
                            },
                            "evidence": _EVIDENCE_SCHEMA,
                        },
                        "required": ["label", "kind"],
                        "additionalProperties": False,
                    },
                },
                "locations": {
                    "type": "array",
                    "description": "Every place that matters, real or invented.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "description": "The place as the book names it.",
                            },
                            "existence": {
                                "type": "string",
                                "enum": ["real", "fictional", "real_but_altered"],
                                "description": "Whether the place exists outside the book.",
                            },
                            "description": {
                                "type": "string",
                                "description": "What it is and what happens there.",
                            },
                            "visual_notes": {
                                "type": ["string", "null"],
                                "description": (
                                    "What it looks like — materials, scale, weather, "
                                    "light. World terms only."
                                ),
                            },
                            "periods": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": (
                                    "Labels of the time_periods this place appears in."
                                ),
                            },
                            "evidence": _EVIDENCE_SCHEMA,
                        },
                        "required": ["name", "existence", "description"],
                        "additionalProperties": False,
                    },
                },
                "milieus": {
                    "type": "array",
                    "description": (
                        "Social groups and what they wear. Groups, never individual "
                        "characters."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "description": "e.g. 'London crossing-sweepers'.",
                            },
                            "description": {
                                "type": ["string", "null"],
                                "description": "Who they are and where they appear.",
                            },
                            "wardrobe": {
                                "type": "string",
                                "description": (
                                    "Fabrics, silhouettes, condition, colour as the "
                                    "world contains it."
                                ),
                            },
                            "evidence": _EVIDENCE_SCHEMA,
                        },
                        "required": ["name", "wardrobe"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["time_periods", "locations", "milieus"],
            "additionalProperties": False,
        }
    },
    "required": ["dossier"],
    "additionalProperties": False,
}


def _style_bible_schema(direction: str) -> dict[str, Any]:
    """Build the schema for one style-bible candidate slot."""
    return {
        "type": "object",
        "properties": {
            "direction": {
                "type": "string",
                "enum": [direction],
                "description": f"Must be '{direction}'.",
            },
            "name": {
                "type": "string",
                "description": "A short name for this art direction.",
            },
            "rationale": {
                "type": "string",
                "description": "Why this direction suits this book.",
            },
            "mood_words": {
                "type": "array",
                "items": {"type": "string"},
                "description": "The mood vocabulary of the pictures.",
            },
            "palette": {
                "type": "array",
                "description": (
                    f"At least {_MIN_PALETTE_COLORS} colours, rendered as real swatches."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "What to call it."},
                        "hex": {
                            "type": "string",
                            "description": "'#rrggbb' — required, and validated.",
                        },
                        "role": {
                            "type": ["string", "null"],
                            "description": "e.g. 'shadows', 'skin', 'accent'.",
                        },
                    },
                    "required": ["name", "hex"],
                    "additionalProperties": False,
                },
            },
            "lighting": {
                "type": "string",
                "description": "How light behaves in these pictures.",
            },
            "influences": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Names from the author profile's visual_tradition that this "
                    "direction draws on. Required for the traditional candidate."
                ),
            },
            "style_prompt_block": {
                "type": "string",
                "description": (
                    "One paragraph of rendering language, appended verbatim to every "
                    "image call for this book. Style only — no characters, places, "
                    "or events."
                ),
            },
            "negative_constraints": {
                "type": "array",
                "items": {"type": "string"},
                "description": "What must never appear: anachronisms, looks to avoid.",
            },
        },
        "required": [
            "direction",
            "name",
            "rationale",
            "lighting",
            "style_prompt_block",
        ],
        "additionalProperties": False,
    }


STYLE_BIBLES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "original": _style_bible_schema("original"),
        "traditional": {
            **_style_bible_schema("traditional"),
            "description": (
                "The tradition-informed candidate. Required whenever the author "
                "profile recorded any visual tradition; omit it only when none was."
            ),
        },
    },
    "required": ["original"],
    "additionalProperties": False,
}


# --------------------------------------------------------------------------- #
# Per-book state shared by one run's tools
# --------------------------------------------------------------------------- #
@dataclass
class _ResearchState:
    """Mutable state shared by the tools bound to one research run.

    Exposes ``context`` under exactly the name the loader's reading tools expect,
    which is what lets those tools be reused here unchanged. Never touches
    ``ReactAgent`` internals, so nothing leaks between successive runs on the same
    :class:`LiteraryResearchAgent`.
    """

    context: EbookContext
    structure: EbookStructure | None = None
    author_profile: AuthorProfile | None = None
    world_dossier: WorldDossier | None = None
    style_bibles: StyleBibleCandidates | None = None
    last_errors: list[str] = field(default_factory=list)
    #: The model's cumulative spend when this run started, so a run's cost is right
    #: even when one model instance is shared across several books.
    cost_before_usd: float = 0.0

    @property
    def is_complete(self) -> bool:
        """Whether all three artifacts have been accepted."""
        return (
            self.author_profile is not None
            and self.world_dossier is not None
            and self.style_bibles is not None
        )

    def missing(self) -> list[str]:
        """Names of the artifacts still outstanding, in submission order."""
        outstanding = []
        if self.author_profile is None:
            outstanding.append("submit_author_profile")
        if self.world_dossier is None:
            outstanding.append("submit_world_dossier")
        if self.style_bibles is None:
            outstanding.append("submit_style_bibles")
        return outstanding

    def partial(self) -> dict[str, Any]:
        """The artifacts accepted so far, for a run that failed before finishing."""
        return {
            "author_profile": self.author_profile,
            "world_dossier": self.world_dossier,
            "style_bibles": self.style_bibles,
        }


def _research_nudge(state: _ResearchState) -> str | None:
    """What to tell a run that has gone quiet with artifacts still outstanding.

    None once all three are in, which lets the run settle normally. Names the tools
    rather than describing them, since the fix is a specific call — and mentions the
    accepted ones so the model doesn't restart work already banked.
    """
    outstanding = state.missing()
    if not outstanding:
        return None
    done = [
        name
        for name, value in (
            ("submit_author_profile", state.author_profile),
            ("submit_world_dossier", state.world_dossier),
            ("submit_style_bibles", state.style_bibles),
        )
        if value is not None
    ]
    banked = (
        f" You have already banked {', '.join(done)}, so don't redo that work."
        if done
        else ""
    )
    return (
        f"You haven't called {', '.join(outstanding)} yet, and nothing is saved until "
        f"you do.{banked} Continue now with the next outstanding submission. If a "
        "previous attempt was rejected, fix the errors it reported and submit again."
    )


def _rejection(errors: list[str], tool_name: str) -> ToolResult:
    """Build the standard 'fix these and resubmit' failure result."""
    return ToolResult.error(
        "Submission rejected — fix these and resubmit:\n"
        + "\n".join(f"- {e}" for e in errors)
        + f"\n\nCall {tool_name} again with the corrections. Do not reply with only "
        "text — the task is not finished until every submission has been accepted."
    )


def _accepted(state: _ResearchState, summary: str) -> ToolResult:
    """Accept one artifact, terminating the run only once all three are in."""
    if state.is_complete:
        return ToolResult.from_text(
            f"{summary} All three artifacts are complete.", terminate=True
        )
    return ToolResult.from_text(f"{summary} Still to do: {', '.join(state.missing())}.")


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #
class GetOutlineTool(Tool):
    """The book's discovered hierarchy, when an earlier pass extracted one."""

    tool_name: str = "get_outline"
    description: str = (
        "Get the book's hierarchical outline (levels, titles, and block ranges) as "
        "extracted by the structure pass. The fastest way to decide which parts of "
        "the book to sample."
    )
    parameters: list[ToolParameter] = []
    state: Any = None

    @weave.op
    async def forward(self) -> Any:
        structure = self.state.structure
        if structure is None:
            return ToolResult.from_text(
                "No outline is available for this book — use get_toc and "
                "list_headings to orient yourself instead."
            )
        lines: list[str] = []

        def walk(node: StructureNode, depth: int) -> None:
            if len(lines) >= _MAX_OUTLINE_NODES:
                return
            label = " ".join(str(p) for p in (node.level_type, node.number) if p)
            if node.title:
                label = f"{label}: {node.title}"
            lines.append(
                f"{'  ' * depth}- {label} [blocks {node.start_block_id}"
                f"-{node.end_block_id}]"
            )
            for child in node.children:
                walk(child, depth + 1)

        for root in structure.root:
            walk(root, 0)
        if len(lines) >= _MAX_OUTLINE_NODES:
            lines.append(f"… truncated at {_MAX_OUTLINE_NODES} nodes.")
        return "\n".join(lines)


class SubmitAuthorProfileTool(Tool):
    """Validate and accept the author profile."""

    tool_name: str = "submit_author_profile"
    description: str = (
        "Submit the literary profile of the author: reader-facing prose about the "
        "author and about this book, plus publication facts and the book's existing "
        "visual tradition. Submit this first — the style bibles are built on it."
    )
    parameters: list[ToolParameter] = []
    parameters_schema: dict[str, Any] | None = AUTHOR_PROFILE_SCHEMA
    state: Any = None

    @weave.op
    async def forward(self, profile: dict) -> Any:
        errors = validate_author_profile(profile)
        if errors:
            self.state.last_errors = errors
            return _rejection(errors, self.tool_name)
        try:
            parsed = AuthorProfile.model_validate(profile)
        except ValidationError as e:
            self.state.last_errors = [str(e)]
            return _rejection([str(e)], self.tool_name)
        self.state.author_profile = parsed
        self.state.last_errors = []
        traditions = len(parsed.visual_tradition)
        return _accepted(
            self.state,
            f"Author profile accepted for {parsed.name} "
            f"({traditions} visual-tradition entr{'y' if traditions == 1 else 'ies'}).",
        )


class SubmitWorldDossierTool(Tool):
    """Validate and accept the style-free world dossier."""

    tool_name: str = "submit_world_dossier"
    description: str = (
        "Submit the world dossier: time periods with their visual markers, the "
        "location registry, and milieu-level wardrobe. Must be style-free — describe "
        "what the world is, never how a picture of it should be rendered."
    )
    parameters: list[ToolParameter] = []
    parameters_schema: dict[str, Any] | None = WORLD_DOSSIER_SCHEMA
    state: Any = None

    @weave.op
    async def forward(self, dossier: dict) -> Any:
        total_blocks = self.state.context.total_blocks
        errors = validate_world_dossier(dossier, total_blocks=total_blocks)
        if errors:
            self.state.last_errors = errors
            return _rejection(errors, self.tool_name)
        try:
            parsed = WorldDossier.model_validate(dossier)
        except ValidationError as e:
            self.state.last_errors = [str(e)]
            return _rejection([str(e)], self.tool_name)
        self.state.world_dossier = parsed
        self.state.last_errors = []
        accepted = _accepted(
            self.state,
            f"World dossier accepted: {len(parsed.time_periods)} period(s), "
            f"{len(parsed.locations)} location(s), {len(parsed.milieus)} milieu(s).",
        )
        # Coverage notes are advisory, so they are appended *after* acceptance
        # rather than folded into the summary: the dossier is in, and this is a
        # nudge back into the text the agent may act on by resubmitting.
        warnings = coverage_warnings(dossier, total_blocks=total_blocks)
        if not warnings:
            return accepted
        return ToolResult.from_text(
            accepted.text + "\n\nNote: " + "\n".join(warnings),
            terminate=accepted.terminate,
        )


class SubmitStyleBiblesTool(Tool):
    """Validate and accept both candidate art directions."""

    tool_name: str = "submit_style_bibles"
    description: str = (
        "Submit the candidate art directions the reader will choose between: an "
        "original one (always) and a tradition-informed one (whenever the author "
        "profile recorded a visual tradition). Submit this last — it depends on the "
        "author profile and the world dossier."
    )
    parameters: list[ToolParameter] = []
    parameters_schema: dict[str, Any] | None = STYLE_BIBLES_SCHEMA
    state: Any = None

    @weave.op
    async def forward(self, original: dict, traditional: dict | None = None) -> Any:
        state = self.state
        # Ordering is a real dependency, not bookkeeping: the traditional candidate
        # is validated against the profile's recorded tradition, and both candidates
        # are supposed to be derived from the dossier's periods and settings.
        if state.author_profile is None or state.world_dossier is None:
            missing = [
                name
                for name, value in (
                    ("submit_author_profile", state.author_profile),
                    ("submit_world_dossier", state.world_dossier),
                )
                if value is None
            ]
            return ToolResult.error(
                "Style bibles must come last — call "
                + " and ".join(missing)
                + " first, then submit these. The style direction is built on the "
                "book's recorded visual tradition and its world periods."
            )

        tradition_names = [t.name for t in state.author_profile.visual_tradition]
        errors = validate_style_bibles(
            original, traditional, tradition_names=tradition_names
        )
        if errors:
            state.last_errors = errors
            return _rejection(errors, self.tool_name)
        try:
            parsed = StyleBibleCandidates.model_validate(
                {"original": original, "traditional": traditional}
            )
        except ValidationError as e:
            state.last_errors = [str(e)]
            return _rejection([str(e)], self.tool_name)
        state.style_bibles = parsed
        state.last_errors = []
        offered = "original and traditional" if parsed.traditional else "original only"
        return _accepted(
            state,
            f"Style bibles accepted ({offered}); '{parsed.original.name}' is the "
            "provisional direction until the reader chooses.",
        )


def _build_tools(
    state: _ResearchState,
    search: WebSearchTool | None,
    viewer: ViewImageTool | None,
) -> list[Tool]:
    """Instantiate a fresh set of book-bound tools for one research run."""
    return [
        GetOverviewTool(state=state),
        GetTocTool(state=state),
        ListHeadingsTool(state=state),
        GetOutlineTool(state=state),
        ReadBlocksTool(state=state),
        SearchBlocksTool(state=state),
        # Search is always exposed, even with no key configured: the tool reports
        # its own unavailability as a normal tool result, which the instructions
        # tell the agent to treat as "work from the text alone". Hiding it would
        # instead leave the model with no way to discover that the web simply is
        # not on the table.
        search or WebSearchTool(),
        viewer or ViewImageTool(),
        SubmitAuthorProfileTool(state=state),
        SubmitWorldDossierTool(state=state),
        SubmitStyleBiblesTool(state=state),
    ]


# --------------------------------------------------------------------------- #
# The agent
# --------------------------------------------------------------------------- #
class LiteraryResearchAgent:
    """Researches a book's literary background, world, and art direction.

    Construct once and call :meth:`research` per book. Each call parses the EPUB,
    builds a fresh :class:`~diorama.core.react.ReactAgent` bound to that book's
    tools, runs it, and returns the :class:`LiteraryResearchReport` assembled from
    the three artifacts the agent submitted.
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
        search_tool: WebSearchTool | None = None,
        view_image_tool: ViewImageTool | None = None,
    ) -> None:
        """Configure the agent used by every subsequent :meth:`research` call.

        Args:
            model (LiteLLMModel | None): A pre-built model to use for every run
                (e.g. a :class:`~tests.fakes.FakeModel` in tests). Takes precedence
                over ``model_id``.
            model_id (str | None): litellm model id to build a fresh
                :class:`LiteLLMModel` from.
            api_key (str | None): Provider credential, when building the model.
                None leaves litellm to read it from the environment.
            temperature (float): Sampling temperature, when building the model.
            max_tokens (int | None): Completion token cap, when building the model.
            max_iterations (int | None): Turn ceiling per run. Research is
                search-heavy and submits three separate artifacts, so it needs
                roughly the loader's headroom rather than the segmenter's.
            instructions (str | None): Extra instructions appended after
                :data:`LITERARY_RESEARCH_INSTRUCTIONS`.
            enable_prompt_caching (bool): Pass-through to the model wrapper.
            weave_project (str | None): When set, initialise W&B Weave tracing.
            usage_sink (UsageSink | None): Receives one
                :class:`~diorama.models.usage.LLMCallRecord` per LLM call. None
                records nothing.
            book_id (str | None): Stamped onto every emitted record.
            run_id (str | None): Stamped onto every emitted record, grouping one
                run's calls.
            search_tool (WebSearchTool | None): The web-search tool to bind. None
                builds a default one, which auto-detects Exa or Tavily from the
                environment and degrades to reporting itself unavailable.
            view_image_tool (ViewImageTool | None): The image-fetching tool to
                bind, letting the agent look at the illustrations and adaptations
                it finds rather than only reading about them. None builds a
                default one. Note this tool needs a **vision-capable** model:
                every provider default is one, but a text-only model would fail
                on the fetched image rather than degrade.
        """
        self._model = model
        self._model_id = model_id
        self._api_key = api_key
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._max_iterations = max_iterations
        self._instructions = LITERARY_RESEARCH_INSTRUCTIONS + (
            f"\n\n{instructions}" if instructions else ""
        )
        self._enable_prompt_caching = enable_prompt_caching
        self._weave_project = weave_project
        self._usage_sink = usage_sink
        self._search_tool = search_tool
        self._view_image_tool = view_image_tool
        self._usage_labels: dict[str, Any] = {
            "book_id": book_id,
            "run_id": run_id or new_run_id(),
            "agent_id": AGENT_ID,
        }

    def _build_agent(
        self, epub_path: str | Path, *, structure: EbookStructure | None
    ) -> tuple[ReactAgent, _ResearchState, EbookContext]:
        """Parse ``epub_path`` and assemble the fresh agent/state/tools for one run."""
        context = EbookContext.parse(epub_path)
        state = _ResearchState(context=context, structure=structure)

        # Built explicitly (rather than left to ReactAgent) so the same instance can
        # be handed to both the agent and the compactor below — the compactor's
        # summarisation calls then fold into the same cumulative usage/cost totals
        # and reach the same usage sink.
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
            tools=_build_tools(state, self._search_tool, self._view_image_tool),
            model=model,
            instructions=self._instructions,
            max_iterations=self._max_iterations,
            compactor=compactor,
            weave_project=self._weave_project,
            # Three artifacts, submitted one at a time, and the run is only finished
            # when all three are in — so a turn that ends with no tool call is not a
            # decision to stop, it is a run about to lose the artifacts it hasn't
            # written yet. This is the guard that would have saved the observed
            # failure, where the provider returned one empty reply straight after the
            # dossier was accepted and the loop read the silence as "done".
            completion_guard=lambda: _research_nudge(state),
        )
        return agent, state, context

    @staticmethod
    def _finalize(
        agent: ReactAgent,
        state: _ResearchState,
        context: EbookContext,
        stop_reason: str,
    ) -> LiteraryResearchReport:
        """Assemble a finished run's artifacts into a report, or raise."""
        if not state.is_complete:
            reason = (
                "; ".join(state.last_errors)
                if state.last_errors
                else describe_stop_reason(stop_reason)
            )
            raise LiteraryResearchError(
                f"LiteraryResearchAgent did not complete research for "
                f"'{context.title}' — missing {', '.join(state.missing())} ({reason})",
                partial=state.partial(),
            )
        spent = (
            float(agent.model.cumulative.get("cost_usd", 0.0)) - state.cost_before_usd
        )
        assert state.author_profile is not None
        assert state.world_dossier is not None
        assert state.style_bibles is not None
        return LiteraryResearchReport(
            title=context.title,
            author=context.author,
            author_profile=state.author_profile,
            world_dossier=state.world_dossier,
            style_bibles=state.style_bibles,
            selected_direction=DEFAULT_STYLE_DIRECTION,
            cost_usd=round(max(spent, 0.0), 6),
        )

    async def research(
        self,
        epub_path: str | Path,
        *,
        structure: EbookStructure | None = None,
        stream: bool = False,
        console: Any = None,
    ) -> LiteraryResearchReport:
        """Parse ``epub_path`` and run the agent to research it.

        Args:
            epub_path (str | Path): Path to the EPUB file.
            structure (EbookStructure | None): The structure an earlier loader run
                extracted, exposed to the agent through ``get_outline``. Optional —
                without it the agent orients itself with the TOC and headings.
            stream (bool): Render assistant text and tool activity to a Rich console.
            console (Any): Optional Rich ``Console`` for rendered output.

        Returns:
            LiteraryResearchReport: All three artifacts, with ``cost_usd`` set to
                this run's spend.

        Raises:
            LiteraryResearchError: If the run ends with any artifact unsubmitted.
                Whatever *was* accepted is carried on the exception's ``partial``.
        """
        agent, state, context = self._build_agent(epub_path, structure=structure)
        result = await agent.run(
            render_research_prompt(context, has_outline=structure is not None),
            stream=stream,
            console=console,
        )
        return self._finalize(agent, state, context, result.stop_reason)

    def stream_research(
        self, epub_path: str | Path, *, structure: EbookStructure | None = None
    ) -> tuple[AsyncIterator[AgentEvent], Callable[[], LiteraryResearchReport]]:
        """Like :meth:`research`, but exposes the run's live events instead of blocking.

        Mirrors :meth:`~diorama.core.react.ReactAgent.stream_events` paired with
        ``last_result``, exactly as the loader's ``stream_load`` does: iterate the
        returned events fully, then call ``finalize()``.

        Args:
            epub_path (str | Path): Path to the EPUB file.
            structure (EbookStructure | None): The structure an earlier loader run
                extracted, exposed through ``get_outline``.

        Returns:
            tuple[AsyncIterator[AgentEvent], Callable[[], LiteraryResearchReport]]:
                The run's event stream, and a callable that resolves the report once
                that stream has been fully consumed. The callable raises
                :class:`LiteraryResearchError` if any artifact went unsubmitted.
        """
        agent, state, context = self._build_agent(epub_path, structure=structure)
        events = agent.stream_events(
            render_research_prompt(context, has_outline=structure is not None),
            provider_stream=False,
        )

        def finalize() -> LiteraryResearchReport:
            assert agent.last_result is not None, (
                "finalize() called before the event stream was fully consumed"
            )
            return self._finalize(agent, state, context, agent.last_result.stop_reason)

        return events, finalize


__all__ = [
    "AGENT_ID",
    "AUTHOR_PROFILE_SCHEMA",
    "DEFAULT_STYLE_DIRECTION",
    "LITERARY_RESEARCH_INSTRUCTIONS",
    "STYLE_BIBLES_SCHEMA",
    "WORLD_DOSSIER_SCHEMA",
    "AuthorProfile",
    "Evidence",
    "GetOutlineTool",
    "LiteraryResearchAgent",
    "LiteraryResearchError",
    "LiteraryResearchReport",
    "LocationProfile",
    "Milieu",
    "PaletteColor",
    "StyleBible",
    "StyleBibleCandidates",
    "SubmitAuthorProfileTool",
    "SubmitStyleBiblesTool",
    "SubmitWorldDossierTool",
    "TimePeriod",
    "VisualMarkers",
    "VisualTraditionEntry",
    "WorldDossier",
    "coverage_warnings",
    "render_research_prompt",
    "validate_author_profile",
    "validate_style_bibles",
    "validate_world_dossier",
]
