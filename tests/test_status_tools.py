from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from runtime.cli import main


class StatusToolTests(TestCase):
    def test_status_reads_terminal_state_from_result_json(self) -> None:
        with TemporaryDirectory() as tmp:
            task_dir = Path(tmp) / "task-1"
            task_dir.mkdir(parents=True)
            (task_dir / "result.json").write_text('{"terminal_state": "archived"}', encoding="utf-8")
            exit_code = main(["status", "--task", "task-1", "--task-root", tmp])
            self.assertEqual(exit_code, 0)

    def test_cleanup_refuses_inspection_required(self) -> None:
        with TemporaryDirectory() as tmp:
            task_dir = Path(tmp) / "task-2"
            task_dir.mkdir(parents=True)
            (task_dir / "result.json").write_text(
                '{"terminal_state": "inspection-required"}',
                encoding="utf-8",
            )
            exit_code = main(["cleanup", "--task", "task-2", "--task-root", tmp])
            self.assertNotEqual(exit_code, 0)

    def test_cleanup_rejects_path_traversal_task_id(self) -> None:
        with TemporaryDirectory() as tmp:
            exit_code = main(["cleanup", "--task", "../oops", "--task-root", tmp])
            self.assertNotEqual(exit_code, 0)
