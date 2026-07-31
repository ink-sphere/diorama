# Graph Report - .  (2026-08-01)

## Corpus Check
- 118 files · ~121,543 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 2282 nodes · 6275 edges · 94 communities (79 shown, 15 thin omitted)
- Extraction: 89% EXTRACTED · 11% INFERRED · 0% AMBIGUOUS · INFERRED: 700 edges (avg confidence: 0.52)
- Token cost: 0 input · 139,554 output

## Community Hubs (Navigation)
- Books API Routes
- Research Artifact Validation
- Settings Resolution Core
- Agent Loop Test Fakes
- Book Reading Tools
- Streaming Test Fakes
- Settings Route Tests
- Model Catalogue & Connection Test
- EPUB Parser Core
- Agent Event Types & Trace
- ReactAgent Loop Safety
- Research Run Lifecycle
- Session Store (JSONL)
- Assistant Message Helpers
- Shelf Icons & Book Card
- Frontend Icon Library
- Backend Pydantic Models
- Context Compaction
- Cost Dashboard Pages
- Reader Page & View
- Settings Page & Model Picker
- Research Run Orchestration
- Frontend Research API Client
- LLM Call Cost Extraction
- Scene Segmentation State
- Frontend Dependencies (package.json)
- Literary Research Agent Core
- Ebook Loader Agent Core
- LLM Provider/Route Extraction
- Google/Gemini Pricing Table
- Demo Tools & Streaming Fakes
- EPUB Parsing & Cost Stamping
- Settings Update & Runtime Resolution
- Per-Call Cost Pricing
- Scene Segmentation Module
- Literary Research Validation Tools
- Context Usage Estimation
- ViewImageTool & Tests
- FastAPI App & Route Tests
- TypeScript Config
- Structure Slicer & Tests
- Ebook Package Init & Models
- Library Page & Icons
- Usage Ledger Aggregation
- Usage Ledger File I/O
- Scene Processing Backend & Tests
- Scene Segmentation Entry Points
- Ebook Loader Tools & State
- ReactAgent Hooks & Queues
- Book Usage Test Fixtures
- Loader Tool Forward Methods
- Scene Paragraph Reading & Validation
- Usage Dashboard Models
- WebSearchTool Provider Resolution
- Segmentation Agent Run-ID Tests
- V0 Roadmap Vision & Agents
- Common Tools & Results Shapes
- Tool Approval & Rendering
- ToolResult Coercion
- WebSearchTool Request Building
- EPUB Cover Extraction
- Cancellation Token Mechanics
- Demo Tools AST/Tracing
- Book Run Reset & Delete
- Cancellation Token Module
- BookScenes & Scene Model
- Usage Dashboard Routes
- Model Usage Recording
- Research Test Fixtures
- Root Layout & Fonts
- Provider Display Name
- Stub Loader Test Double
- Settings PUT Route
- RunLog Subscriber Queue
- Legacy Settings Migration
- Compaction Threshold Calc
- Test Key Isolation Fixture
- Test Retry Backoff Fixture
- Frontend AGENTS.md Warning
- ESLint Config
- Next.js Config
- PostCSS Config
- Test Package Init
- Next.js File Icon Asset
- Next.js Globe Icon Asset
- Next.js Logo Asset
- Vercel Logo Asset
- Next.js Window Icon Asset
- Frontend Boilerplate README
- Repo Root Doc
- Root README Tagline

## God Nodes (most connected - your core abstractions)
1. `FakeModel` - 123 edges
2. `ReactAgent` - 121 edges
3. `response()` - 98 edges
4. `Tool` - 81 edges
5. `LiteLLMModel` - 73 edges
6. `ToolResult` - 72 edges
7. `ContextCompactor` - 63 edges
8. `EbookContext` - 62 edges
9. `WebSearchTool` - 57 edges
10. `ToolParameter` - 52 edges

## Surprising Connections (you probably didn't know these)
- `World model / world state (per-scene continuity state)` --semantically_similar_to--> `WorldDossier`  [INFERRED] [semantically similar]
  docs/00_rough_roadmap.md → diorama/agents/literary_research_agent.py
- `Location registry (V0 roadmap term)` --conceptually_related_to--> `WorldDossier`  [INFERRED]
  docs/00_rough_roadmap.md → diorama/agents/literary_research_agent.py
- `Partial results are kept and rendered` --rationale_for--> `LiteraryResearchError`  [EXTRACTED]
  docs/01_literary_research_agent.md → diorama/agents/literary_research_agent.py
- `Completion guard & empty-reply guard (turn-termination safety)` --conceptually_related_to--> `LiteraryResearchAgent`  [EXTRACTED]
  CLAUDE.md → diorama/agents/literary_research_agent.py
- `Diorama (ebook world-model reader project)` --references--> `EbookSceneSegmentationAgent`  [EXTRACTED]
  CLAUDE.md → diorama/agents/ebook_scene_segmentation.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **The three staged literary-research artifacts (author profile, world dossier, style bibles)** — diorama_agents_literary_research_agent_literaryresearchagent, diorama_agents_literary_research_agent_authorprofile, diorama_agents_literary_research_agent_worlddossier, diorama_agents_literary_research_agent_stylebiblecandidates [EXTRACTED 0.95]
- **Backend components implementing the lazy research run lifecycle** — diorama_backend_research, diorama_backend_runs_runlog, diorama_backend_routes_books, diorama_backend_research_researchrecord, diorama_backend_trace [EXTRACTED 0.90]
- **The five agents envisioned in the V0 roadmap's illustration pipeline (only one implemented)** — diorama_agents_literary_research_agent_literaryresearchagent, docs_00_rough_roadmap_castingdirectoragent, docs_00_rough_roadmap_artdirectoragent, docs_00_rough_roadmap_makeupartistagent, docs_00_rough_roadmap_worldmodeldirectoragent [INFERRED 0.85]

## Communities (94 total, 15 thin omitted)

### Community 0 - "Books API Routes"
Cohesion: 0.07
Nodes (67): BookRecord, One entry on the shelf, persisted in ``library.json``., get_book_record(), get_cover(), get_library(), get_scenes(), get_structure(), BookRecord (+59 more)

### Community 1 - "Research Artifact Validation"
Cohesion: 0.06
Nodes (64): _cited_block_ids(), coverage_warnings(), Check a submitted author profile, returning human-readable errors. Args:…, Check a submitted world dossier, returning human-readable errors. Beyond shape…, Every in-range block id the dossier cites, across all three registries., Advisory notes about how much of the book a dossier's evidence covers.…, Check the submitted style-bible candidates. Args: original (Any): The required…, validate_author_profile() (+56 more)

### Community 2 - "Settings Resolution Core"
Cohesion: 0.06
Nodes (63): get_settings(), AgentConfig, AgentDefinition, AgentView, build_view(), DioramaSettings, load_settings(), mask_key() (+55 more)

### Community 3 - "Agent Loop Test Fakes"
Cohesion: 0.09
Nodes (60): FakeModel, Build a non-streaming litellm-style ``ModelResponse`` stand-in., Drop-in stand-in for ``LiteLLMModel`` driven by a scripted response list. Each…, response(), _agent(), _bulky_history(), _hook_call(), HookTool (+52 more)

### Community 4 - "Book Reading Tools"
Cohesion: 0.09
Nodes (52): Mechanical rejection of rendering vocabulary keeps WorldDossier style-free, GetOverviewTool, GetTocTool, Orientation info about the book: title, author, block count, previews., The EPUB's own table of contents, block-anchored where possible., Substring or regex search over block text., SearchBlocksTool, AuthorProfile (+44 more)

### Community 5 - "Streaming Test Fakes"
Cohesion: 0.09
Nodes (51): SimpleNamespace, chunk(), FlakyModel, Shared fakes for agent tests. Everything here mimics the small slice of litellm…, Returns a streaming generator per scripted entry. Each entry is a factory (see…, Raises ``error`` for the first ``failures`` calls, then behaves normally., Build a streaming chunk stand-in carrying a text or reasoning delta., Return a factory producing an async generator over ``chunks``. When… (+43 more)

### Community 6 - "Settings Route Tests"
Cohesion: 0.09
Nodes (53): client(), _openrouter_model(), fixture, MonkeyPatch, TestClient, Tests for per-agent model settings and provider credentials. Fully offline: the…, The research agent looks at illustrations; a text-only model can't., Warning on absent evidence would cry wolf on every under-described model. (+45 more)

### Community 7 - "Model Catalogue & Connection Test"
Cohesion: 0.08
Nodes (50): ConnectionTest, _catalogue(), CatalogueEntry, CatalogueStatus, _entry(), get_models(), _maybe_float(), _model_warnings() (+42 more)

### Community 8 - "EPUB Parser Core"
Cohesion: 0.06
Nodes (44): Coverage, Block, One paragraph/heading/list-item-sized unit of extracted EPUB text. Attributes:…, _anchor_href(), _basename(), _build_toc(), EbookContext, _first_metadata_value() (+36 more)

### Community 9 - "Agent Event Types & Trace"
Cohesion: 0.14
Nodes (46): Translate ReactAgent's typed events into shelf-card trace lines. The agent loop…, AgentEndEvent, AgentStartEvent, CompactionEndEvent, CompactionStartEvent, MessageEndEvent, MessageStartEvent, MessageUpdateEvent (+38 more)

### Community 10 - "ReactAgent Loop Safety"
Cohesion: 0.05
Nodes (30): Completion guard & empty-reply guard (turn-termination safety), Signed thinking_blocks must be stored and replayed verbatim, Guard against re-entrant runs. Raises: RuntimeError: If a run is already in…, Return True when ``max_iterations`` has been consumed., Ask the completion guard whether the run may settle. Returns: str | None: A…, Answer every tool call that never received a result. A cancelled (or crashed)…, Total number of queued messages., Build a user message. (+22 more)

### Community 11 - "Research Run Lifecycle"
Cohesion: 0.13
Nodes (44): The stored record for ``book_id``, or None when nobody has researched it. A…, Research one book start to finish, publishing every move as a trace line., read_record(), _run_research(), TraceLine, One run's accumulated trace, plus the queues currently watching it., RunLog, _author() (+36 more)

### Community 12 - "Session Store (JSONL)"
Cohesion: 0.07
Nodes (35): _apply_compaction(), entries_by_id(), JsonlSessionStore, _new_id(), path_to_entry(), Any, BaseModel, Path (+27 more)

### Community 13 - "Assistant Message Helpers"
Cohesion: 0.07
Nodes (29): _assistant_message(), _is_empty_reply(), _last_tool_text(), _maybe_await(), AgentEvent, Any, Add a message to history and record it in the session, if any., Append a message and return its start/end events. (+21 more)

### Community 14 - "Shelf Icons & Book Card"
Cohesion: 0.06
Nodes (23): AlertIcon(), CheckIcon(), RetryIcon(), SparkIcon(), TrashIcon(), BookCard(), BookCover(), fallbackWash() (+15 more)

### Community 15 - "Frontend Icon Library"
Cohesion: 0.08
Nodes (31): ChevronLeftIcon(), ChevronRightIcon(), ContentsIcon(), IconProps, ImageIcon(), MoonIcon(), PaletteIcon(), SunIcon() (+23 more)

### Community 16 - "Backend Pydantic Models"
Cohesion: 0.07
Nodes (38): UsageSink, Configure the agent used by every subsequent segmentation call. Args: model…, Coverage, BaseModel, Pydantic models for the library API. ``BookRecord`` is the durable shape…, Where the reader left off in a book. ``section_index`` indexes the book's leaf…, One line of the live agent trace, streamed to the shelf card.…, ReadingProgress (+30 more)

### Community 17 - "Context Compaction"
Cohesion: 0.08
Nodes (36): build_compaction_prompt(), CompactionResult, estimate_content_tokens(), estimate_message_tokens(), estimate_text_tokens(), estimate_tool_tokens(), Any, Context accounting and automatic history compaction. The agent's message list… (+28 more)

### Community 18 - "Cost Dashboard Pages"
Cohesion: 0.13
Nodes (27): metadata, BookCostView(), CallDetail(), CallRow(), pricingLabel(), AXIS_STYLE, BreakdownChart(), DailySpendChart() (+19 more)

### Community 19 - "Reader Page & View"
Cohesion: 0.10
Nodes (33): clamp(), ReaderView(), resolveRestore(), contains(), TocRow(), TocSidebar(), getBook(), getScenes() (+25 more)

### Community 20 - "Settings Page & Model Picker"
Cohesion: 0.07
Nodes (27): metadata, ChevronDownIcon(), KeyIcon(), formatContext(), ModelPicker(), normalizeModelId(), perMillion(), PriceTag() (+19 more)

### Community 21 - "Research Run Orchestration"
Cohesion: 0.06
Nodes (38): _build_search_tool(), ensure_started(), is_running(), _line(), _load_structure(), _now_iso(), AuthorProfile, BaseModel (+30 more)

### Community 22 - "Frontend Research API Client"
Cohesion: 0.08
Nodes (36): Watching is detached from running (closing modal doesn't cancel), ApiError, chooseStyleDirection(), getResearch(), researchStreamUrl(), retryResearch(), AgentView, BookUsage (+28 more)

### Community 23 - "LLM Call Cost Extraction"
Cohesion: 0.08
Nodes (33): BaseException, _extract_actual_cost(), _extract_reasoning_tokens(), _extract_token_counts(), Any, LLMCallRecord, op, OpenRouter's real per-request USD cost when usage accounting is on, else None. (+25 more)

### Community 24 - "Scene Segmentation State"
Cohesion: 0.07
Nodes (31): _build_tools(), RuntimeError, Mutable state shared by the tools bound to one segmentation run. Never touches…, Validate and accept the final scene boundaries; ends the run on success., Instantiate a fresh set of node-bound tools for one run., Resolve a finished run's state into its segmentation, or raise., Raised when a segmentation run ends without a valid set of scene boundaries., SceneSegmentationError (+23 more)

### Community 25 - "Frontend Dependencies (package.json)"
Cohesion: 0.05
Nodes (38): eslint, eslint-config-next, framer-motion, dependencies, framer-motion, next, next-themes, react (+30 more)

### Community 26 - "Literary Research Agent Core"
Cohesion: 0.08
Nodes (30): _build_tools(), LiteraryResearchReport, AgentEvent, EbookStructure, Path, Mutable state shared by the tools bound to one research run. Exposes…, Whether all three artifacts have been accepted., Names of the artifacts still outstanding, in submission order. (+22 more)

### Community 27 - "Ebook Loader Agent Core"
Cohesion: 0.08
Nodes (28): Diorama (ebook world-model reader project), EbookLoaderAgent, EbookLoaderError, AgentEvent, EbookStructure, Path, RuntimeError, UsageSink (+20 more)

### Community 28 - "LLM Provider/Route Extraction"
Cohesion: 0.08
Nodes (33): The upstream provider this stream reported, if any chunk carried one. A…, extract_provider(), extract_route(), _hidden(), Any, Split a litellm model id into ``(route, vendor, model)``. litellm ids come in…, Read ``key`` out of a litellm response's ``_hidden_params``, if present., The API Diorama called and is billed by, e.g. ``"openrouter"``. Prefers… (+25 more)

### Community 29 - "Google/Gemini Pricing Table"
Cohesion: 0.08
Nodes (30): No global 'current provider' — a model id names its own provider, _family(), get_pricing(), is_priced(), Hand-maintained per-token rates for Google AI Studio (Gemini) models. Every…, The table key covering ``model_id``, matching the longest family prefix., Per-token rates for a Gemini model, or None if this table doesn't cover it.…, Whether this table covers ``model_id`` (used to label the picker honestly). (+22 more)

### Community 30 - "Demo Tools & Streaming Fakes"
Cohesion: 0.13
Nodes (31): CalculatorTool, CurrentTimeTool, Evaluate a basic arithmetic expression (``+ - * / // % **`` and parentheses)., Return the current date and time (UTC by default) as an ISO-8601 string., A model whose ``acompletion`` always returns a streaming generator., Build a non-streaming tool-call object shaped like litellm's., StreamModel, tool_call() (+23 more)

### Community 31 - "EPUB Parsing & Cost Stamping"
Cohesion: 0.08
Nodes (33): _clean_text(), Path, Parse an EPUB into blocks and a block-anchored table of contents., Collapse runs of whitespace and strip the result., _full_coverage_call(), The loader stamps its ledger rows so the dashboard can attribute the spend., Cost tracking is opt-in; a script or test that doesn't want it gets no ledger., test_load_happy_path_single_submit() (+25 more)

### Community 32 - "Settings Update & Runtime Resolution"
Cohesion: 0.12
Nodes (33): A partial write. Omitted fields are left exactly as they were. Both dicts are…, Apply a partial update to the stored settings and return the new state., ``(model_id, api_key)`` for one agent, reading settings fresh. The key is the…, resolve_agent_runtime(), save_settings(), SettingsUpdate, Path, Sparse writes: the form only ever held a mask, so absent means unchanged. (+25 more)

### Community 33 - "Per-Call Cost Pricing"
Cohesion: 0.08
Nodes (23): Best-effort USD cost for a call; returns 0.0 if litellm can't price it. Tries…, Estimate (total_cost, cost_by_type, pricing_source) for one call. Rate tables…, cost_model_candidates(), _f(), ModelPricing, normalize_model_id(), _parse_pricing(), PricingTable (+15 more)

### Community 34 - "Scene Segmentation Module"
Cohesion: 0.11
Nodes (28): EbookSceneSegmentationAgent: cuts a leaf node's text into illustratable scenes.…, The default when *no* provider is connected — first in registry order.…, build_scenes(), iter_leaves(), join_paragraphs(), _parse_scene(), EbookStructure, StructureNode (+20 more)

### Community 35 - "Literary Research Validation Tools"
Cohesion: 0.10
Nodes (26): _accepted(), _check_evidence(), _missing_prose(), Any, op, LiteraryResearchAgent: the pre-production research pass over a book. Where…, Build the schema for one style-bible candidate slot., Build the standard 'fix these and resubmit' failure result. (+18 more)

### Community 36 - "Context Usage Estimation"
Cohesion: 0.09
Nodes (27): ContextUsageEstimate, estimate_context_tokens(), estimate_context_usage(), Deterministic context-size accounting for one provider request. Attributes:…, Return deterministic context accounting for a would-be provider request. Args:…, Return the estimated total context size for a would-be provider request., Return True when the next request would exceed the compaction threshold., System prompt(s) for the diorama ReAct agent. Deliberately generic: a basic… (+19 more)

### Community 37 - "ViewImageTool & Tests"
Cohesion: 0.16
Nodes (29): AsyncClient, Fetch an image from a url so the model can actually look at it. The image…, ViewImageTool, _client(), _image_handler(), parametrize, Tests for the tools in diorama.core.common_tools. No network: every request is…, A url ending .png that serves jpeg must be tagged as what it really is. (+21 more)

### Community 38 - "FastAPI App & Route Tests"
Cohesion: 0.15
Nodes (28): health(), FastAPI app for the Diorama library: list/upload books, stream agent traces.…, client(), BookRecord, fixture, MonkeyPatch, Path, TestClient (+20 more)

### Community 39 - "TypeScript Config"
Cohesion: 0.07
Nodes (28): compilerOptions, allowJs, esModuleInterop, incremental, isolatedModules, jsx, lib, module (+20 more)

### Community 40 - "Structure Slicer & Tests"
Cohesion: 0.17
Nodes (27): build_structure(), EbookStructure, Return human-readable errors, or ``[]`` when the tree is valid and coverable., Build the final :class:`EbookStructure` from a validated tree. Raises:…, validate_tree(), _make_context(), Tests for diorama.ebook: EPUB parsing and structure-tree slicing. Fully offline…, Build a synthetic EbookContext from (text, tag) pairs, bypassing EPUB parsing. (+19 more)

### Community 41 - "Ebook Package Init & Models"
Cohesion: 0.12
Nodes (23): Deterministic EPUB parsing and structure-tree slicing. This package has no…, Coverage, EbookStructure, BaseModel, Data models for extracted ebook structure. :class:`Block` is the coordinate…, Block-assignment statistics for a submitted structure tree. Attributes: covered…, The complete result of loading one EPUB. Attributes: title (str): Book title…, One entry from the EPUB's own (publisher-authored) table of contents.… (+15 more)

### Community 42 - "Library Page & Icons"
Cohesion: 0.11
Nodes (20): BookIcon(), CostsIcon(), PlusIcon(), SearchIcon(), SettingsIcon(), UploadIcon(), LibraryView(), FILTERS (+12 more)

### Community 43 - "Usage Ledger Aggregation"
Cohesion: 0.16
Nodes (25): Append-only per-book usage ledger, build_summary(), _by_kind(), _by_model(), _by_provider(), _daily(), _group(), GroupTotals (+17 more)

### Community 44 - "Usage Ledger File I/O"
Cohesion: 0.14
Nodes (26): append_call(), ledger_book_ids(), Path, Book ids that have a ledger on disk., The ledger file for ``book_id`` (not guaranteed to exist)., Append one call record to ``book_id``'s ledger., Every recorded call for ``book_id``, in the order they were made. Unparseable…, read_calls() (+18 more)

### Community 45 - "Scene Processing Backend & Tests"
Cohesion: 0.19
Nodes (23): _BookRun, BookScenes, EbookStructure, Segment every leaf section into scenes, publishing one live progress row. Runs…, _segment_scenes(), _leaf(), _progress(), EbookStructure (+15 more)

### Community 46 - "Scene Segmentation Entry Points"
Cohesion: 0.12
Nodes (18): _describe_node(), AgentEvent, Any, BookScenes, EbookStructure, op, StructureNode, A short human label for what is being segmented, for the opening prompt. (+10 more)

### Community 47 - "Ebook Loader Tools & State"
Cohesion: 0.18
Nodes (22): _build_tools(), ListHeadingsTool, _LoadState, EbookLoaderAgent: extracts an EPUB's hierarchical structure via a ReAct agent.…, Build the initial user message for a ``load()`` run., Mutable state shared by the tools bound to one ``load()`` call. Never touches…, Every block the parser detected as a heading (h1-h6)., Raw text of a block range, each block id-prefixed. (+14 more)

### Community 48 - "ReactAgent Hooks & Queues"
Cohesion: 0.10
Nodes (15): AfterToolCall, BeforeToolCall, Deferred tool discovery (active=False until unlocked by another tool), CompletionGuard, deque, Pop queued messages according to ``queue_mode``., Initialise the agent with its tool set and configuration. Args: tools…, Initialise W&B Weave tracing if a project name is given (best-effort). (+7 more)

### Community 49 - "Book Usage Test Fixtures"
Cohesion: 0.12
Nodes (21): build_book_usage(), BookRecord, One book's full cost page, or None when it has no ledger., client(), ledger(), fixture, MonkeyPatch, Path (+13 more)

### Community 50 - "Loader Tool Forward Methods"
Cohesion: 0.17
Nodes (6): _block_preview(), Any, op, Build a text-only result., Build a failed result carrying ``message``., Any

### Community 51 - "Scene Paragraph Reading & Validation"
Cohesion: 0.16
Nodes (19): Render paragraphs as ``[P n] text`` lines, optionally truncated. Args:…, Numbered paragraph text within a range., ReadParagraphsTool, render_paragraphs(), Return human-readable errors, or ``[]`` when ``scenes`` is a valid partition.…, validate_scenes(), Tests for scene segmentation: the deterministic slicer and the agent that…, Models routinely quote their integers; coerce rather than reject. (+11 more)

### Community 52 - "Usage Dashboard Models"
Cohesion: 0.14
Nodes (17): BookUsage, BookUsageRow, DailyPoint, BaseModel, Summed tokens and spend over some set of calls. ``calls`` counts every recorded…, Spend on one UTC day, for the trend chart., One book's line on the dashboard overview. ``title`` and ``status`` are joined…, Everything the ``/costs`` overview renders. (+9 more)

### Community 53 - "WebSearchTool Provider Resolution"
Cohesion: 0.12
Nodes (16): model_validator, Search the web via Exa or Tavily, normalising both into one result shape. Each…, WebSearchTool, Web search settings card (Exa/Tavily keys), A forced provider does not silently fall back to the other one., A key exported after the tool was built is still honoured., test_a_bare_api_key_is_rejected_because_it_names_no_api(), test_exa_wins_auto_detection_when_both_keys_are_present() (+8 more)

### Community 54 - "Segmentation Agent Run-ID Tests"
Cohesion: 0.25
Nodes (16): EbookSceneSegmentationAgent, Splits a book's leaf sections into illustratable scenes using a ReAct agent.…, _leaf(), One model instance spans a book, so cost must be measured per run., _submit(), test_each_agent_gets_its_own_run_id_when_none_is_supplied(), test_min_paragraphs_zero_always_runs_the_agent(), test_per_node_cost_is_a_difference_not_the_shared_models_running_total() (+8 more)

### Community 55 - "V0 Roadmap Vision & Agents"
Cohesion: 0.17
Nodes (16): ArtDirectorAgent (proposed, unimplemented), Cast book (characters x looks on the story timeline), CastingDirectorAgent (proposed, unimplemented), Diorama vision: simulated 'movie in the head' world model, Fable (V0 feedback author), Image model choice (open question: Gemini / GPT-image / Flux Kontext), Location registry (V0 roadmap term), Looks-library consistency mechanism (fixed pre-generated character x look references) (+8 more)

### Community 56 - "Common Tools & Results Shapes"
Cohesion: 0.17
Nodes (10): Common, book-independent tools shared across diorama agents. Two residents so…, ImageBlock, BaseModel, Rich tool results. A tool's return value used to be flattened to a string,…, The image blocks, in order., A run of text produced by a tool., An image produced by a tool, carried as base64. Attributes: data (str):…, Return the block as a ``data:`` URI suitable for an ``image_url`` part. (+2 more)

### Community 57 - "Tool Approval & Rendering"
Cohesion: 0.14
Nodes (9): Decide whether a tool requiring approval may run. Resolution order: explicit…, AgentEvent, Any, Print a streamed delta, keeping thinking visually distinct from the answer., Close out a finished assistant message., Truncate a value's string form for compact console logging., Initialise the renderer, creating a default console when none is given., Render one event. Safe to register via ``ReactAgent.subscribe``. (+1 more)

### Community 58 - "ToolResult Coercion"
Cohesion: 0.16
Nodes (10): Any, Wrap whatever a tool returned into a :class:`ToolResult`. Existing tools that…, Coerce an arbitrary tool return value into text. Strings pass through…, stringify(), Any, Execute ``tool_name`` and return its normalised result. A tool may return a…, Look up a tool by name, whether active or deferred., Return the *active* tool schemas in OpenAI function-calling format. (+2 more)

### Community 59 - "WebSearchTool Request Building"
Cohesion: 0.22
Nodes (8): Any, op, Resolve which provider this tool would call, and with what key. Resolved at…, Build the (payload, headers) pair for one provider's search call., Map a provider response onto the common result shape., The over-budget message, phrased so the model knows what to do next., _too_large(), SearchProviderId

### Community 60 - "EPUB Cover Extraction"
Cohesion: 0.28
Nodes (12): _as_image(), extract_cover(), _from_filename(), _from_manifest_properties(), _from_opf_meta(), Any, Path, Best-effort cover-image extraction from an EPUB. EPUBs declare their cover in… (+4 more)

### Community 61 - "Cancellation Token Mechanics"
Cohesion: 0.17
Nodes (9): CancellationToken, Anything the loop can poll to decide whether it should stop., Return True when the current run should stop as soon as possible., Run a tool, surfacing its ``on_update`` reports as events while it works. The…, Sleep in short steps, returning False as soon as cancellation is requested., Request cancellation of the running agent (no-op when idle). The run stops at…, _sleep_unless_cancelled(), Stop research affordance (deferred follow-up) (+1 more)

### Community 62 - "Demo Tools AST/Tracing"
Cohesion: 0.22
Nodes (8): AST, Any, op, Small, dependency-free demo tools. These exist so the ReAct loop is runnable…, Return the current time at the given UTC offset. Args: utc_offset_hours (float…, Recursively evaluate a parsed arithmetic AST, rejecting anything unsafe. Args:…, Safely evaluate ``expression`` and return the numeric result. Args: expression…, _safe_eval()

### Community 63 - "Book Run Reset & Delete"
Cohesion: 0.22
Nodes (7): delete, Drop any finished/failed run so the next ``ensure_started`` starts fresh., reset(), Drop any finished/failed run so the next ``ensure_started`` starts fresh., reset(), remove_book(), _Response

### Community 64 - "Cancellation Token Module"
Cohesion: 0.22
Nodes (5): Cooperative cancellation for the agent loop. A single token is created per run…, The default in-process :class:`CancellationToken` implementation., Request cancellation. Idempotent., Return True once :meth:`cancel` has been called., SimpleCancellationToken

### Community 65 - "BookScenes & Scene Model"
Cohesion: 0.22
Nodes (7): BookScenes, BaseModel, Total scenes across every node., One stretch of a leaf node's text that a single illustration could depict.…, How many paragraphs this scene spans., Every leaf node of one book, segmented into scenes. Attributes: title (str):…, Scene

### Community 66 - "Usage Dashboard Routes"
Cohesion: 0.25
Nodes (7): BookUsage, get_book_usage(), get_usage_summary(), Cost-tracking endpoints: the dashboard overview and one book's call-level…, Totals, per-model/provider/agent breakdowns, the daily trend, and book rows. An…, One book's cost page: its runs, its breakdowns, and every LLM call it made.…, UsageSummary

### Community 67 - "Model Usage Recording"
Cohesion: 0.25
Nodes (3): Any, Exception, Build and deliver one ledger row, mirroring ``LiteLLMModel._emit``.

### Community 68 - "Research Test Fixtures"
Cohesion: 0.32
Nodes (8): clear_runs(), data_dir(), offline_runtime(), fixture, MonkeyPatch, Never read real settings, a real API key, or a real search key., Runs are a module-level registry; a leaked one would leak across tests., stub_agent()

### Community 69 - "Root Layout & Fonts"
Cohesion: 0.33
Nodes (4): inter, metadata, newsreader, ThemeProvider()

### Community 70 - "Provider Display Name"
Cohesion: 0.40
Nodes (4): provider_label(), A display name for a provider slug (``"openai"`` → ``"OpenAI"``). OpenRouter…, The upstream provider as a display string., test_provider_label_leaves_provider_supplied_names_alone()

### Community 72 - "Settings PUT Route"
Cohesion: 0.50
Nodes (4): put_settings(), put, SettingsUpdate, SettingsView

### Community 74 - "Legacy Settings Migration"
Cohesion: 0.50
Nodes (3): Any, model_validator, Fold the pre-multi-provider shape into ``api_keys``. Before Google AI Studio…

### Community 75 - "Compaction Threshold Calc"
Cohesion: 0.50
Nodes (3): compaction_threshold(), Return the token count at which compaction should trigger., The estimated token count at which :meth:`compact` should be called.

### Community 76 - "Test Key Isolation Fixture"
Cohesion: 0.67
Nodes (3): _no_ambient_keys(), fixture, Never let a developer's real key leak into a test's provider resolution.

### Community 77 - "Test Retry Backoff Fixture"
Cohesion: 0.67
Nodes (3): instant_backoff(), fixture, Make retry backoff instant so tests do not sleep for real.

## Knowledge Gaps
- **96 isolated node(s):** `eslintConfig`, `nextConfig`, `name`, `version`, `private` (+91 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **15 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Diorama (ebook world-model reader project)` connect `Ebook Loader Agent Core` to `FastAPI App & Route Tests`, `Library Page & Icons`, `Segmentation Agent Run-ID Tests`, `V0 Roadmap Vision & Agents`?**
  _High betweenness centrality (0.147) - this node is a cross-community bridge._
- **Why does `ReactAgent` connect `ReactAgent Loop Safety` to `Agent Loop Test Fakes`, `Book Reading Tools`, `Streaming Test Fakes`, `Agent Event Types & Trace`, `Session Store (JSONL)`, `Assistant Message Helpers`, `Scene Segmentation State`, `Literary Research Agent Core`, `Ebook Loader Agent Core`, `LLM Provider/Route Extraction`, `Demo Tools & Streaming Fakes`, `Scene Segmentation Module`, `Literary Research Validation Tools`, `Context Usage Estimation`, `Scene Segmentation Entry Points`, `Ebook Loader Tools & State`, `ReactAgent Hooks & Queues`, `Scene Paragraph Reading & Validation`, `Segmentation Agent Run-ID Tests`, `Tool Approval & Rendering`, `Cancellation Token Mechanics`, `Cancellation Token Module`?**
  _High betweenness centrality (0.092) - this node is a cross-community bridge._
- **Why does `LiteraryResearchAgent` connect `Ebook Loader Agent Core` to `Research Artifact Validation`, `Settings Resolution Core`, `Literary Research Validation Tools`, `Book Reading Tools`, `ViewImageTool & Tests`, `EPUB Parser Core`, `Agent Event Types & Trace`, `ReactAgent Loop Safety`, `Research Run Lifecycle`, `Ebook Loader Tools & State`, `WebSearchTool Provider Resolution`, `Research Run Orchestration`, `V0 Roadmap Vision & Agents`, `Scene Segmentation State`, `Literary Research Agent Core`, `EPUB Parsing & Cost Stamping`?**
  _High betweenness centrality (0.080) - this node is a cross-community bridge._
- **Are the 13 inferred relationships involving `FakeModel` (e.g. with `LLMCallRecord` and `HookTool`) actually correct?**
  _`FakeModel` has 13 INFERRED edges - model-reasoned connections that need verification._
- **Are the 56 inferred relationships involving `ReactAgent` (e.g. with `EbookLoaderAgent` and `EbookLoaderError`) actually correct?**
  _`ReactAgent` has 56 INFERRED edges - model-reasoned connections that need verification._
- **Are the 46 inferred relationships involving `Tool` (e.g. with `EbookLoaderAgent` and `EbookLoaderError`) actually correct?**
  _`Tool` has 46 INFERRED edges - model-reasoned connections that need verification._
- **Are the 43 inferred relationships involving `LiteLLMModel` (e.g. with `EbookLoaderAgent` and `EbookLoaderError`) actually correct?**
  _`LiteLLMModel` has 43 INFERRED edges - model-reasoned connections that need verification._