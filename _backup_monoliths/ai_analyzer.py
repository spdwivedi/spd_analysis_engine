"""
scripts/ai_analyzer.py
======================
On-Demand AI Intelligence Layer for the SPD Analysis Engine (Phase 5).

Analyzes unified diffs and file patches to generate plain-English intent,
granular modification summaries, and functional capability gains.

Features
--------
1. On-Demand Only: Consumes zero tokens/system resources until explicitly invoked
   via Web Portal UI ("Analyze with AI" button) or REST API.
2. Dual Provider Support:
   - Ollama: Local, offline inference (e.g. qwen2.5-coder:7b) via http://localhost:11434.
   - Gemini: Cloud inference via official Google GenAI SDK (gemini-3.8-flash / gemini-2.0-flash).
3. SHA-256 Diff Caching: Caches analyses in ``ai_data/cache/<sha256>.json`` to prevent
   redundant model invocations for identical code changes.
4. Smart Diff Chunking: Truncates oversized diffs cleanly while preserving structural context.
5. Offline Resilient: Graceful fallback if neither AI provider is reachable.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

try:
    from .ai import (
        RateLimitManager,
        AICacheManager,
        is_sanitized_patch,
        detect_tech_stack,
        generate_directory_tree,
        chunk_diff,
        build_burst_prompt,
        build_command_prompt,
        call_gemini,
        test_gemini_connection,
        call_ollama,
        test_ollama_connection,
    )
except (ImportError, ModuleNotFoundError):
    try:
        from spd_analysis_engine.scripts.ai import (
            RateLimitManager,
            AICacheManager,
            is_sanitized_patch,
            detect_tech_stack,
            generate_directory_tree,
            chunk_diff,
            build_burst_prompt,
            build_command_prompt,
            call_gemini,
            test_gemini_connection,
            call_ollama,
            test_ollama_connection,
        )
    except (ImportError, ModuleNotFoundError):
        from scripts.ai import (
            RateLimitManager,
            AICacheManager,
            is_sanitized_patch,
            detect_tech_stack,
            generate_directory_tree,
            chunk_diff,
            build_burst_prompt,
            build_command_prompt,
            call_gemini,
            test_gemini_connection,
            call_ollama,
            test_ollama_connection,
        )


_IST = timezone(timedelta(hours=5, minutes=30))


def _now_ist_iso() -> str:
    """Return ISO-8601 formatted timestamp in IST (UTC+5:30)."""
    return datetime.now(_IST).strftime("%Y-%m-%dT%H:%M:%S+05:30")


def _now_ist_header() -> str:
    """Return standard release header timestamp in IST (e.g. 2026-09-24 23:14 IST)."""
    return datetime.now(_IST).strftime("%Y-%m-%d %H:%M IST")


SANITIZATION_IGNORE_PATTERNS: tuple[str, ...] = (
    ".sf/", ".sf\\", "/.sf/", "\\.sf\\",
    ".sfdx/", ".sfdx\\", "/.sfdx/", "\\.sfdx\\",
    "catalog.json",
    ".__staging__",
    ".tmp",
    "node_modules/", "node_modules\\",
    "__pycache__/", "__pycache__\\",
    ".git/", ".git\\",
)


def is_sanitized_patch(file_path: str) -> bool:
    """Return True if patch file is clean project source code, False if noise/temporary artifact."""
    if not file_path:
        return False
    fp = file_path.lower().replace("\\", "/")
    if fp.startswith(".sf/") or fp.startswith(".sfdx/") or fp.startswith("node_modules/") or fp.startswith("__pycache__/") or fp.startswith(".git/"):
        return False
    if any(seg in fp for seg in ("/.sf/", "/.sfdx/", "/node_modules/", "/__pycache__/", "/.git/")):
        return False
    if "catalog.json" in fp or ".__staging__" in fp or fp.endswith(".tmp"):
        return False
    return True


class AISynthesizer:
    """
    On-Demand AI Intelligence synthesizer for code diffs and burst events.

    Parameters
    ----------
    engine_root : Path | str | None
        Root directory of ``spd_analysis_engine``. If None, auto-detected from file location.
    """

    @staticmethod
    def detect_tech_stack(workspace_path: Path | str | None) -> str:
        """Detect workspace tech stack from project indicator files."""
        if not workspace_path:
            return "Generic Software Codebase"
        wp = Path(workspace_path)
        if not wp.exists() or not wp.is_dir():
            return "Generic Software Codebase"

        indicators: list[str] = []

        # JavaScript / Node / Web
        pkg_json = wp / "package.json"
        if pkg_json.exists():
            try:
                data = json.loads(pkg_json.read_text(encoding="utf-8", errors="replace"))
                deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
                if "react" in deps:
                    indicators.append("React")
                elif "vue" in deps:
                    indicators.append("Vue")
                elif "svelte" in deps:
                    indicators.append("Svelte")
                elif "next" in deps:
                    indicators.append("Next.js")
                if "vite" in deps:
                    indicators.append("Vite")
                if "typescript" in deps:
                    indicators.append("TypeScript")
                elif not indicators:
                    indicators.append("Node.js")
            except Exception:
                indicators.append("Node.js")
        elif any(wp.glob("*.html")) or any((wp / "web").glob("*.html") if (wp / "web").exists() else ()):
            indicators.append("Vanilla HTML5/CSS3/JavaScript")

        # Python
        py_indicators = [wp / "requirements.txt", wp / "pyproject.toml", wp / "setup.py"]
        if any(p.exists() for p in py_indicators) or any(wp.glob("*.py")) or (wp / "scripts").exists():
            indicators.append("Python")

        # Rust, Go, Java, Salesforce
        if (wp / "Cargo.toml").exists():
            indicators.append("Rust")
        if (wp / "go.mod").exists():
            indicators.append("Go")
        if (wp / "pom.xml").exists() or (wp / "build.gradle").exists():
            indicators.append("Java")
        if (wp / "sfdx-project.json").exists() or (wp / ".sf").exists():
            indicators.append("Salesforce DX / Apex")

        if not indicators:
            return "Polyglot Software Project"
        return " + ".join(indicators)

    @staticmethod
    def generate_directory_tree(workspace_path: Path | str | None, max_depth: int = 2) -> str:
        """Generate lightweight 2-level directory tree of the workspace, excluding ignore patterns."""
        if not workspace_path:
            return "(Workspace directory tree not available)"
        wp = Path(workspace_path)
        if not wp.exists() or not wp.is_dir():
            return f"{wp.name}/ (Path not found)"

        ignore_set = {
            ".git", ".sf", ".sfdx", "node_modules", "__pycache__", ".venv", "venv",
            ".idea", ".vscode", "trash", "ai_data", ".pytest_cache", ".mypy_cache",
            "current", "last_run", "history", "dist", "build"
        }

        tree_lines = [f"{wp.name}/"]

        def _walk(curr: Path, depth: int, prefix: str) -> None:
            if depth > max_depth:
                return
            try:
                items = sorted(curr.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
            except Exception:
                return

            filtered = [
                it for it in items
                if it.name not in ignore_set
                and not it.name.startswith(".__staging__")
                and not it.name.endswith(".tmp")
                and not (it.name.startswith(".") and it.is_dir())
            ]

            for i, item in enumerate(filtered[:18]):
                is_last = (i == len(filtered) - 1 or i == 17)
                connector = "└── " if is_last else "├── "
                suffix = "/" if item.is_dir() else ""
                tree_lines.append(f"{prefix}{connector}{item.name}{suffix}")
                if item.is_dir() and depth < max_depth:
                    sub_prefix = prefix + ("    " if is_last else "│   ")
                    _walk(item, depth + 1, sub_prefix)
            if len(filtered) > 18:
                tree_lines.append(f"{prefix}└── ... [{len(filtered) - 18} more items]")

        _walk(wp, 1, "")
        return "\n".join(tree_lines[:40])

    rate_limiter: RateLimitManager = RateLimitManager(max_rpm=15)

    def __init__(self, engine_root: Path | str | None = None) -> None:
        if engine_root is None:
            self.engine_root = Path(__file__).resolve().parent.parent
        else:
            self.engine_root = Path(engine_root).resolve()

        self.ai_data_dir = self.engine_root / "ai_data"
        self.config_path = self.ai_data_dir / "models_config.json"
        self.cache_dir = self.ai_data_dir / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_mgr = AICacheManager(self.cache_dir)

        self._config: dict[str, Any] = self.load_config(self.engine_root)

    @staticmethod
    def get_config_path(engine_root: Path | str | None = None) -> Path:
        if engine_root is None:
            engine_root = Path(__file__).resolve().parent.parent
        return Path(engine_root) / "ai_data" / "models_config.json"

    @staticmethod
    def _load_env_file(engine_root: Path | str | None = None) -> dict[str, str]:
        """Load key-value pairs from ai_data/.env, project .env, or parent .env without external libraries."""
        if engine_root is None:
            engine_root = Path(__file__).resolve().parent.parent
        root = Path(engine_root)
        candidates = [
            root / "ai_data" / ".env",
            root / ".env",
            root.parent / ".env",
        ]
        env_vars: dict[str, str] = {}
        for cand in candidates:
            if cand.exists() and cand.is_file():
                try:
                    for line in cand.read_text(encoding="utf-8", errors="replace").splitlines():
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        if "=" in line:
                            k, v = line.split("=", 1)
                            k = k.strip()
                            v = v.strip().strip("'\"")
                            if k and k not in env_vars:
                                env_vars[k] = v
                except Exception as exc:
                    logger.debug("Failed to read %s: %s", cand, exc)
        return env_vars

    @classmethod
    def load_config(cls, engine_root: Path | str | None = None) -> dict[str, Any]:
        """Load or reload model configuration adhering to strict credential precedence."""
        config_path = cls.get_config_path(engine_root)
        default_cfg: dict[str, Any] = {
            "default_provider": "gemini" if os.environ.get("GEMINI_API_KEY") else "heuristic",
            "preferred_provider": "gemini" if os.environ.get("GEMINI_API_KEY") else "heuristic",
            "ollama": {
                "enabled": True,
                "base_url": "http://127.0.0.1:11434",
                "default_model": "qwen2.5-coder:7b",
                "timeout_seconds": 60,
                "options": {"temperature": 0.2, "top_p": 0.9, "num_ctx": 4096},
            },
            "gemini": {
                "enabled": True,
                "api_key": "",
                "default_model": "gemini-3.1-flash-lite",
                "timeout_seconds": 30,
                "options": {"temperature": 0.2, "max_output_tokens": 8192},
            },
            "analysis": {
                "max_diff_lines_for_summary": 250,
            },
        }

        raw_config_key = ""
        saved_keys: list[dict[str, Any]] = []
        if config_path.exists():
            try:
                data = json.loads(config_path.read_text(encoding="utf-8"))
                for k, v in data.items():
                    if isinstance(v, dict) and isinstance(default_cfg.get(k), dict):
                        default_cfg[k].update(v)
                    else:
                        default_cfg[k] = v

                gem_sub = data.get("gemini", {})
                if isinstance(gem_sub, dict):
                    raw_config_key = str(gem_sub.get("api_key", "")).strip()
                if not raw_config_key and "gemini_api_key" in data:
                    raw_config_key = str(data["gemini_api_key"]).strip()

                saved_raw = data.get("saved_gemini_keys") or data.get("saved_keys") or []
                if isinstance(saved_raw, list):
                    saved_keys = [dict(x) for x in saved_raw if isinstance(x, dict)]
            except Exception as exc:
                logger.warning("Failed to parse models_config.json: %s; using defaults.", exc)

        # Strict Credential Precedence:
        # 1. models_config.json (explicitly saved by user via UI or file)
        # 2. .env file (ai_data/.env or root .env)
        # 3. os.environ["GEMINI_API_KEY"]
        resolved_key = ""
        key_source = "none"

        if raw_config_key and not raw_config_key.startswith("__REPLACE_"):
            resolved_key = raw_config_key
            key_source = "models_config.json"
        else:
            env_file_vars = cls._load_env_file(engine_root)
            env_file_key = env_file_vars.get("GEMINI_API_KEY", "").strip()
            if env_file_key and not env_file_key.startswith("__REPLACE_"):
                resolved_key = env_file_key
                key_source = ".env"
            else:
                os_key = os.environ.get("GEMINI_API_KEY", "").strip()
                if os_key and not os_key.startswith("__REPLACE_"):
                    resolved_key = os_key
                    key_source = "os.environ"

        # Mirror convenience keys
        pref = default_cfg.get("preferred_provider") or default_cfg.get("default_provider", "heuristic")
        default_cfg["default_provider"] = pref
        default_cfg["preferred_provider"] = pref

        ollama_cfg = default_cfg.setdefault("ollama", {})
        default_cfg["ollama_base_url"] = ollama_cfg.get("base_url", "http://127.0.0.1:11434")
        default_cfg["ollama_model"] = ollama_cfg.get("default_model", "qwen2.5-coder:7b")

        gem_cfg = default_cfg.setdefault("gemini", {})
        gem_cfg["api_key"] = resolved_key
        if gem_cfg.get("default_model") == "gemini-2.5-flash":
            gem_cfg["default_model"] = "gemini-3.1-flash-lite"

        default_cfg["gemini_api_key"] = resolved_key
        default_cfg["gemini_model"] = gem_cfg.get("default_model", "gemini-3.1-flash-lite")
        default_cfg["active_key_source"] = key_source
        default_cfg["active_key_suffix"] = f"...{resolved_key[-4:]}" if len(resolved_key) >= 4 else ("(not set)" if not resolved_key else resolved_key)

        # Ensure raw_config_key from models_config.json is registered in saved_gemini_keys vault
        if raw_config_key and not raw_config_key.startswith("__REPLACE_"):
            existing = next((k for k in saved_keys if k.get("key") == raw_config_key), None)
            if existing:
                if not existing.get("id"):
                    existing["id"] = f"key_{uuid.uuid4().hex[:8]}"
            else:
                sfx = f"...{raw_config_key[-4:]}" if len(raw_config_key) >= 4 else raw_config_key
                saved_keys.insert(0, {
                    "id": f"key_{uuid.uuid4().hex[:8]}",
                    "label": f"Account ({sfx})",
                    "key": raw_config_key,
                    "added_at": _now_ist_iso(),
                    "last_used": _now_ist_iso(),
                })

        default_cfg["saved_gemini_keys"] = saved_keys
        default_cfg["saved_keys"] = saved_keys

        return default_cfg

    @classmethod
    def mask_config(cls, cfg: dict[str, Any]) -> dict[str, Any]:
        """Return config with any sensitive Gemini API keys masked and suffix exposed."""
        copied = json.loads(json.dumps(cfg))
        gem_cfg = copied.get("gemini", {})
        api_key = gem_cfg.get("api_key", "")
        if api_key and "__REPLACE_" not in api_key:
            if len(api_key) <= 8:
                gem_cfg["api_key"] = "****"
            else:
                gem_cfg["api_key"] = f"{api_key[:4]}****{api_key[-4:]}"

        top_key = copied.get("gemini_api_key", "")
        if top_key and "__REPLACE_" not in top_key:
            if len(top_key) <= 8:
                copied["gemini_api_key"] = "****"
            else:
                copied["gemini_api_key"] = f"{top_key[:4]}****{top_key[-4:]}"

        copied["active_key_suffix"] = cfg.get("active_key_suffix", "(not set)")
        copied["active_key_source"] = cfg.get("active_key_source", "none")

        # Mask keys in vault
        masked_vault = []
        act_key = cfg.get("gemini_api_key", "")
        for item in cfg.get("saved_gemini_keys", []):
            raw_k = str(item.get("key", "")).strip()
            if not raw_k or raw_k.startswith("__REPLACE_"):
                continue
            sfx = f"...{raw_k[-4:]}" if len(raw_k) >= 4 else raw_k
            masked = f"{raw_k[:4]}****{raw_k[-4:]}" if len(raw_k) > 8 else "****"
            is_active = bool(raw_k and (raw_k == act_key or raw_k == cfg.get("gemini", {}).get("api_key", "")))
            masked_vault.append({
                "id": item.get("id") or f"key_{uuid.uuid4().hex[:8]}",
                "label": item.get("label") or f"Account ({sfx})",
                "suffix": sfx,
                "masked_key": masked,
                "added_at": item.get("added_at") or _now_ist_iso(),
                "last_used": item.get("last_used") or _now_ist_iso(),
                "active": is_active,
            })
        copied["saved_gemini_keys"] = masked_vault
        copied["saved_keys"] = masked_vault

        return copied

    @classmethod
    def save_config(cls, config_dict: dict[str, Any], engine_root: Path | str | None = None) -> dict[str, Any]:
        """Save updated configuration to models_config.json."""
        current = cls.load_config(engine_root)
        config_path = cls.get_config_path(engine_root)

        # Update preferred_provider
        pref = config_dict.get("preferred_provider") or config_dict.get("default_provider")
        if pref:
            clean_pref = str(pref).strip().lower()
            current["default_provider"] = clean_pref
            current["preferred_provider"] = clean_pref

        # Update ollama
        ollama_cur = current.setdefault("ollama", {})
        if "ollama_base_url" in config_dict:
            ollama_cur["base_url"] = str(config_dict["ollama_base_url"]).strip().rstrip("/")
            current["ollama_base_url"] = ollama_cur["base_url"]
        if "ollama_model" in config_dict:
            ollama_cur["default_model"] = str(config_dict["ollama_model"]).strip()
            current["ollama_model"] = ollama_cur["default_model"]

        if "ollama" in config_dict and isinstance(config_dict["ollama"], dict):
            for k, v in config_dict["ollama"].items():
                if k == "base_url":
                    ollama_cur["base_url"] = str(v).strip().rstrip("/")
                    current["ollama_base_url"] = ollama_cur["base_url"]
                elif k == "default_model":
                    ollama_cur["default_model"] = str(v).strip()
                    current["ollama_model"] = ollama_cur["default_model"]
                elif k in ("enabled", "timeout_seconds"):
                    ollama_cur[k] = v

        # Key Vault operations
        gem_cur = current.setdefault("gemini", {})
        saved_keys = list(current.get("saved_gemini_keys", []))

        # 1. Delete key if requested
        if "delete_key_id" in config_dict and config_dict["delete_key_id"]:
            del_id = str(config_dict["delete_key_id"]).strip()
            del_item = None
            for idx, k in enumerate(saved_keys):
                if k.get("id") == del_id:
                    del_item = saved_keys.pop(idx)
                    break
            if del_item and del_item.get("key") == current.get("gemini_api_key"):
                if saved_keys:
                    nxt = saved_keys[0].get("key", "")
                    current["gemini_api_key"] = nxt
                    gem_cur["api_key"] = nxt
                    current["active_key_suffix"] = f"...{nxt[-4:]}" if len(nxt) >= 4 else nxt
                else:
                    current["gemini_api_key"] = ""
                    gem_cur["api_key"] = ""
                    current["active_key_suffix"] = "(not set)"

        # 2. Select existing key from vault if requested
        if "select_key_id" in config_dict and config_dict["select_key_id"]:
            sel_id = str(config_dict["select_key_id"]).strip()
            for k in saved_keys:
                if k.get("id") == sel_id:
                    k["last_used"] = _now_ist_iso()
                    sel_key = k.get("key", "")
                    current["gemini_api_key"] = sel_key
                    gem_cur["api_key"] = sel_key
                    current["active_key_source"] = "models_config.json"
                    current["active_key_suffix"] = f"...{sel_key[-4:]}" if len(sel_key) >= 4 else sel_key
                    break

        # 3. New raw key passed
        if "gemini_api_key" in config_dict:
            val = str(config_dict["gemini_api_key"]).strip()
            if val and "****" not in val:
                gem_cur["api_key"] = val
                current["gemini_api_key"] = val
                current["active_key_source"] = "models_config.json"
                current["active_key_suffix"] = f"...{val[-4:]}" if len(val) >= 4 else val
                existing = next((k for k in saved_keys if k.get("key") == val), None)
                if existing:
                    existing["last_used"] = _now_ist_iso()
                else:
                    sfx = f"...{val[-4:]}" if len(val) >= 4 else val
                    label = config_dict.get("key_label") or f"Account ({sfx})"
                    saved_keys.insert(0, {
                        "id": f"key_{uuid.uuid4().hex[:8]}",
                        "label": label,
                        "key": val,
                        "added_at": _now_ist_iso(),
                        "last_used": _now_ist_iso(),
                    })
            elif val == "" and not config_dict.get("select_key_id"):
                gem_cur["api_key"] = ""
                current["gemini_api_key"] = ""
                current["active_key_suffix"] = "(not set)"

        if "gemini_model" in config_dict:
            gem_cur["default_model"] = str(config_dict["gemini_model"]).strip()
            current["gemini_model"] = gem_cur["default_model"]

        if "gemini" in config_dict and isinstance(config_dict["gemini"], dict):
            for k, v in config_dict["gemini"].items():
                if k == "api_key":
                    if "gemini_api_key" not in config_dict and not config_dict.get("select_key_id"):
                        val = str(v).strip()
                        if val and "****" not in val:
                            gem_cur["api_key"] = val
                            current["gemini_api_key"] = val
                        elif val == "":
                            gem_cur["api_key"] = ""
                            current["gemini_api_key"] = ""
                elif k == "default_model":
                    gem_cur["default_model"] = str(v).strip()
                    current["gemini_model"] = gem_cur["default_model"]
                elif k in ("enabled", "timeout_seconds"):
                    gem_cur[k] = v

        current["saved_gemini_keys"] = saved_keys
        current["saved_keys"] = saved_keys

        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps(current, indent=2), encoding="utf-8")
        logger.info("Saved AI model configuration to %s", config_path)
        return current

    @classmethod
    def test_ollama_connection(
        cls,
        base_url: str | None = None,
        model: str | None = None,
        engine_root: Path | str | None = None,
    ) -> dict[str, Any]:
        """Test connection to an Ollama instance by querying /api/tags."""
        clean_url = (base_url or "http://127.0.0.1:11434").rstrip("/")
        url = f"{clean_url}/api/tags"

        req = urllib.request.Request(
            url,
            headers={"Accept": "application/json", "User-Agent": "SPD-Analysis-Engine"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                models = [m.get("name") for m in data.get("models", []) if m.get("name")]
                return {
                    "success": True,
                    "message": f"Connected to Ollama at {clean_url}.",
                    "models": models,
                    "installed_models": models,
                    "model_found": (model in models) if model else True,
                }
        except urllib.error.URLError as url_err:
            return {
                "success": False,
                "message": f"Could not connect to Ollama at {clean_url} ({url_err.reason}). Ensure 'ollama serve' is running.",
                "models": [],
                "installed_models": [],
            }
        except Exception as exc:
            return {
                "success": False,
                "message": f"Error connecting to Ollama: {exc}",
                "models": [],
                "installed_models": [],
            }

    @classmethod
    def test_gemini_connection(
        cls,
        api_key: str | None = None,
        key_id: str | None = None,
        model: str | None = None,
        engine_root: Path | str | None = None,
    ) -> dict[str, Any]:
        """Test Gemini API connection by querying the models list endpoint."""
        clean_key = ""
        if key_id:
            cfg = cls.load_config(engine_root)
            for k in cfg.get("saved_gemini_keys", []):
                if k.get("id") == key_id:
                    clean_key = str(k.get("key", "")).strip()
                    break

        if not clean_key and api_key and "****" not in api_key:
            clean_key = api_key.strip()

        if not clean_key:
            cfg = cls.load_config(engine_root)
            clean_key = str(cfg.get("gemini_api_key", "")).strip()

        if not clean_key or "__REPLACE_" in clean_key:
            return {
                "success": False,
                "message": "Gemini API key is required to test connection.",
                "models": [],
            }

        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={clean_key}"
        req = urllib.request.Request(
            url,
            headers={"Accept": "application/json", "User-Agent": "SPD-Analysis-Engine"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=8.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                raw_models = data.get("models", [])
                models = [
                    m["name"].replace("models/", "")
                    for m in raw_models
                    if "gemini" in m.get("name", "").lower()
                ]
                return {
                    "success": True,
                    "message": "Gemini API connection successful! Valid Gemini API key.",
                    "models": models[:12],
                }
        except urllib.error.HTTPError as http_err:
            err_body = ""
            try:
                err_body = http_err.read().decode("utf-8")
            except Exception:
                pass
            scrubbed = err_body.replace(clean_key, "***") if clean_key else err_body
            msg = f"HTTP {http_err.code}"
            try:
                err_json = json.loads(scrubbed)
                msg = err_json.get("error", {}).get("message") or msg
            except Exception:
                pass
            return {
                "success": False,
                "message": f"Gemini connection failed ({http_err.code}): {msg}",
                "models": [],
            }
        except Exception as exc:
            scrubbed_exc = str(exc).replace(clean_key, "***") if clean_key else str(exc)
            return {
                "success": False,
                "message": f"Error connecting to Gemini API: {scrubbed_exc}",
                "models": [],
            }

    # -----------------------------------------------------------------------
    # Diff Caching & Chunking
    # -----------------------------------------------------------------------

    def compute_diff_hash(self, diff_text: str) -> str:
        """Compute a deterministic SHA-256 hash of normalized diff content."""
        normalized = diff_text.strip().encode("utf-8")
        return hashlib.sha256(normalized).hexdigest()

    def get_cached_analysis(
        self,
        diff_hash: str,
        project_name: str | None = None,
        session_id: int | None = None,
        burst_id: int | None = None,
    ) -> dict[str, Any] | None:
        """Check if an analysis for this diff hash already exists in disk cache."""
        return self.cache_mgr.get(
            diff_hash=diff_hash,
            project_name=project_name,
            session_id=session_id,
            burst_id=burst_id,
        )

    def save_cached_analysis(
        self,
        diff_hash: str,
        analysis: dict[str, Any],
        project_name: str | None = None,
        session_id: int | None = None,
        burst_id: int | None = None,
    ) -> None:
        """Persist analysis result to disk cache."""
        self.cache_mgr.save(
            diff_hash=diff_hash,
            analysis=analysis,
            project_name=project_name,
            session_id=session_id,
            burst_id=burst_id,
        )

    def chunk_diff(self, diff_text: str, max_lines: int | None = None, max_chars: int = 30000) -> str:
        """
        Intelligently chunk/truncate diffs exceeding max_lines or max_chars to prevent
        exceeding model context or wasting tokens/TPM limits.
        Preserves diff and file headers while truncating internal file diff bodies.
        """
        if max_lines is None:
            max_lines = self._config.get("analysis", {}).get("max_diff_lines_for_summary", 250)

        file_chunks = [c for c in re.split(r"(?=^diff --git |^--- )", diff_text, flags=re.MULTILINE) if c.strip()]

        if len(file_chunks) > 1 and (len(diff_text) > max_chars or len(diff_text.splitlines()) > max_lines):
            processed_chunks = []
            per_file_chars = max(1000, max_chars // len(file_chunks))
            per_file_lines = max(10, max_lines // len(file_chunks))
            for chunk in file_chunks:
                c_lines = chunk.splitlines()
                headers = [l for l in c_lines if l.startswith(('diff ', 'index ', '---', '+++', '@@'))]
                body_lines = [l for l in c_lines if not l.startswith(('diff ', 'index ', '---', '+++', '@@'))]

                if len(body_lines) > per_file_lines or len(chunk) > per_file_chars:
                    keep = max(3, per_file_lines // 2)
                    head_body = body_lines[:keep]
                    tail_body = body_lines[-keep:] if len(body_lines) > keep * 2 else []
                    omitted_lines = len(body_lines) - (len(head_body) + len(tail_body))
                    new_chunk = (
                        "\n".join(headers + head_body)
                        + f"\n... [{omitted_lines} lines of internal diff omitted for analysis] ...\n"
                        + "\n".join(tail_body)
                    )
                    processed_chunks.append(new_chunk)
                else:
                    processed_chunks.append(chunk)
            truncated = "\n".join(processed_chunks)
        else:
            lines = diff_text.splitlines()
            if len(lines) > max_lines:
                half = max_lines // 2
                head = lines[:half]
                tail = lines[-half:]
                omitted = len(lines) - (len(head) + len(tail))
                truncated = (
                    "\n".join(head)
                    + f"\n\n... [{omitted} lines of unified diff omitted for analysis] ...\n\n"
                    + "\n".join(tail)
                )
            else:
                truncated = diff_text

        # Character budget guard: ensure total length <= max_chars
        if len(truncated) > max_chars:
            margin = 400
            head_len = (max_chars - margin) // 2
            tail_len = head_len
            truncated = (
                truncated[:head_len]
                + f"\n\n... [diff truncated from {len(truncated)} to {max_chars} chars to respect API TPM limits] ...\n\n"
                + truncated[-tail_len:]
            )

        return truncated

    # -----------------------------------------------------------------------
    # Prompt Construction
    # -----------------------------------------------------------------------

    def _build_prompt(
        self,
        diff_text: str,
        first_file: str | None = None,
        summary: str | None = None,
        files: list[str] | None = None,
        project_name: str | None = None,
        workspace_path: Path | str | None = None,
        tech_stack: str | None = None,
        dir_tree: str | None = None,
        previous_summary: str | dict[str, Any] | None = None,
        executed_commands: list[str] | None = None,
    ) -> str:
        topology_parts = []
        if project_name:
            topology_parts.append(f"Project: {project_name}")
        if workspace_path:
            topology_parts.append(f"Workspace: {workspace_path}")
        if tech_stack:
            topology_parts.append(f"Tech Stack: {tech_stack}")
        topology_header = " | ".join(topology_parts) if topology_parts else "Project Topology"

        tree_block = f"\nDirectory Tree (2-level overview):\n```\n{dir_tree}\n```\n" if dir_tree else ""

        prev_text = ""
        if previous_summary:
            if isinstance(previous_summary, dict):
                prev_text = previous_summary.get("intent") or str(previous_summary.get("summary") or "")
            else:
                prev_text = str(previous_summary).strip()
        chronological_block = f"\nPrevious Burst Context:\n\"{prev_text}\"\n" if prev_text else ""

        exec_block = f"\nExecuted Commands:\n" + "\n".join(f"- `{c}`" for c in executed_commands) + "\n" if executed_commands else ""

        file_info = f"Files touched: {', '.join(files)}" if files else ""
        entry_info = f"Primary entrypoint: {first_file}" if first_file else ""
        context_info = f"Burst Summary: {summary}" if summary else ""
        meta = " | ".join(filter(None, [file_info, entry_info, context_info]))

        return f"""You are an expert software architect and code auditor analyzing an atomic code edit burst.
Analyze the provided unified diff, executed commands, and project context, and generate a concise, high-value technical explanation in valid JSON format.

{topology_header}
{tree_block}{chronological_block}{exec_block}
Metadata:
{meta}

Unified Diff:
```diff
{diff_text}
```

Instructions:
You MUST respond with ONLY a valid, parseable JSON object without markdown fences, matching this exact schema:
{{
  "intent": "<High-level architectural intent explaining WHY this change happened and what problem/task it addresses>",
  "execution_rationale": "<Technical rationale explaining why the terminal commands were executed in relation to the code changes>",
  "architecture_impact": "<How this change alters component structure, state management, dependencies, or API flow>",
  "key_modifications": [
    "<Concise bullet point detailing specific structural, function, class, or route changes>",
    "<Next specific structural modification>"
  ],
  "functionality_gained": "<Concrete technical explanation of new capabilities, fixes, or behavioral guarantees achieved by these changes>"
}}
"""

    def _parse_llm_json(self, raw_text: str) -> dict[str, Any]:
        """Strip markdown fences and parse structured JSON from LLM output."""
        cleaned = raw_text.strip()
        # Remove markdown code blocks if wrapped
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)
            cleaned = cleaned.strip()

        # Extract first JSON object if surrounded by preamble
        json_match = re.search(r"(\{.*\})", cleaned, re.DOTALL)
        if json_match:
            cleaned = json_match.group(1)

        data = json.loads(cleaned)
        # Validate schema shape
        intent = str(data.get("intent") or data.get("commit_title") or "Code edit burst analyzed.")
        arch_impact = str(data.get("architecture_impact") or "Component structure and data flow maintained.")
        exec_rationale = str(data.get("execution_rationale") or data.get("rationale") or "Terminal commands executed during validation and runtime verification.")

        mods_raw = data.get("key_modifications") or data.get("summary") or data.get("commit_body") or []
        if isinstance(mods_raw, str):
            mods = [s.strip("- ") for s in mods_raw.splitlines() if s.strip()] or [mods_raw]
        elif isinstance(mods_raw, list):
            mods = [str(x) for x in mods_raw]
        else:
            mods = ["Code changes applied."]

        gains = str(data.get("functionality_gained", "Enhanced functionality."))

        result = dict(data)
        result["intent"] = intent
        result["execution_rationale"] = exec_rationale
        result["architecture_impact"] = arch_impact
        result["key_modifications"] = mods
        result["summary"] = mods  # backward compatibility alias
        result["functionality_gained"] = gains
        return result

    # -----------------------------------------------------------------------
    # Provider Implementations
    # -----------------------------------------------------------------------

    def reload_config(self) -> dict[str, Any]:
        """Reload configuration from disk or environment."""
        self._config = self.load_config(self.engine_root)
        return self._config

    def _call_ollama(self, prompt: str) -> dict[str, Any]:
        """Issue HTTP POST to local Ollama instance."""
        if self.config_path.exists():
            self._config = self.load_config(self.engine_root)
        ollama_cfg = self._config.get("ollama", {})
        base_url = ollama_cfg.get("base_url", "http://localhost:11434").rstrip("/")
        model = ollama_cfg.get("default_model", "qwen2.5-coder:7b")
        timeout = ollama_cfg.get("timeout_seconds", 60)
        options = ollama_cfg.get("options", {})

        url = f"{base_url}/api/generate"
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": options,
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp_body = resp.read().decode("utf-8")
            resp_json = json.loads(resp_body)
            raw_response = resp_json.get("response", "")
            parsed = self._parse_llm_json(raw_response)
            parsed["provider"] = f"ollama:{model}"
            return parsed

    def _call_gemini(self, prompt: str) -> dict[str, Any]:
        """Issue API call to Gemini using official Google GenAI SDK or HTTPS fallback with rate-limit retry."""
        if self.config_path.exists():
            self._config = self.load_config(self.engine_root)
        gemini_cfg = self._config.get("gemini", {})
        api_key = gemini_cfg.get("api_key") or os.environ.get("GEMINI_API_KEY", "")
        if not api_key or "__REPLACE_" in api_key:
            raise ValueError("Gemini API key is not configured")

        model_name = gemini_cfg.get("default_model", "gemini-3.1-flash-lite")
        # Ensure deprecated / zero-quota models are upgraded to active quota tier
        if any(m in model_name for m in ["gemini-1.5", "gemini-2.0"]):
            model_name = "gemini-3.1-flash-lite"

        max_retries = 3
        backoff_delays = [3.0, 7.0, 15.0]

        for attempt in range(max_retries):
            # Check rate limiter on initial call and pause if cooling down
            if attempt == 0:
                self.rate_limiter.wait_if_needed(max_wait_s=5.0)
            try:
                # Try Google GenAI SDK first
                try:
                    from google import genai
                    from google.genai import types

                    client = genai.Client(api_key=api_key)
                    resp = client.models.generate_content(
                        model=model_name,
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            response_mime_type="application/json",
                            temperature=gemini_cfg.get("options", {}).get("temperature", 0.2),
                            tools=[],
                        ),
                    )
                    raw_text = resp.text or ""
                    parsed = self._parse_llm_json(raw_text)
                    parsed["provider"] = f"gemini:{model_name}"
                    return parsed
                except ImportError:
                    logger.debug("google.genai SDK not imported; using HTTPS REST fallback.")
                except Exception as sdk_exc:
                    err_str = str(sdk_exc)
                    err_lower = err_str.lower()
                    if "spending cap" in err_lower:
                        self.rate_limiter.trigger_cooldown(60.0)
                        raise
                    if any(k in err_lower for k in ("429", "503", "resource_exhausted", "quota exceeded", "high demand", "unavailable", "service unavailable")):
                        if attempt == max_retries - 1 and ("429" in err_lower or "resource_exhausted" in err_lower):
                            self.rate_limiter.trigger_cooldown(60.0)
                        raise
                    logger.warning("google.genai SDK call failed: %s; trying HTTPS REST fallback.", sdk_exc)

                # HTTPS REST Fallback
                timeout = gemini_cfg.get("timeout_seconds", 30)
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
                payload = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "responseMimeType": "application/json",
                        "temperature": 0.2,
                    },
                }
                data = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(
                    url,
                    data=data,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )

                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    resp_body = resp.read().decode("utf-8")
                    resp_json = json.loads(resp_body)
                    candidates = resp_json.get("candidates", [])
                    if not candidates:
                        raise RuntimeError("Gemini returned empty candidates")
                    part = candidates[0].get("content", {}).get("parts", [{}])[0]
                    raw_text = part.get("text", "")
                    parsed = self._parse_llm_json(raw_text)
                    parsed["provider"] = f"gemini-rest:{model_name}"
                    return parsed

            except urllib.error.HTTPError as http_err:
                if http_err.code in (429, 503):
                    if attempt < max_retries - 1:
                        sleep_s = backoff_delays[attempt]
                        logger.warning(
                            "Gemini API rate/capacity limit HTTP %d encountered. Retrying in %.0fs (attempt %d/%d)...",
                            http_err.code, sleep_s, attempt + 1, max_retries,
                        )
                        time.sleep(sleep_s)
                        continue
                    else:
                        if http_err.code == 429:
                            self.rate_limiter.trigger_cooldown(60.0)
                raise
            except Exception as exc:
                err_str = str(exc)
                err_lower = err_str.lower()
                if "spending cap" in err_lower or "api_key_invalid" in err_lower or "api key not valid" in err_lower:
                    self.rate_limiter.trigger_cooldown(60.0)
                    raise
                if any(k in err_lower for k in ("429", "503", "resource_exhausted", "quota exceeded", "high demand", "unavailable", "service unavailable")):
                    if attempt < max_retries - 1:
                        sleep_s = backoff_delays[attempt]
                        logger.warning(
                            "Gemini API rate/capacity limit (%s) encountered. Retrying in %.0fs (attempt %d/%d)...",
                            err_str[:80], sleep_s, attempt + 1, max_retries,
                        )
                        time.sleep(sleep_s)
                        continue
                    else:
                        if "429" in err_lower or "resource_exhausted" in err_lower:
                            self.rate_limiter.trigger_cooldown(60.0)
                raise

    # -----------------------------------------------------------------------
    # Fallback Generation
    # -----------------------------------------------------------------------

    def _fallback_analysis(
        self,
        files: list[str],
        first_file: str | None,
        summary: str | None,
        reason: str = "offline",
        project_name: str | None = None,
        workspace_path: Path | str | None = None,
    ) -> dict[str, Any]:
        """Generate a meaningful heuristic fallback analysis when AI is unreachable."""
        f_count = len(files)
        entry_txt = f" starting at '{first_file}'" if first_file else ""
        desc = summary or f"Burst modification affecting {f_count} file(s)"

        summary_bullets = [
            f"Atomic file modification detected across {f_count} file(s){entry_txt}.",
            f"Event details: {desc}",
        ]
        if files:
            summary_bullets.append(f"Target files: {', '.join(files[:6])}{' ...' if len(files) > 6 else ''}")

        return {
            "intent": f"Code modification bundle ({desc}). [AI offline fallback]",
            "execution_rationale": "Terminal commands executed during development workflow to verify changes in workspace context.",
            "architecture_impact": f"Component modifications committed to project '{project_name or 'workspace'}' without regression.",
            "key_modifications": summary_bullets,
            "summary": summary_bullets,
            "functionality_gained": "Modifications captured into ACID SQLite WAL storage, unified diff cache, and isolated Shadow Git micro-versioning.",
            "provider": "offline-fallback",
            "fallback_reason": reason,
            "cached": False,
        }

    # -----------------------------------------------------------------------
    # Public API: analyze_burst & analyze_event
    # -----------------------------------------------------------------------

    def analyze_burst(
        self,
        *args: Any,
        project_name: str | None = None,
        workspace_path: Path | str | None = None,
        event: dict[str, Any] | None = None,
        patches: list[dict[str, Any]] | None = None,
        previous_summary: str | dict[str, Any] | None = None,
        provider: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """
        Analyze an atomic code burst event with deep architectural context and strict diff sanitization.
        Supports both signatures:
          analyze_burst(project_name, workspace_path, event, previous_summary=...)
          analyze_burst(event_dict, patches, provider=...)
        """
        # Parse positional arguments for flexible signature compatibility
        if len(args) >= 1:
            if isinstance(args[0], str):
                project_name = args[0]
                if len(args) >= 2:
                    workspace_path = args[1]
                if len(args) >= 3 and isinstance(args[2], dict):
                    event = args[2]
                if len(args) >= 4:
                    previous_summary = args[3]
            elif isinstance(args[0], dict):
                event = args[0]
                if len(args) >= 2 and isinstance(args[1], list):
                    patches = args[1]
                if len(args) >= 3 and isinstance(args[2], str):
                    provider = args[2]

        if event is None:
            event = {}

        if patches is None:
            if "patches" in event and isinstance(event["patches"], list):
                patches = event["patches"]
            else:
                patches = []

        # Strict Diff Sanitization: Strip noise/temporary artifacts
        sanitized_patches = [
            p for p in patches
            if is_sanitized_patch(p.get("file_path", ""))
        ]

        files = [p.get("file_path", "") for p in sanitized_patches if p.get("file_path")]
        first_file = event.get("first_file_touched")
        summary = event.get("summary")

        # 1. Aggregate sanitized diff content
        diff_blocks = []
        for p in sanitized_patches:
            fp = p.get("file_path", "unknown")
            diff = p.get("diff_content") or ""
            diff_blocks.append(f"diff --git a/{fp} b/{fp}\n{diff}")

        full_diff = "\n\n".join(diff_blocks).strip()

        # Check if isolated ShadowGit has recorded an atomic micro-commit delta for this burst
        delta_diff = ""
        if project_name and (workspace_path or event.get("workspace_path")):
            w_path = workspace_path or event.get("workspace_path")
            slug = str(project_name).lower().replace(" ", "_").replace("-", "_")
            for tier in ("current", "last_run"):
                cand_shadow = self.engine_root / tier / slug / "shadow_git"
                if cand_shadow.exists():
                    try:
                        from spd_analysis_engine.scripts.git_shadow import ShadowGit
                        sg = ShadowGit(shadow_dir=cand_shadow, target_dir=w_path)
                        delta_diff = sg.get_burst_delta_diff(event_id=event.get("id"), files=files)
                        if delta_diff and delta_diff.strip():
                            break
                    except Exception as s_exc:
                        logger.debug("Could not extract ShadowGit delta diff: %s", s_exc)

        if delta_diff and delta_diff.strip():
            full_diff = delta_diff.strip()

        if not full_diff:
            return self._fallback_analysis(
                files, first_file, summary,
                reason="No non-noise diff content after sanitization",
                project_name=project_name,
                workspace_path=workspace_path,
            )

        # 2. Check Cache
        sess_id = event.get("session_id") if isinstance(event, dict) else kwargs.get("session_id")
        burst_num = (event.get("burst_num") or event.get("id")) if isinstance(event, dict) else kwargs.get("burst_id")
        p_name = project_name or (event.get("project_name") if isinstance(event, dict) else kwargs.get("project_name"))
        diff_hash = self.compute_diff_hash(full_diff)
        cached = self.get_cached_analysis(
            diff_hash,
            project_name=p_name,
            session_id=sess_id,
            burst_id=burst_num,
        )
        if cached:
            return cached

        # 3. Deep Context Synthesis: Directory Tree & Tech Stack & Executed Commands
        tree = self.generate_directory_tree(workspace_path)
        tech_stack = self.detect_tech_stack(workspace_path)

        executed_commands: list[str] = []
        if project_name:
            slug = str(project_name).lower().replace(" ", "_").replace("-", "_")
            db_cand = None
            for tier in ("current", "last_run"):
                cand = self.engine_root / tier / slug / "session.db"
                if cand.exists():
                    db_cand = cand
                    break
            if db_cand:
                try:
                    conn = sqlite3.connect(f"file:{db_cand}?mode=ro", uri=True)
                    cur = conn.cursor()
                    curr_id = event.get("id")
                    session_id = event.get("session_id")
                    if curr_id is not None:
                        if session_id is not None:
                            cur.execute(
                                "SELECT first_file_touched, summary FROM events WHERE event_type = 'EXEC' AND session_id = ? AND id < ? ORDER BY id DESC LIMIT 5",
                                (session_id, curr_id)
                            )
                        else:
                            cur.execute(
                                "SELECT first_file_touched, summary FROM events WHERE event_type = 'EXEC' AND id < ? ORDER BY id DESC LIMIT 5",
                                (curr_id,)
                            )
                        for r in reversed(cur.fetchall()):
                            cmd = r[0] or r[1] or ""
                            if cmd and cmd not in executed_commands:
                                executed_commands.append(cmd)
                    conn.close()
                except Exception as q_exc:
                    logger.debug("Failed querying EXEC commands for burst: %s", q_exc)

        # 4. Chunk diff & Build deep prompt
        chunked_diff = self.chunk_diff(full_diff)
        prompt = self._build_prompt(
            chunked_diff,
            first_file=first_file,
            summary=summary,
            files=files,
            project_name=project_name,
            workspace_path=workspace_path,
            tech_stack=tech_stack,
            dir_tree=tree,
            previous_summary=previous_summary,
            executed_commands=executed_commands,
        )

        # 5. Resolve provider order
        cfg = self.load_config(self.engine_root)
        pref_provider = (provider or cfg.get("preferred_provider") or cfg.get("default_provider", "gemini")).lower()

        if pref_provider in ("heuristic", "none"):
            fallback = self._fallback_analysis(
                files, first_file, summary,
                reason="heuristic_mode",
                project_name=project_name,
                workspace_path=workspace_path,
            )
            fallback["provider"] = "heuristic"
            self.save_cached_analysis(
                diff_hash,
                fallback,
                project_name=p_name,
                session_id=sess_id,
                burst_id=burst_num,
            )
            return fallback

        has_gemini = bool(cfg.get("gemini", {}).get("api_key") or os.environ.get("GEMINI_API_KEY"))

        providers_to_try = []
        if pref_provider == "gemini" and has_gemini:
            providers_to_try = ["gemini", "ollama"]
        elif pref_provider == "ollama":
            providers_to_try = ["ollama", "gemini"] if has_gemini else ["ollama"]
        else:
            providers_to_try = ["gemini"] if has_gemini else ["ollama"]

        last_error = ""
        result = None

        for p_name_curr in providers_to_try:
            try:
                if p_name_curr == "gemini":
                    rate_status = self.rate_limiter.get_status()
                    if rate_status["cooling_down"] and rate_status["cooldown_remaining_s"] > 3.0:
                        logger.info("Gemini is cooling down (%.1fs remaining); skipping to next provider.", rate_status["cooldown_remaining_s"])
                        last_error = f"Gemini cooling down ({rate_status['cooldown_remaining_s']}s remaining)"
                        continue
                    logger.info("Attempting deep AI synthesis via Gemini API...")
                    result = self._call_gemini(prompt)
                    break
                elif p_name_curr == "ollama":
                    logger.info("Attempting deep AI synthesis via local Ollama...")
                    result = self._call_ollama(prompt)
                    break
            except Exception as exc:
                last_error = str(exc)
                logger.warning("Provider '%s' failed for diff %s: %s", p_name_curr, diff_hash[:8], exc)

        # 6. Fallback if all providers failed
        if not result:
            result = self._fallback_analysis(
                files, first_file, summary,
                reason=f"Providers unreachable ({last_error})",
                project_name=project_name,
                workspace_path=workspace_path,
            )

        result["cached"] = False
        result["diff_hash"] = diff_hash

        # 7. Save valid results to cache
        self.save_cached_analysis(
            diff_hash,
            result,
            project_name=p_name,
            session_id=sess_id,
            burst_id=burst_num,
        )
        return result

    analyze_event = analyze_burst

    # -----------------------------------------------------------------------
    # Public API: generate_session_changelog
    # -----------------------------------------------------------------------

    def _fallback_changelog(
        self,
        files: list[str],
        events: list[dict[str, Any]],
        reason: str = "offline",
    ) -> dict[str, Any]:
        """Generate a structured heuristic Conventional Commit & CHANGELOG when AI is offline."""
        ts_str = _now_ist_header()

        # Guess scope from files
        scope = "core"
        if any(f.startswith("web/") or f.endswith((".html", ".css", ".js")) for f in files):
            scope = "web" if not any("scripts/" in f or f.endswith(".py") for f in files) else "fullstack"
        elif any(f.startswith("tests/") for f in files):
            scope = "test"

        action = "feat" if any("create" in str(e.get("summary", "")).lower() or "add" in str(e.get("summary", "")).lower() for e in events) else "refactor"
        if any("fix" in str(e.get("summary", "")).lower() or "error" in str(e.get("summary", "")).lower() for e in events):
            action = "fix"

        f_count = len(files)
        commit_title = f"{action}({scope}): synchronize session changes across {f_count} file(s)"

        bullets = []
        for e in events:
            s = e.get("summary")
            if s and s not in bullets:
                bullets.append(f"- {s}")
        if not bullets:
            bullets = [f"- Modified {f}" for f in files[:8]]

        body_text = "\n".join(bullets[:10])

        file_list_md = "\n".join(f"- `{f}`" for f in files[:12])
        if len(files) > 12:
            file_list_md += f"\n- ... and {len(files) - 12} other files"

        changelog_md = f"""### [{ts_str}] {commit_title}

#### Key Highlights
{body_text}

#### Files Modified
{file_list_md}

#### Functional & Architectural Impact
Captured atomic multi-file prompt burst edits into session database and repository working tree. [Synthesized via offline fallback: {reason}]"""

        return {
            "commit_title": commit_title,
            "commit_body": body_text,
            "changelog_entry": changelog_md,
            "provider": "offline-fallback",
            "fallback_reason": reason,
            "cached": False,
        }

    def generate_session_changelog(
        self,
        session_events: list[dict[str, Any]],
        diffs: list[dict[str, Any]] | None = None,
        provider: str | None = None,
    ) -> dict[str, Any]:
        """
        Analyze all prompt bursts and diffs across a monitoring session to produce
        a Conventional Commit title, description, and markdown changelog entry.

        Parameters
        ----------
        session_events : list[dict]
            List of event dictionaries recorded during the session.
        diffs : list[dict] | None
            Optional explicit list of patch dictionaries. If omitted, extracted
            from session_events.
        provider : str | None
            Optional AI provider override ('heuristic', 'ollama', 'gemini').

        Returns
        -------
        dict[str, Any]
            ``{"commit_title": str, "commit_body": str, "changelog_entry": str, "provider": str, "cached": bool}``
        """
        ts_str = _now_ist_header()

        # 1. Gather all files and diff contents
        files_set: set[str] = set()
        diff_snippets: list[str] = []

        all_patches = list(diffs or [])
        for ev in session_events:
            for p in ev.get("patches", []):
                all_patches.append(p)

        for p in all_patches:
            fp = p.get("file_path")
            if fp:
                files_set.add(fp)
            diff = p.get("diff_content")
            if diff:
                diff_snippets.append(f"--- {fp} ---\n{diff}")

        files_list = sorted(list(files_set))
        if not files_list:
            return self._fallback_changelog([], session_events, reason="No files modified in session")

        # 2. Check Cache using session diff hash
        aggregated_diff = "\n\n".join(diff_snippets).strip()
        session_hash = "session_" + self.compute_diff_hash(aggregated_diff or ", ".join(files_list))
        cached = self.get_cached_analysis(session_hash)
        if cached:
            return cached

        # 3. Build Prompt
        event_summaries = [f"- Burst #{e.get('id', '?')}: {e.get('summary', 'burst edit')}" for e in session_events if e.get("event_type") == "EDIT"]
        event_text = "\n".join(event_summaries[:15]) or "- Multi-file prompt burst edits"

        chunked_diffs = self.chunk_diff(aggregated_diff, max_lines=250) if aggregated_diff else "Diff not available"

        prompt = f"""You are a senior software architect and release engineer writing a Conventional Commit and CHANGELOG entry for a software session.
Analyze the following session event history, modified files, and diff excerpts to generate a high quality release summary.

Session Metadata:
- Timestamp: {ts_str}
- Files touched: {', '.join(files_list[:25])}
- Event progression:
{event_text}

Diff Excerpts:
```diff
{chunked_diffs}
```

Instructions:
Respond with ONLY a valid, parseable JSON object without markdown fences, matching this exact schema:
{{
  "commit_title": "<Conventional commit message under 72 chars with type and scope, e.g. feat(core): implement user authentication and oauth2 flow>",
  "commit_body": "<Bulleted list of high-level modifications grouped by component>",
  "changelog_entry": "### [{ts_str}] <commit_title>\\n\\n#### Key Highlights\\n- <bullet 1>\\n- <bullet 2>\\n\\n#### Files Modified\\n{', '.join(files_list[:10])}\\n\\n#### Functional & Architectural Impact\\n<Detailed paragraph explaining concrete capabilities gained, bugs resolved, or behavioral guarantees achieved.>"
}}
"""

        # 4. Resolve providers
        cfg = self.load_config(self.engine_root)
        pref_provider = (provider or cfg.get("preferred_provider") or cfg.get("default_provider", "gemini")).lower()

        if pref_provider in ("heuristic", "none"):
            fallback = self._fallback_changelog(files_list, session_events, reason="heuristic_mode")
            fallback["provider"] = "heuristic"
            self.save_cached_analysis(session_hash, fallback)
            return fallback

        has_gemini = bool(cfg.get("gemini", {}).get("api_key") or os.environ.get("GEMINI_API_KEY"))

        providers = []
        if pref_provider == "gemini" and has_gemini:
            providers = ["gemini", "ollama"]
        elif pref_provider == "ollama":
            providers = ["ollama", "gemini"] if has_gemini else ["ollama"]
        else:
            providers = ["gemini"] if has_gemini else ["ollama"]

        result = None
        last_error = ""

        for prov in providers:
            try:
                if prov == "gemini":
                    rate_status = self.rate_limiter.get_status()
                    if rate_status["cooling_down"] and rate_status["cooldown_remaining_s"] > 3.0:
                        logger.info("Gemini is cooling down (%.1fs remaining); skipping changelog to next provider.", rate_status["cooldown_remaining_s"])
                        last_error = f"Gemini cooling down ({rate_status['cooldown_remaining_s']}s remaining)"
                        continue
                    logger.info("Generating session changelog via Gemini API...")
                    raw_res = self._call_gemini(prompt)
                elif prov == "ollama":
                    logger.info("Generating session changelog via local Ollama...")
                    raw_res = self._call_ollama(prompt)
                else:
                    continue

                # Ensure result conforms to changelog schema
                commit_title = str(raw_res.get("commit_title") or raw_res.get("intent") or "feat: session code updates").strip()
                if not any(commit_title.startswith(p) for p in ("feat", "fix", "refactor", "chore", "docs", "style", "test", "perf")):
                    commit_title = f"feat(core): {commit_title}"
                commit_body = raw_res.get("commit_body")
                if isinstance(commit_body, list):
                    commit_body = "\n".join(f"- {b}" for b in commit_body)
                elif not commit_body:
                    commit_body = "\n".join(f"- {s}" for s in raw_res.get("summary", ["Applied code modifications."]))

                changelog_entry = raw_res.get("changelog_entry")
                if not changelog_entry:
                    changelog_entry = f"### [{ts_str}] {commit_title}\n\n#### Key Highlights\n{commit_body}\n\n#### Files Modified\n" + "\n".join(f"- `{f}`" for f in files_list[:10]) + f"\n\n#### Functional & Architectural Impact\n{raw_res.get('functionality_gained', 'Enhanced functionality.')}"

                result = {
                    "commit_title": commit_title,
                    "commit_body": commit_body,
                    "changelog_entry": changelog_entry,
                    "provider": raw_res.get("provider", prov),
                    "cached": False,
                }
                break
            except Exception as exc:
                last_error = str(exc)
                logger.warning("Provider %s failed for changelog: %s", prov, exc)

        if not result:
            result = self._fallback_changelog(files_list, session_events, reason=f"Providers unreachable ({last_error})")

        result["diff_hash"] = session_hash
        self.save_cached_analysis(session_hash, result)
        return result

    # -----------------------------------------------------------------------
    # Public API: explain_command
    # -----------------------------------------------------------------------

    def _fallback_command_explanation(
        self,
        command_str: str,
        exit_code: int = 0,
        duration_s: float = 0.0,
        reason: str = "offline",
    ) -> dict[str, Any]:
        """Generate structured heuristic command intent, technical purpose, and outcome analysis."""
        cmd_clean = command_str.strip()
        tokens = cmd_clean.split()
        first_token = os.path.basename(tokens[0]).lower() if tokens else ""

        intent = "Execute CLI tool or terminal command."
        technical_purpose = f"Invoked '{first_token}' with {len(tokens) - 1} parameter(s)."

        # Specific heuristics for common developer tools
        if first_token in ("git", "git.exe"):
            subcmd = tokens[1].lower() if len(tokens) > 1 else ""
            if subcmd == "status":
                intent = "Inspect working directory and staging area status."
                technical_purpose = "Git CLI query checking for modified, untracked, or staged changes without modifying repository state."
            elif subcmd == "commit":
                intent = "Record staged snapshot into project Git version history."
                technical_purpose = "Git commit operation creating a new revision object with author metadata and commit message."
            elif subcmd == "diff":
                intent = "Inspect working tree or branch code line modifications."
                technical_purpose = "Git diff comparator outputting unified diff hunks."
            elif subcmd == "push":
                intent = "Publish local Git commits to remote upstream repository."
                technical_purpose = "Git network push transferring packfiles and updating remote refs."
            elif subcmd in ("branch", "checkout", "switch"):
                intent = "Manage or switch workspace active Git branch."
                technical_purpose = f"Git branch operation ({subcmd}) adjusting HEAD reference."
            else:
                intent = f"Git version control command ({subcmd or 'general'})."
                technical_purpose = f"Invoked Git subcommand '{subcmd}' to manipulate or query repository state."

        elif first_token in ("python", "python3", "py", "python.exe"):
            if " -c " in f" {cmd_clean} ":
                intent = "Execute inline Python snippet or one-liner evaluation."
                technical_purpose = "Python runtime evaluation via '-c' flag for ad-hoc script execution."
            elif "-m unittest" in cmd_clean:
                intent = "Execute unit test suite using Python unittest framework."
                technical_purpose = "Python test discovery and test runner execution."
            elif "-m py_compile" in cmd_clean:
                intent = "Syntax verification and bytecode pre-compilation."
                technical_purpose = "Python py_compile module validating script syntax without full execution."
            else:
                script_name = tokens[1] if len(tokens) > 1 and not tokens[1].startswith("-") else "script"
                intent = f"Run Python program '{os.path.basename(script_name)}'."
                technical_purpose = f"Python interpreter invoked to execute '{script_name}'."

        elif first_token in ("pytest", "pytest.exe"):
            intent = "Execute automated test suite with pytest test runner."
            technical_purpose = "Pytest runner executing assertions, fixtures, and regression tests."

        elif first_token in ("npm", "yarn", "pnpm", "npx"):
            subcmd = tokens[1].lower() if len(tokens) > 1 else ""
            if subcmd in ("test", "t"):
                intent = "Run project test suite defined in package.json."
                technical_purpose = "Node.js package manager triggering the 'test' npm lifecycle script."
            elif subcmd in ("run", "start", "dev"):
                intent = f"Start application or execute script '{tokens[2] if len(tokens) > 2 else subcmd}'."
                technical_purpose = f"Node package manager running configured build/dev lifecycle script."
            elif subcmd in ("install", "i", "add"):
                intent = "Install project dependencies or packages."
                technical_purpose = "Package dependency resolution, lockfile synchronization, and node_modules extraction."
            else:
                intent = f"Node.js package manager operation ({subcmd})."
                technical_purpose = f"Invoked {first_token} with subcommand '{subcmd}'."

        elif first_token in ("powershell", "powershell.exe", "pwsh", "pwsh.exe"):
            if any(f in cmd_clean.lower() for f in ("-command", "-c ", " -c\"")):
                intent = "Execute automated PowerShell script block or headless command."
                technical_purpose = "PowerShell host running non-interactive headless script command."
            else:
                intent = "Execute interactive or scripting PowerShell directive."
                technical_purpose = "PowerShell shell runtime processing command expression."

        elif first_token in ("cmd", "cmd.exe"):
            intent = "Execute Windows command processor directive."
            technical_purpose = "Windows cmd.exe shell processing batch/CLI arguments."

        # Outcome analysis
        if exit_code == 0:
            outcome_analysis = f"Process terminated successfully (exit code 0) in {duration_s:.2f}s."
        else:
            outcome_analysis = f"Process failed or returned non-zero status (exit code {exit_code}) after {duration_s:.2f}s."

        summary = [
            f"Command: {cmd_clean[:70]}{'...' if len(cmd_clean) > 70 else ''}",
            f"Execution time: {duration_s:.2f}s | Result: {'Success' if exit_code == 0 else f'Failed (code {exit_code})'}",
        ]

        return {
            "intent": intent,
            "technical_purpose": technical_purpose,
            "outcome_analysis": outcome_analysis,
            "summary": summary,
            "provider": f"heuristic ({reason})",
            "cached": False,
        }

    def explain_command(
        self,
        project_name: str,
        command_str: str,
        exit_code: int = 0,
        duration_s: float = 0.0,
        surrounding_burst_context: list[dict[str, Any]] | None = None,
        provider: str | None = None,
    ) -> dict[str, Any]:
        """
        Explain a terminal command execution using AI (Gemini or Ollama) with offline fallback.

        Returns JSON matching schema:
        {
          "intent": str,
          "technical_purpose": str,
          "outcome_analysis": str,
          "summary": list[str],
          "provider": str,
          "cached": bool
        }
        """
        clean_cmd = command_str.strip()
        cache_key = "cmd_" + hashlib.sha256(f"{clean_cmd}_{exit_code}_{duration_s:.1f}".encode()).hexdigest()[:24]

        if not clean_cmd:
            res = self._fallback_command_explanation(command_str, exit_code, duration_s, reason="empty_command")
            res["diff_hash"] = cache_key
            return res

        # 1. Check Cache
        cached = self.get_cached_analysis(cache_key, project_name=project_name)
        if cached:
            return cached

        # 2. Check Provider Preferences
        cfg = self.load_config(self.engine_root)
        pref_provider = (provider or cfg.get("preferred_provider") or cfg.get("default_provider", "gemini")).lower()

        if pref_provider in ("heuristic", "none"):
            fallback = self._fallback_command_explanation(clean_cmd, exit_code, duration_s, reason="heuristic_mode")
            fallback["provider"] = "heuristic"
            fallback["diff_hash"] = cache_key
            self.save_cached_analysis(cache_key, fallback, project_name=project_name)
            return fallback

        # 3. Build Prompt
        context_str = ""
        if surrounding_burst_context:
            c_lines = [
                f"- #{c.get('id', '?')} [{c.get('event_type', 'EVENT')}]: {c.get('first_file_touched') or c.get('summary', '')}"
                for c in surrounding_burst_context[:5]
            ]
            context_str = f"\nSurrounding Workspace Events:\n" + "\n".join(c_lines)

        prompt = f"""You are a senior DevOps, systems architecture, and security engineer.
Analyze the following terminal command execution recorded during a software development session.

Project: {project_name}
Command: {clean_cmd}
Exit Code: {exit_code}
Execution Duration: {duration_s:.2f} seconds
{context_str}

Instructions:
Respond with ONLY a valid, parseable JSON object without markdown fences, matching this exact schema:
{{
  "intent": "<Clear, plain-English statement of why this command was invoked and what the developer or automated agent was attempting to achieve>",
  "technical_purpose": "<Specific explanation of the binary, compiler, interpreter, flags, and system interaction>",
  "outcome_analysis": "<Assessment of the execution result based on exit code {exit_code} and runtime {duration_s:.2f}s>",
  "summary": [
    "<Highlight bullet 1>",
    "<Highlight bullet 2>"
  ]
}}
"""

        has_gemini = bool(cfg.get("gemini", {}).get("api_key") or os.environ.get("GEMINI_API_KEY"))
        providers = []
        if pref_provider == "gemini" and has_gemini:
            providers = ["gemini", "ollama"]
        elif pref_provider == "ollama":
            providers = ["ollama", "gemini"] if has_gemini else ["ollama"]
        else:
            providers = ["gemini"] if has_gemini else ["ollama"]

        result = None
        last_error = ""

        for prov in providers:
            try:
                if prov == "gemini":
                    rate_status = self.rate_limiter.get_status()
                    if rate_status["cooling_down"] and rate_status["cooldown_remaining_s"] > 3.0:
                        logger.info("Gemini cooling down (%.1fs remaining); skipping to next provider.", rate_status["cooldown_remaining_s"])
                        last_error = f"Gemini cooling down ({rate_status['cooldown_remaining_s']}s remaining)"
                        continue
                    logger.info("Explaining command via Gemini API...")
                    raw_res = self._call_gemini(prompt)
                elif prov == "ollama":
                    logger.info("Explaining command via local Ollama...")
                    raw_res = self._call_ollama(prompt)
                else:
                    continue

                intent = str(raw_res.get("intent") or "Command executed").strip()
                tech_purpose = str(raw_res.get("technical_purpose") or raw_res.get("architecture_impact") or "CLI tool execution").strip()
                outcome = str(raw_res.get("outcome_analysis") or f"Terminated with exit code {exit_code} in {duration_s:.2f}s").strip()
                summary_raw = raw_res.get("summary")
                if isinstance(summary_raw, list):
                    summary = [str(s) for s in summary_raw]
                elif isinstance(summary_raw, str):
                    summary = [summary_raw]
                else:
                    summary = [f"Ran {clean_cmd[:50]}", f"Exit: {exit_code} ({duration_s:.2f}s)"]

                result = {
                    "intent": intent,
                    "technical_purpose": tech_purpose,
                    "outcome_analysis": outcome,
                    "summary": summary,
                    "provider": raw_res.get("provider", prov),
                    "cached": False,
                }
                break
            except Exception as exc:
                last_error = str(exc)
                logger.warning("Provider %s failed for command explanation: %s", prov, exc)

        if not result:
            result = self._fallback_command_explanation(clean_cmd, exit_code, duration_s, reason=f"Providers unreachable ({last_error})")

        result["diff_hash"] = cache_key
        self.save_cached_analysis(cache_key, result, project_name=project_name)
        return result

