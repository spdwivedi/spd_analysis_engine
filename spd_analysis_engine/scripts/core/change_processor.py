"""
scripts/core/change_processor.py
================================
Authoritative single-consumer ChangeProcessor per project.

Key architectural guarantees:
- Exactly one ordered ChangeProcessor consumer per project.
- Serial execution: Burst N and Burst N+1 NEVER overlap.
- Effective-change calculation using fingerprints (before != after),
  correctly detecting empty files, deletions, and binary changes while
  ignoring timestamp-only rewrites.
- Periodic idle manifest scan roughly every 60 seconds.
- Immediate reconciliation on queue overflow, watcher restart,
  sleep/wake gap, or detected event loss.
"""
from __future__ import annotations

import logging
import os
import queue
import threading
import time
from pathlib import Path
from typing import Any, Callable

from ..storage.fingerprint import (
    ChangeResult,
    FileFingerprint,
    compare_fingerprints,
    compute_fingerprint,
)
from ..storage.ignore_policy import IgnorePolicy
from ..storage.manifest import FileManifest
from .changeset import ChangeSet

logger = logging.getLogger(__name__)

DEFAULT_RECONCILE_INTERVAL_S = 60.0
SLEEP_WAKE_GAP_THRESHOLD_S = 5.0


class ChangeProcessor:
    """
    Serial consumer processing ordered bursts and maintaining project state consistency.
    """

    def __init__(
        self,
        root_path: Path | str,
        downstream_queue: queue.Queue[dict[str, Any]],
        manifest: FileManifest | None = None,
        ignore_policy: IgnorePolicy | None = None,
        on_bundle_processed: Callable[[dict[str, Any]], None] | None = None,
        on_effective_change: Callable[[list[ChangeResult]], None] | None = None,
        on_changeset_produced: Callable[[ChangeSet], None] | None = None,
        reconcile_interval_s: float = DEFAULT_RECONCILE_INTERVAL_S,
    ) -> None:
        self.root_path = Path(root_path).resolve()
        self.downstream_queue = downstream_queue
        self.ignore_policy = ignore_policy if ignore_policy is not None else IgnorePolicy(self.root_path)
        self.manifest = manifest if manifest is not None else FileManifest(self.root_path, self.ignore_policy)
        self.on_bundle_processed = on_bundle_processed
        self.on_effective_change = on_effective_change
        self.on_changeset_produced = on_changeset_produced
        self.reconcile_interval_s = max(5.0, float(reconcile_interval_s))

        self._processing_lock = threading.RLock()
        self._running = False
        self._paused = False
        self._thread: threading.Thread | None = None

        now_mono = time.monotonic()
        self._last_activity_mono = now_mono
        self._last_reconcile_mono = now_mono
        self._last_wall_time = time.time()
        self._last_mono_time = now_mono

        self.burst_counter = 0
        self.reconciliation_counter = 0

    # -------------------------------------------------------------------------
    # Lifecycle Management
    # -------------------------------------------------------------------------

    def start(self) -> None:
        """Start the single ordered consumer thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._consumer_loop,
            name=f"ChangeProcessor-{self.root_path.name}",
            daemon=True,
        )
        self._thread.start()

    def stop(self, drain: bool = True, timeout: float = 5.0) -> None:
        """Stop consumer thread, optionally draining any pending bursts."""
        self._running = False
        if drain:
            self.drain(timeout=timeout)

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
            self._thread = None

    def pause(self) -> None:
        """Pause bundle consumption during rollback or maintenance."""
        self._paused = True
        logger.info("ChangeProcessor paused for %s", self.root_path)

    def resume(self) -> None:
        """Resume bundle consumption."""
        self._paused = False
        logger.info("ChangeProcessor resumed for %s", self.root_path)

    @property
    def is_paused(self) -> bool:
        """Return True if consumption is currently paused."""
        return self._paused

    def drain(self, timeout: float = 5.0) -> None:
        """Process all remaining items in downstream_queue."""
        t0 = time.monotonic()
        while not self.downstream_queue.empty():
            try:
                bundle = self.downstream_queue.get_nowait()
                if bundle.get("needs_reconcile") and not bundle.get("_reconciled"):
                    logger.warning("Burst flagged needs_reconcile in drain; executing immediate reconciliation.")
                    self.reconcile(reason="queue_overflow")
                    bundle["_reconciled"] = True
                with self._processing_lock:
                    self._process_single_bundle(bundle)
                self.downstream_queue.task_done()
            except queue.Empty:
                break
            if time.monotonic() - t0 > timeout:
                break

    # -------------------------------------------------------------------------
    # Consumer Loop
    # -------------------------------------------------------------------------

    def _consumer_loop(self) -> None:
        """
        Main loop: consume exactly one bundle at a time in strict FIFO order.
        Never overlaps burst N and burst N+1.
        """
        while self._running:
            if self._paused:
                time.sleep(0.05)
                continue
            # 1. Sleep/wake gap detection
            now_wall = time.time()
            now_mono = time.monotonic()
            wall_delta = now_wall - self._last_wall_time
            mono_delta = now_mono - self._last_mono_time
            if abs(wall_delta - mono_delta) > SLEEP_WAKE_GAP_THRESHOLD_S:
                logger.info("System sleep/wake gap detected (wall: %.1fs, mono: %.1fs). Reconciling.", wall_delta, mono_delta)
                self.reconcile(reason="sleep_wake_gap")
            self._last_wall_time = now_wall
            self._last_mono_time = now_mono

            # 2. Pull next burst bundle
            try:
                bundle = self.downstream_queue.get(timeout=0.2)
            except queue.Empty:
                # Check periodic idle reconciliation
                self._check_periodic_reconciliation(now_mono)
                continue

            self._last_activity_mono = time.monotonic()

            # Immediate reconciliation on queue overflow before acquiring lock
            if bundle.get("needs_reconcile") and not bundle.get("_reconciled"):
                logger.warning("Burst flagged needs_reconcile; executing immediate reconciliation.")
                self.reconcile(reason="queue_overflow")
                bundle["_reconciled"] = True

            # Strictly serialized: Burst N and Burst N+1 NEVER overlap
            with self._processing_lock:
                self._process_single_bundle(bundle)
            self.downstream_queue.task_done()

    def _process_single_bundle(self, bundle: dict[str, Any]) -> None:
        """Process a single burst bundle, updating manifest with fingerprints."""
        self.burst_counter += 1
        bundle["burst_number"] = self.burst_counter

        # Immediate reconciliation on queue overflow fallback
        if bundle.get("needs_reconcile") and not bundle.get("_reconciled"):
            bundle["_reconciled"] = True
            logger.warning("Burst flagged needs_reconcile; executing immediate reconciliation.")
            self.reconcile(reason="queue_overflow")

        files_touched = bundle.get("files_touched", [])
        effective_changes: list[ChangeResult] = []
        before_fps: dict[str, FileFingerprint | None] = {}
        after_fps: dict[str, FileFingerprint | None] = {}

        for rel_path in files_touched:
            abs_path = self.root_path / rel_path
            prev_entry = self.manifest.get(rel_path)
            before_fp = FileFingerprint.from_manifest_entry(prev_entry) if prev_entry else None
            after_fp = compute_fingerprint(abs_path, rel_path)

            before_fps[rel_path] = before_fp
            after_fps[rel_path] = after_fp

            change = compare_fingerprints(before_fp, after_fp, rel_path)
            if change.is_effective:
                effective_changes.append(change)
                if after_fp.exists:
                    self.manifest.record_file(rel_path)
                else:
                    self.manifest.record_deletion(rel_path)

        # Detect potential file renames (deleted hash == created hash)
        renames: list[tuple[str, str]] = []
        created_changes = [c for c in effective_changes if c.change_type == "created" and c.after and c.after.sha256]
        deleted_changes = [c for c in effective_changes if c.change_type == "deleted" and c.before and c.before.sha256]
        for c in created_changes:
            for d in deleted_changes:
                if c.after.sha256 == d.before.sha256 and c.after.size == d.before.size and c.after.size > 0:
                    renames.append((d.relative_path, c.relative_path))
                    break

        # Construct authoritative ChangeSet domain object
        burst_uid = bundle.get("burst_uid") or f"burst_{self.burst_counter}"
        changeset = ChangeSet(
            burst_uid=burst_uid,
            event_id=bundle.get("event_id"),
            sequence=self.burst_counter,
            files=list(files_touched),
            effective_files=[c.relative_path for c in effective_changes],
            renames=renames,
            before_fingerprints=before_fps,
            after_fingerprints=after_fps,
            git_state="pending",
        )

        bundle["changeset"] = changeset
        bundle["effective_changes"] = [c.relative_path for c in effective_changes]
        bundle["change_results"] = effective_changes

        if self.on_changeset_produced:
            try:
                self.on_changeset_produced(changeset)
            except Exception as exc:
                logger.error("Error in on_changeset_produced callback: %s", exc)

        if self.on_bundle_processed:
            try:
                self.on_bundle_processed(bundle)
            except Exception as exc:
                logger.error("Error in on_bundle_processed callback: %s", exc)

        if self.on_effective_change and effective_changes:
            try:
                self.on_effective_change(effective_changes)
            except Exception as exc:
                logger.error("Error in on_effective_change callback: %s", exc)

    # -------------------------------------------------------------------------
    # Reconciliation Engine
    # -------------------------------------------------------------------------

    def _check_periodic_reconciliation(self, now_mono: float) -> None:
        """Trigger periodic manifest scan if idle for >= reconcile_interval_s."""
        idle_duration = now_mono - self._last_activity_mono
        time_since_reconcile = now_mono - self._last_reconcile_mono

        if idle_duration >= self.reconcile_interval_s and time_since_reconcile >= self.reconcile_interval_s:
            self.reconcile(reason="idle_periodic")

    def reconcile(self, reason: str = "manual") -> list[ChangeResult]:
        """
        Execute manifest reconciliation against disk.
        Detects missing/added/modified files and updates manifest state.
        """
        with self._processing_lock:
            self.reconciliation_counter += 1
            self._last_reconcile_mono = time.monotonic()
            logger.info("Executing manifest reconciliation (reason=%s, count=%d)", reason, self.reconciliation_counter)

            changes: list[ChangeResult] = []
            found_paths: set[str] = set()

            for dirpath, dirnames, filenames in os.walk(str(self.root_path)):
                rel_dir = os.path.relpath(dirpath, str(self.root_path))
                norm_rel_dir = "" if rel_dir == "." else IgnorePolicy.normalize_rel_path(rel_dir)

                dirnames[:] = [
                    d for d in dirnames
                    if self.ignore_policy.should_watch(f"{norm_rel_dir}/{d}" if norm_rel_dir else d)
                ]

                for fname in filenames:
                    rel_f = f"{norm_rel_dir}/{fname}" if norm_rel_dir else fname
                    norm_rel = IgnorePolicy.normalize_rel_path(rel_f)

                    if not self.ignore_policy.should_index(norm_rel):
                        continue

                    found_paths.add(norm_rel)
                    abs_p = self.root_path / norm_rel
                    prev_entry = self.manifest.get(norm_rel)
                    before_fp = FileFingerprint.from_manifest_entry(prev_entry) if prev_entry else None
                    after_fp = compute_fingerprint(abs_p, norm_rel)

                    change = compare_fingerprints(before_fp, after_fp, norm_rel)
                    if change.is_effective:
                        changes.append(change)
                        self.manifest.record_file(norm_rel)

            # Detect deleted files in manifest that are no longer on disk
            for known_path, entry in list(self.manifest._entries.items()):
                if entry.exists and known_path not in found_paths:
                    before_fp = FileFingerprint.from_manifest_entry(entry)
                    change = compare_fingerprints(before_fp, None, known_path)
                    changes.append(change)
                    self.manifest.record_deletion(known_path)

            if self.on_effective_change and changes:
                try:
                    self.on_effective_change(changes)
                except Exception as exc:
                    logger.error("Error in on_effective_change during reconciliation: %s", exc)

            return changes

    # -------------------------------------------------------------------------
    # External Reconciliation Triggers
    # -------------------------------------------------------------------------

    def on_watcher_restarted(self) -> list[ChangeResult]:
        """Trigger immediate reconciliation when watcher restarts."""
        return self.reconcile(reason="watcher_restart")

    def on_queue_overflow(self) -> list[ChangeResult]:
        """Trigger immediate reconciliation on queue overflow."""
        return self.reconcile(reason="queue_overflow")

    def on_event_loss_detected(self) -> list[ChangeResult]:
        """Trigger immediate reconciliation when filesystem event loss is detected."""
        return self.reconcile(reason="event_loss")
