"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  chooseStyleDirection,
  getResearch,
  researchStreamUrl,
  retryResearch,
} from "./api";
import type { ResearchRecord, StyleDirection, TraceLine } from "./types";

/**
 * One book's research: the stored record, the live trace, and the two actions.
 *
 * Owned by the reader rather than the moodboard modal, for the decision that
 * *closing the modal must not cancel the run*. The `EventSource` lives here and stays
 * open while the reader is on the page, so dismissing the modal only stops anyone
 * looking at it — the run keeps going, the chrome keeps showing that it is, and
 * reopening rejoins a stream that never dropped.
 *
 * `begin()` is what starts research at all: the stream endpoint launches the run as a
 * side effect of being opened, because opening the moodboard *is* the request. It is
 * safe to call repeatedly — it starts at most one attempt per book per visit, and the
 * backend joins an existing run rather than starting a rival one. Only `retry()` will
 * start a second attempt, so a stream that fails can't be re-opened in a loop.
 */
export interface Research {
  /** The stored artifacts. Null until loaded, or when nobody has researched this book. */
  record: ResearchRecord | null;
  /** False until the first fetch settles — distinguishes "none" from "not yet known". */
  loaded: boolean;
  lines: TraceLine[];
  /** A run is in flight and we're watching it. */
  streaming: boolean;
  /** Set when the stream itself failed (backend down), not when a *run* failed. */
  connectionError: string | null;
  begin: () => void;
  retry: () => void;
  choose: (direction: StyleDirection) => void;
}

export function useResearch(bookId: string): Research {
  const [record, setRecord] = useState<ResearchRecord | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [lines, setLines] = useState<TraceLine[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [connectionError, setConnectionError] = useState<string | null>(null);

  const sourceRef = useRef<EventSource | null>(null);
  /** Guards against a fetch that resolves after the reader has moved on. */
  const aliveRef = useRef(true);
  /** How many lines this run has produced, readable without depending on state. */
  const receivedRef = useRef(0);
  /**
   * Whether a start has already been attempted for this book on this page.
   *
   * `begin()` is called from an effect that re-runs whenever `streaming` changes, so
   * without this a stream that fails immediately (backend down) would be retried on
   * every settle — a hot loop against a server that is already not answering. Only an
   * explicit retry clears it.
   */
  const startedRef = useRef(false);

  useEffect(() => {
    aliveRef.current = true;
    startedRef.current = false;
    return () => {
      aliveRef.current = false;
      sourceRef.current?.close();
      sourceRef.current = null;
    };
  }, [bookId]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const existing = await getResearch(bookId);
        if (!cancelled) setRecord(existing);
      } catch {
        /* a moodboard that won't load is not a reason to fail the book */
      } finally {
        if (!cancelled) setLoaded(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [bookId]);

  const refetch = useCallback(async () => {
    try {
      const fresh = await getResearch(bookId);
      if (aliveRef.current) setRecord(fresh);
    } catch {
      /* leave the last known record in place */
    }
  }, [bookId]);

  const begin = useCallback(() => {
    if (sourceRef.current || startedRef.current) return;
    startedRef.current = true;
    setConnectionError(null);
    receivedRef.current = 0;
    const source = new EventSource(researchStreamUrl(bookId));
    sourceRef.current = source;
    setStreaming(true);

    const finish = () => {
      source.close();
      if (sourceRef.current === source) sourceRef.current = null;
      if (!aliveRef.current) return;
      setStreaming(false);
      void refetch();
    };

    source.onmessage = (event) => {
      let line: TraceLine;
      try {
        line = JSON.parse(event.data) as TraceLine;
      } catch {
        return;
      }
      if (!aliveRef.current) return;
      receivedRef.current += 1;
      // Merged by id, like the shelf's trace: a tool call's pending and done events
      // share the `tool_call_id`, so the row updates in place instead of doubling.
      setLines((previous) => {
        const at = previous.findIndex((existing) => existing.id === line.id);
        if (at === -1) return [...previous, line];
        const next = [...previous];
        next[at] = line;
        return next;
      });
      if (line.kind === "done" || line.kind === "error") finish();
    };

    source.onerror = () => {
      // The stream ends by closing the connection, so an error after the run has
      // settled is just that — only an error with nothing received is worth showing.
      source.close();
      if (sourceRef.current === source) sourceRef.current = null;
      if (!aliveRef.current) return;
      setStreaming(false);
      if (receivedRef.current === 0) {
        setConnectionError(
          "Lost the connection to Diorama's backend. Is it still running?",
        );
      }
      void refetch();
    };
  }, [bookId, refetch]);

  const retry = useCallback(() => {
    sourceRef.current?.close();
    sourceRef.current = null;
    startedRef.current = false;
    setLines([]);
    setConnectionError(null);
    void (async () => {
      try {
        await retryResearch(bookId);
      } catch {
        // Best-effort: the retry endpoint starts the fresh run, but if it didn't
        // land, opening the stream below starts one anyway.
      }
      if (aliveRef.current) begin();
    })();
  }, [begin, bookId]);

  const choose = useCallback(
    (direction: StyleDirection) => {
      // Optimistic: the picker is a toggle, and waiting on a round trip to redraw a
      // selection the reader just made would feel broken rather than careful.
      setRecord((previous) =>
        previous ? { ...previous, chosen_direction: direction } : previous,
      );
      void chooseStyleDirection(bookId, direction)
        .then((updated) => {
          if (aliveRef.current) setRecord(updated);
        })
        .catch(() => {
          void refetch();
        });
    },
    [bookId, refetch],
  );

  return {
    record,
    loaded,
    lines,
    streaming,
    connectionError,
    begin,
    retry,
    choose,
  };
}
