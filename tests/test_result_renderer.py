from __future__ import annotations

from unittest import TestCase

from runtime.result_renderer import render_result_markdown


class ResultRendererTests(TestCase):
    def test_result_markdown_includes_summary(self) -> None:
        markdown = render_result_markdown({"task_id": "task-1", "summary": "Done"})
        self.assertIn("Done", markdown)
