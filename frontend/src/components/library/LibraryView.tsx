"use client";

import { AnimatePresence, motion } from "framer-motion";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { BookIcon, CostsIcon, SettingsIcon } from "@/components/Icons";
import { ThemeToggle } from "@/components/ThemeToggle";
import { deleteBook, listBooks, retryBook, streamUrl, uploadBook } from "@/lib/api";
import type { BookRecord, TraceLine } from "@/lib/types";

import { BookCard } from "./BookCard";
import { matchesFilter, ShelfFilters, type ShelfFilter } from "./ShelfFilters";
import { UploadButton, UploadZone } from "./UploadZone";

/**
 * The shelf: the library API's client, and the only place that owns SSE streams.
 *
 * One `EventSource` per in-flight book, opened when the book first appears in a
 * pending state and closed the moment its run settles. Trace lines are merged **by
 * id**, never appended: a tool call emits a "pending" and a "done" line under the
 * same `tool_call_id`, so appending would both duplicate the row and collide on
 * React keys.
 */
const OFFLINE_MESSAGE =
  "Can't reach the Diorama backend. Start it with `uv run uvicorn diorama.backend.main:app --reload --port 8000`.";

export function LibraryView() {
  const [books, setBooks] = useState<BookRecord[]>([]);
  const [traces, setTraces] = useState<Record<string, TraceLine[]>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(0);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<ShelfFilter>("all");

  const sources = useRef(new Map<string, EventSource>());

  const refresh = useCallback(async () => {
    try {
      setBooks(await listBooks());
      setError(null);
    } catch {
      setError(OFFLINE_MESSAGE);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const items = await listBooks();
        if (!cancelled) {
          setBooks(items);
          setError(null);
        }
      } catch {
        if (!cancelled) setError(OFFLINE_MESSAGE);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const closeStream = useCallback((bookId: string) => {
    sources.current.get(bookId)?.close();
    sources.current.delete(bookId);
  }, []);

  // Open a stream for every book still being worked on, and close streams for
  // books that have settled or left the shelf.
  useEffect(() => {
    const pending = books.filter(
      (book) => book.status === "queued" || book.status === "processing",
    );
    const pendingIds = new Set(pending.map((book) => book.id));

    for (const [bookId, source] of sources.current) {
      if (!pendingIds.has(bookId)) {
        source.close();
        sources.current.delete(bookId);
      }
    }

    for (const book of pending) {
      if (sources.current.has(book.id)) continue;
      const source = new EventSource(streamUrl(book.id));

      source.onmessage = (event) => {
        const line = JSON.parse(event.data) as TraceLine;

        setTraces((current) => {
          const existing = current[book.id] ?? [];
          const at = existing.findIndex((item) => item.id === line.id);
          const next =
            at === -1
              ? [...existing, line]
              : existing.map((item, index) => (index === at ? line : item));
          return { ...current, [book.id]: next };
        });

        // The backend flips the record to "processing" as it starts, but the shelf
        // only learns that on a refetch — showing it the moment work is visibly
        // happening avoids a card that says "Queued" while its log scrolls.
        if (line.kind !== "done" && line.kind !== "error") {
          setBooks((current) =>
            current.map((item) =>
              item.id === book.id && item.status === "queued"
                ? { ...item, status: "processing" }
                : item,
            ),
          );
        }

        if (line.kind === "done" || line.kind === "error") {
          closeStream(book.id);
          void refresh();
        }
      };

      // A stream error (backend restarted mid-run) would otherwise have the browser
      // reconnect forever; drop it and let the next refresh re-establish state.
      source.onerror = () => closeStream(book.id);

      sources.current.set(book.id, source);
    }
  }, [books, closeStream, refresh]);

  useEffect(() => {
    const open = sources.current;
    return () => {
      for (const source of open.values()) source.close();
      open.clear();
    };
  }, []);

  const handleFiles = useCallback(async (files: File[]) => {
    setUploading((count) => count + files.length);
    for (const file of files) {
      try {
        const { book } = await uploadBook(file);
        setBooks((current) => [book, ...current.filter((it) => it.id !== book.id)]);
        setError(null);
      } catch (uploadError) {
        setError(
          uploadError instanceof Error
            ? uploadError.message
            : "That file couldn't be uploaded.",
        );
      } finally {
        setUploading((count) => Math.max(0, count - 1));
      }
    }
  }, []);

  const handleRetry = useCallback(
    async (bookId: string) => {
      closeStream(bookId);
      setTraces((current) => ({ ...current, [bookId]: [] }));
      try {
        const book = await retryBook(bookId);
        setBooks((current) =>
          current.map((item) => (item.id === bookId ? book : item)),
        );
      } catch {
        setError("Couldn't restart that book.");
      }
    },
    [closeStream],
  );

  const handleDelete = useCallback(
    async (bookId: string) => {
      closeStream(bookId);
      try {
        await deleteBook(bookId);
        setBooks((current) => current.filter((item) => item.id !== bookId));
      } catch {
        setError("Couldn't remove that book.");
      }
    },
    [closeStream],
  );

  const counts = useMemo(
    () => ({
      all: books.length,
      ready: books.filter((book) => book.status === "ready").length,
      working: books.filter(
        (book) => book.status === "queued" || book.status === "processing",
      ).length,
      failed: books.filter((book) => book.status === "failed").length,
    }),
    [books],
  );

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return books.filter((book) => {
      if (!matchesFilter(book.status, filter)) return false;
      if (!needle) return true;
      return `${book.title} ${book.author ?? ""}`.toLowerCase().includes(needle);
    });
  }, [books, filter, query]);

  return (
    <div className="min-h-full">
      <UploadZone onFiles={handleFiles} />

      <div className="mx-auto w-full max-w-6xl px-6 pb-24 sm:px-10">
        <header className="flex items-center justify-between py-6">
          <div className="flex items-baseline gap-3">
            <span className="font-serif text-[1.35rem] leading-none font-medium tracking-tight text-ink">
              Diorama
            </span>
            <span className="label hidden text-ink-faint sm:inline">Reading room</span>
          </div>
          <div className="flex items-center gap-2">
            <UploadButton onFiles={handleFiles} busy={uploading > 0} />
            <Link
              href="/costs"
              aria-label="Costs"
              className="grid size-9 place-items-center rounded-full text-ink-soft transition-colors hover:bg-shell-raised hover:text-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
            >
              <CostsIcon className="size-[18px]" />
            </Link>
            <Link
              href="/settings"
              aria-label="Settings"
              className="grid size-9 place-items-center rounded-full text-ink-soft transition-colors hover:bg-shell-raised hover:text-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
            >
              <SettingsIcon className="size-[18px]" />
            </Link>
            <ThemeToggle />
          </div>
        </header>

        <section className="border-t border-rule pt-14 pb-12">
          <motion.h1
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
            className="max-w-2xl font-serif text-[2.6rem] leading-[1.08] font-normal tracking-[-0.015em] text-ink sm:text-[3.2rem]"
          >
            Every book, mapped
            <span className="text-ink-faint"> before you open it.</span>
          </motion.h1>
          <motion.p
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5, delay: 0.06, ease: [0.16, 1, 0.3, 1] }}
            className="mt-5 max-w-xl font-serif text-[1.05rem] leading-relaxed text-ink-soft"
          >
            Drop an EPUB anywhere on this page. Diorama&apos;s loader reads it end to
            end, works out its real structure — acts, chapters, scenes — and shelves it
            ready to read.
          </motion.p>
        </section>

        <ShelfFilters
          query={query}
          onQuery={setQuery}
          filter={filter}
          onFilter={setFilter}
          counts={counts}
        />

        <AnimatePresence>
          {error ? (
            <motion.p
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: "auto" }}
              exit={{ opacity: 0, height: 0 }}
              className="mt-6 overflow-hidden text-[0.85rem] text-danger"
            >
              {error}
            </motion.p>
          ) : null}
        </AnimatePresence>

        <main className="mt-12">
          {loading ? (
            <ShelfSkeleton />
          ) : visible.length === 0 ? (
            <EmptyShelf hasBooks={books.length > 0} />
          ) : (
            <motion.div
              layout
              className="grid grid-cols-2 gap-x-8 gap-y-14 sm:grid-cols-3 lg:grid-cols-4 xl:gap-x-10"
            >
              <AnimatePresence mode="popLayout">
                {visible.map((book) => (
                  <BookCard
                    key={book.id}
                    book={book}
                    trace={traces[book.id] ?? []}
                    onRetry={handleRetry}
                    onDelete={handleDelete}
                  />
                ))}
              </AnimatePresence>
            </motion.div>
          )}
        </main>
      </div>
    </div>
  );
}

function ShelfSkeleton() {
  return (
    <div className="grid grid-cols-2 gap-x-8 gap-y-14 sm:grid-cols-3 lg:grid-cols-4">
      {Array.from({ length: 4 }).map((_, index) => (
        <div key={index} className="animate-pulse">
          <div className="aspect-[2/3] w-full rounded-[3px] bg-shell-raised ring-1 ring-rule" />
          <div className="mt-4 h-3 w-3/4 rounded-full bg-shell-raised" />
          <div className="mt-2.5 h-2.5 w-1/2 rounded-full bg-shell-raised" />
        </div>
      ))}
    </div>
  );
}

function EmptyShelf({ hasBooks }: { hasBooks: boolean }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
      className="flex flex-col items-center justify-center rounded-[4px] border border-dashed border-rule py-24 text-center"
    >
      <BookIcon className="size-6 text-ink-faint" />
      <p className="mt-5 font-serif text-lg text-ink">
        {hasBooks ? "Nothing matches that." : "The shelf is empty."}
      </p>
      <p className="mt-2 max-w-xs text-[0.86rem] leading-relaxed text-ink-soft">
        {hasBooks
          ? "Try a different title, author, or filter."
          : "Drop an EPUB anywhere on this page to add your first book."}
      </p>
    </motion.div>
  );
}
