# Secrets & Configuration Guide

> **Security Policy:** Never commit real API keys, PATs, or secrets to version control.
> Always use the `.example.json` template files as your starting point.

---

## 1. Configuration File Overview

| File | Purpose | Contains Secrets? |
|---|---|---|
| `ai_data/models_config.json` | AI provider credentials & model settings | ✅ Yes (Gemini API key) |
| `ai_data/git_config.json` | GitHub PAT and remote repo URL | ✅ Yes (PAT token) |
| `ai_data/models_config.example.json` | Safe template — commit this | ❌ No |
| `ai_data/git_config.example.json` | Safe template — commit this | ❌ No |

Both `*_config.json` files are listed in `.gitignore` and are **never committed**.

---

## 2. AI Configuration (`ai_data/models_config.json`)

### Setup

```bash
cp ai_data/models_config.example.json ai_data/models_config.json
```

### File Structure

```json
{
  "preferred_provider": "gemini",
  "gemini_model": "gemini-2.5-flash-lite",
  "ollama_model": "qwen2.5-coder:7b",
  "ollama_base_url": "http://127.0.0.1:11434",
  "gemini": {
    "api_key": "YOUR_GEMINI_API_KEY_HERE"
  },
  "keys": [
    {
      "id": "key_001",
      "label": "Primary Gemini Key",
      "api_key": "YOUR_GEMINI_API_KEY_HERE",
      "model": "gemini-2.5-flash-lite",
      "created_at": "2026-01-01T00:00:00+05:30"
    }
  ],
  "analysis": {
    "max_diff_lines_for_summary": 250
  }
}
```

### Field Reference

| Field | Type | Description |
|---|---|---|
| `preferred_provider` | string | `"gemini"`, `"ollama"`, or `"heuristic"` |
| `gemini_model` | string | Model ID. Recommended: `gemini-2.5-flash-lite` |
| `ollama_model` | string | Ollama model name. Recommended: `qwen2.5-coder:7b` |
| `ollama_base_url` | string | Ollama server URL (default: `http://127.0.0.1:11434`) |
| `gemini.api_key` | string | Your Google Gemini API key |
| `keys` | array | Multi-key store for key rotation |
| `analysis.max_diff_lines_for_summary` | int | Max diff lines sent to AI (default: 250) |

### Obtaining a Gemini API Key

1. Go to [Google AI Studio](https://aistudio.google.com/)
2. Click **"Get API Key"**
3. Create or select a Google Cloud project
4. Copy the key into `gemini.api_key` in your `models_config.json`
5. Optionally add it to the `keys` array for multi-key rotation

> **Rate Limiting:** The engine automatically enforces a 15 RPM rate limit via `scripts/ai/rate_limiter.py`. When the limit is reached, it falls back to Ollama or heuristic mode.

### Provider Priority Logic

```
preferred_provider = "gemini"  →  try Gemini → fallback Ollama → fallback heuristic
preferred_provider = "ollama"  →  try Ollama → fallback Gemini (if key present) → heuristic
preferred_provider = "heuristic" → pure heuristic, no network calls ever
```

### Heuristic Mode (Zero-AI, Fully Offline)

Set `preferred_provider` to `"heuristic"` for completely offline operation. The engine generates meaningful rule-based burst summaries with zero network calls, zero GPU load.

---

## 3. Setting Up Local Ollama

Ollama provides a local LLM runtime with no cloud dependency.

### Install Ollama

```bash
# Windows — download installer from https://ollama.ai/download
# Linux
curl -fsSL https://ollama.ai/install.sh | sh

# macOS
brew install ollama
```

### Start Ollama and Pull the Model

```bash
# Start the server
ollama serve

# Pull the recommended coder model
ollama pull qwen2.5-coder:7b

# Alternative: smaller model for low-RAM machines
ollama pull qwen2.5-coder:3b

# Verify it works
curl http://127.0.0.1:11434/api/tags
```

### Configure the Engine

In `ai_data/models_config.json`:
```json
{
  "preferred_provider": "ollama",
  "ollama_model": "qwen2.5-coder:7b",
  "ollama_base_url": "http://127.0.0.1:11434"
}
```

### Test the Connection via Dashboard

1. Open the dashboard → click **⚙ Settings** (top-right)
2. Under **AI Provider**, select "Ollama"
3. Click **"Test Connection"**
4. You should see: `✅ Ollama is reachable. Model: qwen2.5-coder:7b`

---

## 4. Git Remote Sync Configuration (`ai_data/git_config.json`)

This feature allows the engine to automatically push milestone commits (with AI-generated changelogs) to a GitHub repository.

### Setup

```bash
cp ai_data/git_config.example.json ai_data/git_config.json
```

### File Structure

```json
{
  "remote_url": "https://github.com/YOUR_USERNAME/YOUR_REPO.git",
  "branch": "main",
  "remote_name": "origin",
  "pat_token": "ghp_YOUR_PERSONAL_ACCESS_TOKEN_HERE",
  "committer_name": "SPD Analysis Engine",
  "committer_email": "engine@local.dev",
  "auto_push_on_stop": false
}
```

### Field Reference

| Field | Type | Description |
|---|---|---|
| `remote_url` | string | Full HTTPS URL of the target GitHub repo |
| `branch` | string | Target branch name (default: `main`) |
| `remote_name` | string | Git remote alias (default: `origin`) |
| `pat_token` | string | GitHub Personal Access Token |
| `committer_name` | string | Name used for generated commits |
| `committer_email` | string | Email used for generated commits |
| `auto_push_on_stop` | bool | Push milestone commit when project stops |

### Creating a GitHub Personal Access Token (PAT)

1. Go to **GitHub → Settings → Developer Settings → Personal Access Tokens → Tokens (classic)**
2. Click **"Generate new token (classic)"**
3. Set expiration to your preference (90 days recommended)
4. Select scopes:
   - ✅ `repo` — Full control of private repositories
   - ✅ `workflow` — If pushing to repos with GitHub Actions
5. Click **"Generate token"**
6. Copy the token immediately — it is shown only once
7. Paste into `pat_token` in your `git_config.json`

> **Security:** The PAT is stored only in `ai_data/git_config.json` (gitignored). It is masked with `****` in all API responses and never logged.

### Token Masking Behavior

When the dashboard shows git configuration, the token is always masked:
```json
{
  "pat_token": "ghp_****************************4a2b",
  "remote_url": "https://github.com/spdwivedi/nova_pulse.git"
}
```

The engine uses `scripts/git/utils.py → scrub_tokens()` to sanitize tokens from all log output and API responses.

### Testing the Git Connection

```powershell
# Via the dashboard: Settings → Git Remote → Test Connection

# Via API
Invoke-RestMethod -Method POST `
    -Uri http://localhost:8765/api/git/test `
    -Body '{"provider": "github"}' `
    -ContentType "application/json"
```

---

## 5. Environment Variables (Alternative to Config Files)

The engine also respects these environment variables, which take precedence over config files:

| Variable | Overrides |
|---|---|
| `GEMINI_API_KEY` | `models_config.json → gemini.api_key` |
| `SPD_OLLAMA_URL` | `models_config.json → ollama_base_url` |
| `SPD_GIT_PAT` | `git_config.json → pat_token` |

```powershell
# Windows PowerShell (session-scoped)
$env:GEMINI_API_KEY = "your-key-here"
python start.py ui --port 8765

# Linux / macOS
export GEMINI_API_KEY="your-key-here"
python start.py ui --port 8765
```

---

## 6. Key Rotation (Multiple Gemini Keys)

The engine supports a named key store for rotating API keys across rate-limit windows.

### Adding Keys via the Dashboard

1. Open **Settings → AI Provider → Gemini Keys**
2. Click **"+ Add Key"**
3. Enter a label (e.g., `"Work Key"`) and the API key value
4. Click **Save**

### Selecting the Active Key

In the dashboard, click a key row and click **"Make Active"**. The engine records `select_key_id` in `models_config.json` and uses that key for all subsequent requests.

### Key Storage Schema (in `models_config.json`)

```json
{
  "select_key_id": "key_002",
  "keys": [
    { "id": "key_001", "label": "Primary", "api_key": "AIza...", "model": "gemini-2.5-flash-lite" },
    { "id": "key_002", "label": "Backup",  "api_key": "AIza...", "model": "gemini-2.5-flash" }
  ]
}
```

---

## 7. `.gitignore` Reference

The following files are always excluded from version control:

```gitignore
ai_data/models_config.json
ai_data/git_config.json
ai_data/cache/
current/
last_run/
history/
trash/
*.pyc
__pycache__/
.venv/
.env
```

Never remove these entries. The `.example.json` files are safe to commit and serve as documentation for contributors.
