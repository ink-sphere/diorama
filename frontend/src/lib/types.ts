/**
 * Mirrors of the backend's pydantic models.
 *
 * `BookRecord` / `TraceLine` come from `diorama/backend/models.py`; `EbookStructure`
 * and friends from `diorama/ebook/models.py`. Keep them in sync — nothing generates
 * these, and a silent drift shows up as an empty reader rather than a type error.
 */

export type BookStatus = "queued" | "processing" | "ready" | "failed";
export type TraceKind = "status" | "thinking" | "tool" | "done" | "error";
export type TraceStatus = "pending" | "done" | "error";

export interface Coverage {
  covered: boolean;
  total_blocks: number;
  assigned_blocks: number;
}

export interface ReadingProgress {
  section_index: number;
  page: number;
  pages: number;
  percent: number;
  updated_at?: string | null;
}

export interface BookRecord {
  id: string;
  title: string;
  author?: string | null;
  source_filename: string;
  status: BookStatus;
  created_at: string;
  finished_at?: string | null;
  level_types: string[];
  structure_line?: string | null;
  breakdown: Record<string, number>;
  top_level_count?: number | null;
  coverage?: Coverage | null;
  cost_usd?: number | null;
  error?: string | null;
  progress?: ReadingProgress | null;
}

export interface TraceLine {
  id: string;
  kind: TraceKind;
  status: TraceStatus;
  text: string;
  tool?: string | null;
  at: number;
}

export interface StructureNode {
  level_type: string;
  number?: string | null;
  title?: string | null;
  start_block_id: number;
  end_block_id: number;
  text?: string | null;
  segments?: string[] | null;
  preamble_text?: string | null;
  children: StructureNode[];
}

export interface EbookStructure {
  title: string;
  author?: string | null;
  level_types: string[];
  root: StructureNode[];
  toc: unknown[];
  coverage: Coverage;
  cost_usd: number;
}

export interface UploadResponse {
  book: BookRecord;
  stream_url: string;
}

/* -------------------------------------------------------------------------- */
/* Settings — mirrors `diorama/backend/settings.py`.                           */
/* -------------------------------------------------------------------------- */

export type Provider = "openrouter" | "google";

/**
 * Where a live value came from. The backend resolves settings → env → default, so
 * a field can be filled without the user ever having typed it here; `source` is
 * what lets the form say so instead of looking mysteriously pre-populated.
 */
export type ValueSource = "settings" | "env" | "default" | "none";

/** One provider and the state of its credential. Keys are held per provider. */
export interface ProviderView {
  id: Provider;
  name: string;
  api_key_env: string;
  console_url: string;
  key_prefix_hint: string;
  blurb: string;
  /** The litellm prefix that marks a model as this provider's (`gemini/`). */
  model_prefix: string;
  api_key_configured: boolean;
  /** A display-only mask like `sk-or-v1…a4f2`; the real key never leaves the backend. */
  api_key_masked?: string | null;
  api_key_source: ValueSource;
}

export interface AgentView {
  id: string;
  name: string;
  description: string;
  /** The model this agent will actually run with, after resolution. */
  model_id: string;
  model_source: ValueSource;
  default_model_id: string;
  model_env_var: string;
  /** Only what's saved in settings.json — null when the value is inherited. */
  configured_model_id?: string | null;
  /** Derived from `model_id`'s prefix; null when it names no known provider. */
  provider?: Provider | null;
}

export interface SettingsView {
  providers: ProviderView[];
  agents: AgentView[];
}

/**
 * A partial write — an omitted entry keeps its stored value, an empty string erases
 * it (clearing a key, or resetting an agent to inherit). The form only ever holds a
 * mask of a key and a possibly-inherited model id, so sending everything it rendered
 * would bake inherited values into the file.
 */
export interface SettingsUpdate {
  /** provider id → API key; "" clears the saved key. */
  api_keys?: Record<string, string>;
  /** agent id → litellm model id; "" resets that agent to inherit. */
  agents?: Record<string, string>;
}

export interface CatalogueEntry {
  /** The litellm id (`openrouter/openai/gpt-4o`, `gemini/gemini-2.5-flash`). */
  id: string;
  provider: Provider;
  /** The bare id at that provider, as its own console spells it. */
  provider_model_id: string;
  name: string;
  vendor: string;
  context_length?: number | null;
  prompt_price: number;
  completion_price: number;
  /** False when no rate is known — 0.0 then means "no idea", not "free". */
  pricing_known: boolean;
  supports_tools: boolean;
}

/** Why one provider's slice of the picker is (or isn't) populated. */
export interface CatalogueStatus {
  provider: Provider;
  name: string;
  available: boolean;
  /** True when the provider isn't connected — its models are withheld, not missing. */
  needs_key: boolean;
  count: number;
}

export interface ModelCatalogue {
  models: CatalogueEntry[];
  /** False when no provider returned anything at all. */
  available: boolean;
  providers: CatalogueStatus[];
}

export interface ConnectionTest {
  ok: boolean;
  message: string;
  provider?: Provider;
  label?: string | null;
  /** OpenRouter reports spend against the key; Google has no equivalent. */
  usage_usd?: number | null;
  limit_usd?: number | null;
  is_free_tier?: boolean | null;
  warnings: string[];
}

/* -------------------------------------------------------------------------- */
/* Cost tracking — mirrors `diorama/models/usage.py` + `backend/usage_store.py`. */
/* -------------------------------------------------------------------------- */

/** Agent turn, or the context compactor summarising history to stay in-window. */
export type LLMCallKind = "turn" | "compaction";

/** `retry` failed transiently and was re-issued; `error` failed terminally. */
export type LLMCallStatus = "ok" | "retry" | "error";

/** Which rate table produced a call's figures — surfaced so an estimate never
 *  masquerades as a real charge. */
export type PricingSource =
  | "openrouter_live"
  | "google_static"
  | "litellm_static"
  | "actual"
  | "unpriced";

/**
 * One LLM call, priced and attributed.
 *
 * `route` is who bills you (OpenRouter); `provider` is the upstream that actually
 * served the tokens, which OpenRouter picks per request. They differ, and the
 * dashboard reports both.
 */
export interface LLMCallRecord {
  id: string;
  run_id?: string | null;
  book_id?: string | null;
  agent_id?: string | null;

  kind: LLMCallKind;
  status: LLMCallStatus;
  turn?: number | null;
  attempt: number;

  started_at: string;
  duration_ms?: number | null;

  model_id: string;
  model: string;
  route: string;
  provider?: string | null;
  streamed: boolean;
  finish_reason?: string | null;
  error?: string | null;

  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
  reasoning_tokens: number;

  cost_usd: number;
  estimated_cost_usd: number;
  actual_cost_usd?: number | null;
  cost_by_type: Record<string, number>;
  pricing_source: PricingSource;
}

export interface UsageTotals {
  /** Every recorded attempt, including retried and failed ones. */
  calls: number;
  /** Attempts that completed and returned usage — what you were actually billed for. */
  billed_calls: number;
  failed_calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
  reasoning_tokens: number;
  cost_usd: number;
  estimated_cost_usd: number;
  cost_by_type: Record<string, number>;
  avg_duration_ms?: number | null;
}

export interface GroupTotals {
  key: string;
  label: string;
  calls: number;
  total_tokens: number;
  cost_usd: number;
  /** On provider groups: the models that provider served. */
  detail: string[];
}

export interface DailyPoint {
  date: string;
  calls: number;
  total_tokens: number;
  cost_usd: number;
}

export interface BookUsageRow {
  book_id: string;
  title: string;
  author?: string | null;
  status?: string | null;
  /** False when the ledger outlived its shelf entry — still counted, never hidden. */
  known_book: boolean;
  runs: number;
  totals: UsageTotals;
  models: string[];
  providers: string[];
  first_call_at?: string | null;
  last_call_at?: string | null;
}

export interface UsageSummary {
  totals: UsageTotals;
  by_model: GroupTotals[];
  by_provider: GroupTotals[];
  by_route: GroupTotals[];
  by_agent: GroupTotals[];
  by_kind: GroupTotals[];
  daily: DailyPoint[];
  books: BookUsageRow[];
}

/** One agent run within a book's ledger — reprocessing appends, never replaces. */
export interface RunGroup {
  run_id: string;
  agent_id?: string | null;
  model_ids: string[];
  started_at?: string | null;
  ended_at?: string | null;
  totals: UsageTotals;
}

export interface BookUsage {
  book_id: string;
  title: string;
  author?: string | null;
  status?: string | null;
  known_book: boolean;
  totals: UsageTotals;
  by_model: GroupTotals[];
  by_provider: GroupTotals[];
  by_kind: GroupTotals[];
  runs: RunGroup[];
  calls: LLMCallRecord[];
}
