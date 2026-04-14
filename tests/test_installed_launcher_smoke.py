from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, skipUnless


REPO_ROOT = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash") or "/bin/bash"
UNIX_SMOKE_AVAILABLE = os.name != "nt" and shutil.which("bash") is not None


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _make_python_shims(bin_dir: Path) -> None:
    script = f"""#!{BASH}
set -euo pipefail
exec {sys.executable} "$@"
"""
    _write_executable(bin_dir / "python3", script)
    _write_executable(bin_dir / "python", script)


def _make_git_shim(bin_dir: Path) -> None:
    git = shutil.which("git")
    if not git:
        raise RuntimeError("git unavailable")
    script = f"""#!{BASH}
set -euo pipefail
exec {git} "$@"
"""
    _write_executable(bin_dir / "git", script)


def _make_fake_claude(bin_dir: Path) -> None:
    script = f"""#!{BASH}
set -euo pipefail
if [[ "${{1-}}" == "--help" ]]; then
  cat <<'EOF'
Usage: claude [options]
  -p
  --output-format
  --json-schema
  --add-dir
  --append-system-prompt
  --agents
EOF
  exit 0
fi
if [[ "${{CCOLLAB_FAKE_CLAUDE_MODE-stdout}}" == "timeout" ]]; then
  printf 'partial stdout'
  sleep "${{CCOLLAB_FAKE_CLAUDE_SLEEP_SECONDS-5}}"
  exit 0
fi
printf '%s' "${{CCOLLAB_FAKE_CLAUDE_STDOUT}}"
"""
    _write_executable(bin_dir / "claude", script)


def _base_env(temp_root: Path, *, include_git: bool) -> dict[str, str]:
    home = temp_root / "home"
    bin_dir = temp_root / "bin"
    home.mkdir(parents=True, exist_ok=True)
    bin_dir.mkdir(parents=True, exist_ok=True)
    _make_python_shims(bin_dir)
    _make_fake_claude(bin_dir)
    if include_git:
        _make_git_shim(bin_dir)
    return {
        "HOME": str(home),
        "CODEX_HOME": str(home / ".codex"),
        "LOCALAPPDATA": str(home / "AppData" / "Local"),
        "APPDATA": str(home / "AppData" / "Roaming"),
        "PATH": os.pathsep.join([str(bin_dir), "/usr/bin", "/bin", "/usr/sbin", "/sbin"]),
    }


def _install_launcher(temp_root: Path, *, include_git: bool) -> subprocess.CompletedProcess[str]:
    env = _base_env(temp_root, include_git=include_git)
    return subprocess.run(
        [BASH, str(REPO_ROOT / "install" / "install-all.sh")],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _rewrite_request(template_path: Path, destination: Path, workdir: Path) -> dict:
    payload = json.loads(template_path.read_text(encoding="utf-8"))
    payload["workdir"] = str(workdir)
    destination.write_text(json.dumps(payload), encoding="utf-8")
    return payload


def _seed_git_repo(repo_root: Path, env: dict[str, str]) -> None:
    repo_root.mkdir(parents=True, exist_ok=True)
    readme = repo_root / "README.md"
    readme.write_text("smoke\n", encoding="utf-8")
    subprocess.run(["git", "init", str(repo_root)], env=env, text=True, capture_output=True, check=True)
    subprocess.run(
        ["git", "-C", str(repo_root), "config", "user.name", "ccollab smoke"],
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_root), "config", "user.email", "ccollab-smoke@example.com"],
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    subprocess.run(["git", "-C", str(repo_root), "add", "README.md"], env=env, text=True, capture_output=True, check=True)
    subprocess.run(
        ["git", "-C", str(repo_root), "commit", "-m", "init smoke repo"],
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )


def run_installed_ccollab(
    template_name: str,
    *,
    temp_root: str,
    rewrite_workdir: str,
    seed_git_repo: bool = False,
    fake_claude_mode: str = "stdout",
    fake_claude_sleep_seconds: int = 5,
    request_mutator: callable | None = None,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    temp_path = Path(temp_root)
    env = _base_env(temp_path, include_git=seed_git_repo)
    install_result = _install_launcher(temp_path, include_git=seed_git_repo)
    if install_result.returncode != 0:
        raise AssertionError(install_result.stderr or install_result.stdout)
    workdir = Path(rewrite_workdir)
    if seed_git_repo:
        _seed_git_repo(workdir, env)
    else:
        workdir.mkdir(parents=True, exist_ok=True)
    request_path = temp_path / "request.json"
    request = _rewrite_request(REPO_ROOT / template_name, request_path, workdir)
    if request_mutator is not None:
        request_mutator(request)
        request_path.write_text(json.dumps(request), encoding="utf-8")
    env["CCOLLAB_FAKE_CLAUDE_MODE"] = fake_claude_mode
    env["CCOLLAB_FAKE_CLAUDE_SLEEP_SECONDS"] = str(fake_claude_sleep_seconds)
    env["CCOLLAB_FAKE_CLAUDE_STDOUT"] = json.dumps(
        {
            "task_id": request["task_id"],
            "status": "completed",
            "summary": "smoke ok",
            "decisions": [],
            "changed_files": [],
            "verification": {"commands_run": [], "results": [], "all_passed": True},
            "open_questions": [],
            "risks": [],
            "follow_up_suggestions": [],
            "agent_usage": {"used_subagents": False, "notes": "fake claude smoke"},
            "terminal_state": "archived",
        }
    )
    launcher = temp_path / "home" / ".local" / "bin" / "ccollab"
    task_root = temp_path / "tasks"
    result = subprocess.run(
        [str(launcher), "run", "--request", str(request_path), "--task-root", str(task_root)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    return result, task_root / request["task_id"]


@skipUnless(UNIX_SMOKE_AVAILABLE, "Unix installed-launcher smoke requires bash and the Unix launcher")
class InstalledLauncherSmokeTests(TestCase):
    def test_installed_launcher_run_filesystem_only_smoke(self) -> None:
        with TemporaryDirectory() as tmp:
            result, task_dir = run_installed_ccollab(
                "examples/filesystem-only-smoke-task.json",
                temp_root=tmp,
                rewrite_workdir=tmp,
            )
            self.assertEqual(result.returncode, 0)
            payload = json.loads((task_dir / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "completed")
            self.assertEqual(payload["runtime_mode"], "filesystem-only")
            self.assertTrue((task_dir / "result.md").exists())

    @skipUnless(shutil.which("git"), "git required for git-aware installed-launcher smoke")
    def test_installed_launcher_run_git_aware_smoke(self) -> None:
        with TemporaryDirectory() as tmp:
            repo_root = Path(tmp) / "repo"
            result, task_dir = run_installed_ccollab(
                "examples/git-aware-smoke-task.json",
                temp_root=tmp,
                rewrite_workdir=str(repo_root),
                seed_git_repo=True,
            )
            self.assertEqual(result.returncode, 0)
            payload = json.loads((task_dir / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "completed")
            self.assertEqual(payload["runtime_mode"], "git-aware")
            self.assertTrue((task_dir / "result.md").exists())

    def test_installed_launcher_timeout_persists_failure_artifacts(self) -> None:
        with TemporaryDirectory() as tmp:
            result, task_dir = run_installed_ccollab(
                "examples/filesystem-only-smoke-task.json",
                temp_root=tmp,
                rewrite_workdir=tmp,
                fake_claude_mode="timeout",
                fake_claude_sleep_seconds=2,
                request_mutator=lambda request: request["claude_role"].__setitem__("timeout_seconds", 1),
            )
            self.assertEqual(result.returncode, 1)
            payload = json.loads((task_dir / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "failed")
            self.assertIn("timed out", payload["summary"].lower())
            self.assertTrue((task_dir / "run.log").exists())
            run_log = (task_dir / "run.log").read_text(encoding="utf-8")
            self.assertIn("partial stdout", run_log)
