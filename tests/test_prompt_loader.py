from __future__ import annotations

from unittest import TestCase

from runtime.prompt_loader import load_prompt


class PromptLoaderTests(TestCase):
    def test_load_prompt_reads_named_prompt(self) -> None:
        prompt = load_prompt("research")
        self.assertIn("research", prompt.lower())

    def test_research_prompt_requires_minimal_json_without_workspace_inspection(self) -> None:
        prompt = load_prompt("research")

        self.assertIn("Return exactly one JSON object", prompt)
        self.assertIn("Do not wrap the JSON in markdown fences", prompt)
        self.assertIn(
            "Do not inspect the workspace or use tools unless the task explicitly requires it",
            prompt,
        )
