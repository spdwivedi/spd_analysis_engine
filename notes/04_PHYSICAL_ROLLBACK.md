# Physical Workspace Rollback — Runbook

> This runbook explains the two-stage rollback mechanism that reverts both the physical filesystem and the SQLite event database to a prior burst state.

---

## 1. Rollback Overview

A rollback in the SPD Analysis Engine is a **reversible, two-stage operation**:

| Stage | What Happens | Where |
|---|---|---|
| **Stage 1** | Shadow Git checkout reverts workspace files to the state at the target burst | `current/<slug>/shadow_git/` → workspace |
| **Stage 2** | SQLite WAL events and patches after the target burst are soft-archived | `current/<slug>/session.db` |

**Critically, stage 2 is soft-delete only** — events are not permanently deleted. They are moved to `trash/` or flagged with `is_rolled_back=True`, preserving full auditability.

---

## 2. Initiating a Rollback

### Via the Dashboard

1. Open the **Timeline** panel.
2. Locate the target burst (the state you want to revert TO).
3. Click the **↩ Rollback to here** button on that burst card.
4. Confirm the rollback in the modal dialog.
5. The rollback progress tray appears at the bottom of the screen with live status updates.

### Via API

```bash
POST /api/project/<name>/rollback-burst
Body:
{
  "event_id": 38,       # The burst ID to roll back TO (inclusive)
  "target_dir": "D:\\Projects\\nova_pulse\\src"
}
```

---

## 3. Stage 1: Physical File Reversion via Shadow Git

The Shadow Git rollback uses `git --work-tree` to check out the workspace files at the exact state captured at the target burst's commit.

### Implementation (`scripts/git/shadow_rollback.py`)

```python
class ShadowRollbackMixin:
    def restore_workspace_to_commit(
        self,
        commit_hash: str,
        target_dir: Path | str,
    ) -> bool:
        # 1. Checkout all tracked files at commit_hash into target_dir
        self._run_git([
            "checkout", "-f", commit_hash, "--", "."
        ], work_tree=target_dir)

        # 2. Query untracked files added AFTER this commit
        untracked = self._get_files_added_after(commit_hash)

        # 3. Remove those untracked files from the target workspace
        for filepath in untracked:
            target = Path(target_dir) / filepath
            if target.exists():
                target.unlink()

        return True
```

**Why `git checkout -f`?**  
The `-f` (force) flag discards any local modifications in the work-tree without prompting. This is safe here because we're operating on the shadow work-tree, not the developer's `.git`.

### Finding Untracked Files After a Commit

```python
def _get_files_added_after(self, commit_hash: str) -> list[str]:
    # Get all commits after the target
    log = self._run_git(["log", "--oneline", f"{commit_hash}..HEAD"])
    later_commits = [line.split()[0] for line in log.strip().splitlines()]

    added = set()
    for c in later_commits:
        # Files added (A status) in that commit
        diff = self._run_git(["diff-tree", "--no-commit-id", "-r",
                               "--name-status", c])
        for line in diff.splitlines():
            if line.startswith("A\t"):
                added.add(line[2:].strip())
    return list(added)
```

---

## 4. Stage 2: SQLite WAL Event Rollback

After the workspace files are restored, the SQLite records for all bursts **after** the target event are soft-archived.

### Implementation (`scripts/storage/rollback.py`)

```python
def rollback_burst(
    project_name: str,
    target_event_id: int,
    target_dir: Path | str,
    engine_root: Path | str,
) -> dict:
    slug = _slug(project_name)
    db_path = engine_root / "current" / slug / "session.db"
    meta = read_project_meta(engine_root, project_name)
    shadow_dir = engine_root / "current" / slug / "shadow_git"

    # Stage 1 — physical reversion
    sg = ShadowGit(shadow_dir=shadow_dir, target_dir=target_dir)
    commit_hash = sg.get_commit_for_event(target_event_id)
    sg.restore_workspace_to_commit(commit_hash, target_dir)

    # Stage 2 — soft-archive events after target
    with SessionDB(db_path) as db:
        conn = db.conn
        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM events WHERE id > ? ORDER BY id ASC",
            (target_event_id,)
        )
        later_ids = [r["id"] for r in cur.fetchall()]

        for eid in later_ids:
            # Mark as rolled back (soft delete)
            cur.execute(
                "UPDATE events SET is_rolled_back=1 WHERE id=?", (eid,)
            )
            # Archive patches to trash
            cur.execute(
                "DELETE FROM patches WHERE event_id=?", (eid,)
            )
        conn.commit()

    return {
        "success": True,
        "reverted_to_event": target_event_id,
        "archived_events": later_ids,
        "commit_hash": commit_hash,
    }
```

---

## 5. Windows File Locking — WinError 5 & WinError 32

On Windows, files in `.git/objects/` are often set **read-only** by Git itself. Standard `shutil.rmtree()` fails with `PermissionError: [WinError 5] Access is denied`.

### The Windows-Hardened Error Handler

```python
# scripts/trash/fs_ops.py

def _on_rm_error(func, path, exc_info):
    """onerror/onexc handler: strip read-only bit then retry deletion."""
    import stat
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)  # retry
    except Exception:
        pass  # best-effort

def _remove_readonly_recursive(path: Path) -> None:
    """Strip FILE_ATTRIBUTE_READONLY from all files before deletion."""
    for root, dirs, files in os.walk(str(path)):
        for fname in files:
            fpath = os.path.join(root, fname)
            try:
                os.chmod(fpath, stat.S_IWRITE)
            except Exception:
                pass

def _force_rmtree(path: Path | str) -> None:
    path = Path(path)
    if not path.exists():
        return
    _remove_readonly_recursive(path)
    try:
        # Python 3.12+: onexc parameter
        shutil.rmtree(str(path), onexc=_on_rm_error)
    except TypeError:
        # Python < 3.12: onerror parameter
        shutil.rmtree(str(path), onerror=_on_rm_error)
```

### WinError 32: File Locked by Another Process

`WinError 32` occurs when a file is open by another process (e.g., a Python process still holding a file handle, or Windows Defender scanning).

**Mitigation strategy:**
1. The engine uses a **retry loop** (up to 5 attempts, 200ms sleep between each).
2. After 5 failures, it raises the exception for the caller to handle.
3. The Trash Manager moves files to `trash/` rather than deleting outright, reducing the chance of lock conflicts.

```python
for attempt in range(5):
    try:
        shutil.move(str(current_dir), str(last_run_dir))
        break
    except PermissionError:
        if attempt < 4:
            time.sleep(0.2)
        else:
            raise
```

---

## 6. Rollback Progress Tray

The dashboard shows a live rollback progress tray at the bottom of the screen while a rollback is in progress.

### SSE Events During Rollback

```
data: {"event_type": "rollback_started", "project": "nova_pulse", "target_event_id": 38}
data: {"event_type": "rollback_progress", "stage": 1, "message": "Restoring workspace files..."}
data: {"event_type": "rollback_progress", "stage": 2, "message": "Archiving 4 events after burst #38..."}
data: {"event_type": "rollback_complete", "success": true, "reverted_to_event": 38}
```

### Dashboard Behavior

1. A **progress tray** slides up from the bottom with `"Rollback in progress..."`.
2. Each stage is shown with a spinner and a description.
3. On completion, the tray shows `"✅ Rollback complete — workspace restored to burst #38"`.
4. The timeline automatically refreshes to reflect the rolled-back state.
5. The tray auto-dismisses after 5 seconds.

---

## 7. Selective Burst Trash Deletion

The Trash Manager supports selectively deleting individual burst records without a full rollback.

### Via the Dashboard

1. In the timeline, click the **🗑 Trash** icon on a burst card.
2. Confirm in the modal.
3. The burst record is moved to `trash/` and the timeline card disappears.

### Implementation (`scripts/trash/burst_trash.py`)

```python
def delete_selective_bursts(
    project_name: str,
    event_ids: list[int],
    engine_root: Path,
) -> dict:
    """Move specific burst events to trash without rolling back the workspace."""
    slug = _slug(project_name)
    db_path = engine_root / "current" / slug / "session.db"
    trash_dir = engine_root / "trash" / slug

    results = []
    with SessionDB(db_path) as db:
        for eid in event_ids:
            # Export event + patches to trash JSON
            event_data = db.get_event(eid)
            patches = db.get_patches(eid)
            trash_entry = {
                "event": event_data,
                "patches": patches,
                "trashed_at": _utcnow_iso(),
            }
            trash_file = trash_dir / f"event_{eid}_{int(time.time())}.json"
            trash_file.parent.mkdir(parents=True, exist_ok=True)
            trash_file.write_text(json.dumps(trash_entry, indent=2))

            # Soft-delete from DB
            db.conn.execute("UPDATE events SET is_trashed=1 WHERE id=?", (eid,))
            results.append({"event_id": eid, "trash_file": str(trash_file)})

        db.conn.commit()

    return {"success": True, "trashed": results}
```

---

## 8. Recovery from a Botched Rollback

If a rollback fails mid-way (e.g., WinError 5 during file deletion), you may be left with a partially reverted workspace.

### Manual Recovery Steps

```bash
# 1. Check the shadow git log to find the target commit hash
git --git-dir=current/nova_pulse/shadow_git/.git log --oneline -20

# 2. Manually force-checkout to the target commit
git --git-dir=current/nova_pulse/shadow_git/.git \
    --work-tree=D:\Projects\nova_pulse\src \
    checkout -f abc1234 -- .

# 3. Check the session DB for rolled-back events
python -c "
import sqlite3
conn = sqlite3.connect('current/nova_pulse/session.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()
cur.execute('SELECT id, is_rolled_back FROM events ORDER BY id ASC')
for r in cur.fetchall():
    print(dict(r))
"
```

If needed, reset `is_rolled_back=0` to restore event visibility in the dashboard:
```sql
UPDATE events SET is_rolled_back=0 WHERE id IN (39, 40, 41);
```
