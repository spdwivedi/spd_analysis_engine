# scripts/ai/ — AI Synthesis Pipeline

This sub-package implements the full AI intelligence layer: model configuration, prompt engineering, multi-provider API calls, rate limiting, hierarchical disk caching, and graceful heuristic fallback.

---

## Module Map

| Module | Responsibility |
|---|---|
| [`synthesizer.py`](./synthesizer.py) | `AISynthesizer` — core class, config, cache, dispatch |
| [`synthesizer_burst.py`](./synthesizer_burst.py) | `BurstSynthesizerMixin` — `analyze_burst()`, `analyze_burst_overview()` |
| [`synthesizer_file.py`](./synthesizer_file.py) | `FileSynthesizerMixin` — `analyze_file()`, `_fallback_file_analysis()` |
| [`cache_manager.py`](./cache_manager.py) | `AICacheManager` — cache directory init and get/save dispatch |
| [`cache_queries.py`](./cache_queries.py) | `CacheQueriesMixin` — 8 typed CRUD methods for file and burst cache |
| [`prompt_builder.py`](./prompt_builder.py) | Prompt construction, `is_sanitized_patch()`, `chunk_diff()` |
| [`provider_gemini.py`](./provider_gemini.py) | `call_gemini()` — Gemini API call with SDK + HTTPS fallback |
| [`provider_ollama.py`](./provider_ollama.py) | `call_ollama()` — Local Ollama HTTP call |
| [`rate_limiter.py`](./rate_limiter.py) | `RateLimitManager` — 15 RPM sliding-window rate limiter |
| [`changelog.py`](./changelog.py) | `generate_session_changelog()` — AI session summary |
| [`command_explainer.py`](./command_explainer.py) | `explain_command()` — AI EXEC event explanation |
| [`config.py`](./config.py) | `load_model_config()`, `save_model_config()`, key management |

---

## `AISynthesizer` Class

The top-level AI class. Inherits from `BurstSynthesizerMixin` and `FileSynthesizerMixin`.

```
AISynthesizer
  ├── BurstSynthesizerMixin  (analyze_burst, analyze_burst_overview)
  └── FileSynthesizerMixin   (analyze_file, _fallback_file_analysis)
```

### Initialization

```python
syn = AISynthesizer(engine_root=Path("spd_analysis_engine"))
# Creates:
#   syn.engine_root  → Path to engine root
#   syn.ai_data_dir  → engine_root / "ai_data"
#   syn.cache_dir    → engine_root / "ai_data" / "cache"
#   syn.cache_mgr    → AICacheManager(cache_dir)
#   syn._config      → loaded from models_config.json
```

### Static / Class Methods (no instance needed)

```python
# Load model config from disk
cfg = AISynthesizer.load_config(engine_root=Path("."))

# Mask API keys in config before API response
masked = AISynthesizer.mask_config(cfg)

# Save updated config
AISynthesizer.save_config(new_cfg, engine_root=Path("."))

# Test provider connectivity
AISynthesizer.test_gemini_connection(api_key="...", model="gemini-2.5-flash-lite")
AISynthesizer.test_ollama_connection(base_url="http://127.0.0.1:11434")
```

---

## `analyze_burst()` (`synthesizer_burst.py`)

Analyzes a sealed burst event (multiple file diffs) using AI.

### Signature

```python
analysis = syn.analyze_burst(
    event=event_dict,          # Dict from SessionDB.get_events()
    patches=patch_list,        # List of {file_path, diff_content} dicts
    previous_summary=None,     # Optional prior burst summary for context
    provider="gemini",         # Override preferred provider
)
```

### Pipeline

```
1. Filter patches through is_sanitized_patch() → remove noise files
2. Build full_diff string from sanitized patches
3. Try to get ShadowGit delta diff (cleaner than raw filesystem diff)
4. Check disk cache (diff_hash match)
5. If cache hit → return cached result
6. Build tech_stack + dir_tree for context
7. Query EXEC events from DB for terminal command context
8. chunk_diff() → truncate to 30KB max
9. Build prompt via build_burst_prompt()
10. Call AI provider (Gemini → Ollama → heuristic fallback)
11. Parse JSON response via _parse_llm_json()
12. Save to disk cache
13. Return analysis dict
```

### Return Schema

```json
{
  "intent": "Refactored supervisor debounce grace window logic",
  "execution_rationale": "python -m pytest ran during validation",
  "architecture_impact": "Supervisor stability improved, no regression",
  "key_modifications": [
    "Modified hb_grace_threshold computation in supervisor.py",
    "Added max() guard against debounce_window < 12.0"
  ],
  "summary": ["same as key_modifications"],
  "functionality_gained": "Dead worker detection no longer triggers on long quiet sessions",
  "provider": "gemini",
  "cached": false,
  "diff_hash": "a1b2c3..."
}
```

---

## `analyze_burst_overview()` (`synthesizer_burst.py`)

Generates a high-level architectural overview of all files touched in a burst — without verbose line diffs.

```python
overview = syn.analyze_burst_overview(
    project_name="nova_pulse",
    burst_id=42,
    patches=patch_list,      # Optional — will query DB if not supplied
    session_id=1,
    provider="gemini",
    force_refresh=False,
)
```

**Return schema:**
```json
{
  "burst_id": 42,
  "session_id": 1,
  "file_count": 3,
  "overview": {
    "scripts/core/supervisor.py": "Updated heartbeat grace window to prevent false crash detection",
    "scripts/core/worker_helpers.py": "Extracted helper functions from supervisor.py"
  },
  "burst_summary": "Refactored process supervision layer for improved resilience",
  "provider": "gemini",
  "cached": false
}
```

---

## `analyze_file()` (`synthesizer_file.py`)

Analyzes a single file diff in isolation — used for the per-file deep-analysis panel in the dashboard.

```python
result = syn.analyze_file(
    project_name="nova_pulse",
    file_path="scripts/core/supervisor.py",
    diff_text="--- a/...\n+++ b/...\n@@...",
    burst_id=42,
    session_id=1,
    mode="deep",   # "deep" | "compact"
    provider=None,  # Use configured preferred_provider
    force_refresh=False,
)
```

**Return schema:**
```json
{
  "file_path": "scripts/core/supervisor.py",
  "summary": "Modified heartbeat grace window calculation",
  "why_modified": "Guard against false dead-worker detection on long idle sessions",
  "changed_symbols": ["hb_grace_threshold", "stop_project", "start_project"],
  "breaking_changes": ["None detected"],
  "risk_level": "LOW",
  "mode": "deep",
  "provider": "gemini",
  "cached": false,
  "diff_hash": "..."
}
```

---

## Cache Architecture (`cache_manager.py` + `cache_queries.py`)

### `AICacheManager(CacheQueriesMixin)`

```
AICacheManager
  └── CacheQueriesMixin  (8 typed CRUD methods)
```

### Cache Directory Layout

```
ai_data/cache/
  nova_pulse/                  ← project slug
    session_1/                 ← session ID
      event_42/                ← event/burst ID
        file__scripts_core_supervisor_py__<hash>.json
        burst_overview_<hash>.json
```

### Cache CRUD Methods (from `CacheQueriesMixin`)

| Method | Description |
|---|---|
| `get_file_analysis(...)` | Exact-match lookup by diff_hash |
| `find_file_analysis(...)` | Fuzzy lookup by burst_id + file_path |
| `save_file_analysis(...)` | Persist analysis to disk |
| `delete_file_analysis(...)` | Delete cached file analysis (for force_refresh) |
| `get_burst_overview(...)` | Exact-match lookup by patch_hash |
| `find_burst_overview(...)` | Fuzzy lookup by burst_id |
| `save_burst_overview(...)` | Persist overview to disk |
| `delete_burst_overview(...)` | Delete cached overview |

### Cache Key Resolution

Cache files use a dual-key strategy:
1. **Primary key:** `diff_hash` or `patch_hash` (deterministic SHA-256 of the diff content)
2. **Secondary key:** `burst_id + session_id + project_name` (for fuzzy lookup when hash is unavailable)

---

## Rate Limiter (`rate_limiter.py`)

```python
class RateLimitManager:
    max_rpm: int = 15  # class-level default; shared across all AISynthesizer instances

    def acquire(self) -> bool:
        """Returns True if a call is allowed, False if rate-limited."""

    def get_status(self) -> dict:
        """Returns {"cooling_down": bool, "cooldown_remaining_s": float, "calls_in_window": int}"""
```

The rate limiter uses a **sliding window deque** of recent call timestamps. When `len(window) >= max_rpm`, it sets a `cooldown_until` timestamp and returns `False` for subsequent `acquire()` calls until the window clears.

---

## Prompt Builder (`prompt_builder.py`)

### `is_sanitized_patch(file_path)`

Returns `False` (meaning "exclude this file") for:
- Salesforce DX files: `.sf/`, `.sfdx/`, `.forceignore`
- Compiled artifacts: `*.pyc`, `*.class`, `dist/`
- Lock files: `package-lock.json`, `yarn.lock`, `Pipfile.lock`
- IDE config: `.vscode/settings.json`

### `chunk_diff(diff_text, max_lines=250, max_chars=30000)`

Truncates diffs that exceed `max_lines` or `max_chars` to prevent excessive token usage. Adds a `[DIFF TRUNCATED...]` footer when truncated.

### `build_burst_prompt(...)` and `build_file_diff_prompt(...)`

Construct the full AI prompt with:
- Project context (name, tech stack, directory tree)
- Diff content (chunked)
- EXEC command context
- Output schema specification (enforces JSON response format)

---

## Provider Waterfall

```
analyze_burst() / analyze_file() / analyze_burst_overview()
          │
          ├── preferred_provider == "heuristic"?
          │       └── _fallback_analysis() / _fallback_file_analysis()
          │
          ├── preferred_provider == "gemini"?
          │       ├── rate_limiter.acquire() → True?
          │       │       └── call_gemini(prompt)
          │       └── rate_limiter.acquire() → False?
          │               └── skip to next provider
          │
          ├── preferred_provider == "ollama"?
          │       └── call_ollama(prompt)
          │
          └── all providers failed?
                  └── _fallback_analysis() / _fallback_file_analysis()
```
