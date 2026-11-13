"""
scripts/core/project_config.py
==============================
Single validated project configuration model for the SPD Analysis Engine.

Enforces configuration precedence:
    validated CLI override
            ↓
    persisted project config
            ↓
    engine default

Missing or invalid configuration values fall back to validated defaults
and record/log explicit warnings.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Engine Defaults & Invariant Bounds
DEFAULT_DEBOUNCE_S: float = 3.5
MIN_DEBOUNCE_S: float = 0.5
MAX_STANDARD_DEBOUNCE_S: float = 3600.0
MAX_DEBOUNCE_S: float = 3600.0

DEFAULT_MAX_BURST_S: float = 60.0

DEFAULT_AI_PROFILE: str = "antigravity"
DEFAULT_AI_SEND_CODE: bool = True
DEFAULT_AUTO_PUSH: bool = False

DEFAULT_WATCH_POLICY: dict[str, bool] = {
    "track_edits": True,
    "track_reads": True,
    "track_exec": True,
}

DEFAULT_SCAN_POLICY: dict[str, Any] = {
    "exclude_patterns": [".git", ".spd", "__pycache__", "node_modules", ".venv*", "dist", "build"],
    "poll_interval_s": 1.0,
}

DEFAULT_GIT_POLICY: dict[str, Any] = {
    "shadow_git": True,
    "git_init_primary": False,
    "auto_push": False,
}


def _parse_bool(val: Any, default: bool) -> tuple[bool, bool]:
    """Parse bool candidate. Returns (value, is_valid)."""
    if isinstance(val, bool):
        return val, True
    v_str = str(val).lower().strip()
    if v_str in ("true", "1", "yes", "on"):
        return True, True
    if v_str in ("false", "0", "no", "off"):
        return False, True
    return default, False


def parse_meta_text(raw: str) -> dict[str, Any]:
    """Parse metadata text supporting both JSON format and key=value lines."""
    text = raw.strip()
    if not text:
        return {}

    if text.startswith("{"):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass

    data: dict[str, Any] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip()
        if (v.startswith("{") and v.endswith("}")) or (v.startswith("[") and v.endswith("]")):
            try:
                data[k] = json.loads(v)
                continue
            except Exception:
                pass
        data[k] = v
    return data


@dataclass
class ProjectConfig:
    """Validated project configuration container."""

    debounce_s: float = DEFAULT_DEBOUNCE_S
    max_burst_s: float = DEFAULT_MAX_BURST_S
    watch_policy: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_WATCH_POLICY))
    scan_policy: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_SCAN_POLICY))
    git_policy: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_GIT_POLICY))
    ai_profile: str = DEFAULT_AI_PROFILE
    ai_send_code: bool = DEFAULT_AI_SEND_CODE
    auto_push: bool = DEFAULT_AUTO_PUSH
    extended_soak: bool = False
    warnings: list[str] = field(default_factory=list)
    _listeners: list[Callable[[ProjectConfig], None]] = field(default_factory=list, repr=False, compare=False)

    def __post_init__(self) -> None:
        self.validate()

    @property
    def debounce(self) -> float:
        return self.debounce_s

    @property
    def debounce_window(self) -> float:
        return self.debounce_s

    @property
    def powers(self) -> dict[str, Any]:
        p = dict(self.watch_policy)
        p["shadow_git"] = bool(self.git_policy.get("shadow_git", True))
        p["ide_profile"] = self.ai_profile
        return p

    def add_listener(self, listener: Callable[[ProjectConfig], None]) -> None:
        if listener not in self._listeners:
            self._listeners.append(listener)

    def notify_listeners(self) -> None:
        for cb in list(self._listeners):
            try:
                cb(self)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Config listener exception: %s", exc)

    def validate(self) -> list[str]:
        """Validate fields, enforce ranges/invariants, and return newly emitted warnings."""
        new_warnings: list[str] = []

        def _warn(msg: str) -> None:
            logger.warning(msg)
            self.warnings.append(msg)
            new_warnings.append(msg)

        # 1. Debounce range
        try:
            val = float(self.debounce_s)
            if val < MIN_DEBOUNCE_S:
                _warn(f"debounce_s ({val}s) is below minimum allowed ({MIN_DEBOUNCE_S}s); falling back to default {DEFAULT_DEBOUNCE_S}s")
                self.debounce_s = DEFAULT_DEBOUNCE_S
            elif val > MAX_DEBOUNCE_S:
                _warn(f"debounce_s ({val}s) is above maximum allowed ({MAX_DEBOUNCE_S}s); falling back to default {DEFAULT_DEBOUNCE_S}s")
                self.debounce_s = DEFAULT_DEBOUNCE_S
            else:
                if val > 60.0:
                    self.extended_soak = True
                self.debounce_s = val
        except (ValueError, TypeError):
            _warn(f"debounce_s ({self.debounce_s!r}) is not numeric; falling back to default {DEFAULT_DEBOUNCE_S}s")
            self.debounce_s = DEFAULT_DEBOUNCE_S

        # 2. Max burst & invariant max_burst_s >= debounce_s * 2.0
        try:
            mb = float(self.max_burst_s)
            min_burst = max(DEFAULT_MAX_BURST_S, self.debounce_s * 2.0)
            if mb <= 0:
                _warn(f"max_burst_s ({mb}s) must be positive; falling back to {min_burst}s")
                mb = min_burst
            elif mb < self.debounce_s * 2.0:
                target_mb = max(mb, self.debounce_s * 2.0)
                _warn(f"max_burst_s ({mb}s) cannot be smaller than 2x debounce_s ({self.debounce_s * 2.0}s); adjusting to {target_mb}s")
                mb = target_mb
            self.max_burst_s = mb
        except (ValueError, TypeError):
            target_mb = max(DEFAULT_MAX_BURST_S, self.debounce_s * 2.0)
            _warn(f"max_burst_s ({self.max_burst_s!r}) is not numeric; falling back to {target_mb}s")
            self.max_burst_s = target_mb

        # 3. Policies
        if not isinstance(self.watch_policy, dict):
            _warn(f"watch_policy expected dict, got {type(self.watch_policy).__name__}; using default")
            self.watch_policy = dict(DEFAULT_WATCH_POLICY)
        else:
            for k, def_v in DEFAULT_WATCH_POLICY.items():
                if k not in self.watch_policy:
                    self.watch_policy[k] = def_v
                else:
                    self.watch_policy[k], _ = _parse_bool(self.watch_policy[k], def_v)

        if not isinstance(self.scan_policy, dict):
            _warn(f"scan_policy expected dict, got {type(self.scan_policy).__name__}; using default")
            self.scan_policy = dict(DEFAULT_SCAN_POLICY)

        if not isinstance(self.git_policy, dict):
            _warn(f"git_policy expected dict, got {type(self.git_policy).__name__}; using default")
            self.git_policy = dict(DEFAULT_GIT_POLICY)

        # 4. AI profile & flags
        if not isinstance(self.ai_profile, str) or not self.ai_profile.strip():
            _warn(f"ai_profile invalid ({self.ai_profile!r}); using default '{DEFAULT_AI_PROFILE}'")
            self.ai_profile = DEFAULT_AI_PROFILE
        else:
            self.ai_profile = self.ai_profile.strip()

        sc_val, sc_ok = _parse_bool(self.ai_send_code, DEFAULT_AI_SEND_CODE)
        if not sc_ok:
            _warn(f"ai_send_code invalid ({self.ai_send_code!r}); using default {DEFAULT_AI_SEND_CODE}")
        self.ai_send_code = sc_val

        ap_val, ap_ok = _parse_bool(self.auto_push, DEFAULT_AUTO_PUSH)
        if not ap_ok:
            _warn(f"auto_push invalid ({self.auto_push!r}); using default {DEFAULT_AUTO_PUSH}")
        self.auto_push = ap_val
        self.git_policy["auto_push"] = self.auto_push
        return new_warnings

    def update_from_dict(self, data: dict[str, Any]) -> bool:
        """Update configuration in-place from data, validating candidates and preserving state."""
        if not data:
            return False

        old = (self.debounce_s, self.max_burst_s, self.ai_profile, self.ai_send_code,
               self.auto_push, dict(self.watch_policy), dict(self.scan_policy), dict(self.git_policy))

        def _warn(msg: str) -> None:
            logger.warning(msg)
            self.warnings.append(msg)

        # Extended soak flag
        if "extended_soak" in data and data["extended_soak"] is not None:
            es_val, es_ok = _parse_bool(data["extended_soak"], self.extended_soak)
            if es_ok:
                self.extended_soak = es_val

        # 1. Debounce
        for k in ("debounce_s", "debounce_window", "debounce"):
            if k in data and data[k] is not None:
                try:
                    val = float(data[k])
                    if val < MIN_DEBOUNCE_S:
                        _warn(f"debounce_s ({val}s) is below minimum allowed ({MIN_DEBOUNCE_S}s); falling back to default {self.debounce_s}s")
                    elif val > MAX_DEBOUNCE_S:
                        _warn(f"debounce_s ({val}s) is above maximum allowed ({MAX_DEBOUNCE_S}s); falling back to default {self.debounce_s}s")
                    else:
                        if val > 60.0:
                            self.extended_soak = True
                        self.debounce_s = val
                except (ValueError, TypeError):
                    _warn(f"debounce_s ({data[k]!r}) is not numeric; falling back to default {self.debounce_s}s")
                break

        # 2. Max burst
        for k in ("max_burst_s", "max_burst"):
            if k in data and data[k] is not None:
                try:
                    val = float(data[k])
                    if val <= 0:
                        _warn(f"max_burst_s ({val}s) must be positive; keeping current {self.max_burst_s}s")
                    else:
                        self.max_burst_s = val
                except (ValueError, TypeError):
                    _warn(f"max_burst_s ({data[k]!r}) is not numeric; keeping current {self.max_burst_s}s")
                break

        # 3. Policies
        if "watch_policy" in data and data["watch_policy"] is not None:
            if isinstance(data["watch_policy"], dict):
                self.watch_policy.update(data["watch_policy"])
            else:
                _warn(f"watch_policy expected dict, got {type(data['watch_policy']).__name__}; using default")

        if "powers" in data and data["powers"] is not None:
            p = data["powers"]
            if isinstance(p, str):
                try:
                    p = json.loads(p)
                except Exception:
                    pass
            if isinstance(p, dict):
                for pk in ("track_edits", "track_reads", "track_exec"):
                    if pk in p:
                        self.watch_policy[pk] = p[pk]
                if "shadow_git" in p:
                    self.git_policy["shadow_git"] = p["shadow_git"]
                if "ide_profile" in p:
                    self.ai_profile = str(p["ide_profile"])

        for pk in ("track_edits", "track_reads", "track_exec"):
            if pk in data and data[pk] is not None:
                self.watch_policy[pk] = data[pk]

        if "scan_policy" in data and data["scan_policy"] is not None:
            if isinstance(data["scan_policy"], dict):
                self.scan_policy.update(data["scan_policy"])
            else:
                _warn(f"scan_policy expected dict, got {type(data['scan_policy']).__name__}; using default")

        if "git_policy" in data and data["git_policy"] is not None:
            if isinstance(data["git_policy"], dict):
                self.git_policy.update(data["git_policy"])
            else:
                _warn(f"git_policy expected dict, got {type(data['git_policy']).__name__}; using default")

        if "shadow_git" in data and data["shadow_git"] is not None:
            self.git_policy["shadow_git"] = data["shadow_git"]
        if "git_init_primary" in data and data["git_init_primary"] is not None:
            self.git_policy["git_init_primary"] = data["git_init_primary"]

        # 4. AI & Push
        for ak in ("ai_profile", "ide_profile"):
            if ak in data and data[ak] is not None:
                raw_prof = str(data[ak]).strip()
                if raw_prof:
                    self.ai_profile = raw_prof
                else:
                    _warn(f"ai_profile invalid ({data[ak]!r}); using default '{DEFAULT_AI_PROFILE}'")
                break

        if "ai_send_code" in data and data["ai_send_code"] is not None:
            sc_val, sc_ok = _parse_bool(data["ai_send_code"], self.ai_send_code)
            if sc_ok:
                self.ai_send_code = sc_val
            else:
                _warn(f"ai_send_code invalid ({data['ai_send_code']!r}); using default {DEFAULT_AI_SEND_CODE}")

        if "auto_push" in data and data["auto_push"] is not None:
            ap_val, ap_ok = _parse_bool(data["auto_push"], self.auto_push)
            if ap_ok:
                self.auto_push = ap_val
            else:
                _warn(f"auto_push invalid ({data['auto_push']!r}); using default {DEFAULT_AUTO_PUSH}")

        self.validate()

        new_state = (self.debounce_s, self.max_burst_s, self.ai_profile, self.ai_send_code,
                     self.auto_push, dict(self.watch_policy), dict(self.scan_policy), dict(self.git_policy))
        changed = (abs(old[0] - new_state[0]) > 1e-4 or abs(old[1] - new_state[1]) > 1e-4 or old[2:] != new_state[2:])
        if changed:
            self.notify_listeners()
        return changed

    def reload_from_meta(self, meta_path: Path | str) -> bool:
        """Reload configuration in-place from a project.meta file."""
        p = Path(meta_path)
        if not p.exists():
            msg = f"Cannot reload config: file '{p}' not found"
            logger.warning(msg)
            self.warnings.append(msg)
            return False
        try:
            data = parse_meta_text(p.read_text(encoding="utf-8"))
            return self.update_from_dict(data)
        except Exception as exc:  # noqa: BLE001
            msg = f"Failed to reload config from '{p}': {exc}"
            logger.warning(msg)
            self.warnings.append(msg)
            return False

    def to_dict(self) -> dict[str, Any]:
        """Convert configuration to a clean JSON-serializable dictionary."""
        return {
            "debounce_s": self.debounce_s,
            "max_burst_s": self.max_burst_s,
            "watch_policy": dict(self.watch_policy),
            "scan_policy": dict(self.scan_policy),
            "git_policy": dict(self.git_policy),
            "ai_profile": self.ai_profile,
            "ai_send_code": self.ai_send_code,
            "auto_push": self.auto_push,
            "extended_soak": self.extended_soak,
            "warnings": list(self.warnings),
        }

    def to_meta_dict(self) -> dict[str, str]:
        """Convert configuration to string key=value dictionary suitable for project.meta."""
        return {
            "debounce_window": str(self.debounce_s),
            "debounce": str(self.debounce_s),
            "max_burst_s": str(self.max_burst_s),
            "extended_soak": "true" if self.extended_soak else "false",
            "powers": json.dumps(self.powers),
            "ide_profile": self.ai_profile,
            "ai_send_code": "true" if self.ai_send_code else "false",
            "auto_push": "true" if self.auto_push else "false",
        }

    @classmethod
    def resolve(
        cls,
        cli_overrides: dict[str, Any] | Any | None = None,
        persisted_config: dict[str, Any] | None = None,
        persisted_meta_path: Path | str | None = None,
        base_config: ProjectConfig | None = None,
    ) -> ProjectConfig:
        """
        Build ProjectConfig resolving precedence:
            validated CLI override -> persisted project config -> engine default
        """
        cfg = cls() if base_config is None else cls(**base_config.to_dict())

        # Tier 2: Persisted project config
        p_data: dict[str, Any] = {}
        if persisted_meta_path is not None:
            p = Path(persisted_meta_path)
            if p.exists():
                try:
                    p_data = parse_meta_text(p.read_text(encoding="utf-8"))
                except Exception as exc:  # noqa: BLE001
                    msg = f"Malformed persisted config file '{p}': {exc}; using defaults"
                    logger.warning(msg)
                    cfg.warnings.append(msg)
            else:
                msg = f"Persisted config file '{p}' not found; using defaults"
                logger.warning(msg)
                cfg.warnings.append(msg)

        if persisted_config is not None:
            p_data.update(persisted_config)

        if p_data:
            cfg.update_from_dict(p_data)

        # Tier 1: Validated CLI overrides
        if cli_overrides is not None:
            cli_dict = vars(cli_overrides) if hasattr(cli_overrides, "__dict__") and not isinstance(cli_overrides, dict) else dict(cli_overrides)
            filtered_cli = {k: v for k, v in cli_dict.items() if v is not None}
            if filtered_cli:
                cfg.update_from_dict(filtered_cli)

        return cfg
