"use client";

import { motion } from "framer-motion";

import { SearchIcon } from "@/components/Icons";
import type { BookStatus } from "@/lib/types";

export type ShelfFilter = "all" | "ready" | "working" | "failed";

const FILTERS: { value: ShelfFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "ready", label: "Ready" },
  { value: "working", label: "Processing" },
  { value: "failed", label: "Failed" },
];

/** "working" covers both queued and processing — one waiting state, to a reader. */
export function matchesFilter(status: BookStatus, filter: ShelfFilter): boolean {
  if (filter === "all") return true;
  if (filter === "working") return status === "queued" || status === "processing";
  return status === filter;
}

export function ShelfFilters({
  query,
  onQuery,
  filter,
  onFilter,
  counts,
}: {
  query: string;
  onQuery: (value: string) => void;
  filter: ShelfFilter;
  onFilter: (value: ShelfFilter) => void;
  counts: Record<ShelfFilter, number>;
}) {
  return (
    <div className="flex flex-col gap-4 border-y border-rule py-3 sm:flex-row sm:items-center sm:justify-between">
      <div className="flex items-center gap-2.5">
        <SearchIcon className="size-4 shrink-0 text-ink-faint" />
        <input
          value={query}
          onChange={(event) => onQuery(event.target.value)}
          placeholder="Search titles and authors"
          aria-label="Search the shelf"
          className="w-full min-w-0 bg-transparent font-serif text-[0.95rem] text-ink placeholder:text-ink-faint focus:outline-none sm:w-64"
        />
      </div>

      <div className="flex items-center gap-1">
        {FILTERS.map(({ value, label }) => {
          const active = filter === value;
          const count = counts[value];
          return (
            <button
              key={value}
              type="button"
              onClick={() => onFilter(value)}
              disabled={count === 0 && value !== "all"}
              className={`label relative rounded-full px-3 py-1.5 transition-colors disabled:cursor-default disabled:opacity-35 ${
                active ? "text-ink" : "text-ink-faint hover:text-ink-soft"
              }`}
            >
              {active ? (
                <motion.span
                  layoutId="shelf-filter-pill"
                  className="absolute inset-0 rounded-full bg-shell-raised ring-1 ring-rule"
                  transition={{ duration: 0.3, ease: [0.16, 1, 0.3, 1] }}
                />
              ) : null}
              <span className="relative">
                {label}
                {count > 0 ? (
                  <span className="ml-1.5 text-ink-faint">{count}</span>
                ) : null}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
