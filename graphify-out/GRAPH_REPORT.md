# Graph Report - .  (2026-07-29)

## Corpus Check
- 106 files · ~87,261 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1818 nodes · 4713 edges · 89 communities (75 shown, 14 thin omitted)
- Extraction: 92% EXTRACTED · 8% INFERRED · 0% AMBIGUOUS · INFERRED: 397 edges (avg confidence: 0.54)
- Token cost: 0 input · 351,195 output

## Community Hubs (Navigation)
- Agent Event Trace Translation
- ReAct Agent Turn Execution
- Settings Tests
- Context Compaction
- Session Store
- Agent Settings & Providers
- Cost Dashboard Pages
- Scene Segmentation Processing
- Frontend Dependencies
- Core Agent Loop & Tracing
- Model Pricing Tables
- Agent Loop Test Fakes
- EPUB Structure Parsing
- Book Run Lifecycle
- Backend Pydantic Models
- Final Answer Tool
- Settings Routes & Catalogue
- Ebook Loader Tools
- Shelf Page & Icons
- Demo Tools & Agent Tests
- Settings Page & Model Picker
- Tool Result Blocks & Flaky Model
- Reader Chrome & Navigation
- Google Pricing & Providers
- Usage Ledger Store
- Cost Estimation Pricing
- TypeScript Config
- Tool Layer Tests
- Structure Slicer & Coverage
- Scene Segmentation Agent
- Settings Partial Update
- Library API Route Tests
- Frontend API Client & Types
- Project Architecture Overview
- Tool Forward Methods
- Scene Segmentation Tools
- Ebook Data Models
- Ebook Loader Agent
- Segmentation Agent Builder
- Usage Summary Aggregation
- TOC Sidebar & Structure
- LLM Call Provenance
- Model Catalogue Fetchers
- EPUB Block Parsing
- Scene Slicer
- Prompt Caching
- Cost Dashboard Models
- Reader View Page
- Shelf Book Card
- System Prompts & Agent Errors
- Cooperative Cancellation
- Usage API Tests
- Fallback Model & Demo Tools
- Tool Call Hooks & Weave
- LLM Call Record Emission
- Structure-wide Segmentation
- Steering & Follow-up Queues
- Console Rendering
- Tool Image Follow-up
- LiteLLM Model Wrapper
- FastAPI App & Usage Routes
- Ebook Loader Run Lifecycle
- Book Run Subscribers
- Test Model Fakes
- Library JSON Store
- Usage Sink Emission
- Event-to-Trace Mapping
- Root Layout & Theme
- Theme Toggle & Media Query
- Ebook Loader Errors
- Stub Loader Test Double
- Settings Provider Migration
- LiteLLM Completion Call
- Test Retry Backoff Fixture
- Deferred Tool Activation
- ESLint Config
- Next.js Config
- PostCSS Config
- Test Package Init
- Tool Forward Stub
- File Icon Asset
- Globe Icon Asset
- Next.js Logo Asset
- Vercel Logo Asset
- Window Icon Asset
- Diorama Root Package

## God Nodes (most connected - your core abstractions)
1. `FakeModel` - 104 edges
2. `ReactAgent` - 100 edges
3. `response()` - 84 edges
4. `Tool` - 57 edges
5. `LiteLLMModel` - 53 edges
6. `ToolResult` - 46 edges
7. `ContextCompactor` - 44 edges
8. `EbookSceneSegmentationAgent` - 39 edges
9. `EbookContext` - 37 edges
10. `QueuedMessages` - 33 edges

## Surprising Connections (you probably didn't know these)
- `JsonlSessionStore` --semantically_similar_to--> `usage_store.py (append-only ledger)`  [INFERRED] [semantically similar]
  diorama/core/session.py → CLAUDE.md
- `settings.py / resolve_agent_runtime()` --references--> `_BookRun`  [EXTRACTED]
  CLAUDE.md → diorama/backend/processing.py
- `Compaction reserve bumped to 48k tokens` --rationale_for--> `ContextCompactor`  [EXTRACTED]
  CLAUDE.md → diorama/core/context.py
- `EbookContext` --references--> `Deterministic EPUB Parsing`  [EXTRACTED]
  diorama/ebook/parser.py → CLAUDE.md
- `Compaction reserve bumped to 48k tokens` --rationale_for--> `EbookLoaderAgent`  [EXTRACTED]
  CLAUDE.md → diorama/agents/ebook_loader.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Core ReactAgent framework components** — diorama_core_react_reactagent, diorama_core_router_toolrouter, diorama_models_litellm_model_litellmmodel, diorama_core_session_jsonlsessionstore, diorama_core_context_contextcompactor, diorama_core_cancellation_cancellationtoken [EXTRACTED 1.00]
- **Upload -> shelf -> reader processing pipeline** — diorama_agents_ebook_loader_ebookloaderagent, diorama_agents_ebook_scene_segmentation_ebookscenesegmentationagent, diorama_backend_processing_bookrun, diorama_backend_trace_traceline, diorama_backend_routes_books_routes [EXTRACTED 1.00]
- **No global 'current provider' setting design** — diorama_models_providers_provider_for_model, diorama_backend_settings_settings, frontend_src_components_settings_modelpicker, diorama_models_providers_no_global_provider [INFERRED 0.85]

## Communities (89 total, 14 thin omitted)

### Community 0 - "Agent Event Trace Translation"
Cohesion: 0.13
Nodes (50): Translate ReactAgent's typed events into shelf-card trace lines. The agent loop…, ContextUsageEstimate, Deterministic context-size accounting for one provider request. Attributes:…, AgentEndEvent, AgentStartEvent, CompactionEndEvent, CompactionStartEvent, MessageEndEvent (+42 more)

### Community 1 - "ReAct Agent Turn Execution"
Cohesion: 0.06
Nodes (34): _abnormal_stop_reason(), _assistant_message(), _last_tool_text(), _maybe_await(), AgentEvent, Any, Call the model, append the assistant message, and emit its events. Also carries…, Normalise a non-streaming litellm response into ``(LLMResult, raw_usage)``. (+26 more)

### Community 2 - "Settings Tests"
Cohesion: 0.09
Nodes (49): client(), _google_model(), _openrouter_model(), fixture, MonkeyPatch, TestClient, Tests for per-agent model settings and provider credentials. Fully offline: the…, `default_model_id` labels the Reset control, so it has to be the real one. (+41 more)

### Community 3 - "Context Compaction"
Cohesion: 0.07
Nodes (41): build_compaction_prompt(), compaction_threshold(), CompactionResult, estimate_content_tokens(), estimate_context_tokens(), estimate_context_usage(), estimate_message_tokens(), estimate_text_tokens() (+33 more)

### Community 4 - "Session Store"
Cohesion: 0.07
Nodes (35): _apply_compaction(), entries_by_id(), JsonlSessionStore, _new_id(), path_to_entry(), Any, BaseModel, Path (+27 more)

### Community 5 - "Agent Settings & Providers"
Cohesion: 0.08
Nodes (46): _model_warnings(), Flag models this provider doesn't serve, or that can't call tools. Scoped to…, AgentConfig, AgentDefinition, AgentView, build_view(), DioramaSettings, mask_key() (+38 more)

### Community 6 - "Cost Dashboard Pages"
Cohesion: 0.13
Nodes (27): metadata, BookCostView(), CallDetail(), CallRow(), pricingLabel(), AXIS_STYLE, BreakdownChart(), DailySpendChart() (+19 more)

### Community 7 - "Scene Segmentation Processing"
Cohesion: 0.12
Nodes (37): BookScenes, EbookStructure, Segment every leaf section into scenes, publishing one live progress row. Runs…, _segment_scenes(), upsert_book(), data_dir(), _leaf(), offline_runtime() (+29 more)

### Community 8 - "Frontend Dependencies"
Cohesion: 0.05
Nodes (38): eslint, eslint-config-next, framer-motion, dependencies, framer-motion, next, next-themes, react (+30 more)

### Community 9 - "Core Agent Loop & Tracing"
Cohesion: 0.07
Nodes (25): TraceLine / trace.py, AgentEvent typed union (events.py), Execute one tool call, appending its result and emitting its events. Order of…, Run a tool, surfacing its ``on_update`` reports as events while it works. The…, Emit the end-of-execution event and append the resulting message(s). Any tools…, Decide whether a tool requiring approval may run. Resolution order: explicit…, A stateful ReAct agent over diorama's async :class:`LiteLLMModel`. Attributes:…, Whether a run is currently in progress. (+17 more)

### Community 10 - "Model Pricing Tables"
Cohesion: 0.08
Nodes (36): google_pricing.py hand-maintained rate table, litellm_pricing(), ModelPricing, Per-token rates for ``model_id`` from litellm's static cost map, or None.…, Per-unit USD pricing for one model (0.0 for any rate OpenRouter omits).…, Estimated USD cost per token type for one call. `prompt` is the *non-cached*…, client(), ledger() (+28 more)

### Community 11 - "Agent Loop Test Fakes"
Cohesion: 0.17
Nodes (36): FakeModel, Build a non-streaming litellm-style ``ModelResponse`` stand-in., Drop-in stand-in for ``LiteLLMModel`` driven by a scripted response list. Each…, response(), _agent(), _hook_call(), HookTool, Tests for the agent's loop capabilities: cancellation, events, state,… (+28 more)

### Community 12 - "EPUB Structure Parsing"
Cohesion: 0.12
Nodes (35): Path, Parse an EPUB into blocks and a block-anchored table of contents., build_structure(), EbookStructure, Return human-readable errors, or ``[]`` when the tree is valid and coverable., Build the final :class:`EbookStructure` from a validated tree. Raises:…, validate_tree(), _make_context() (+27 more)

### Community 13 - "Book Run Lifecycle"
Cohesion: 0.10
Nodes (35): delete, Drop any finished/failed run so the next ``ensure_started`` starts fresh., reset(), get_book_record(), get_cover(), get_library(), get_scenes(), get_structure() (+27 more)

### Community 14 - "Backend Pydantic Models"
Cohesion: 0.09
Nodes (32): UsageSink, Configure the agent used by every subsequent segmentation call. Args: model…, Coverage, BaseModel, Pydantic models for the library API. ``BookRecord`` is the durable shape…, Where the reader left off in a book. ``section_index`` indexes the book's leaf…, One line of the live agent trace, streamed to the shelf card.…, ReadingProgress (+24 more)

### Community 15 - "Final Answer Tool"
Cohesion: 0.08
Nodes (25): AST, FinalAnswerTool, Any, op, Optional terminal tool. Termination semantics: the diorama loop ends a turn…, Passthrough tool: returns its ``answer`` argument unchanged., Return the ``answer`` argument unchanged. Args: answer (Any): The final answer…, Small, dependency-free demo tools. These exist so the ReAct loop is runnable… (+17 more)

### Community 16 - "Settings Routes & Catalogue"
Cohesion: 0.10
Nodes (34): ConnectionTest, _catalogue(), CatalogueEntry, CatalogueStatus, _entry(), get_models(), get_settings(), _maybe_float() (+26 more)

### Community 17 - "Ebook Loader Tools"
Cohesion: 0.14
Nodes (33): _build_tools(), GetOverviewTool, GetTocTool, ListHeadingsTool, _LoadState, EbookLoaderAgent: extracts an EPUB's hierarchical structure via a ReAct agent.…, Build the initial user message for a ``load()`` run., Mutable state shared by the tools bound to one ``load()`` call. Never touches… (+25 more)

### Community 18 - "Shelf Page & Icons"
Cohesion: 0.10
Nodes (25): BookIcon(), ChevronDownIcon(), CostsIcon(), IconProps, KeyIcon(), PlusIcon(), SearchIcon(), SettingsIcon() (+17 more)

### Community 19 - "Demo Tools & Agent Tests"
Cohesion: 0.13
Nodes (30): CalculatorTool, CurrentTimeTool, Evaluate a basic arithmetic expression (``+ - * / // % **`` and parentheses)., Return the current date and time (UTC by default) as an ISO-8601 string., A model whose ``acompletion`` always returns a streaming generator., Build a non-streaming tool-call object shaped like litellm's., StreamModel, tool_call() (+22 more)

### Community 20 - "Settings Page & Model Picker"
Cohesion: 0.09
Nodes (20): metadata, CheckIcon(), formatContext(), ModelPicker(), normalizeModelId(), perMillion(), PriceTag(), Row() (+12 more)

### Community 21 - "Tool Result Blocks & Flaky Model"
Cohesion: 0.09
Nodes (23): ImageBlock, BaseModel, A run of text produced by a tool., An image produced by a tool, carried as base64. Attributes: data (str):…, TextBlock, FlakyModel, Returns a streaming generator per scripted entry. Each entry is a factory (see…, Raises ``error`` for the first ``failures`` calls, then behaves normally. (+15 more)

### Community 22 - "Reader Chrome & Navigation"
Cohesion: 0.09
Nodes (25): ChevronLeftIcon(), ChevronRightIcon(), ContentsIcon(), ImageIcon(), TypeIcon(), PageNav(), ReaderHeader(), PlatePage() (+17 more)

### Community 23 - "Google Pricing & Providers"
Cohesion: 0.08
Nodes (28): settings.py / resolve_agent_runtime(), _family(), get_pricing(), is_priced(), Hand-maintained per-token rates for Google AI Studio (Gemini) models. Every…, The table key covering ``model_id``, matching the longest family prefix., Per-token rates for a Gemini model, or None if this table doesn't cover it.…, Whether this table covers ``model_id`` (used to label the picker honestly). (+20 more)

### Community 24 - "Usage Ledger Store"
Cohesion: 0.11
Nodes (29): append_call(), build_book_usage(), delete_usage(), ledger_book_ids(), BookRecord, Path, The per-book cost ledger, and the rollups the dashboard renders. Every LLM call…, Book ids that have a ledger on disk. (+21 more)

### Community 25 - "Cost Estimation Pricing"
Cohesion: 0.09
Nodes (20): Best-effort USD cost for a call; returns 0.0 if litellm can't price it. Tries…, Estimate (total_cost, cost_by_type, pricing_source) for one call. Rate tables…, cost_model_candidates(), _f(), normalize_model_id(), _parse_pricing(), PricingTable, Path (+12 more)

### Community 26 - "TypeScript Config"
Cohesion: 0.07
Nodes (28): compilerOptions, allowJs, esModuleInterop, incremental, isolatedModules, jsx, lib, module (+20 more)

### Community 27 - "Tool Layer Tests"
Cohesion: 0.16
Nodes (28): _agent(), _call(), Tests for the tool layer (rich results, hooks, progress, dynamic tools) and the…, The tool cannot finish until a subscriber has *seen* its progress report. If…, Returns a full ToolResult with structured details., RichTool, test_a_tool_can_unlock_deferred_tools(), test_activating_an_unknown_tool_is_ignored() (+20 more)

### Community 28 - "Structure Slicer & Coverage"
Cohesion: 0.11
Nodes (26): Coverage, EbookContext, Parsed EPUB: metadata, flattened blocks, and the book's own table of contents.…, Total number of blocks in the book., _compute_coverage(), _expand_pattern(), _join_blocks(), _parse_raw() (+18 more)

### Community 29 - "Scene Segmentation Agent"
Cohesion: 0.16
Nodes (27): EbookSceneSegmentationAgent, Splits a book's leaf sections into illustratable scenes using a ReAct agent.…, Return human-readable errors, or ``[]`` when ``scenes`` is a valid partition.…, validate_scenes(), _leaf(), Tests for scene segmentation: the deterministic slicer and the agent that…, Models routinely quote their integers; coerce rather than reject., One model instance spans a book, so cost must be measured per run. (+19 more)

### Community 30 - "Settings Partial Update"
Cohesion: 0.14
Nodes (28): A partial write. Omitted fields are left exactly as they were. Both dicts are…, Apply a partial update to the stored settings and return the new state., ``(model_id, api_key)`` for one agent, reading settings fresh. The key is the…, _read(), resolve_agent_runtime(), save_settings(), SettingsUpdate, Path (+20 more)

### Community 31 - "Library API Route Tests"
Cohesion: 0.17
Nodes (27): structure_path(), client(), BookRecord, fixture, MonkeyPatch, Path, TestClient, Tests for the library API's read endpoints. These cover the contract the reader… (+19 more)

### Community 32 - "Frontend API Client & Types"
Cohesion: 0.10
Nodes (25): ApiError, AgentView, BookUsage, BookUsageRow, ConnectionTest, Coverage, DailyPoint, LLMCallKind (+17 more)

### Community 33 - "Project Architecture Overview"
Cohesion: 0.10
Nodes (26): Diorama Project Overview, Deterministic EPUB Parsing, ReAct Agent Pattern, SSE Live Progress Streaming, World Models (extracted from ebooks), FastAPI app (main.py), routes/books.py, routes/usage.py (+18 more)

### Community 34 - "Tool Forward Methods"
Cohesion: 0.12
Nodes (15): _block_preview(), Any, op, Any, Build a text-only result., Build a failed result carrying ``message``., Wrap whatever a tool returned into a :class:`ToolResult`. Existing tools that…, Coerce an arbitrary tool return value into text. Strings pass through… (+7 more)

### Community 35 - "Scene Segmentation Tools"
Cohesion: 0.11
Nodes (20): RuntimeError, EbookSceneSegmentationAgent: cuts a leaf node's text into illustratable scenes.…, Numbered paragraph text within a range., Validate and accept the final scene boundaries; ends the run on success., Raised when a segmentation run ends without a valid set of scene boundaries., ReadParagraphsTool, SceneSegmentationError, SubmitScenesTool (+12 more)

### Community 36 - "Ebook Data Models"
Cohesion: 0.13
Nodes (21): Deterministic EPUB parsing and structure-tree slicing. This package has no…, Coverage, EbookStructure, BaseModel, Data models for extracted ebook structure. :class:`Block` is the coordinate…, Block-assignment statistics for a submitted structure tree. Attributes: covered…, The complete result of loading one EPUB. Attributes: title (str): Book title…, One entry from the EPUB's own (publisher-authored) table of contents.… (+13 more)

### Community 37 - "Ebook Loader Agent"
Cohesion: 0.11
Nodes (21): Compaction reserve bumped to 48k tokens, EbookLoaderAgent, UsageSink, Extracts an EPUB's hierarchical structure using a ReAct agent. Construct once…, Configure the agent used by every subsequent :meth:`load` call. Args: model…, main(), _print_tree(), StructureNode (+13 more)

### Community 38 - "Segmentation Agent Builder"
Cohesion: 0.14
Nodes (18): _build_tools(), _describe_node(), AgentEvent, StructureNode, A short human label for what is being segmented, for the opening prompt., Build the initial user message for one node's segmentation run., Mutable state shared by the tools bound to one segmentation run. Never touches…, Instantiate a fresh set of node-bound tools for one run. (+10 more)

### Community 39 - "Usage Summary Aggregation"
Cohesion: 0.14
Nodes (22): build_summary(), _by_kind(), _by_model(), _by_provider(), _daily(), _group(), GroupTotals, LLMCallRecord (+14 more)

### Community 40 - "TOC Sidebar & Structure"
Cohesion: 0.15
Nodes (20): contains(), TocRow(), TocSidebar(), byStartBlock(), countWords(), escapeRegExp(), firstSectionIndex(), headingFor() (+12 more)

### Community 41 - "LLM Call Provenance"
Cohesion: 0.13
Nodes (20): The upstream provider this stream reported, if any chunk carried one. A…, extract_provider(), extract_route(), _hidden(), new_call_id(), Any, The per-LLM-call ledger row, and the plumbing that fills one in. Diorama's cost…, Split a litellm model id into ``(route, vendor, model)``. litellm ids come in… (+12 more)

### Community 42 - "Model Catalogue Fetchers"
Cohesion: 0.16
Nodes (20): routes/settings.py, _f(), list_google_models(), list_models(), _parse_google(), _parse_openrouter(), Path, The model lists the settings UI's picker offers, one fetcher per provider.… (+12 more)

### Community 43 - "EPUB Block Parsing"
Cohesion: 0.12
Nodes (18): Block, One paragraph/heading/list-item-sized unit of extracted EPUB text. Attributes:…, _anchor_href(), _basename(), _build_toc(), _clean_text(), _first_metadata_value(), _is_document_item() (+10 more)

### Community 44 - "Scene Slicer"
Cohesion: 0.15
Nodes (19): build_scenes(), join_paragraphs(), _parse_scene(), Deterministic paragraph-boundary -> scene slicing and validation. The scene-…, Split ``text`` into the paragraphs the agent addresses by index. A plain split…, Inverse of :func:`split_paragraphs`., Coerce one submitted scene dict into ``(start, end)``. Raises: ValueError: If…, Build a :class:`SceneSegmentation` from validated boundaries. Args: scenes… (+11 more)

### Community 45 - "Prompt Caching"
Cohesion: 0.18
Nodes (18): apply_prompt_caching(), _cache_target_index(), _content_with_cache_control(), extract_cache_tokens(), _get(), _has_cacheable_text(), model_supports_explicit_cache(), Any (+10 more)

### Community 46 - "Cost Dashboard Models"
Cohesion: 0.16
Nodes (17): BookRecord, One entry on the shelf, persisted in ``library.json``., BookUsage, BookUsageRow, DailyPoint, BaseModel, Summed tokens and spend over some set of calls. ``calls`` counts every recorded…, Spend on one UTC day, for the trend chart. (+9 more)

### Community 47 - "Reader View Page"
Cohesion: 0.20
Nodes (13): clamp(), ReaderView(), resolveRestore(), getBook(), getScenes(), getStructure(), saveProgress(), api.ts (+5 more)

### Community 48 - "Shelf Book Card"
Cohesion: 0.15
Nodes (11): AlertIcon(), RetryIcon(), SparkIcon(), TrashIcon(), BookCard(), BookCover(), fallbackWash(), TraceLog() (+3 more)

### Community 49 - "System Prompts & Agent Errors"
Cohesion: 0.18
Nodes (15): System prompt(s) for the diorama ReAct agent. Deliberately generic: a basic…, _is_rate_limit_error(), _is_transient_error(), _litellm_exception_classes(), Exception, A stateful ReAct agent over diorama's :class:`LiteLLMModel`. The agent drives a…, Return the HTTP status carried by a provider exception, if any., Return ``(rate_limit_types, transient_types)`` from litellm, if importable.… (+7 more)

### Community 50 - "Cooperative Cancellation"
Cohesion: 0.12
Nodes (11): CancellationToken, Cooperative cancellation for the agent loop. A single token is created per run…, Anything the loop can poll to decide whether it should stop., Return True when the current run should stop as soon as possible., The default in-process :class:`CancellationToken` implementation., Request cancellation. Idempotent., Return True once :meth:`cancel` has been called., SimpleCancellationToken (+3 more)

### Community 51 - "Usage API Tests"
Cohesion: 0.23
Nodes (14): _call(), LLMCallRecord, TestClient, A book processed before cost tracking has an aggregate but no attribution., Never silently swallow spend, even when the shelf entry is gone., Forward-compatibility: unknown fields must not 500 the whole dashboard., _shelve(), test_a_ledger_whose_book_vanished_is_still_reported() (+6 more)

### Community 52 - "Fallback Model & Demo Tools"
Cohesion: 0.17
Nodes (8): The default when *no* provider is connected — first in registry order.…, Any, op, Return the current time at the given UTC offset. Args: utc_offset_hours (float…, Safely evaluate ``expression`` and return the numeric result. Args: expression…, Return the block at ``block_id``. Raises: ValueError: If ``block_id`` is out of…, Return blocks in ``[start, end]`` inclusive. Raises: ValueError: If the range…, ValueError

### Community 53 - "Tool Call Hooks & Weave"
Cohesion: 0.17
Nodes (8): AfterToolCall, BeforeToolCall, deque, Initialise the agent with its tool set and configuration. Args: tools…, Initialise W&B Weave tracing if a project name is given (best-effort)., Adopt an existing session's history, or start a fresh one., Pop queued messages according to ``queue_mode``., QueueMode

### Community 54 - "LLM Call Record Emission"
Cohesion: 0.23
Nodes (8): BaseException, LLMCallRecord, Fold one call's usage into the cumulative totals; return that call's slice.…, Emit a ledger row for a call that failed, with no usage to account for. A…, The run-level and model-level fields shared by every emitted record., Hand a finished record to the sink, if one is installed. Deliberately swallows…, The current UTC time as an ISO-8601 string, matching the rest of the store., utc_now_iso()

### Community 55 - "Structure-wide Segmentation"
Cohesion: 0.18
Nodes (9): Any, BookScenes, EbookStructure, op, Render paragraphs as ``[P n] text`` lines, optionally truncated. Args:…, Segment every leaf of an extracted structure, in reading order. One agent run…, render_paragraphs(), test_render_paragraphs_numbers_from_the_given_offset() (+1 more)

### Community 56 - "Steering & Follow-up Queues"
Cohesion: 0.15
Nodes (7): Total number of queued messages., Build a user message., Queue a user message to be injected at the next turn boundary., Queue a raw message to be injected at the next turn boundary., Queue a user message to run once the agent would otherwise settle., Queue a raw message to run once the agent would otherwise settle., _user_message()

### Community 57 - "Console Rendering"
Cohesion: 0.17
Nodes (8): AgentEvent, Any, Print a streamed delta, keeping thinking visually distinct from the answer., Close out a finished assistant message., Truncate a value's string form for compact console logging., Initialise the renderer, creating a default console when none is given., Render one event. Safe to register via ``ReactAgent.subscribe``., short_text()

### Community 58 - "Tool Image Follow-up"
Cohesion: 0.17
Nodes (8): image_followup_message(), The image blocks, in order., Return the text the ``role: tool`` message should carry. Image-only results…, Build the user message that carries a tool's images to the model. The Chat…, Return the block as a ``data:`` URI suitable for an ``image_url`` part., The full outcome of one tool call. Attributes: content (list[ContentBlock]):…, The concatenated text blocks — what the model actually sees., ToolResult

### Community 59 - "LiteLLM Model Wrapper"
Cohesion: 0.24
Nodes (10): _extract_actual_cost(), _extract_reasoning_tokens(), _extract_token_counts(), Any, litellm-backed model wrapper. Diorama talks to every model through litellm…, OpenRouter's real per-request USD cost when usage accounting is on, else None., Return (prompt_tokens, completion_tokens) from a litellm usage object/dict., Extract a field from a usage object or dict, returning None if absent. Args:… (+2 more)

### Community 60 - "FastAPI App & Usage Routes"
Cohesion: 0.18
Nodes (9): BookUsage, health(), FastAPI app for the Diorama library: list/upload books, stream agent traces.…, get_book_usage(), get_usage_summary(), Cost-tracking endpoints: the dashboard overview and one book's call-level…, Totals, per-model/provider/agent breakdowns, the daily trend, and book rows. An…, One book's cost page: its runs, its breakdowns, and every LLM call it made.… (+1 more)

### Community 61 - "Ebook Loader Run Lifecycle"
Cohesion: 0.25
Nodes (7): AgentEvent, EbookStructure, Path, Parse ``epub_path`` and assemble the fresh agent/state/tools for one run., Resolve a finished run's state into its structure, or raise., Parse ``epub_path`` and run the agent to extract its structure. Args: epub_path…, Like :meth:`load`, but exposes the run's live events instead of blocking.…

### Community 62 - "Book Run Subscribers"
Cohesion: 0.20
Nodes (6): _BookRun, Shared state for one book's in-flight (or finished) processing run., Two-phase processing (loader then scene segmenter), Register an event listener and return a function that unsubscribes it. The…, EventListener, Queue

### Community 63 - "Test Model Fakes"
Cohesion: 0.27
Nodes (9): SimpleNamespace, chunk(), Shared fakes for agent tests. Everything here mimics the small slice of litellm…, Build a streaming chunk stand-in carrying a text or reasoning delta., Return a factory producing an async generator over ``chunks``. When…, stream_of(), test_stream_failing_before_any_output_is_retried(), test_stream_failing_mid_output_is_not_retried() (+1 more)

### Community 64 - "Library JSON Store"
Cohesion: 0.43
Nodes (7): delete_book(), list_books(), BookRecord, JSON-backed library store. A single-user shelf doesn't need a database:…, Remove a book from the shelf along with its upload, structure, cover and…, _read_all(), _write_all()

### Community 65 - "Usage Sink Emission"
Cohesion: 0.25
Nodes (3): Any, Exception, Build and deliver one ledger row, mirroring ``LiteLLMModel._emit``.

### Community 66 - "Event-to-Trace Mapping"
Cohesion: 0.33
Nodes (7): _describe_call(), event_to_trace_line(), AgentEvent, Any, TraceLine, Return the trace line ``event`` produces, or None when it's not shown. Most…, _truncate()

### Community 67 - "Root Layout & Theme"
Cohesion: 0.33
Nodes (4): inter, metadata, newsreader, ThemeProvider()

### Community 68 - "Theme Toggle & Media Query"
Cohesion: 0.43
Nodes (5): MoonIcon(), SunIcon(), ThemeToggle(), noopSubscribe(), useMounted()

### Community 69 - "Ebook Loader Errors"
Cohesion: 0.40
Nodes (4): EbookLoaderError, RuntimeError, Raised when a :meth:`EbookLoaderAgent.load` run ends without a valid structure., Concrete agents built on top of diorama.core's ReAct agent framework.

### Community 71 - "Settings Provider Migration"
Cohesion: 0.50
Nodes (3): Any, Fold the pre-multi-provider shape into ``api_keys``. Before Google AI Studio…, model_validator

### Community 73 - "Test Retry Backoff Fixture"
Cohesion: 0.67
Nodes (3): instant_backoff(), fixture, Make retry backoff instant so tests do not sleep for real.

## Ambiguous Edges - Review These
- `Diorama Project Overview` → `create-next-app boilerplate README`  [AMBIGUOUS]
  frontend/README.md · relation: references

## Knowledge Gaps
- **89 isolated node(s):** `eslintConfig`, `nextConfig`, `name`, `version`, `private` (+84 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **14 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **What is the exact relationship between `Diorama Project Overview` and `create-next-app boilerplate README`?**
  _Edge tagged AMBIGUOUS (relation: references) - confidence is low._
- **Why does `ReactAgent` connect `Core Agent Loop & Tracing` to `Agent Event Trace Translation`, `ReAct Agent Turn Execution`, `Context Compaction`, `Session Store`, `Model Pricing Tables`, `Agent Loop Test Fakes`, `Final Answer Tool`, `Ebook Loader Tools`, `Demo Tools & Agent Tests`, `Tool Result Blocks & Flaky Model`, `Tool Layer Tests`, `Scene Segmentation Agent`, `Project Architecture Overview`, `Scene Segmentation Tools`, `Ebook Loader Agent`, `Segmentation Agent Builder`, `System Prompts & Agent Errors`, `Cooperative Cancellation`, `Tool Call Hooks & Weave`, `Steering & Follow-up Queues`, `Tool Image Follow-up`, `Ebook Loader Run Lifecycle`, `Book Run Subscribers`, `Ebook Loader Errors`?**
  _High betweenness centrality (0.129) - this node is a cross-community bridge._
- **Why does `LiteLLMModel` connect `Ebook Loader Tools` to `Agent Event Trace Translation`, `Core Agent Loop & Tracing`, `Model Pricing Tables`, `Backend Pydantic Models`, `Cost Estimation Pricing`, `Scene Segmentation Agent`, `Project Architecture Overview`, `Scene Segmentation Tools`, `Ebook Loader Agent`, `Segmentation Agent Builder`, `Prompt Caching`, `Cost Dashboard Models`, `System Prompts & Agent Errors`, `Tool Call Hooks & Weave`, `LLM Call Record Emission`, `LiteLLM Model Wrapper`, `Ebook Loader Run Lifecycle`, `Ebook Loader Errors`, `LiteLLM Completion Call`?**
  _High betweenness centrality (0.099) - this node is a cross-community bridge._
- **Why does `routes/books.py` connect `Project Architecture Overview` to `Shelf Page & Icons`, `Book Run Subscribers`?**
  _High betweenness centrality (0.095) - this node is a cross-community bridge._
- **Are the 13 inferred relationships involving `FakeModel` (e.g. with `LLMCallRecord` and `HookTool`) actually correct?**
  _`FakeModel` has 13 INFERRED edges - model-reasoned connections that need verification._
- **Are the 36 inferred relationships involving `ReactAgent` (e.g. with `EbookLoaderAgent` and `EbookLoaderError`) actually correct?**
  _`ReactAgent` has 36 INFERRED edges - model-reasoned connections that need verification._
- **Are the 25 inferred relationships involving `Tool` (e.g. with `EbookLoaderAgent` and `EbookLoaderError`) actually correct?**
  _`Tool` has 25 INFERRED edges - model-reasoned connections that need verification._