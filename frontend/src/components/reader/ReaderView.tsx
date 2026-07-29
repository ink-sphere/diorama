"use client";

import { motion } from "framer-motion";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { getBook, getScenes, getStructure, saveProgress } from "@/lib/api";
import { readBook, type ReadableBook } from "@/lib/structure";
import type { BookRecord } from "@/lib/types";
import { useMediaQuery } from "@/lib/useMediaQuery";
import { usePagination } from "@/lib/usePagination";
import { useReaderPrefs } from "@/lib/useReaderPrefs";

import { PageNav, ReaderHeader } from "./ReaderChrome";
import { Spread } from "./Spread";
import { TocSidebar } from "./TocSidebar";

/**
 * The reader.
 *
 * Position is a (section, scene, page) triple, in descending order of stability. The
 * section indexes the structure's leaves and the scene indexes that section's scenes;
 * both survive a change of type size. The page does not — it only means something
 * relative to the current type size and viewport, both of which can change mid-read.
 * So the page is always derived from a measurement, and a restored position falls back
 * through those three: the exact page when the pagination still matches, else the
 * scene, else a ratio scaled into the new page count.
 */
export function ReaderView({ bookId }: { bookId: string }) {
  const [record, setRecord] = useState<BookRecord | null>(null);
  const [book, setBook] = useState<ReadableBook | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [sectionIndex, setSectionIndex] = useState(0);
  const [page, setPage] = useState(0);
  const [typeMenuOpen, setTypeMenuOpen] = useState(false);

  // The plate needs real width to be worth showing; below that the text page takes
  // the whole sheet. Matches the `lg:` breakpoint the spread itself uses.
  const wideEnoughForPlate = useMediaQuery("(min-width: 1024px)");
  const roomForContents = useMediaQuery("(min-width: 900px)");
  // Contents defaults to open on a wide window and closed on a narrow one, where it
  // would cover the page it navigates — until the reader says otherwise.
  const [contentsOverride, setContentsOverride] = useState<boolean | null>(null);
  const contentsOpen = contentsOverride ?? roomForContents;

  const { prefs, update, reset } = useReaderPrefs();

  const viewportRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);

  /** After a section change, land on its first or last page (arriving backwards). */
  const pendingEdge = useRef<"start" | "end" | null>(null);
  /** A saved position waiting for the first measurement to be resolved against. */
  const pendingRestore = useRef<{
    page: number;
    pages: number;
    scene: number | null;
  } | null>(null);

  const sections = useMemo(() => book?.sections ?? [], [book]);
  const section = sections[sectionIndex] ?? null;

  // Everything that changes the typesetting — including the arrival of the text
  // itself. Without the section's own identity in here, the first measurement runs
  // against an unmounted spread and never re-runs, leaving the reader on one
  // over-long page.
  const signature = [
    section?.startBlockId ?? -1,
    sectionIndex,
    prefs.fontScale,
    prefs.lineHeight,
    prefs.family,
    prefs.measure,
    wideEnoughForPlate,
    contentsOpen,
  ].join("|");

  const { pageCount, pageWidth, scenePages, measured } = usePagination(
    viewportRef,
    contentRef,
    signature,
  );

  // The scene the current page sits in: the last one that has started by now. -1
  // until the first measurement, which the plate reads as "nothing to name yet".
  const sceneIndex = useMemo(() => {
    let found = -1;
    for (let index = 0; index < scenePages.length; index += 1) {
      if (scenePages[index] <= page) found = index;
      else break;
    }
    return found;
  }, [page, scenePages]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        // Scenes are optional — `getScenes` resolves to null for a book that has
        // none, and the reader paginates continuously as it did before them.
        const [bookRecord, structure, scenes] = await Promise.all([
          getBook(bookId),
          getStructure(bookId),
          getScenes(bookId),
        ]);
        if (cancelled) return;
        const readable = readBook(structure, scenes);
        setRecord(bookRecord);
        setBook(readable);

        const progress = bookRecord.progress;
        if (progress && readable.sections.length > 0) {
          setSectionIndex(
            Math.min(Math.max(0, progress.section_index), readable.sections.length - 1),
          );
          pendingRestore.current = {
            page: progress.page,
            pages: progress.pages,
            scene: progress.scene_index ?? null,
          };
        }
      } catch {
        if (!cancelled) {
          setLoadError(
            "This book isn't ready to read yet. It may still be processing, or the backend may be offline.",
          );
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [bookId]);

  useEffect(() => {
    if (record?.title) document.title = `${record.title} · Diorama`;
  }, [record?.title]);

  // Resolve any pending landing point once the new pagination is known.
  useEffect(() => {
    if (!measured) return;

    const restore = pendingRestore.current;
    if (restore) {
      pendingRestore.current = null;
      setPage(resolveRestore(restore, pageCount, scenePages));
      return;
    }

    if (pendingEdge.current === "end") {
      pendingEdge.current = null;
      setPage(pageCount - 1);
      return;
    }
    pendingEdge.current = null;

    // A larger type size can shrink the section past the current page.
    setPage((current) => clamp(current, 0, pageCount - 1));
  }, [measured, pageCount, scenePages, sectionIndex]);

  const goToSection = useCallback(
    (index: number, edge: "start" | "end" = "start") => {
      if (index < 0 || index >= sections.length) return;
      pendingEdge.current = edge;
      setSectionIndex(index);
      setPage(edge === "end" ? 0 : 0);
    },
    [sections.length],
  );

  const next = useCallback(() => {
    if (page < pageCount - 1) {
      setPage(page + 1);
      return;
    }
    goToSection(sectionIndex + 1, "start");
  }, [goToSection, page, pageCount, sectionIndex]);

  const previous = useCallback(() => {
    if (page > 0) {
      setPage(page - 1);
      return;
    }
    goToSection(sectionIndex - 1, "end");
  }, [goToSection, page, sectionIndex]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target?.matches("input, textarea, [contenteditable]")) return;

      if (event.key === "ArrowRight" || event.key === "PageDown") {
        event.preventDefault();
        next();
      } else if (event.key === "ArrowLeft" || event.key === "PageUp") {
        event.preventDefault();
        previous();
      } else if (event.key === " ") {
        event.preventDefault();
        if (event.shiftKey) previous();
        else next();
      } else if (event.key === "Escape") {
        setTypeMenuOpen(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [next, previous]);

  // Cumulative word counts turn (section, page) into a percentage that reflects how
  // much book is actually behind you — sections vary wildly in length, so counting
  // sections alone would make a two-page preface worth as much as a long chapter.
  const wordTotals = useMemo(() => {
    const before: number[] = [];
    let running = 0;
    for (const item of sections) {
      before.push(running);
      running += Math.max(1, item.wordCount);
    }
    return { before, total: Math.max(1, running) };
  }, [sections]);

  const percent = useMemo(() => {
    if (!section) return 0;
    const within = ((page + 1) / Math.max(1, pageCount)) * Math.max(1, section.wordCount);
    return clamp((wordTotals.before[sectionIndex] + within) / wordTotals.total, 0, 1);
  }, [page, pageCount, section, sectionIndex, wordTotals]);

  // Persist the position, debounced: a fast page-turn run would otherwise write
  // once per key press.
  useEffect(() => {
    if (!book || !measured) return;
    const timer = window.setTimeout(() => {
      void saveProgress(bookId, {
        section_index: sectionIndex,
        scene_index: sceneIndex >= 0 ? sceneIndex : null,
        page,
        pages: pageCount,
        percent,
      }).catch(() => {
        /* a lost position is not worth interrupting the read for */
      });
    }, 700);
    return () => window.clearTimeout(timer);
  }, [book, bookId, measured, page, pageCount, percent, sceneIndex, sectionIndex]);

  if (loadError) {
    return (
      <div className="grid min-h-screen place-items-center px-6 text-center">
        <div>
          <p className="font-serif text-xl text-ink">Can&apos;t open this book</p>
          <p className="mx-auto mt-3 max-w-sm text-[0.9rem] leading-relaxed text-ink-soft">
            {loadError}
          </p>
          <Link
            href="/"
            className="label mt-6 inline-block rounded-full border border-rule-strong px-4 py-2 text-ink-soft transition hover:border-ink hover:text-ink"
          >
            Back to the library
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-screen flex-col overflow-hidden">
      <ReaderHeader
        bookTitle={book?.title ?? record?.title ?? "…"}
        sectionHeading={section?.heading ?? null}
        contentsOpen={contentsOpen}
        onToggleContents={() => setContentsOverride(!contentsOpen)}
        typeMenuOpen={typeMenuOpen}
        onTypeMenuOpenChange={setTypeMenuOpen}
        prefs={prefs}
        onPrefsChange={update}
        onPrefsReset={reset}
      />

      <div className="flex min-h-0 flex-1">
        <TocSidebar
          open={contentsOpen && !!book}
          toc={book?.toc ?? []}
          currentSection={sectionIndex}
          onSelect={(index) => {
            goToSection(index, "start");
            // On a narrow window the sidebar sits over the page — picking a
            // chapter there means "take me to it", so get out of the way.
            if (!roomForContents) setContentsOverride(false);
          }}
        />

        <div className="flex min-w-0 flex-1 flex-col">
          <div className="min-h-0 flex-1 px-4 pt-4 pb-1 sm:px-8 sm:pt-6">
            {/* The sheet takes the whole width it is given — the text page is
                capped by `prefs.measure` and the plate by its own frame, so the
                room a wide window offers becomes margin on the paper rather
                than blank desk around it. */}
            <div className="mx-auto h-full w-full">
              {section ? (
                <Spread
                  section={section}
                  bookTitle={book?.title ?? ""}
                  page={page}
                  pageCount={pageCount}
                  pageWidth={pageWidth}
                  sceneIndex={sceneIndex}
                  measured={measured}
                  viewportRef={viewportRef}
                  contentRef={contentRef}
                  prefs={prefs}
                  showPlate={wideEnoughForPlate}
                />
              ) : (
                <SpreadSkeleton />
              )}
            </div>
          </div>

          <PageNav
            page={page}
            pageCount={pageCount}
            canGoBack={page > 0 || sectionIndex > 0}
            canGoForward={page < pageCount - 1 || sectionIndex < sections.length - 1}
            onPrevious={previous}
            onNext={next}
            onSelectPage={setPage}
          />
        </div>
      </div>
    </div>
  );
}

function SpreadSkeleton() {
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      className="h-full w-full rounded-[4px] bg-paper shadow-page ring-1 ring-rule page-surface"
    >
      <div className="flex h-full animate-pulse flex-col gap-4 px-[8%] py-[5%]">
        <div className="h-6 w-2/5 rounded-full bg-shell-raised" />
        <div className="mt-4 space-y-3">
          {Array.from({ length: 12 }).map((_, index) => (
            <div
              key={index}
              className="h-2.5 rounded-full bg-shell-raised"
              style={{ width: `${72 + ((index * 13) % 26)}%` }}
            />
          ))}
        </div>
      </div>
    </motion.div>
  );
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), Math.max(min, max));
}

/**
 * Turn a saved position into a page in the pagination that now exists.
 *
 * Three fallbacks, most faithful first:
 *
 * 1. **The same page**, when the section still breaks into the same number of pages —
 *    the overwhelmingly common case of closing a book and reopening it unchanged,
 *    where any cleverness would move the reader for no reason.
 * 2. **The scene's first page.** Scene boundaries don't move when the type size does,
 *    so this lands on the passage the reader was actually in. The page *within* a long
 *    scene is lost, but that number no longer refers to anything.
 * 3. **A scaled ratio**, for a book with no scenes at all.
 */
function resolveRestore(
  saved: { page: number; pages: number; scene: number | null },
  pageCount: number,
  scenePages: number[],
): number {
  if (saved.pages === pageCount && saved.page < pageCount) {
    return clamp(saved.page, 0, pageCount - 1);
  }
  if (saved.scene !== null && saved.scene >= 0 && saved.scene < scenePages.length) {
    return clamp(scenePages[saved.scene], 0, pageCount - 1);
  }
  const ratio = saved.pages > 1 ? saved.page / (saved.pages - 1) : 0;
  return clamp(Math.round(ratio * (pageCount - 1)), 0, pageCount - 1);
}
