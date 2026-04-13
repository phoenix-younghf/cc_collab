from __future__ import annotations

from unittest import TestCase

from runtime.prompt_loader import load_prompt


class PromptLoaderTests(TestCase):
    def test_load_prompt_reads_named_prompt(self) -> None:
        prompt = load_prompt("research")
        self.assertIn("research", prompt.lower())

    def test_research_prompt_requires_json_only_contract_without_smoke_shortcuts(self) -> None:
        prompt = load_prompt("research")

        self.assertIn("Return exactly one JSON object", prompt)
        self.assertIn("Do not wrap the JSON in markdown fences", prompt)
        self.assertNotIn("Do not inspect the workspace", prompt)
        self.assertNotIn("Return the minimal valid structured result immediately", prompt)
