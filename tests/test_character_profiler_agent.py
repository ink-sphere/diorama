from __future__ import annotations

import asyncio
import json
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import pytest
from tau_agent import AssistantMessage, SimpleCancellationToken, message_text
from tau_ai import AssistantDoneEvent, AssistantErrorEvent, FakeProvider

from diorama.agents.character_profiler_agent import (
    CharacterProfilerAgent,
    CharacterProfilerError,
)
from diorama.agents.character_profiler_agent.agent import _narrative_leaves
from diorama.models.character_profile import CharacterProfile
from diorama.models.storybook import (
    EbookMetadata,
    StoryBook,
    StructureNode,
    TextContent,
)


def chapter(text, *, title="Chapter", narrative=True, raw=None):
    return StructureNode(
        structure_type="chapter",
        structure_title=title,
        is_part_of_narrative=narrative,
        content=[TextContent(raw_text=raw or text, markdown_text=text)],
    )


def book(*nodes):
    return StoryBook(
        id="test-book", metadata=EbookMetadata(title="A Story"), structure=nodes
    )


def update(name="Alice", *, ids=(), aliases=(), description="A curious traveler."):
    return {
        "existing_ids": list(ids),
        "character_name": name,
        "character_alias": list(aliases),
        "character_profile_description": description,
        "headshot_visual_prompt": "Freckled face. Other features are unspecified.",
    }


def response(characters):
    return [
        AssistantDoneEvent(
            reason="stop",
            message=AssistantMessage(content=json.dumps({"characters": characters})),
        )
    ]


class RegistryProvider(FakeProvider):
    def __init__(self, replies):
        super().__init__([])
        self.replies = list(replies)
        self.requests = []

    def stream_response(self, **kwargs):
        request = json.loads(message_text(kwargs["messages"][0]))
        self.requests.append(request)
        reply = self.replies.pop(0)
        self._streams.append(response(reply(request) if callable(reply) else reply))
        return super().stream_response(**kwargs)


async def test_scans_nested_narrative_and_enriches_one_identity(tmp_path):
    def enrich(request):
        identity = request["registry"][0]["id"]
        return [
            update(
                "Alice", ids=[identity], aliases=["Al"], description="A brave traveler."
            )
        ]

    provider = RegistryProvider([[update("Al")], enrich])
    source = book(
        chapter("Editor Jane thanks the publisher.", narrative=False),
        StructureNode(
            structure_type="part",
            structure_title="Part One",
            is_part_of_narrative=True,
            content=[
                chapter("Al arrived.", title="One"),
                chapter("Alice, called Al, bravely led the way.", title="Two"),
            ],
        ),
    )
    before = source.model_dump_json()
    events = []
    profiles = await CharacterProfilerAgent(
        provider=provider,
        model="fake",
        workspace_root=tmp_path,
        callbacks=[events.append],
    ).run(source)
    assert len(profiles) == 1
    assert isinstance(profiles[0], CharacterProfile)
    assert profiles[0].character_name == "Alice"
    assert profiles[0].character_alias == ["Al"]
    assert profiles[0].book_id == source.id
    assert profiles[0].id == uuid5(NAMESPACE_URL, json.dumps(["test-book", 1, 0])).hex
    assert provider.requests[1]["registry"][0]["id"] == "c1"
    assert "book_id" not in provider.requests[1]["registry"][0]
    assert profiles[0].character_profile_description == "A brave traveler."
    assert [request["excerpt"]["path"] for request in provider.requests] == [
        ["Part One", "One"],
        ["Part One", "Two"],
    ]
    assert "Editor Jane" not in str(provider.requests)
    assert source.model_dump_json() == before
    artifacts = list(tmp_path.rglob("*"))
    files = [path for path in artifacts if path.is_file()]
    assert len(files) == 1 and files[0].name == "character_profiles.json"
    assert json.loads(files[0].read_text()) == [
        profile.model_dump() for profile in profiles
    ]
    assert events[0].event.type == "run_start"
    assert events[-1].event.type == "run_end"
    assert any(event.event.type == "output_saved" for event in events)
    assert all(not call[3] for call in provider.calls)


async def test_reconciliation_merges_aliases_but_preserves_same_name_people():
    def reconcile(request):
        assert request["mode"] == "reconcile"
        first, alias, other = request["registry"]
        return [
            update(
                "Alice", ids=[alias["id"], first["id"]], aliases=[" Al ", "AL", "alice"]
            )
        ]

    provider = RegistryProvider(
        [
            [
                update("Alice", aliases=["Traveler"]),
                update("Al"),
                update("Alice", description="A different person."),
            ],
            reconcile,
        ]
    )
    profiles = await CharacterProfilerAgent(provider=provider, model="fake").run(
        book(chapter("Alice, also called Al, met another Alice."))
    )
    assert len(profiles) == 2
    assert profiles[0].character_alias == ["Al", "Traveler"]
    assert profiles[0].id == uuid5(NAMESPACE_URL, json.dumps(["test-book", 1, 0])).hex
    assert provider.requests[1]["registry"][0]["id"] == "c1"
    assert "book_id" not in provider.requests[1]["registry"][0]
    assert profiles[0].id != profiles[1].id
    assert profiles[1].character_profile_description == "A different person."


async def test_updates_do_not_drop_absent_characters():
    provider = RegistryProvider([[update()], [update("Bob")], []])
    profiles = await CharacterProfilerAgent(provider=provider, model="fake").run(
        book(chapter("Alice arrived."), chapter("Bob waited elsewhere."))
    )
    assert [profile.character_name for profile in profiles] == ["Alice", "Bob"]


def test_leaves_preserve_order_and_complete_text_with_raw_fallback():
    text = " ".join(f"word{index}" for index in range(100))
    source = book(chapter(text), chapter("", raw="<p>Bob <em>smiled</em>.</p>"))
    assert list(_narrative_leaves(source.structure)) == [
        (("Chapter",), text),
        (("Chapter",), "Bob smiled ."),
    ]


async def test_default_keeps_long_leaf_and_its_fragments_in_one_request():
    first = "Alice traveled. " * 3000
    last = "The traveler finally revealed that her name was Alice."
    node = chapter(first)
    node.content.append(TextContent(raw_text=last, markdown_text=last))
    provider = RegistryProvider([[update()]])
    await CharacterProfilerAgent(provider=provider, model="fake").run(book(node))
    assert len(provider.requests) == 1
    assert provider.requests[0]["excerpt"]["text"] == first.strip() + "\n\n" + last


async def test_explicit_limit_prefers_paragraph_boundaries_without_losing_text():
    text = "Alice arrived.\n\nBob greeted her.\n\nThey walked home."
    provider = RegistryProvider([[], [], []])
    await CharacterProfilerAgent(
        provider=provider, model="fake", max_chunk_characters=25
    ).run(book(chapter(text)))
    excerpts = [request["excerpt"]["text"] for request in provider.requests]
    assert excerpts == [
        "Alice arrived.\n\n",
        "Bob greeted her.\n\n",
        "They walked home.",
    ]
    assert "".join(excerpts) == text


async def test_fallback_accounts_for_growing_registry_and_json_escaping():
    text = ('Alice said "hello". \\ 雪\n' * 50 + "\n") * 30

    def reply(request):
        if not request["registry"]:
            return [update(description="Alice traveled. " * 300)]
        return []

    provider = RegistryProvider([reply] * 100)
    await CharacterProfilerAgent(
        provider=provider, model="fake", max_context_characters=20_000
    ).run(book(chapter(text)))
    excerpts = [request["excerpt"]["text"] for request in provider.requests]
    assert len(excerpts) > 1
    assert len(excerpts[1]) < len(excerpts[0])
    assert "".join(excerpts) == text.strip()
    assert all(
        request["excerpt"]["path"] == ["Chapter"] for request in provider.requests
    )
    for _, system, messages, _ in provider.calls:
        assert len(system) + len(message_text(messages[0])) <= 20_000
    assert all(request["registry"] for request in provider.requests[1:])


async def test_fallback_reserves_room_for_validation_repair():
    text = "Alice traveled. " * 2000
    invalid = {"characters": [{"character_name": "A" * 10_000}]}
    provider = FakeProvider(
        [
            [
                AssistantDoneEvent(
                    reason="stop", message=AssistantMessage(content=json.dumps(invalid))
                )
            ],
            *[response([]) for _ in range(20)],
        ]
    )
    assert (
        await CharacterProfilerAgent(
            provider=provider, model="fake", max_context_characters=20_000
        ).run(book(chapter(text)))
        == []
    )
    assert "Validation error" in message_text(provider.calls[1][2][0])
    for _, system, messages, _ in provider.calls:
        assert len(system) + len(message_text(messages[0])) <= 20_000


async def test_fallback_fails_when_registry_leaves_no_room_for_text(tmp_path):
    provider = RegistryProvider([[update(description="A" * 20_000)]])
    with pytest.raises(CharacterProfilerError, match="no room for narrative text"):
        await CharacterProfilerAgent(
            provider=provider,
            model="fake",
            max_context_characters=20_000,
            workspace_root=tmp_path,
        ).run(book(chapter("Alice arrived."), chapter("Alice returned.")))
    assert len(provider.calls) == 1
    assert not list(tmp_path.iterdir())


async def test_chunk_limit_is_used_in_model_requests():
    provider = RegistryProvider([[], [], []])
    await CharacterProfilerAgent(
        provider=provider, model="fake", max_chunk_characters=8
    ).run(book(chapter("abcdefghijklmnopqrst")))
    assert [request["excerpt"]["text"] for request in provider.requests] == [
        "abcdefgh",
        "ijklmnop",
        "qrst",
    ]


async def test_progress_counts_leaves_in_reading_order_not_chunks_or_retries():
    provider = FakeProvider(
        [
            [
                AssistantDoneEvent(
                    reason="stop", message=AssistantMessage(content="invalid")
                )
            ],
            *[response([]) for _ in range(4)],
        ]
    )
    events = []
    source = book(
        chapter("Credits.", narrative=False),
        StructureNode(
            structure_type="part",
            structure_title="Part One",
            is_part_of_narrative=True,
            content=[
                chapter("abcdefghijklmnopqrst", title="First"),
                chapter("Done.", title="Second"),
            ],
        ),
    )
    await CharacterProfilerAgent(
        provider=provider,
        model="fake",
        callbacks=[events.append],
        max_chunk_characters=8,
    ).run(source)
    progress = [
        trace.event
        for trace in events
        if trace.event.type
        in {
            "profiling_start",
            "node_start",
            "node_end",
            "profiles_ready",
        }
    ]
    assert [event.type for event in progress] == [
        "profiling_start",
        "node_start",
        "node_end",
        "node_start",
        "node_end",
        "profiles_ready",
    ]
    assert progress[0].details == {"total_nodes": 2, "completed_nodes": 0}
    assert (
        progress[1].details
        == progress[2].details
        == {
            "node_index": 1,
            "total_nodes": 2,
            "path": ("Part One", "First"),
        }
    )
    assert (
        progress[3].details
        == progress[4].details
        == {
            "node_index": 2,
            "total_nodes": 2,
            "path": ("Part One", "Second"),
        }
    )
    assert len(provider.calls) == 5


async def test_failed_node_is_not_reported_complete():
    provider = FakeProvider(
        [
            [
                AssistantDoneEvent(
                    reason="stop", message=AssistantMessage(content="invalid")
                )
            ],
        ]
    )
    events = []
    with pytest.raises(CharacterProfilerError):
        await CharacterProfilerAgent(
            provider=provider,
            model="fake",
            max_repair_attempts=0,
            callbacks=[events.append],
        ).run(book(chapter("Alice arrived.")))
    assert any(trace.event.type == "node_start" for trace in events)
    assert not any(
        trace.event.type in {"node_end", "profiles_ready"} for trace in events
    )
    assert events[-1].event.type == "run_error"


@pytest.mark.parametrize("nodes", [[], [chapter("Thanks to Jane.", narrative=False)]])
async def test_no_narrative_needs_no_model_calls(nodes, tmp_path):
    provider = FakeProvider([])
    assert (
        await CharacterProfilerAgent(
            provider=provider, model="fake", workspace_root=tmp_path
        ).run(book(*nodes))
        == []
    )
    assert not provider.calls
    assert json.loads(next(tmp_path.rglob("character_profiles.json")).read_text()) == []


@pytest.mark.parametrize(
    "invalid",
    [
        {"characters": [update(ids=["missing"])]},
        {"characters": [update(name=" ")]},
        {"characters": [dict(update(), book_id="wrong")]},
        {"characters": "invalid"},
    ],
)
async def test_invalid_output_repaired_without_partial_updates(invalid):
    provider = FakeProvider(
        [
            [
                AssistantDoneEvent(
                    reason="stop", message=AssistantMessage(content=json.dumps(invalid))
                )
            ],
            response([update()]),
        ]
    )
    events = []
    profiles = await CharacterProfilerAgent(
        provider=provider, model="fake", callbacks=[events.append]
    ).run(book(chapter("Alice arrived.")))
    assert len(profiles) == 1
    assert "Validation error" in message_text(provider.calls[1][2][0])
    assert any(event.event.type == "validation_failed" for event in events)


async def test_invalid_reconciliation_publishes_nothing(tmp_path):
    provider = RegistryProvider([[update(), update("Bob")], [update("Invented")]])
    with pytest.raises(CharacterProfilerError, match="cannot introduce"):
        await CharacterProfilerAgent(
            provider=provider,
            model="fake",
            workspace_root=tmp_path,
            max_repair_attempts=0,
        ).run(book(chapter("Alice met Bob.")))
    assert not list(tmp_path.iterdir())


async def test_same_existing_id_cannot_be_updated_twice():
    def duplicate(request):
        identity = request["registry"][0]["id"]
        return [update(ids=[identity]), update(ids=[identity])]

    provider = RegistryProvider([[update()], duplicate])
    with pytest.raises(CharacterProfilerError, match="more than once"):
        await CharacterProfilerAgent(
            provider=provider, model="fake", max_repair_attempts=0
        ).run(book(chapter("Alice arrived."), chapter("Alice returned.")))


async def test_repairs_are_bounded_and_invalid_json_is_not_saved(tmp_path):
    provider = FakeProvider(
        [
            [
                AssistantDoneEvent(
                    reason="stop", message=AssistantMessage(content="not JSON")
                )
            ]
            for _ in range(3)
        ]
    )
    with pytest.raises(CharacterProfilerError, match="validation failed"):
        await CharacterProfilerAgent(
            provider=provider, model="fake", workspace_root=tmp_path
        ).run(book(chapter("Alice arrived.")))
    assert len(provider.calls) == 3
    assert not list(tmp_path.iterdir())


async def test_model_error_propagates():
    provider = FakeProvider(
        [
            [
                AssistantErrorEvent(
                    reason="error",
                    error=AssistantMessage(
                        content="", stop_reason="error", error_message="Model failed"
                    ),
                )
            ]
        ]
    )
    with pytest.raises(CharacterProfilerError, match="Model failed"):
        await CharacterProfilerAgent(provider=provider, model="fake").run(
            book(chapter("Alice arrived."))
        )


class WaitingProvider(FakeProvider):
    def __init__(self):
        super().__init__([])
        self.started = asyncio.Event()
        self.closed = asyncio.Event()

    def stream_response(self, **kwargs):
        async def stream():
            self.started.set()
            try:
                await asyncio.Event().wait()
                yield
            finally:
                self.closed.set()

        return stream()


async def test_cancellation_before_start():
    signal = SimpleCancellationToken()
    signal.cancel()
    provider = FakeProvider([])
    with pytest.raises(asyncio.CancelledError):
        await CharacterProfilerAgent(provider=provider, model="fake").run(
            book(), signal=signal
        )
    assert not provider.calls


async def test_cancellation_interrupts_waiting_model():
    provider = WaitingProvider()
    signal = SimpleCancellationToken()
    task = asyncio.create_task(
        CharacterProfilerAgent(provider=provider, model="fake").run(
            book(chapter("Alice arrived.")), signal=signal
        )
    )
    await asyncio.wait_for(provider.started.wait(), 2)
    signal.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 2)
    assert provider.closed.is_set()


async def test_timeout_and_context_limit():
    provider = WaitingProvider()
    with pytest.raises(CharacterProfilerError, match="timed out"):
        await CharacterProfilerAgent(
            provider=provider, model="fake", timeout_seconds=0.05
        ).run(book(chapter("Alice arrived.")))
    assert provider.closed.is_set()
    provider = FakeProvider([])
    with pytest.raises(CharacterProfilerError, match="max_context_characters"):
        await CharacterProfilerAgent(
            provider=provider, model="fake", max_context_characters=1
        ).run(book(chapter("Alice arrived.")))
    assert not provider.calls


@pytest.mark.parametrize(
    "options",
    [
        {"max_chunk_characters": 0},
        {"max_context_characters": 0},
        {"max_repair_attempts": -1},
        {"timeout_seconds": 0},
    ],
)
def test_invalid_limits(options):
    with pytest.raises(ValueError):
        CharacterProfilerAgent(provider=FakeProvider([]), model="fake", **options)


async def test_reusable_agent_keeps_run_state_isolated(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    provider = RegistryProvider([[update()], []])
    agent = CharacterProfilerAgent(
        provider=provider, model="fake", workspace_root="runs"
    )
    assert len(await agent.run(book(chapter("Alice arrived.")))) == 1
    assert await agent.run(book(chapter("An empty landscape."))) == []
    assert provider.requests[1]["registry"] == []
    assert len(set(provider.session_ids)) == 2
    assert Path.cwd() == tmp_path
    assert len(list((tmp_path / "runs").glob("*/character_profiles.json"))) == 2


async def test_references_are_remapped_after_merges_and_ids_stay_private():
    provider = RegistryProvider(
        [
            [update("Alice"), update("Al"), update("Bob")],
            [update("Alice", ids=["c2", "c1"])],
            [update("Bob", ids=["c2"], description="Bob returned.")],
            [],
        ]
    )
    profiles = await CharacterProfilerAgent(provider=provider, model="fake").run(
        book(
            chapter("Alice, Al, Bob."),
            chapter("Alice is Al."),
            chapter("Bob returned."),
        )
    )
    assert [p.character_name for p in profiles] == ["Alice", "Bob"]
    assert profiles[1].id == uuid5(NAMESPACE_URL, json.dumps(["test-book", 1, 2])).hex
    assert profiles[1].character_profile_description == "Bob returned."
    assert [p["id"] for p in provider.requests[2]["registry"]] == ["c1", "c2"]
    for request in provider.requests:
        encoded = json.dumps(request)
        assert "book_id" not in encoded
        assert all(profile.id not in encoded for profile in profiles)


async def test_reference_repair_identifies_bad_update_and_keeps_registry_unchanged():
    provider = FakeProvider(
        [
            response([update()]),
            response(
                [
                    update(ids=["c1"], description="Rejected description."),
                    update("Bob", ids=["c99"]),
                ]
            ),
            response([update("Bob")]),
            response([]),
        ]
    )
    profiles = await CharacterProfilerAgent(provider=provider, model="fake").run(
        book(chapter("Alice."), chapter("Alice met Bob."))
    )
    repaired_request = message_text(provider.calls[2][2][0])
    assert "characters[1] ('Bob')" in repaired_request
    assert "unknown character reference(s) ['c99']" in repaired_request
    assert "Valid references in this request: c1" in repaired_request
    assert "existing_ids=[]" in repaired_request
    original_request = message_text(provider.calls[1][2][0])
    assert repaired_request.startswith(original_request)
    assert profiles[0].character_profile_description == "A curious traveler."
    assert [p.character_name for p in profiles] == ["Alice", "Bob"]


@pytest.mark.parametrize("maximum", [1, 2, 3, 10])
async def test_groups_consecutive_narrative_leaves_with_paths_and_partial_last_group(
    maximum,
):
    source = book(
        chapter("Credits.", narrative=False),
        StructureNode(
            structure_type="part",
            structure_title="Part One",
            is_part_of_narrative=True,
            content=[chapter(f"Text {i}.", title=f"Scene {i}") for i in range(3)],
        ),
        chapter("", raw=" "),
        chapter("Text 3.", title="Final"),
    )
    provider = RegistryProvider([[]] * 4)
    await CharacterProfilerAgent(
        provider=provider, model="fake", max_nodes_group=maximum
    ).run(source)
    groups = [
        request["excerpt"].get("nodes", [request["excerpt"]])
        for request in provider.requests
    ]
    assert [len(group) for group in groups] == [
        min(maximum, 4 - i) for i in range(0, 4, maximum)
    ]
    assert [(node["path"], node["text"]) for group in groups for node in group] == [
        (["Part One", "Scene 0"], "Text 0."),
        (["Part One", "Scene 1"], "Text 1."),
        (["Part One", "Scene 2"], "Text 2."),
        (["Final"], "Text 3."),
    ]


async def test_grouping_carries_registry_between_requests_and_reconciles_once():
    provider = RegistryProvider(
        [
            [update()],
            [update(ids=["c1"], description="Alice now knows Bob."), update("Bob")],
            [],
        ]
    )
    profiles = await CharacterProfilerAgent(
        provider=provider, model="fake", max_nodes_group=2
    ).run(
        book(
            *(
                chapter(text)
                for text in ["Alice.", "Alice waited.", "Bob.", "Alice met Bob."]
            )
        )
    )
    assert [request["mode"] for request in provider.requests] == [
        "extract",
        "extract",
        "reconcile",
    ]
    assert provider.requests[1]["registry"][0]["id"] == "c1"
    assert profiles[0].id == uuid5(NAMESPACE_URL, json.dumps(["test-book", 1, 0])).hex
    assert profiles[0].character_profile_description == "Alice now knows Bob."


async def test_group_limit_respects_serialized_context_budget_and_repair_headroom():
    from diorama.agents.character_profiler_agent.agent import _Updates
    from diorama.agents.character_profiler_agent.prompts import (
        build_extraction_prompt,
        build_system_prompt,
    )

    text = 'Snow 雪 "quoted" \\ ' * 100
    source = book(*(chapter(text, title=str(i)) for i in range(3)))
    nodes = [{"path": [str(i)], "text": text.strip()} for i in range(2)]
    size = len(build_system_prompt(_Updates.model_json_schema())) + len(
        build_extraction_prompt(source.metadata.title, [], {"nodes": nodes})
    )
    limit = size + 4096
    provider = RegistryProvider([[], []])
    profiler = CharacterProfilerAgent(
        provider=provider, model="fake", max_nodes_group=3, max_context_characters=limit
    )
    await profiler.run(source)
    assert len(provider.requests[0]["excerpt"]["nodes"]) == 2
    assert provider.requests[1]["excerpt"]["path"] == ["2"]
    for _, system, messages, _ in provider.calls:
        assert (
            len(system) + len(message_text(messages[0])) + profiler._repair_headroom
            <= limit
        )


async def test_oversized_node_splits_before_grouping_later_nodes_without_losing_text():
    provider = RegistryProvider([[]] * 4)
    await CharacterProfilerAgent(
        provider=provider, model="fake", max_nodes_group=3, max_chunk_characters=8
    ).run(book(chapter("abcdefghijklmnopqrst"), chapter("One."), chapter("Two.")))
    assert [r["excerpt"]["text"] for r in provider.requests[:3]] == [
        "abcdefgh",
        "ijklmnop",
        "qrst",
    ]
    assert [n["text"] for n in provider.requests[3]["excerpt"]["nodes"]] == [
        "One.",
        "Two.",
    ]


@pytest.mark.parametrize("maximum", [0, -1, 1.5, True, "2"])
def test_invalid_group_limits(maximum):
    with pytest.raises(ValueError, match="max_nodes_group must be a positive integer"):
        CharacterProfilerAgent(
            provider=FakeProvider([]), model="fake", max_nodes_group=maximum
        )
