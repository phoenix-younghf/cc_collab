from __future__ import annotations

import io
import subprocess
from unittest import TestCase
from unittest.mock import patch

from runtime.claude_runner import (
    RESEARCH_AGENT_PACK,
    ClaudeTimeoutError,
    build_command,
    resolve_claude_launcher,
    run_claude,
)


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

    @patch("runtime.claude_runner.subprocess.Popen")
    def test_run_claude_returns_stdout_and_log(self, mock_run) -> None:
        process = _FakeProcess(
            stdout_text='{"status":"completed"}',
            stderr_text="",
            poll_sequence=[0],
        )
        mock_run.return_value = process
        stdout, stderr = run_claude(["claude", "-p"])
        self.assertEqual(stdout, '{"status":"completed"}')
        self.assertEqual(stderr, "")

    @patch("runtime.claude_runner.subprocess.Popen")
    def test_run_claude_raises_runtime_error_on_nonzero_exit(self, mock_popen) -> None:
        process = _FakeProcess(
            stdout_text="",
            stderr_text="claude failed badly",
            poll_sequence=[1],
        )
        mock_popen.return_value = process

        with self.assertRaises(RuntimeError) as ctx:
            run_claude(["claude", "-p"])

        self.assertEqual(str(ctx.exception), "claude failed badly")

    @patch("runtime.claude_runner.time.sleep", return_value=None)
    @patch("runtime.claude_runner._terminate_process_tree")
    @patch("runtime.claude_runner.subprocess.Popen")
    def test_run_claude_raises_timeout_error_when_process_hangs(
        self,
        mock_popen,
        mock_terminate,
        _sleep,
    ) -> None:
        process = _FakeProcess(
            stdout_text="partial stdout",
            stderr_text="partial stderr",
            poll_sequence=[None, None],
        )
        mock_popen.return_value = process

        with self.assertRaises(ClaudeTimeoutError) as ctx:
            run_claude(["claude", "-p"], timeout_seconds=0)

        self.assertEqual(ctx.exception.timeout_seconds, 0)
        self.assertEqual(ctx.exception.stdout, "partial stdout")
        self.assertEqual(ctx.exception.stderr, "partial stderr")
        self.assertIn("0", str(ctx.exception))
        mock_terminate.assert_called_once_with(process)

    @patch("runtime.claude_runner.time.sleep", return_value=None)
    @patch("runtime.claude_runner._terminate_process_tree")
    @patch("runtime.claude_runner.subprocess.Popen")
    def test_run_claude_returns_valid_json_emitted_before_timeout(
        self,
        mock_popen,
        mock_terminate,
        _sleep,
    ) -> None:
        process = _FakeProcess(
            stdout_text='{"status":"completed"}',
            stderr_text="",
            poll_sequence=[None, None],
        )
        mock_popen.return_value = process

        stdout, stderr = run_claude(["claude", "-p"], timeout_seconds=0)

        self.assertEqual(stdout, '{"status":"completed"}')
        self.assertEqual(stderr, "")
        mock_terminate.assert_called_once_with(process)

    @patch("runtime.claude_runner.os.name", "nt")
    @patch("runtime.claude_runner.subprocess.Popen")
    def test_run_claude_wraps_windows_batch_launchers_via_cmd_exe(
        self,
        mock_popen,
    ) -> None:
        process = _FakeProcess(
            stdout_text='{"status":"completed"}',
            stderr_text="",
            poll_sequence=[0],
        )
        mock_popen.return_value = process

        run_claude([r"C:\nvm4w\nodejs\claude.CMD", "-p"])

        self.assertEqual(
            mock_popen.call_args.args[0][:4],
            ["cmd.exe", "/d", "/s", "/c"],
        )
        self.assertIn(r'C:\nvm4w\nodejs\claude.CMD -p', mock_popen.call_args.args[0][4])


class _FakeProcess:
    def __init__(self, *, stdout_text: str, stderr_text: str, poll_sequence: list[int | None]) -> None:
        self.stdout = io.StringIO(stdout_text)
        self.stderr = io.StringIO(stderr_text)
        self._poll_sequence = list(poll_sequence)
        self.returncode = None
        self.pid = 1234

    def poll(self) -> int | None:
        if self._poll_sequence:
            result = self._poll_sequence.pop(0)
            if result is not None:
                self.returncode = result
            return result
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is None:
            self.returncode = 1
        return self.returncode
