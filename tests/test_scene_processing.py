"""Tests for the backend's scene-segmentation phase.

Phase two of processing runs one agent per leaf section, so what matters here is
everything *around* the agent: that it reports a single advancing progress row instead
of a trace, that a section which won't segment can't take the book down with it, that
concurrency is actually bounded, and that the run's artefacts (scenes file, scene
count, combined cost) land where the shelf and the reader expect them.

Fully offline: the store is redirected into a tmp_path, the loader is stubbed out, and
the real segmentation agent runs against a scripted model that reads the paragraph
count out of its own prompt.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from diorama.agents.ebook_scene_segmentation import EbookSceneSegmentationAgent
from diorama.backend import processing, store, usage_store
from diorama.backend.models import BookRecord
from diorama.backend.processing import _BookRun, _run_book, _segment_scenes
from diorama.ebook.models import Coverage, EbookStructure, StructureNode
from diorama.ebook.scenes import SceneSegmentation
from tests.fakes import FakeModel, response as _response, tool_call as _tool_call

PARAGRAPHS = [
    "The hall was long and low, and lit by a single lamp.",
    "The doctor did not look up from the ledger.",
    "By morning the snow had stopped and the valley was white.",
    "Maria walked out to the ridge alone.",
]
TEXT = "\n\n".join(PARAGRAPHS)


# --------------------------------------------------------------------------- #
# Fixtures & doubles
# --------------------------------------------------------------------------- #
@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect every on-disk artefact of a run into tmp_path."""
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "UPLOADS_DIR", tmp_path / "uploads")
    monkeypatch.setattr(store, "STRUCTURES_DIR", tmp_path / "structures")
    monkeypatch.setattr(store, "SCENES_DIR", tmp_path / "scenes")
    monkeypatch.setattr(store, "COVERS_DIR", tmp_path / "covers")
    monkeypatch.setattr(store, "LIBRARY_FILE", tmp_path / "library.json")
    monkeypatch.setattr(usage_store, "USAGE_DIR", tmp_path / "usage")
    return tmp_path


@pytest.fixture(autouse=True)
def offline_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never read real settings or a real API key while resolving an agent."""

    async def resolve(agent_id: str) -> tuple[str, str | None]:
        return "openrouter/test/model", None

    monkeypatch.setattr(processing, "resolve_agent_runtime", resolve)


class SceneModel(FakeModel):
    """Answers any segmentation prompt by splitting that section down the middle.

    The prompt states its own paragraph count, so one scripted model can serve sections
    of different lengths — which is what lets these tests drive the *real* agent, tools
    and validator rather than a stand-in for them.
    """

    def __init__(self, *, fail_on: str | None = None, **kwargs) -> None:
        super().__init__([], **kwargs)
        self.fail_on = fail_on
        self.in_flight = 0
        self.max_in_flight = 0

    async def acompletion(self, messages, tools=None, stream: bool = False):
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            self.calls.append({"messages": list(messages)})
            prompt = str(messages[-1].get("content") or "")
            if self.fail_on and self.fail_on in prompt:
                raise RuntimeError("the model refused this section")
            await asyncio.sleep(0)  # a real turn yields; make interleaving observable
            total = int(re.search(r"It has (\d+) paragraphs", prompt).group(1))
            middle = max(total // 2, 1)
            bounds = [(0, middle - 1), (middle, total - 1)] if total > 1 else [(0, 0)]
            payload = {
                "scenes": [
                    {"start_paragraph": s, "end_paragraph": e} for s, e in bounds
                ]
            }
            return _response(
                tool_calls=[_tool_call("c1", "submit_scenes", json.dumps(payload))]
            )
        finally:
            self.in_flight -= 1


def scene_agent_class(model: FakeModel):
    """A drop-in for ``EbookSceneSegmentationAgent`` bound to a scripted model."""

    class _Bound(EbookSceneSegmentationAgent):
        def __init__(self, **kwargs) -> None:
            kwargs.pop("model_id", None)
            kwargs.pop("api_key", None)
            super().__init__(model=model, **kwargs)

    return _Bound


class StubLoader:
    """Stands in for ``EbookLoaderAgent``; its own behaviour is tested elsewhere."""

    structure: EbookStructure

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs

    def stream_load(self, epub_path):
        async def events():
            return
            yield  # pragma: no cover — an empty async iterator

        return events(), lambda: StubLoader.structure


def _leaf(
    number: str, text: str = TEXT, start: int = 0, title: str | None = None
) -> StructureNode:
    return StructureNode(
        level_type="chapter",
        number=number,
        title=title or f"Chapter {number}",
        start_block_id=start,
        end_block_id=start + len(text.split("\n\n")) - 1,
        text=text,
    )


def _structure(*leaves: StructureNode) -> EbookStructure:
    total = sum(len(n.text.split("\n\n")) for n in leaves if n.text)
    return EbookStructure(
        title="A Tale",
        author="Ann Onymous",
        level_types=["chapter"],
        root=list(leaves),
        coverage=Coverage(covered=True, total_blocks=total, assigned_blocks=total),
        cost_usd=0.004,
    )


def _progress(run: _BookRun) -> list:
    return [line for line in run.log if line.kind == "progress"]


# --------------------------------------------------------------------------- #
# _segment_scenes
# --------------------------------------------------------------------------- #
async def test_progress_is_one_advancing_row_not_a_trace(data_dir, monkeypatch):
    """The whole phase reports through a single id, so the shelf draws one bar."""
    model = SceneModel()
    monkeypatch.setattr(
        processing, "EbookSceneSegmentationAgent", scene_agent_class(model)
    )
    run = _BookRun()
    structure = _structure(_leaf("I"), _leaf("II"), _leaf("III"))

    scenes = await _segment_scenes("b1", "r1", structure, run)

    rows = _progress(run)
    assert {row.id for row in rows} == {"scenes-b1"}
    assert [(row.done, row.total) for row in rows] == [
        (0, 3),
        (1, 3),
        (2, 3),
        (3, 3),
        (3, 3),
    ]
    # Only the final row is "done" — the last section finishing and the phase
    # finishing are different moments.
    assert [row.status for row in rows[:-1]] == ["pending"] * 4
    assert rows[-1].status == "done"
    assert rows[-1].text == "Marked 6 scenes across 3 sections"
    assert scenes.scene_count == 6
    assert len(run.log) == len(rows), "the phase must not emit any other trace lines"


async def test_sections_come_back_in_reading_order(data_dir, monkeypatch):
    """Concurrent runs finish out of order; the result must not."""
    model = SceneModel()
    monkeypatch.setattr(
        processing, "EbookSceneSegmentationAgent", scene_agent_class(model)
    )
    structure = _structure(*[_leaf(str(i)) for i in range(1, 7)])

    scenes = await _segment_scenes("b1", "r1", structure, _BookRun())

    assert [s.title for s in scenes.segmentations] == [
        f"Chapter {i}" for i in range(1, 7)
    ]


async def test_a_section_that_wont_segment_falls_back_instead_of_failing(
    data_dir, monkeypatch
):
    """The structure is already saved; one stubborn section can't cost the book."""
    model = SceneModel(fail_on="The Ridge")
    monkeypatch.setattr(
        processing, "EbookSceneSegmentationAgent", scene_agent_class(model)
    )
    run = _BookRun()
    structure = _structure(
        _leaf("I", title="The Arrival"),
        _leaf("II", title="The Ridge"),
        _leaf("III", title="The Return"),
    )

    scenes = await _segment_scenes("b1", "r1", structure, run)

    assert len(scenes.segmentations) == 3
    # The failed one is present as a single whole-section scene, not missing.
    fallback = scenes.segmentations[1]
    assert len(fallback.scenes) == 1
    assert fallback.scenes[0].text == TEXT
    assert fallback.title == "The Ridge"
    assert [len(s.scenes) for s in scenes.segmentations] == [2, 1, 2]

    final = _progress(run)[-1]
    assert final.status == "done"
    assert final.text == (
        "Marked 5 scenes across 2 of 3 sections (1 couldn't be split)"
    )


async def test_concurrency_is_bounded(data_dir, monkeypatch):
    model = SceneModel()
    monkeypatch.setattr(
        processing, "EbookSceneSegmentationAgent", scene_agent_class(model)
    )
    monkeypatch.setattr(processing, "SCENE_CONCURRENCY", 2)
    structure = _structure(*[_leaf(str(i)) for i in range(8)])

    await _segment_scenes("b1", "r1", structure, _BookRun())

    assert model.max_in_flight == 2
    assert len(model.calls) == 8


async def test_a_book_with_no_leaves_does_no_work(data_dir, monkeypatch):
    model = SceneModel()
    monkeypatch.setattr(
        processing, "EbookSceneSegmentationAgent", scene_agent_class(model)
    )
    run = _BookRun()

    scenes = await _segment_scenes("b1", "r1", _structure(), run)

    assert scenes.segmentations == []
    assert model.calls == []
    assert run.log == [], "no sections means no bar to draw"


# --------------------------------------------------------------------------- #
# _run_book: the two phases wired together
# --------------------------------------------------------------------------- #
def _shelve(record: BookRecord) -> None:
    asyncio.run(store.upsert_book(record))


async def test_run_book_segments_after_mapping_and_shelves_both(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    StubLoader.structure = _structure(_leaf("I"), _leaf("II"))
    model = SceneModel()
    monkeypatch.setattr(processing, "EbookLoaderAgent", StubLoader)
    monkeypatch.setattr(
        processing, "EbookSceneSegmentationAgent", scene_agent_class(model)
    )
    await store.upsert_book(
        BookRecord(
            id="b1",
            title="pending.epub",
            source_filename="pending.epub",
            created_at=datetime.now(timezone.utc).isoformat(),
        )
    )
    run = _BookRun()

    await _run_book("b1", run)

    book = await store.get_book("b1")
    assert book is not None
    assert book.status == "ready"
    assert book.title == "A Tale"
    assert book.scene_count == 4
    # The card's figure is read back from the ledger, so it covers the segmentation
    # calls too — here that is all of it, since the loader is stubbed and spends
    # nothing. Both phases share one run id, with agent_id telling them apart.
    assert book.cost_usd == pytest.approx(2 * FakeModel.CALL_COST_USD)
    ledger = usage_store.read_calls("b1")
    assert {r.agent_id for r in ledger} == {"ebook_scene_segmentation"}
    assert len({r.run_id for r in ledger}) == 1

    saved = json.loads(store.scenes_path("b1").read_text())
    assert [s["title"] for s in saved["segmentations"]] == ["Chapter I", "Chapter II"]
    assert store.structure_path("b1").exists()

    kinds = [line.kind for line in run.log]
    assert "progress" in kinds
    assert kinds[-1] == "done"


async def test_a_failed_segmentation_phase_still_shelves_the_book(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    """Scenes are the optional half — the book stays readable without them."""
    StubLoader.structure = _structure(_leaf("I"))
    monkeypatch.setattr(processing, "EbookLoaderAgent", StubLoader)

    def explode(**kwargs):
        raise RuntimeError("no model configured")

    monkeypatch.setattr(processing, "EbookSceneSegmentationAgent", explode)
    await store.upsert_book(
        BookRecord(
            id="b1",
            title="pending.epub",
            source_filename="pending.epub",
            created_at=datetime.now(timezone.utc).isoformat(),
        )
    )
    run = _BookRun()

    await _run_book("b1", run)

    book = await store.get_book("b1")
    assert book is not None
    assert book.status == "ready"
    assert book.error is None
    assert book.scene_count is None
    assert not store.scenes_path("b1").exists()
    assert store.structure_path("b1").exists()

    failed = [line for line in run.log if line.kind == "progress"]
    assert failed and failed[-1].status == "error"
    assert "still readable" in failed[-1].text
    assert run.log[-1].kind == "done"


async def test_reprocessing_replaces_the_scenes_file(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    """Unlike the ledger, scenes are current state — a retry overwrites, not appends."""
    StubLoader.structure = _structure(_leaf("I"))
    monkeypatch.setattr(processing, "EbookLoaderAgent", StubLoader)
    monkeypatch.setattr(
        processing, "EbookSceneSegmentationAgent", scene_agent_class(SceneModel())
    )
    await store.upsert_book(
        BookRecord(
            id="b1",
            title="pending.epub",
            source_filename="pending.epub",
            created_at=datetime.now(timezone.utc).isoformat(),
        )
    )
    store.scenes_path("b1").write_text(
        SceneSegmentation(paragraph_count=1).model_dump_json()
    )

    await _run_book("b1", _BookRun())

    saved = json.loads(store.scenes_path("b1").read_text())
    assert saved["title"] == "A Tale"
    assert len(saved["segmentations"]) == 1
