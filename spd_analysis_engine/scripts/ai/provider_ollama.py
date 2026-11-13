"""
scripts/ai/provider_ollama.py
=============================
Local Ollama model provider integration, runtime probing, and profile options.
Exposes whether the configured model is running on GPU and applies profile-driven
context, GPU layers, and keep_alive.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any, Callable

logger = logging.getLogger(__name__)


def probe_ollama_runtime(
    base_url: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """
    Probe Ollama runtime status via /api/ps to expose if configured model
    is actually loaded in memory and offloaded to GPU (VRAM).
    """
    clean_url = (base_url or "http://127.0.0.1:11434").rstrip("/")
    url = f"{clean_url}/api/ps"
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "SPD-Analysis-Engine"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=4.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            running_models = data.get("models", [])
            target = (model or "").lower()
            matched = None
            for m in running_models:
                m_name = m.get("name", "").lower()
                if target and (target in m_name or m_name in target):
                    matched = m
                    break
            if not matched and running_models:
                matched = running_models[0]

            if matched:
                size_vram = matched.get("size_vram", 0)
                total_size = matched.get("size", 0)
                loaded_on_gpu = bool(size_vram and size_vram > 0)
                return {
                    "running": True,
                    "model": matched.get("name"),
                    "loaded_on_gpu": loaded_on_gpu,
                    "size_vram_bytes": size_vram,
                    "size_total_bytes": total_size,
                    "details": matched.get("details", {}),
                }
            return {
                "running": False,
                "model": model,
                "loaded_on_gpu": False,
                "message": "Model is not currently active in memory",
            }
    except Exception as exc:
        return {
            "running": False,
            "model": model,
            "loaded_on_gpu": False,
            "error": str(exc),
        }


def call_ollama(
    prompt: str,
    ollama_cfg: dict[str, Any],
    parse_json_func: Callable[[str], dict[str, Any]],
) -> dict[str, Any]:
    """Issue HTTP POST to local Ollama instance with profile-driven options."""
    base_url = ollama_cfg.get("base_url", "http://localhost:11434").rstrip("/")
    model = ollama_cfg.get("default_model", "qwen2.5-coder:7b")
    timeout = ollama_cfg.get("timeout_seconds", 60)
    options = dict(ollama_cfg.get("options", {}))

    # Apply profile-driven context and GPU configurations
    if "num_ctx" in ollama_cfg:
        options["num_ctx"] = int(ollama_cfg["num_ctx"])
    if "num_gpu" in ollama_cfg:
        options["num_gpu"] = int(ollama_cfg["num_gpu"])
    keep_alive = ollama_cfg.get("keep_alive", "5m")

    url = f"{base_url}/api/generate"
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": options,
        "keep_alive": keep_alive,
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
        parsed = parse_json_func(raw_response)
        parsed["provider"] = f"ollama:{model}"
        return parsed


def test_ollama_connection(
    base_url: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Test connection to an Ollama instance by querying /api/tags and probing GPU runtime."""
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
            runtime_info = probe_ollama_runtime(clean_url, model)
            return {
                "success": True,
                "message": f"Connected to Ollama at {clean_url}.",
                "models": models,
                "installed_models": models,
                "model_found": (model in models) if model else True,
                "loaded_on_gpu": runtime_info.get("loaded_on_gpu", False),
                "runtime": runtime_info,
            }
    except urllib.error.URLError as url_err:
        return {
            "success": False,
            "message": f"Could not connect to Ollama at {clean_url} ({url_err.reason}). Ensure 'ollama serve' is running.",
            "models": [],
            "installed_models": [],
            "loaded_on_gpu": False,
        }
    except Exception as exc:
        return {
            "success": False,
            "message": f"Error connecting to Ollama: {exc}",
            "models": [],
            "installed_models": [],
            "loaded_on_gpu": False,
        }
