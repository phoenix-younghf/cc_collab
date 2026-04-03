from __future__ import annotations

from unittest import TestCase

from runtime.prompt_loader import load_prompt


class PromptLoaderTests(TestCase):
    def test_load_prompt_reads_named_prompt(self) -> None:
        prompt = load_prompt("research")
        self.assertIn("research", prompt.lower())
