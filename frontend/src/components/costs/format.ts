/**
 * Formatters for the cost dashboard.
 *
 * LLM spend spans an awkward range: a single cheap call is $0.0004 and a month of
 * reading is $12.40. A fixed two-decimal format renders most of this app's real
 * numbers as "$0.00", which reads as free rather than small — so precision here is
 * chosen per magnitude instead of fixed.
 */

/**
 * A USD amount at a precision that keeps it legible at its own scale.
 *
 * Sub-cent values get four significant decimals (`$0.0042`), everything else the
 * conventional two (`$12.40`). Exactly zero is `$0` rather than `$0.00`, so a truly
 * free row is visibly different from a rounded-down one.
 */
export function formatUsd(value: number): string {
  if (!value) return "$0";
  if (value < 0.01) return `$${value.toFixed(4)}`;
  if (value < 1) return `$${value.toFixed(3)}`;
  return `$${value.toFixed(2)}`;
}

/** A compact USD amount for chart axes, where space is tight. */
export function formatUsdAxis(value: number): string {
  if (!value) return "$0";
  if (value < 0.01) return `$${value.toFixed(3)}`;
  if (value < 10) return `$${value.toFixed(2)}`;
  return `$${Math.round(value)}`;
}

/** `1234` → `1.2K`, `8_200_000` → `8.2M`. */
export function formatTokens(value: number): string {
  if (value < 1_000) return String(value);
  if (value < 1_000_000) return `${(value / 1_000).toFixed(1)}K`;
  return `${(value / 1_000_000).toFixed(2)}M`;
}

/** `850` → `0.85s`, `12_400` → `12.4s`, `95_000` → `1m 35s`. */
export function formatDuration(ms: number | null | undefined): string {
  if (ms == null) return "—";
  if (ms < 1_000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1_000).toFixed(1)}s`;
  const minutes = Math.floor(ms / 60_000);
  return `${minutes}m ${Math.round((ms % 60_000) / 1_000)}s`;
}

/** `2026-07-28T10:04:11+00:00` → `28 Jul, 10:04`. */
export function formatStamp(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString(undefined, {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** `2026-07-28` → `28 Jul`, for the daily axis. */
export function formatDay(date: string): string {
  const parsed = new Date(`${date}T00:00:00Z`);
  if (Number.isNaN(parsed.getTime())) return date;
  return parsed.toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    timeZone: "UTC",
  });
}

/** `openrouter/openai/gpt-4o-mini` reads better as `openai/gpt-4o-mini`. */
export function shortModelId(modelId: string): string {
  return modelId.replace(/^(openrouter|gemini)\//, "");
}

/** `cache_read` → `Cache read`, for the cost-by-type breakdown. */
export function tokenTypeLabel(key: string): string {
  const labels: Record<string, string> = {
    prompt: "Prompt",
    completion: "Completion",
    cache_read: "Cache read",
    cache_write: "Cache write",
    reasoning: "Reasoning",
    request: "Per request",
  };
  return labels[key] ?? key.replace(/_/g, " ");
}

/**
 * The chart series colours, in order.
 *
 * Returned as `var(--chart-N)` rather than resolved values so a theme switch
 * recolours the charts without React re-rendering them — the SVG fills inherit the
 * new custom-property values the moment the `dark` class flips on `<html>`.
 */
export const CHART_COLORS = [
  "var(--chart-1)",
  "var(--chart-2)",
  "var(--chart-3)",
  "var(--chart-4)",
  "var(--chart-5)",
  "var(--chart-6)",
] as const;

export function chartColor(index: number): string {
  return CHART_COLORS[index % CHART_COLORS.length];
}
