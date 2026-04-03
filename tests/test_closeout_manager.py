from __future__ import annotations

from unittest import TestCase

from runtime.closeout_manager import (
    build_patch_ready_metadata,
    choose_failure_terminal_state,
    validate_terminal_state,
)


class CloseoutManagerTests(TestCase):
    def test_failure_prefers_patch_ready_when_allowed(self) -> None:
        state = choose_failure_terminal_state(["patch-ready", "inspection-required"])
        self.assertEqual(state, "patch-ready")

    def test_terminal_state_must_match_allowed_value(self) -> None:
        with self.assertRaises(ValueError):
            validate_terminal_state("archived", "patch-ready")

    def test_patch_ready_metadata_uses_changes_patch(self) -> None:
        metadata = build_patch_ready_metadata("/tmp/task-1")
        self.assertEqual(metadata["patch_path"], "/tmp/task-1/changes.patch")
        self.assertIn("git apply", metadata["apply_command"])
