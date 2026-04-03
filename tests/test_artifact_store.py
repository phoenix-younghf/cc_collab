from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from runtime.artifact_store import create_task_dir, write_json_artifact, write_log_artifact


class ArtifactStoreTests(TestCase):
    def test_task_dir_contains_json_and_log_artifacts(self) -> None:
        with TemporaryDirectory() as tmp:
            task_dir = create_task_dir(Path(tmp), "task-123")
            write_json_artifact(task_dir, "request.json", {"task_id": "task-123"})
            write_log_artifact(task_dir, "run.log", "hello\n")
            self.assertTrue((task_dir / "request.json").exists())
            self.assertTrue((task_dir / "run.log").exists())

    def test_invalid_task_id_is_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                create_task_dir(Path(tmp), "../oops")
