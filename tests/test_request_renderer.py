from __future__ import annotations

from unittest import TestCase

from runtime.request_renderer import render_request_markdown


class RequestRendererTests(TestCase):
    def test_request_markdown_includes_objective(self) -> None:
        markdown = render_request_markdown({"task_id": "task-1", "objective": "Review plan"})
        self.assertIn("Review plan", markdown)
