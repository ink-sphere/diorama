"use client";

import { AnimatePresence, motion } from "framer-motion";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  AlertIcon,
  CheckIcon,
  ChevronLeftIcon,
  KeyIcon,
  SparkIcon,
} from "@/components/Icons";
import { ThemeToggle } from "@/components/ThemeToggle";
import { getSettings, listModels, saveSettings, testConnection } from "@/lib/api";
import type {
  CatalogueEntry,
  ConnectionTest,
  SettingsView as Settings,
  ValueSource,
} from "@/lib/types";

import { ModelPicker } from "./ModelPicker";

/**
 * Which model each agent runs, and the key it authenticates with.
 *
 * The backend resolves every value as **settings → environment → default**, and
 * reports which of the three each live value came from. This page leans on that:
 * a field is only ever "dirty" relative to what is *saved here*, never relative to
 * what is merely inherited — so opening the page and pressing Save on an untouched
 * form writes nothing, and an existing `.env` is never silently baked into
 * `settings.json`.
 *
 * The API key is write-only across the wire. The backend returns a mask
 * (`sk-or-v1…a4f2`) and never the key, so an empty input means "leave it alone"
 * rather than "clear it" — clearing needs its own explicit action.
 */
const OFFLINE_MESSAGE =
  "Can't reach the Diorama backend. Start it with `uv run uvicorn diorama.backend.main:app --reload --port 8000`.";

/** Agent model drafts: `null` means "inherit", matching `configured_model_id`. */
type ModelDrafts = Record<string, string | null>;

export function SettingsView() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [models, setModels] = useState<CatalogueEntry[] | null>(null);
  const [catalogueAvailable, setCatalogueAvailable] = useState(true);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [drafts, setDrafts] = useState<ModelDrafts>({});
  const [keyDraft, setKeyDraft] = useState("");
  const [clearKey, setClearKey] = useState(false);

  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState(0);
  const [testing, setTesting] = useState(false);
  const [test, setTest] = useState<ConnectionTest | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const current = await getSettings();
        if (cancelled) return;
        setSettings(current);
        setDrafts(draftsFrom(current));
        setError(null);
      } catch {
        if (!cancelled) setError(OFFLINE_MESSAGE);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // The catalogue is fetched separately: it can take a network round trip on a
  // cold cache, and the form is perfectly usable while it lands.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const catalogue = await listModels();
        if (cancelled) return;
        setModels(catalogue.models);
        setCatalogueAvailable(catalogue.available);
      } catch {
        if (!cancelled) {
          setModels([]);
          setCatalogueAvailable(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const dirty = useMemo(() => {
    if (!settings) return false;
    if (keyDraft.trim() !== "" || clearKey) return true;
    return settings.agents.some(
      (agent) => (drafts[agent.id] ?? null) !== (agent.configured_model_id ?? null),
    );
  }, [settings, drafts, keyDraft, clearKey]);

  const handleSave = useCallback(async () => {
    if (!settings) return;
    setSaving(true);
    setTest(null);
    try {
      const changed = settings.agents.filter(
        (agent) => (drafts[agent.id] ?? null) !== (agent.configured_model_id ?? null),
      );
      const next = await saveSettings({
        ...(clearKey
          ? { clear_api_key: true }
          : keyDraft.trim()
            ? { api_key: keyDraft.trim() }
            : {}),
        ...(changed.length
          ? {
              agents: Object.fromEntries(
                changed.map((agent) => [agent.id, drafts[agent.id] ?? ""]),
              ),
            }
          : {}),
      });
      setSettings(next);
      setDrafts(draftsFrom(next));
      setKeyDraft("");
      setClearKey(false);
      setSavedAt(Date.now());
      setError(null);
    } catch (saveError) {
      setError(
        saveError instanceof Error ? saveError.message : "Couldn't save settings.",
      );
    } finally {
      setSaving(false);
    }
  }, [settings, drafts, keyDraft, clearKey]);

  const handleRevert = useCallback(() => {
    if (!settings) return;
    setDrafts(draftsFrom(settings));
    setKeyDraft("");
    setClearKey(false);
    setTest(null);
  }, [settings]);

  const handleTest = useCallback(async () => {
    setTesting(true);
    setTest(null);
    try {
      setTest(await testConnection(keyDraft.trim() || undefined));
    } catch {
      setTest({ ok: false, message: "Couldn't run the test.", warnings: [] });
    } finally {
      setTesting(false);
    }
  }, [keyDraft]);

  const provider = settings?.providers.find((p) => p.id === settings.provider);

  return (
    <div className="min-h-full">
      <div className="mx-auto w-full max-w-3xl px-6 pb-40 sm:px-10">
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

        <section className="border-t border-rule pt-12 pb-10">
          <motion.h1
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
            className="font-serif text-[2.4rem] leading-[1.1] font-normal tracking-[-0.015em] text-ink"
          >
            Settings
          </motion.h1>
          <motion.p
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5, delay: 0.06, ease: [0.16, 1, 0.3, 1] }}
            className="mt-4 max-w-xl font-serif text-[1.02rem] leading-relaxed text-ink-soft"
          >
            Diorama&apos;s agents read your books for you. Choose which model each one
            thinks with, and the key it bills against.
          </motion.p>
        </section>

        {error ? (
          <p className="mb-8 rounded-[3px] border border-danger/40 px-4 py-3 text-[0.85rem] text-danger">
            {error}
          </p>
        ) : null}

        {loading ? (
          <SettingsSkeleton />
        ) : settings ? (
          <div className="space-y-14">
            <Section
              title="Provider"
              blurb="Where every agent's model requests are routed."
            >
              <div className="flex flex-wrap gap-2">
                {settings.providers.map((option) => (
                  <span
                    key={option.id}
                    aria-current={option.id === settings.provider}
                    className={`inline-flex items-center gap-2 rounded-full px-3.5 py-1.5 text-[0.85rem] ring-1 ${
                      option.id === settings.provider
                        ? "bg-shell-raised text-ink ring-rule-strong"
                        : "text-ink-faint ring-rule"
                    }`}
                  >
                    {option.id === settings.provider ? (
                      <CheckIcon className="size-3.5 text-accent" />
                    ) : null}
                    {option.name}
                  </span>
                ))}
              </div>
              <p className="mt-3 text-[0.8rem] text-ink-faint">
                OpenRouter is the only provider Diorama routes through today.
              </p>
            </Section>

            <Section
              title="API key"
              blurb="Shared by every agent. Stored locally in .diorama_data/settings.json — it never leaves this machine."
            >
              {!settings.api_key_configured ? (
                <p className="mb-4 flex items-start gap-2.5 rounded-[3px] border border-rule bg-shell-raised px-3.5 py-3 text-[0.84rem] leading-relaxed text-ink-soft">
                  <AlertIcon className="mt-0.5 size-4 shrink-0 text-accent" />
                  <span>
                    No key is set, so books can&apos;t be processed yet. Create one at{" "}
                    <a
                      href={provider?.console_url ?? "https://openrouter.ai/keys"}
                      target="_blank"
                      rel="noreferrer"
                      className="text-ink underline decoration-rule-strong underline-offset-2 hover:decoration-ink"
                    >
                      openrouter.ai/keys
                    </a>
                    .
                  </span>
                </p>
              ) : null}

              <label htmlFor="api-key" className="label mb-2 block text-ink-faint">
                {provider?.name ?? "Provider"} key
              </label>
              <div className="flex flex-col gap-2 sm:flex-row">
                <div className="relative flex-1">
                  <KeyIcon className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-ink-faint" />
                  <input
                    id="api-key"
                    type="password"
                    value={keyDraft}
                    onChange={(event) => {
                      setKeyDraft(event.target.value);
                      setClearKey(false);
                      setTest(null);
                    }}
                    autoComplete="off"
                    spellCheck={false}
                    placeholder={
                      clearKey
                        ? "Will be cleared when you save"
                        : (settings.api_key_masked ?? "sk-or-v1-…")
                    }
                    className="w-full rounded-[3px] border border-rule bg-shell-raised py-2 pr-3 pl-9 font-mono text-[0.82rem] text-ink transition-colors placeholder:font-mono placeholder:text-ink-faint focus:border-rule-strong focus:outline-none"
                  />
                </div>
                <button
                  type="button"
                  onClick={handleTest}
                  disabled={testing}
                  className="label shrink-0 rounded-[3px] border border-rule px-4 py-2 text-ink-soft transition hover:border-rule-strong hover:text-ink disabled:opacity-50"
                >
                  {testing ? "Testing…" : "Test connection"}
                </button>
              </div>

              <div className="mt-2.5 flex flex-wrap items-center gap-x-3 gap-y-1.5">
                <SourceNote
                  source={settings.api_key_source}
                  envVar={provider?.api_key_env ?? "OPENROUTER_API_KEY"}
                  configuredLabel="Saved here"
                  noneLabel="Not set"
                />
                {settings.api_key_source === "settings" && !clearKey ? (
                  <button
                    type="button"
                    onClick={() => {
                      setClearKey(true);
                      setKeyDraft("");
                      setTest(null);
                    }}
                    className="text-[0.78rem] text-ink-faint underline decoration-rule-strong underline-offset-2 transition-colors hover:text-danger"
                  >
                    Clear saved key
                  </button>
                ) : null}
                {clearKey ? (
                  <button
                    type="button"
                    onClick={() => setClearKey(false)}
                    className="text-[0.78rem] text-danger underline decoration-danger/40 underline-offset-2"
                  >
                    Keep it after all
                  </button>
                ) : null}
              </div>

              <AnimatePresence>
                {test ? <TestResult key="test" result={test} /> : null}
              </AnimatePresence>
            </Section>

            <Section
              title="Agents"
              blurb="Each agent can think with a different model — pay for reasoning only where it earns its keep."
            >
              <div className="space-y-8">
                {settings.agents.map((agent) => (
                  <div key={agent.id}>
                    <div className="flex items-baseline justify-between gap-4">
                      <label
                        htmlFor={`model-${agent.id}`}
                        className="font-serif text-[1.15rem] text-ink"
                      >
                        {agent.name}
                      </label>
                      {(drafts[agent.id] ?? null) !== null ? (
                        <button
                          type="button"
                          onClick={() =>
                            setDrafts((current) => ({ ...current, [agent.id]: null }))
                          }
                          className="shrink-0 text-[0.78rem] text-ink-faint underline decoration-rule-strong underline-offset-2 transition-colors hover:text-ink-soft"
                        >
                          Use inherited
                        </button>
                      ) : null}
                    </div>
                    <p className="mt-1.5 mb-3 max-w-xl text-[0.85rem] leading-relaxed text-ink-soft">
                      {agent.description}
                    </p>

                    <ModelPicker
                      id={`model-${agent.id}`}
                      value={drafts[agent.id] ?? agent.model_id}
                      onChange={(modelId) =>
                        setDrafts((current) => ({ ...current, [agent.id]: modelId }))
                      }
                      models={models ?? []}
                      available={catalogueAvailable}
                      loading={models === null}
                    />

                    <div className="mt-2">
                      <SourceNote
                        source={
                          (drafts[agent.id] ?? null) !== null
                            ? "settings"
                            : agent.model_source
                        }
                        envVar={agent.model_env_var}
                        configuredLabel="Saved here"
                        defaultLabel={`Diorama default (${shortId(agent.default_model_id)})`}
                      />
                    </div>
                  </div>
                ))}
              </div>
            </Section>
          </div>
        ) : null}
      </div>

      <AnimatePresence>
        {dirty ? (
          <SaveBar
            key="save"
            saving={saving}
            onSave={handleSave}
            onRevert={handleRevert}
          />
        ) : savedAt ? (
          <SavedToast key={savedAt} />
        ) : null}
      </AnimatePresence>
    </div>
  );
}

function draftsFrom(settings: Settings): ModelDrafts {
  return Object.fromEntries(
    settings.agents.map((agent) => [agent.id, agent.configured_model_id ?? null]),
  );
}

/** `openrouter/openai/gpt-4o-mini` reads better as `openai/gpt-4o-mini` in prose. */
function shortId(modelId: string): string {
  return modelId.replace(/^openrouter\//, "");
}

function Section({
  title,
  blurb,
  children,
}: {
  title: string;
  blurb?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="border-t border-rule pt-8">
      <h2 className="label text-ink-faint">{title}</h2>
      {blurb ? (
        <p className="mt-3 mb-6 max-w-xl font-serif text-[1rem] leading-relaxed text-ink-soft">
          {blurb}
        </p>
      ) : (
        <div className="mb-6" />
      )}
      {children}
    </section>
  );
}

/** Says where a live value came from, so an inherited field isn't a mystery. */
function SourceNote({
  source,
  envVar,
  configuredLabel,
  defaultLabel,
  noneLabel,
}: {
  source: ValueSource;
  envVar: string;
  configuredLabel: string;
  defaultLabel?: string;
  noneLabel?: string;
}) {
  const text =
    source === "settings"
      ? configuredLabel
      : source === "env"
        ? `Inherited from ${envVar}`
        : source === "default"
          ? (defaultLabel ?? "Diorama default")
          : (noneLabel ?? "Not set");
  return <span className="text-[0.78rem] text-ink-faint">{text}</span>;
}

function TestResult({ result }: { result: ConnectionTest }) {
  return (
    <motion.div
      initial={{ opacity: 0, height: 0 }}
      animate={{ opacity: 1, height: "auto" }}
      exit={{ opacity: 0, height: 0 }}
      transition={{ duration: 0.25, ease: [0.16, 1, 0.3, 1] }}
      className="overflow-hidden"
    >
      <div className="mt-4 rounded-[3px] border border-rule bg-shell-raised px-3.5 py-3">
        <p
          className={`flex items-center gap-2 text-[0.86rem] ${
            result.ok ? "text-ink" : "text-danger"
          }`}
        >
          {result.ok ? (
            <CheckIcon className="size-4 shrink-0 text-accent" />
          ) : (
            <AlertIcon className="size-4 shrink-0" />
          )}
          {result.message}
        </p>

        {result.ok ? (
          <p className="mt-1.5 pl-6 text-[0.78rem] text-ink-faint tabular-nums">
            {[
              result.label,
              result.usage_usd != null
                ? `$${result.usage_usd.toFixed(2)} used`
                : null,
              result.limit_usd != null
                ? `$${result.limit_usd.toFixed(2)} limit`
                : "no spend limit",
            ]
              .filter(Boolean)
              .join(" · ")}
          </p>
        ) : null}

        {result.warnings.map((warning) => (
          <p
            key={warning}
            className="mt-2 flex items-start gap-2 pl-6 text-[0.8rem] leading-relaxed text-ink-soft"
          >
            <AlertIcon className="mt-0.5 size-3.5 shrink-0 text-danger" />
            {warning}
          </p>
        ))}
      </div>
    </motion.div>
  );
}

function SaveBar({
  saving,
  onSave,
  onRevert,
}: {
  saving: boolean;
  onSave: () => void;
  onRevert: () => void;
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: 20 }}
      transition={{ duration: 0.28, ease: [0.16, 1, 0.3, 1] }}
      className="fixed inset-x-0 bottom-0 z-30 border-t border-rule bg-shell/85 backdrop-blur-sm"
    >
      <div className="mx-auto flex w-full max-w-3xl items-center justify-between gap-4 px-6 py-4 sm:px-10">
        <p className="text-[0.84rem] text-ink-soft">
          Unsaved changes. They apply to the next book you process.
        </p>
        <div className="flex shrink-0 items-center gap-2">
          <button
            type="button"
            onClick={onRevert}
            disabled={saving}
            className="label rounded-[3px] px-3 py-2 text-ink-faint transition hover:text-ink-soft disabled:opacity-50"
          >
            Discard
          </button>
          <button
            type="button"
            onClick={onSave}
            disabled={saving}
            className="label rounded-[3px] bg-ink px-4 py-2 text-shell transition hover:opacity-90 disabled:opacity-50"
          >
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </motion.div>
  );
}

function SavedToast() {
  return (
    <motion.p
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: [0, 1, 1, 0], y: 0 }}
      transition={{ duration: 2.6, times: [0, 0.12, 0.75, 1] }}
      className="label pointer-events-none fixed inset-x-0 bottom-8 z-30 flex items-center justify-center gap-2 text-ink-soft"
    >
      <span className="inline-flex items-center gap-2 rounded-full border border-rule bg-shell-raised px-4 py-2 shadow-page">
        <SparkIcon className="size-3.5 text-accent" />
        Settings saved
      </span>
    </motion.p>
  );
}

function SettingsSkeleton() {
  return (
    <div className="space-y-14">
      {Array.from({ length: 3 }).map((_, index) => (
        <div key={index} className="animate-pulse border-t border-rule pt-8">
          <div className="h-2.5 w-24 rounded-full bg-shell-raised" />
          <div className="mt-5 h-3 w-2/3 rounded-full bg-shell-raised" />
          <div className="mt-6 h-10 w-full rounded-[3px] bg-shell-raised" />
        </div>
      ))}
    </div>
  );
}
