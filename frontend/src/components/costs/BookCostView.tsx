"use client";

/**
 * One book's cost trace: its runs, its breakdowns, and every LLM call it made.
 *
 * This is the bottom of the drill-down, so it shows the ledger rows more or less
 * verbatim — turn number, model, upstream provider, token split, latency, price.
 * Anything summarised above is recoverable here, which is the point: a total you
 * cannot decompose is a number you have to take on faith.
 */

import { AnimatePresence, motion } from "framer-motion";
import Link from "next/link";
import { useEffect, useState } from "react";

import { AlertIcon, ChevronLeftIcon, ChevronDownIcon } from "@/components/Icons";
import { ThemeToggle } from "@/components/ThemeToggle";
import { ApiError, getBookUsage } from "@/lib/api";
import type { BookUsage, LLMCallRecord } from "@/lib/types";

import { BreakdownChart } from "./Charts";
import { CostByTypeBar, PageHeading, Panel, StatTile, offline } from "./Primitives";
import {
  formatDuration,
  formatStamp,
  formatTokens,
  formatUsd,
  shortModelId,
  tokenTypeLabel,
} from "./format";

const NO_LEDGER =
  "This book has no call-level detail. It was processed before cost tracking existed — its shelf card still shows what the run cost in total. Reprocessing it will record the full trace.";

export function BookCostView({ bookId }: { bookId: string }) {
  const [usage, setUsage] = useState<BookUsage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const next = await getBookUsage(bookId);
        if (!cancelled) {
          setUsage(next);
          setError(null);
        }
      } catch (fetchError) {
        if (cancelled) return;
        // A 404 here is the expected answer for a pre-tracking book, not a fault.
        setError(
          fetchError instanceof ApiError && fetchError.status === 404
            ? NO_LEDGER
            : offline,
        );
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [bookId]);

  const totals = usage?.totals;

  return (
    <div className="min-h-full">
      <div className="mx-auto w-full max-w-5xl px-6 pb-24 sm:px-10">
        <header className="flex items-center justify-between py-6">
          <Link
            href="/costs"
            className="label inline-flex items-center gap-1 rounded-full py-2 pr-3 text-ink-soft transition-colors hover:text-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          >
            <ChevronLeftIcon className="size-3.5" />
            Costs
          </Link>
          <ThemeToggle />
        </header>

        <PageHeading
          eyebrow="Cost trace"
          title={usage?.title ?? (loading ? "…" : "Book")}
          blurb={
            usage
              ? `Every model call made while reading ${
                  usage.author ? `${usage.title} by ${usage.author}` : usage.title
                }.`
              : "Every model call made while reading this book."
          }
        />

        {error ? (
          <div className="border-t border-rule pt-8">
            <p className="flex max-w-xl items-start gap-2.5 rounded-[3px] border border-rule bg-shell-raised px-3.5 py-3 text-[0.85rem] leading-relaxed text-ink-soft">
              <AlertIcon className="mt-0.5 size-4 shrink-0 text-accent" />
              {error}
            </p>
          </div>
        ) : null}

        {loading ? <BookCostSkeleton /> : null}

        {usage && totals ? (
          <div className="space-y-12">
            <section className="grid grid-cols-2 gap-px border border-rule bg-rule sm:grid-cols-4">
              <StatTile label="Spend" value={formatUsd(totals.cost_usd)} />
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
                note={
                  totals.cache_read_tokens
                    ? `${formatTokens(totals.cache_read_tokens)} from cache`
                    : `${formatTokens(totals.prompt_tokens)} in · ${formatTokens(
                        totals.completion_tokens,
                      )} out`
                }
              />
              <StatTile
                label="Avg latency"
                value={formatDuration(totals.avg_duration_ms)}
              />
            </section>

            {usage.runs.length > 1 ? (
              <Panel
                title="Runs"
                blurb="This book was processed more than once. Each attempt is kept — a retry adds to the bill rather than replacing what the first one spent."
              >
                <ul className="border-t border-rule">
                  {usage.runs.map((run) => (
                    <li
                      key={run.run_id}
                      className="flex items-baseline justify-between gap-4 border-b border-rule py-3"
                    >
                      <div className="min-w-0">
                        <p className="truncate text-[0.88rem] text-ink">
                          {formatStamp(run.started_at)}
                        </p>
                        <p className="mt-0.5 truncate text-[0.75rem] text-ink-faint">
                          {run.model_ids.map(shortModelId).join(", ")} ·{" "}
                          {run.totals.billed_calls} calls
                        </p>
                      </div>
                      <span className="shrink-0 text-[0.88rem] text-ink tabular-nums">
                        {formatUsd(run.totals.cost_usd)}
                      </span>
                    </li>
                  ))}
                </ul>
              </Panel>
            ) : null}

            <div className="grid gap-12 lg:grid-cols-2">
              <Panel title="By provider">
                <BreakdownChart data={usage.by_provider} unit="provider" />
              </Panel>
              <Panel title="By model">
                <BreakdownChart data={usage.by_model} unit="model" />
              </Panel>
            </div>

            <Panel title="Where the money goes">
              <CostByTypeBar costByType={totals.cost_by_type} />
            </Panel>

            <Panel
              title="Every call"
              blurb={`${usage.calls.length} recorded ${
                usage.calls.length === 1 ? "request" : "requests"
              }, oldest first. Select one to see its full token and price split.`}
            >
              <CallTable calls={usage.calls} />
            </Panel>
          </div>
        ) : null}
      </div>
    </div>
  );
}

function CallTable({ calls }: { calls: LLMCallRecord[] }) {
  const [openId, setOpenId] = useState<string | null>(null);

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[42rem] border-collapse text-left">
        <thead>
          <tr className="border-y border-rule">
            <th className="label py-2.5 pr-3 font-medium text-ink-faint">Turn</th>
            <th className="label py-2.5 pr-3 font-medium text-ink-faint">Model</th>
            <th className="label py-2.5 pr-3 font-medium text-ink-faint">Provider</th>
            <th className="label py-2.5 pr-3 text-right font-medium text-ink-faint">
              In
            </th>
            <th className="label py-2.5 pr-3 text-right font-medium text-ink-faint">
              Out
            </th>
            <th className="label py-2.5 pr-3 text-right font-medium text-ink-faint">
              Time
            </th>
            <th className="label py-2.5 pr-3 text-right font-medium text-ink-faint">
              Cost
            </th>
            <th className="w-6" />
          </tr>
        </thead>
        <tbody>
          {calls.map((call) => {
            const open = openId === call.id;
            return (
              <CallRow
                key={call.id}
                call={call}
                open={open}
                onToggle={() => setOpenId(open ? null : call.id)}
              />
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function CallRow({
  call,
  open,
  onToggle,
}: {
  call: LLMCallRecord;
  open: boolean;
  onToggle: () => void;
}) {
  const failed = call.status !== "ok";
  return (
    <>
      <tr
        onClick={onToggle}
        className="cursor-pointer border-b border-rule transition-colors hover:bg-shell-raised"
      >
        <td className="py-2.5 pr-3 text-[0.82rem] text-ink tabular-nums">
          {call.kind === "compaction" ? (
            <span className="label text-ink-faint">compact</span>
          ) : (
            (call.turn ?? "—")
          )}
          {call.attempt > 1 ? (
            <span className="ml-1 text-[0.7rem] text-ink-faint">
              ·{call.attempt}
            </span>
          ) : null}
        </td>
        <td className="max-w-[12rem] truncate py-2.5 pr-3 text-[0.82rem] text-ink">
          {call.model || shortModelId(call.model_id)}
        </td>
        <td className="py-2.5 pr-3 text-[0.82rem] text-ink-soft">
          {call.provider ?? "—"}
        </td>
        <td className="py-2.5 pr-3 text-right text-[0.82rem] text-ink-soft tabular-nums">
          {formatTokens(call.prompt_tokens)}
          {call.cache_read_tokens ? (
            <span className="ml-1 text-[0.7rem] text-accent">
              ({formatTokens(call.cache_read_tokens)}c)
            </span>
          ) : null}
        </td>
        <td className="py-2.5 pr-3 text-right text-[0.82rem] text-ink-soft tabular-nums">
          {formatTokens(call.completion_tokens)}
        </td>
        <td className="py-2.5 pr-3 text-right text-[0.82rem] text-ink-faint tabular-nums">
          {formatDuration(call.duration_ms)}
        </td>
        <td
          className={`py-2.5 pr-3 text-right text-[0.82rem] tabular-nums ${
            failed ? "text-danger" : "text-ink"
          }`}
        >
          {failed ? call.status : formatUsd(call.cost_usd)}
        </td>
        <td className="py-2.5 text-right">
          <ChevronDownIcon
            className={`inline size-3.5 text-ink-faint transition-transform ${
              open ? "rotate-180" : ""
            }`}
          />
        </td>
      </tr>
      <AnimatePresence initial={false}>
        {open ? (
          <tr>
            <td colSpan={8} className="p-0">
              <motion.div
                initial={{ opacity: 0, height: 0 }}
                animate={{ opacity: 1, height: "auto" }}
                exit={{ opacity: 0, height: 0 }}
                transition={{ duration: 0.22, ease: [0.16, 1, 0.3, 1] }}
                className="overflow-hidden"
              >
                <CallDetail call={call} />
              </motion.div>
            </td>
          </tr>
        ) : null}
      </AnimatePresence>
    </>
  );
}

function CallDetail({ call }: { call: LLMCallRecord }) {
  const costEntries = Object.entries(call.cost_by_type).filter(([, v]) => v > 0);
  return (
    <div className="border-b border-rule bg-shell-raised px-4 py-4">
      {call.error ? (
        <p className="mb-4 flex items-start gap-2 text-[0.82rem] leading-relaxed text-danger">
          <AlertIcon className="mt-0.5 size-3.5 shrink-0" />
          {call.error}
        </p>
      ) : null}

      <dl className="grid grid-cols-2 gap-x-8 gap-y-2 sm:grid-cols-4">
        <Field label="Started" value={formatStamp(call.started_at)} />
        <Field label="Route" value={call.route} />
        <Field label="Model id" value={shortModelId(call.model_id)} />
        <Field label="Finish reason" value={call.finish_reason ?? "—"} />
        <Field
          label="Cache write"
          value={formatTokens(call.cache_write_tokens)}
        />
        <Field label="Reasoning" value={formatTokens(call.reasoning_tokens)} />
        <Field label="Streamed" value={call.streamed ? "yes" : "no"} />
        <Field label="Priced by" value={pricingLabel(call.pricing_source)} />
      </dl>

      {costEntries.length ? (
        <div className="mt-4 border-t border-rule pt-3">
          <p className="label mb-2 text-ink-faint">Cost split</p>
          <ul className="flex flex-wrap gap-x-6 gap-y-1">
            {costEntries.map(([type, value]) => (
              <li key={type} className="text-[0.8rem] text-ink-soft tabular-nums">
                {tokenTypeLabel(type)}{" "}
                <span className="text-ink">{formatUsd(value)}</span>
              </li>
            ))}
          </ul>
          {call.actual_cost_usd != null &&
          Math.abs(call.actual_cost_usd - call.estimated_cost_usd) > 1e-9 ? (
            <p className="mt-2.5 text-[0.75rem] text-ink-faint tabular-nums">
              Rate table estimated {formatUsd(call.estimated_cost_usd)}; the
              provider billed {formatUsd(call.actual_cost_usd)}. The split above is
              scaled to the real charge.
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="label text-ink-faint">{label}</dt>
      <dd className="mt-0.5 truncate text-[0.82rem] text-ink">{value}</dd>
    </div>
  );
}

/** Never let an estimate pass itself off as a real charge. */
function pricingLabel(source: LLMCallRecord["pricing_source"]): string {
  switch (source) {
    case "actual":
      return "Provider charge";
    case "openrouter_live":
      return "OpenRouter live rates";
    case "google_static":
      return "Google published rates";
    case "litellm_static":
      return "litellm static rates";
    default:
      return "Not priced";
  }
}

function BookCostSkeleton() {
  return (
    <div className="grid animate-pulse grid-cols-2 gap-px border border-rule bg-rule sm:grid-cols-4">
      {Array.from({ length: 4 }).map((_, index) => (
        <div key={index} className="bg-shell px-4 py-5">
          <div className="h-2.5 w-16 rounded-full bg-shell-raised" />
          <div className="mt-4 h-5 w-20 rounded-full bg-shell-raised" />
        </div>
      ))}
    </div>
  );
}
