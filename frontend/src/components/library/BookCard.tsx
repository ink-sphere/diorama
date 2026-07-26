"use client";

import { AnimatePresence, motion } from "framer-motion";
import Link from "next/link";
import { useState } from "react";

import { AlertIcon, RetryIcon, TrashIcon } from "@/components/Icons";
import type { BookRecord, TraceLine } from "@/lib/types";

import { BookCover } from "./BookCover";
import { TraceLog } from "./TraceLog";

/**
 * One volume on the shelf.
 *
 * The card is the same object in all four states — it never swaps layout wholesale
 * — so a book that finishes processing settles into its ready form instead of
 * being replaced by a different-looking card.
 */
export function BookCard({
  book,
  trace,
  onRetry,
  onDelete,
}: {
  book: BookRecord;
  trace: TraceLine[];
  onRetry: (bookId: string) => void;
  onDelete: (bookId: string) => void;
}) {
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const isReady = book.status === "ready";
  const percent = Math.round((book.progress?.percent ?? 0) * 100);

  const cover = (
    <div className="relative aspect-[2/3] w-full overflow-hidden rounded-[3px] bg-paper shadow-page ring-1 ring-rule">
      <BookCover bookId={book.id} title={book.title} author={book.author} />
      {/* The spine: a hairline gradient down the binding edge, so a cover reads as
          an object with thickness rather than a flat crop. */}
      <div
        className="pointer-events-none absolute inset-y-0 left-0 w-3 bg-gradient-to-r from-black/20 via-black/5 to-transparent"
        aria-hidden
      />
      {!isReady ? (
        <div className="absolute inset-0 bg-shell/70 backdrop-blur-[1px]" aria-hidden />
      ) : null}
      <AnimatePresence>
        {isReady && percent > 0 ? (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            className="absolute inset-x-0 bottom-0 h-[3px] bg-ink/10"
          >
            <motion.div
              className="h-full bg-accent"
              initial={{ width: 0 }}
              animate={{ width: `${percent}%` }}
              transition={{ duration: 0.6, ease: [0.16, 1, 0.3, 1] }}
            />
          </motion.div>
        ) : null}
      </AnimatePresence>
    </div>
  );

  return (
    <motion.article
      layout
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, scale: 0.97 }}
      transition={{ duration: 0.35, ease: [0.16, 1, 0.3, 1] }}
      className="group relative flex flex-col"
    >
      {isReady ? (
        <Link
          href={`/read/${book.id}`}
          className="block rounded-[3px] transition-transform duration-300 ease-out will-change-transform hover:-translate-y-1 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-accent"
        >
          {cover}
        </Link>
      ) : (
        cover
      )}

      <div className="mt-4 flex items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 className="font-serif text-[1.02rem] leading-snug font-medium text-ink [text-wrap:balance]">
            {isReady ? (
              <Link
                href={`/read/${book.id}`}
                className="decoration-rule-strong underline-offset-4 hover:underline"
              >
                {book.title}
              </Link>
            ) : (
              book.title
            )}
          </h3>
          {book.author ? (
            <p className="mt-1 font-serif text-[0.86rem] text-ink-soft italic">
              {book.author}
            </p>
          ) : null}
        </div>

        <button
          type="button"
          onClick={() => setConfirmingDelete((value) => !value)}
          aria-label={`Remove ${book.title} from the shelf`}
          className="mt-0.5 grid size-7 shrink-0 place-items-center rounded-full text-ink-faint opacity-0 transition group-hover:opacity-100 hover:bg-shell-raised hover:text-danger focus-visible:opacity-100 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          <TrashIcon className="size-[15px]" />
        </button>
      </div>

      <div className="mt-2">
        <StatusLine book={book} percent={percent} />
      </div>

      <AnimatePresence initial={false}>
        {confirmingDelete ? (
          <motion.div
            key="confirm"
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.22, ease: [0.16, 1, 0.3, 1] }}
            className="overflow-hidden"
          >
            <div className="mt-3 rounded-[3px] border border-rule bg-shell-raised p-3">
              <p className="text-[0.8rem] text-ink-soft">
                Remove this book and everything Diorama extracted from it?
              </p>
              <div className="mt-2.5 flex gap-2">
                <button
                  type="button"
                  onClick={() => onDelete(book.id)}
                  className="label rounded-full bg-danger px-3 py-1.5 text-paper transition hover:opacity-90"
                >
                  Remove
                </button>
                <button
                  type="button"
                  onClick={() => setConfirmingDelete(false)}
                  className="label rounded-full px-3 py-1.5 text-ink-soft transition hover:bg-shell hover:text-ink"
                >
                  Keep
                </button>
              </div>
            </div>
          </motion.div>
        ) : null}
      </AnimatePresence>

      <AnimatePresence initial={false}>
        {book.status === "processing" || book.status === "queued" ? (
          <motion.div
            key="trace"
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.3, ease: [0.16, 1, 0.3, 1] }}
            className="overflow-hidden"
          >
            <div className="mt-3 border-t border-rule pt-3">
              {trace.length > 0 ? (
                <TraceLog lines={trace} />
              ) : (
                <p className="text-[0.78rem] text-ink-faint">
                  Waiting for the loader to open the book…
                </p>
              )}
            </div>
          </motion.div>
        ) : null}

        {book.status === "failed" ? (
          <motion.div
            key="failed"
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.3, ease: [0.16, 1, 0.3, 1] }}
            className="overflow-hidden"
          >
            <div className="mt-3 border-t border-rule pt-3">
              <p className="flex gap-2 text-[0.8rem] leading-snug text-ink-soft">
                <AlertIcon className="mt-px size-3.5 shrink-0 text-danger" />
                <span>{book.error ?? "Something went wrong while reading this book."}</span>
              </p>
              <button
                type="button"
                onClick={() => onRetry(book.id)}
                className="label mt-3 inline-flex items-center gap-1.5 rounded-full border border-rule-strong px-3 py-1.5 text-ink-soft transition hover:border-ink hover:text-ink"
              >
                <RetryIcon className="size-3.5" />
                Try again
              </button>
            </div>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </motion.article>
  );
}

function StatusLine({ book, percent }: { book: BookRecord; percent: number }) {
  if (book.status === "ready") {
    return (
      <p className="label text-ink-faint">
        {percent > 0 ? `${percent}% read` : "Unread"}
        {book.structure_line ? (
          <>
            <span className="mx-1.5 text-rule-strong">/</span>
            {book.structure_line}
          </>
        ) : null}
      </p>
    );
  }

  if (book.status === "failed") {
    return <p className="label text-danger">Couldn&apos;t be mapped</p>;
  }

  return (
    <p className="label flex items-center gap-2 text-ink-soft">
      <motion.span
        className="block size-1.5 rounded-full bg-accent"
        animate={{ opacity: [1, 0.3, 1] }}
        transition={{ duration: 1.4, repeat: Infinity, ease: "easeInOut" }}
      />
      {book.status === "queued" ? "Queued" : "Reading the book"}
    </p>
  );
}
