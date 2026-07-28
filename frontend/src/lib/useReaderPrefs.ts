"use client";

import { useCallback, useSyncExternalStore } from "react";

/**
 * Typography settings behind the reader's "Aa" menu.
 *
 * These describe *this screen* — type size, leading, measure — so they live in
 * localStorage, unlike reading position, which is a fact about the book and is
 * persisted on the backend.
 *
 * The store is module-level and read through `useSyncExternalStore` so the server
 * renders defaults, the browser swaps in stored values after subscribing, and two
 * open tabs stay in agreement.
 */
export interface ReaderPrefs {
  /** Multiplier on the base type size. */
  fontScale: number;
  lineHeight: number;
  family: "serif" | "sans";
  /** Column measure in ch — how wide a page's text block is allowed to get. */
  measure: number;
}

export const DEFAULT_PREFS: ReaderPrefs = {
  fontScale: 1,
  lineHeight: 1.62,
  family: "serif",
  measure: 62,
};

export const FONT_SCALES = [0.85, 0.925, 1, 1.1, 1.2, 1.35, 1.5];
export const LINE_HEIGHTS = [1.4, 1.5, 1.62, 1.78, 1.95];
export const MEASURES = [52, 62, 72, 84];

const STORAGE_KEY = "diorama:reader-prefs";

let snapshot: ReaderPrefs = DEFAULT_PREFS;
let loaded = false;
const listeners = new Set<() => void>();

function readStored(): ReaderPrefs {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_PREFS;
    const parsed = JSON.parse(raw) as Partial<ReaderPrefs>;
    return { ...DEFAULT_PREFS, ...parsed };
  } catch {
    return DEFAULT_PREFS;
  }
}

function same(a: ReaderPrefs, b: ReaderPrefs): boolean {
  return (
    a.fontScale === b.fontScale &&
    a.lineHeight === b.lineHeight &&
    a.family === b.family &&
    a.measure === b.measure
  );
}

function publish(next: ReaderPrefs) {
  // getSnapshot must return a stable reference while nothing has changed, or
  // useSyncExternalStore re-renders forever.
  if (same(next, snapshot)) return;
  snapshot = next;
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  if (!loaded) {
    loaded = true;
    publish(readStored());
  }
  const onStorage = (event: StorageEvent) => {
    if (event.key === STORAGE_KEY) publish(readStored());
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", onStorage);
  };
}

function write(next: ReaderPrefs) {
  publish(next);
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  } catch {
    /* private mode / quota — preferences just won't persist */
  }
}

export function useReaderPrefs() {
  const prefs = useSyncExternalStore(
    subscribe,
    () => snapshot,
    () => DEFAULT_PREFS,
  );

  const update = useCallback((patch: Partial<ReaderPrefs>) => {
    write({ ...snapshot, ...patch });
  }, []);

  const reset = useCallback(() => write(DEFAULT_PREFS), []);

  return { prefs, update, reset };
}
