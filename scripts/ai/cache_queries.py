"""
scripts/ai/cache_queries.py
===========================
CacheQueriesMixin: per-file and burst-overview cache CRUD operations extracted
from AICacheManager to keep individual modules under 500 lines.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _to_slug(name: str) -> str:
    """Normalize project name into directory slug."""
    try:
        from spd_analysis_engine.scripts.storage_rotator import _slug
        return _slug(name)
    except (ImportError, ModuleNotFoundError):
        try:
            from scripts.storage_rotator import _slug
            return _slug(name)
        except Exception:
            import re
            s = str(name).strip().lower()
            s = re.sub(r"[^\w\s-]", "", s)
            return re.sub(r"[-\s]+", "_", s)


def _collect_burst_ids(burst_id: Any, burst_number: Any, event_id: Any) -> list[Any]:
    """Collate unique burst/event identifiers for candidate cache search."""
    ids: list[Any] = []
    if burst_number is not None:
        ids.append(burst_number)
    if burst_id is not None and burst_id not in ids:
        ids.append(burst_id)
    if event_id is not None and event_id not in ids:
        ids.append(event_id)
    return ids or [0]


class CacheQueriesMixin:
    """Mixin providing per-file diff analysis and burst-overview cache CRUD for AICacheManager."""

    def get_file_analysis(
        self,
        burst_id: int | str,
        file_path: str,
        diff_hash: str,
        session_id: int | str | None = None,
        project_name: str | None = None,
        burst_number: int | str | None = None,
        event_id: int | str | None = None,
    ) -> dict[str, Any] | None:
        """Retrieve cached per-file diff analysis with session_id, burst_number, and event_id isolation."""
        file_slug = self._sanitize_file_key(file_path)
        sess_id = session_id if session_id is not None else 1
        clean_hash = diff_hash[:16] if len(diff_hash) > 16 else diff_hash
        b_ids = _collect_burst_ids(burst_id, burst_number, event_id)

        for b_id in b_ids:
            cache_filename = f"file_analysis_{sess_id}_{b_id}_{file_slug}_{clean_hash}.json"
            legacy_filename = f"{b_id}_{file_slug}_{clean_hash}.json"

            if project_name:
                slug = _to_slug(project_name)
                for fname in (cache_filename, legacy_filename):
                    hier_path = self.cache_dir / slug / "files" / fname
                    if hier_path.exists():
                        try:
                            data = json.loads(hier_path.read_text(encoding="utf-8"))
                            data["cached"] = True
                            return data
                        except Exception:
                            pass
                    sess_path = self.cache_dir / slug / f"session_{sess_id}" / "files" / fname
                    if sess_path.exists():
                        try:
                            data = json.loads(sess_path.read_text(encoding="utf-8"))
                            data["cached"] = True
                            return data
                        except Exception:
                            pass

            for fname in (cache_filename, legacy_filename):
                flat_path = self.cache_dir / fname
                if flat_path.exists():
                    try:
                        data = json.loads(flat_path.read_text(encoding="utf-8"))
                        data["cached"] = True
                        return data
                    except Exception:
                        pass
        return None

    def find_file_analysis(
        self,
        burst_id: int | str | None = None,
        file_path: str | None = None,
        session_id: int | str | None = None,
        project_name: str | None = None,
        diff_hash: str | None = None,
        burst_number: int | str | None = None,
        event_id: int | str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        """Retrieve cached per-file diff analysis with strict session and burst isolation."""
        if isinstance(burst_id, str) and (
            isinstance(file_path, int)
            or (isinstance(file_path, str) and file_path.isdigit())
        ):
            actual_proj = burst_id
            actual_sess = file_path
            actual_burst = session_id
            actual_file = project_name
            project_name = actual_proj
            session_id = actual_sess
            burst_id = actual_burst
            file_path = actual_file

        if "project" in kwargs and not project_name:
            project_name = kwargs["project"]
        if "burst" in kwargs and burst_id is None:
            burst_id = kwargs["burst"]
        if "session" in kwargs and session_id is None:
            session_id = kwargs["session"]
        if "burst_number" in kwargs and burst_number is None:
            burst_number = kwargs["burst_number"]
        if "burst_num" in kwargs and burst_number is None:
            burst_number = kwargs["burst_num"]
        if "event_id" in kwargs and event_id is None:
            event_id = kwargs["event_id"]

        if not file_path:
            return None

        sess_id = session_id if session_id is not None else 1
        b_ids = _collect_burst_ids(burst_id, burst_number, event_id)

        if diff_hash:
            return self.get_file_analysis(
                burst_id=b_ids[0],
                file_path=file_path,
                diff_hash=diff_hash,
                session_id=sess_id,
                project_name=project_name,
                burst_number=burst_number,
                event_id=event_id,
            )

        file_slug = self._sanitize_file_key(file_path)
        candidates: list[Path] = []

        for b_id in b_ids:
            pattern = f"file_analysis_{sess_id}_{b_id}_{file_slug}_*.json"
            legacy_pattern = f"{b_id}_{file_slug}_*.json"

            if project_name:
                slug = _to_slug(project_name)
                files_dir = self.cache_dir / slug / "files"
                if files_dir.exists():
                    candidates.extend(files_dir.glob(pattern))
                    candidates.extend(files_dir.glob(legacy_pattern))
                sess_files_dir = self.cache_dir / slug / f"session_{sess_id}" / "files"
                if sess_files_dir.exists():
                    candidates.extend(sess_files_dir.glob(pattern))

            candidates.extend(self.cache_dir.glob(pattern))
            candidates.extend(self.cache_dir.glob(legacy_pattern))

        if candidates:
            candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            for cand in candidates:
                try:
                    data = json.loads(cand.read_text(encoding="utf-8"))
                    data["cached"] = True
                    return data
                except Exception:
                    pass
        return None

    def save_file_analysis(
        self,
        burst_id: int | str,
        file_path: str,
        diff_hash: str,
        analysis: dict[str, Any],
        session_id: int | str | None = None,
        project_name: str | None = None,
        burst_number: int | str | None = None,
        event_id: int | str | None = None,
    ) -> None:
        """Persist per-file diff analysis with session_id and burst_number isolation."""
        file_slug = self._sanitize_file_key(file_path)
        sess_id = session_id if session_id is not None else 1
        clean_hash = diff_hash[:16] if len(diff_hash) > 16 else diff_hash

        b_primary = burst_number if burst_number is not None else (burst_id if burst_id is not None else 0)
        cache_filenames = [f"file_analysis_{sess_id}_{b_primary}_{file_slug}_{clean_hash}.json"]

        if event_id is not None and str(event_id) != str(b_primary):
            cache_filenames.append(f"file_analysis_{sess_id}_{event_id}_{file_slug}_{clean_hash}.json")

        for cache_filename in cache_filenames:
            flat_path = self.cache_dir / cache_filename
            try:
                flat_path.write_text(json.dumps(analysis, indent=2), encoding="utf-8")
            except Exception as exc:
                logger.warning("Could not write flat file cache %s: %s", flat_path, exc)

            if project_name:
                slug = _to_slug(project_name)
                hier_path = self.cache_dir / slug / "files" / cache_filename
                try:
                    hier_path.parent.mkdir(parents=True, exist_ok=True)
                    hier_path.write_text(json.dumps(analysis, indent=2), encoding="utf-8")
                except Exception as exc:
                    logger.warning("Could not write hierarchical file cache %s: %s", hier_path, exc)

    def delete_file_analysis(
        self,
        burst_id: int | str,
        file_path: str,
        session_id: int | str | None = None,
        project_name: str | None = None,
        burst_number: int | str | None = None,
        event_id: int | str | None = None,
    ) -> None:
        """Delete cached JSON files for a specific (session_id, burst_id, file_path) combination."""
        file_slug = self._sanitize_file_key(file_path)
        sess_id = session_id if session_id is not None else 1
        b_ids = _collect_burst_ids(burst_id, burst_number, event_id)

        patterns: list[str] = []
        for b_id in b_ids:
            patterns.append(f"file_analysis_{sess_id}_{b_id}_{file_slug}_*.json")
            patterns.append(f"{b_id}_{file_slug}_*.json")

        dirs_to_check: list[Path] = [self.cache_dir]
        if project_name:
            slug = _to_slug(project_name)
            dirs_to_check.append(self.cache_dir / slug / "files")
            dirs_to_check.append(self.cache_dir / slug / f"session_{sess_id}" / "files")

        for d in dirs_to_check:
            if d.exists():
                for pat in patterns:
                    for f in d.glob(pat):
                        try:
                            f.unlink(missing_ok=True)
                            logger.debug("Deleted cache file %s", f)
                        except Exception as exc:
                            logger.warning("Failed to delete cache file %s: %s", f, exc)

    def get_burst_overview(
        self,
        burst_id: int | str,
        patch_hash: str,
        session_id: int | str | None = None,
        project_name: str | None = None,
        burst_number: int | str | None = None,
        event_id: int | str | None = None,
    ) -> dict[str, Any] | None:
        """Retrieve cached burst overview with strict session_id and burst_number isolation."""
        sess_id = session_id if session_id is not None else 1
        clean_hash = patch_hash[:16] if len(patch_hash) > 16 else patch_hash

        b_ids = _collect_burst_ids(burst_id, burst_number, event_id)

        for b_id in b_ids:
            cache_filename = f"burst_overview_{sess_id}_{b_id}_{clean_hash}.json"
            legacy_filename = f"overview_{b_id}_{clean_hash}.json"

            if project_name:
                slug = _to_slug(project_name)
                for fname in (cache_filename, legacy_filename):
                    hier_path = self.cache_dir / slug / "overviews" / fname
                    if hier_path.exists():
                        try:
                            data = json.loads(hier_path.read_text(encoding="utf-8"))
                            data["cached"] = True
                            return data
                        except Exception:
                            pass
                    sess_path = self.cache_dir / slug / f"session_{sess_id}" / fname
                    if sess_path.exists():
                        try:
                            data = json.loads(sess_path.read_text(encoding="utf-8"))
                            data["cached"] = True
                            return data
                        except Exception:
                            pass

            for fname in (cache_filename, legacy_filename):
                flat_path = self.cache_dir / fname
                if flat_path.exists():
                    try:
                        data = json.loads(flat_path.read_text(encoding="utf-8"))
                        data["cached"] = True
                        return data
                    except Exception:
                        pass
        return None

    def find_burst_overview(
        self,
        burst_id: int | str | None = None,
        session_id: int | str | None = None,
        project_name: str | None = None,
        patch_hash: str | None = None,
        burst_number: int | str | None = None,
        event_id: int | str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        """Retrieve cached burst overview with strict session and burst isolation."""
        if isinstance(burst_id, str) and (
            isinstance(session_id, int)
            or (isinstance(session_id, str) and session_id.isdigit())
        ):
            pass  # positional compat handled via kwargs below

        if "project" in kwargs and not project_name:
            project_name = kwargs["project"]
        if "burst" in kwargs and burst_id is None:
            burst_id = kwargs["burst"]
        if "session" in kwargs and session_id is None:
            session_id = kwargs["session"]
        if "burst_number" in kwargs and burst_number is None:
            burst_number = kwargs["burst_number"]
        if "burst_num" in kwargs and burst_number is None:
            burst_number = kwargs["burst_num"]
        if "event_id" in kwargs and event_id is None:
            event_id = kwargs["event_id"]

        sess_id = session_id if session_id is not None else 1
        b_ids = _collect_burst_ids(burst_id, burst_number, event_id)

        if patch_hash:
            return self.get_burst_overview(
                burst_id=b_ids[0],
                patch_hash=patch_hash,
                session_id=sess_id,
                project_name=project_name,
                burst_number=burst_number,
                event_id=event_id,
            )

        candidates: list[Path] = []
        for b_id in b_ids:
            pattern = f"burst_overview_{sess_id}_{b_id}_*.json"
            legacy_pattern = f"overview_{b_id}_*.json"

            if project_name:
                slug = _to_slug(project_name)
                overviews_dir = self.cache_dir / slug / "overviews"
                if overviews_dir.exists():
                    candidates.extend(overviews_dir.glob(pattern))
                    candidates.extend(overviews_dir.glob(legacy_pattern))
                sess_dir = self.cache_dir / slug / f"session_{sess_id}"
                if sess_dir.exists():
                    candidates.extend(sess_dir.glob(pattern))
                    candidates.extend(sess_dir.glob(legacy_pattern))

            candidates.extend(self.cache_dir.glob(pattern))
            candidates.extend(self.cache_dir.glob(legacy_pattern))

        if candidates:
            candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            for cand in candidates:
                try:
                    data = json.loads(cand.read_text(encoding="utf-8"))
                    data["cached"] = True
                    return data
                except Exception:
                    pass
        return None

    def save_burst_overview(
        self,
        burst_id: int | str,
        patch_hash: str,
        overview: dict[str, Any],
        session_id: int | str | None = None,
        project_name: str | None = None,
        burst_number: int | str | None = None,
        event_id: int | str | None = None,
    ) -> None:
        """Persist burst overview with session_id and burst_number isolation."""
        sess_id = session_id if session_id is not None else 1
        clean_hash = patch_hash[:16] if len(patch_hash) > 16 else patch_hash

        b_primary = burst_number if burst_number is not None else (burst_id if burst_id is not None else 0)
        cache_filenames = [f"burst_overview_{sess_id}_{b_primary}_{clean_hash}.json"]

        if event_id is not None and str(event_id) != str(b_primary):
            cache_filenames.append(f"burst_overview_{sess_id}_{event_id}_{clean_hash}.json")

        for cache_filename in cache_filenames:
            flat_path = self.cache_dir / cache_filename
            try:
                flat_path.write_text(json.dumps(overview, indent=2), encoding="utf-8")
            except Exception as exc:
                logger.warning("Could not write flat burst overview cache %s: %s", flat_path, exc)

            if project_name:
                slug = _to_slug(project_name)
                hier_path = self.cache_dir / slug / "overviews" / cache_filename
                try:
                    hier_path.parent.mkdir(parents=True, exist_ok=True)
                    hier_path.write_text(json.dumps(overview, indent=2), encoding="utf-8")
                except Exception as exc:
                    logger.warning("Could not write hierarchical burst overview cache %s: %s", hier_path, exc)

    def delete_burst_overview(
        self,
        burst_id: int | str,
        session_id: int | str | None = None,
        project_name: str | None = None,
        burst_number: int | str | None = None,
        event_id: int | str | None = None,
    ) -> None:
        """Delete cached JSON files for a specific (session_id, burst_id) burst overview."""
        sess_id = session_id if session_id is not None else 1
        b_ids = _collect_burst_ids(burst_id, burst_number, event_id)

        patterns: list[str] = []
        for b_id in b_ids:
            patterns.append(f"burst_overview_{sess_id}_{b_id}_*.json")
            patterns.append(f"overview_{b_id}_*.json")

        dirs_to_check: list[Path] = [self.cache_dir]
        if project_name:
            slug = _to_slug(project_name)
            dirs_to_check.append(self.cache_dir / slug / "overviews")
            dirs_to_check.append(self.cache_dir / slug / f"session_{sess_id}")

        for d in dirs_to_check:
            if d.exists():
                for pat in patterns:
                    for f in d.glob(pat):
                        try:
                            f.unlink(missing_ok=True)
                            logger.debug("Deleted burst overview cache file %s", f)
                        except Exception as exc:
                            logger.warning("Failed to delete burst overview cache file %s: %s", f, exc)
