from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from runtime.workspace_guard import (
    capture_baseline,
    copy_workspace_tree,
    detect_post_run_changes_with_snapshots,
    detect_unsafe_dirty_state,
    snapshot_paths,
    snapshot_workspace_tree,
)


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

    def test_capture_baseline_without_git_status_uses_bounded_snapshot(self) -> None:
        with TemporaryDirectory() as tmp:
            project = Path(tmp)
            task_root = project / "tasks"
            source = project / "src.txt"
            source.write_text("hello", encoding="utf-8")
            (project / "__pycache__").mkdir()
            (project / "__pycache__" / "ignored.pyc").write_text("cached", encoding="utf-8")
            (project / ".git").mkdir()
            (project / ".git" / "config").write_text("ignored", encoding="utf-8")
            (task_root / "task-1").mkdir(parents=True)
            (task_root / "task-1" / "result.json").write_text("ignored", encoding="utf-8")
            baseline = capture_baseline(
                project,
                ["src.txt"],
                git_head=None,
                git_status=None,
                task_root=task_root,
            )
            self.assertIsNone(baseline.git_status)
            self.assertIn("src.txt", baseline.status_snapshot)
            self.assertNotIn(".git/config", baseline.status_snapshot)
            self.assertNotIn("__pycache__/ignored.pyc", baseline.status_snapshot)
            self.assertNotIn("tasks/task-1/result.json", baseline.status_snapshot)

    def test_snapshot_detection_catches_dirty_file_rewrite(self) -> None:
        with TemporaryDirectory() as tmp:
            project = Path(tmp)
            target = project / "src.txt"
            target.write_text("before", encoding="utf-8")
            pre_snapshot = snapshot_paths(project, ["src.txt"])
            target.write_text("after", encoding="utf-8")
            changed = detect_post_run_changes_with_snapshots(
                project,
                " M src.txt",
                pre_snapshot,
                " M src.txt",
            )
            self.assertEqual(changed, ["src.txt"])

    def test_snapshot_detection_supports_non_git_workspaces(self) -> None:
        with TemporaryDirectory() as tmp:
            project = Path(tmp)
            target = project / "src.txt"
            target.write_text("before", encoding="utf-8")
            pre_snapshot = snapshot_workspace_tree(project)
            target.write_text("after", encoding="utf-8")
            changed = detect_post_run_changes_with_snapshots(
                project,
                None,
                pre_snapshot,
                None,
            )
            self.assertEqual(changed, ["src.txt"])

    def test_snapshot_workspace_tree_excludes_git_and_task_root(self) -> None:
        with TemporaryDirectory() as tmp:
            project = Path(tmp)
            task_root = project / "tasks"
            (project / ".git").mkdir()
            (project / ".git" / "config").write_text("ignored", encoding="utf-8")
            (project / "src.txt").write_text("hello", encoding="utf-8")
            (task_root / "task-1").mkdir(parents=True)
            (task_root / "task-1" / "result.json").write_text("ignored", encoding="utf-8")
            snapshot = snapshot_workspace_tree(project, task_root=task_root)
            self.assertIn("src.txt", snapshot)
            self.assertNotIn(".git/config", snapshot)
            self.assertNotIn("tasks/task-1/result.json", snapshot)

    def test_copy_workspace_tree_skips_recursion_hazards(self) -> None:
        with TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            destination = Path(tmp) / "copy"
            task_root = project / "tasks"
            project.mkdir()
            (project / "src.txt").write_text("hello", encoding="utf-8")
            (project / ".git").mkdir()
            (project / ".git" / "config").write_text("ignored", encoding="utf-8")
            (project / "__pycache__").mkdir()
            (project / "__pycache__" / "ignored.pyc").write_text("ignored", encoding="utf-8")
            (task_root / "task-1").mkdir(parents=True)
            (task_root / "task-1" / "result.json").write_text("ignored", encoding="utf-8")
            copied_manifest = copy_workspace_tree(project, destination, task_root=task_root)
            self.assertTrue((destination / "src.txt").exists())
            self.assertFalse((destination / ".git").exists())
            self.assertFalse((destination / "__pycache__").exists())
            self.assertFalse((destination / "tasks").exists())
            self.assertIn("src.txt", copied_manifest)

    def test_directory_path_is_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "dir").mkdir()
            with self.assertRaises(RuntimeError):
                capture_baseline(project, ["dir"], git_head="abc123", git_status="")
