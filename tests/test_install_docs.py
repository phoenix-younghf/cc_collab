from pathlib import Path
from unittest import TestCase


class InstallDocsTests(TestCase):
    def test_readme_starts_with_quick_install(self) -> None:
        readme = Path("README.md").read_text(encoding="utf-8")
        self.assertTrue(
            readme.lstrip().startswith("## Quick Install"),
            "README.md must begin with the Quick Install section.",
        )
        self.assertIn("./install/install-all.sh", readme)
        self.assertIn("ccollab doctor", readme)

    def test_agents_doc_mentions_install_and_doctor(self) -> None:
        agents = Path("AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("install/install-all.sh", agents)
        self.assertIn("ccollab doctor", agents)

