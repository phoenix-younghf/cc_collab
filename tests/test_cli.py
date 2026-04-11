from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import redirect_stderr
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from runtime.capabilities import (
    ClaudeCapability,
    GitCapabilities,
    PythonCapability,
    RuntimeCapabilities,
)
from runtime.cli import main
from runtime.constants import REQUIRED_CLAUDE_FLAGS

ROOT = Path(__file__).resolve().parents[1]


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "runtime.cli", *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )


def completed_output(
    task_id: str = "task-1",
    *,
    changed_files: list[str] | None = None,
    terminal_state: str = "archived",
    notes: str = "",
) -> str:
    payload = {
        "task_id": task_id,
        "status": "completed",
        "summary": "ok",
        "decisions": [],
        "changed_files": changed_files or [],
        "verification": {
            "commands_run": [],
            "results": [],
            "all_passed": True,
        },
        "open_questions": [],
        "risks": [],
        "follow_up_suggestions": [],
        "agent_usage": {
            "used_subagents": False,
            "notes": notes,
        },
        "terminal_state": terminal_state,
    }
    return json.dumps(payload)


def write_request(
    temp_root: str,
    *,
    task_id: str = "task-1",
    write_policy: str = "read-only",
    success_terminal: str | None = None,
    failure_terminal: str | None = None,
    workdir: str | None = None,
    files: list[str] | None = None,
    create_workdir: bool = True,
) -> Path:
    workdir_path = Path(workdir or temp_root)
    if create_workdir:
        workdir_path.mkdir(parents=True, exist_ok=True)
    default_success = {
        "read-only": "archived",
        "write-in-place": "integrated",
        "write-isolated": "commit-ready",
    }
    default_failure = {
        "read-only": "inspection-required",
        "write-in-place": "inspection-required",
        "write-isolated": "discarded",
    }
    requested_files = files
    if requested_files is None:
        requested_files = [] if write_policy == "read-only" else ["src.txt"]
    for relative_path in requested_files:
        target = workdir_path / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_text("before", encoding="utf-8")
    request_path = Path(temp_root) / f"{task_id}.json"
    request_path.write_text(
        json.dumps(
            {
                "task_id": task_id,
                "task_type": "research",
                "execution_mode": "single-worker",
                "write_policy": write_policy,
                "origin": {
                    "controller": "codex",
                    "workflow_stage": "research",
                },
                "workdir": str(workdir_path),
                "objective": "Research",
                "context_summary": "Summary",
                "inputs": {
                    "files": requested_files,
                    "constraints": [],
                    "acceptance_criteria": ["A"],
                    "verification_commands": [],
                    "closeout": {
                        "on_success": success_terminal or default_success[write_policy],
                        "on_failure": failure_terminal or default_failure[write_policy],
                    },
                },
                "claude_role": {
                    "mode": "research",
                    "allow_subagents": False,
                },
            }
        ),
        encoding="utf-8",
    )
    return request_path


def git_aware_caps(*, worktree_usable: bool = True) -> RuntimeCapabilities:
    remediation = None
    if not worktree_usable:
        remediation = "Enable git worktree support to keep write-isolated runs on the Git-backed path."
    return RuntimeCapabilities(
        python=PythonCapability(available=True, launcher="python3", remediation=None),
        claude=ClaudeCapability(available=True, missing_flags=[], remediation=None),
        git=GitCapabilities(
            git_available=True,
            repo=True,
            worktree_usable=worktree_usable,
            mode="git-aware",
            remediation=remediation,
        ),
    )


def filesystem_only_caps() -> RuntimeCapabilities:
    return RuntimeCapabilities(
        python=PythonCapability(available=True, launcher="python3", remediation=None),
        claude=ClaudeCapability(available=True, missing_flags=[], remediation=None),
        git=GitCapabilities(
            git_available=False,
            repo=False,
            worktree_usable=False,
            mode="filesystem-only",
            remediation="Install Git to enable git-aware safety features and patch-based closeout.",
        ),
    )


def missing_claude_caps() -> RuntimeCapabilities:
    return RuntimeCapabilities(
        python=PythonCapability(available=True, launcher="python3", remediation=None),
        claude=ClaudeCapability(
            available=False,
            missing_flags=list(REQUIRED_CLAUDE_FLAGS),
            remediation="Install Claude CLI, then rerun ccollab doctor.",
        ),
        git=GitCapabilities(
            git_available=False,
            repo=False,
            worktree_usable=False,
            mode="filesystem-only",
            remediation="Install Git to enable git-aware safety features and patch-based closeout.",
        ),
    )


def unsupported_claude_caps() -> RuntimeCapabilities:
    return RuntimeCapabilities(
        python=PythonCapability(available=True, launcher="python3", remediation=None),
        claude=ClaudeCapability(
            available=True,
            missing_flags=["--agents"],
            remediation="Upgrade Claude CLI so it supports the required flags: --agents",
        ),
        git=GitCapabilities(
            git_available=True,
            repo=True,
            worktree_usable=True,
            mode="git-aware",
            remediation=None,
        ),
    )


def diagnostic_dir_for(task_id: str) -> Path:
    return Path(tempfile.gettempdir()) / "ccollab-diagnostics" / task_id


def find_temp_diagnostic(task_id: str) -> Path:
    return diagnostic_dir_for(task_id) / "result.json"


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

    def test_installed_entrypoint_works_outside_repo_via_symlink(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            entrypoint = tmp_path / "ccollab"
            entrypoint.symlink_to(ROOT / "bin" / "ccollab")
            env = os.environ.copy()
            env.pop("PYTHONPATH", None)
            result = subprocess.run(
                [str(entrypoint), "--help"],
                cwd=tmp_path,
                text=True,
                capture_output=True,
                env=env,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("doctor", result.stdout)


class CliIntegrationTests(TestCase):
    @patch("runtime.cli.run_claude", return_value=(completed_output("task-1"), ""))
    @patch("runtime.cli.detect_post_run_changes_with_snapshots", return_value=[])
    @patch("runtime.cli.capture_git_status", return_value="")
    @patch("runtime.cli.capture_git_head", side_effect=["before", "before"])
    def test_run_writes_all_required_artifacts(
        self,
        _mock_head,
        _mock_status,
        _mock_changes,
        _mock_run,
    ) -> None:
        with TemporaryDirectory() as tmp:
            request = write_request(tmp, task_id="task-1", write_policy="read-only")
            with patch("runtime.cli.detect_runtime_capabilities", return_value=git_aware_caps()):
                exit_code = main(["run", "--request", str(request), "--task-root", tmp])
            task_dir = Path(tmp) / "task-1"
            self.assertEqual(exit_code, 0)
            self.assertTrue((task_dir / "request.json").exists())
            self.assertTrue((task_dir / "request.md").exists())
            self.assertTrue((task_dir / "result.json").exists())
            self.assertTrue((task_dir / "result.md").exists())
            self.assertTrue((task_dir / "run.log").exists())

    @patch("runtime.cli.run_claude", return_value=(completed_output("task-1"), ""))
    @patch("runtime.cli.detect_post_run_changes_with_snapshots", return_value=["src/a.py"])
    @patch("runtime.cli.capture_git_status", return_value="")
    @patch("runtime.cli.capture_git_head", side_effect=["before", "before"])
    def test_read_only_change_detection_forces_inspection_required(
        self,
        _mock_head,
        _mock_status,
        _mock_changes,
        _mock_run,
    ) -> None:
        with TemporaryDirectory() as tmp:
            request_path = write_request(tmp, task_id="task-1", write_policy="read-only")
            with patch("runtime.cli.detect_runtime_capabilities", return_value=git_aware_caps()):
                exit_code = main(["run", "--request", str(request_path), "--task-root", tmp])
            result = json.loads(
                (Path(tmp) / "task-1" / "result.json").read_text(encoding="utf-8")
            )
            self.assertNotEqual(exit_code, 0)
            self.assertEqual(result["terminal_state"], "inspection-required")

    @patch("runtime.cli.run_claude", return_value=(completed_output("task-2"), ""))
    @patch("runtime.cli.build_command", return_value=["claude", "-p"])
    @patch("runtime.cli.capture_git_status", return_value="")
    @patch("runtime.cli.detect_post_run_changes_with_snapshots", return_value=[])
    @patch("runtime.cli.capture_git_head", side_effect=["before", "before"])
    def test_run_includes_rendered_request_in_prompt(
        self,
        _mock_head,
        _mock_changes,
        _mock_status,
        mock_build_command,
        _mock_run,
    ) -> None:
        with TemporaryDirectory() as tmp:
            request = write_request(tmp, task_id="task-2", write_policy="read-only")
            with patch("runtime.cli.detect_runtime_capabilities", return_value=git_aware_caps()):
                exit_code = main(["run", "--request", str(request), "--task-root", tmp])
            self.assertEqual(exit_code, 0)
            prompt = mock_build_command.call_args.kwargs["prompt"]
            self.assertIn("Research", prompt)
            self.assertIn("A", prompt)

    @patch(
        "runtime.cli.run_claude",
        side_effect=[
            (
                '{"type":"result","subtype":"success","is_error":false,"result":"Delegation succeeded. Evidence came from README.md and runtime/claude_runner.py."}',
                "",
            ),
            (completed_output("task-2b", notes="repair"), ""),
        ],
    )
    @patch("runtime.cli.detect_post_run_changes_with_snapshots", return_value=[])
    @patch("runtime.cli.capture_git_status", return_value="")
    @patch("runtime.cli.capture_git_head", side_effect=["before", "before"])
    def test_run_repairs_envelope_result_without_structured_payload(
        self,
        _mock_head,
        _mock_status,
        _mock_changes,
        mock_run,
    ) -> None:
        with TemporaryDirectory() as tmp:
            request = write_request(tmp, task_id="task-2b", write_policy="read-only")
            with patch("runtime.cli.detect_runtime_capabilities", return_value=git_aware_caps()):
                exit_code = main(["run", "--request", str(request), "--task-root", tmp])
            result = json.loads(
                (Path(tmp) / "task-2b" / "result.json").read_text(encoding="utf-8")
            )
            self.assertEqual(exit_code, 0)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(mock_run.call_count, 2)

    @patch(
        "runtime.cli.run_claude",
        side_effect=[
            (
                '{"type":"result","subtype":"success","is_error":false,"result":"Delegation succeeded based on README.md and runtime/claude_runner.py."}',
                "",
            ),
            (
                '{"answer":"yes","explanation":"Delegation is implemented.","evidence":["README.md","runtime/claude_runner.py"]}',
                "",
            ),
        ],
    )
    @patch("runtime.cli.detect_post_run_changes_with_snapshots", return_value=[])
    @patch("runtime.cli.capture_git_status", return_value="")
    @patch("runtime.cli.capture_git_head", side_effect=["before", "before"])
    def test_run_normalizes_nonstandard_repair_payload_for_read_only_success(
        self,
        _mock_head,
        _mock_status,
        _mock_changes,
        mock_run,
    ) -> None:
        with TemporaryDirectory() as tmp:
            request = write_request(tmp, task_id="task-2c", write_policy="read-only")
            with patch("runtime.cli.detect_runtime_capabilities", return_value=git_aware_caps()):
                exit_code = main(["run", "--request", str(request), "--task-root", tmp])
            result = json.loads(
                (Path(tmp) / "task-2c" / "result.json").read_text(encoding="utf-8")
            )
            self.assertEqual(exit_code, 0)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["terminal_state"], "archived")
            self.assertIn("Delegation succeeded", result["summary"])
            self.assertEqual(mock_run.call_count, 2)

    @patch("runtime.cli.run_claude", return_value=(completed_output("task-4"), ""))
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
            request_path = write_request(tmp, task_id="task-4", write_policy="read-only")
            with patch("runtime.cli.detect_runtime_capabilities", return_value=git_aware_caps()):
                exit_code = main(["run", "--request", str(request_path), "--task-root", tmp])
            result = json.loads(
                (Path(tmp) / "task-4" / "result.json").read_text(encoding="utf-8")
            )
            self.assertNotEqual(exit_code, 0)
            self.assertEqual(result["terminal_state"], "inspection-required")


class CliRuntimeModeTests(TestCase):
    @patch("runtime.cli.run_claude", return_value=(completed_output("task-1"), ""))
    @patch("runtime.cli.detect_post_run_changes_with_snapshots", return_value=[])
    @patch("runtime.cli.capture_git_status", return_value="")
    @patch("runtime.cli.capture_git_head", side_effect=["before", "before"])
    def test_git_aware_read_only_runs_inside_repo(
        self,
        _mock_head,
        _mock_status,
        _mock_changes,
        _mock_run,
    ) -> None:
        with TemporaryDirectory() as tmp:
            request = write_request(tmp, write_policy="read-only")
            with patch("runtime.cli.detect_runtime_capabilities", return_value=git_aware_caps()):
                exit_code = main(["run", "--request", str(request), "--task-root", tmp])
            result = json.loads((Path(tmp) / "task-1" / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(exit_code, 0)
            self.assertEqual(result["runtime_mode"], "git-aware")

    def test_run_preflight_fails_when_claude_flags_are_unsupported(self) -> None:
        with TemporaryDirectory() as tmp:
            request = write_request(tmp, write_policy="read-only")
            with patch(
                "runtime.cli.detect_runtime_capabilities",
                return_value=unsupported_claude_caps(),
            ):
                exit_code = main(["run", "--request", str(request), "--task-root", tmp])
            result = json.loads((Path(tmp) / "task-1" / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(exit_code, 1)
            self.assertIn("flag", result["summary"].lower())
            self.assertIn("upgrade", result["remediation"].lower())

    @patch("runtime.cli.run_claude", return_value=(completed_output("task-1"), ""))
    def test_non_git_read_only_runs_in_filesystem_only_mode(self, _mock_run: object) -> None:
        with TemporaryDirectory() as tmp:
            request = write_request(tmp, write_policy="read-only")
            with patch(
                "runtime.cli.detect_runtime_capabilities",
                return_value=filesystem_only_caps(),
            ):
                exit_code = main(["run", "--request", str(request), "--task-root", tmp])
            result = json.loads((Path(tmp) / "task-1" / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(exit_code, 0)
            self.assertEqual(result["runtime_mode"], "filesystem-only")

    def test_run_preflight_persists_failure_when_claude_missing(self) -> None:
        with TemporaryDirectory() as tmp:
            request = write_request(tmp, write_policy="read-only")
            with patch(
                "runtime.cli.detect_runtime_capabilities",
                return_value=missing_claude_caps(),
            ):
                exit_code = main(["run", "--request", str(request), "--task-root", tmp])
            result = json.loads((Path(tmp) / "task-1" / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(exit_code, 1)
            self.assertEqual(result["status"], "failed")
            self.assertIn("claude", result["summary"].lower())
            self.assertIn("preflight", result["capability_summary"]["status"])
            self.assertIn("install", result["remediation"].lower())

    def test_run_preflight_fails_when_workdir_missing(self) -> None:
        with TemporaryDirectory() as tmp:
            request = write_request(
                tmp,
                workdir=str(Path(tmp) / "missing"),
                create_workdir=False,
            )
            exit_code = main(["run", "--request", str(request), "--task-root", tmp])
            result = json.loads((Path(tmp) / "task-1" / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(exit_code, 1)
            self.assertIn("workdir", result["summary"].lower())

    def test_run_preflight_writes_temp_diagnostics_when_task_root_unwritable(self) -> None:
        task_id = "task-diag"
        diagnostic_dir = diagnostic_dir_for(task_id)
        shutil.rmtree(diagnostic_dir, ignore_errors=True)
        with TemporaryDirectory() as tmp:
            request = write_request(tmp, task_id=task_id)
            stderr = io.StringIO()
            with patch("runtime.cli.create_task_dir", side_effect=PermissionError("blocked")):
                with redirect_stderr(stderr):
                    exit_code = main(["run", "--request", str(request), "--task-root", str(Path(tmp) / "blocked")])
        self.assertEqual(exit_code, 1)
        self.assertTrue(find_temp_diagnostic(task_id).exists())
        self.assertIn(str(diagnostic_dir), stderr.getvalue())

    @patch(
        "runtime.cli.generate_patch_from_workspace_pair",
        return_value={
            "patch_path": "/tmp/changes.patch",
            "apply_command": "git apply -p2 /tmp/changes.patch",
        },
    )
    @patch("runtime.cli.run_claude", return_value=(completed_output("task-1", changed_files=["src.txt"]), ""))
    def test_non_git_write_isolated_uses_filesystem_copy(
        self,
        _mock_run: object,
        _mock_patch_pair: object,
    ) -> None:
        with TemporaryDirectory() as tmp:
            request = write_request(tmp, write_policy="write-isolated")
            with patch(
                "runtime.cli.detect_runtime_capabilities",
                return_value=filesystem_only_caps(),
            ):
                exit_code = main(["run", "--request", str(request), "--task-root", tmp])
            result = json.loads((Path(tmp) / "task-1" / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(exit_code, 0)
            self.assertEqual(result["runtime_mode"], "filesystem-only")

    @patch(
        "runtime.cli.generate_patch_from_workspace_pair",
        return_value={
            "patch_path": "/tmp/changes.patch",
            "apply_command": "git apply -p2 /tmp/changes.patch",
        },
    )
    @patch("runtime.cli.run_claude", return_value=(completed_output("task-1", changed_files=["src.txt"]), ""))
    def test_non_git_write_isolated_patch_ready_emits_patch_metadata(
        self,
        _mock_run: object,
        mock_patch_pair: object,
    ) -> None:
        with TemporaryDirectory() as tmp:
            request = write_request(
                tmp,
                write_policy="write-isolated",
                success_terminal="commit-ready",
            )
            with patch(
                "runtime.cli.detect_runtime_capabilities",
                return_value=filesystem_only_caps(),
            ):
                exit_code = main(["run", "--request", str(request), "--task-root", tmp])
            result = json.loads((Path(tmp) / "task-1" / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(exit_code, 0)
            self.assertEqual(result["terminal_state"], "patch-ready")
            self.assertEqual(result["patch_path"], "/tmp/changes.patch")
            self.assertIn("git apply", result["apply_command"])
            mock_patch_pair.assert_called_once()

    @patch("runtime.cli.generate_patch", return_value={"patch_path": "/tmp/changes.patch"})
    @patch("runtime.cli.run_claude", return_value=(completed_output("task-1", changed_files=["src.txt"]), ""))
    @patch("runtime.cli.build_command", return_value=["claude", "-p"])
    def test_degraded_write_isolated_commit_ready_is_rewritten_to_patch_ready_before_claude(
        self,
        mock_build: object,
        _mock_run: object,
        _mock_patch: object,
    ) -> None:
        with TemporaryDirectory() as tmp:
            request = write_request(
                tmp,
                write_policy="write-isolated",
                success_terminal="commit-ready",
            )
            with patch(
                "runtime.cli.detect_runtime_capabilities",
                return_value=filesystem_only_caps(),
            ):
                main(["run", "--request", str(request), "--task-root", tmp])
        runtime_contract = mock_build.call_args.kwargs["runtime_contract"]
        self.assertIn("allowed_success_terminal=patch-ready", runtime_contract)

    @patch(
        "runtime.cli.generate_patch_from_workspace_pair",
        return_value={
            "patch_path": "/tmp/changes.patch",
            "apply_command": "git apply -p2 /tmp/changes.patch",
        },
    )
    @patch("runtime.cli.run_claude", return_value=(completed_output("task-1", changed_files=["src.txt"]), ""))
    def test_degraded_git_aware_write_isolated_success_stays_git_aware(
        self,
        _mock_run: object,
        _mock_patch_pair: object,
    ) -> None:
        with TemporaryDirectory() as tmp:
            request = write_request(
                tmp,
                write_policy="write-isolated",
                success_terminal="patch-ready",
            )
            with patch(
                "runtime.cli.detect_runtime_capabilities",
                return_value=git_aware_caps(worktree_usable=False),
            ):
                exit_code = main(["run", "--request", str(request), "--task-root", tmp])
            result = json.loads((Path(tmp) / "task-1" / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(exit_code, 0)
            self.assertEqual(result["runtime_mode"], "git-aware")
            self.assertTrue(result["degradation_notes"])

    @patch(
        "runtime.cli.generate_patch_from_workspace_pair",
        return_value={
            "patch_path": "/tmp/changes.patch",
            "apply_command": "git apply -p2 /tmp/changes.patch",
        },
    )
    @patch("runtime.cli.detect_post_run_changes_with_snapshots", return_value=["src.txt", "extra.txt"])
    @patch("runtime.cli.run_claude", return_value=(completed_output("task-1"), ""))
    def test_filesystem_only_write_in_place_patch_ready_failure_emits_patch_metadata(
        self,
        _mock_run: object,
        _mock_changes: object,
        mock_patch_pair: object,
    ) -> None:
        with TemporaryDirectory() as tmp:
            request = write_request(
                tmp,
                write_policy="write-in-place",
                failure_terminal="patch-ready",
                files=["src.txt"],
            )
            with patch(
                "runtime.cli.detect_runtime_capabilities",
                return_value=filesystem_only_caps(),
            ):
                exit_code = main(["run", "--request", str(request), "--task-root", tmp])
            result = json.loads((Path(tmp) / "task-1" / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(exit_code, 1)
            self.assertEqual(result["terminal_state"], "patch-ready")
            self.assertEqual(result["patch_path"], "/tmp/changes.patch")
            self.assertIn("git apply", result["apply_command"])
            mock_patch_pair.assert_called_once()

    @patch(
        "runtime.cli.generate_patch_from_workspace_pair",
        return_value={
            "patch_path": "/tmp/changes.patch",
            "apply_command": "git apply -p2 /tmp/changes.patch",
        },
    )
    @patch("runtime.cli.run_claude", side_effect=RuntimeError("boom"))
    def test_filesystem_only_write_in_place_exception_emits_patch_metadata(
        self,
        _mock_run: object,
        mock_patch_pair: object,
    ) -> None:
        with TemporaryDirectory() as tmp:
            request = write_request(
                tmp,
                write_policy="write-in-place",
                failure_terminal="patch-ready",
                files=["src.txt"],
            )
            with patch(
                "runtime.cli.detect_runtime_capabilities",
                return_value=filesystem_only_caps(),
            ):
                exit_code = main(["run", "--request", str(request), "--task-root", tmp])
            result = json.loads((Path(tmp) / "task-1" / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(exit_code, 1)
            self.assertEqual(result["terminal_state"], "patch-ready")
            self.assertEqual(result["patch_path"], "/tmp/changes.patch")
            self.assertIn("git apply", result["apply_command"])
            mock_patch_pair.assert_called_once()
