from __future__ import annotations

from unittest import TestCase

from runtime.claude_runner import IMPLEMENTATION_AGENT_PACK
from runtime.worktree_manager import build_commit_ready_metadata, build_worktree_add_command


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
