"""
tests/test_diff_calc_git_apply.py
==================================
Comprehensive test suite verifying that DiffCalculator and format_unified_diff
generate strictly valid unified diffs accepted by `git apply --check` across
all required edge cases:
- Normal text modification
- New file creation (with and without trailing newline)
- Deleted file (with and without trailing newline)
- Empty file creation and deletion
- Files ending with newline vs lacking newline (\\ No newline at end of file)
- Unicode content (emojis, CJK, multibyte sequences)
- Binary file detection and handling
- CRLF and LF preservation
- Large file diff generation (10,000+ lines)
- Malformed / partial files (mixed endings, replacement chars, BOM, single char)
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.diff_calc import DiffCalculator, format_unified_diff, is_binary_file, read_file_text, write_file_text


class TestDiffCalculatorGitApply(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name).resolve()
        self.repo_dir = self.root / "repo"
        self.repo_dir.mkdir(parents=True, exist_ok=True)
        self.baseline_dir = self.root / "baseline"
        self.baseline_dir.mkdir(parents=True, exist_ok=True)

        # Initialize clean git repo
        subprocess.run(["git", "init"], cwd=self.repo_dir, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=self.repo_dir, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.repo_dir, capture_output=True, check=True)
        subprocess.run(["git", "config", "core.autocrlf", "false"], cwd=self.repo_dir, capture_output=True, check=True)

        self.calc = DiffCalculator(baseline_dir=self.baseline_dir, target_dir=self.repo_dir)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def _commit_file(self, rel_path: str, content: str) -> None:
        """Write content to repo_dir and commit it to git."""
        file_path = self.repo_dir / Path(rel_path)
        write_file_text(file_path, content)
        subprocess.run(["git", "add", rel_path], cwd=self.repo_dir, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", f"commit {rel_path}"], cwd=self.repo_dir, capture_output=True, check=True)

    def _assert_git_apply_roundtrip(
        self,
        rel_path: str,
        patch_str: str,
        expected_content: str | None,
    ) -> None:
        """Verify git apply --check succeeds, git apply succeeds, and file matches expected."""
        self.assertTrue(bool(patch_str), "Expected a non-empty patch string for a valid modification")

        # 1. git apply --check must succeed
        res_check = subprocess.run(
            ["git", "apply", "--check", "-"],
            input=patch_str.encode("utf-8"),
            cwd=self.repo_dir,
            capture_output=True,
        )
        self.assertEqual(
            res_check.returncode,
            0,
            f"git apply --check failed with code {res_check.returncode}:\n"
            f"STDERR: {res_check.stderr.decode('utf-8', errors='replace')}\n"
            f"PATCH:\n{patch_str}",
        )

        # 2. git apply must apply cleanly
        res_apply = subprocess.run(
            ["git", "apply", "-"],
            input=patch_str.encode("utf-8"),
            cwd=self.repo_dir,
            capture_output=True,
        )
        self.assertEqual(
            res_apply.returncode,
            0,
            f"git apply failed with code {res_apply.returncode}:\n"
            f"STDERR: {res_apply.stderr.decode('utf-8', errors='replace')}",
        )

        # 3. Verify resulting content on disk
        target_file = self.repo_dir / Path(rel_path)
        if expected_content is None:
            self.assertFalse(target_file.exists(), f"File {rel_path} was expected to be deleted")
        else:
            self.assertTrue(target_file.exists(), f"File {rel_path} was expected to exist")
            actual = read_file_text(target_file)
            self.assertEqual(actual, expected_content, f"Content mismatch after applying patch to {rel_path}")

    # -----------------------------------------------------------------------
    # 1. Normal text modification
    # -----------------------------------------------------------------------
    def test_normal_text_modification(self) -> None:
        old_content = "line 1\nline 2 to edit\nline 3\n"
        new_content = "line 1\nline 2 EDITED\nline 3\n"
        self._commit_file("main.py", old_content)
        self.calc.capture_baseline()

        # Edit file on disk and compute diff
        write_file_text(self.repo_dir / "main.py", new_content)
        patch = self.calc.compute_diff("main.py")

        # Restore to committed state before testing patch application
        subprocess.run(["git", "checkout", "main.py"], cwd=self.repo_dir, capture_output=True, check=True)
        self._assert_git_apply_roundtrip("main.py", patch, new_content)

    # -----------------------------------------------------------------------
    # 2. New file creation
    # -----------------------------------------------------------------------
    def test_new_file_creation(self) -> None:
        self.calc.capture_baseline()
        new_content = "def add(a, b):\n    return a + b\n"
        write_file_text(self.repo_dir / "new_module.py", new_content)

        patch = self.calc.compute_diff("new_module.py")
        (self.repo_dir / "new_module.py").unlink()

        self._assert_git_apply_roundtrip("new_module.py", patch, new_content)

    # -----------------------------------------------------------------------
    # 3. Deleted file
    # -----------------------------------------------------------------------
    def test_deleted_file(self) -> None:
        old_content = "obsolete code\nline 2\n"
        self._commit_file("old_module.py", old_content)
        self.calc.capture_baseline()

        (self.repo_dir / "old_module.py").unlink()
        patch = self.calc.compute_diff("old_module.py")

        # Restore before testing patch application
        subprocess.run(["git", "checkout", "old_module.py"], cwd=self.repo_dir, capture_output=True, check=True)
        self._assert_git_apply_roundtrip("old_module.py", patch, None)

    # -----------------------------------------------------------------------
    # 4. Empty file creation & deletion
    # -----------------------------------------------------------------------
    def test_empty_file_creation(self) -> None:
        self.calc.capture_baseline()
        write_file_text(self.repo_dir / "empty.txt", "")

        patch = self.calc.compute_diff("empty.txt")
        (self.repo_dir / "empty.txt").unlink()

        self.assertIn("new file mode 100644", patch)
        self._assert_git_apply_roundtrip("empty.txt", patch, "")

    def test_empty_file_deletion(self) -> None:
        self._commit_file("empty_del.txt", "")
        self.calc.capture_baseline()

        (self.repo_dir / "empty_del.txt").unlink()
        patch = self.calc.compute_diff("empty_del.txt")

        subprocess.run(["git", "checkout", "empty_del.txt"], cwd=self.repo_dir, capture_output=True, check=True)
        self.assertIn("deleted file mode 100644", patch)
        self._assert_git_apply_roundtrip("empty_del.txt", patch, None)

    # -----------------------------------------------------------------------
    # 5. File ending with newline
    # -----------------------------------------------------------------------
    def test_file_ending_with_newline(self) -> None:
        old_content = "alpha\nbeta\ngamma\n"
        new_content = "alpha\nBETA\ngamma\n"
        self._commit_file("nl.txt", old_content)
        self.calc.capture_baseline()

        write_file_text(self.repo_dir / "nl.txt", new_content)
        patch = self.calc.compute_diff("nl.txt")

        subprocess.run(["git", "checkout", "nl.txt"], cwd=self.repo_dir, capture_output=True, check=True)
        self.assertNotIn("\\ No newline at end of file", patch)
        self._assert_git_apply_roundtrip("nl.txt", patch, new_content)

    # -----------------------------------------------------------------------
    # 6. File without newline (\\ No newline at end of file)
    # -----------------------------------------------------------------------
    def test_file_without_newline(self) -> None:
        old_content = "alpha\nno newline here"
        new_content = "alpha\nno newline updated"
        self._commit_file("nonl.txt", old_content)
        self.calc.capture_baseline()

        write_file_text(self.repo_dir / "nonl.txt", new_content)
        patch = self.calc.compute_diff("nonl.txt")

        subprocess.run(["git", "checkout", "nonl.txt"], cwd=self.repo_dir, capture_output=True, check=True)
        self.assertIn("\\ No newline at end of file", patch)
        self._assert_git_apply_roundtrip("nonl.txt", patch, new_content)

    def test_newline_transition_added_and_removed(self) -> None:
        # 6a. Gaining newline
        old_a = "hello"
        new_a = "hello\n"
        self._commit_file("trans_a.txt", old_a)
        self.calc.capture_baseline()
        write_file_text(self.repo_dir / "trans_a.txt", new_a)
        patch_a = self.calc.compute_diff("trans_a.txt")
        subprocess.run(["git", "checkout", "trans_a.txt"], cwd=self.repo_dir, capture_output=True, check=True)
        self._assert_git_apply_roundtrip("trans_a.txt", patch_a, new_a)

        # 6b. Losing newline
        old_b = "world\n"
        new_b = "world"
        self._commit_file("trans_b.txt", old_b)
        self.calc.capture_baseline()
        write_file_text(self.repo_dir / "trans_b.txt", new_b)
        patch_b = self.calc.compute_diff("trans_b.txt")
        subprocess.run(["git", "checkout", "trans_b.txt"], cwd=self.repo_dir, capture_output=True, check=True)
        self._assert_git_apply_roundtrip("trans_b.txt", patch_b, new_b)

    # -----------------------------------------------------------------------
    # 7. Unicode
    # -----------------------------------------------------------------------
    def test_unicode_content(self) -> None:
        old_content = "# Café & résumé\nprint('こんにちは世界')\nflag = '🚀'\n"
        new_content = "# Café & résumé updated\nprint('こんにちは世界 🌸')\nflag = '🌟'\n"
        self._commit_file("unicode.py", old_content)
        self.calc.capture_baseline()

        write_file_text(self.repo_dir / "unicode.py", new_content)
        patch = self.calc.compute_diff("unicode.py")

        subprocess.run(["git", "checkout", "unicode.py"], cwd=self.repo_dir, capture_output=True, check=True)
        self._assert_git_apply_roundtrip("unicode.py", patch, new_content)

    # -----------------------------------------------------------------------
    # 8. Binary files
    # -----------------------------------------------------------------------
    def test_binary_file_handling(self) -> None:
        bin_path = self.repo_dir / "data.bin"
        bin_path.write_bytes(b"\x00\x01\x02\x03\xff\xfe")
        self.assertTrue(is_binary_file(bin_path))

        # Baseline capture must ignore binary files
        captured = self.calc.capture_baseline()
        self.assertNotIn("data.bin", captured)

        # compute_diff on binary must return empty string (no corrupt textual patch)
        patch = self.calc.compute_diff("data.bin")
        self.assertEqual(patch, "")

        # String diff with null bytes must return empty string
        diff_str = format_unified_diff("binary.dat", "abc\x00def", "abc\x00xyz")
        self.assertEqual(diff_str, "")

    # -----------------------------------------------------------------------
    # 9. CRLF and LF preservation
    # -----------------------------------------------------------------------
    def test_crlf_line_endings(self) -> None:
        old_content = "line1\r\nline2\r\nline3\r\n"
        new_content = "line1\r\nline2 MODIFIED\r\nline3\r\n"
        self._commit_file("crlf.txt", old_content)
        self.calc.capture_baseline()

        write_file_text(self.repo_dir / "crlf.txt", new_content)
        patch = self.calc.compute_diff("crlf.txt")

        subprocess.run(["git", "checkout", "crlf.txt"], cwd=self.repo_dir, capture_output=True, check=True)
        self._assert_git_apply_roundtrip("crlf.txt", patch, new_content)

    def test_lf_line_endings(self) -> None:
        old_content = "line1\nline2\nline3\n"
        new_content = "line1\nline2 MODIFIED\nline3\n"
        self._commit_file("lf.txt", old_content)
        self.calc.capture_baseline()

        write_file_text(self.repo_dir / "lf.txt", new_content)
        patch = self.calc.compute_diff("lf.txt")

        subprocess.run(["git", "checkout", "lf.txt"], cwd=self.repo_dir, capture_output=True, check=True)
        self._assert_git_apply_roundtrip("lf.txt", patch, new_content)

    def test_crlf_to_lf_conversion(self) -> None:
        old_content = "line1\r\nline2\r\n"
        new_content = "line1\nline2\n"
        self._commit_file("conv.txt", old_content)
        self.calc.capture_baseline()

        write_file_text(self.repo_dir / "conv.txt", new_content)
        patch = self.calc.compute_diff("conv.txt")

        subprocess.run(["git", "checkout", "conv.txt"], cwd=self.repo_dir, capture_output=True, check=True)
        self._assert_git_apply_roundtrip("conv.txt", patch, new_content)

    # -----------------------------------------------------------------------
    # 10. Large files
    # -----------------------------------------------------------------------
    def test_large_files(self) -> None:
        old_lines = [f"row_{i:05d} initial content\n" for i in range(10000)]
        new_lines = [f"row_{i:05d} {'MODIFIED' if i % 250 == 0 else 'initial content'}\n" for i in range(10000)]
        old_content = "".join(old_lines)
        new_content = "".join(new_lines)

        self._commit_file("large.txt", old_content)
        self.calc.capture_baseline()

        write_file_text(self.repo_dir / "large.txt", new_content)
        patch = self.calc.compute_diff("large.txt")

        subprocess.run(["git", "checkout", "large.txt"], cwd=self.repo_dir, capture_output=True, check=True)
        self._assert_git_apply_roundtrip("large.txt", patch, new_content)

    # -----------------------------------------------------------------------
    # 11. Malformed and partial files
    # -----------------------------------------------------------------------
    def test_malformed_mixed_line_endings(self) -> None:
        old_content = "line1\r\nline2\nline3\r\nline4"
        new_content = "line1\r\nline2 CHANGED\nline3\r\nline4 CHANGED"
        self._commit_file("mixed.txt", old_content)
        self.calc.capture_baseline()

        write_file_text(self.repo_dir / "mixed.txt", new_content)
        patch = self.calc.compute_diff("mixed.txt")

        subprocess.run(["git", "checkout", "mixed.txt"], cwd=self.repo_dir, capture_output=True, check=True)
        self._assert_git_apply_roundtrip("mixed.txt", patch, new_content)

    def test_utf8_bom_file(self) -> None:
        old_content = "\ufeffconfig = 'v1'\nport = 8080\n"
        new_content = "\ufeffconfig = 'v2'\nport = 9090\n"
        self._commit_file("bom.ini", old_content)
        self.calc.capture_baseline()

        write_file_text(self.repo_dir / "bom.ini", new_content)
        patch = self.calc.compute_diff("bom.ini")

        subprocess.run(["git", "checkout", "bom.ini"], cwd=self.repo_dir, capture_output=True, check=True)
        self._assert_git_apply_roundtrip("bom.ini", patch, new_content)

    def test_replacement_characters(self) -> None:
        old_content = "data:\ufffd\ufffd_sample\n"
        new_content = "data:\ufffd\ufffd_sample_MOD\n"
        self._commit_file("replace.txt", old_content)
        self.calc.capture_baseline()

        write_file_text(self.repo_dir / "replace.txt", new_content)
        patch = self.calc.compute_diff("replace.txt")

        subprocess.run(["git", "checkout", "replace.txt"], cwd=self.repo_dir, capture_output=True, check=True)
        self._assert_git_apply_roundtrip("replace.txt", patch, new_content)

    def test_single_character_files(self) -> None:
        old_content = "x"
        new_content = "y"
        self._commit_file("single.txt", old_content)
        self.calc.capture_baseline()

        write_file_text(self.repo_dir / "single.txt", new_content)
        patch = self.calc.compute_diff("single.txt")

        subprocess.run(["git", "checkout", "single.txt"], cwd=self.repo_dir, capture_output=True, check=True)
        self._assert_git_apply_roundtrip("single.txt", patch, new_content)


if __name__ == "__main__":
    unittest.main()
