"""
scripts/ai/cache_manager.py
===========================
Hierarchical Categorical AI Cache Manager for the SPD Analysis Engine.

Restructures ai_data/cache/ into a project- and session-scoped tree:
  ai_data/cache/<project_slug>/session_<N>/burst_<B>_<diff_hash[:16]>.json
  ai_data/cache/<project_slug>/commands/cmd_<hash[:16]>.json

Maintains full backward-compatible fallback lookup in flat ai_data/cache/
for older legacy cache files.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

try:
    from .cache_queries import CacheQueriesMixin
except (ImportError, ModuleNotFoundError):
    try:
        from spd_analysis_engine.scripts.ai.cache_queries import CacheQueriesMixin
    except (ImportError, ModuleNotFoundError):
        from scripts.ai.cache_queries import CacheQueriesMixin

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


class AICacheManager(CacheQueriesMixin):
    """
    Manages hierarchical disk caching of diff and command AI analyses.
    """

    def __init__(self, cache_dir: Path | str) -> None:
        self.cache_dir = Path(cache_dir).resolve()
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _resolve_paths(
        self,
        diff_hash: str,
        project_name: str | None = None,
        session_id: int | None = None,
        burst_id: int | None = None,
    ) -> tuple[Path, Path]:
        """
        Compute (hierarchical_path, flat_legacy_path) for an analysis payload.
        """
        flat_path = self.cache_dir / f"{diff_hash}.json"
        if not project_name:
            return flat_path, flat_path

        slug = _to_slug(project_name)

        if diff_hash.startswith("cmd_"):
            clean_hash = diff_hash[4:20] if len(diff_hash) >= 20 else diff_hash[4:]
            hier_path = self.cache_dir / slug / "commands" / f"cmd_{clean_hash}.json"
        else:
            clean_hash = diff_hash[:16]
            b_num = burst_id or 1
            if session_id is not None:
                hier_path = self.cache_dir / slug / f"session_{session_id}" / f"burst_{b_num}_{clean_hash}.json"
            else:
                hier_path = self.cache_dir / slug / f"burst_{b_num}_{clean_hash}.json"

        return hier_path, flat_path

    def get(
        self,
        diff_hash: str,
        project_name: str | None = None,
        session_id: int | None = None,
        burst_id: int | None = None,
    ) -> dict[str, Any] | None:
        """
        Retrieve cached analysis. Checks hierarchical path first, then glob,
        then falls back to flat legacy cache file.
        """
        hier_path, flat_path = self._resolve_paths(diff_hash, project_name, session_id, burst_id)

        # 1. Exact hierarchical path
        if hier_path.exists():
            try:
                data = json.loads(hier_path.read_text(encoding="utf-8"))
                data["cached"] = True
                data["diff_hash"] = diff_hash
                logger.debug("Hierarchical cache hit: %s", hier_path.name)
                return data
            except Exception as exc:
                logger.warning("Corrupted cache file %s: %s", hier_path, exc)

        # 2. Session directory glob (in case burst index differs)
        if project_name and session_id is not None and not diff_hash.startswith("cmd_"):
            slug = _to_slug(project_name)
            sess_dir = self.cache_dir / slug / f"session_{session_id}"
            if sess_dir.exists():
                clean_hash = diff_hash[:16]
                for f in sess_dir.glob(f"burst_*_{clean_hash}.json"):
                    try:
                        data = json.loads(f.read_text(encoding="utf-8"))
                        data["cached"] = True
                        data["diff_hash"] = diff_hash
                        logger.debug("Hierarchical session glob cache hit: %s", f.name)
                        return data
                    except Exception:
                        pass

        # 3. Flat legacy cache fallback
        if flat_path.exists():
            try:
                data = json.loads(flat_path.read_text(encoding="utf-8"))
                data["cached"] = True
                data["diff_hash"] = diff_hash
                logger.debug("Legacy flat cache hit: %s", flat_path.name)
                return data
            except Exception as exc:
                logger.warning("Corrupted legacy cache file %s: %s", flat_path, exc)

        return None

    def save(
        self,
        diff_hash: str,
        analysis: dict[str, Any],
        project_name: str | None = None,
        session_id: int | None = None,
        burst_id: int | None = None,
    ) -> None:
        """
        Persist analysis into hierarchical tree and ensure parent directory exists.
        """
        hier_path, flat_path = self._resolve_paths(diff_hash, project_name, session_id, burst_id)
        try:
            hier_path.parent.mkdir(parents=True, exist_ok=True)
            hier_path.write_text(json.dumps(analysis, indent=2), encoding="utf-8")
            logger.debug("Saved to hierarchical cache: %s", hier_path)
        except Exception as exc:
            logger.warning("Could not write hierarchical cache file %s: %s", hier_path, exc)
            try:
                flat_path.write_text(json.dumps(analysis, indent=2), encoding="utf-8")
            except Exception:
                pass

    def _sanitize_file_key(self, file_path: str) -> str:
        """Sanitize path separators and illegal filename characters."""
        import re
        clean = file_path.replace("\\", "_").replace("/", "_").strip("_")
        return re.sub(r"[^\w\.-]", "_", clean)

