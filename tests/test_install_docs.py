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
        self.assertIn("./install/install-all.ps1", readme)
        self.assertIn("ccollab doctor", readme)
        self.assertNotIn("use WSL for now", readme)

    def test_agents_doc_mentions_install_and_doctor(self) -> None:
        agents = Path("AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("install/install-all.sh", agents)
        self.assertIn("install/install-all.ps1", agents)
        self.assertIn("ccollab doctor", agents)
        self.assertNotIn("use WSL for now", agents)

    def test_skill_docs_include_bootstrap_and_review_task_type(self) -> None:
        skill = Path("skill/delegate-to-claude-code/SKILL.md").read_text(encoding="utf-8")
        routing = Path(
            "skill/delegate-to-claude-code/templates/task-routing.md"
        ).read_text(encoding="utf-8")
        self.assertIn("ccollab doctor", skill)
        self.assertIn("command -v ccollab", skill)
        self.assertIn("Get-Command ccollab", skill)
        self.assertIn("py -3 -m runtime.cli doctor", skill)
        self.assertIn("`review`", routing)
        self.assertNotIn("`code-review`", routing)

    def test_windows_install_artifacts_exist(self) -> None:
        self.assertTrue(Path("install/install-all.ps1").exists())
        self.assertTrue(Path("install/install-bin.ps1").exists())
        self.assertTrue(Path("install/install-skill.ps1").exists())
        self.assertTrue(Path("bin/ccollab.cmd").exists())

    def test_readme_mentions_install_root_and_git_optional_mode(self) -> None:
        readme = Path("README.md").read_text(encoding="utf-8")
        self.assertIn("install root", readme.lower())
        self.assertIn("Git is optional", readme)
        self.assertIn("filesystem-only", readme)
        self.assertIn("template", readme.lower())

    def test_agents_doc_mentions_native_smoke_templates(self) -> None:
        agents = Path("AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("filesystem-only-smoke-task.json", agents)
        self.assertIn("git-aware-smoke-task.json", agents)
        self.assertIn("template", agents.lower())

    def test_skill_docs_explain_filesystem_only_runtime(self) -> None:
        skill = Path("skill/delegate-to-claude-code/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("filesystem-only", skill)
        self.assertIn("Git-aware", skill)

    def test_readme_lists_explicit_windows_smoke_commands(self) -> None:
        readme = Path("README.md").read_text(encoding="utf-8")
        self.assertIn("Copy-Item .\\examples\\filesystem-only-smoke-task.json", readme)
        self.assertIn("git init $env:TEMP\\ccollab-git-smoke", readme)
        self.assertIn("cmd /c ccollab run", readme)

    def test_readme_uses_dedicated_filesystem_smoke_workdirs(self) -> None:
        readme = Path("README.md").read_text(encoding="utf-8")
        self.assertIn("/tmp/ccollab-filesystem-workdir", readme)
        self.assertIn("$env:TEMP\\ccollab-filesystem-workdir", readme)

    def test_readme_lists_manual_validation_prerequisites(self) -> None:
        readme = Path("README.md").read_text(encoding="utf-8")
        self.assertIn("python3 -m unittest tests.test_cli -v", readme)
        self.assertIn("cmd /c ccollab doctor", readme)

    def test_readme_uses_bom_safe_windows_json_rewrite(self) -> None:
        readme = Path("README.md").read_text(encoding="utf-8")
        self.assertIn("UTF8Encoding", readme)
        self.assertNotIn("Set-Content $dst -Encoding utf8", readme)

    def test_runtime_docs_describe_degraded_git_aware_mode(self) -> None:
        readme = Path("README.md").read_text(encoding="utf-8")
        skill = Path("skill/delegate-to-claude-code/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("remains `git-aware`", readme)
        self.assertIn("filesystem-copy isolation", readme)
        self.assertIn("remains `git-aware`", skill.lower())

    def test_readme_mentions_version_and_update_commands(self) -> None:
        readme = Path("README.md").read_text(encoding="utf-8")
        self.assertIn("ccollab version", readme)
        self.assertIn("ccollab update", readme)

    def test_readme_mentions_draft_release_windows_gate(self) -> None:
        readme = Path("README.md").read_text(encoding="utf-8")
        self.assertIn("draft", readme.lower())
        self.assertIn("Windows", readme)
        self.assertIn("ccollab-update-checklist", readme)

    def test_agents_doc_points_to_release_checklist(self) -> None:
        agents = Path("AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("ccollab-update-checklist", agents)
        self.assertIn("ccollab version", agents)
        self.assertIn("ccollab update", agents)

    def test_release_checklist_includes_required_windows_validations(self) -> None:
        checklist = Path("docs/release/ccollab-update-checklist.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("native Windows update from outside the install root", checklist)
        self.assertIn("native Windows update from inside the install root", checklist)
        self.assertIn("forced verification failure", checklist)
        self.assertIn("rollback", checklist.lower())
        self.assertIn("stale-lock", checklist.lower())
        self.assertIn("path", checklist.lower())
        self.assertIn("spaces", checklist.lower())
        self.assertIn("PowerShell", checklist)
        self.assertIn("CMD", checklist)
        self.assertIn("ccollab version", checklist)
        self.assertIn("ccollab update", checklist)
        self.assertIn("& $HOME\\.local\\bin\\ccollab.cmd update", checklist)
