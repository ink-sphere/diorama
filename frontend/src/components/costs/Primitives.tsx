"use client";

/**
 * Shared chrome for the two cost pages: stat tiles, section panels, the
 * cost-by-type bar. Kept apart from the views so the overview and the per-book
 * page can't drift into two slightly different visual languages.
 */

import { motion } from "framer-motion";

import { formatUsd, tokenTypeLabel } from "./format";

export const offline =
  "Can't reach the Diorama backend. Start it with `uv run uvicorn diorama.backend.main:app --reload --port 8000`.";

export function PageHeading({
  title,
  blurb,
  eyebrow,
}: {
  title: string;
  blurb: string;
  eyebrow?: string;
}) {
  return (
    <section className="border-t border-rule pt-12 pb-10">
      {eyebrow ? <p className="label mb-4 text-ink-faint">{eyebrow}</p> : null}
      <motion.h1
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
        className="font-serif text-[2.4rem] leading-[1.1] font-normal tracking-[-0.015em] text-ink"
      >
        {title}
      </motion.h1>
      <motion.p
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5, delay: 0.06, ease: [0.16, 1, 0.3, 1] }}
        className="mt-4 max-w-xl font-serif text-[1.02rem] leading-relaxed text-ink-soft"
      >
        {blurb}
      </motion.p>
    </section>
  );
}

/**
 * One headline number.
 *
 * Tiles sit in a `gap-px` grid over a `bg-rule` parent, so the hairlines between
 * them are the parent showing through rather than per-tile borders that would
 * double up where two tiles meet.
 */
export function StatTile({
  label,
  value,
  note,
}: {
  label: string;
  value: string;
  note?: string;
}) {
  return (
    <div className="bg-shell px-4 py-5">
      <p className="label text-ink-faint">{label}</p>
      <p className="mt-2.5 font-serif text-[1.5rem] leading-none text-ink tabular-nums">
        {value}
      </p>
      {note ? (
        <p className="mt-2 text-[0.75rem] text-ink-faint tabular-nums">{note}</p>
      ) : null}
    </div>
  );
}

export function Panel({
  title,
  blurb,
  children,
  action,
}: {
  title: string;
  blurb?: string;
  children: React.ReactNode;
  action?: React.ReactNode;
}) {
  return (
    <section className="border-t border-rule pt-8">
      <div className="flex items-baseline justify-between gap-4">
        <h2 className="label text-ink-faint">{title}</h2>
        {action}
      </div>
      {blurb ? (
        <p className="mt-3 max-w-xl text-[0.85rem] leading-relaxed text-ink-soft">
          {blurb}
        </p>
      ) : null}
      <div className="mt-6">{children}</div>
    </section>
  );
}

/**
 * Spend split by token type as a single stacked bar plus a legend.
 *
 * One bar rather than six, because these parts genuinely sum to a whole — the
 * question this answers is "what proportion of the bill was cache reads", and a
 * grouped chart would make that a subtraction the reader has to do themselves.
 */
export function CostByTypeBar({
  costByType,
}: {
  costByType: Record<string, number>;
}) {
  const entries = Object.entries(costByType).filter(([, value]) => value > 0);
  const total = entries.reduce((sum, [, value]) => sum + value, 0);

  if (!total) {
    return (
      <p className="text-[0.85rem] text-ink-faint">
        No per-token-type breakdown recorded — the rate table couldn&apos;t price
        these calls.
      </p>
    );
  }

  return (
    <div>
      <div className="flex h-6 w-full overflow-hidden rounded-[3px]">
        {entries.map(([type, value], index) => (
          <div
            key={type}
            title={`${tokenTypeLabel(type)} — ${formatUsd(value)}`}
            style={{
              width: `${(value / total) * 100}%`,
              backgroundColor: `var(--chart-${(index % 6) + 1})`,
            }}
          />
        ))}
      </div>
      <ul className="mt-4 flex flex-wrap gap-x-6 gap-y-2">
        {entries.map(([type, value], index) => (
          <li key={type} className="flex items-center gap-2">
            <span
              aria-hidden
              className="size-2.5 shrink-0 rounded-[1px]"
              style={{ backgroundColor: `var(--chart-${(index % 6) + 1})` }}
            />
            <span className="text-[0.82rem] text-ink-soft">
              {tokenTypeLabel(type)}
            </span>
            <span className="text-[0.82rem] text-ink tabular-nums">
              {formatUsd(value)}
            </span>
            <span className="text-[0.75rem] text-ink-faint tabular-nums">
              {Math.round((value / total) * 100)}%
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
