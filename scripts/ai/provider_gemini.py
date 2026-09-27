"""
scripts/ai/provider_gemini.py
=============================
Google Gemini API provider integration with official Google GenAI SDK,
HTTPS REST fallback, exponential backoff, and connection verification.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


def call_gemini(
    prompt: str,
    gemini_cfg: dict[str, Any],
    rate_limiter: Any,
    parse_json_func: Callable[[str], dict[str, Any]],
) -> dict[str, Any]:
    """Issue API call to Gemini using official Google GenAI SDK or HTTPS fallback with rate-limit retry."""
    api_key = gemini_cfg.get("api_key") or os.environ.get("GEMINI_API_KEY", "")
    if not api_key or "__REPLACE_" in api_key:
        raise ValueError("Gemini API key is not configured")

    model_name = gemini_cfg.get("default_model", "gemini-3.1-flash-lite")
    if any(m in model_name for m in ["gemini-1.5", "gemini-2.0"]):
        model_name = "gemini-3.1-flash-lite"

    max_retries = 3
    backoff_delays = [3.0, 7.0, 15.0]

    for attempt in range(max_retries):
        if attempt == 0 and rate_limiter:
            rate_limiter.wait_if_needed(max_wait_s=5.0)
        try:
            # 1. Try Google GenAI SDK first
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
                parsed = parse_json_func(raw_text)
                parsed["provider"] = f"gemini:{model_name}"
                return parsed
            except ImportError:
                logger.debug("google.genai SDK not imported; using HTTPS REST fallback.")
            except Exception as sdk_exc:
                err_str = str(sdk_exc)
                err_lower = err_str.lower()
                if "spending cap" in err_lower:
                    if rate_limiter:
                        rate_limiter.trigger_cooldown(60.0)
                    raise
                if any(k in err_lower for k in ("429", "503", "resource_exhausted", "quota exceeded", "high demand", "unavailable", "service unavailable")):
                    if attempt == max_retries - 1 and ("429" in err_lower or "resource_exhausted" in err_lower):
                        if rate_limiter:
                            rate_limiter.trigger_cooldown(60.0)
                    raise
                logger.warning("google.genai SDK call failed: %s; trying HTTPS REST fallback.", sdk_exc)

            # 2. HTTPS REST Fallback
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
                parsed = parse_json_func(raw_text)
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
                    if http_err.code == 429 and rate_limiter:
                        rate_limiter.trigger_cooldown(60.0)
            raise
        except Exception as exc:
            err_str = str(exc)
            err_lower = err_str.lower()
            if "spending cap" in err_lower or "api_key_invalid" in err_lower or "api key not valid" in err_lower:
                if rate_limiter:
                    rate_limiter.trigger_cooldown(60.0)
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
                    if ("429" in err_lower or "resource_exhausted" in err_lower) and rate_limiter:
                        rate_limiter.trigger_cooldown(60.0)
            raise

    raise RuntimeError("Gemini retries exhausted")


def test_gemini_connection(
    api_key: str | None = None,
    key_id: str | None = None,
    model: str | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Test Gemini API connection by querying the models list endpoint."""
    clean_key = ""
    cfg = config or {}
    if key_id:
        for k in cfg.get("saved_gemini_keys", []):
            if k.get("id") == key_id:
                clean_key = str(k.get("key", "")).strip()
                break

    if not clean_key and api_key and "****" not in api_key:
        clean_key = api_key.strip()

    if not clean_key:
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
