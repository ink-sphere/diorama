"""Tests for diorama.agents.literary_research_agent.

Runs entirely against a :class:`FakeModel` (see tests/fakes.py) and a web-search
tool with no key configured, so no network or API keys are required. EPUB parsing
is exercised against the real sample book in books/; every LLM turn is scripted.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from diorama.agents import LiteraryResearchAgent, LiteraryResearchError
from diorama.agents.literary_research_agent import (
    DEFAULT_STYLE_DIRECTION,
    LITERARY_RESEARCH_INSTRUCTIONS,
    GetOutlineTool,
    SubmitAuthorProfileTool,
    SubmitStyleBiblesTool,
    SubmitWorldDossierTool,
    _ResearchState,
    coverage_warnings,
    render_research_prompt,
    validate_author_profile,
    validate_style_bibles,
    validate_world_dossier,
)
from diorama.ebook import EbookContext
from diorama.ebook.models import Block, Coverage, EbookStructure, StructureNode
from tests.fakes import FakeModel, response as _response, tool_call as _tool_call

BOOKS_DIR = Path(__file__).resolve().parent.parent / "books"
ALICE = BOOKS_DIR / "alice-in-wonderland.epub"

pytestmark = pytest.mark.skipif(
    not ALICE.exists(), reason="sample EPUB not present in books/"
)

PROSE = " ".join(["word"] * 60)


# --------------------------------------------------------------------------- #
# Fixtures / builders
# --------------------------------------------------------------------------- #
def _synthetic_context() -> EbookContext:
    specs = [("CHAPTER ONE", "h1"), ("Down the rabbit hole.", None)]
    blocks = [
        Block(id=i, text=t, tag=g, spine_index=0) for i, (t, g) in enumerate(specs)
    ]
    return EbookContext(
        path=Path("synthetic.epub"), title="A Tale", author="Ann Onymous", blocks=blocks
    )


def _long_context(total: int = 300) -> EbookContext:
    """A book long enough for 'first third' to mean something."""
    blocks = [
        Block(id=i, text=f"Paragraph {i}.", tag=None, spine_index=0)
        for i in range(total)
    ]
    return EbookContext(
        path=Path("long.epub"), title="A Long Tale", author="Ann Onymous", blocks=blocks
    )


def _state(**kwargs) -> _ResearchState:
    return _ResearchState(context=_synthetic_context(), **kwargs)


def profile(**overrides) -> dict:
    base = {
        "name": "Lewis Carroll",
        "birth_date": "1832",
        "death_date": "1898",
        "bio_prose": PROSE,
        "work_context_prose": PROSE,
        "publication_year": 1865,
        "authorship_period": "mid-Victorian",
        "visual_tradition": [
            {
                "name": "John Tenniel",
                "kind": "illustrator",
                "medium": "wood engraving",
                "description": "Dense cross-hatched line engravings.",
            }
        ],
        "evidence": {"block_ids": [0], "urls": []},
    }
    base.update(overrides)
    return base


def dossier(**overrides) -> dict:
    base = {
        "time_periods": [
            {
                "label": "Victorian England",
                "kind": "story",
                "span": "1860s",
                "visual_markers": {
                    "clothing": "Pinafores, frock coats, top hats.",
                    "light_sources": "Candles and oil lamps.",
                },
                "evidence": {"block_ids": [1], "urls": []},
            }
        ],
        "locations": [
            {
                "name": "The riverbank",
                "existence": "real",
                "description": "A slow green stretch of the Thames near Oxford.",
                "visual_notes": "Willows, flat water, hot afternoon light.",
                "periods": ["Victorian England"],
                "evidence": {"block_ids": [1], "urls": []},
            }
        ],
        "milieus": [
            {
                "name": "Victorian gentry children",
                "description": "Well-off children of the professional classes.",
                "wardrobe": "Starched pinafores over cotton frocks, buttoned boots.",
                "evidence": {"block_ids": [0], "urls": []},
            }
        ],
    }
    base.update(overrides)
    return base


def bible(direction: str = "original", **overrides) -> dict:
    base = {
        "direction": direction,
        "name": "Sunlit Ink",
        "rationale": "Suits the book's dry comedy and outdoor opening.",
        "mood_words": ["dreamlike", "dry", "bright"],
        "palette": [
            {"name": "River green", "hex": "#5b7f4e", "role": "ground"},
            {"name": "Pinafore white", "hex": "#f4f1e8"},
            {"name": "Ink black", "hex": "#1a1a18", "role": "line"},
        ],
        "lighting": "High afternoon sun, hard shadows.",
        "style_prompt_block": "x" * 200,
        "negative_constraints": ["No electric light", "No modern clothing"],
    }
    if direction == "traditional":
        base["influences"] = ["John Tenniel"]
    base.update(overrides)
    return base


# --------------------------------------------------------------------------- #
# Author-profile validation
# --------------------------------------------------------------------------- #
def test_valid_author_profile_passes():
    assert validate_author_profile(profile()) == []


def test_author_profile_requires_a_name():
    errors = validate_author_profile(profile(name=""))
    assert any("name is required" in e for e in errors)


def test_stub_prose_is_rejected():
    errors = validate_author_profile(profile(bio_prose="A writer."))
    assert any("bio_prose" in e and "words" in e for e in errors)


def test_implausible_publication_year_is_rejected():
    errors = validate_author_profile(profile(publication_year=99999))
    assert any("publication_year" in e for e in errors)


def test_visual_tradition_entry_needs_a_kind_and_description():
    errors = validate_author_profile(
        profile(visual_tradition=[{"name": "Someone", "kind": "movie"}])
    )
    assert any("kind must be one of" in e for e in errors)
    assert any("description is required" in e for e in errors)


def test_empty_visual_tradition_is_acceptable():
    """A book with no illustration history is a normal case, not an error."""
    assert validate_author_profile(profile(visual_tradition=[])) == []


# --------------------------------------------------------------------------- #
# World-dossier validation
# --------------------------------------------------------------------------- #
def test_valid_dossier_passes():
    assert validate_world_dossier(dossier(), total_blocks=2) == []


def test_dossier_requires_each_of_its_three_registries():
    errors = validate_world_dossier(
        {"time_periods": [], "locations": [], "milieus": []}, total_blocks=2
    )
    assert any("time_periods is empty" in e for e in errors)
    assert any("locations is empty" in e for e in errors)
    assert any("milieus is empty" in e for e in errors)


def test_period_without_any_visual_markers_is_rejected():
    """The markers are the whole point — a period with none is not usable."""
    errors = validate_world_dossier(
        dossier(
            time_periods=[
                {"label": "Victorian England", "kind": "story", "visual_markers": {}}
            ]
        ),
        total_blocks=2,
    )
    assert any("visual_markers is empty" in e for e in errors)


def test_location_referencing_an_unknown_period_is_rejected():
    errors = validate_world_dossier(
        dossier(
            locations=[
                {
                    "name": "Wonderland",
                    "existence": "fictional",
                    "description": "A dream country.",
                    "periods": ["Jurassic"],
                }
            ]
        ),
        total_blocks=2,
    )
    assert any("Jurassic" in e and "not one of the time_periods" in e for e in errors)


def test_evidence_citing_a_nonexistent_block_is_rejected():
    """Catches invented citations, which look identical to real ones otherwise."""
    errors = validate_world_dossier(
        dossier(
            milieus=[
                {
                    "name": "Gentry",
                    "wardrobe": "Pinafores.",
                    "evidence": {"block_ids": [9999]},
                }
            ]
        ),
        total_blocks=2,
    )
    assert any("9999" in e for e in errors)


def test_milieu_without_wardrobe_is_rejected():
    errors = validate_world_dossier(
        dossier(milieus=[{"name": "Gentry", "description": "Comfortable folk."}]),
        total_blocks=2,
    )
    assert any("wardrobe is required" in e for e in errors)


def test_rendering_vocabulary_in_the_dossier_is_rejected():
    """The style-free invariant is what makes the art style swappable."""
    errors = validate_world_dossier(
        dossier(
            locations=[
                {
                    "name": "The riverbank",
                    "existence": "real",
                    "description": "A green stretch of river.",
                    "visual_notes": "Rendered in a soft watercolour wash.",
                }
            ]
        ),
        total_blocks=2,
    )
    assert any("style-free" in e for e in errors)


def test_world_terms_that_merely_sound_artistic_are_allowed():
    """A book may contain an oil painting; that is a prop, not a rendering choice."""
    errors = validate_world_dossier(
        dossier(
            locations=[
                {
                    "name": "The parlour",
                    "existence": "fictional",
                    "description": "A dim room hung with oil paintings in gilt frames.",
                    "visual_notes": "Candlelight on varnish.",
                }
            ]
        ),
        total_blocks=2,
    )
    assert errors == []


# --------------------------------------------------------------------------- #
# Coverage warnings (advisory, never fatal)
# --------------------------------------------------------------------------- #
def _cited(*block_ids: int) -> dict:
    """A dossier whose three registries cite exactly ``block_ids`` between them."""
    entries = dossier()
    for registry in ("time_periods", "locations", "milieus"):
        entries[registry][0]["evidence"] = {"block_ids": [], "urls": []}
    for index, block_id in enumerate(block_ids):
        registry = ("time_periods", "locations", "milieus")[index % 3]
        entries[registry][0]["evidence"]["block_ids"].append(block_id)
    return entries


def test_evidence_confined_to_the_opening_third_earns_a_warning():
    warnings = coverage_warnings(_cited(2, 40, 90), total_blocks=300)
    assert len(warnings) == 1
    assert "first third" in warnings[0]
    assert "submit_world_dossier again" in warnings[0]


def test_evidence_reaching_the_late_chapters_is_not_warned_about():
    assert coverage_warnings(_cited(2, 40, 280), total_blocks=300) == []


def test_a_single_late_citation_is_enough_to_clear_the_warning():
    """The check asks whether the back of the book was opened at all."""
    assert coverage_warnings(_cited(0, 1, 2, 3, 4, 101), total_blocks=300) == []


def test_too_few_citations_to_judge_produces_no_warning():
    """Two early block ids say nothing about how much of the book was read."""
    assert coverage_warnings(_cited(1, 2), total_blocks=300) == []


def test_a_book_too_short_for_thirds_is_never_warned_about():
    assert coverage_warnings(dossier(), total_blocks=2) == []


def test_out_of_range_citations_do_not_count_toward_coverage():
    """An invented late block id must not buy off the warning."""
    warnings = coverage_warnings(_cited(2, 40, 90, 9999), total_blocks=300)
    assert len(warnings) == 1


# --------------------------------------------------------------------------- #
# Style-bible validation
# --------------------------------------------------------------------------- #
def test_valid_style_bibles_pass():
    assert (
        validate_style_bibles(
            bible("original"), bible("traditional"), tradition_names=["John Tenniel"]
        )
        == []
    )


def test_traditional_candidate_is_required_when_a_tradition_was_recorded():
    errors = validate_style_bibles(
        bible("original"), None, tradition_names=["John Tenniel"]
    )
    assert any("traditional candidate is required" in e.lower() for e in errors)


def test_original_alone_is_enough_when_no_tradition_was_recorded():
    assert validate_style_bibles(bible("original"), None, tradition_names=[]) == []


def test_traditional_candidate_must_name_its_influences():
    errors = validate_style_bibles(
        bible("original"),
        bible("traditional", influences=[]),
        tradition_names=["John Tenniel"],
    )
    assert any("influences is empty" in e for e in errors)


def test_direction_must_match_the_slot_it_was_submitted_in():
    mislabelled = bible("original")
    mislabelled["direction"] = "traditional"
    errors = validate_style_bibles(mislabelled, None, tradition_names=[])
    assert any("must be 'original'" in e for e in errors)


def test_palette_needs_at_least_three_colours():
    errors = validate_style_bibles(
        bible("original", palette=[{"name": "Grey", "hex": "#888888"}]),
        None,
        tradition_names=[],
    )
    assert any("palette has 1 colour" in e for e in errors)


def test_palette_colours_must_be_hex_so_they_can_be_drawn():
    errors = validate_style_bibles(
        bible(
            "original",
            palette=[
                {"name": "Dusty rose", "hex": "dusty rose"},
                {"name": "B", "hex": "#123456"},
                {"name": "C", "hex": "#abcdef"},
            ],
        ),
        None,
        tradition_names=[],
    )
    assert any("#rrggbb" in e for e in errors)


def test_a_hollow_style_prompt_block_is_rejected():
    errors = validate_style_bibles(
        bible("original", style_prompt_block="watercolour"), None, tradition_names=[]
    )
    assert any("style_prompt_block" in e for e in errors)


def test_negative_constraints_are_required():
    errors = validate_style_bibles(
        bible("original", negative_constraints=[]), None, tradition_names=[]
    )
    assert any("negative_constraints is empty" in e for e in errors)


# --------------------------------------------------------------------------- #
# Submit tools
# --------------------------------------------------------------------------- #
async def test_submit_tools_accept_in_order_and_only_the_last_one_terminates():
    state = _state()

    first = await SubmitAuthorProfileTool(state=state).forward(profile=profile())
    assert first.is_error is False
    assert first.terminate is False
    assert "submit_world_dossier" in first.text
    assert state.author_profile is not None

    second = await SubmitWorldDossierTool(state=state).forward(dossier=dossier())
    assert second.terminate is False
    assert state.world_dossier is not None

    third = await SubmitStyleBiblesTool(state=state).forward(
        original=bible("original"), traditional=bible("traditional")
    )
    assert third.is_error is False
    assert third.terminate is True
    assert state.is_complete is True


async def test_style_bibles_before_their_prerequisites_are_refused():
    state = _state()
    result = await SubmitStyleBiblesTool(state=state).forward(original=bible())
    assert result.is_error is True
    assert "submit_author_profile" in result.text
    assert "submit_world_dossier" in result.text
    assert state.style_bibles is None


async def test_style_bibles_are_validated_against_the_submitted_profile():
    """The traditional candidate is mandatory *because* the profile recorded one."""
    state = _state()
    await SubmitAuthorProfileTool(state=state).forward(profile=profile())
    await SubmitWorldDossierTool(state=state).forward(dossier=dossier())

    rejected = await SubmitStyleBiblesTool(state=state).forward(original=bible())
    assert rejected.is_error is True
    assert state.style_bibles is None

    # Same submission, but for a book whose profile recorded no tradition.
    clean = _state()
    await SubmitAuthorProfileTool(state=clean).forward(
        profile=profile(visual_tradition=[])
    )
    await SubmitWorldDossierTool(state=clean).forward(dossier=dossier())
    accepted = await SubmitStyleBiblesTool(state=clean).forward(original=bible())
    assert accepted.is_error is False
    assert clean.style_bibles is not None
    assert clean.style_bibles.traditional is None


async def test_a_rejected_submission_leaves_the_state_untouched_and_reports_errors():
    state = _state()
    result = await SubmitAuthorProfileTool(state=state).forward(
        profile=profile(bio_prose="")
    )
    assert result.is_error is True
    assert "resubmit" in result.text
    assert state.author_profile is None
    assert state.last_errors


async def test_a_front_loaded_dossier_is_accepted_with_a_nudge_not_rejected():
    """The nudge must not cost the agent the dossier it just wrote."""
    state = _ResearchState(context=_long_context())
    result = await SubmitWorldDossierTool(state=state).forward(
        dossier=_cited(2, 40, 90)
    )

    assert result.is_error is False
    assert state.world_dossier is not None
    assert state.last_errors == []
    assert "accepted" in result.text
    assert "first third" in result.text


async def test_a_well_spread_dossier_is_accepted_without_a_note():
    state = _ResearchState(context=_long_context())
    result = await SubmitWorldDossierTool(state=state).forward(
        dossier=_cited(2, 40, 280)
    )
    assert result.is_error is False
    assert "Note:" not in result.text


async def test_a_nudged_dossier_can_be_resubmitted_with_wider_evidence():
    """The note tells the agent to resubmit, so resubmission must work."""
    state = _ResearchState(context=_long_context())
    await SubmitWorldDossierTool(state=state).forward(dossier=_cited(2, 40, 90))

    wider = _cited(2, 40, 90, 250)
    wider["locations"].append(
        {
            "name": "The far shore",
            "existence": "fictional",
            "description": "Where the second half of the book takes place.",
            "periods": ["Victorian England"],
            "evidence": {"block_ids": [260], "urls": []},
        }
    )
    again = await SubmitWorldDossierTool(state=state).forward(dossier=wider)

    assert again.is_error is False
    assert "Note:" not in again.text
    assert [location.name for location in state.world_dossier.locations] == [
        "The riverbank",
        "The far shore",
    ]


async def test_dossier_evidence_is_checked_against_the_real_book(tmp_path):
    """Block range comes from the parsed book, not from the submission."""
    state = _state()  # synthetic context has exactly 2 blocks
    result = await SubmitWorldDossierTool(state=state).forward(
        dossier=dossier(
            milieus=[
                {
                    "name": "Gentry",
                    "wardrobe": "Pinafores.",
                    "evidence": {"block_ids": [5]},
                }
            ]
        )
    )
    assert result.is_error is True
    assert "0..1" in result.text


# --------------------------------------------------------------------------- #
# get_outline
# --------------------------------------------------------------------------- #
async def test_get_outline_renders_the_structure_when_one_was_supplied():
    structure = EbookStructure(
        title="A Tale",
        root=[
            StructureNode(
                level_type="book",
                start_block_id=0,
                end_block_id=1,
                children=[
                    StructureNode(
                        level_type="chapter",
                        number="1",
                        title="Down the Rabbit-Hole",
                        start_block_id=0,
                        end_block_id=1,
                    )
                ],
            )
        ],
        coverage=Coverage(covered=True, total_blocks=2, assigned_blocks=2),
    )
    result = await GetOutlineTool(state=_state(structure=structure)).forward()
    assert "chapter 1: Down the Rabbit-Hole" in result
    assert "[blocks 0-1]" in result


async def test_get_outline_without_a_structure_points_elsewhere():
    result = await GetOutlineTool(state=_state()).forward()
    assert result.is_error is False
    assert "list_headings" in result.text


# --------------------------------------------------------------------------- #
# End to end (scripted model)
# --------------------------------------------------------------------------- #
def _submissions(total_blocks: int) -> list:
    """The three accepted submissions, as three scripted assistant turns."""
    evidence = {"block_ids": [0], "urls": []}
    return [
        _response(
            tool_calls=[
                _tool_call(
                    "c1",
                    "submit_author_profile",
                    json.dumps({"profile": profile(evidence=evidence)}),
                )
            ]
        ),
        _response(
            tool_calls=[
                _tool_call(
                    "c2", "submit_world_dossier", json.dumps({"dossier": dossier()})
                )
            ]
        ),
        _response(
            tool_calls=[
                _tool_call(
                    "c3",
                    "submit_style_bibles",
                    json.dumps(
                        {
                            "original": bible("original"),
                            "traditional": bible("traditional"),
                        }
                    ),
                )
            ]
        ),
    ]


async def test_research_happy_path_assembles_the_full_report():
    total = EbookContext.parse(ALICE).total_blocks
    model = FakeModel(_submissions(total))

    report = await LiteraryResearchAgent(model=model).research(ALICE)

    assert report.title == "Alice's Adventures in Wonderland"
    assert report.author_profile.name == "Lewis Carroll"
    assert report.world_dossier.locations[0].name == "The riverbank"
    assert report.style_bibles.traditional is not None
    assert report.selected_direction == DEFAULT_STYLE_DIRECTION
    assert report.cost_usd > 0
    assert len(model.calls) == 3


async def test_active_style_bible_follows_the_selection_and_falls_back_safely():
    total = EbookContext.parse(ALICE).total_blocks
    report = await LiteraryResearchAgent(model=FakeModel(_submissions(total))).research(
        ALICE
    )

    assert report.active_style_bible.direction == "original"

    report.selected_direction = "traditional"
    assert report.active_style_bible.direction == "traditional"

    # A selection pointing at a candidate that does not exist must not leave the
    # render stage with no style at all.
    report.style_bibles.traditional = None
    assert report.active_style_bible.direction == "original"


async def test_failed_run_carries_the_artifacts_it_did_finish():
    """Staged submission exists so a partial run is still worth something."""
    total = EbookContext.parse(ALICE).total_blocks
    # Three refusals: the quiet turn that ends the work, plus both nudges from the
    # completion guard, which will not let a run stop silently with an artifact owed.
    refusal = "I could not settle on an art direction."
    partial_script = _submissions(total)[:2] + [
        _response(content=refusal) for _ in range(3)
    ]
    model = FakeModel(partial_script)

    with pytest.raises(LiteraryResearchError) as excinfo:
        await LiteraryResearchAgent(model=model).research(ALICE)

    error = excinfo.value
    assert "submit_style_bibles" in str(error)
    assert error.partial["author_profile"] is not None
    assert error.partial["world_dossier"] is not None
    assert error.partial["style_bibles"] is None


async def test_research_retries_after_a_rejected_submission():
    total = EbookContext.parse(ALICE).total_blocks
    bad = _response(
        tool_calls=[
            _tool_call(
                "c0",
                "submit_author_profile",
                json.dumps({"profile": profile(bio_prose="")}),
            )
        ]
    )
    model = FakeModel([bad] + _submissions(total))

    report = await LiteraryResearchAgent(model=model).research(ALICE)

    assert report.author_profile.bio_prose == PROSE
    assert len(model.calls) == 4
    tool_messages = [m for m in model.calls[1]["messages"] if m.get("role") == "tool"]
    assert any("rejected" in m["content"] for m in tool_messages)


async def test_research_records_every_call_against_the_book_and_run():
    total = EbookContext.parse(ALICE).total_blocks
    rows: list = []

    await LiteraryResearchAgent(
        model=FakeModel(_submissions(total)),
        usage_sink=rows.append,
        book_id="book1",
        run_id="run1",
    ).research(ALICE)

    assert {(r.book_id, r.run_id, r.agent_id) for r in rows} == {
        ("book1", "run1", "literary_research")
    }


async def test_each_agent_gets_its_own_run_id_when_none_is_supplied():
    first = LiteraryResearchAgent(model=FakeModel([]))
    second = LiteraryResearchAgent(model=FakeModel([]))
    assert first._usage_labels["run_id"] != second._usage_labels["run_id"]


async def test_web_search_is_offered_even_with_no_key_configured(monkeypatch):
    """The agent must be able to *discover* that the web is unavailable."""
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    total = EbookContext.parse(ALICE).total_blocks
    model = FakeModel(_submissions(total))

    await LiteraryResearchAgent(model=model).research(ALICE)

    names = {t["function"]["name"] for t in model.calls[0]["tools"]}
    assert "web_search" in names
    assert {"submit_author_profile", "submit_world_dossier", "submit_style_bibles"} <= (
        names
    )


async def test_the_agent_can_look_at_the_illustrations_it_researches():
    total = EbookContext.parse(ALICE).total_blocks
    model = FakeModel(_submissions(total))

    await LiteraryResearchAgent(model=model).research(ALICE)

    names = {t["function"]["name"] for t in model.calls[0]["tools"]}
    assert "view_image" in names


def test_render_research_prompt_mentions_the_block_range_and_outline():
    context = _synthetic_context()
    assert "0..1" in render_research_prompt(context)
    assert "get_outline" not in render_research_prompt(context)
    assert "get_outline" in render_research_prompt(context, has_outline=True)


def test_the_instructions_ask_for_samples_from_across_the_whole_book():
    """Sampling advice with no spread requirement is how late chapters go missing."""
    instructions = LITERARY_RESEARCH_INSTRUCTIONS
    assert "beginning, the middle AND the late chapters" in instructions
    assert "view_image" in instructions


# --------------------------------------------------------------------------- #
# The run that used to be lost
# --------------------------------------------------------------------------- #
async def test_an_empty_reply_after_the_dossier_no_longer_ends_the_run():
    """A replay of the failure this guard exists for.

    Observed in production: nineteen healthy turns, the world dossier accepted, then
    one empty reply from the provider — ``finish_reason: "stop"``, zero tokens, zero
    cost — and the loop read the silence as "the model is done". The run ended with
    two of three artifacts, having spent $0.17 to get there.
    """
    total = EbookContext.parse(ALICE).total_blocks
    profile_call, dossier_call, bibles_call = _submissions(total)
    model = FakeModel(
        [profile_call, dossier_call, _response(content=None), bibles_call]
    )

    report = await LiteraryResearchAgent(model=model).research(ALICE)

    assert len(model.calls) == 4  # the empty turn was retried, not obeyed
    assert report.author_profile is not None
    assert report.world_dossier is not None
    assert report.style_bibles is not None


async def test_a_quiet_model_is_told_which_artifacts_are_still_owed():
    """The nudge names the outstanding tools and credits the banked ones, so a model
    that goes quiet doesn't restart work already accepted."""
    total = EbookContext.parse(ALICE).total_blocks
    profile_call, dossier_call, bibles_call = _submissions(total)
    model = FakeModel(
        [profile_call, dossier_call, _response(content="I'm done here."), bibles_call]
    )
    agent = LiteraryResearchAgent(model=model)

    await agent.research(ALICE)

    nudges = [
        m
        for m in model.calls[-1]["messages"]
        if m["role"] == "user" and "submit_style_bibles" in str(m.get("content"))
    ]
    assert nudges, "the guard should have named the outstanding tool"
    text = str(nudges[-1]["content"])
    assert "submit_author_profile" not in text.split("already banked")[0]
    assert "already banked" in text
