from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from runtime.workspace_guard import capture_baseline, detect_unsafe_dirty_state


class WorkspaceGuardTests(TestCase):
    def test_capture_baseline_records_git_and_hashes(self) -> None:
        with TemporaryDirectory() as tmp:
            project = Path(tmp)
            target = project / "src.txt"
            target.write_text("hello", encoding="utf-8")
            baseline = capture_baseline(project, ["src.txt"], git_head="abc123", git_status="")
            self.assertEqual(baseline.git_head, "abc123")
            self.assertEqual(baseline.files[0].relative_path, "src.txt")
            self.assertTrue(baseline.files[0].sha256)

    def test_declared_dirty_file_is_unsafe(self) -> None:
        with TemporaryDirectory() as tmp:
            project = Path(tmp)
            target = project / "src.txt"
            target.write_text("hello", encoding="utf-8")
            baseline = capture_baseline(project, ["src.txt"], git_head="abc123", git_status=" M src.txt")
            self.assertTrue(detect_unsafe_dirty_state(baseline))

    def test_missing_git_capture_is_fatal(self) -> None:
        with self.assertRaises(RuntimeError):
            capture_baseline(Path("/tmp/project"), ["src.txt"], git_head=None, git_status=None)  # type: ignore[arg-type]
