"use client";

import { AnimatePresence, motion } from "framer-motion";
import { useEffect } from "react";

import { AlertIcon, CheckIcon, RetryIcon, SparkIcon } from "@/components/Icons";
import type { TraceLine } from "@/lib/types";
import type { Research } from "@/lib/useResearch";

import { MoodboardBoard } from "./MoodboardBoard";

/**
 * The moodboard: a book's research, as a floating modal over the spread.
 *
 * It has one job in two halves. Before research exists, **opening it is the request**
 * — the stream starts on open and the modal shows the agent's live trace, because
 * this run does legible work (reading the book, searching for its author, studying an
 * illustrator's plates) and watching it is a better loading state than a spinner.
 * Once artifacts exist it becomes the board itself.
 *
 * A partial run renders what it finished and says what it didn't, rather than
 * discarding two good artifacts because a third never arrived.
 */
export function Moodboard({
  open,
  onClose,
  bookTitle,
  research,
}: {
  open: boolean;
  onClose: () => void;
  bookTitle: string;
  research: Research;
}) {
  const { record, loaded, streaming, begin } = research;

  // Opening the moodboard is the request to research: nothing has been spent on this
  // book yet, so there is nothing to confirm first. Waits for the initial fetch to
  // settle so an already-researched book never starts a needless stream, and only
  // fires for a book with *no* record — a partial one waits for an explicit retry,
  // since re-running it costs money the reader didn't ask to spend twice.
  useEffect(() => {
    if (!open || !loaded || streaming) return;
    if (record === null) begin();
  }, [open, loaded, streaming, record, begin]);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        onClose();
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [open, onClose]);

  return (
    <AnimatePresence>
      {open ? (
        <motion.div
          className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto p-4 sm:p-8"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.2 }}
        >
          <button
            type="button"
            aria-label="Close the moodboard"
            onClick={onClose}
            className="fixed inset-0 cursor-default bg-ink/25 backdrop-blur-[2px]"
          />

          <motion.div
            role="dialog"
            aria-modal="true"
            aria-label={`Moodboard for ${bookTitle}`}
            initial={{ opacity: 0, y: 14, scale: 0.985 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 8, scale: 0.99 }}
            transition={{ duration: 0.32, ease: [0.16, 1, 0.3, 1] }}
            className="relative my-auto w-full max-w-4xl rounded-[6px] bg-paper shadow-page ring-1 ring-rule page-surface"
          >
            <header className="flex items-start justify-between gap-4 border-b border-rule px-6 py-5 sm:px-8">
              <div>
                <p className="label text-ink-faint">Moodboard</p>
                <h2 className="mt-1 font-serif text-xl leading-tight text-ink">
                  {bookTitle}
                </h2>
              </div>
              <button
                type="button"
                onClick={onClose}
                aria-label="Close"
                className="label -mt-1 shrink-0 rounded-full px-3 py-2 text-ink-soft transition-colors hover:bg-shell-raised hover:text-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
              >
                Close
              </button>
            </header>

            <div className="px-6 py-6 sm:px-8 sm:py-8">
              <MoodboardBody research={research} bookTitle={bookTitle} />
            </div>
          </motion.div>
        </motion.div>
      ) : null}
    </AnimatePresence>
  );
}

function MoodboardBody({
  research,
  bookTitle,
}: {
  research: Research;
  bookTitle: string;
}) {
  const { record, loaded, streaming, lines, connectionError, retry, choose } =
    research;

  if (!loaded) return <BodySkeleton />;

  // The run is live, or has produced nothing yet: watching it *is* the state.
  if (streaming || (record === null && lines.length > 0)) {
    return <ResearchTrace lines={lines} bookTitle={bookTitle} />;
  }

  if (record === null) {
    return (
      <EmptyState
        title="Nothing researched yet"
        body={
          connectionError ??
          "Diorama couldn't start the research pass for this book."
        }
        onRetry={retry}
      />
    );
  }

  const nothingLanded =
    !record.author_profile && !record.world_dossier && !record.style_bibles;
  if (record.status === "partial" && nothingLanded) {
    return (
      <EmptyState
        title="The research didn't get anywhere"
        body={record.error ?? "The run stopped before it produced anything."}
        onRetry={retry}
      />
    );
  }

  return <MoodboardBoard record={record} onChoose={choose} onRetry={retry} />;
}

/**
 * The live trace, with the three artifacts pinned above it as milestones.
 *
 * Both at once on purpose: the trace is the interesting part and the milestones are
 * the legible part, and picking one would cost either the sense of progress or the
 * sense that something specific is happening.
 */
function ResearchTrace({
  lines,
  bookTitle,
}: {
  lines: TraceLine[];
  bookTitle: string;
}) {
  const milestones = [
    { tool: "submit_author_profile", label: "The author and their work" },
    { tool: "submit_world_dossier", label: "The world of the book" },
    { tool: "submit_style_bibles", label: "Two art directions" },
  ];
  const done = new Set(
    lines.filter((line) => line.status === "done").map((line) => line.tool ?? ""),
  );
  const failed = lines.some((line) => line.kind === "error");

  return (
    <div>
      <p className="max-w-prose font-serif text-[1.05rem] leading-relaxed text-ink-soft">
        Reading {bookTitle}, then looking up who wrote it, the world it takes place
        in, and how it has been pictured before.
      </p>

      <ol className="mt-6 space-y-2.5">
        {milestones.map((milestone) => {
          const complete = done.has(milestone.tool);
          return (
            <li key={milestone.tool} className="flex items-center gap-3">
              <span className="grid size-4 place-items-center">
                {complete ? (
                  <CheckIcon className="size-3.5 text-accent" />
                ) : (
                  <motion.span
                    className="block size-[6px] rounded-full bg-rule-strong"
                    animate={
                      failed ? {} : { opacity: [1, 0.3, 1], scale: [1, 0.8, 1] }
                    }
                    transition={{
                      duration: 1.6,
                      repeat: Infinity,
                      ease: "easeInOut",
                    }}
                  />
                )}
              </span>
              <span
                className={`text-[0.9rem] ${complete ? "text-ink" : "text-ink-faint"}`}
              >
                {milestone.label}
              </span>
            </li>
          );
        })}
      </ol>

      <div className="mt-7 border-t border-rule pt-5">
        <ol className="no-scrollbar max-h-64 space-y-1.5 overflow-y-auto pr-1" aria-live="polite">
          <AnimatePresence initial={false}>
            {lines.slice(-40).map((line) => (
              <motion.li
                key={line.id}
                layout="position"
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.25, ease: [0.16, 1, 0.3, 1] }}
                className="flex items-start gap-2.5 text-[0.78rem] leading-snug"
              >
                <TraceMarker kind={line.kind} status={line.status} />
                <span
                  className={
                    line.status === "error"
                      ? "text-danger"
                      : line.kind === "thinking"
                        ? "text-ink-faint italic"
                        : "text-ink-soft"
                  }
                >
                  {line.text}
                </span>
              </motion.li>
            ))}
          </AnimatePresence>
        </ol>
      </div>
    </div>
  );
}

function TraceMarker({
  kind,
  status,
}: {
  kind: TraceLine["kind"];
  status: TraceLine["status"];
}) {
  const base = "mt-[3px] size-3 shrink-0";
  if (status === "error") return <AlertIcon className={`${base} text-danger`} />;
  if (kind === "thinking") return <SparkIcon className={`${base} text-ink-faint`} />;
  if (status === "pending") {
    return (
      <span className={`${base} grid place-items-center`}>
        <motion.span
          className="block size-[6px] rounded-full bg-accent"
          animate={{ opacity: [1, 0.25, 1], scale: [1, 0.82, 1] }}
          transition={{ duration: 1.3, repeat: Infinity, ease: "easeInOut" }}
        />
      </span>
    );
  }
  return <CheckIcon className={`${base} text-ink-faint`} />;
}

export function EmptyState({
  title,
  body,
  onRetry,
}: {
  title: string;
  body: string;
  onRetry: () => void;
}) {
  return (
    <div className="py-6 text-center">
      <p className="font-serif text-lg text-ink">{title}</p>
      <p className="mx-auto mt-2 max-w-sm text-[0.9rem] leading-relaxed text-ink-soft">
        {body}
      </p>
      <button
        type="button"
        onClick={onRetry}
        className="label mt-6 inline-flex items-center gap-2 rounded-full border border-rule-strong px-4 py-2 text-ink-soft transition hover:border-ink hover:text-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
      >
        <RetryIcon className="size-3.5" />
        Research this book
      </button>
    </div>
  );
}

function BodySkeleton() {
  return (
    <div className="animate-pulse space-y-3">
      <div className="h-5 w-1/3 rounded-full bg-shell-raised" />
      {Array.from({ length: 5 }).map((_, index) => (
        <div
          key={index}
          className="h-2.5 rounded-full bg-shell-raised"
          style={{ width: `${70 + ((index * 11) % 28)}%` }}
        />
      ))}
    </div>
  );
}
