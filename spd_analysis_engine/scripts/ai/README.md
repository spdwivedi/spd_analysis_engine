# AI Subsystem (`scripts/ai/`)

## Module Purpose
The `ai` module manages progressive code analysis, changelog generation, command explanations, and LLM orchestration. Operating entirely under the control plane, it isolates external model communication from workers, sanitizes sensitive tokens before dispatch, enforces rate limits, and caches structured outputs across four progressive analysis tiers.

---

## File Roster & Technical Reference

- **`__init__.py`**
  - Package initialization and exports for AI synthesizers and executors.

- **`cache_manager.py`**
  - **Classes:** `AICacheManager`
  - **Purpose:** File-based and database-backed cache manager storing LLM synthesis outputs under content-addressed keys.

- **`cache_queries.py`**
  - **Classes:** `CacheQueriesMixin`
  - **Purpose:** Query mixin providing fast lookups for cached burst analysis records across sessions.

- **`changelog.py`**
  - **Classes:** `ChangelogMixin`
  - **Core Functions:** `generate_session_changelog(session_id, bursts)`
  - **Purpose:** Generates conventional, markdown-formatted changelogs from captured burst histories.

- **`command_explainer.py`**
  - **Classes:** `CommandExplainerMixin`
  - **Core Functions:** `explain_command(cmd, context)`
  - **Purpose:** Generates natural language explanations for observed shell executions with local heuristic fallbacks.

- **`config.py`**
  - **Classes:** `AIConfig`
  - **Core Functions:** `load_model_config()`, `mask_model_config()`, `save_model_config()`
  - **Purpose:** Manages `models_config.json`, storing model identifiers, temperatures, and provider parameters while masking API credentials on read.

- **`controller_db.py`**
  - **Classes:** `EngineDB`
  - **Purpose:** SQLite storage backend for controller-managed AI jobs and analysis records in `engine.db`.

- **`controller_executor.py`**
  - **Classes:** `ControllerAIExecutor`
  - **Purpose:** Background worker pool executing durable AI jobs scheduled by the controller. Coordinates sanitization, provider calls, retries, and result persistence.

- **`error_handling.py`**
  - **Classes:** `AIError`, `RateLimitError`, `AuthenticationError`, `JSONParseError`, `TimeoutError`, `PermanentError`, `ConsentRequiredError`, `RetryPlan`
  - **Core Functions:** `classify_ai_exception(exc)`, `evaluate_retry_plan(error, attempt)`
  - **Purpose:** Categorizes provider failures and computes deterministic backoff retry schedules.

- **`identity.py`**
  - **Classes:** `AnalysisStatus`, `ProgressiveLevel`, `AnalysisIdentity`
  - **Core Functions:** `compute_content_hash(text)`, `compute_analysis_cache_key(...)`
  - **Purpose:** Formulates composite 8-attribute cache keys: `target_id`, `profile`, `input_hash`, `model`, `provider`, `prompt_version`, `sanitizer_version`, and `context_hash`.

- **`profiles.py`**
  - **Classes:** `ExecutionProfile`, `ProfileConfig`
  - **Core Functions:** `get_profile_config(profile_enum)`
  - **Purpose:** Defines execution profiles (`NORMAL`, `ECO`, `GPU_AI`, `OFFLINE`) controlling concurrency, Ollama `num_ctx`, and timeout limits.

- **`progressive_levels.py`**
  - **Core Functions:** `build_progressive_prompt(level, diff, prior_level_text)`, `parse_progressive_result(level, raw_json)`
  - **Purpose:** Builds prompts for the 4 progressive levels (L1 Quick, L2 Detailed, L3 Architecture, L4 Risk/Impact), ensuring higher levels build on previous outputs without repetition.

- **`prompt_builder.py`**
  - **Core Functions:** `detect_tech_stack(files)`, `generate_directory_tree(files)`, `chunk_diff(diff_text, max_tokens)`, `build_burst_prompt(...)`
  - **Purpose:** Formats context-rich LLM prompts containing directory trees, language stacks, and chunked patch diffs.

- **`provider_gemini.py`**
  - **Core Functions:** `validate_gemini_model(model)`, `call_gemini(prompt, config)`, `test_gemini_connection()`
  - **Purpose:** Google Gemini API provider adapter. Enforces header-based authentication and rate limiting.

- **`provider_ollama.py`**
  - **Core Functions:** `probe_ollama_runtime()`, `call_ollama(prompt, config)`, `test_ollama_connection()`
  - **Purpose:** Local Ollama provider adapter. Detects GPU offload status, manages `keep_alive`, and handles local inference.

- **`provider_pool.py`**
  - **Classes:** `BoundedProviderPool`
  - **Purpose:** Thread-safe provider connection pool regulating concurrent requests to avoid rate limits and memory spikes.

- **`rate_limiter.py`**
  - **Classes:** `RateLimitManager`
  - **Purpose:** Token-bucket and sliding-window rate limiters governing external API calls.

- **`sanitizer.py`**
  - **Core Functions:** `is_sensitive_file(path)`, `redact_sensitive_text(text)`, `sanitize_diff_content(diff)`, `sanitize_context_dict(ctx)`
  - **Purpose:** Pre-flight secret sanitizer that strips API keys, JWTs, AWS credentials, SSH keys, passwords, and sensitive file contents.

- **`synthesizer.py`**
  - **Classes:** `AISynthesizer`
  - **Purpose:** High-level synthesizer facade integrating cache queries, burst synthesizers, and changelog generators.

- **`synthesizer_burst.py`**
  - **Classes:** `BurstSynthesizerMixin`
  - **Purpose:** Synthesis mixin specifically handling discrete burst change descriptions.

- **`synthesizer_file.py`**
  - **Classes:** `FileSynthesizerMixin`
  - **Purpose:** Synthesis mixin generating single-file impact explanations.

- **`synthesizer_tiered.py`**
  - **Core Functions:** `tier1_heuristic_synthesize(files)`, `synthesize_milestone(bursts)`
  - **Purpose:** Fast offline heuristic engine providing instant zero-cost summaries without requiring external LLM connections.
