# Technical Specification: AI Subsystem & Secret Sanitization

> **Document ID:** SPEC-005  
> **Target Subsystem:** `scripts/ai/`  
> **Status:** Current Production Architecture  
> **Author:** Antigravity, Senior Systems Architect  

---

## 1. Architecture & Approach

The **AI Subsystem & Secret Sanitization Subsystem** provides intelligent, multi-layered telemetry analysis while guaranteeing zero secret leakage and zero direct provider API calls from worker processes.

### Core Architectural Decisions & Invariants
1. **Controller-Owned Execution Plane:** Project worker processes are strictly isolated from AI provider credentials. Workers enqueue analysis requests into `data/engine.db` via `enqueue_analysis()`. Only the central controller process (`ControllerAIExecutor`) holds API keys, manages bounded provider concurrency pools, and issues outbound LLM requests.
2. **Durable Job Leasing & Reclaim Loop:** Analysis tasks are tracked in the `jobs` table of `engine.db`. A controller worker leases a job with a 20-second expiration (`lease_duration_s = 20.0`) and sends heartbeat extensions every 5 seconds. If a controller crashes mid-inference, abandoned leases are automatically reclaimed and resumed upon boot.
3. **9-Part Canonical Cache Key:** Computations are aggressively cached using a 9-tuple SHA-256 hash. Identical inputs across identical versions never incur redundant LLM tokens.
4. **Progressive Analysis Hierarchy (L1–L4):** Analysis is stratified into four progressive levels, allowing instant high-level summaries without waiting for exhaustive architectural decomposition. Higher levels chain context from lower levels using explicit `"expand, don't restate"` prompt instructions.
5. **Defense-in-Depth Secret Sanitization (`v2.0`):** Sensitive files (`.env*`, `*.pem`, `*.key`, `id_rsa*`) are blocked entirely from AI payload generation. In addition, all diff and context text is scrubbed using pre-compiled regular expressions before caching, database insertion, or API transmission.
6. **Explicit Cloud Consent Enforcement:** Local providers (e.g., Ollama, local heuristics) execute freely. Cloud providers (Google Gemini) require explicit per-project opt-in in project configuration (`cloud_ai_enabled: true`).

```mermaid
flowchart TD
    subgraph WorkerPlane ["Worker Plane (No Secrets)"]
        WP["ProjectWorker"]
        DIFF["Raw Diffs & Burst Metadata"]
        
        WP --> DIFF
        DIFF -- "enqueue_analysis (Sanitized)" --> JOB_QUEUE
    end

    subgraph ControllerPlane ["Controller Plane (Owns Provider Keys)"]
        JOB_QUEUE[("engine.db: jobs\n(status: queued, running, done)")]
        EXEC["ControllerAIExecutor"]
        POOL["BoundedProviderPool\n(Rate Limiting & Concurrency)"]
        
        JOB_QUEUE -. "claim_job(lease=20s)" .-> EXEC
        EXEC -. "Heartbeat every 5s" .-> JOB_QUEUE
        EXEC --> POOL
    end

    subgraph SanitizerLayer ["Sanitization & Redaction Layer (sanitizer.py v2.0)"]
        SAN["Secret Sanitizer"]
        SAN -. "Redacts AWS, GitHub, Google, OpenAI, JWT, PEM, Passwords" .-> EXEC
    end

    subgraph LLMProviders ["External / Local Inference"]
        GEMINI["Google Gemini API\n(Flash / Pro)\n*Requires Cloud Consent*"]
        OLLAMA["Local Ollama\n(Qwen2.5-Coder)"]
        
        POOL --> GEMINI
        POOL --> OLLAMA
    end
```

---

## 2. Core Classes & Technical Reference

### 2.1 `ControllerAIExecutor` (`scripts/ai/controller_executor.py`)
- **`enqueue_analysis(...)`**: Worker-safe entry point. Sanitizes diffs and contexts, computes SHA-256 content hashes, computes the 9-part cache key, and writes a `queued` job to `engine.db`.
- **`claim_job(worker_id, lease_duration_s=20.0)`**: Claims an unleased job, updates status to `running`, and marks lease expiration.
- **`reclaim_expired_leases()`**: Reverts abandoned or expired leases back to `queued`, allowing uninterrupted recovery from crashes.
- **`check_cloud_consent(project_slug, provider)`**: Raises `ConsentRequiredError` if a cloud provider is targeted without explicit project configuration consent or if running under an offline profile.
- **`_process_single_job(job)`**: Runs the execution pipeline: checks cache key, spans heartbeat daemon, acquires provider slot from `BoundedProviderPool`, invokes provider, parses response, and updates job status to `done`.

### 2.2 Canonical Identity & Cache Key (`scripts/ai/identity.py`)
- **`compute_analysis_cache_key(...)`**: Computes SHA-256 over exactly 9 components:
  $$\text{key} = \text{SHA-256}(\text{target\_kind} : \text{target\_id} : \text{profile} : \text{input\_hash} : \text{model} : \text{provider} : \text{prompt\_version} : \text{sanitizer\_version} : \text{context\_hash})$$
- **`AnalysisStatus` Enum:** `queued`, `running`, `done`, `failed`, `superseded`.
- **`ProgressiveLevel` Enum:**
  - **`L1 Quick`**: High-level intent, executive summary, and primary change domain.
  - **`L2 Detailed`**: Per-file mechanics, modified routines, and data flow.
  - **`L3 Architecture`**: System coupling, architectural invariants, and design patterns.
  - **`L4 Risk/Impact`**: Regression vectors, test obligations, edge cases, and rollback risks.

### 2.3 Secret Sanitizer (`scripts/ai/sanitizer.py`)
- **Current Version:** `v2.0` (tracked explicitly in cache key to force cache invalidation upon rule updates).
- **File-Level Exclusions:** Matches `SENSITIVE_FILE_PATTERNS` (`.env*`, `*.pem`, `*.key`, `id_rsa*`, `id_ed25519*`, `credentials*`, `*.pfx`, `*.p12`, `*.keystore`).
- **Text-Level Redaction Patterns:**
  - `_AWS_KEY_PATTERN`: AWS Access Key IDs (`AKIA...`, `ASIA...`).
  - `_GITHUB_TOKEN_PATTERN`: Personal access tokens (`ghp_...`, `github_pat_...`).
  - `_GOOGLE_KEY_PATTERN`: Google API keys (`AIza...`).
  - `_OPENAI_KEY_PATTERN`: OpenAI secret keys (`sk-...`, `sk-proj-...`).
  - `_SLACK_TOKEN_PATTERN`: Slack workspace and bot tokens (`xoxb-...`).
  - `_JWT_PATTERN`: Compact JSON Web Tokens (`eyJ...`).
  - `_PEM_KEY_PATTERN`: Private key blocks (`-----BEGIN ... PRIVATE KEY-----`).
  - `_KV_SECRET_PATTERN`: Key-value assignments (`password=`, `token=`, `api_key=`).
  - `_URL_CREDENTIAL_PATTERN`: Embedded URL user/password schemes (`https://user:pass@host`).

---

## 3. Data Flow: Analysis Job Lifecycle

```mermaid
sequenceDiagram
    autonumber
    participant WP as ProjectWorker
    participant DB as SQLite (engine.db)
    participant Exec as ControllerAIExecutor
    participant Pool as BoundedProviderPool
    participant LLM as Provider (Gemini / Ollama)

    WP->>Exec: enqueue_analysis(slug, target, diff, L1)
    Note over Exec: Sanitize diff & context\nCompute 9-part cache key
    Exec->>DB: INSERT INTO jobs (status='queued', cache_key=...)
    
    loop Background Worker Loop
        Exec->>DB: claim_job(worker_id, lease=20s)
        DB-->>Exec: job record
    end

    activate Exec
    Note over Exec: Spawn heartbeat thread (extends lease every 5s)
    Exec->>DB: Check existing cache_key
    alt Cache Hit
        Exec->>DB: complete_job(job_id, analysis_id=cached.id)
    else Cache Miss
        Exec->>Exec: check_cloud_consent()
        Exec->>Pool: acquire(provider, timeout=30s)
        Pool-->>Exec: slot acquired
        Exec->>LLM: call_provider(prompt, sanitized_diff)
        LLM-->>Exec: model response
        Exec->>DB: INSERT INTO analyses (...)\nUPDATE jobs SET status='done'
    end
    deactivate Exec
```

---

## 4. Prompt Chaining & Progressive Hierarchy

```mermaid
flowchart TD
    subgraph Pipeline ["Progressive Prompt Chaining"]
        L1["L1: Quick Intent Summary\n(Diff + Context)"]
        L2["L2: Detailed Mechanics\n(Diff + L1 Output + 'expand, don't restate')"]
        L3["L3: Architectural Coupling\n(Diff + L2 Output + 'expand, don't restate')"]
        L4["L4: Risk & Rollback Impact\n(Diff + L3 Output + 'expand, don't restate')"]
        
        L1 --> L2 --> L3 --> L4
    end
```

When building prompts for higher levels (`L2`, `L3`, `L4`), `build_progressive_prompt()` injects the outputs of prior completed levels alongside the directive `EXPAND_INSTRUCTION = "expand, don't restate"`. This avoids token waste on repeated summaries and focuses subsequent model inferences strictly on deeper structural and risk questions.

---

## 5. Failure Recovery & Error Classification

- **Error Classification:** Provider exceptions are classified via `classify_ai_exception()` into `RateLimitError`, `AuthenticationError`, `ConsentRequiredError`, `TimeoutError`, and `JSONParseError`.
- **Adaptive Retries:**
  - Rate limits invoke exponential backoff with jitter managed by `RateLimitManager`.
  - Authentication errors or missing consent abort immediately without consuming retry quotas.
  - Parse failures trigger secondary schema repair prompts.
- **Lease Timeout Recovery:** If a controller terminates abnormally, its active jobs expire after 20 seconds. The periodic `reclaim_expired_leases()` pass resets their status to `queued` with incremented retry counters, guaranteeing that no telemetry burst is left unanalyzed.
