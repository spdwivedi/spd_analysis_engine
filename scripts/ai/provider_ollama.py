"""
scripts/ai/provider_ollama.py
=============================
Local Ollama model provider integration and health checks.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any, Callable

logger = logging.getLogger(__name__)


def call_ollama(
    prompt: str,
    ollama_cfg: dict[str, Any],
    parse_json_func: Callable[[str], dict[str, Any]],
) -> dict[str, Any]:
    """Issue HTTP POST to local Ollama instance."""
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
        parsed = parse_json_func(raw_response)
        parsed["provider"] = f"ollama:{model}"
        return parsed


def test_ollama_connection(
    base_url: str | None = None,
    model: str | None = None,
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
