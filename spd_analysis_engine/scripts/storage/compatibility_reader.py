"""
scripts/storage/compatibility_reader.py
=======================================
Read-only SQLite query and schema compatibility layer for the SPD Analysis Engine.

Guarantees:
- Strictly read-only: zero mutations, zero writes, zero DDL, zero WAL checkpointing.
- Legacy v1 schema compatibility: gracefully queries legacy databases without
  requiring `event_id`, `seq`, `burst_uid`, or `adds`/`dels` columns.
- Deterministic compatibility values: provides stable `event_id` (`evt_legacy_<id>`),
  seq order, and computed additions/deletions from diffs where legacy schema lacks columns.
- Resilient against empty, missing, or malformed tables and databases.
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Any

logger = logging.getLogger(__name__)


def parse_diff_stats(diff_text: str | None) -> tuple[int, int]:
    """
    Parse additions (+) and deletions (-) from unified diff text.
    Ignores diff headers ('+++', '---').
    """
    if not diff_text:
        return 0, 0
    adds = 0
    dels = 0
    for line in diff_text.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            adds += 1
        elif line.startswith("-") and not line.startswith("---"):
            dels += 1
    return adds, dels


def get_table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    """Return the set of existing column names for table_name, or empty set if missing."""
    try:
        cur = conn.cursor()
        cur.execute(f"PRAGMA table_info({table_name});")
        return {row[1] for row in cur.fetchall()}
    except Exception:
        return set()


def resolve_event_row(conn: sqlite3.Connection, item_id: str | int) -> sqlite3.Row | None:
    """
    Resolve a single event row by identifier across modern and legacy v1 schemas.

    Lookup strategy:
    1. If `event_id` column exists:
       - Digit: query `WHERE id = ? OR event_id = ?`
       - Non-digit: query `WHERE event_id = ?`
       - Prefix match fallback: `evt_legacy_<id>`, `ev_<id>`, `evt_<id>` -> `WHERE id = ?`
    2. If `event_id` column does NOT exist (legacy v1):
       - Digit: query `WHERE id = ?`
       - Prefix match fallback: `evt_legacy_<id>`, `ev_<id>`, `evt_<id>` -> `WHERE id = ?`
       - Otherwise: return None
    """
    event_cols = get_table_columns(conn, "events")
    if not event_cols:
        return None

    cur = conn.cursor()
    has_event_id_col = "event_id" in event_cols
    item_str = str(item_id).strip()

    select_clause = "*, ('ev_' || id) AS event_id" if not has_event_id_col else "*"

    if item_str.isdigit():
        num_id = int(item_str)
        if has_event_id_col:
            cur.execute("SELECT * FROM events WHERE id = ? OR event_id = ? LIMIT 1", (num_id, item_str))
        else:
            cur.execute(f"SELECT {select_clause} FROM events WHERE id = ? LIMIT 1", (num_id,))
        return cur.fetchone()

    # Non-digit identifier
    if has_event_id_col:
        cur.execute("SELECT * FROM events WHERE event_id = ? LIMIT 1", (item_str,))
        row = cur.fetchone()
        if row:
            return row

    # Fallback for legacy prefixes: evt_legacy_<id>, ev_<id>, evt_<id>
    for prefix in ("evt_legacy_", "ev_", "evt_"):
        if item_str.startswith(prefix) and item_str[len(prefix):].isdigit():
            num_id = int(item_str[len(prefix):])
            cur.execute(f"SELECT {select_clause} FROM events WHERE id = ? LIMIT 1", (num_id,))
            return cur.fetchone()

    return None


def read_events_summary(
    conn: sqlite3.Connection,
    since_id: int | None = None,
    session_id: int | None = None,
    event_types: list[str] | None = None,
    limit: int = 50,
) -> tuple[list[dict[str, Any]], bool]:
    """
    Query bounded event summaries without full diff bodies.
    Adapts safely to legacy v1, partial, or modern schemas.
    Returns (summaries, has_more).
    """
    event_cols = get_table_columns(conn, "events")
    if not event_cols:
        return [], False

    patch_cols = get_table_columns(conn, "patches")
    has_patches = bool(patch_cols)
    has_patch_adds = "adds" in patch_cols
    has_patch_dels = "dels" in patch_cols
    has_diff_content = "diff_content" in patch_cols

    where_clauses = ["1=1"]
    params: list[Any] = []

    if since_id is not None:
        where_clauses.append("e.id > ?")
        params.append(since_id)

    if session_id is not None:
        where_clauses.append("e.session_id = ?")
        params.append(session_id)

    if event_types:
        placeholders = ",".join("?" for _ in event_types)
        where_clauses.append(f"UPPER(e.event_type) IN ({placeholders})")
        params.extend(event_types)

    where_sql = " AND ".join(where_clauses)

    # Adapt column expressions for missing optional/legacy columns
    col_exprs = [
        "e.id",
        "e.session_id" if "session_id" in event_cols else "1 AS session_id",
        "e.event_type" if "event_type" in event_cols else "'EDIT' AS event_type",
        "e.timestamp" if "timestamp" in event_cols else "'' AS timestamp",
        "e.first_file_touched" if "first_file_touched" in event_cols else "NULL AS first_file_touched",
        "e.summary" if "summary" in event_cols else "NULL AS summary",
        "e.executor" if "executor" in event_cols else "NULL AS executor",
        "e.burst_num" if "burst_num" in event_cols else "NULL AS burst_num",
        "COALESCE(e.commit_hash, (SELECT g.commit_hash FROM events g WHERE g.session_id = e.session_id AND g.event_type = 'GIT' AND g.id = e.id + 1 LIMIT 1)) AS commit_hash" if "commit_hash" in event_cols else "NULL AS commit_hash",
        "e.is_rolled_back" if "is_rolled_back" in event_cols else "0 AS is_rolled_back",
        "e.event_id" if "event_id" in event_cols else "('ev_' || e.id) AS event_id",
        "e.seq" if "seq" in event_cols else "e.id AS seq",
        "e.burst_uid" if "burst_uid" in event_cols else "NULL AS burst_uid",
        "e.effective_files" if "effective_files" in event_cols else "NULL AS effective_files",
    ]

    cur = conn.cursor()

    if has_patches and has_patch_adds and has_patch_dels:
        # Modern path: direct aggregate in SQL
        sql = f"""
            SELECT
                {', '.join(col_exprs)},
                COUNT(p.id) AS files_count,
                COALESCE(SUM(p.adds), 0) AS total_adds,
                COALESCE(SUM(p.dels), 0) AS total_dels
            FROM events e
            LEFT JOIN patches p ON p.event_id = e.id
            WHERE {where_sql}
            GROUP BY e.id
            ORDER BY e.id ASC
            LIMIT ?
        """
        params.append(limit + 1)
        cur.execute(sql, params)
        rows = cur.fetchall()
        diff_stats_map: dict[int, tuple[int, int]] = {}
    elif has_patches:
        # Legacy v1 path with patches: join for files_count, compute adds/dels from diff_content if available
        sql = f"""
            SELECT
                {', '.join(col_exprs)},
                COUNT(p.id) AS files_count,
                0 AS total_adds,
                0 AS total_dels
            FROM events e
            LEFT JOIN patches p ON p.event_id = e.id
            WHERE {where_sql}
            GROUP BY e.id
            ORDER BY e.id ASC
            LIMIT ?
        """
        params.append(limit + 1)
        cur.execute(sql, params)
        rows = cur.fetchall()

        diff_stats_map = {}
        if has_diff_content and rows:
            page_ids = [r["id"] for r in rows[:limit]]
            if page_ids:
                ph = ",".join("?" for _ in page_ids)
                cur.execute(
                    f"SELECT event_id, diff_content FROM patches WHERE event_id IN ({ph})",
                    page_ids,
                )
                for pr in cur.fetchall():
                    eid = pr[0]
                    adds, dels = parse_diff_stats(pr[1])
                    cur_adds, cur_dels = diff_stats_map.get(eid, (0, 0))
                    diff_stats_map[eid] = (cur_adds + adds, cur_dels + dels)
    else:
        # No patches table at all
        sql = f"""
            SELECT
                {', '.join(col_exprs)},
                0 AS files_count,
                0 AS total_adds,
                0 AS total_dels
            FROM events e
            WHERE {where_sql}
            ORDER BY e.id ASC
            LIMIT ?
        """
        params.append(limit + 1)
        cur.execute(sql, params)
        rows = cur.fetchall()
        diff_stats_map = {}

    has_more = len(rows) > limit
    result_rows = rows[:limit]

    page_ids = [r["id"] for r in result_rows]
    files_by_event: dict[int, list[str]] = {}
    if has_patches and page_ids:
        ph = ",".join("?" for _ in page_ids)
        try:
            cur.execute(
                f"SELECT event_id, file_path FROM patches WHERE event_id IN ({ph}) ORDER BY id ASC",
                page_ids,
            )
            for pr in cur.fetchall():
                files_by_event.setdefault(pr[0], []).append(pr[1])
        except Exception:
            pass

    burst_by_session: dict[int, int] = {}
    summaries = []
    for r in result_rows:
        eid = r["id"]
        sid = r["session_id"]
        raw_event_id = r["event_id"]
        stable_event_id = raw_event_id if raw_event_id else f"ev_{eid}"

        is_burst = (r["event_type"] == "EDIT") or (r["files_count"] > 0)
        if r["burst_num"] is not None:
            burst_num_val = r["burst_num"]
        elif is_burst:
            burst_by_session[sid] = burst_by_session.get(sid, 0) + 1
            burst_num_val = burst_by_session[sid]
        else:
            burst_num_val = None

        if eid in diff_stats_map:
            adds, dels = diff_stats_map[eid]
        else:
            adds = r["total_adds"]
            dels = r["total_dels"]

        ev_files = files_by_event.get(eid)
        if not ev_files and ("effective_files" in r.keys() and r["effective_files"]):
            try:
                import json
                eff_p = json.loads(r["effective_files"])
                if isinstance(eff_p, list):
                    ev_files = eff_p
            except Exception:
                pass
        if not ev_files and r["first_file_touched"]:
            ev_files = [r["first_file_touched"]]
        if not ev_files:
            ev_files = []

        files_cnt = r["files_count"] or len(ev_files)

        summaries.append({
            "id": eid,
            "event_id": stable_event_id,
            "seq": r["seq"],
            "session_id": sid,
            "burst_num": burst_num_val,
            "burst_uid": r["burst_uid"],
            "event_type": r["event_type"],
            "timestamp": r["timestamp"],
            "first_file_touched": r["first_file_touched"],
            "summary": r["summary"],
            "executor": r["executor"],
            "commit_hash": r["commit_hash"],
            "is_rolled_back": int(r["is_rolled_back"]) if r["is_rolled_back"] is not None else 0,
            "files_count": files_cnt,
            "files": ev_files,
            "additions": adds,
            "deletions": dels,
        })

    return summaries, has_more


def read_event_detail(
    conn: sqlite3.Connection,
    item_id: str | int,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """
    Fetch single event metadata and touched file list (no diff bodies).
    Returns (event_dict, files_list) or (None, []).
    """
    row = resolve_event_row(conn, item_id)
    if not row:
        return None, []

    ev_dict = dict(row)
    eid = ev_dict["id"]
    if not ev_dict.get("event_id"):
        ev_dict["event_id"] = f"ev_{eid}"
    if "seq" not in ev_dict or ev_dict["seq"] is None:
        ev_dict["seq"] = eid
    if "burst_uid" not in ev_dict:
        ev_dict["burst_uid"] = None
    if "is_rolled_back" not in ev_dict or ev_dict["is_rolled_back"] is None:
        ev_dict["is_rolled_back"] = 0

    patch_cols = get_table_columns(conn, "patches")
    if not patch_cols:
        return ev_dict, []

    cur = conn.cursor()
    has_patch_adds = "adds" in patch_cols
    has_patch_dels = "dels" in patch_cols
    has_kind = "kind" in patch_cols
    has_size = "size" in patch_cols
    has_hash_before = "hash_before" in patch_cols
    has_hash_after = "hash_after" in patch_cols
    has_diff_content = "diff_content" in patch_cols

    select_cols = ["id", "file_path"]
    if has_patch_adds:
        select_cols.append("adds")
    if has_patch_dels:
        select_cols.append("dels")
    if has_kind:
        select_cols.append("kind")
    if has_size:
        select_cols.append("size")
    if has_hash_before:
        select_cols.append("hash_before")
    if has_hash_after:
        select_cols.append("hash_after")
    if not has_patch_adds and has_diff_content:
        select_cols.append("diff_content")

    cur.execute(
        f"SELECT {', '.join(select_cols)} FROM patches WHERE event_id = ? ORDER BY id ASC",
        (eid,),
    )
    patch_rows = cur.fetchall()

    files = []
    for pr in patch_rows:
        if has_patch_adds and has_patch_dels:
            adds = pr["adds"] or 0
            dels = pr["dels"] or 0
        elif has_diff_content and "diff_content" in pr.keys():
            adds, dels = parse_diff_stats(pr["diff_content"])
        else:
            adds, dels = 0, 0

        diff_text = pr["diff_content"] if (has_diff_content and "diff_content" in pr.keys()) else ""
        size_val = pr["size"] if has_size else (len(diff_text.encode("utf-8")) if diff_text else 0)

        files.append({
            "patch_id": pr["id"],
            "file_path": pr["file_path"],
            "additions": adds,
            "deletions": dels,
            "kind": pr["kind"] if (has_kind and pr["kind"]) else "unified",
            "size": size_val or 0,
            "hash_before": pr["hash_before"] if has_hash_before else None,
            "hash_after": pr["hash_after"] if has_hash_after else None,
        })

    return ev_dict, files


def read_event_file_diff(
    conn: sqlite3.Connection,
    item_id: str | int,
    file_path: str,
) -> dict[str, Any] | None:
    """
    Fetch on-demand unified diff content for a single file in an event.
    Returns diff dictionary or None if not found.
    """
    row = resolve_event_row(conn, item_id)
    if not row:
        return None

    eid = row["id"]
    patch_cols = get_table_columns(conn, "patches")
    if not patch_cols:
        return None

    has_patch_adds = "adds" in patch_cols
    has_patch_dels = "dels" in patch_cols
    has_kind = "kind" in patch_cols
    has_diff_content = "diff_content" in patch_cols

    select_cols = ["id", "file_path"]
    if has_diff_content:
        select_cols.append("diff_content")
    if has_patch_adds:
        select_cols.append("adds")
    if has_patch_dels:
        select_cols.append("dels")
    if has_kind:
        select_cols.append("kind")

    cur = conn.cursor()
    cur.execute(
        f"SELECT {', '.join(select_cols)} FROM patches WHERE event_id = ? AND file_path = ? LIMIT 1",
        (eid, file_path),
    )
    pr = cur.fetchone()
    if not pr:
        return None

    diff_text = pr["diff_content"] if (has_diff_content and "diff_content" in pr.keys()) else ""
    if has_patch_adds and has_patch_dels:
        adds = pr["adds"] or 0
        dels = pr["dels"] or 0
    else:
        adds, dels = parse_diff_stats(diff_text)

    stable_event_id = row["event_id"] if ("event_id" in row.keys() and row["event_id"]) else f"ev_{eid}"

    return {
        "event_id": stable_event_id,
        "file_path": pr["file_path"],
        "diff_content": diff_text or "",
        "additions": adds,
        "deletions": dels,
        "kind": pr["kind"] if (has_kind and pr["kind"]) else "unified",
    }
