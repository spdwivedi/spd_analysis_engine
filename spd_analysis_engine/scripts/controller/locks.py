"""
scripts/controller/locks.py
===========================
OS-level file locking and in-process mutual exclusion for the authoritative
lifecycle controller layer.

Provides:
- EngineLock: Global lock on engine.lock
- ProjectOperationLock: Per-project operation lock preventing concurrent
  start/stop/resume/rollback/delete operations on the same project.
- OperationLockConflictError: 409-equivalent error raised on contention.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

# Windows vs POSIX file locking
_IS_WINDOWS = sys.platform.startswith("win")
if _IS_WINDOWS:
    import msvcrt
else:
    try:
        import fcntl
    except ImportError:
        fcntl = None  # type: ignore


class OperationLockConflictError(Exception):
    """
    Raised when an operation lock cannot be acquired due to contention
    (HTTP 409 Conflict equivalent).
    """

    status_code: int = 409

    def __init__(self, message: str, project_id: str | None = None, lock_path: Path | None = None):
        super().__init__(message)
        self.project_id = project_id
        self.lock_path = lock_path


class _InProcessLockRegistry:
    """Thread-safe registry mapping canonical lock paths to reentrant threading locks."""

    _guard = threading.Lock()
    _locks: dict[str, threading.RLock] = {}

    @classmethod
    def get_lock(cls, canonical_path: str) -> threading.RLock:
        with cls._guard:
            if canonical_path not in cls._locks:
                cls._locks[canonical_path] = threading.RLock()
            return cls._locks[canonical_path]

    @classmethod
    def reset(cls) -> None:
        """Reset the lock table (primarily for test isolation)."""
        with cls._guard:
            cls._locks.clear()


class FileLock:
    """
    Combined OS-level file lock and in-process thread lock.
    Guarantees strict serialization across processes and threads.
    """

    def __init__(
        self,
        lock_path: Path | str,
        timeout_s: float = 10.0,
        poll_interval_s: float = 0.05,
    ) -> None:
        self.lock_path = Path(lock_path).resolve()
        self.timeout_s = float(timeout_s)
        self.poll_interval_s = float(poll_interval_s)
        self._canonical_str = str(self.lock_path)
        self._thread_lock = _InProcessLockRegistry.get_lock(self._canonical_str)
        self._fd: int | None = None
        self._thread_acquired = False
        self._file_locked = False
        self._owner_thread: int | None = None
        self._reentrant_count: int = 0

    def acquire(self, timeout_s: float | None = None) -> bool:
        """
        Acquire both the in-process thread lock and the OS file lock.
        Raises OperationLockConflictError if lock cannot be acquired within timeout.
        """
        current_tid = threading.get_ident()
        if self._owner_thread == current_tid:
            self._reentrant_count += 1
            return True

        effective_timeout = self.timeout_s if timeout_s is None else float(timeout_s)
        deadline = time.monotonic() + max(0.0, effective_timeout)

        # 1. Acquire in-process thread lock with polling
        thread_acquired = False
        while True:
            # Use non-blocking acquire to allow timeout polling
            if self._thread_lock.acquire(blocking=False):
                thread_acquired = True
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(self.poll_interval_s)

        if not thread_acquired:
            raise OperationLockConflictError(
                f"Thread lock contention on {self.lock_path.name} after {effective_timeout:.2f}s",
                lock_path=self.lock_path,
            )

        self._thread_acquired = True

        # 2. Acquire OS-level file lock
        try:
            self.lock_path.parent.mkdir(parents=True, exist_ok=True)
            self._fd = os.open(str(self.lock_path), os.O_RDWR | os.O_CREAT, 0o666)

            # Ensure file is at least 1 byte so locking has a valid byte range
            if os.path.getsize(str(self.lock_path)) == 0:
                try:
                    os.write(self._fd, b"\0")
                    os.fsync(self._fd)
                except OSError:
                    pass

            file_locked = False
            while True:
                try:
                    os.lseek(self._fd, 0, os.SEEK_SET)
                    if _IS_WINDOWS:
                        msvcrt.locking(self._fd, msvcrt.LK_NBLCK, 1)
                    elif fcntl is not None:
                        fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    file_locked = True
                    break
                except (OSError, IOError):
                    if time.monotonic() >= deadline:
                        break
                    time.sleep(self.poll_interval_s)

            if not file_locked:
                raise OperationLockConflictError(
                    f"OS file lock contention on {self.lock_path.name} after {effective_timeout:.2f}s",
                    lock_path=self.lock_path,
                )

            self._file_locked = True
            self._owner_thread = current_tid
            self._reentrant_count = 1
            return True

        except Exception:
            # Cleanup thread lock on OS lock failure
            if self._fd is not None:
                try:
                    os.close(self._fd)
                except OSError:
                    pass
                self._fd = None
            if self._thread_acquired:
                self._thread_lock.release()
                self._thread_acquired = False
            self._owner_thread = None
            self._reentrant_count = 0
            raise

    def release(self) -> None:
        """Release the OS file lock and the in-process thread lock."""
        current_tid = threading.get_ident()
        if self._owner_thread != current_tid:
            return

        if self._reentrant_count > 1:
            self._reentrant_count -= 1
            return

        self._reentrant_count = 0
        self._owner_thread = None

        # 1. Release OS file lock
        if self._file_locked and self._fd is not None:
            try:
                os.lseek(self._fd, 0, os.SEEK_SET)
                if _IS_WINDOWS:
                    msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
                elif fcntl is not None:
                    fcntl.flock(self._fd, fcntl.LOCK_UN)
            except OSError:
                pass
            finally:
                self._file_locked = False

        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            finally:
                self._fd = None

        # 2. Release thread lock
        if self._thread_acquired:
            self._thread_acquired = False
            self._thread_lock.release()

    def __enter__(self) -> FileLock:
        self.acquire()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.release()


class EngineLock(FileLock):
    """Authoritative global engine lock for engine-wide coordinator operations."""

    def __init__(self, engine_root: Path | str, timeout_s: float = 10.0) -> None:
        path = Path(engine_root).resolve() / "engine.lock"
        super().__init__(lock_path=path, timeout_s=timeout_s)


class ProjectOperationLock(FileLock):
    """
    Per-project operation lock ensuring no two start/stop/resume/rollback/delete
    operations for the same project execute concurrently.
    """

    def __init__(self, engine_root: Path | str, project_id: str, timeout_s: float = 10.0) -> None:
        self.project_id = str(project_id)
        # Store project operation locks in engine_root/locks/
        path = Path(engine_root).resolve() / "locks" / f"project_{self.project_id}.lock"
        super().__init__(lock_path=path, timeout_s=timeout_s)

    def acquire(self, timeout_s: float | None = None) -> bool:
        try:
            return super().acquire(timeout_s=timeout_s)
        except OperationLockConflictError as exc:
            # Attach project_id for rich error context
            raise OperationLockConflictError(
                f"Operation lock conflict for project '{self.project_id}': another operation is in progress",
                project_id=self.project_id,
                lock_path=self.lock_path,
            ) from exc
