from __future__ import annotations

from unittest import TestCase
from unittest.mock import patch

from runtime.claude_runner import RESEARCH_AGENT_PACK, build_command, resolve_claude_launcher, run_claude


class ClaudeRunnerTests(TestCase):
    @patch("runtime.claude_runner.shutil.which")
    def test_resolve_claude_launcher_falls_back_to_claude_cmd(self, mock_which) -> None:
        launcher = r"C:\Users\zengs\AppData\Local\Programs\claude\claude.cmd"
        mock_which.side_effect = lambda name: launcher if name == "claude.cmd" else None

        resolved = resolve_claude_launcher()

        self.assertEqual(resolved, launcher)

    def test_build_command_includes_schema_and_add_dir(self) -> None:
        cmd = build_command(
            workdir="/tmp/project",
            prompt="Do work",
            schema_json='{"type":"object"}',
            runtime_contract="contract",
            agent_pack_json='{"researcher": {}}',
            model="opus",
        )
        self.assertIn("--json-schema", cmd)
        self.assertIn("--agents", cmd)
        self.assertIn("--model", cmd)
        self.assertIn("opus", cmd)
        self.assertIn("/tmp/project", cmd)

    @patch(
        "runtime.claude_runner.resolve_claude_launcher",
        return_value=r"C:\Users\zengs\AppData\Local\Programs\claude\claude.cmd",
    )
    def test_build_command_uses_resolved_windows_launcher(self, _resolve_launcher) -> None:
        cmd = build_command(
            workdir="/tmp/project",
            prompt="Do work",
            schema_json='{"type":"object"}',
            runtime_contract="contract",
            agent_pack_json=None,
        )
        self.assertEqual(
            cmd[0],
            r"C:\Users\zengs\AppData\Local\Programs\claude\claude.cmd",
        )

    def test_research_agent_pack_contains_required_roles(self) -> None:
        self.assertIn("researcher", RESEARCH_AGENT_PACK)
        self.assertIn("synthesizer", RESEARCH_AGENT_PACK)
        self.assertIn("critic", RESEARCH_AGENT_PACK)

    @patch("runtime.claude_runner.subprocess.run")
    def test_run_claude_returns_stdout_and_log(self, mock_run) -> None:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = '{"status":"completed"}'
        mock_run.return_value.stderr = ""
        stdout, stderr = run_claude(["claude", "-p"])
        self.assertEqual(stdout, '{"status":"completed"}')
        self.assertEqual(stderr, "")
