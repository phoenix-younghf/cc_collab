from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from runtime.claude_runner import IMPLEMENTATION_AGENT_PACK
from runtime.worktree_manager import (
    build_commit_ready_metadata,
    build_worktree_add_command,
    choose_isolation_strategy,
    create_filesystem_copy,
)


class WorktreeManagerTests(TestCase):
    def test_build_worktree_add_command_uses_repo_and_branch(self) -> None:
        cmd = build_worktree_add_command("feature-1", "/tmp/repo", "/tmp/wt")
        self.assertEqual(cmd[:4], ["git", "-C", "/tmp/repo", "worktree"])
        self.assertIn("feature-1", cmd)

    def test_implementation_agent_pack_has_required_roles(self) -> None:
        self.assertIn("implementer", IMPLEMENTATION_AGENT_PACK)
        self.assertIn("reviewer", IMPLEMENTATION_AGENT_PACK)
        self.assertIn("tester", IMPLEMENTATION_AGENT_PACK)

    def test_commit_ready_metadata_records_path_and_commit(self) -> None:
        metadata = build_commit_ready_metadata("/tmp/wt", ["abc123"])
        self.assertEqual(metadata["isolated_path"], "/tmp/wt")
        self.assertEqual(metadata["commit_shas"], ["abc123"])


class WorktreeFallbackTests(TestCase):
    def test_write_isolated_falls_back_when_worktree_unavailable(self) -> None:
        isolation = choose_isolation_strategy(
            git_available=True,
            repo=True,
            worktree_usable=False,
        )
        self.assertEqual(isolation, "filesystem-copy")

    def test_write_isolated_prefers_git_worktree_when_available(self) -> None:
        isolation = choose_isolation_strategy(
            git_available=True,
            repo=True,
            worktree_usable=True,
        )
        self.assertEqual(isolation, "git-worktree")

    def test_create_filesystem_copy_excludes_git_metadata(self) -> None:
        with TemporaryDirectory() as tmp:
            repo_root = Path(tmp) / "repo"
            task_dir = Path(tmp) / "task"
            repo_root.mkdir()
            task_dir.mkdir()
            (repo_root / "src.txt").write_text("hello", encoding="utf-8")
            (repo_root / ".git").mkdir()
            (repo_root / ".git" / "config").write_text("ignored", encoding="utf-8")
            copied_root = create_filesystem_copy(repo_root, task_dir)
            self.assertTrue((copied_root / "src.txt").exists())
            self.assertFalse((copied_root / ".git").exists())
