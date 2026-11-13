"""
scripts/ai/controller_executor.py
=================================
Controller-owned AI execution coordinator.
Owns provider calls, bounded pools, job lease recovery, retry rules, and progressive levels.
Workers may enqueue jobs but never hold provider API keys or make provider calls.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .controller_db import EngineDB
from .error_handling import (
    AuthenticationError,
    ConsentRequiredError,
    JSONParseError,
    RateLimitError,
    TimeoutError,
    classify_ai_exception,
    evaluate_retry_plan,
)
from .identity import (
    DEFAULT_SANITIZER_VERSION,
    PROMPT_VERSION,
    AnalysisIdentity,
    AnalysisStatus,
    ProgressiveLevel,
    compute_analysis_cache_key,
    compute_content_hash,
)
from .profiles import ExecutionProfile, ProfileConfig, get_profile_config
from .progressive_levels import build_progressive_prompt, parse_progressive_result
from .provider_gemini import call_gemini, validate_gemini_model
from .provider_ollama import call_ollama
from .provider_pool import BoundedProviderPool
from .rate_limiter import RateLimitManager
from .sanitizer import SANITIZER_VERSION, sanitize_context_dict, sanitize_diff_content

logger = logging.getLogger(__name__)


class ControllerAIExecutor:
    """Controller daemon engine that executes AI analysis jobs asynchronously."""

    def __init__(
        self,
        engine_root: Path | str,
        db: EngineDB | None = None,
        provider_pool: BoundedProviderPool | None = None,
        worker_id: str | None = None,
        lease_duration_s: float = 20.0,
        heartbeat_interval_s: float = 5.0,
        profile: str = ExecutionProfile.NORMAL,
        rate_limiter: RateLimitManager | None = None,
    ) -> None:
        self.engine_root = Path(engine_root).resolve()
        db_path = self.engine_root / "data" / "engine.db"
        self.db = db or EngineDB(db_path)
        self.provider_pool = provider_pool or BoundedProviderPool()
        self.worker_id = worker_id or f"ctrl_ai_{uuid.uuid4().hex[:8]}"
        self.lease_duration_s = lease_duration_s
        self.heartbeat_interval_s = heartbeat_interval_s
        self.rate_limiter = rate_limiter or RateLimitManager(max_rpm=15)
        self.profile_config = get_profile_config(profile)
        self.provider_pool.apply_profile(self.profile_config)

        self._stop_event = threading.Event()
        self._worker_thread: threading.Thread | None = None
        self._reclaim_timer: threading.Thread | None = None

        # Reclaim any abandoned job leases on startup so killed controller jobs resume
        self.db.reclaim_expired_leases()

    def start(self) -> None:
        """Start the controller AI execution worker."""
        if self._worker_thread and self._worker_thread.is_alive():
            return
        self._stop_event.clear()
        self._worker_thread = threading.Thread(target=self._run_loop, name=f"ControllerAI-{self.worker_id}", daemon=True)
        self._worker_thread.start()
        logger.info("ControllerAIExecutor started (worker_id=%s)", self.worker_id)

    def stop(self, timeout: float = 5.0) -> None:
        """Stop the controller AI execution worker."""
        self._stop_event.set()
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=timeout)
        logger.info("ControllerAIExecutor stopped")

    def set_execution_profile(self, profile: str | ProfileConfig) -> None:
        """Dynamically update execution profile and adapt provider pools."""
        if isinstance(profile, ProfileConfig):
            self.profile_config = profile
        else:
            self.profile_config = get_profile_config(profile)
        self.provider_pool.apply_profile(self.profile_config)
        logger.info("Updated execution profile to %s", self.profile_config.name)

    def check_cloud_consent(self, project_slug: str, provider: str) -> None:
        """
        Verify whether cloud analysis is permitted by profile and project configuration.
        Local providers (ollama, heuristic) are always permitted locally.
        Cloud providers (gemini) strictly require per-project consent.
        """
        prov = (provider or "").lower().strip()
        if "gemini" not in prov and "cloud" not in prov:
            return

        if not self.profile_config.allow_cloud_ai:
            raise ConsentRequiredError(
                f"Cloud AI analysis is disabled under the '{self.profile_config.name}' execution profile."
            )

        from scripts.storage.project_storage import read_project_config
        cfg = read_project_config(self.engine_root, project_slug)
        if not (cfg.get("cloud_ai_enabled") or cfg.get("cloud_consent")):
            raise ConsentRequiredError(
                f"Cloud AI analysis requires explicit per-project consent. "
                f"Set 'cloud_ai_enabled: true' in project configuration for '{project_slug}'."
            )

    def enqueue_analysis(
        self,
        project_slug: str,
        target_kind: str,
        target_id: str,
        diff_text: str,
        level: str = ProgressiveLevel.L1,
        profile: str = "default",
        context: dict[str, Any] | None = None,
        provider: str | None = None,
        model: str | None = None,
        prompt_version: str | None = None,
        sanitizer_version: str | None = None,
    ) -> str:
        """
        Public/worker-safe enqueue endpoint.
        Workers invoke this without requiring any provider API keys or clients.
        Diff and context are sanitized before caching/storage to ensure zero secret leakage.
        """
        ctx = context or {}
        # Defense-in-depth sanitization at enqueue boundary
        clean_diff = sanitize_diff_content(diff_text)
        clean_context = sanitize_context_dict(ctx)

        input_h = compute_content_hash(clean_diff)
        ctx_h = compute_content_hash(clean_context)
        prov = provider or "gemini"
        mod = model or ("gemini-3.1-flash-lite" if "gemini" in prov.lower() else "qwen2.5-coder:7b")
        eff_profile = profile if profile != "default" else f"{level.lower()}_analysis"
        pmt_version = prompt_version or PROMPT_VERSION
        san_version = sanitizer_version or SANITIZER_VERSION

        cache_key = compute_analysis_cache_key(
            target_kind=target_kind,
            target_id=target_id,
            profile=eff_profile,
            input_hash=input_h,
            model=mod,
            provider=prov,
            prompt_version=pmt_version,
            sanitizer_version=san_version,
            context_hash=ctx_h,
        )

        payload = {
            "diff_text": clean_diff,
            "context": clean_context,
            "provider": prov,
            "model": mod,
            "level": level,
            "profile": eff_profile,
            "input_hash": input_h,
            "context_hash": ctx_h,
            "prompt_version": pmt_version,
            "sanitizer_version": san_version,
        }

        return self.db.enqueue_job(
            project_slug=project_slug,
            target_kind=target_kind,
            target_id=target_id,
            payload=payload,
            level=level,
            profile=eff_profile,
            cache_key=cache_key,
        )

    def _run_loop(self) -> None:
        """Main loop: claim jobs, execute, handle leases & heartbeats."""
        reclaim_due = time.time() + 10.0
        while not self._stop_event.is_set():
            now = time.time()
            if now >= reclaim_due:
                reclaimed = self.db.reclaim_expired_leases()
                if reclaimed > 0:
                    logger.info("Resumed %d killed controller job leases", reclaimed)
                reclaim_due = now + 10.0

            job = self.db.claim_job(self.worker_id, lease_duration_s=self.lease_duration_s)
            if not job:
                poll_delay = max(0.2, self.profile_config.polling_interval_s)
                self._stop_event.wait(poll_delay)
                continue

            try:
                self._process_single_job(job)
            except Exception as exc:
                logger.exception("Unexpected error processing job %s: %s", job["id"], exc)

    def _process_single_job(self, job: dict[str, Any]) -> None:
        job_id = job["id"]
        payload = json.loads(job["payload_json"])
        level = job.get("level", ProgressiveLevel.L1)
        target_kind = job["target_kind"]
        target_id = job["target_id"]
        provider = payload.get("provider", "gemini")
        model = payload.get("model", "gemini-3.1-flash-lite")
        profile = job.get("profile", "default")
        diff_text = payload.get("diff_text", "")
        context = payload.get("context", {})
        retry_count = int(job.get("retry_count", 0))

        # Apply profile context limits
        lines = diff_text.splitlines()
        if len(lines) > self.profile_config.max_diff_lines:
            diff_text = "\n".join(lines[: self.profile_config.max_diff_lines]) + "\n... [diff truncated by profile limit]"
        raw_b = diff_text.encode("utf-8")
        if len(raw_b) > self.profile_config.max_context_bytes:
            diff_text = raw_b[: self.profile_config.max_context_bytes].decode("utf-8", errors="ignore") + "\n... [diff truncated by byte limit]"

        # Defense-in-depth sanitization
        clean_diff = sanitize_diff_content(diff_text)
        clean_context = sanitize_context_dict(context)

        # Versions and cache key
        p_version = payload.get("prompt_version") or PROMPT_VERSION
        s_version = payload.get("sanitizer_version") or SANITIZER_VERSION
        cache_key = job.get("cache_key") or compute_analysis_cache_key(
            target_kind, target_id, profile, payload.get("input_hash", ""),
            model, provider, p_version, s_version, payload.get("context_hash", ""),
        )
        existing = self.db.get_analysis_by_cache_key(cache_key)
        if existing and existing["status"] == AnalysisStatus.DONE:
            self.db.complete_job(job_id, analysis_id=existing["id"])
            return

        # Resolve parent analysis if higher level
        parent_analysis_id = None
        lower_level_output = None
        if level in (ProgressiveLevel.L2, ProgressiveLevel.L3, ProgressiveLevel.L4):
            prev_level = {
                ProgressiveLevel.L2: ProgressiveLevel.L1,
                ProgressiveLevel.L3: ProgressiveLevel.L2,
                ProgressiveLevel.L4: ProgressiveLevel.L3,
            }.get(level)
            if prev_level:
                lower_analyses = self.db.get_analyses_for_target(target_kind, target_id, level=prev_level)
                if lower_analyses:
                    latest = lower_analyses[-1]
                    parent_analysis_id = latest["id"]
                    lower_level_output = latest.get("result")

        # Heartbeat timer for active job lease
        hb_stop = threading.Event()

        def _heartbeat_worker() -> None:
            while not hb_stop.wait(self.heartbeat_interval_s):
                self.db.heartbeat_job(job_id, self.worker_id, extend_s=self.lease_duration_s)

        hb_thread = threading.Thread(target=_heartbeat_worker, daemon=True)
        hb_thread.start()

        start_t = time.perf_counter()
        try:
            # Cloud consent verification
            self.check_cloud_consent(job["project_slug"], provider)
            timeout = self.profile_config.timeout_s
            with self.provider_pool.acquire(provider, timeout=timeout):
                sys_inst, prompt = build_progressive_prompt(
                    level=level,
                    diff_text=clean_diff,
                    context=clean_context,
                    lower_level_output=lower_level_output,
                )
                res_dict, prompt_tok, comp_tok = self._call_provider(
                    provider=provider,
                    model=model,
                    sys_instruction=sys_inst,
                    prompt=prompt,
                )

            latency_ms = (time.perf_counter() - start_t) * 1000.0
            parsed_result = parse_progressive_result(level, res_dict)

            # Store done analysis
            analysis_id = self.db.record_analysis({
                "cache_key": cache_key,
                "project_slug": job["project_slug"],
                "target_kind": target_kind,
                "target_id": target_id,
                "profile": profile,
                "level": level,
                "input_hash": payload.get("input_hash", ""),
                "model": model,
                "provider": provider,
                "prompt_version": p_version,
                "sanitizer_version": s_version,
                "context_hash": payload.get("context_hash", ""),
                "parent_analysis_id": parent_analysis_id,
                "status": AnalysisStatus.DONE,
                "result_json": parsed_result,
                "prompt_tokens": prompt_tok,
                "completion_tokens": comp_tok,
                "total_tokens": prompt_tok + comp_tok,
                "latency_ms": latency_ms,
            })
            self.db.complete_job(job_id, analysis_id=analysis_id)

        except Exception as exc:
            latency_ms = (time.perf_counter() - start_t) * 1000.0
            err_class, typed_exc = classify_ai_exception(exc)
            retry_plan = evaluate_retry_plan(err_class, retry_count, max_retries=int(job.get("max_retries", 3)))

            logger.warning("Job %s failed with %s (%s). Retry plan: %s", job_id, err_class, exc, retry_plan.action)

            # Never store failure as a successful result
            self.db.record_analysis({
                "cache_key": cache_key,
                "project_slug": job["project_slug"],
                "target_kind": target_kind,
                "target_id": target_id,
                "profile": profile,
                "level": level,
                "input_hash": payload.get("input_hash", ""),
                "model": model,
                "provider": provider,
                "prompt_version": p_version,
                "sanitizer_version": s_version,
                "context_hash": payload.get("context_hash", ""),
                "parent_analysis_id": parent_analysis_id,
                "status": AnalysisStatus.FAILED,
                "result_json": None,
                "latency_ms": latency_ms,
                "error_class": err_class,
                "error_message": str(exc),
            })

            if retry_plan.should_retry:
                if retry_plan.action == "smaller_context":
                    payload["diff_text"] = diff_text[: len(diff_text) // 2]
                self.db.requeue_job(
                    job_id=job_id,
                    error_class=err_class,
                    error_message=str(exc),
                    retry_count=retry_count + 1,
                    payload_json=json.dumps(payload),
                )
                if retry_plan.delay_s > 0:
                    time.sleep(retry_plan.delay_s)
            else:
                self.db.fail_job(
                    job_id=job_id,
                    error_class=err_class,
                    error_message=str(exc),
                    retry_count=retry_count + 1,
                )

        finally:
            hb_stop.set()
            hb_thread.join(timeout=1.0)

    def _call_provider(
        self,
        provider: str,
        model: str,
        sys_instruction: str,
        prompt: str,
    ) -> tuple[dict[str, Any], int, int]:
        """Execute model call using controller-held credentials and profile settings."""
        prov = provider.lower()
        full_prompt = f"{sys_instruction}\n\n{prompt}".strip()
        timeout = self.profile_config.timeout_s

        if "gemini" in prov:
            validate_gemini_model(model)
            from .config import load_model_config
            cfg = load_model_config(self.engine_root)
            api_key = cfg.get("gemini", {}).get("api_key") or ""
            if not api_key:
                raise AuthenticationError("Gemini API key is not configured in controller vault")
            gemini_cfg = {"api_key": api_key, "default_model": model, "timeout_seconds": timeout}
            try:
                res = call_gemini(full_prompt, gemini_cfg, self.rate_limiter, json.loads, fast_fail=True)
                return res, 100, 150
            except Exception as e:
                err_lower = str(e).lower()
                if any(k in err_lower for k in ("429", "resource_exhausted", "503", "quota")):
                    raise RateLimitError(str(e)) from e
                if any(k in err_lower for k in ("401", "403", "invalid api key", "unauthenticated")):
                    raise AuthenticationError(str(e)) from e
                if any(k in err_lower for k in ("timeout", "timed out", "deadline")):
                    raise TimeoutError(str(e)) from e
                if "json" in err_lower:
                    raise JSONParseError(str(e)) from e
                raise

        if "ollama" in prov:
            from .config import load_model_config
            cfg = load_model_config(self.engine_root)
            ollama_cfg = {
                "base_url": cfg.get("ollama_base_url", "http://localhost:11434"),
                "default_model": model or cfg.get("ollama_model", "qwen2.5-coder:7b"),
                "timeout_seconds": timeout,
                "options": self.profile_config.get_ollama_options(),
                "num_ctx": self.profile_config.ollama_num_ctx,
                "num_gpu": self.profile_config.ollama_num_gpu,
                "keep_alive": self.profile_config.ollama_keep_alive,
            }
            try:
                res = call_ollama(full_prompt, ollama_cfg, json.loads)
                return res, 150, 200
            except Exception as e:
                err_lower = str(e).lower()
                if "timed out" in err_lower or "timeout" in err_lower:
                    raise TimeoutError(str(e)) from e
                if "json" in err_lower:
                    raise JSONParseError(str(e)) from e
                raise

        # Fallback heuristic provider for offline testing
        return {
            "intent": "Progressive synthesized code change",
            "category": "feat",
            "summary": "Synthesized code update executed by controller.",
        }, 50, 50
