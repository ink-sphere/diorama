"use client";

/**
 * The cost dashboard's overview: what everything cost, split by the dimensions that
 * actually move the number, plus a row per book to drill into.
 *
 * Only books with a call ledger appear. A book processed before cost tracking
 * existed carries an aggregate on its shelf record and nothing else, and putting it
 * in these totals would add spend that no chart here could attribute — the page
 * says so explicitly rather than quietly under-reporting.
 */

import { motion } from "framer-motion";
import Link from "next/link";
import { useEffect, useState } from "react";

import { ChevronLeftIcon, ChevronRightIcon } from "@/components/Icons";
import { ThemeToggle } from "@/components/ThemeToggle";
import { getUsageSummary } from "@/lib/api";
import type { GroupTotals, UsageSummary } from "@/lib/types";

import { BreakdownChart, DailySpendChart } from "./Charts";
import {
  CostByTypeBar,
  PageHeading,
  Panel,
  StatTile,
  offline,
} from "./Primitives";
import {
  formatDuration,
  formatStamp,
  formatTokens,
  formatUsd,
  shortModelId,
} from "./format";

export function CostsView() {
  const [summary, setSummary] = useState<UsageSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const next = await getUsageSummary();
        if (!cancelled) {
          setSummary(next);
          setError(null);
        }
      } catch {
        if (!cancelled) setError(offline);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const totals = summary?.totals;
  const empty = !loading && !error && (summary?.books.length ?? 0) === 0;

  return (
    <div className="min-h-full">
      <div className="mx-auto w-full max-w-5xl px-6 pb-24 sm:px-10">
        <header className="flex items-center justify-between py-6">
          <Link
            href="/"
            className="label inline-flex items-center gap-1 rounded-full py-2 pr-3 text-ink-soft transition-colors hover:text-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          >
            <ChevronLeftIcon className="size-3.5" />
            Library
          </Link>
          <ThemeToggle />
        </header>

        <PageHeading
          title="Costs"
          blurb="Every model call Diorama has made on your books' behalf — what it cost, which
          provider served it, and where the tokens went."
        />

        {error ? (
          <p className="mb-8 rounded-[3px] border border-danger/40 px-4 py-3 text-[0.85rem] text-danger">
            {error}
          </p>
        ) : null}

        {loading ? <CostsSkeleton /> : null}

        {empty ? (
          <div className="border-t border-rule py-16 text-center">
            <p className="font-serif text-[1.15rem] text-ink-soft">
              No model calls recorded yet.
            </p>
            <p className="mx-auto mt-3 max-w-md text-[0.85rem] leading-relaxed text-ink-faint">
              Upload a book and its every LLM call — each agent turn and each context
              compaction — will be priced and logged here as it happens. Books
              processed before cost tracking existed keep the single total on their
              shelf card, but have no call-level detail to show.
            </p>
            <Link
              href="/"
              className="label mt-8 inline-block rounded-[3px] bg-ink px-4 py-2 text-shell transition hover:opacity-90"
            >
              Go to the library
            </Link>
          </div>
        ) : null}

        {summary && totals && !empty ? (
          <div className="space-y-12">
            <section className="grid grid-cols-2 gap-px border border-rule bg-rule sm:grid-cols-4">
              <StatTile label="Total spend" value={formatUsd(totals.cost_usd)} />
              <StatTile
                label="Model calls"
                value={String(totals.billed_calls)}
                note={
                  totals.failed_calls
                    ? `${totals.failed_calls} failed or retried`
                    : undefined
                }
              />
              <StatTile
                label="Tokens"
                value={formatTokens(totals.total_tokens)}
                note={`${formatTokens(totals.prompt_tokens)} in · ${formatTokens(
                  totals.completion_tokens,
                )} out`}
              />
              <StatTile
                label="Avg latency"
                value={formatDuration(totals.avg_duration_ms)}
                note={`across ${summary.books.length} book${
                  summary.books.length === 1 ? "" : "s"
                }`}
              />
            </section>

            {summary.daily.length > 1 ? (
              <Panel title="Spend over time">
                <DailySpendChart data={summary.daily} />
              </Panel>
            ) : null}

            <div className="grid gap-12 lg:grid-cols-2">
              <Panel
                title="By model"
                blurb="Which models you are actually paying for."
              >
                <BreakdownChart data={summary.by_model} unit="model" />
              </Panel>
              <Panel
                title="By provider"
                blurb="The upstream that served each request — OpenRouter picks this per call, so it shifts under you."
              >
                <BreakdownChart data={summary.by_provider} unit="provider" />
              </Panel>
            </div>

            <Panel
              title="Where the money goes"
              blurb="Cache reads bill far below fresh prompt tokens, so this split is the difference between a cheap run and an expensive one."
            >
              <CostByTypeBar costByType={totals.cost_by_type} />
            </Panel>

            {/* Both panels only exist once there is more than one agent, and today
                there is exactly one — so the grid is sized to what's actually
                present rather than leaving a dead half-width column. */}
            {(() => {
              const panels = [
                summary.by_agent.length > 1 ? (
                  <Panel key="agent" title="By agent">
                    <GroupList groups={summary.by_agent} />
                  </Panel>
                ) : null,
                summary.by_kind.length > 1 ? (
                  <Panel
                    key="kind"
                    title="By call kind"
                    blurb="Compaction is the summarisation the agent runs to stay inside the context window — real spend that hides inside a single total."
                  >
                    <GroupList groups={summary.by_kind} />
                  </Panel>
                ) : null,
              ].filter(Boolean);
              if (!panels.length) return null;
              return (
                <div
                  className={`grid gap-12 ${panels.length > 1 ? "lg:grid-cols-2" : ""}`}
                >
                  {panels}
                </div>
              );
            })()}

            <Panel title="Per book">
              <ul className="border-t border-rule">
                {summary.books.map((book, index) => (
                  <motion.li
                    key={book.book_id}
                    initial={{ opacity: 0, y: 6 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{
                      duration: 0.35,
                      delay: Math.min(index * 0.03, 0.3),
                      ease: [0.16, 1, 0.3, 1],
                    }}
                    className="border-b border-rule"
                  >
                    <Link
                      href={`/costs/${book.book_id}`}
                      className="group flex items-center gap-4 py-4 transition-colors hover:bg-shell-raised focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                    >
                      <div className="min-w-0 flex-1">
                        <p className="truncate font-serif text-[1.05rem] text-ink">
                          {book.title}
                          {!book.known_book ? (
                            <span className="label ml-2 text-ink-faint">
                              no longer on the shelf
                            </span>
                          ) : null}
                        </p>
                        <p className="mt-1 truncate text-[0.78rem] text-ink-faint">
                          {[
                            `${book.totals.billed_calls} calls`,
                            book.runs > 1 ? `${book.runs} runs` : null,
                            book.models.map(shortModelId).join(", "),
                            book.providers.join(", "),
                            formatStamp(book.last_call_at),
                          ]
                            .filter(Boolean)
                            .join(" · ")}
                        </p>
                      </div>
                      <div className="shrink-0 text-right">
                        <p className="font-serif text-[1.05rem] text-ink tabular-nums">
                          {formatUsd(book.totals.cost_usd)}
                        </p>
                        <p className="text-[0.78rem] text-ink-faint tabular-nums">
                          {formatTokens(book.totals.total_tokens)} tokens
                        </p>
                      </div>
                      <ChevronRightIcon className="size-4 shrink-0 text-ink-faint transition-transform group-hover:translate-x-0.5" />
                    </Link>
                  </motion.li>
                ))}
              </ul>
            </Panel>
          </div>
        ) : null}
      </div>
    </div>
  );
}

/** A compact ranked list, for dimensions that don't earn a chart of their own. */
function GroupList({ groups }: { groups: GroupTotals[] }) {
  const max = Math.max(...groups.map((g) => g.cost_usd), 0.0000001);
  return (
    <ul className="space-y-3">
      {groups.map((group, index) => (
        <li key={group.key}>
          <div className="flex items-baseline justify-between gap-4">
            <span className="truncate text-[0.88rem] text-ink">{group.label}</span>
            <span className="shrink-0 text-[0.88rem] text-ink tabular-nums">
              {formatUsd(group.cost_usd)}
            </span>
          </div>
          <div className="mt-1.5 h-1 w-full overflow-hidden rounded-full bg-rule">
            <div
              className="h-full rounded-full"
              style={{
                width: `${Math.max((group.cost_usd / max) * 100, 1.5)}%`,
                backgroundColor: `var(--chart-${(index % 6) + 1})`,
              }}
            />
          </div>
          <p className="mt-1 text-[0.75rem] text-ink-faint tabular-nums">
            {group.calls} calls · {formatTokens(group.total_tokens)} tokens
          </p>
        </li>
      ))}
    </ul>
  );
}

function CostsSkeleton() {
  return (
    <div className="space-y-12">
      <div className="grid animate-pulse grid-cols-2 gap-px border border-rule bg-rule sm:grid-cols-4">
        {Array.from({ length: 4 }).map((_, index) => (
          <div key={index} className="bg-shell px-4 py-5">
            <div className="h-2.5 w-16 rounded-full bg-shell-raised" />
            <div className="mt-4 h-5 w-20 rounded-full bg-shell-raised" />
          </div>
        ))}
      </div>
      <div className="animate-pulse border-t border-rule pt-8">
        <div className="h-2.5 w-28 rounded-full bg-shell-raised" />
        <div className="mt-6 h-48 w-full rounded-[3px] bg-shell-raised" />
      </div>
    </div>
  );
}
