"""
tests/test_import_normalization.py
==================================
Comprehensive test suite verifying canonical namespace import normalization,
backward-compatibility facades, module/singleton identity across namespaces,
clean process startup, worker startup, and test discovery.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ENGINE_ROOT = Path(__file__).resolve().parent.parent
PYTHON_EXE = sys.executable


class TestCanonicalImports(unittest.TestCase):
    """Verify importing directly from the canonical spd_analysis_engine namespace."""

    def test_canonical_root_import(self):
        import spd_analysis_engine
        self.assertIsNotNone(spd_analysis_engine)
        self.assertTrue(hasattr(spd_analysis_engine, "Supervisor"))
        self.assertTrue(hasattr(spd_analysis_engine, "StorageRotator"))
        self.assertTrue(hasattr(spd_analysis_engine, "SessionDB"))
        self.assertTrue(hasattr(spd_analysis_engine, "DiffCalculator"))
        self.assertTrue(hasattr(spd_analysis_engine, "EventBundler"))

    def test_canonical_scripts_modules(self):
        import spd_analysis_engine.scripts as can_scripts
        import spd_analysis_engine.scripts.orchestrator as can_orch
        import spd_analysis_engine.scripts.storage_rotator as can_sr
        import spd_analysis_engine.scripts.diff_calc as can_dc
        import spd_analysis_engine.scripts.event_bundler as can_eb
        import spd_analysis_engine.scripts.git_shadow as can_gs
        import spd_analysis_engine.scripts.core.project_config as can_cfg
        import spd_analysis_engine.scripts.core.worker_lifecycle as can_wl

        self.assertIsNotNone(can_scripts)
        self.assertIsNotNone(can_orch.Supervisor)
        self.assertIsNotNone(can_sr.SessionDB)
        self.assertIsNotNone(can_sr.StorageRotator)
        self.assertIsNotNone(can_dc.DiffCalculator)
        self.assertIsNotNone(can_eb.EventBundler)
        self.assertIsNotNone(can_gs.ShadowGit)
        self.assertIsNotNone(can_cfg.ProjectConfig)
        self.assertIsNotNone(can_wl)

    def test_canonical_subpackages(self):
        import spd_analysis_engine.scripts.ai.rate_limiter as ai_rl
        import spd_analysis_engine.scripts.server.security as srv_sec
        import spd_analysis_engine.scripts.shell.tracker as sh_tr

        self.assertIsNotNone(ai_rl)
        self.assertIsNotNone(srv_sec)
        self.assertIsNotNone(sh_tr)


class TestLegacyFacadeImports(unittest.TestCase):
    """Verify importing through the backward-compatible scripts facade."""

    def test_legacy_scripts_modules(self):
        import scripts as leg_scripts
        import scripts.orchestrator as leg_orch
        import scripts.storage_rotator as leg_sr
        import scripts.diff_calc as leg_dc
        import scripts.event_bundler as leg_eb
        import scripts.git_shadow as leg_gs
        import scripts.core.project_config as leg_cfg

        self.assertIsNotNone(leg_scripts)
        self.assertIsNotNone(leg_orch.Supervisor)
        self.assertIsNotNone(leg_sr.SessionDB)
        self.assertIsNotNone(leg_sr.StorageRotator)
        self.assertIsNotNone(leg_dc.DiffCalculator)
        self.assertIsNotNone(leg_eb.EventBundler)
        self.assertIsNotNone(leg_gs.ShadowGit)
        self.assertIsNotNone(leg_cfg.ProjectConfig)

    def test_legacy_facade_symbols(self):
        from scripts import SessionDB, StorageRotator, Supervisor, DiffCalculator, EventBundler
        self.assertIsNotNone(SessionDB)
        self.assertIsNotNone(StorageRotator)
        self.assertIsNotNone(Supervisor)
        self.assertIsNotNone(DiffCalculator)
        self.assertIsNotNone(EventBundler)


class TestNoDuplicateModuleAndSingletonInstances(unittest.TestCase):
    """
    Verify in fresh subprocesses that importing canonical first or legacy first
    results in the EXACT same module and singleton instances (is identity).
    """

    def test_canonical_first_order_identity(self):
        code = """
import sys
import tempfile
import spd_analysis_engine.scripts.orchestrator as can_orch
import scripts.orchestrator as leg_orch
import spd_analysis_engine.scripts.storage_rotator as can_sr
import scripts.storage_rotator as leg_sr
import spd_analysis_engine.scripts.diff_calc as can_dc
import scripts.diff_calc as leg_dc
import spd_analysis_engine.scripts.event_bundler as can_eb
import scripts.event_bundler as leg_eb
import spd_analysis_engine.scripts.git_shadow as can_gs
import scripts.git_shadow as leg_gs
import spd_analysis_engine.scripts.core.project_config as can_cfg
import scripts.core.project_config as leg_cfg

# Module identity
assert can_orch is leg_orch, f"orchestrator mismatch: {can_orch} vs {leg_orch}"
assert can_sr is leg_sr, f"storage_rotator mismatch: {can_sr} vs {leg_sr}"
assert can_dc is leg_dc, f"diff_calc mismatch: {can_dc} vs {leg_dc}"
assert can_eb is leg_eb, f"event_bundler mismatch: {can_eb} vs {leg_eb}"
assert can_gs is leg_gs, f"git_shadow mismatch: {can_gs} vs {leg_gs}"
assert can_cfg is leg_cfg, f"project_config mismatch: {can_cfg} vs {leg_cfg}"

# Class identity
assert can_orch.Supervisor is leg_orch.Supervisor, "Supervisor class mismatch"
assert can_sr.SessionDB is leg_sr.SessionDB, "SessionDB class mismatch"
assert can_sr.StorageRotator is leg_sr.StorageRotator, "StorageRotator class mismatch"
assert can_dc.DiffCalculator is leg_dc.DiffCalculator, "DiffCalculator class mismatch"
assert can_cfg.ProjectConfig is leg_cfg.ProjectConfig, "ProjectConfig class mismatch"

# sys.modules registration
assert sys.modules["scripts.orchestrator"] is sys.modules["spd_analysis_engine.scripts.orchestrator"]
assert sys.modules["scripts.storage_rotator"] is sys.modules["spd_analysis_engine.scripts.storage_rotator"]

# Singleton instance identity
with tempfile.TemporaryDirectory() as tmp:
    s1 = can_orch.Supervisor(tmp)
    s2 = leg_orch.Supervisor(tmp)
    assert s1 is s2, f"Supervisor singleton mismatch: {s1} is not {s2}"

print("OK_CANONICAL_FIRST")
"""
        result = subprocess.run(
            [PYTHON_EXE, "-c", code],
            cwd=str(ENGINE_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"Subprocess failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )
        self.assertIn("OK_CANONICAL_FIRST", result.stdout)

    def test_legacy_first_order_identity(self):
        code = """
import sys
import tempfile
import scripts.orchestrator as leg_orch
import spd_analysis_engine.scripts.orchestrator as can_orch
import scripts.storage_rotator as leg_sr
import spd_analysis_engine.scripts.storage_rotator as can_sr
import scripts.diff_calc as leg_dc
import spd_analysis_engine.scripts.diff_calc as can_dc
import scripts.event_bundler as leg_eb
import spd_analysis_engine.scripts.event_bundler as can_eb
import scripts.git_shadow as leg_gs
import spd_analysis_engine.scripts.git_shadow as can_gs
import scripts.core.project_config as leg_cfg
import spd_analysis_engine.scripts.core.project_config as can_cfg

# Module identity
assert leg_orch is can_orch, f"orchestrator mismatch: {leg_orch} vs {can_orch}"
assert leg_sr is can_sr, f"storage_rotator mismatch: {leg_sr} vs {can_sr}"
assert leg_dc is can_dc, f"diff_calc mismatch: {leg_dc} vs {can_dc}"
assert leg_eb is can_eb, f"event_bundler mismatch: {leg_eb} vs {can_eb}"
assert leg_gs is can_gs, f"git_shadow mismatch: {leg_gs} vs {can_gs}"
assert leg_cfg is can_cfg, f"project_config mismatch: {leg_cfg} vs {can_cfg}"

# Class identity
assert leg_orch.Supervisor is can_orch.Supervisor, "Supervisor class mismatch"
assert leg_sr.SessionDB is can_sr.SessionDB, "SessionDB class mismatch"
assert leg_sr.StorageRotator is can_sr.StorageRotator, "StorageRotator class mismatch"
assert leg_dc.DiffCalculator is can_dc.DiffCalculator, "DiffCalculator class mismatch"
assert leg_cfg.ProjectConfig is can_cfg.ProjectConfig, "ProjectConfig class mismatch"

# sys.modules registration
assert sys.modules["scripts.orchestrator"] is sys.modules["spd_analysis_engine.scripts.orchestrator"]
assert sys.modules["scripts.storage_rotator"] is sys.modules["spd_analysis_engine.scripts.storage_rotator"]

# Singleton instance identity
with tempfile.TemporaryDirectory() as tmp:
    s1 = leg_orch.Supervisor(tmp)
    s2 = can_orch.Supervisor(tmp)
    assert s1 is s2, f"Supervisor singleton mismatch: {s1} is not {s2}"

print("OK_LEGACY_FIRST")
"""
        result = subprocess.run(
            [PYTHON_EXE, "-c", code],
            cwd=str(ENGINE_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"Subprocess failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )
        self.assertIn("OK_LEGACY_FIRST", result.stdout)


class TestCleanProcessStartup(unittest.TestCase):
    """Verify clean CLI process startup without import or initialization errors."""

    def test_start_py_help(self):
        result = subprocess.run(
            [PYTHON_EXE, "start.py", "--help"],
            cwd=str(ENGINE_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("Phase 1 CLI", result.stdout)
        self.assertIn("positional arguments:", result.stdout)

    def test_start_py_list_json(self):
        result = subprocess.run(
            [PYTHON_EXE, "start.py", "--json", "list"],
            cwd=str(ENGINE_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertIn("running", data)
        self.assertIn("on_disk", data)

    def test_start_py_status_json(self):
        result = subprocess.run(
            [PYTHON_EXE, "start.py", "--json", "status"],
            cwd=str(ENGINE_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertIsInstance(data, list)


class TestWorkerStartup(unittest.TestCase):
    """Verify worker process startup via canonical and legacy namespaces."""

    def test_canonical_worker_module_help(self):
        result = subprocess.run(
            [PYTHON_EXE, "-m", "spd_analysis_engine.scripts.project_worker", "--help"],
            cwd=str(ENGINE_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("SPD Analysis Engine worker", result.stdout)

    def test_legacy_worker_module_help(self):
        result = subprocess.run(
            [PYTHON_EXE, "-m", "scripts.project_worker", "--help"],
            cwd=str(ENGINE_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("SPD Analysis Engine worker", result.stdout)


class TestTestDiscovery(unittest.TestCase):
    """Verify standard test discovery finds and executes tests cleanly."""

    def test_unittest_discovery_subset(self):
        result = subprocess.run(
            [
                PYTHON_EXE,
                "-m",
                "unittest",
                "discover",
                "-s",
                "tests",
                "-p",
                "test_lifecycle_authority.py",
            ],
            cwd=str(ENGINE_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        self.assertEqual(result.returncode, 0, f"Unittest discovery failed: {result.stderr}")
        self.assertIn("OK", result.stderr)


if __name__ == "__main__":
    unittest.main()
