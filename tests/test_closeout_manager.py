from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from runtime.closeout_manager import (
    build_file_change_set_metadata,
    build_git_patch_metadata_for_workspace_pair,
    build_patch_ready_metadata,
    choose_failure_terminal_state,
    generate_patch_from_workspace_pair,
    generate_file_change_set,
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

    def test_file_change_set_metadata_records_artifact_type(self) -> None:
        with TemporaryDirectory() as tmp:
            task_dir = Path(tmp) / "task-1"
            task_dir.mkdir()
            metadata = build_file_change_set_metadata(
                task_dir,
                [
                    {
                        "original_path": "src/app.py",
                        "stored_path": "file-change-set/src/app.py",
                        "before_hash": "before-hash",
                        "after_hash": "after-hash",
                        "change_kind": "modified",
                    }
                ],
            )
        self.assertEqual(metadata["artifact_type"], "file-change-set")
        self.assertEqual(metadata["changed_files"], ["src/app.py"])
        manifest = metadata["change_set_manifest"]
        self.assertEqual(manifest["entries"][0]["original_path"], "src/app.py")
        self.assertEqual(manifest["entries"][0]["before_hash"], "before-hash")
        self.assertEqual(manifest["entries"][0]["after_hash"], "after-hash")
        self.assertTrue(manifest["inspect_instructions"])
        self.assertTrue(manifest["copy_back_instructions"])

    def test_degraded_git_aware_write_isolated_closeout_still_emits_git_patch(self) -> None:
        with TemporaryDirectory() as tmp:
            task_dir = Path(tmp) / "task-1"
            task_dir.mkdir()
            metadata = build_git_patch_metadata_for_workspace_pair(
                task_dir=task_dir,
                patch_path=task_dir / "changes.patch",
            )
        self.assertEqual(metadata["artifact_type"], "git-patch")

    def test_generate_file_change_set_represents_renames(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            original_root = root / "before"
            modified_root = root / "after"
            task_dir = root / "task-1"
            original_root.mkdir()
            modified_root.mkdir()
            task_dir.mkdir()
            (original_root / "src").mkdir()
            (modified_root / "src").mkdir()
            (original_root / "src" / "old.py").write_text("print('same')\n", encoding="utf-8")
            (modified_root / "src" / "new.py").write_text("print('same')\n", encoding="utf-8")

            metadata = generate_file_change_set(
                original_root=original_root,
                modified_root=modified_root,
                task_dir=task_dir,
                changed_paths=["src/old.py", "src/new.py"],
            )

        self.assertEqual(metadata["artifact_type"], "file-change-set")
        self.assertEqual(metadata["changed_files"], ["src/new.py"])
        manifest = metadata["change_set_manifest"]
        self.assertEqual(len(manifest["entries"]), 1)
        entry = manifest["entries"][0]
        self.assertEqual(entry["original_path"], "src/old.py")
        self.assertEqual(entry["renamed_path"], "src/new.py")
        self.assertEqual(entry["change_kind"], "renamed")
        self.assertEqual(entry["before_hash"], entry["after_hash"])
        self.assertEqual(entry["stored_path"], "file-change-set/files/src/new.py")

    def test_generate_file_change_set_ignores_unchanged_paths(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            original_root = root / "before"
            modified_root = root / "after"
            task_dir = root / "task-1"
            original_root.mkdir()
            modified_root.mkdir()
            task_dir.mkdir()
            (original_root / "src").mkdir()
            (modified_root / "src").mkdir()
            (original_root / "src" / "same.py").write_text("print('same')\n", encoding="utf-8")
            (modified_root / "src" / "same.py").write_text("print('same')\n", encoding="utf-8")

            with self.assertRaises(RuntimeError):
                generate_file_change_set(
                    original_root=original_root,
                    modified_root=modified_root,
                    task_dir=task_dir,
                    changed_paths=["src/same.py"],
                )

    def test_generate_file_change_set_represents_renamed_and_edited_files(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            original_root = root / "before"
            modified_root = root / "after"
            task_dir = root / "task-1"
            original_root.mkdir()
            modified_root.mkdir()
            task_dir.mkdir()
            (original_root / "src").mkdir()
            (modified_root / "src").mkdir()
            (original_root / "src" / "old.py").write_text("print('before')\n", encoding="utf-8")
            (modified_root / "src" / "new.py").write_text("print('after')\n", encoding="utf-8")

            metadata = generate_file_change_set(
                original_root=original_root,
                modified_root=modified_root,
                task_dir=task_dir,
                changed_paths=["src/old.py", "src/new.py"],
            )

        manifest = metadata["change_set_manifest"]
        self.assertEqual(len(manifest["entries"]), 1)
        entry = manifest["entries"][0]
        self.assertEqual(entry["original_path"], "src/old.py")
        self.assertEqual(entry["renamed_path"], "src/new.py")
        self.assertEqual(entry["change_kind"], "renamed")
        self.assertNotEqual(entry["before_hash"], entry["after_hash"])

    def test_generate_file_change_set_keeps_unrelated_delete_and_add_separate(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            original_root = root / "before"
            modified_root = root / "after"
            task_dir = root / "task-1"
            original_root.mkdir()
            modified_root.mkdir()
            task_dir.mkdir()
            (original_root / "src").mkdir()
            (modified_root / "src").mkdir()
            (original_root / "src" / "a.py").write_text("print('alpha')\n", encoding="utf-8")
            (modified_root / "src" / "b.py").write_text("print('beta')\n", encoding="utf-8")

            metadata = generate_file_change_set(
                original_root=original_root,
                modified_root=modified_root,
                task_dir=task_dir,
                changed_paths=["src/a.py", "src/b.py"],
            )

        entries = metadata["change_set_manifest"]["entries"]
        self.assertEqual(len(entries), 2)
        self.assertEqual({entry["change_kind"] for entry in entries}, {"added", "deleted"})

    def test_generate_patch_from_workspace_pair_rejects_empty_diff(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            original_root = root / "before"
            modified_root = root / "after"
            task_dir = root / "task-1"
            original_root.mkdir()
            modified_root.mkdir()
            task_dir.mkdir()
            (original_root / "src").mkdir()
            (modified_root / "src").mkdir()
            (original_root / "src" / "same.py").write_text("print('same')\n", encoding="utf-8")
            (modified_root / "src" / "same.py").write_text("print('same')\n", encoding="utf-8")

            with self.assertRaises(RuntimeError):
                generate_patch_from_workspace_pair(
                    original_root=original_root,
                    modified_root=modified_root,
                    task_dir=task_dir,
                    paths_to_patch=["src/same.py"],
                )
