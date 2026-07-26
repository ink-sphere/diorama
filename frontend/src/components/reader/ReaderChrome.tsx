"use client";

import { motion } from "framer-motion";
import Link from "next/link";

import {
  ChevronLeftIcon,
  ChevronRightIcon,
  ContentsIcon,
} from "@/components/Icons";
import { ThemeToggle } from "@/components/ThemeToggle";
import type { ReaderPrefs } from "@/lib/useReaderPrefs";

import { TypeMenu } from "./TypeMenu";

export function ReaderHeader({
  bookTitle,
  sectionHeading,
  contentsOpen,
  onToggleContents,
  typeMenuOpen,
  onTypeMenuOpenChange,
  prefs,
  onPrefsChange,
  onPrefsReset,
}: {
  bookTitle: string;
  sectionHeading: string | null;
  contentsOpen: boolean;
  onToggleContents: () => void;
  typeMenuOpen: boolean;
  onTypeMenuOpenChange: (open: boolean) => void;
  prefs: ReaderPrefs;
  onPrefsChange: (patch: Partial<ReaderPrefs>) => void;
  onPrefsReset: () => void;
}) {
  return (
    <header className="relative z-30 flex h-14 shrink-0 items-center justify-between gap-4 border-b border-rule px-3 sm:px-4">
      <Link
        href="/"
        className="label inline-flex items-center gap-1 rounded-full py-2 pr-3 pl-2 text-ink-soft transition-colors hover:text-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
      >
        <ChevronLeftIcon className="size-3.5" />
        Library
      </Link>

      {/* Centred over the whole bar rather than inside the flex row, so the title
          stays optically centred no matter how wide the side groups get. */}
      <div className="pointer-events-none absolute inset-x-0 flex flex-col items-center">
        <p className="max-w-[45vw] truncate font-serif text-[0.95rem] text-ink">
          {bookTitle}
        </p>
        {sectionHeading ? (
          <p className="label max-w-[45vw] truncate text-ink-faint">
            {sectionHeading}
          </p>
        ) : null}
      </div>

      <div className="flex items-center gap-1">
        <button
          type="button"
          onClick={onToggleContents}
          aria-label="Table of contents"
          aria-pressed={contentsOpen}
          className={`grid size-9 place-items-center rounded-full transition-colors hover:bg-shell-raised hover:text-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent ${
            contentsOpen ? "bg-shell-raised text-ink" : "text-ink-soft"
          }`}
        >
          <ContentsIcon className="size-[18px]" />
        </button>
        <TypeMenu
          open={typeMenuOpen}
          onOpenChange={onTypeMenuOpenChange}
          prefs={prefs}
          onChange={onPrefsChange}
          onReset={onPrefsReset}
        />
        <ThemeToggle />
      </div>
    </header>
  );
}

export function PageNav({
  page,
  pageCount,
  canGoBack,
  canGoForward,
  onPrevious,
  onNext,
  onSelectPage,
}: {
  page: number;
  pageCount: number;
  canGoBack: boolean;
  canGoForward: boolean;
  onPrevious: () => void;
  onNext: () => void;
  onSelectPage: (page: number) => void;
}) {
  return (
    <nav className="flex h-16 shrink-0 items-center justify-between gap-4 px-4 sm:px-8">
      <NavButton onClick={onPrevious} disabled={!canGoBack} side="left">
        Previous
      </NavButton>

      {/* Dots read as a page count at a glance, but stop being legible past a
          dozen or so — long sections get a proportional rule instead. */}
      {pageCount <= 14 ? (
        <ol className="flex items-center gap-1.5">
          {Array.from({ length: pageCount }).map((_, index) => (
            <li key={index}>
              <button
                type="button"
                onClick={() => onSelectPage(index)}
                aria-label={`Page ${index + 1}`}
                aria-current={index === page ? "true" : undefined}
                className="group grid size-4 place-items-center"
              >
                <span
                  className={`block size-1.5 rounded-full transition-colors ${
                    index === page
                      ? "bg-ink"
                      : "bg-rule-strong group-hover:bg-ink-faint"
                  }`}
                />
              </button>
            </li>
          ))}
        </ol>
      ) : (
        <div className="flex items-center gap-3">
          <div className="h-px w-32 bg-rule-strong sm:w-48">
            <motion.div
              className="h-px bg-ink"
              animate={{
                width: `${((page + 1) / Math.max(1, pageCount)) * 100}%`,
              }}
              transition={{ duration: 0.35, ease: [0.16, 1, 0.3, 1] }}
            />
          </div>
          <span className="label tabular-nums text-ink-faint">
            {page + 1}/{pageCount}
          </span>
        </div>
      )}

      <NavButton onClick={onNext} disabled={!canGoForward} side="right">
        Next page
      </NavButton>
    </nav>
  );
}

function NavButton({
  children,
  onClick,
  disabled,
  side,
}: {
  children: React.ReactNode;
  onClick: () => void;
  disabled: boolean;
  side: "left" | "right";
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="label group inline-flex items-center gap-2 rounded-full px-3 py-2 text-ink-soft transition-colors hover:text-ink disabled:opacity-30 disabled:hover:text-ink-soft focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
    >
      {side === "left" ? (
        <ChevronLeftIcon className="size-3.5 transition-transform group-enabled:group-hover:-translate-x-0.5" />
      ) : null}
      {children}
      {side === "right" ? (
        <ChevronRightIcon className="size-3.5 transition-transform group-enabled:group-hover:translate-x-0.5" />
      ) : null}
    </button>
  );
}
