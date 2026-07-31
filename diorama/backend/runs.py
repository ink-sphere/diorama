"""The shared "one background run, many watchers" primitive.

Both of Diorama's live surfaces — upload processing (:mod:`diorama.backend.processing`)
and the moodboard's research pass (:mod:`diorama.backend.research`) — have the same
shape: one background task per book, an accumulating log of
:class:`~diorama.backend.models.TraceLine` rows, and any number of browser tabs that
may attach *after* the run started and must not miss what already happened.

:class:`RunLog` is that shape, and nothing more. It knows nothing about agents, books,
or HTTP; a subscriber gets every line logged so far replayed into its queue and then
joins the live tail, and :data:`DONE` closes it. Keeping it here rather than in either
consumer is what lets research reuse the mechanics without importing the upload
pipeline (and re-triggering it as a side effect of an import).

In-memory only, deliberately: a restarted server has no in-flight runs to resume, and
for a personal single-process tool the answer is to start a fresh run on the next
request rather than to persist a task queue.
"""

from __future__ import annotations

import asyncio

from diorama.backend.models import TraceLine

#: End-of-stream sentinel pushed to every subscriber queue once a run settles.
DONE = object()


class RunLog:
    """One run's accumulated trace, plus the queues currently watching it."""

    def __init__(self) -> None:
        self.log: list[TraceLine] = []
        self.subscribers: list[asyncio.Queue] = []
        self.finished = False
        self.task: asyncio.Task | None = None

    def subscribe(self) -> asyncio.Queue:
        """A queue primed with the whole log so far, then joined to the live tail.

        A run that has already settled hands back a queue that replays the log and
        ends immediately — so a late watcher sees the same story as an early one,
        which is what makes reopening the moodboard mid-run (or after it) work
        without any special-casing at the call site.
        """
        queue: asyncio.Queue = asyncio.Queue()
        for line in self.log:
            queue.put_nowait(line)
        if self.finished:
            queue.put_nowait(DONE)
        else:
            self.subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        if queue in self.subscribers:
            self.subscribers.remove(queue)

    def publish(self, line: TraceLine) -> None:
        self.log.append(line)
        for queue in self.subscribers:
            queue.put_nowait(line)

    def close(self) -> None:
        self.finished = True
        for queue in self.subscribers:
            queue.put_nowait(DONE)
        self.subscribers.clear()


__all__ = ["DONE", "RunLog"]
