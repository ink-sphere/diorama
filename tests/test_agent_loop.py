"""Tests for the agent's loop capabilities: cancellation, events, state, compaction,
sessions, and message queues.

Like the rest of the suite these run entirely against :class:`FakeModel`.
"""

from __future__ import annotations

from typing import Any

import pytest

from diorama.core import (
    CalculatorTool,
    ContextCompactor,
    JsonlSessionStore,
    ReactAgent,
    SessionState,
    Tool,
    ToolParameter,
    estimate_context_tokens,
)
from diorama.core.context import split_for_compaction
from diorama.core.session import SessionEntry, SessionTreeError, path_to_entry
from tests.fakes import FakeModel, response, tool_call

# A hook the fake tools below call, rebound per test.
HOOK: list[Any] = [lambda: None]


def _agent(model: FakeModel, tools=None, **kwargs) -> ReactAgent:
    kwargs.setdefault("auto_compact", False)
    return ReactAgent(tools if tools is not None else [], model=model, **kwargs)


class HookTool(Tool):
    """Calls ``HOOK[0]`` when executed — used to poke the agent mid-run."""

    tool_name: str = "hook"
    description: str = "Trigger the test hook."
    parameters: list[ToolParameter] = []

    async def forward(self) -> Any:  # noqa: D102
        HOOK[0]()
        return "hooked"


def _hook_call(call_id: str = "h1"):
    return tool_call(call_id, "hook", "{}")


# --------------------------------------------------------------------------- #
# Cancellation
# --------------------------------------------------------------------------- #
async def test_cancel_during_tool_stops_the_run():
    model = FakeModel([response(tool_calls=[_hook_call()])], loop_last=True)
    agent = _agent(model, [HookTool()])
    HOOK[0] = agent.cancel

    result = await agent.run("go")

    assert result.stop_reason == "cancelled"
    assert result.completed is False
    assert result.steps == 1
    # The tool that ran before cancellation still recorded its result.
    assert [m["content"] for m in agent.messages if m["role"] == "tool"] == ["hooked"]


async def test_cancel_answers_remaining_tool_calls_in_the_same_turn():
    model = FakeModel(
        [response(tool_calls=[_hook_call("h1"), tool_call("h2", "calculator", "{}")])],
        loop_last=True,
    )
    agent = _agent(model, [HookTool(), CalculatorTool()])
    HOOK[0] = agent.cancel

    await agent.run("go")

    tool_msgs = [m for m in agent.messages if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tool_msgs] == ["h1", "h2"]
    assert "interrupted" in tool_msgs[1]["content"]


async def test_cancelling_a_follow_up_turn_clears_earlier_completion():
    model = FakeModel(
        [response(content="first"), response(tool_calls=[_hook_call()])],
        loop_last=True,
    )
    agent = _agent(model, [HookTool()])
    HOOK[0] = agent.cancel
    agent.follow_up("and then this")

    result = await agent.run("go")

    # Turn 1 settled, but the cancelled follow-up turn must not report completion.
    assert result.stop_reason == "cancelled"
    assert result.completed is False
    assert result.steps == 2


async def test_orphaned_tool_calls_are_repaired_before_the_next_run():
    agent = _agent(FakeModel([response(content="ok")]))
    # Simulate a run that died mid-turn: an assistant tool call with no result.
    agent.messages.append(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "orphan",
                    "type": "function",
                    "function": {"name": "calculator", "arguments": "{}"},
                }
            ],
        }
    )

    await agent.run("carry on")

    tool_msgs = [m for m in agent.messages if m["role"] == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["tool_call_id"] == "orphan"
    assert "interrupted" in tool_msgs[0]["content"]
    # The repair lands before the new user message, keeping the transcript valid.
    roles = [m["role"] for m in agent.messages]
    assert roles.index("tool") < roles.index("user")


async def test_empty_assistant_turn_is_not_replayed_to_the_provider():
    # The empty reply is retried (see the empty-response tests below), so the first
    # run consumes two responses before it settles.
    model = FakeModel(
        [response(content=None), response(content="first"), response(content="second")]
    )
    agent = _agent(model)

    await agent.run("one")
    await agent.run("two")

    # History keeps the empty turn, but it never reaches the provider.
    assert any(
        m["role"] == "assistant" and m["content"] is None for m in agent.messages
    )
    sent = model.calls[-1]["messages"]
    assert not any(
        m["role"] == "assistant" and not m.get("content") and not m.get("tool_calls")
        for m in sent
    )


# --------------------------------------------------------------------------- #
# Events
# --------------------------------------------------------------------------- #
async def test_event_stream_shape():
    model = FakeModel([response(tool_calls=[_hook_call()]), response(content="done")])
    agent = _agent(model, [HookTool()])
    HOOK[0] = lambda: None
    seen: list[Any] = []
    agent.subscribe(seen.append)

    await agent.run("go")

    types = [e.type for e in seen]
    assert types[0] == "agent_start"
    assert types[-1] == "agent_end"
    assert types.count("turn_start") == 2
    assert types.count("turn_end") == 2
    assert "tool_execution_start" in types
    assert "tool_execution_end" in types
    # Every tool execution is paired with the message that carries its result.
    end = next(e for e in seen if e.type == "tool_execution_end")
    assert end.is_error is False
    assert end.output == "hooked"


async def test_tool_failure_is_flagged_on_the_event():
    model = FakeModel(
        [
            response(
                tool_calls=[tool_call("c1", "calculator", '{"expression": "1/0"}')]
            ),
            response(content="ok"),
        ]
    )
    agent = _agent(model, [CalculatorTool()])
    seen: list[Any] = []
    agent.subscribe(seen.append)

    await agent.run("go")

    end = next(e for e in seen if e.type == "tool_execution_end")
    assert end.is_error is True


async def test_async_subscribers_are_awaited():
    calls: list[str] = []

    async def listener(event):
        calls.append(event.type)

    agent = _agent(FakeModel([response(content="hi")]))
    agent.subscribe(listener)
    await agent.run("go")

    assert "agent_end" in calls


async def test_unsubscribe_stops_delivery():
    seen: list[Any] = []
    agent = _agent(FakeModel([response(content="a"), response(content="b")]))
    unsubscribe = agent.subscribe(seen.append)

    await agent.run("one")
    count = len(seen)
    unsubscribe()
    await agent.run("two")

    assert len(seen) == count


async def test_stream_events_yields_and_sets_is_running():
    agent = _agent(FakeModel([response(content="hi")]))
    events = agent.stream_events("go")
    assert agent.is_running is True

    collected = [e async for e in events]

    assert agent.is_running is False
    assert [collected[0].type, collected[-1].type] == ["agent_start", "agent_end"]


async def test_reentrant_run_is_rejected():
    agent = _agent(FakeModel([response(content="hi")], loop_last=True))
    events = agent.stream_events("go")
    await events.__anext__()
    try:
        with pytest.raises(RuntimeError, match="already running"):
            await agent.run("again")
    finally:
        await events.aclose()


# --------------------------------------------------------------------------- #
# Statefulness
# --------------------------------------------------------------------------- #
async def test_history_persists_across_runs():
    model = FakeModel([response(content="first"), response(content="second")])
    agent = _agent(model)

    await agent.run("one")
    result = await agent.run("two")

    assert [m["role"] for m in agent.messages].count("system") == 1
    assert [m["content"] for m in agent.messages if m["role"] == "user"] == [
        "one",
        "two",
    ]
    assert result.final_answer == "second"
    # The second call saw the first exchange.
    assert len(model.calls[1]["messages"]) > len(model.calls[0]["messages"])


async def test_continue_runs_without_a_new_user_message():
    model = FakeModel([response(content="a"), response(content="b")])
    agent = _agent(model)

    await agent.run("one")
    result = await agent.continue_()

    assert result.final_answer == "b"
    assert [m["content"] for m in agent.messages if m["role"] == "user"] == ["one"]


async def test_reset_clears_history():
    agent = _agent(FakeModel([response(content="a")]))
    await agent.run("one")
    agent.reset()

    assert [m["role"] for m in agent.messages] == ["system"]


async def test_reset_starts_a_new_root_in_the_session(tmp_path):
    store = JsonlSessionStore(tmp_path / "s.jsonl")
    agent = _agent(
        FakeModel([response(content="a"), response(content="b")]), session=store
    )
    await agent.run("one")
    agent.reset()
    await agent.run("two")

    # Replay matches memory exactly — the old conversation is a separate branch.
    assert JsonlSessionStore(tmp_path / "s.jsonl").state().messages == agent.messages
    assert [m["content"] for m in agent.messages if m["role"] == "user"] == ["two"]
    assert len(store.leaf_ids()) == 2


async def test_truncated_reply_is_not_reported_as_completed():
    model = FakeModel([response(content="half an ans", finish_reason="length")])
    result = await _agent(model).run("go")

    assert result.stop_reason == "length"
    assert result.completed is False
    assert result.final_answer == "half an ans"


# --------------------------------------------------------------------------- #
# Steering + follow-ups
# --------------------------------------------------------------------------- #
async def test_steering_message_is_injected_at_the_turn_boundary():
    model = FakeModel(
        [response(tool_calls=[_hook_call()]), response(content="acknowledged")]
    )
    agent = _agent(model, [HookTool()])
    HOOK[0] = lambda: agent.steer("actually, stop after this")

    result = await agent.run("go")

    user_messages = [m["content"] for m in agent.messages if m["role"] == "user"]
    assert user_messages == ["go", "actually, stop after this"]
    assert result.final_answer == "acknowledged"
    assert agent.queued_messages.count == 0


async def test_follow_up_starts_another_turn_after_the_agent_settles():
    model = FakeModel([response(content="first"), response(content="second")])
    agent = _agent(model)
    agent.follow_up("and now this")

    result = await agent.run("do this")

    assert result.steps == 2
    assert result.final_answer == "second"
    assert [m["content"] for m in agent.messages if m["role"] == "user"] == [
        "do this",
        "and now this",
    ]


async def test_queue_mode_all_drains_everything_at_once():
    model = FakeModel([response(content="first"), response(content="second")])
    agent = _agent(model, queue_mode="all")
    agent.follow_up("a")
    agent.follow_up("b")

    await agent.run("go")

    assert [m["content"] for m in agent.messages if m["role"] == "user"] == [
        "go",
        "a",
        "b",
    ]


async def test_clear_queues_discards_pending_messages():
    agent = _agent(FakeModel([response(content="only")]))
    agent.steer("x")
    agent.follow_up("y")
    discarded = agent.clear_queues()

    assert discarded.count == 2
    result = await agent.run("go")
    assert result.steps == 1


# --------------------------------------------------------------------------- #
# Context accounting + compaction
# --------------------------------------------------------------------------- #
def test_context_estimate_counts_tool_schemas():
    messages = [{"role": "user", "content": "hello"}]
    specs = [CalculatorTool().to_json_schema()]

    assert estimate_context_tokens(messages, specs) > estimate_context_tokens(messages)


def test_split_never_starts_the_tail_on_a_tool_message():
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "1"}]},
        {"role": "tool", "content": "x" * 400, "tool_call_id": "1", "name": "t"},
        {"role": "assistant", "content": "done"},
    ]
    _, tail = split_for_compaction(messages, keep_recent_tokens=10)

    assert tail and tail[0]["role"] != "tool"


class SummarizingModel(FakeModel):
    """A model that answers the compaction summariser separately from the loop."""

    async def acompletion(self, messages, tools=None, stream: bool = False):
        if messages and "summarization assistant" in (messages[0].get("content") or ""):
            self.summarized = True
            return response(content="SUMMARY OF EARLIER WORK")
        return await super().acompletion(messages, tools=tools, stream=stream)


def _bulky_history(agent: ReactAgent, pairs: int = 40) -> None:
    for i in range(pairs):
        agent.messages.append({"role": "user", "content": f"{i} " + "x" * 400})
        agent.messages.append({"role": "assistant", "content": f"{i} " + "y" * 400})


async def test_history_is_compacted_before_the_next_call():
    model = SummarizingModel([response(content="after compaction")])
    agent = ReactAgent(
        [],
        model=model,
        compactor=ContextCompactor(
            model,
            context_window_tokens=4_000,
            reserve_tokens=1_000,
            keep_recent_tokens=200,
        ),
    )
    _bulky_history(agent)
    seen: list[Any] = []
    agent.subscribe(seen.append)
    before = len(agent.messages)

    result = await agent.run("carry on")

    assert getattr(model, "summarized", False) is True
    assert len(agent.messages) < before
    assert agent.messages[0]["role"] == "system"
    assert agent.messages[1]["content"].startswith("Previous conversation summary:")
    assert "SUMMARY OF EARLIER WORK" in agent.messages[1]["content"]
    assert result.final_answer == "after compaction"

    types = [e.type for e in seen]
    assert types.index("compaction_start") < types.index("compaction_end")


async def test_compaction_is_skipped_below_the_threshold():
    model = SummarizingModel([response(content="hi")])
    agent = ReactAgent([], model=model, compactor=ContextCompactor(model))
    seen: list[Any] = []
    agent.subscribe(seen.append)

    await agent.run("go")

    assert getattr(model, "summarized", False) is False
    assert "compaction_start" not in [e.type for e in seen]


async def test_failed_summarisation_leaves_history_untouched():
    class BrokenSummarizer(SummarizingModel):
        async def acompletion(self, messages, tools=None, stream: bool = False):
            if messages and "summarization assistant" in (
                messages[0].get("content") or ""
            ):
                raise RuntimeError("summariser exploded")
            return await FakeModel.acompletion(
                self, messages, tools=tools, stream=stream
            )

    model = BrokenSummarizer([response(content="still fine")])
    agent = ReactAgent(
        [],
        model=model,
        compactor=ContextCompactor(
            model,
            context_window_tokens=4_000,
            reserve_tokens=1_000,
            keep_recent_tokens=200,
        ),
    )
    _bulky_history(agent)
    before = len(agent.messages)

    result = await agent.run("carry on")

    assert result.final_answer == "still fine"
    # Nothing was dropped: the run continues with full history rather than dying.
    assert len(agent.messages) > before
    assert not any(
        str(m.get("content") or "").startswith("Previous conversation summary:")
        for m in agent.messages
    )


# --------------------------------------------------------------------------- #
# Sessions
# --------------------------------------------------------------------------- #
async def test_session_records_and_resumes(tmp_path):
    path = tmp_path / "session.jsonl"
    first = _agent(
        FakeModel([response(content="answer one")]),
        session=JsonlSessionStore(path),
    )
    await first.run("question one")

    resumed = _agent(
        FakeModel([response(content="answer two")]),
        session=JsonlSessionStore(path),
    )

    assert [m["content"] for m in resumed.messages if m["role"] == "user"] == [
        "question one"
    ]
    await resumed.run("question two")
    assert [m["content"] for m in resumed.messages if m["role"] == "assistant"] == [
        "answer one",
        "answer two",
    ]

    # A third open sees the whole appended history.
    reopened = JsonlSessionStore(path)
    assert len(reopened.state().messages) == len(resumed.messages)


async def test_session_records_tool_results(tmp_path):
    store = JsonlSessionStore(tmp_path / "s.jsonl")
    model = FakeModel(
        [
            response(
                tool_calls=[tool_call("c1", "calculator", '{"expression": "6*7"}')]
            ),
            response(content="42"),
        ]
    )
    agent = _agent(model, [CalculatorTool()], session=store)
    await agent.run("compute")

    replayed = JsonlSessionStore(tmp_path / "s.jsonl").state().messages
    assert [m["content"] for m in replayed if m["role"] == "tool"] == ["42"]


async def test_branching_forks_history_without_losing_the_old_path(tmp_path):
    store = JsonlSessionStore(tmp_path / "s.jsonl")
    agent = _agent(
        FakeModel([response(content="one"), response(content="two")]), session=store
    )
    await agent.run("first")
    fork_point = store.active_leaf_id
    await agent.run("second")

    assert len(agent.messages) == 5  # system + 2 exchanges

    agent.model = FakeModel([response(content="alternative")])
    agent.branch(fork_point)

    assert [m["content"] for m in agent.messages if m["role"] == "assistant"] == ["one"]
    await agent.run("second, differently")

    assert [m["content"] for m in agent.messages if m["role"] == "user"] == [
        "first",
        "second, differently",
    ]
    # Both branch tips survive in the file.
    assert len(store.leaf_ids()) == 2


async def test_compaction_is_reproduced_on_replay(tmp_path):
    store = JsonlSessionStore(tmp_path / "s.jsonl")
    model = SummarizingModel([response(content="after compaction")])
    agent = ReactAgent(
        [],
        model=model,
        session=store,
        compactor=ContextCompactor(
            model,
            context_window_tokens=4_000,
            reserve_tokens=1_000,
            keep_recent_tokens=200,
        ),
    )
    for i in range(40):
        agent._append({"role": "user", "content": f"{i} " + "x" * 400})
        agent._append({"role": "assistant", "content": f"{i} " + "y" * 400})

    await agent.run("carry on")

    # Compaction really happened, and replaying the file reproduces the compacted
    # list rather than the raw 80-message history that was written to it.
    assert agent.messages[1]["content"].startswith("Previous conversation summary:")
    assert len(agent.messages) < 20
    replayed = JsonlSessionStore(tmp_path / "s.jsonl").state().messages
    assert replayed == agent.messages


def test_session_tree_replay_follows_one_path():
    root = SessionEntry(
        id="a",
        entry_type="message",
        payload={"message": {"role": "system", "content": "s"}},
    )
    left = SessionEntry(
        id="b",
        parent_id="a",
        entry_type="message",
        payload={"message": {"role": "user", "content": "left"}},
    )
    right = SessionEntry(
        id="c",
        parent_id="a",
        entry_type="message",
        payload={"message": {"role": "user", "content": "right"}},
    )
    entries = [root, left, right]

    assert [m["content"] for m in SessionState.replay(entries, "b").messages] == [
        "s",
        "left",
    ]
    assert [m["content"] for m in SessionState.replay(entries, "c").messages] == [
        "s",
        "right",
    ]
    assert SessionState.replay(entries, None).messages == []


def test_session_tree_rejects_cycles_and_missing_entries():
    a = SessionEntry(id="a", parent_id="b", entry_type="info")
    b = SessionEntry(id="b", parent_id="a", entry_type="info")
    with pytest.raises(SessionTreeError, match="Cycle"):
        path_to_entry([a, b], "a")
    with pytest.raises(SessionTreeError, match="Missing"):
        path_to_entry([SessionEntry(id="a", entry_type="info")], "nope")


def test_branch_requires_a_session():
    agent = _agent(FakeModel([]))
    with pytest.raises(RuntimeError, match="session store"):
        agent.branch("whatever")


# --------------------------------------------------------------------------- #
# Closing the implicit exit
#
# The loop's normal rule — a turn ends when the model returns no tool calls — cannot
# by itself tell "the model is finished" apart from "the provider returned nothing"
# or "the model stopped with its actual job undone". Both were previously reported as
# a successful, completed run. These cover the two guards that close that gap.
# --------------------------------------------------------------------------- #
async def test_an_empty_reply_is_retried_rather_than_read_as_finished():
    """The observed failure: an empty candidate ended a run mid-task.

    ``finish_reason: "stop"``, no content, no tool calls, no usage — indistinguishable
    from a deliberate finish under the old rule.
    """
    model = FakeModel([response(content=None), response(content="Actually, here.")])
    agent = _agent(model)

    result = await agent.run("go")

    assert len(model.calls) == 2  # it asked again instead of giving up
    assert result.stop_reason == "completed"
    assert result.final_answer == "Actually, here."


async def test_a_persistently_empty_provider_stops_as_empty_response():
    """Bounded: a retry that never helps must not spin, and must not claim success."""
    model = FakeModel([response(content=None) for _ in range(3)])
    agent = _agent(model)

    result = await agent.run("go")

    assert len(model.calls) == 3  # the first reply plus max_empty_replies retries
    assert result.stop_reason == "empty_response"
    assert result.completed is False


async def test_the_empty_reply_budget_resets_after_a_productive_turn():
    """Two blips early shouldn't spend the budget protecting a blip much later."""
    model = FakeModel(
        [
            response(content=None),
            response(content=None),
            response(
                tool_calls=[tool_call("c1", "calculator", '{"expression": "1+1"}')]
            ),
            response(content=None),
            response(content="done"),
        ]
    )
    agent = _agent(model, [CalculatorTool()])

    result = await agent.run("go")

    assert len(model.calls) == 5
    assert result.stop_reason == "completed"
    assert result.final_answer == "done"


async def test_a_completion_guard_pushes_a_quiet_run_onward():
    """The guard turns a premature stop into one more turn, not a failed run."""
    submitted: list[str] = []
    model = FakeModel(
        [
            response(content="I think that's everything."),
            response(
                tool_calls=[tool_call("c1", "calculator", '{"expression": "2+2"}')]
            ),
            response(content="Now it's everything."),
        ]
    )
    agent = _agent(
        model,
        [CalculatorTool()],
        completion_guard=lambda: None if submitted else "You haven't done the sum yet.",
    )
    agent.subscribe(
        lambda event: (
            submitted.append("done")
            if getattr(event, "tool_name", None) == "calculator"
            else None
        )
    )

    result = await agent.run("go")

    assert len(model.calls) == 3
    assert result.stop_reason == "completed"
    assert result.completed is True
    # The nudge is a real user message, so the model can see what it was told.
    assert any(
        m["role"] == "user" and "haven't done the sum" in str(m.get("content"))
        for m in agent.messages
    )


async def test_an_unsatisfied_guard_stops_as_incomplete_not_completed():
    """The point of the whole change: stopping short is no longer indistinguishable
    from finishing."""
    model = FakeModel([response(content="Nothing more from me.") for _ in range(3)])
    agent = _agent(model, completion_guard=lambda: "You still owe an artifact.")

    result = await agent.run("go")

    assert len(model.calls) == 3  # the quiet turn plus max_completion_nudges
    assert result.stop_reason == "incomplete"
    assert result.completed is False


async def test_the_nudge_budget_is_configurable():
    model = FakeModel([response(content="no") for _ in range(5)])
    agent = _agent(
        model, completion_guard=lambda: "keep going", max_completion_nudges=4
    )

    result = await agent.run("go")

    assert len(model.calls) == 5
    assert result.stop_reason == "incomplete"


async def test_an_async_completion_guard_is_awaited():
    calls: list[int] = []

    async def guard() -> str | None:
        calls.append(1)
        return None if len(calls) > 1 else "once more"

    model = FakeModel([response(content="a"), response(content="b")])
    agent = _agent(model, completion_guard=guard)

    result = await agent.run("go")

    assert len(calls) == 2
    assert result.stop_reason == "completed"


async def test_no_guard_leaves_the_ordinary_ending_untouched():
    """A chat agent's deliverable *is* the closing message; nothing changes for it."""
    model = FakeModel([response(content="here you go")])
    agent = _agent(model)

    result = await agent.run("go")

    assert len(model.calls) == 1
    assert result.stop_reason == "completed"
    assert result.completed is True
    assert result.final_answer == "here you go"
