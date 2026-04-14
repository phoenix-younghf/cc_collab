from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
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
from runtime.claude_runner import ClaudeTimeoutError
from runtime.cli import _repair_source_output, main
from runtime.constants import REQUIRED_CLAUDE_FLAGS
from runtime.updater import (
    BrokenLauncherError,
    CompatibilityError,
    GhAuthenticationError,
    GhPrerequisiteError,
    RepoAccessError,
    UpdateExecutionError,
    UpdateResult,
    UpdaterError,
)
from runtime.versioning import (
    InstallDiscovery,
    InstallRootNotFoundError,
    InstallMetadata,
    MultipleInstallRootsError,
    read_install_metadata,
    write_install_metadata,
)

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


def file_change_set_metadata(
    changed_files: list[str] | None = None,
) -> dict[str, object]:
    files = changed_files or ["src.txt"]
    entries = [
        {
            "original_path": path,
            "stored_path": f"file-change-set/{path}",
            "before_hash": f"before-{index}",
            "after_hash": f"after-{index}",
            "change_kind": "modified",
        }
        for index, path in enumerate(files, start=1)
    ]
    return {
        "artifact_type": "file-change-set",
        "changed_files": files,
        "change_set_manifest": {
            "entries": entries,
            "inspect_instructions": "Inspect copied files under file-change-set/.",
            "copy_back_instructions": "Copy reviewed files back into the workspace.",
        },
    }


def git_patch_metadata() -> dict[str, str]:
    return {
        "artifact_type": "git-patch",
        "patch_path": "/tmp/changes.patch",
        "apply_command": "git apply /tmp/changes.patch",
    }


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
    claude_model: str | None = None,
    timeout_seconds: int | None = None,
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
    claude_role: dict[str, object] = {
        "mode": "research",
        "allow_subagents": False,
    }
    if claude_model is not None:
        claude_role["model"] = claude_model
    if timeout_seconds is not None:
        claude_role["timeout_seconds"] = timeout_seconds
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
                "claude_role": claude_role,
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


class CliVersionTests(TestCase):
    def test_version_discovers_active_runtime_root_independently_from_override(self) -> None:
        with TemporaryDirectory() as tmp:
            active_root = Path(tmp) / "active-install"
            (active_root / "runtime").mkdir(parents=True)
            (active_root / "bin").mkdir()
            discovery = InstallDiscovery(
                install_root=active_root,
                status="installed",
                metadata=None,
                version="0.4.2",
                channel="stable",
                repo="owner/cc_collab",
            )
            captured: dict[str, object] = {}

            def fake_discover_install_root(**kwargs: object) -> InstallDiscovery:
                captured.update(kwargs)
                captured["env"] = dict(kwargs["env"])  # type: ignore[index]
                return discovery

            with patch.dict(
                os.environ,
                {"CCOLLAB_RUNTIME_ROOT": "/tmp/override-install"},
                clear=False,
            ):
                with patch("runtime.cli.__file__", str(active_root / "runtime" / "cli.py")):
                    with patch(
                        "runtime.cli.discover_install_root",
                        side_effect=fake_discover_install_root,
                    ):
                        stdout = io.StringIO()
                        with redirect_stdout(stdout):
                            exit_code = main(["version"])

            self.assertEqual(exit_code, 0)
            self.assertEqual(captured["active_runtime_root"], active_root)
            self.assertEqual(
                captured["env"]["CCOLLAB_RUNTIME_ROOT"],  # type: ignore[index]
                "/tmp/override-install",
            )

    def test_version_reports_installed_metadata(self) -> None:
        with TemporaryDirectory() as tmp:
            install_root = Path(tmp) / "install"
            install_root.mkdir()
            write_install_metadata(
                install_root,
                InstallMetadata(
                    version="0.4.2",
                    channel="stable",
                    repo="owner/cc_collab",
                    platform="linux-x64",
                    installed_at="2026-04-13T12:34:56Z",
                    asset_name="ccollab-linux-x64.tar.gz",
                    asset_sha256="abc123",
                    install_root=str(install_root),
                ),
            )
            discovery = InstallDiscovery(
                install_root=install_root,
                status="installed",
                metadata=read_install_metadata(install_root),
                version="0.4.2",
                channel="stable",
                repo="owner/cc_collab",
            )
            with patch("runtime.cli.discover_install_root", return_value=discovery):
                stdout = io.StringIO()
                with redirect_stdout(stdout):
                    exit_code = main(["version"])
            self.assertEqual(exit_code, 0)
            self.assertEqual(
                stdout.getvalue(),
                "\n".join(
                    [
                        "ccollab 0.4.2",
                        f"install root: {install_root}",
                        "source: github.com/owner/cc_collab",
                        "channel: stable",
                        "",
                    ]
                ),
            )

    def test_version_reports_legacy_install(self) -> None:
        discovery = InstallDiscovery(
            install_root=Path("/tmp/legacy-install"),
            status="legacy-install",
            metadata=None,
            version="unknown",
            channel="unknown",
            repo="legacy-install",
        )
        with patch("runtime.cli.discover_install_root", return_value=discovery):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = main(["version"])
        self.assertEqual(exit_code, 0)
        self.assertEqual(
            stdout.getvalue(),
            "\n".join(
                [
                    "ccollab unknown",
                    "install root: /tmp/legacy-install",
                    "source: legacy-install",
                    "channel: unknown",
                    "",
                ]
            ),
        )

    def test_version_degrades_invalid_metadata_to_legacy_install(self) -> None:
        with TemporaryDirectory() as tmp:
            install_root = Path(tmp) / "install"
            (install_root / "runtime").mkdir(parents=True)
            (install_root / "bin").mkdir()
            (install_root / "install-metadata.json").write_text('{"version": ', encoding="utf-8")
            stdout = io.StringIO()
            with patch("runtime.cli.get_active_runtime_root", return_value=install_root):
                with patch.dict(os.environ, {"HOME": tmp}, clear=True):
                    with redirect_stdout(stdout):
                        exit_code = main(["version"])
            self.assertEqual(exit_code, 0)
            self.assertEqual(
                stdout.getvalue(),
                "\n".join(
                    [
                        "ccollab unknown",
                        f"install root: {install_root}",
                        "source: legacy-install",
                        "channel: unknown",
                        "",
                    ]
                ),
            )

    def test_version_reports_multiple_install_remediation(self) -> None:
        with patch(
            "runtime.cli.discover_install_root",
            side_effect=MultipleInstallRootsError(
                "Set CCOLLAB_RUNTIME_ROOT to the intended install and retry."
            ),
        ):
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                exit_code = main(["version"])
        self.assertNotEqual(exit_code, 0)
        self.assertIn("CCOLLAB_RUNTIME_ROOT", stderr.getvalue())


class CliUpdateTests(TestCase):
    def test_update_reports_already_up_to_date(self) -> None:
        with patch(
            "runtime.cli.run_update",
            return_value=UpdateResult.noop(current_version="0.4.2", latest_version="0.4.2"),
        ):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = main(["update"])
        self.assertEqual(exit_code, 0)
        output = stdout.getvalue()
        self.assertIn("Current version: 0.4.2", output)
        self.assertIn("Latest version: 0.4.2", output)
        self.assertIn("already up to date", output)

    def test_update_reports_success_for_legacy_unknown_install(self) -> None:
        with patch(
            "runtime.cli.run_update",
            return_value=UpdateResult.success(current_version="unknown", latest_version="0.4.2"),
        ):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = main(["update"])
        self.assertEqual(exit_code, 0)
        output = stdout.getvalue()
        self.assertIn("Current version: unknown", output)
        self.assertIn("Latest version: 0.4.2", output)
        self.assertIn("Updated ccollab to 0.4.2", output)

    def test_update_renders_progress_messages_before_success_line(self) -> None:
        with patch(
            "runtime.cli.run_update",
            return_value=UpdateResult.success(
                current_version="0.4.1",
                latest_version="0.4.2",
                progress_messages=(
                    "Downloading ccollab-linux-x64.tar.gz...",
                    "Verifying checksum...",
                    "Installing update...",
                    "Running post-install verification...",
                ),
            ),
        ):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = main(["update"])
        self.assertEqual(exit_code, 0)
        output = stdout.getvalue()
        self.assertIn("Downloading ccollab-linux-x64.tar.gz...", output)
        self.assertIn("Verifying checksum...", output)
        self.assertIn("Installing update...", output)
        self.assertIn("Running post-install verification...", output)
        self.assertIn("Updated ccollab to 0.4.2", output)

    def test_update_does_not_create_task_artifacts(self) -> None:
        with patch(
            "runtime.cli.run_update",
            return_value=UpdateResult.noop(current_version="0.4.2", latest_version="0.4.2"),
        ):
            with patch("runtime.cli.create_task_dir") as create_task_dir_mock:
                exit_code = main(["update"])
        self.assertEqual(exit_code, 0)
        create_task_dir_mock.assert_not_called()

    def test_update_reports_missing_install_root_remediation(self) -> None:
        with patch(
            "runtime.cli.run_update",
            side_effect=InstallRootNotFoundError(
                "No valid ccollab install was found. Reinstall ccollab using the normal install flow, then retry."
            ),
        ):
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                exit_code = main(["update"])
        self.assertNotEqual(exit_code, 0)
        self.assertIn("normal install flow", stderr.getvalue())

    def test_update_reports_broken_launcher_remediation(self) -> None:
        with patch(
            "runtime.cli.run_update",
            side_effect=BrokenLauncherError(
                "Launcher is missing or unhealthy. Reinstall ccollab to repair the launcher, then retry."
            ),
        ):
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                exit_code = main(["update"])
        self.assertNotEqual(exit_code, 0)
        self.assertIn("repair the launcher", stderr.getvalue())

    def test_update_reports_multiple_install_remediation(self) -> None:
        with patch(
            "runtime.cli.run_update",
            side_effect=MultipleInstallRootsError(
                "Multiple ccollab installs were detected. Set CCOLLAB_RUNTIME_ROOT to the intended install and retry."
            ),
        ):
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                exit_code = main(["update"])
        self.assertNotEqual(exit_code, 0)
        self.assertIn("CCOLLAB_RUNTIME_ROOT", stderr.getvalue())

    def test_update_reports_missing_gh_remediation(self) -> None:
        with patch(
            "runtime.cli.run_update",
            side_effect=GhPrerequisiteError("Install GitHub CLI and run 'gh auth login'."),
        ):
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                exit_code = main(["update"])
        self.assertNotEqual(exit_code, 0)
        self.assertIn("Install GitHub CLI", stderr.getvalue())

    def test_update_reports_gh_authentication_remediation(self) -> None:
        with patch(
            "runtime.cli.run_update",
            side_effect=GhAuthenticationError("Run 'gh auth login' for github.com, then retry."),
        ):
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                exit_code = main(["update"])
        self.assertNotEqual(exit_code, 0)
        self.assertIn("gh auth login", stderr.getvalue())

    def test_update_reports_repo_access_remediation(self) -> None:
        with patch(
            "runtime.cli.run_update",
            side_effect=RepoAccessError("Authenticated GitHub CLI could not access owner/cc_collab releases."),
        ):
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                exit_code = main(["update"])
        self.assertNotEqual(exit_code, 0)
        self.assertIn("could not access", stderr.getvalue())

    def test_update_reports_dependency_compatibility_remediation(self) -> None:
        with patch(
            "runtime.cli.run_update",
            side_effect=CompatibilityError("Python 3.8.18 does not satisfy manifest minimum 3.9.0."),
        ):
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                exit_code = main(["update"])
        self.assertNotEqual(exit_code, 0)
        output = stderr.getvalue()
        self.assertIn("newer local runtime dependency", output)
        self.assertIn("Python 3.8.18", output)

    def test_update_reports_generic_failure(self) -> None:
        with patch(
            "runtime.cli.run_update",
            side_effect=UpdaterError("checksum mismatch"),
        ):
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                exit_code = main(["update"])
        self.assertNotEqual(exit_code, 0)
        output = stderr.getvalue()
        self.assertIn("Update failed: checksum mismatch", output)
        self.assertIn("Existing installation was left unchanged.", output)

    def test_update_reports_successful_rollback_after_failed_verification(self) -> None:
        with patch(
            "runtime.cli.run_update",
            side_effect=UpdateExecutionError(
                "doctor failed",
                current_version="0.4.1",
                latest_version="0.4.2",
                progress_messages=(
                    "Downloading ccollab-linux-x64.tar.gz...",
                    "Verifying checksum...",
                    "Installing update...",
                    "Running post-install verification...",
                ),
                rollback_succeeded=True,
            ),
        ):
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                exit_code = main(["update"])
        self.assertNotEqual(exit_code, 0)
        output = stderr.getvalue()
        self.assertIn("Current version: 0.4.1", output)
        self.assertIn("Latest version: 0.4.2", output)
        self.assertIn("Running post-install verification...", output)
        self.assertIn("Update failed: doctor failed", output)
        self.assertIn("Previous installation was restored.", output)


class CliIntegrationTests(TestCase):
    def test_repair_source_output_prefers_partial_parsed_dict_over_raw_envelope(self) -> None:
        parsed_output = {
            "task_id": "task-2d",
            "status": "completed",
            "summary": "ok",
            "result": {
                "task_id": "task-2d",
                "status": "completed",
                "summary": "ok",
            },
        }
        raw_output = '{"type":"result","subtype":"success","result":"noisy envelope"}'

        repair_source = _repair_source_output(parsed_output, raw_output)

        self.assertEqual(json.loads(repair_source), parsed_output)

    @patch("runtime.cli.detect_post_run_changes_with_snapshots", return_value=[])
    @patch("runtime.cli.capture_git_status", return_value="")
    @patch("runtime.cli.capture_git_head", side_effect=["before", "before"])
    def test_filesystem_only_run_repairs_partial_result_and_writes_artifact(
        self,
        _mock_head,
        _mock_status,
        _mock_changes,
    ) -> None:
        partial_payload = {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": json.dumps(
                {
                    "task_id": "task-2d",
                    "status": "completed",
                    "summary": "smoke ok",
                    "execution_mode": "single-worker",
                    "write_policy": "read-only",
                    "runtime_path": "filesystem-only",
                    "constraints_honored": True,
                    "result": {
                        "artifact_written": True,
                    },
                }
            ),
        }
        call_count = 0

        def fake_run(command: list[str]) -> tuple[str, str]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return json.dumps(partial_payload), ""
            repair_prompt = command[-1]
            self.assertIn('"task_id": "task-2d"', repair_prompt)
            self.assertNotIn('"type": "result"', repair_prompt)
            return completed_output("task-2d", notes="repair"), ""

        with TemporaryDirectory() as tmp:
            request = write_request(tmp, task_id="task-2d", write_policy="read-only")
            with patch("runtime.cli.detect_runtime_capabilities", return_value=filesystem_only_caps()):
                with patch("runtime.cli.run_claude", side_effect=fake_run):
                    exit_code = main(["run", "--request", str(request), "--task-root", tmp])
            task_dir = Path(tmp) / "task-2d"
            result = json.loads((task_dir / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(exit_code, 0)
            self.assertTrue((task_dir / "result.json").exists())
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["runtime_mode"], "filesystem-only")
            self.assertEqual(call_count, 2)

    @patch("runtime.cli.detect_post_run_changes_with_snapshots", return_value=[])
    @patch("runtime.cli.capture_git_status", return_value="")
    @patch("runtime.cli.capture_git_head", side_effect=["before", "before"])
    def test_run_persists_timeout_failure_when_claude_hangs(
        self,
        _mock_head,
        _mock_status,
        _mock_changes,
    ) -> None:
        with TemporaryDirectory() as tmp:
            request = write_request(
                tmp,
                task_id="task-timeout",
                write_policy="read-only",
                timeout_seconds=45,
            )
            with patch("runtime.cli.detect_runtime_capabilities", return_value=filesystem_only_caps()):
                with patch("runtime.cli.run_claude", side_effect=ClaudeTimeoutError(45)):
                    exit_code = main(["run", "--request", str(request), "--task-root", tmp])
            task_dir = Path(tmp) / "task-timeout"
            result = json.loads((task_dir / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(exit_code, 1)
            self.assertEqual(result["status"], "failed")
            self.assertIn("timed out", result["summary"].lower())
            self.assertEqual(result["terminal_state"], "inspection-required")
            self.assertTrue((task_dir / "run.log").exists())

    @patch("runtime.cli.detect_post_run_changes_with_snapshots", return_value=[])
    @patch("runtime.cli.capture_git_status", return_value="")
    @patch("runtime.cli.capture_git_head", side_effect=["before", "before"])
    def test_run_persists_timeout_failure_with_partial_bytes_output(
        self,
        _mock_head,
        _mock_status,
        _mock_changes,
    ) -> None:
        with TemporaryDirectory() as tmp:
            request = write_request(
                tmp,
                task_id="task-timeout-bytes",
                write_policy="read-only",
                timeout_seconds=45,
            )
            with patch("runtime.cli.detect_runtime_capabilities", return_value=filesystem_only_caps()):
                with patch(
                    "runtime.cli.run_claude",
                    side_effect=ClaudeTimeoutError(
                        45,
                        stdout=b"partial stdout",
                        stderr=b"partial stderr",
                    ),
                ):
                    exit_code = main(["run", "--request", str(request), "--task-root", tmp])
            task_dir = Path(tmp) / "task-timeout-bytes"
            result = json.loads((task_dir / "result.json").read_text(encoding="utf-8"))
            run_log = (task_dir / "run.log").read_text(encoding="utf-8")
            self.assertEqual(exit_code, 1)
            self.assertEqual(result["status"], "failed")
            self.assertIn("timed out", result["summary"].lower())
            self.assertIn("partial stdout", run_log)
            self.assertIn("partial stderr", run_log)

    @patch("runtime.cli.run_claude", return_value=(completed_output("task-timeout-2"), ""))
    @patch("runtime.cli.detect_post_run_changes_with_snapshots", return_value=[])
    @patch("runtime.cli.capture_git_status", return_value="")
    @patch("runtime.cli.capture_git_head", side_effect=["before", "before"])
    def test_run_passes_request_timeout_to_runner(
        self,
        _mock_head,
        _mock_status,
        _mock_changes,
        mock_run,
    ) -> None:
        with TemporaryDirectory() as tmp:
            request = write_request(
                tmp,
                task_id="task-timeout-2",
                write_policy="read-only",
                timeout_seconds=45,
            )
            with patch("runtime.cli.detect_runtime_capabilities", return_value=filesystem_only_caps()):
                exit_code = main(["run", "--request", str(request), "--task-root", tmp])
            self.assertEqual(exit_code, 0)
            self.assertEqual(mock_run.call_args.kwargs["timeout_seconds"], 45)

    @patch("runtime.cli.detect_post_run_changes_with_snapshots", return_value=[])
    @patch("runtime.cli.capture_git_status", return_value="")
    @patch("runtime.cli.capture_git_head", side_effect=["before", "before"])
    def test_run_repair_uses_requested_model_and_timeout(
        self,
        _mock_head,
        _mock_status,
        _mock_changes,
    ) -> None:
        calls: list[tuple[list[str], dict[str, object]]] = []

        def fake_run(command: list[str], **kwargs: object) -> tuple[str, str]:
            calls.append((command, kwargs))
            if len(calls) == 1:
                return '{"invalid":', ""
            return completed_output("task-repair-timeout"), ""

        with TemporaryDirectory() as tmp:
            request = write_request(
                tmp,
                task_id="task-repair-timeout",
                write_policy="read-only",
                claude_model="sonnet",
                timeout_seconds=45,
            )
            with patch("runtime.cli.detect_runtime_capabilities", return_value=filesystem_only_caps()):
                with patch("runtime.cli.run_claude", side_effect=fake_run):
                    exit_code = main(["run", "--request", str(request), "--task-root", tmp])
            self.assertEqual(exit_code, 0)
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[0][1]["timeout_seconds"], 45)
            self.assertEqual(calls[1][1]["timeout_seconds"], 45)
            self.assertIn("--model", calls[1][0])
            self.assertIn("sonnet", calls[1][0])

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
            self.assertEqual(result["artifact_type"], "none")
            self.assertIn("capability_summary", result)
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
            self.assertEqual(result["artifact_type"], "none")
            self.assertIn("capability_summary", result)

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
            self.assertEqual(result["artifact_type"], "none")
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
        "runtime.cli.build_file_change_set_metadata",
        return_value=file_change_set_metadata(),
    )
    def test_non_git_write_isolated_uses_filesystem_copy(
        self,
        _mock_change_set: object,
    ) -> None:
        with TemporaryDirectory() as tmp:
            request = write_request(tmp, write_policy="write-isolated")
            def fake_run(_command: object) -> tuple[str, str]:
                (Path(tmp) / "task-1" / "isolated-copy" / "src.txt").write_text("after", encoding="utf-8")
                return completed_output("task-1", changed_files=["src.txt"]), ""
            with patch(
                "runtime.cli.detect_runtime_capabilities",
                return_value=filesystem_only_caps(),
            ), patch("runtime.cli.run_claude", side_effect=fake_run):
                exit_code = main(["run", "--request", str(request), "--task-root", tmp])
            result = json.loads((Path(tmp) / "task-1" / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(exit_code, 0)
            self.assertEqual(result["runtime_mode"], "filesystem-only")
            self.assertEqual(result["artifact_type"], "file-change-set")

    @patch(
        "runtime.cli.build_file_change_set_metadata",
        return_value=file_change_set_metadata(),
    )
    def test_non_git_write_isolated_patch_ready_emits_file_change_set_metadata(
        self,
        mock_change_set: object,
    ) -> None:
        with TemporaryDirectory() as tmp:
            request = write_request(
                tmp,
                write_policy="write-isolated",
                success_terminal="commit-ready",
            )
            def fake_run(_command: object) -> tuple[str, str]:
                (Path(tmp) / "task-1" / "isolated-copy" / "src.txt").write_text("after", encoding="utf-8")
                return completed_output("task-1", changed_files=["src.txt"]), ""
            with patch(
                "runtime.cli.detect_runtime_capabilities",
                return_value=filesystem_only_caps(),
            ), patch("runtime.cli.run_claude", side_effect=fake_run):
                exit_code = main(["run", "--request", str(request), "--task-root", tmp])
            result = json.loads((Path(tmp) / "task-1" / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(exit_code, 0)
            self.assertEqual(result["terminal_state"], "patch-ready")
            self.assertEqual(result["artifact_type"], "file-change-set")
            self.assertIn("change_set_manifest", result)
            mock_change_set.assert_called_once()

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
        "runtime.cli.build_git_patch_metadata_for_workspace_pair",
        return_value=git_patch_metadata(),
    )
    def test_degraded_git_aware_write_isolated_success_stays_git_aware(
        self,
        _mock_patch_pair: object,
    ) -> None:
        with TemporaryDirectory() as tmp:
            request = write_request(
                tmp,
                write_policy="write-isolated",
                success_terminal="patch-ready",
            )
            def fake_run(_command: object) -> tuple[str, str]:
                (Path(tmp) / "task-1" / "isolated-copy" / "src.txt").write_text("after", encoding="utf-8")
                return completed_output("task-1", changed_files=["src.txt"]), ""
            with patch(
                "runtime.cli.detect_runtime_capabilities",
                return_value=git_aware_caps(worktree_usable=False),
            ), patch("runtime.cli.run_claude", side_effect=fake_run):
                exit_code = main(["run", "--request", str(request), "--task-root", tmp])
            result = json.loads((Path(tmp) / "task-1" / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(exit_code, 0)
            self.assertEqual(result["runtime_mode"], "git-aware")
            self.assertEqual(result["artifact_type"], "git-patch")
            self.assertTrue(result["degradation_notes"])

    @patch(
        "runtime.cli.build_file_change_set_metadata",
        return_value=file_change_set_metadata(["src.txt", "extra.txt"]),
    )
    @patch("runtime.cli.detect_post_run_changes_with_snapshots", return_value=["src.txt", "extra.txt"])
    def test_filesystem_only_write_in_place_patch_ready_failure_emits_change_set_metadata(
        self,
        _mock_changes: object,
        mock_change_set: object,
    ) -> None:
        with TemporaryDirectory() as tmp:
            request = write_request(
                tmp,
                write_policy="write-in-place",
                failure_terminal="patch-ready",
                files=["src.txt"],
            )
            def fake_run(_command: object) -> tuple[str, str]:
                (Path(tmp) / "src.txt").write_text("after", encoding="utf-8")
                return completed_output("task-1"), ""
            with patch(
                "runtime.cli.detect_runtime_capabilities",
                return_value=filesystem_only_caps(),
            ), patch("runtime.cli.run_claude", side_effect=fake_run):
                exit_code = main(["run", "--request", str(request), "--task-root", tmp])
            result = json.loads((Path(tmp) / "task-1" / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(exit_code, 1)
            self.assertEqual(result["terminal_state"], "patch-ready")
            self.assertEqual(result["artifact_type"], "file-change-set")
            self.assertIn("change_set_manifest", result)
            mock_change_set.assert_called_once()

    @patch("runtime.cli.collect_file_change_set_entries", side_effect=ValueError("boom"))
    @patch("runtime.cli.run_claude", side_effect=RuntimeError("boom"))
    def test_filesystem_only_change_set_failure_degrades_to_inspection_required(
        self,
        _mock_run: object,
        _mock_entries: object,
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
            self.assertEqual(result["terminal_state"], "inspection-required")
            self.assertEqual(result["artifact_type"], "none")

    @patch(
        "runtime.cli.build_file_change_set_metadata",
        return_value=file_change_set_metadata(["src.txt"]),
    )
    def test_filesystem_only_write_in_place_exception_emits_change_set_metadata(
        self,
        mock_change_set: object,
    ) -> None:
        with TemporaryDirectory() as tmp:
            request = write_request(
                tmp,
                write_policy="write-in-place",
                failure_terminal="patch-ready",
                files=["src.txt"],
            )
            def fake_run(_command: object) -> tuple[str, str]:
                (Path(tmp) / "src.txt").write_text("after", encoding="utf-8")
                raise RuntimeError("boom")
            with patch(
                "runtime.cli.detect_runtime_capabilities",
                return_value=filesystem_only_caps(),
            ), patch("runtime.cli.run_claude", side_effect=fake_run):
                exit_code = main(["run", "--request", str(request), "--task-root", tmp])
            result = json.loads((Path(tmp) / "task-1" / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(exit_code, 1)
            self.assertEqual(result["terminal_state"], "patch-ready")
            self.assertEqual(result["artifact_type"], "file-change-set")
            self.assertIn("change_set_manifest", result)
            mock_change_set.assert_called_once()
