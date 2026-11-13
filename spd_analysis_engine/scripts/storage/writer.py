"""
scripts/storage/writer.py - Authoritative single-writer architecture for session.db.
- ProjectWriter thread owns the ONLY write connection for project telemetry.
- Future used for critical writes; non-blocking with drop counter for best-effort writes.
- Batches writes (~50ms or N ops) preserving FIFO ordering. Independent read-only connections.
"""
from __future__ import annotations

import concurrent.futures
import logging
import queue
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from .schema_migration import apply_schema_migrations
from .utils import _utcnow_iso
from .writer_ops import WriteOperation, WriterMetrics, execute_operation
def check_fault(name: str) -> None:
    from ..core.faults import check_fault as _cf
    _cf(name)

logger = logging.getLogger(__name__)


class ProjectWriter:
    """Dedicated single-writer thread for project session.db."""

    def __init__(
        self,
        db_path: Path | str,
        batch_window_s: float = 0.05,
        max_batch_size: int = 500,
        max_queue_size: int = 10000,
    ) -> None:
        self.db_path = Path(db_path).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.batch_window_s = max(0.001, float(batch_window_s))
        self.max_batch_size = max(1, int(max_batch_size))
        self.max_queue_size = max(1, int(max_queue_size))

        self._queue: queue.Queue[WriteOperation] = queue.Queue(maxsize=self.max_queue_size)
        self._seq_counter = 0
        self._seq_lock = threading.Lock()

        self._stop_event = threading.Event()
        self._is_stopping = False

        self._submitted_count = 0
        self._committed_count = 0
        self._dropped_count = 0
        self._total_batches = 0
        self._last_batch_duration_ms = 0.0

        # Open the single write connection
        self._conn: sqlite3.Connection | None = None
        self._open_writer_connection()

        # Start the background writer thread
        self._thread = threading.Thread(
            target=self._writer_loop,
            name=f"ProjectWriter-{self.db_path.parent.name}",
            daemon=True,
        )
        self._thread.start()
        logger.debug("ProjectWriter initialized for %s", self.db_path)

    # -------------------------------------------------------------------------
    # Connection Management
    # -------------------------------------------------------------------------

    def _open_writer_connection(self) -> None:
        """Open the single write connection in WAL mode with busy timeout."""
        self._conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
            isolation_level=None,  # Autocommit mode; explicit BEGIN/COMMIT
            timeout=30.0,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout = 30000;")
        self._conn.execute("PRAGMA journal_mode = WAL;")
        self._conn.execute("PRAGMA synchronous = NORMAL;")
        self._conn.execute("PRAGMA foreign_keys = ON;")
        apply_schema_migrations(self._conn)

    def open_ro_connection(self) -> sqlite3.Connection:
        """
        Open and return an independent read-only SQLite connection in WAL mode.
        Allows concurrent readers without competing for writer locks or touching WAL.
        """
        db_uri = self.db_path.resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(db_uri, uri=True, timeout=30.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 30000;")
        conn.execute("PRAGMA query_only = ON;")
        return conn

    # -------------------------------------------------------------------------
    # Operation Submission
    # -------------------------------------------------------------------------

    def _next_seq(self) -> int:
        with self._seq_lock:
            self._seq_counter += 1
            return self._seq_counter

    def submit_critical(
        self,
        op_type: str,
        payload: dict[str, Any],
        timeout: float | None = None,
    ) -> concurrent.futures.Future:
        """
        Submit a durability-critical write operation.
        Returns a Future that resolves with the operation result or raises an exception.
        Blocks on queue backpressure if queue is full until space is available.
        """
        if self._is_stopping or self._stop_event.is_set():
            fut: concurrent.futures.Future = concurrent.futures.Future()
            fut.set_exception(RuntimeError("ProjectWriter is stopped"))
            return fut

        fut = concurrent.futures.Future()
        op = WriteOperation(
            op_type=op_type,
            payload=payload,
            future=fut,
            is_critical=True,
            sequence=self._next_seq(),
        )
        try:
            put_timeout = 5.0 if (self._is_stopping and timeout is None) else timeout
            self._queue.put(op, block=True, timeout=put_timeout)
            with self._seq_lock:
                self._submitted_count += 1
        except queue.Full:
            fut.set_exception(queue.Full("Write queue full during critical submission"))
        return fut

    def submit_best_effort(self, op_type: str, payload: dict[str, Any]) -> bool:
        """
        Submit a best-effort write operation using non-blocking submission.
        If queue is full or writer is stopping, drops the operation and increments drop counter.
        """
        if self._is_stopping or self._stop_event.is_set():
            with self._seq_lock:
                self._dropped_count += 1
            return False

        op = WriteOperation(
            op_type=op_type,
            payload=payload,
            future=None,
            is_critical=False,
            sequence=self._next_seq(),
        )
        try:
            self._queue.put_nowait(op)
            with self._seq_lock:
                self._submitted_count += 1
            return True
        except queue.Full:
            with self._seq_lock:
                self._dropped_count += 1
            return False

    # -------------------------------------------------------------------------
    # High-Level Synchronous Wrappers (Durability-Critical)
    # -------------------------------------------------------------------------

    def create_session(
        self, project_name: str, target_path: str, end_reason: str | None = None,
        worker_id: str | None = None, config_json: str | None = None,
        actor: str | None = None, timeout: float | None = 10.0,
    ) -> int:
        fut = self.submit_critical("session_create", {
            "project_name": project_name, "target_path": target_path,
            "end_reason": end_reason, "worker_id": worker_id,
            "config_json": config_json, "actor": actor,
        })
        return int(fut.result(timeout=timeout))

    def close_session(
        self, session_id: int, status: str = "STOPPED",
        end_reason: str | None = None, timeout: float | None = 10.0,
    ) -> None:
        fut = self.submit_critical("session_close", {
            "session_id": session_id, "status": status, "end_reason": end_reason
        })
        fut.result(timeout=timeout)

    def record_event(
        self, session_id: int, event_type: str, first_file: str | None = None,
        summary: str | None = None, executor: str | None = None,
        commit_hash: str | None = None, is_rolled_back: int = 0,
        burst_num: int | None = None, event_id: str | None = None,
        seq: int | None = None, burst_uid: str | None = None,
        effective_files: str | None = None, timeout: float | None = 10.0,
    ) -> int:
        fut = self.submit_critical("event", {
            "session_id": session_id, "event_type": event_type, "first_file": first_file,
            "summary": summary, "executor": executor, "commit_hash": commit_hash,
            "is_rolled_back": is_rolled_back, "burst_num": burst_num, "event_id": event_id,
            "seq": seq, "burst_uid": burst_uid, "effective_files": effective_files,
        })
        return int(fut.result(timeout=timeout))

    def record_patch(
        self, event_id: int, file_path: str, diff_content: str | None = None,
        adds: int = 0, dels: int = 0, kind: str = "unified", size: int = 0,
        hash_before: str | None = None, hash_after: str | None = None,
        truncated: int = 0, timeout: float | None = 10.0,
    ) -> int:
        fut = self.submit_critical("patch", {
            "event_id": event_id, "file_path": file_path, "diff_content": diff_content,
            "adds": adds, "dels": dels, "kind": kind, "size": size,
            "hash_before": hash_before, "hash_after": hash_after, "truncated": truncated,
        })
        return int(fut.result(timeout=timeout))

    def record_manifest(
        self, file_path: str, file_hash: str, file_size: int, last_modified: str,
        tracked_status: str = "TRACKED", session_id: int | None = None,
        timeout: float | None = 10.0,
    ) -> str:
        fut = self.submit_critical("manifest", {
            "file_path": file_path, "file_hash": file_hash, "file_size": file_size,
            "last_modified": last_modified, "tracked_status": tracked_status,
            "session_id": session_id,
        })
        return str(fut.result(timeout=timeout))

    def record_git_attribution(
        self, session_id: int, commit_hash: str, author_name: str | None = None,
        author_email: str | None = None, committed_at: str | None = None,
        milestone_id: int | None = None, burst_ids: str | None = None,
        timeout: float | None = 10.0,
    ) -> int:
        fut = self.submit_critical("git_attribution", {
            "session_id": session_id, "commit_hash": commit_hash,
            "author_name": author_name, "author_email": author_email,
            "committed_at": committed_at or _utcnow_iso(),
            "milestone_id": milestone_id, "burst_ids": burst_ids,
        })
        return int(fut.result(timeout=timeout))

    def record_capture_metadata(
        self, key: str, value: str, session_id: int | None = None,
        timeout: float | None = 10.0,
    ) -> str:
        fut = self.submit_critical(
            "capture_meta", {"key": key, "value": value, "session_id": session_id}
        )
        return str(fut.result(timeout=timeout))

    def execute_sql(
        self, sql: str, params: tuple[Any, ...] = (), timeout: float | None = 10.0,
    ) -> Any:
        fut = self.submit_critical("sql", {"sql": sql, "params": params})
        return fut.result(timeout=timeout)

    # -------------------------------------------------------------------------
    # Writer Background Loop & Batch Processing
    # -------------------------------------------------------------------------

    def _writer_loop(self) -> None:
        """Main loop: batches writes approximately every batch_window_s or max_batch_size."""
        while not self._stop_event.is_set() or not self._queue.empty():
            try:
                op = self._queue.get(timeout=self.batch_window_s)
            except queue.Empty:
                continue

            batch = [op]
            deadline = time.perf_counter() + self.batch_window_s
            while len(batch) < self.max_batch_size:
                rem = deadline - time.perf_counter()
                if rem <= 0:
                    break
                try:
                    next_op = self._queue.get_nowait()
                    batch.append(next_op)
                except queue.Empty:
                    break

            self._flush_batch(batch)
            for _ in range(len(batch)):
                self._queue.task_done()

    def _flush_batch(self, batch: list[WriteOperation]) -> None:
        """Execute a single atomic transaction for the given batch of operations."""
        if not batch or self._conn is None:
            return

        t0 = time.perf_counter()
        results: list[tuple[WriteOperation, Any]] = []
        try:
            self._conn.execute("BEGIN IMMEDIATE;")
            check_fault("inside_db_transaction")
            for op in batch:
                res = execute_operation(self._conn, op)
                results.append((op, res))
            self._conn.execute("COMMIT;")

            # Notify success
            for op, res in results:
                if op.future and not op.future.done():
                    op.future.set_result(res)

            with self._seq_lock:
                self._committed_count += len(batch)
        except Exception as exc:
            try:
                self._conn.execute("ROLLBACK;")
            except Exception:
                pass
            # Notify error for all futures in this batch
            for op in batch:
                if op.future and not op.future.done():
                    op.future.set_exception(exc)
        finally:
            self._last_batch_duration_ms = (time.perf_counter() - t0) * 1000.0
            self._total_batches += 1

    # -------------------------------------------------------------------------
    # Drain, Stop, and Lifecycle
    # -------------------------------------------------------------------------

    def drain(self, timeout: float = 10.0) -> None:
        """Wait until all pending items in the queue have been flushed."""
        t0 = time.perf_counter()
        while not self._queue.empty():
            time.sleep(0.005)
            if (time.perf_counter() - t0) > timeout:
                logger.warning("ProjectWriter drain timed out after %.2fs", timeout)
                break
        while getattr(self._queue, "unfinished_tasks", 0) > 0:
            time.sleep(0.005)
            if (time.perf_counter() - t0) > timeout:
                break

    def stop(self, drain: bool = True, timeout: float = 10.0) -> None:
        """Signal thread stop, optionally drain pending writes, and close connection."""
        self._is_stopping = True
        if drain:
            self.drain(timeout=timeout)

        self._stop_event.set()
        if self._thread.is_alive():
            self._thread.join(timeout=timeout)

        if not self._thread.is_alive() and self._conn is not None:
            try:
                self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
            except Exception:
                pass
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None
        logger.debug("ProjectWriter stopped for %s", self.db_path)

    def close(self, drain: bool = True, timeout: float = 10.0) -> None:
        """Alias for stop()."""
        self.stop(drain=drain, timeout=timeout)

    def __enter__(self) -> "ProjectWriter":
        return self

    def __exit__(self, *_: Any) -> None:
        self.stop(drain=True)

    # -------------------------------------------------------------------------
    # Metrics & Observability
    # -------------------------------------------------------------------------

    @property
    def queue_depth(self) -> int:
        return self._queue.qsize()

    @property
    def drop_counter(self) -> int:
        with self._seq_lock:
            return self._dropped_count

    @property
    def is_running(self) -> bool:
        return self._thread.is_alive() and not self._stop_event.is_set()

    def get_metrics(self) -> WriterMetrics:
        with self._seq_lock:
            q_depth = self._queue.qsize()
            sub = self._submitted_count
            com = self._committed_count
            drop = self._dropped_count
            batches = self._total_batches
            last_ms = self._last_batch_duration_ms

        avg_size = (com / batches) if batches > 0 else 0.0
        backpressure = q_depth >= (self.max_queue_size * 0.8)
        return WriterMetrics(
            queue_depth=q_depth,
            max_queue_size=self.max_queue_size,
            total_submitted=sub,
            total_committed=com,
            total_dropped=drop,
            total_batches=batches,
            avg_batch_size=avg_size,
            last_batch_duration_ms=last_ms,
            is_running=self.is_running,
            backpressure_active=backpressure,
        )
