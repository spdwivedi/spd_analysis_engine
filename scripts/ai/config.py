"""
scripts/ai/config.py
====================
AI provider configuration management, key vault operations, and credential masking.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
_IST = timezone(timedelta(hours=5, minutes=30))


def _now_ist_iso() -> str:
    """Return ISO-8601 formatted timestamp in IST (UTC+5:30)."""
    return datetime.now(_IST).strftime("%Y-%m-%dT%H:%M:%S+05:30")


def get_config_path(engine_root: Path | str | None = None) -> Path:
    """Return path to models_config.json."""
    if engine_root is None:
        engine_root = Path(__file__).resolve().parent.parent.parent
    return Path(engine_root) / "ai_data" / "models_config.json"


def _load_env_file(engine_root: Path | str | None = None) -> dict[str, str]:
    """Load key-value pairs from ai_data/.env, project .env, or parent .env without external libraries."""
    if engine_root is None:
        engine_root = Path(__file__).resolve().parent.parent.parent
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


def load_model_config(engine_root: Path | str | None = None) -> dict[str, Any]:
    """Load or reload model configuration adhering to strict credential precedence."""
    config_path = get_config_path(engine_root)
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
    else:
        example_path = config_path.parent / "models_config.example.json"
        if example_path.exists():
            try:
                data = json.loads(example_path.read_text(encoding="utf-8"))
                for k, v in data.items():
                    if isinstance(v, dict) and isinstance(default_cfg.get(k), dict):
                        default_cfg[k].update(v)
                    else:
                        default_cfg[k] = v
                if "gemini_api_key" in data and not raw_config_key:
                    cand_k = str(data["gemini_api_key"]).strip()
                    if cand_k and cand_k != "YOUR_GEMINI_API_KEY_HERE":
                        raw_config_key = cand_k
            except Exception as exc:
                logger.debug("Failed reading %s: %s", example_path, exc)

    resolved_key = ""
    key_source = "none"

    if raw_config_key and not raw_config_key.startswith("__REPLACE_") and raw_config_key != "YOUR_GEMINI_API_KEY_HERE":
        resolved_key = raw_config_key
        key_source = "models_config.json"
    else:
        env_file_vars = _load_env_file(engine_root)
        env_file_key = env_file_vars.get("GEMINI_API_KEY", "").strip()
        if env_file_key and not env_file_key.startswith("__REPLACE_") and env_file_key != "YOUR_GEMINI_API_KEY_HERE":
            resolved_key = env_file_key
            key_source = ".env"
        else:
            os_key = os.environ.get("GEMINI_API_KEY", "").strip()
            if os_key and not os_key.startswith("__REPLACE_") and os_key != "YOUR_GEMINI_API_KEY_HERE":
                resolved_key = os_key
                key_source = "os.environ"

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


def mask_model_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """Return config with sensitive Gemini API keys masked."""
    copied = json.loads(json.dumps(cfg))
    gem_cfg = copied.get("gemini", {})
    api_key = gem_cfg.get("api_key", "")
    if api_key and "__REPLACE_" not in api_key:
        gem_cfg["api_key"] = "****" if len(api_key) <= 8 else f"{api_key[:4]}****{api_key[-4:]}"

    top_key = copied.get("gemini_api_key", "")
    if top_key and "__REPLACE_" not in top_key:
        copied["gemini_api_key"] = "****" if len(top_key) <= 8 else f"{top_key[:4]}****{top_key[-4:]}"

    copied["active_key_suffix"] = cfg.get("active_key_suffix", "(not set)")
    copied["active_key_source"] = cfg.get("active_key_source", "none")

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


def save_model_config(config_dict: dict[str, Any], engine_root: Path | str | None = None) -> dict[str, Any]:
    """Save updated configuration to models_config.json."""
    current = load_model_config(engine_root)
    config_path = get_config_path(engine_root)

    pref = config_dict.get("preferred_provider") or config_dict.get("default_provider")
    if pref:
        clean_pref = str(pref).strip().lower()
        current["default_provider"] = clean_pref
        current["preferred_provider"] = clean_pref

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

    gem_cur = current.setdefault("gemini", {})
    saved_keys = list(current.get("saved_gemini_keys", []))

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


__all__ = [
    "get_config_path",
    "_load_env_file",
    "load_model_config",
    "mask_model_config",
    "save_model_config",
]
