# scripts/git/ — Shadow Git Micro-Versioning Engine

This sub-package implements the isolated Shadow Git micro-versioning system that captures every sealed burst as a micro-commit, supports delta diff extraction, workspace rollback, and optional remote GitHub synchronization.

---

## Module Map

| Module | Responsibility |
|---|---|
| [`shadow_repo.py`](./shadow_repo.py) | `ShadowGit` class — core micro-commit engine |
| [`shadow_rollback.py`](./shadow_rollback.py) | `ShadowRollbackMixin` — workspace restore and commit reversion |
| [`shadow_push.py`](./shadow_push.py) | `push_to_remote_repo()` — GitHub remote sync |
| [`credentials.py`](./credentials.py) | `GitCredentialManager` — PAT storage and auth URL building |
| [`remote_sync.py`](./remote_sync.py) | `push_to_remote()` / `init_primary_repo()` convenience aliases |
| [`utils.py`](./utils.py) | `_NO_WINDOW_FLAG`, `HARDENED_GITIGNORE_TEMPLATE`, helper functions |

---

## `ShadowGit` (`shadow_repo.py`)

The central class. Inherits `ShadowRollbackMixin` for rollback capabilities.

### Initialization

```python
sg = ShadowGit(
    shadow_dir=Path("current/nova_pulse/shadow_git"),
    target_dir=Path("D:/Projects/nova_pulse/src"),
)
```

On `__init__`, if `shadow_git/` doesn't exist, `ShadowGit._ensure_shadow_repo()` is called to:
1. `git init --bare` the shadow directory.
2. Write `HARDENED_GITIGNORE_TEMPLATE` to exclude noise files.
3. Configure `user.name` and `user.email` for commits.

### `commit_burst(event_id, summary, files_changed)`

```python
commit_hash = sg.commit_burst(
    event_id=42,
    summary="Refactor supervisor debounce window logic",
    files_changed=["scripts/core/supervisor.py", "scripts/core/worker_helpers.py"],
)
```

Internally:
```bash
git --git-dir=<shadow>/.git --work-tree=<workspace> add -A
git --git-dir=<shadow>/.git --work-tree=<workspace> commit \
    -m "[SPD #42] Refactor supervisor debounce window logic" \
    --author="SPD Engine <engine@local.dev>"
```

### `get_burst_delta_diff(event_id, files)`

Returns the unified diff between the commit at `event_id - 1` and the commit at `event_id`, optionally filtered to specific files.

```python
delta = sg.get_burst_delta_diff(event_id=42, files=["scripts/core/supervisor.py"])
# Returns: "--- a/scripts/core/supervisor.py\n+++ b/scripts/core/supervisor.py\n@@..."
```

### `get_commit_for_event(event_id)` 

Searches the shadow git log for the commit tagged with `[SPD #<event_id>]` and returns its hash.

```python
commit_hash = sg.get_commit_for_event(42)
# Returns: "a1b2c3d4..."
```

### `get_commit_history(limit)`

Returns a list of recent micro-commits with their event IDs and summaries.

---

## `ShadowRollbackMixin` (`shadow_rollback.py`)

Provides workspace restoration capabilities to `ShadowGit`.

### `rollback_burst_commit(event_id)`

Resets the shadow git HEAD to the commit at `event_id`, discarding all subsequent micro-commits from the shadow history.

```python
sg.rollback_burst_commit(event_id=38)
# Shadow git HEAD is now at the commit tagged [SPD #38]
```

### `restore_workspace_to_commit(commit_hash, target_dir)`

Checks out the workspace files from the shadow repo at `commit_hash` into `target_dir`:

```bash
git --git-dir=<shadow>/.git --work-tree=<target_dir> checkout -f <commit_hash> -- .
```

Then queries files added after `commit_hash` and deletes them from `target_dir` (these are files that didn't exist at the target burst).

---

## `GitCredentialManager` (`credentials.py`)

Manages GitHub PAT storage, token masking, and authenticated URL construction.

### `get_authenticated_url(remote_url, pat_token)`

Injects the PAT into the remote URL for authenticated pushes:
```python
# Input:  "https://github.com/spdwivedi/nova_pulse.git"
# Output: "https://<pat>@github.com/spdwivedi/nova_pulse.git"
```

### `mask_token(token)` / `scrub_text(text, token)`

```python
GitCredentialManager.mask_token("ghp_abcdef123456")
# Returns: "ghp_****************************3456"

GitCredentialManager.scrub_text(log_output, pat_token)
# Replaces any occurrence of pat_token with "****"
```

---

## `push_to_remote_repo()` (`shadow_push.py`)

Pushes a milestone commit (with AI-generated changelog) to a GitHub remote.

### Usage

```python
result = push_to_remote_repo(
    repo_path=Path("current/nova_pulse"),
    remote_url="https://github.com/spdwivedi/nova_pulse.git",
    commit_message="[Milestone] Phase 3 complete — Shadow Git micro-versioning",
    changelog_content="# Changelog\n\n## Phase 3\n...",
    branch="main",
    engine_root=engine_root,
)
```

### What it Does

1. Reads PAT from `git_config.json` via `GitCredentialManager`.
2. Initializes a primary repo at `repo_path` if not already a git repo.
3. Writes `CHANGELOG.md` to the repo root.
4. Stages and commits with the provided message.
5. Pushes to the authenticated remote URL.
6. Calls `suppress_file_path("CHANGELOG.md", 4.0)` to prevent the bundler from treating the changelog write as a code burst.

---

## Utilities (`utils.py`)

### `HARDENED_GITIGNORE_TEMPLATE`

A production-grade `.gitignore` applied to every new shadow repository. Excludes:
- `node_modules/`, `__pycache__/`, `.venv/`, `dist/`, `build/`
- `.sf/`, `.sfdx/`, `.salesforce/`
- `*.pyc`, `*.class`, `*.o`, `*.so`
- IDE configs: `.vscode/settings.json`, `.idea/`
- Secrets: `.env`, `*.pem`, `*.key`

### `_NO_WINDOW_FLAG`

```python
_NO_WINDOW_FLAG = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
```

Applied to every `subprocess.Popen` call in this package to prevent ghost console windows on Windows.

### `scrub_tokens(text, token=None)`

Strips PAT tokens from any log or output text using regex replacement.

### `discover_native_git_remote(target_path)`

Reads the developer's own `.git/config` to discover their existing remote URL, used to pre-fill the GitHub sync configuration.

### `is_salesforce_or_transient_file(path_str)`

Returns `True` for files that should be excluded from shadow git tracking (Salesforce DX artifacts, transient build outputs).

---

## Architecture Notes

### Why a Separate Shadow `.git`?

The shadow `.git` is completely isolated from the developer's own project `.git`. This means:
- The developer's commit history is never polluted with telemetry micro-commits.
- The shadow repo can be deleted and rebuilt at any time without data loss.
- Rollbacks operate only on the shadow's work-tree — the developer's unstaged changes are not affected.

### Git Command Execution Pattern

All git commands are run via `ShadowGit._run_git()`:

```python
def _run_git(self, args: list[str], work_tree=None) -> str:
    cmd = [
        "git",
        f"--git-dir={self.shadow_dir}/.git",
    ]
    if work_tree:
        cmd.append(f"--work-tree={work_tree}")
    cmd.extend(args)

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        creationflags=_NO_WINDOW_FLAG,  # Windows: no console popup
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())
    return result.stdout
```
