from pathlib import Path
from unittest import TestCase


class InstallDocsTests(TestCase):
    def test_readme_starts_with_quick_install(self) -> None:
        readme = Path("README.md").read_text(encoding="utf-8")
        self.assertTrue(
            readme.lstrip().startswith("## Quick Install"),
            "README.md must begin with the Quick Install section.",
        )
        self.assertIn("macOS / Linux", readme)
        self.assertIn("Windows", readme)
        self.assertIn("./install/install-all.sh", readme)
        self.assertIn("ccollab doctor", readme)

    def test_agents_doc_mentions_install_and_doctor(self) -> None:
        agents = Path("AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("install/install-all.sh", agents)
        self.assertIn("ccollab doctor", agents)

    def test_skill_docs_include_bootstrap_and_review_task_type(self) -> None:
        skill = Path("skill/delegate-to-claude-code/SKILL.md").read_text(encoding="utf-8")
        routing = Path(
            "skill/delegate-to-claude-code/templates/task-routing.md"
        ).read_text(encoding="utf-8")
        self.assertIn("ccollab doctor", skill)
        self.assertIn("command -v ccollab", skill)
        self.assertIn("`review`", routing)
        self.assertNotIn("`code-review`", routing)
