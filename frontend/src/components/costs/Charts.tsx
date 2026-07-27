"use client";

/**
 * Recharts wrappers themed to Diorama's design tokens.
 *
 * Recharts ships an opinionated default look — heavy grids, a white tooltip card,
 * saturated primaries — that sits badly next to hairline rules and small-caps
 * labels. Everything visible is overridden here once, so the pages below only ever
 * pass data. Three rules the defaults get wrong for this UI:
 *
 * - No cartesian grid, only a faint horizontal one. Vertical rules compete with
 *   the page's own hairlines.
 * - The tooltip is our own component, not Recharts' card, so it uses the shell
 *   colours and the `label` type treatment.
 * - Series colours come through as `var(--chart-N)`, so a theme toggle recolours
 *   the SVG with no React work.
 */

import {
  Bar,
  BarChart,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { chartColor, formatUsd, formatUsdAxis, shortModelId } from "./format";
import type { DailyPoint, GroupTotals } from "@/lib/types";
import { formatDay, formatTokens } from "./format";

// Tick labels sit a notch quieter than body text and take their colour from the
// theme token, so a theme flip recolours them without a re-render. Deliberately not
// uppercased like the `.label` chrome: these are data (dates, model ids), and
// small-caps mangles an id like `gpt-4o-mini` into something harder to scan.
const AXIS_STYLE = {
  fontSize: 10,
  letterSpacing: "0.04em",
  fill: "var(--ink-faint)",
};

function TooltipCard({
  title,
  rows,
}: {
  title: string;
  rows: [string, string][];
}) {
  return (
    <div className="rounded-[3px] border border-rule bg-shell-raised px-3 py-2 shadow-page">
      <p className="label mb-1.5 text-ink-faint">{title}</p>
      {rows.map(([key, value]) => (
        <p
          key={key}
          className="flex justify-between gap-6 text-[0.82rem] text-ink tabular-nums"
        >
          <span className="text-ink-soft">{key}</span>
          <span>{value}</span>
        </p>
      ))}
    </div>
  );
}

/**
 * Daily spend. Bars, not a line: spend is a discrete quantity accrued on a day,
 * and a line between two days implies a rate of change that never existed.
 */
export function DailySpendChart({ data }: { data: DailyPoint[] }) {
  return (
    <div className="h-56 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 4, right: 4, bottom: 0, left: -8 }}>
          <XAxis
            dataKey="date"
            tickFormatter={formatDay}
            tick={AXIS_STYLE}
            tickLine={false}
            axisLine={{ stroke: "var(--rule)" }}
            minTickGap={16}
          />
          <YAxis
            tickFormatter={formatUsdAxis}
            tick={AXIS_STYLE}
            tickLine={false}
            axisLine={false}
            width={52}
          />
          <Tooltip
            cursor={{ fill: "var(--rule)", opacity: 0.35 }}
            content={({ active, payload }) => {
              if (!active || !payload?.length) return null;
              const point = payload[0].payload as DailyPoint;
              return (
                <TooltipCard
                  title={formatDay(point.date)}
                  rows={[
                    ["Spend", formatUsd(point.cost_usd)],
                    ["Calls", String(point.calls)],
                    ["Tokens", formatTokens(point.total_tokens)],
                  ]}
                />
              );
            }}
          />
          <Bar
            dataKey="cost_usd"
            fill="var(--chart-1)"
            radius={[2, 2, 0, 0]}
            // A run's worth of days makes for few, very fat bars otherwise.
            maxBarSize={64}
            isAnimationActive={false}
          />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

/**
 * Spend by category — model, provider, agent — as horizontal bars.
 *
 * Horizontal because the labels are model ids: rotated vertical tick labels are
 * the single most common way a chart like this becomes unreadable.
 */
export function BreakdownChart({
  data,
  unit = "model",
}: {
  data: GroupTotals[];
  unit?: string;
}) {
  const height = Math.max(120, data.length * 34 + 24);
  return (
    <div className="w-full" style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart
          data={data}
          layout="vertical"
          margin={{ top: 0, right: 12, bottom: 0, left: 0 }}
        >
          <XAxis
            type="number"
            tickFormatter={formatUsdAxis}
            tick={AXIS_STYLE}
            tickLine={false}
            axisLine={{ stroke: "var(--rule)" }}
          />
          <YAxis
            type="category"
            dataKey="label"
            tickFormatter={shortModelId}
            tick={AXIS_STYLE}
            tickLine={false}
            axisLine={false}
            width={128}
          />
          <Tooltip
            cursor={{ fill: "var(--rule)", opacity: 0.35 }}
            content={({ active, payload }) => {
              if (!active || !payload?.length) return null;
              const group = payload[0].payload as GroupTotals;
              return (
                <TooltipCard
                  title={shortModelId(group.label)}
                  rows={[
                    ["Spend", formatUsd(group.cost_usd)],
                    ["Calls", String(group.calls)],
                    ["Tokens", formatTokens(group.total_tokens)],
                    ...(group.detail.length
                      ? ([["Models", group.detail.join(", ")]] as [string, string][])
                      : []),
                  ]}
                />
              );
            }}
          />
          {/* Animation off: Recharts replays the grow-in on every resize, so
              expanding a call row further down the page would make these charts
              flicker for something that has nothing to do with them. */}
          <Bar
            dataKey="cost_usd"
            radius={[0, 2, 2, 0]}
            barSize={16}
            isAnimationActive={false}
          >
            {data.map((group, index) => (
              <Cell key={group.key} fill={chartColor(index)} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
      <span className="sr-only">Spend by {unit}</span>
    </div>
  );
}
