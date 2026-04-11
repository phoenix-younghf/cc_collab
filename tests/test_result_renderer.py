from __future__ import annotations

from unittest import TestCase

from runtime.result_renderer import render_result_markdown


class ResultRendererTests(TestCase):
    def test_result_markdown_includes_summary(self) -> None:
        markdown = render_result_markdown({"task_id": "task-1", "summary": "Done"})
        self.assertIn("Done", markdown)

    def test_result_markdown_shows_runtime_mode_and_artifact_type(self) -> None:
        markdown = render_result_markdown(
            {
                "task_id": "task-1",
                "summary": "Done",
                "runtime_mode": "filesystem-only",
                "artifact_type": "file-change-set",
                "capability_summary": {"mode": "filesystem-only"},
                "degradation_notes": ["Git not found; filesystem-only mode active"],
            }
        )
        self.assertIn("filesystem-only", markdown)
        self.assertIn("file-change-set", markdown)
        self.assertIn("Git not found", markdown)
        self.assertIn("capability", markdown.lower())
