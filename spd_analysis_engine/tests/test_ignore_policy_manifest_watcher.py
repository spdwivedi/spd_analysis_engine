"""
tests/test_ignore_policy_manifest_watcher.py
=============================================
Comprehensive test suite for:
- IgnorePolicy (multi-layered: built-in, .gitignore, .spdignore, sensitive blocklist)
- FileManifest (fingerprinting, hashing on size/mtime change, empty/binary file detection)
- FsEvent & EventCoalescer (editor atomic saves, directory move expansion into file renames)
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from scripts.core.fs_event import EventCoalescer, FsEvent, is_editor_temp_file
from scripts.storage.ignore_policy import IgnorePolicy
from scripts.storage.manifest import (
    EMPTY_SHA256,
    FileManifest,
    FileManifestEntry,
    file_manifest,
)


class TestIgnorePolicy(unittest.TestCase):
    """Test suite for IgnorePolicy multi-layer evaluation."""

    def setUp(self) -> None:
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="spd_ignore_test_"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_gitignore(self) -> None:
        """Verify .gitignore parsing and method evaluation."""
        gi_content = """
# Dependencies & build
node_modules/
build/
*.log
!important.log
/root_only.txt
temp/*.txt
"""
        policy = IgnorePolicy(
            root_path=self.tmp_dir,
            gitignore_content=gi_content,
        )

        # Ignored files
        self.assertFalse(policy.should_watch("build/output.js"))
        self.assertFalse(policy.should_index("build/output.js"))
        self.assertFalse(policy.should_diff("build/output.js"))
        self.assertFalse(policy.should_stage("build/output.js"))
        self.assertFalse(policy.should_send_to_ai("build/output.js"))

        self.assertFalse(policy.should_watch("logs/debug.log"))
        self.assertFalse(policy.should_diff("logs/debug.log"))

        # Negation !important.log should be allowed
        self.assertTrue(policy.should_watch("logs/important.log"))
        self.assertTrue(policy.should_diff("logs/important.log"))
        self.assertTrue(policy.should_stage("logs/important.log"))

        # Root only
        self.assertFalse(policy.should_watch("root_only.txt"))
        self.assertTrue(policy.should_watch("sub/root_only.txt"))

        # Regular non-ignored source files
        self.assertTrue(policy.should_watch("src/index.py"))
        self.assertTrue(policy.should_index("src/index.py"))
        self.assertTrue(policy.should_diff("src/index.py"))
        self.assertTrue(policy.should_stage("src/index.py"))
        self.assertTrue(policy.should_send_to_ai("src/index.py"))

    def test_spdignore(self) -> None:
        """Verify optional .spdignore rules take precedence and completely ignore files."""
        spd_content = """
secret_experiments/
*.local.json
internal_benchmark.py
"""
        policy = IgnorePolicy(
            root_path=self.tmp_dir,
            spdignore_content=spd_content,
        )

        self.assertTrue(policy.is_spdignored("secret_experiments/algo.py"))
        self.assertFalse(policy.should_watch("secret_experiments/algo.py"))
        self.assertFalse(policy.should_index("secret_experiments/algo.py"))
        self.assertFalse(policy.should_diff("secret_experiments/algo.py"))
        self.assertFalse(policy.should_stage("secret_experiments/algo.py"))
        self.assertFalse(policy.should_send_to_ai("secret_experiments/algo.py"))

        self.assertFalse(policy.should_watch("config.local.json"))
        self.assertFalse(policy.should_watch("internal_benchmark.py"))

        # Non-spdignored file is allowed
        self.assertTrue(policy.should_watch("config.production.json"))

    def test_sensitive_files(self) -> None:
        """
        Verify sensitive defaults (.env*, *.pem, *.key, id_rsa*, credentials*, *.pfx)
        are metadata-only: watched and indexed, but NEVER diffed, staged, or sent to AI.
        """
        policy = IgnorePolicy(root_path=self.tmp_dir)

        sensitive_paths = [
            ".env",
            ".env.local",
            ".env.production",
            "secrets/prod.key",
            "certs/tls.pem",
            "ssh/id_rsa",
            "ssh/id_rsa.pub",
            "credentials.json",
            "aws/credentials",
            "certs/cert.pfx",
            "keys/id_ed25519",
        ]

        for p in sensitive_paths:
            self.assertTrue(policy.is_sensitive(p), f"Should be sensitive: {p}")
            # Metadata-only by default:
            self.assertTrue(policy.should_watch(p), f"Sensitive file {p} should be watched (metadata-only)")
            self.assertTrue(policy.should_index(p), f"Sensitive file {p} should be indexed in manifest (metadata-only)")
            self.assertFalse(policy.should_diff(p), f"Sensitive file {p} must NEVER be diffed")
            self.assertFalse(policy.should_stage(p), f"Sensitive file {p} must NEVER be staged")
            self.assertFalse(policy.should_send_to_ai(p), f"Sensitive file {p} must NEVER be sent to AI")

        # Normal files are not sensitive
        normal_paths = ["src/main.py", "docs/README.md", "package.json"]
        for p in normal_paths:
            self.assertFalse(policy.is_sensitive(p))
            self.assertTrue(policy.should_diff(p))
            self.assertTrue(policy.should_stage(p))
            self.assertTrue(policy.should_send_to_ai(p))

    def test_nested_directories_named_patches_baseline(self) -> None:
        """
        Verify that while root-level 'patches/' and 'baseline/' are ignored,
        nested directories like 'src/patches/' and 'lib/baseline/' are NOT ignored.
        """
        policy = IgnorePolicy(root_path=self.tmp_dir)

        # Root-level patches and baseline MUST be ignored
        self.assertTrue(policy.is_builtin_ignored("patches/0001.patch"))
        self.assertTrue(policy.is_builtin_ignored("patches"))
        self.assertFalse(policy.should_watch("patches/0001.patch"))

        self.assertTrue(policy.is_builtin_ignored("baseline/state.json"))
        self.assertTrue(policy.is_builtin_ignored("baseline"))
        self.assertFalse(policy.should_watch("baseline/state.json"))

        # Nested directories named 'patches' or 'baseline' MUST NOT be ignored
        nested_allowed = [
            "src/patches/fix.py",
            "my_module/patches/utils.py",
            "components/baseline/algo.py",
            "lib/baseline/metrics.py",
            "tests/fixtures/patches/data.txt",
        ]
        for p in nested_allowed:
            self.assertFalse(
                policy.is_builtin_ignored(p),
                f"Nested path {p} should NOT be recognized as built-in ignored",
            )
            self.assertTrue(policy.should_watch(p), f"Nested path {p} should be watched")
            self.assertTrue(policy.should_index(p), f"Nested path {p} should be indexed")
            self.assertTrue(policy.should_diff(p), f"Nested path {p} should be diffed")

    def test_pycache_and_bytecode_rejection(self) -> None:
        """
        Verify that __pycache__ directories at any depth and bytecode files
        (*.pyc, *.pyo, *.pyd) are permanently rejected by IgnorePolicy.
        """
        policy = IgnorePolicy(root_path=self.tmp_dir)

        bytecode_paths = [
            "__pycache__",
            "__pycache__/module.cpython-311.pyc",
            "src/__pycache__",
            "src/__pycache__/utils.cpython-312.pyc",
            "tests/sub/__pycache__/test_foo.pyc",
            "app.pyc",
            "lib/worker.pyo",
            "native_ext.pyd",
            "src/bindings/core.pyd",
        ]

        for p in bytecode_paths:
            self.assertTrue(
                policy.is_builtin_ignored(p),
                f"Path {p} must be recognized as built-in ignored",
            )
            self.assertFalse(policy.should_watch(p), f"Path {p} must NOT be watched")
            self.assertFalse(policy.should_index(p), f"Path {p} must NOT be indexed in manifest")
            self.assertFalse(policy.should_diff(p), f"Path {p} must NOT be diffed")
            self.assertFalse(policy.should_stage(p), f"Path {p} must NOT be staged to shadow git")
            self.assertFalse(policy.should_send_to_ai(p), f"Path {p} must NOT be sent to AI")


class TestFileManifest(unittest.TestCase):
    """Test suite for FileManifest fingerprinting, hashing, empty, and binary files."""

    def setUp(self) -> None:
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="spd_manifest_test_"))
        self.manifest = FileManifest(self.tmp_dir)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_empty_files(self) -> None:
        """Verify empty files have 0 size, not binary, and correct empty SHA256."""
        empty_file = self.tmp_dir / "empty.txt"
        empty_file.touch()

        entry = self.manifest.record_file("empty.txt")
        self.assertTrue(entry.exists)
        self.assertEqual(entry.size, 0)
        self.assertFalse(entry.is_binary)
        self.assertEqual(entry.sha256, EMPTY_SHA256)
        self.assertIn("empty.txt", self.manifest)

    def test_binary_files(self) -> None:
        """Verify binary files with null bytes are detected as is_binary=True."""
        bin_file = self.tmp_dir / "image.png"
        bin_content = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        bin_file.write_bytes(bin_content)

        entry = self.manifest.record_file("image.png")
        self.assertTrue(entry.exists)
        self.assertTrue(entry.is_binary)
        self.assertEqual(entry.size, len(bin_content))
        self.assertEqual(entry.sha256, hashlib.sha256(bin_content).hexdigest())

        # Plain text file should be is_binary=False
        txt_file = self.tmp_dir / "code.py"
        txt_content = b"print('Hello, world!')\n"
        txt_file.write_bytes(txt_content)

        entry_txt = self.manifest.record_file("code.py")
        self.assertFalse(entry_txt.is_binary)
        self.assertEqual(entry_txt.sha256, hashlib.sha256(txt_content).hexdigest())

    def test_hash_only_when_size_or_mtime_changes(self) -> None:
        """Verify manifest reuses cached hash without re-reading unless size or mtime changes."""
        test_file = self.tmp_dir / "tracked.txt"
        test_file.write_text("initial content", encoding="utf-8")

        entry1 = self.manifest.record_file("tracked.txt")
        initial_hash = entry1.sha256
        self.assertIsNotNone(initial_hash)

        # Intentionally tamper with cached sha256 in memory to prove reuse
        self.manifest._entries["tracked.txt"].sha256 = "cached_marker"

        # Record file again without changing file on disk
        entry2 = self.manifest.record_file("tracked.txt")
        self.assertEqual(entry2.sha256, "cached_marker", "Should reuse cached sha256 when size/mtime unchanged")

        # Now touch file to update mtime
        time.sleep(0.01)
        st = test_file.stat()
        os.utime(test_file, (st.st_atime, st.st_mtime + 5.0))

        entry3 = self.manifest.record_file("tracked.txt")
        self.assertEqual(entry3.sha256, initial_hash, "Should recompute sha256 when mtime changes")

    def test_scan_all_and_persistence(self) -> None:
        """Verify full directory scan and SQLite persistence."""
        (self.tmp_dir / "src").mkdir()
        (self.tmp_dir / "src" / "a.py").write_text("a = 1", encoding="utf-8")
        (self.tmp_dir / "src" / "b.py").write_text("b = 2", encoding="utf-8")

        count = self.manifest.scan_all()
        self.assertEqual(count, 2)
        self.assertEqual(len(self.manifest), 2)

        # Test save and load to SQLite
        db_path = self.tmp_dir / "session.db"
        conn = sqlite3.connect(str(db_path))
        try:
            self.manifest.save_to_db(conn)

            # Load into fresh manifest
            new_manifest = FileManifest(self.tmp_dir)
            new_manifest.load_from_db(conn)
            self.assertEqual(len(new_manifest), 2)
            self.assertIn("src/a.py", new_manifest)
            self.assertEqual(new_manifest["src/a.py"].sha256, self.manifest["src/a.py"].sha256)
        finally:
            conn.close()


class TestFsEventAndEventCoalescer(unittest.TestCase):
    """Test suite for FsEvent normalization, editor atomic saves, and directory moves."""

    def setUp(self) -> None:
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="spd_watcher_test_"))
        self.manifest = FileManifest(self.tmp_dir)
        self.coalescer = EventCoalescer(manifest=self.manifest, coalesce_window_s=0.02)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_rename(self) -> None:
        """Verify standard file renames and directory move expansion via manifest."""
        # 1. Standard file rename
        self.coalescer.feed("renamed", "new_file.py", old_rel_path="old_file.py")
        flushed = self.coalescer.flush()
        self.assertEqual(len(flushed), 1)
        self.assertEqual(flushed[0].kind, "renamed")
        self.assertEqual(flushed[0].rel_path, "new_file.py")
        self.assertEqual(flushed[0].old_rel_path, "old_file.py")

        # 2. Directory move expanded using manifest knowledge
        # Populate manifest with files inside old_dir
        (self.tmp_dir / "old_dir" / "sub").mkdir(parents=True)
        (self.tmp_dir / "old_dir" / "file1.txt").write_text("1", encoding="utf-8")
        (self.tmp_dir / "old_dir" / "sub" / "file2.txt").write_text("2", encoding="utf-8")
        self.manifest.record_file("old_dir/file1.txt")
        self.manifest.record_file("old_dir/sub/file2.txt")

        # Trigger directory move: old_dir -> new_dir
        dir_events = self.coalescer.feed(
            "renamed",
            "new_dir",
            is_directory=True,
            old_rel_path="old_dir",
        )
        self.assertEqual(len(dir_events), 2)
        dir_events.sort(key=lambda e: e.rel_path)

        self.assertEqual(dir_events[0].kind, "renamed")
        self.assertEqual(dir_events[0].rel_path, "new_dir/file1.txt")
        self.assertEqual(dir_events[0].old_rel_path, "old_dir/file1.txt")

        self.assertEqual(dir_events[1].kind, "renamed")
        self.assertEqual(dir_events[1].rel_path, "new_dir/sub/file2.txt")
        self.assertEqual(dir_events[1].old_rel_path, "old_dir/sub/file2.txt")

    def test_editor_temp_file_saves(self) -> None:
        """
        Verify editor atomic save sequences:
        - Temp file create + write + delete target + rename temp to target
          coalesces to a single 'modified' on the target file.
        - No temp file events leak into public FsEvent stream.
        """
        # Populate target file in manifest
        (self.tmp_dir / "main.py").write_text("original", encoding="utf-8")
        self.manifest.record_file("main.py")

        # Atomic save sequence (e.g. Vim / VS Code / JetBrains)
        t = time.time()
        self.coalescer.feed("created", "main.py.tmp", timestamp=t)
        self.coalescer.feed("modified", "main.py.tmp", timestamp=t + 0.001)
        self.coalescer.feed("deleted", "main.py", timestamp=t + 0.002)
        self.coalescer.feed("renamed", "main.py", timestamp=t + 0.003, old_rel_path="main.py.tmp")

        flushed = self.coalescer.flush()
        self.assertEqual(len(flushed), 1, f"Expected exactly 1 coalesced event, got {flushed}")
        self.assertEqual(flushed[0].kind, "modified")
        self.assertEqual(flushed[0].rel_path, "main.py")

        # Verify temp file created + deleted produces 0 events
        self.coalescer.feed("created", "4913", timestamp=t + 0.01)
        self.coalescer.feed("deleted", "4913", timestamp=t + 0.015)
        flushed_temp = self.coalescer.flush()
        self.assertEqual(len(flushed_temp), 0)

        # Verify delete + immediate create sequence coalesces to 'modified'
        self.coalescer.feed("deleted", "config.py", timestamp=t + 0.02)
        self.coalescer.feed("created", "config.py", timestamp=t + 0.025)
        flushed_recreate = self.coalescer.flush()
        self.assertEqual(len(flushed_recreate), 1)
        self.assertEqual(flushed_recreate[0].kind, "modified")
        self.assertEqual(flushed_recreate[0].rel_path, "config.py")


if __name__ == "__main__":
    unittest.main()
