"use client";

import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useMemo, useRef, useState } from "react";

import { CheckIcon, ChevronDownIcon, SearchIcon } from "@/components/Icons";
import type { CatalogueEntry, CatalogueStatus, ProviderView } from "@/lib/types";

/**
 * A searchable list of every model, across every provider a key is set for.
 *
 * Two filters, for two different traps. Tool calling: OpenRouter alone lists ~340
 * models, most of which can't call tools — and an agent pointed at a tool-less model
 * fails only once a book is halfway through processing, so the list is narrowed to
 * tool-capable models by default. Provider: the merged list would otherwise bury a
 * dozen Gemini models under hundreds of OpenRouter ones, and "which provider am I
 * about to bill" is the question a chip answers faster than reading a prefix.
 *
 * The catalogue is a convenience, never a gate: when nothing can be reached
 * (`available === false`) this degrades to a plain text field, and a model id typed
 * into the search box can always be used verbatim even if it isn't in the list.
 */
export function ModelPicker({
  value,
  onChange,
  models,
  providers,
  statuses,
  available,
  loading,
  id,
}: {
  value: string;
  onChange: (modelId: string) => void;
  models: CatalogueEntry[];
  providers: ProviderView[];
  statuses: CatalogueStatus[];
  available: boolean;
  loading: boolean;
  id?: string;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [showAll, setShowAll] = useState(false);
  const [provider, setProvider] = useState<string | null>(null);
  const [cursor, setCursor] = useState(0);

  const wrapperRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const selected = useMemo(
    () => models.find((model) => model.id === value),
    [models, value],
  );
  const providerNames = useMemo(
    () => new Map(providers.map((p) => [p.id as string, p.name])),
    [providers],
  );

  const matches = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return models.filter((model) => {
      // A model that's already selected stays visible even if it fails a filter —
      // otherwise the list appears not to contain the very thing the button above
      // it is displaying.
      const chosen = model.id === value;
      if (provider && model.provider !== provider && !chosen) return false;
      if (!showAll && !model.supports_tools && !chosen) return false;
      if (!needle) return true;
      return `${model.name} ${model.id}`.toLowerCase().includes(needle);
    });
  }, [models, query, showAll, provider, value]);

  const visible = matches.slice(0, MAX_ROWS);
  const typed = query.trim();
  const custom =
    typed.length > 2 &&
    !models.some((m) => m.id === typed || m.provider_model_id === typed)
      ? typed
      : null;
  // Only worth a chip row when there is actually something to choose between.
  const chips = statuses.filter((status) => status.count > 0);
  const missing = statuses.filter((status) => status.needs_key);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (!wrapperRef.current?.contains(event.target as Node)) setOpen(false);
    };
    window.addEventListener("pointerdown", onPointerDown);
    return () => window.removeEventListener("pointerdown", onPointerDown);
  }, [open]);

  // Focus the search box on open, and keep the keyboard cursor in view as it moves.
  useEffect(() => {
    if (open) searchRef.current?.focus();
  }, [open]);

  useEffect(() => {
    listRef.current
      ?.querySelector('[data-active="true"]')
      ?.scrollIntoView({ block: "nearest" });
  }, [cursor, query]);

  function choose(modelId: string) {
    onChange(normalizeModelId(modelId, providers));
    setOpen(false);
    setQuery("");
  }

  function onKeyDown(event: React.KeyboardEvent) {
    const rows = custom ? visible.length + 1 : visible.length;
    if (event.key === "Escape") {
      event.preventDefault();
      setOpen(false);
      return;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (rows === 0) return;
      const step = event.key === "ArrowDown" ? 1 : -1;
      setCursor((current) => (current + step + rows) % rows);
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      if (custom && cursor === rows - 1) choose(custom);
      else if (visible[cursor]) choose(visible[cursor].id);
    }
  }

  // Nothing to list: a text field is more useful than an empty dropdown. Say which
  // of the two reasons it is, since only one of them is the user's to fix.
  if (!available && !loading) {
    const unconnected = statuses.length > 0 && statuses.every((s) => s.needs_key);
    return (
      <div>
        <input
          id={id}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          spellCheck={false}
          placeholder="openrouter/openai/gpt-4o"
          className="w-full rounded-[3px] border border-rule bg-shell-raised px-3 py-2 font-mono text-[0.82rem] text-ink transition-colors placeholder:text-ink-faint focus:border-rule-strong focus:outline-none"
        />
        <p className="mt-1.5 text-[0.78rem] text-ink-faint">
          {unconnected
            ? "Add a provider key above to browse its models — or enter an id by hand."
            : "No model list available — enter a model id by hand."}
        </p>
      </div>
    );
  }

  return (
    <div ref={wrapperRef} className="relative">
      <button
        id={id}
        type="button"
        onClick={() => {
          setOpen(!open);
          setQuery("");
          setCursor(0);
        }}
        aria-expanded={open}
        aria-haspopup="listbox"
        disabled={loading}
        className="flex w-full items-center justify-between gap-3 rounded-[3px] border border-rule bg-shell-raised px-3 py-2 text-left transition-colors hover:border-rule-strong focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:opacity-50"
      >
        <span className="min-w-0">
          <span className="block truncate font-sans text-[0.9rem] text-ink">
            {loading ? "Loading models…" : (selected?.name ?? value)}
          </span>
          <span className="mt-0.5 block truncate font-mono text-[0.72rem] text-ink-faint">
            {value}
          </span>
        </span>
        <span className="flex shrink-0 items-center gap-2.5">
          {selected ? (
            <>
              <span className="label hidden text-ink-faint sm:inline">
                {providerNames.get(selected.provider) ?? selected.provider}
              </span>
              <PriceTag model={selected} />
            </>
          ) : null}
          <ChevronDownIcon className="size-4 text-ink-faint" />
        </span>
      </button>

      <AnimatePresence>
        {open ? (
          <motion.div
            initial={{ opacity: 0, y: -6, scale: 0.99 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -6, scale: 0.99 }}
            transition={{ duration: 0.18, ease: [0.16, 1, 0.3, 1] }}
            className="absolute inset-x-0 z-40 mt-1.5 origin-top overflow-hidden rounded-[4px] border border-rule bg-shell-raised shadow-page"
          >
            <div className="flex items-center gap-2.5 border-b border-rule px-3 py-2.5">
              <SearchIcon className="size-4 shrink-0 text-ink-faint" />
              <input
                ref={searchRef}
                value={query}
                onChange={(event) => {
                  setQuery(event.target.value);
                  setCursor(0);
                }}
                onKeyDown={onKeyDown}
                placeholder="Search models, or paste a model id"
                aria-label="Search models"
                className="w-full min-w-0 bg-transparent font-sans text-[0.88rem] text-ink placeholder:text-ink-faint focus:outline-none"
              />
            </div>

            {chips.length > 1 ? (
              <div className="flex flex-wrap gap-1.5 border-b border-rule px-3 py-2">
                <Chip
                  label="All"
                  count={models.length}
                  active={provider === null}
                  onClick={() => {
                    setProvider(null);
                    setCursor(0);
                  }}
                />
                {chips.map((status) => (
                  <Chip
                    key={status.provider}
                    label={status.name}
                    count={status.count}
                    active={provider === status.provider}
                    onClick={() => {
                      setProvider(
                        provider === status.provider ? null : status.provider,
                      );
                      setCursor(0);
                    }}
                  />
                ))}
              </div>
            ) : null}

            <div ref={listRef} className="max-h-72 overflow-y-auto py-1">
              {visible.map((model, index) => (
                <Row
                  key={model.id}
                  model={model}
                  providerName={providerNames.get(model.provider)}
                  active={index === cursor}
                  chosen={model.id === value}
                  onSelect={() => choose(model.id)}
                  onHover={() => setCursor(index)}
                />
              ))}

              {custom ? (
                <button
                  type="button"
                  data-active={cursor === visible.length}
                  onMouseEnter={() => setCursor(visible.length)}
                  onClick={() => choose(custom)}
                  className={`flex w-full items-center gap-2 px-3 py-2 text-left transition-colors ${
                    cursor === visible.length ? "bg-shell" : ""
                  }`}
                >
                  <span className="truncate font-mono text-[0.78rem] text-ink">
                    Use “{normalizeModelId(custom, providers)}”
                  </span>
                  <span className="label shrink-0 text-ink-faint">Custom</span>
                </button>
              ) : null}

              {visible.length === 0 && !custom ? (
                <p className="px-3 py-6 text-center text-[0.82rem] text-ink-faint">
                  Nothing matches that.
                </p>
              ) : null}

              {matches.length > MAX_ROWS ? (
                <p className="px-3 py-2 text-[0.76rem] text-ink-faint">
                  {matches.length - MAX_ROWS} more — keep typing to narrow it down.
                </p>
              ) : null}
            </div>

            <div className="border-t border-rule px-3 py-2.5">
              <label className="flex cursor-pointer items-center gap-2 text-[0.78rem] text-ink-soft">
                <input
                  type="checkbox"
                  checked={showAll}
                  onChange={(event) => {
                    setShowAll(event.target.checked);
                    setCursor(0);
                  }}
                  className="size-3.5 accent-[var(--accent)]"
                />
                Include models without tool calling
              </label>
              {missing.length ? (
                <p className="mt-1.5 text-[0.72rem] text-ink-faint">
                  Add a {missing.map((status) => status.name).join(" or ")} key above
                  to list its models here.
                </p>
              ) : null}
              <p className="mt-1.5 text-[0.72rem] text-ink-faint">
                Prices are USD per million tokens, input / output.
              </p>
            </div>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </div>
  );
}

/** Rows past this just make the list slow to scan; the search box is the real filter. */
const MAX_ROWS = 60;

/**
 * A hand-typed id is routed by its prefix, so accept one that already names a
 * provider and assume OpenRouter otherwise — which is what a bare `openai/gpt-4o`
 * pasted from OpenRouter's own site means.
 */
function normalizeModelId(modelId: string, providers: ProviderView[]): string {
  const trimmed = modelId.trim();
  if (providers.some((p) => trimmed.startsWith(p.model_prefix))) return trimmed;
  return `openrouter/${trimmed}`;
}

function Chip({
  label,
  count,
  active,
  onClick,
}: {
  label: string;
  count: number;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[0.75rem] ring-1 transition-colors ${
        active
          ? "bg-shell text-ink ring-rule-strong"
          : "text-ink-faint ring-rule hover:text-ink-soft"
      }`}
    >
      {label}
      <span className="tabular-nums opacity-60">{count}</span>
    </button>
  );
}

function Row({
  model,
  providerName,
  active,
  chosen,
  onSelect,
  onHover,
}: {
  model: CatalogueEntry;
  providerName?: string;
  active: boolean;
  chosen: boolean;
  onSelect: () => void;
  onHover: () => void;
}) {
  return (
    <button
      type="button"
      role="option"
      aria-selected={chosen}
      data-active={active}
      onMouseEnter={onHover}
      onClick={onSelect}
      className={`flex w-full items-center gap-3 px-3 py-2 text-left transition-colors ${
        active ? "bg-shell" : ""
      }`}
    >
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-1.5">
          <span className="truncate font-sans text-[0.86rem] text-ink">
            {model.name}
          </span>
          {chosen ? <CheckIcon className="size-3.5 shrink-0 text-accent" /> : null}
        </span>
        <span className="mt-0.5 block truncate font-mono text-[0.7rem] text-ink-faint">
          {model.provider_model_id}
        </span>
      </span>
      <span className="flex shrink-0 flex-col items-end gap-0.5">
        <PriceTag model={model} />
        <span className="font-sans text-[0.68rem] text-ink-faint tabular-nums">
          {providerName ? `${providerName} · ` : ""}
          {model.context_length ? `${formatContext(model.context_length)} ctx` : "—"}
          {model.supports_tools ? "" : " · no tools"}
        </span>
      </span>
    </button>
  );
}

function PriceTag({ model }: { model: CatalogueEntry }) {
  // An unknown rate is not a zero one: Google publishes no price list, so a Gemini
  // model outside Diorama's table has no figure to show rather than a free one.
  if (!model.pricing_known) {
    return <span className="label whitespace-nowrap text-ink-faint">Rate unknown</span>;
  }
  const free = model.prompt_price === 0 && model.completion_price === 0;
  return (
    <span className="label whitespace-nowrap text-ink-faint tabular-nums">
      {free
        ? "Free"
        : `$${perMillion(model.prompt_price)} / $${perMillion(model.completion_price)}`}
    </span>
  );
}

/** Providers price per token; per-million is the figure anyone actually compares.
 *
 * Fixed to two decimals so a scrolling column stays aligned under `tabular-nums` —
 * mixing "$1.3" and "$0.25" makes prices harder to compare than showing "$1.30". */
function perMillion(price: number): string {
  const value = price * 1_000_000;
  return value >= 100 ? value.toFixed(0) : value.toFixed(2);
}

function formatContext(tokens: number): string {
  return tokens >= 1_000_000
    ? `${(tokens / 1_000_000).toFixed(tokens % 1_000_000 === 0 ? 0 : 1)}M`
    : `${Math.round(tokens / 1000)}K`;
}
