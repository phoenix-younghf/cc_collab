from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from runtime.cli import main

ROOT = Path(__file__).resolve().parents[1]


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "runtime.cli", *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )


class CliSmokeTests(TestCase):
    def test_help_lists_core_commands(self) -> None:
        result = run_cli("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("run", result.stdout)
        self.assertIn("status", result.stdout)
        self.assertIn("cleanup", result.stdout)
        self.assertIn("doctor", result.stdout)

    def test_unknown_command_fails(self) -> None:
        result = run_cli("nope")
        self.assertNotEqual(result.returncode, 0)


class CliIntegrationTests(TestCase):
    @patch(
        "runtime.cli.run_claude",
        return_value=(
            '{"task_id":"task-1","status":"completed","summary":"ok","decisions":[],"changed_files":[],"verification":{"commands_run":[],"results":[],"all_passed":true},"open_questions":[],"risks":[],"follow_up_suggestions":[],"agent_usage":{"used_subagents":false,"notes":""},"terminal_state":"archived"}',
            "",
        ),
    )
    @patch("runtime.cli.detect_post_run_changes_with_snapshots", return_value=[])
    @patch("runtime.cli.capture_git_status", return_value="")
    def test_run_writes_all_required_artifacts(
        self,
        _mock_status,
        _mock_changes,
        _mock_run,
    ) -> None:
        with TemporaryDirectory() as tmp:
            request = Path(tmp) / "request.json"
            request.write_text(
                '{"task_id":"task-1","task_type":"research","execution_mode":"single-worker","write_policy":"read-only","origin":{"controller":"codex","workflow_stage":"research"},"workdir":"%s","objective":"Research","context_summary":"Summary","inputs":{"files":[],"constraints":[],"acceptance_criteria":["A"],"verification_commands":[],"closeout":{"on_success":"archived","on_failure":"inspection-required"}},"claude_role":{"mode":"research","allow_subagents":false}}'
                % tmp,
                encoding="utf-8",
            )
            exit_code = main(["run", "--request", str(request), "--task-root", tmp])
            task_dir = Path(tmp) / "task-1"
            self.assertEqual(exit_code, 0)
            self.assertTrue((task_dir / "request.json").exists())
            self.assertTrue((task_dir / "request.md").exists())
            self.assertTrue((task_dir / "result.json").exists())
            self.assertTrue((task_dir / "result.md").exists())
            self.assertTrue((task_dir / "run.log").exists())

    @patch(
        "runtime.cli.run_claude",
        return_value=(
            '{"task_id":"task-1","status":"completed","summary":"ok","decisions":[],"changed_files":[],"verification":{"commands_run":[],"results":[],"all_passed":true},"open_questions":[],"risks":[],"follow_up_suggestions":[],"agent_usage":{"used_subagents":false,"notes":""},"terminal_state":"archived"}',
            "",
        ),
    )
    @patch("runtime.cli.detect_post_run_changes_with_snapshots", return_value=["src/a.py"])
    @patch("runtime.cli.capture_git_status", return_value="")
    def test_read_only_change_detection_forces_inspection_required(
        self,
        _mock_status,
        _mock_changes,
        _mock_run,
    ) -> None:
        with TemporaryDirectory() as tmp:
            request_path = Path(tmp) / "request.json"
            request_path.write_text(
                '{"task_id":"task-1","task_type":"research","execution_mode":"single-worker","write_policy":"read-only","origin":{"controller":"codex","workflow_stage":"research"},"workdir":"%s","objective":"Research","context_summary":"Summary","inputs":{"files":[],"constraints":[],"acceptance_criteria":["A"],"verification_commands":[],"closeout":{"on_success":"archived","on_failure":"inspection-required"}},"claude_role":{"mode":"research","allow_subagents":false}}'
                % tmp,
                encoding="utf-8",
            )
            exit_code = main(["run", "--request", str(request_path), "--task-root", tmp])
            result = json.loads(
                (Path(tmp) / "task-1" / "result.json").read_text(encoding="utf-8")
            )
            self.assertNotEqual(exit_code, 0)
            self.assertEqual(result["terminal_state"], "inspection-required")

    @patch(
        "runtime.cli.run_claude",
        return_value=(
            '{"task_id":"task-2","status":"completed","summary":"ok","decisions":[],"changed_files":[],"verification":{"commands_run":[],"results":[],"all_passed":true},"open_questions":[],"risks":[],"follow_up_suggestions":[],"agent_usage":{"used_subagents":false,"notes":""},"terminal_state":"archived"}',
            "",
        ),
    )
    @patch("runtime.cli.build_command", return_value=["claude", "-p"])
    @patch("runtime.cli.capture_git_status", return_value="")
    @patch("runtime.cli.detect_post_run_changes_with_snapshots", return_value=[])
    def test_run_includes_rendered_request_in_prompt(
        self,
        _mock_changes,
        _mock_status,
        mock_build_command,
        _mock_run,
    ) -> None:
        with TemporaryDirectory() as tmp:
            request = Path(tmp) / "request.json"
            request.write_text(
                '{"task_id":"task-2","task_type":"research","execution_mode":"single-worker","write_policy":"read-only","origin":{"controller":"codex","workflow_stage":"research"},"workdir":"%s","objective":"Research prompt","context_summary":"Summary body","inputs":{"files":[],"constraints":["stay local"],"acceptance_criteria":["Return findings"],"verification_commands":[],"closeout":{"on_success":"archived","on_failure":"inspection-required"}},"claude_role":{"mode":"research","allow_subagents":false}}'
                % tmp,
                encoding="utf-8",
            )
            exit_code = main(["run", "--request", str(request), "--task-root", tmp])
            self.assertEqual(exit_code, 0)
            prompt = mock_build_command.call_args.kwargs["prompt"]
            self.assertIn("Research prompt", prompt)
            self.assertIn("Return findings", prompt)

    def test_non_git_read_only_fails_closed(self) -> None:
        with TemporaryDirectory() as tmp:
            request_path = Path(tmp) / "request.json"
            request_path.write_text(
                '{"task_id":"task-3","task_type":"research","execution_mode":"single-worker","write_policy":"read-only","origin":{"controller":"codex","workflow_stage":"research"},"workdir":"%s","objective":"Research","context_summary":"Summary","inputs":{"files":[],"constraints":[],"acceptance_criteria":["A"],"verification_commands":[],"closeout":{"on_success":"archived","on_failure":"inspection-required"}},"claude_role":{"mode":"research","allow_subagents":false}}'
                % tmp,
                encoding="utf-8",
            )
            exit_code = main(["run", "--request", str(request_path), "--task-root", tmp])
            result = json.loads(
                (Path(tmp) / "task-3" / "result.json").read_text(encoding="utf-8")
            )
            self.assertNotEqual(exit_code, 0)
            self.assertEqual(result["terminal_state"], "inspection-required")

    @patch(
        "runtime.cli.run_claude",
        return_value=(
            '{"task_id":"task-4","status":"completed","summary":"ok","decisions":[],"changed_files":[],"verification":{"commands_run":[],"results":[],"all_passed":true},"open_questions":[],"risks":[],"follow_up_suggestions":[],"agent_usage":{"used_subagents":false,"notes":""},"terminal_state":"archived"}',
            "",
        ),
    )
    @patch("runtime.cli.detect_post_run_changes_with_snapshots", return_value=[])
    @patch("runtime.cli.capture_git_status", return_value="")
    @patch("runtime.cli.capture_git_head", side_effect=["before", "after"])
    def test_read_only_head_change_forces_inspection_required(
        self,
        _mock_head,
        _mock_status,
        _mock_changes,
        _mock_run,
    ) -> None:
        with TemporaryDirectory() as tmp:
            request_path = Path(tmp) / "request.json"
            request_path.write_text(
                '{"task_id":"task-4","task_type":"research","execution_mode":"single-worker","write_policy":"read-only","origin":{"controller":"codex","workflow_stage":"research"},"workdir":"%s","objective":"Research","context_summary":"Summary","inputs":{"files":[],"constraints":[],"acceptance_criteria":["A"],"verification_commands":[],"closeout":{"on_success":"archived","on_failure":"inspection-required"}},"claude_role":{"mode":"research","allow_subagents":false}}'
                % tmp,
                encoding="utf-8",
            )
            exit_code = main(["run", "--request", str(request_path), "--task-root", tmp])
            result = json.loads(
                (Path(tmp) / "task-4" / "result.json").read_text(encoding="utf-8")
            )
            self.assertNotEqual(exit_code, 0)
            self.assertEqual(result["terminal_state"], "inspection-required")
