"""
tests/test_project_config.py
============================
Validation and regression tests for the single validated ProjectConfig model:
- Missing configuration fallback
- Malformed configuration handling with explicit warnings
- Debounce below minimum (0.5s)
- Debounce above maximum (3600.0s)
- Max burst smaller than debounce invariant (max_burst_s >= debounce_s)
- Resume without saved config (no rogue 300s default)
- UI setting persistence via project.meta
- Hot reload updating the shared configuration object
- Full configuration precedence resolution (CLI override -> Persisted -> Engine default)
"""
from __future__ import annotations

import json
import logging
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from scripts.core.project_config import (
    DEFAULT_AI_PROFILE,
    DEFAULT_AI_SEND_CODE,
    DEFAULT_AUTO_PUSH,
    DEFAULT_DEBOUNCE_S,
    DEFAULT_GIT_POLICY,
    DEFAULT_MAX_BURST_S,
    DEFAULT_SCAN_POLICY,
    DEFAULT_WATCH_POLICY,
    MAX_DEBOUNCE_S,
    MIN_DEBOUNCE_S,
    ProjectConfig,
    parse_meta_text,
)
from scripts.core.worker_lifecycle import WorkerState
from scripts.event_bundler import EventBundler
from scripts.storage_rotator import read_project_meta, synthesize_project_meta, update_project_meta


class TestProjectConfig(unittest.TestCase):
    """Test suite for ProjectConfig validation, precedence, and hot-reload."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="spd_cfg_test_"))
        self.engine_root = self.temp_dir / "engine"
        self.engine_root.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # 1. Missing config
    def test_missing_config_falls_back_to_engine_defaults(self) -> None:
        """When configuration is missing, engine defaults must be applied cleanly."""
        cfg = ProjectConfig()
        self.assertEqual(cfg.debounce_s, DEFAULT_DEBOUNCE_S)
        self.assertEqual(cfg.max_burst_s, DEFAULT_MAX_BURST_S)
        self.assertEqual(cfg.watch_policy, DEFAULT_WATCH_POLICY)
        self.assertEqual(cfg.scan_policy, DEFAULT_SCAN_POLICY)
        self.assertEqual(cfg.git_policy, DEFAULT_GIT_POLICY)
        self.assertEqual(cfg.ai_profile, DEFAULT_AI_PROFILE)
        self.assertEqual(cfg.ai_send_code, DEFAULT_AI_SEND_CODE)
        self.assertEqual(cfg.auto_push, DEFAULT_AUTO_PUSH)
        self.assertEqual(len(cfg.warnings), 0)

        # Resolving with missing persisted path also yields defaults and logs warning
        non_existent_meta = self.engine_root / "missing" / "project.meta"
        resolved = ProjectConfig.resolve(persisted_meta_path=non_existent_meta)
        self.assertEqual(resolved.debounce_s, DEFAULT_DEBOUNCE_S)
        self.assertEqual(resolved.max_burst_s, DEFAULT_MAX_BURST_S)
        self.assertTrue(any("not found" in w.lower() for w in resolved.warnings))

    # 2. Malformed config
    def test_malformed_config_falls_back_with_warning(self) -> None:
        """Malformed configuration values fall back to defaults and record explicit warnings."""
        malformed_dict = {
            "debounce_s": "not_a_valid_number",
            "max_burst_s": -10.0,
            "watch_policy": 12345,  # Expected dict
            "scan_policy": "not_a_dict",
            "git_policy": None,
            "ai_profile": "   ",  # Blank string
            "ai_send_code": "unknown_value",
            "auto_push": "not_a_bool",
        }
        cfg = ProjectConfig()
        cfg.update_from_dict(malformed_dict)

        # Assert fallbacks
        self.assertEqual(cfg.debounce_s, DEFAULT_DEBOUNCE_S)
        self.assertEqual(cfg.max_burst_s, DEFAULT_MAX_BURST_S)
        self.assertEqual(cfg.watch_policy, DEFAULT_WATCH_POLICY)
        self.assertEqual(cfg.scan_policy, DEFAULT_SCAN_POLICY)
        self.assertEqual(cfg.git_policy, DEFAULT_GIT_POLICY)
        self.assertEqual(cfg.ai_profile, DEFAULT_AI_PROFILE)
        self.assertEqual(cfg.ai_send_code, DEFAULT_AI_SEND_CODE)
        self.assertEqual(cfg.auto_push, DEFAULT_AUTO_PUSH)

        # Assert explicit warnings recorded
        self.assertGreater(len(cfg.warnings), 0)
        warning_text = " ".join(cfg.warnings).lower()
        self.assertIn("debounce_s", warning_text)
        self.assertIn("max_burst_s", warning_text)
        self.assertIn("watch_policy", warning_text)
        self.assertIn("scan_policy", warning_text)
        self.assertIn("ai_profile", warning_text)

    # 3. Debounce below minimum (0.5s)
    def test_debounce_below_minimum_falls_back_with_warning(self) -> None:
        """debounce_s below MIN_DEBOUNCE_S (0.5s) must fall back to default and expose warning."""
        cfg = ProjectConfig(debounce_s=0.1)
        self.assertEqual(cfg.debounce_s, DEFAULT_DEBOUNCE_S)
        self.assertTrue(any("below minimum" in w.lower() for w in cfg.warnings))

        # Test via resolve with persisted config
        resolved = ProjectConfig.resolve(persisted_config={"debounce_window": 0.2})
        self.assertEqual(resolved.debounce_s, DEFAULT_DEBOUNCE_S)
        self.assertTrue(any("below minimum" in w.lower() for w in resolved.warnings))

        # Test CLI override below minimum
        resolved_cli = ProjectConfig.resolve(
            cli_overrides={"debounce": 0.05},
            persisted_config={"debounce": 10.0},
        )
        self.assertEqual(resolved_cli.debounce_s, 10.0)
        self.assertTrue(any("below minimum" in w.lower() for w in resolved_cli.warnings))

    # 4. Debounce above maximum (3600.0s)
    def test_debounce_above_maximum_falls_back_with_warning(self) -> None:
        """debounce_s above MAX_DEBOUNCE_S (3600.0s) must fall back to default and expose warning."""
        cfg = ProjectConfig(debounce_s=5000.0)
        self.assertEqual(cfg.debounce_s, DEFAULT_DEBOUNCE_S)
        self.assertTrue(any("above maximum" in w.lower() for w in cfg.warnings))

        # Test via resolve with persisted config
        resolved = ProjectConfig.resolve(persisted_config={"debounce_window": 7200.0})
        self.assertEqual(resolved.debounce_s, DEFAULT_DEBOUNCE_S)
        self.assertTrue(any("above maximum" in w.lower() for w in resolved.warnings))

        # Test CLI override above maximum
        resolved_cli = ProjectConfig.resolve(
            cli_overrides={"debounce": 9999.0},
            persisted_config={"debounce": 15.0},
        )
        self.assertEqual(resolved_cli.debounce_s, 15.0)
        self.assertTrue(any("above maximum" in w.lower() for w in resolved_cli.warnings))

    # 5. Max burst smaller than debounce (invariant: max_burst_s >= debounce_s * 2.0)
    def test_max_burst_smaller_than_debounce_enforces_invariant(self) -> None:
        """max_burst_s cannot be smaller than 2x debounce_s; must adjust and expose warning."""
        # Case A: debounce is 100.0, max_burst requested as 30.0
        cfg = ProjectConfig(debounce_s=100.0, max_burst_s=30.0, extended_soak=True)
        self.assertEqual(cfg.debounce_s, 100.0)
        self.assertGreaterEqual(cfg.max_burst_s, cfg.debounce_s * 2.0)
        self.assertEqual(cfg.max_burst_s, 200.0)
        self.assertTrue(any("cannot be smaller than 2x debounce_s" in w.lower() for w in cfg.warnings))

    def test_debounce_above_60s_not_clamped_and_auto_extended_soak(self) -> None:
        """debounce_s above 60.0s is allowed up to 3600.0s and auto-enables extended_soak."""
        cfg = ProjectConfig(debounce_s=120.0)
        self.assertEqual(cfg.debounce_s, 120.0)
        self.assertTrue(cfg.extended_soak)
        self.assertGreaterEqual(cfg.max_burst_s, 240.0)

        cfg_soak = ProjectConfig(debounce_s=120.0, extended_soak=True)
        self.assertEqual(cfg_soak.debounce_s, 120.0)
        self.assertGreaterEqual(cfg_soak.max_burst_s, 240.0)

        resolved = ProjectConfig.resolve(persisted_config={"debounce": 120.0})
        self.assertEqual(resolved.debounce_s, 120.0)
        self.assertTrue(resolved.extended_soak)

        resolved_soak = ProjectConfig.resolve(persisted_config={"debounce": 120.0, "extended_soak": True})
        self.assertEqual(resolved_soak.debounce_s, 120.0)

        # Case B: debounce is 10.0, max_burst requested as 5.0
        cfg_b = ProjectConfig(debounce_s=10.0, max_burst_s=5.0)
        self.assertEqual(cfg_b.debounce_s, 10.0)
        self.assertGreaterEqual(cfg_b.max_burst_s, cfg_b.debounce_s * 2.0)
        self.assertEqual(cfg_b.max_burst_s, 20.0)  # max(5.0, 10.0 * 2) = 20.0
        self.assertTrue(any("cannot be smaller than 2x debounce_s" in w.lower() for w in cfg_b.warnings))

        # Case C: EventBundler honors max_burst_s bound
        flushed_bundles: list[dict] = []
        bundler = EventBundler(
            callback=lambda b: flushed_bundles.append(b),
            quiet_period=1.0,
            max_burst_s=0.2,  # Force short max burst
        )
        # Verify bundler enforced invariant internally
        self.assertGreaterEqual(bundler.max_burst_s, bundler.quiet_period)

    # 6. Resume without saved config
    def test_resume_without_saved_config_uses_validated_default(self) -> None:
        """Resuming a project without saved config must NOT use rogue 300s or 600s defaults."""
        slug = "fresh_resume_proj"
        target_dir = self.temp_dir / "target_workspace"
        target_dir.mkdir(parents=True, exist_ok=True)

        # Synthesize metadata without an existing project.meta or session.db
        synth = synthesize_project_meta(self.engine_root, slug, persist=False)
        self.assertIsNone(synth)  # None when no prior runs exist

        # Test resolving config with empty/missing persisted meta
        meta = read_project_meta(self.engine_root, slug)
        resolved = ProjectConfig.resolve(persisted_config=meta)

        self.assertEqual(resolved.debounce_s, DEFAULT_DEBOUNCE_S)
        self.assertNotEqual(resolved.debounce_s, 300.0)
        self.assertNotEqual(resolved.debounce_s, 600.0)
        self.assertEqual(resolved.max_burst_s, DEFAULT_MAX_BURST_S)

    # 7. UI setting persistence
    def test_ui_setting_persistence_in_project_meta(self) -> None:
        """UI updates to debounce and policies persist cleanly in project.meta."""
        slug = "ui_persisted_proj"
        proj_dir = self.engine_root / "current" / slug
        proj_dir.mkdir(parents=True, exist_ok=True)
        meta_file = proj_dir / "project.meta"

        # Simulate UI updating debounce to 45.0s, enabling auto_push, and modifying powers
        update_project_meta(
            self.engine_root,
            slug,
            target_path=str(self.temp_dir),
            debounce_window=45.0,
            auto_push="true",
            powers={"track_edits": True, "track_reads": False, "track_exec": True, "shadow_git": True},
            explicit_debounce=True,
        )

        self.assertTrue(meta_file.exists())
        saved_meta = read_project_meta(self.engine_root, slug)
        self.assertEqual(saved_meta.get("debounce_window"), "45.0")
        self.assertEqual(saved_meta.get("auto_push"), "true")

        # Load with ProjectConfig
        cfg = ProjectConfig.resolve(persisted_meta_path=meta_file)
        self.assertEqual(cfg.debounce_s, 45.0)
        self.assertTrue(cfg.auto_push)
        self.assertTrue(cfg.watch_policy.get("track_edits"))
        self.assertFalse(cfg.watch_policy.get("track_reads"))
        self.assertTrue(cfg.watch_policy.get("track_exec"))

    # 8. Hot reload updating shared configuration object
    def test_hot_reload_updates_shared_configuration_object(self) -> None:
        """Modifying project.meta on disk updates the shared config and EventBundler in-place."""
        slug = "hot_reload_proj"
        proj_dir = self.engine_root / "current" / slug
        proj_dir.mkdir(parents=True, exist_ok=True)
        meta_file = proj_dir / "project.meta"

        # Initial metadata
        meta_file.write_text("debounce_window=3.5\nmax_burst_s=60.0\nauto_push=false\n", encoding="utf-8")

        # Initialize shared configuration and state
        shared_cfg = ProjectConfig.resolve(persisted_meta_path=meta_file)
        self.assertEqual(shared_cfg.debounce_s, 3.5)

        bundler = EventBundler(config=shared_cfg)
        self.assertEqual(bundler.quiet_period, 3.5)
        self.assertEqual(bundler.max_burst_s, 60.0)

        # Setup worker state holding shared config
        state = WorkerState()
        state.config = shared_cfg
        state.event_bundler = bundler
        state.project_name = slug

        # Register listener to sync bundler on hot reload
        def _sync(cfg: ProjectConfig) -> None:
            state.event_bundler.quiet_period = cfg.debounce_s
            state.event_bundler.configured_quiet_period = cfg.debounce_s
            state.event_bundler.max_burst_s = cfg.max_burst_s

        state.config.add_listener(_sync)

        # Simulate user updating settings via Web UI / project.meta on disk
        time.sleep(0.05)  # Ensure mtime change
        meta_file.write_text("debounce_window=14.0\nmax_burst_s=120.0\nauto_push=true\n", encoding="utf-8")

        # Trigger hot reload
        changed = state.config.reload_from_meta(meta_file)
        self.assertTrue(changed)

        # Assert shared config object was updated in-place
        self.assertEqual(shared_cfg.debounce_s, 14.0)
        self.assertEqual(shared_cfg.max_burst_s, 120.0)
        self.assertTrue(shared_cfg.auto_push)

        # Assert EventBundler was updated in-place without worker restart
        self.assertEqual(bundler.quiet_period, 14.0)
        self.assertEqual(bundler.configured_quiet_period, 14.0)
        self.assertEqual(bundler.max_burst_s, 120.0)

    # 9. Configuration precedence hierarchy
    def test_configuration_precedence_hierarchy(self) -> None:
        """
        Configuration precedence must strictly resolve:
        validated CLI override -> persisted project config -> engine default
        """
        # Tier 3 only: Engine defaults
        cfg_default = ProjectConfig.resolve()
        self.assertEqual(cfg_default.debounce_s, 3.5)

        # Tier 2: Persisted overrides Engine default
        cfg_persisted = ProjectConfig.resolve(persisted_config={"debounce_window": 12.0})
        self.assertEqual(cfg_persisted.debounce_s, 12.0)

        # Tier 1: CLI override overrides Persisted config
        cfg_cli = ProjectConfig.resolve(
            cli_overrides={"debounce": 25.0},
            persisted_config={"debounce_window": 12.0},
        )
        self.assertEqual(cfg_cli.debounce_s, 25.0)

        # Tier 1 invalid: Invalid CLI override falls back to persisted config with warning
        cfg_invalid_cli = ProjectConfig.resolve(
            cli_overrides={"debounce": 9999.0},  # Out of range > 3600
            persisted_config={"debounce_window": 12.0},
        )
        self.assertEqual(cfg_invalid_cli.debounce_s, 12.0)
        self.assertTrue(any("above maximum" in w.lower() for w in cfg_invalid_cli.warnings))


if __name__ == "__main__":
    unittest.main()
