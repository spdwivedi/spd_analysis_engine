# scripts/shell/ — Shell Command Interception & Classification

This sub-package captures, classifies, and records terminal command execution events during a monitored project session.

---

## Module Map

| Module | Responsibility |
|---|---|
| [`classifier.py`](./classifier.py) | Command classification by type and significance |
| [`cmd_parser.py`](./cmd_parser.py) | Shell command AST parsing and argument extraction |
| [`tracker.py`](./tracker.py) | `ExecutionTracker` — active command tracking with timing |

---

## Command Classification (`classifier.py`)

The classifier determines whether a shell command should be recorded as an `EXEC` event in the session database.

### Command Categories

| Category | Examples | Recorded? |
|---|---|---|
| `TEST_RUN` | `pytest`, `npm test`, `jest`, `cargo test`, `go test` | ✅ Yes |
| `BUILD` | `make`, `npm run build`, `cargo build`, `mvn package` | ✅ Yes |
| `GIT_OP` | `git commit`, `git push`, `git merge`, `git rebase` | ✅ Yes |
| `INSTALL` | `pip install`, `npm install`, `yarn add`, `cargo add` | ✅ Yes |
| `RUN` | `python script.py`, `node app.js`, `./run.sh` | ✅ Yes |
| `LINT` | `eslint`, `pylint`, `ruff`, `flake8` | ✅ Yes |
| `DEPLOY` | `docker build`, `kubectl apply`, `firebase deploy` | ✅ Yes |
| `IGNORED` | `ls`, `cd`, `echo`, `cat`, `pwd`, `clear` | ❌ No |

### Classification Logic

```python
def classify_command(command: str) -> tuple[str, bool]:
    """
    Returns (category, is_significant).
    is_significant=True means the command should be recorded.
    """
    cmd_lower = command.strip().lower()

    # Ignored noise commands
    NOISE_PREFIXES = ("ls", "cd", "echo ", "cat ", "pwd", "clear",
                      "dir", "type ", "set ", "get-")
    if any(cmd_lower.startswith(p) for p in NOISE_PREFIXES):
        return ("IGNORED", False)

    # Test runners
    TEST_PATTERNS = ("pytest", "npm test", "jest", "mocha", "cargo test",
                     "go test", "gradle test", "mvn test", "flutter test")
    if any(p in cmd_lower for p in TEST_PATTERNS):
        return ("TEST_RUN", True)

    # Build tools
    BUILD_PATTERNS = ("make ", "npm run build", "cargo build",
                      "gradle build", "mvn package", "tsc", "vite build")
    if any(p in cmd_lower for p in BUILD_PATTERNS):
        return ("BUILD", True)

    # ... etc.
    return ("RUN", True)  # Default: record as generic run
```

---

## Command AST Parser (`cmd_parser.py`)

Parses shell commands to extract structured information:

- **Executable name:** `pytest`, `npm`, `python`
- **Arguments:** `["--verbose", "-k", "test_supervisor"]`
- **Flags:** `{"verbose": True, "k": "test_supervisor"}`
- **Redirections:** `> output.txt`, `2>&1`
- **Pipes:** `pytest | tee test_output.log`

```python
parsed = parse_command("pytest tests/ -v -k test_supervisor --tb=short")
# Returns:
# {
#   "executable": "pytest",
#   "args": ["tests/", "-v", "-k", "test_supervisor", "--tb=short"],
#   "flags": {"v": True, "k": "test_supervisor", "tb": "short"},
#   "is_background": False,
# }
```

---

## Execution Tracker (`tracker.py`)

`ExecutionTracker` is a context manager that records command start time, completion, and exit code.

```python
class ExecutionTracker:
    def __init__(self, db: SessionDB, session_id: int):
        self.db = db
        self.session_id = session_id

    def track(self, command: str, exit_code: int, duration_s: float):
        """Record a completed command execution as an EXEC event."""
        category, is_significant = classify_command(command)
        if not is_significant:
            return

        event_id = self.db.record_event(
            session_id=self.session_id,
            event_type="EXEC",
            first_file_touched=command,
            summary=f"exec: {command} [exit={exit_code}, {duration_s:.1f}s]",
            file_count=0,
        )
        return event_id
```

---

## Integration with Project Worker

The shell interceptor is invoked by the project worker's event loop:

```python
# In project_worker.py
tracker = ExecutionTracker(db=session_db, session_id=current_session_id)

# When the IDE extension or shell hook fires a command completion event:
tracker.track(
    command="python -m pytest tests/ -v",
    exit_code=0,
    duration_s=38.4,
)
```

The `ExecutionTracker` records the EXEC event, which appears in the timeline between EDIT burst events, creating a complete narrative of the development session.

---

## Shell Hook Setup (Optional)

To capture real-time terminal commands, configure your shell to call the engine's record-exec endpoint:

### Bash / Zsh Hook

```bash
# Add to ~/.bashrc or ~/.zshrc
function _spd_post_exec() {
    local exit_code=$?
    local cmd="$1"
    local duration=$SECONDS
    curl -s -X POST http://localhost:8765/api/project/my_project/record-exec \
        -H "Content-Type: application/json" \
        -d "{\"command\": \"$cmd\", \"exit_code\": $exit_code, \"duration_s\": $duration}" \
        > /dev/null 2>&1 &
}
trap '_spd_post_exec "$BASH_COMMAND"' DEBUG
```

### PowerShell Hook

```powershell
# Add to $PROFILE
function Invoke-SpdPostExec {
    param($Command, $ExitCode, $Duration)
    $body = @{command=$Command; exit_code=$ExitCode; duration_s=$Duration} | ConvertTo-Json
    Invoke-RestMethod -Method POST -Uri "http://localhost:8765/api/project/my_project/record-exec" `
        -Body $body -ContentType "application/json" -ErrorAction SilentlyContinue
}
```
