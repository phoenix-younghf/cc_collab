from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from runtime.constants import REQUIRED_CLAUDE_FLAGS


CommandExists = Callable[[str], bool]
FlagProbe = Callable[[str], bool]
RunGit = Callable[[Path, list[str]], tuple[int, str, str]]


@dataclass(frozen=True)
class PythonCapability:
    available: bool
    launcher: str | None
    remediation: str | None


@dataclass(frozen=True)
class ClaudeCapability:
    available: bool
    missing_flags: list[str]
    remediation: str | None


@dataclass(frozen=True)
class GitCapabilities:
    git_available: bool
    repo: bool
    worktree_usable: bool
    mode: str
    remediation: str | None


@dataclass(frozen=True)
class RuntimeCapabilities:
    python: PythonCapability
    claude: ClaudeCapability
    git: GitCapabilities


def _default_command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def _default_flag_probe(flag: str) -> bool:
    help_result = subprocess.run(
        ["claude", "--help"],
        text=True,
        capture_output=True,
        check=False,
    )
    help_text = (help_result.stdout or "") + "\n" + (help_result.stderr or "")
    return flag in help_text or (flag == "--print" and "-p" in help_text)


def _default_run_git(workdir: Path, args: list[str]) -> tuple[int, str, str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(workdir), *args],
            text=True,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError:
        return 1, "", "git unavailable"
    return result.returncode, result.stdout, result.stderr


def detect_python_launcher(
    *,
    os_name: str | None = None,
    command_exists: CommandExists | None = None,
) -> str | None:
    capability = detect_python_capability(
        os_name=os_name,
        command_exists=command_exists,
    )
    return capability.launcher


def detect_python_capability(
    *,
    os_name: str | None = None,
    command_exists: CommandExists | None = None,
) -> PythonCapability:
    target_os = os.name if os_name is None else os_name
    exists = _default_command_exists if command_exists is None else command_exists
    candidates = ("py", "python", "python3") if target_os == "nt" else ("python3", "python")
    for candidate in candidates:
        if exists(candidate):
            return PythonCapability(
                available=True,
                launcher=candidate,
                remediation=None,
            )
    if target_os == "nt":
        remediation = (
            "Install Python 3.9 or newer and rerun ccollab doctor. "
            "If available, use: winget install --exact --id Python.Python.3"
        )
    else:
        remediation = (
            "Install Python 3.9 or newer and rerun ccollab doctor. "
            "On macOS, you can use Homebrew: brew install python"
        )
    return PythonCapability(
        available=False,
        launcher=None,
        remediation=remediation,
    )


def detect_claude_capabilities(
    *,
    command_exists: CommandExists | None = None,
    flag_probe: FlagProbe | None = None,
) -> ClaudeCapability:
    exists = _default_command_exists if command_exists is None else command_exists
    probe = _default_flag_probe if flag_probe is None else flag_probe
    if not exists("claude"):
        return ClaudeCapability(
            available=False,
            missing_flags=list(REQUIRED_CLAUDE_FLAGS),
            remediation="Install Claude CLI, then rerun ccollab doctor.",
        )
    missing_flags = [flag for flag in REQUIRED_CLAUDE_FLAGS if not probe(flag)]
    remediation = None
    if missing_flags:
        remediation = (
            "Upgrade Claude CLI so it supports the required flags: "
            + ", ".join(missing_flags)
        )
    return ClaudeCapability(
        available=True,
        missing_flags=missing_flags,
        remediation=remediation,
    )


def detect_git_capabilities(
    *,
    workdir: Path,
    command_exists: CommandExists | None = None,
    run_git: RunGit | None = None,
) -> GitCapabilities:
    exists = _default_command_exists if command_exists is None else command_exists
    runner = _default_run_git if run_git is None else run_git
    if not exists("git"):
        return GitCapabilities(
            git_available=False,
            repo=False,
            worktree_usable=False,
            mode="filesystem-only",
            remediation="Install Git to enable git-aware safety features and patch-based closeout.",
        )

    repo_returncode, repo_stdout, _repo_stderr = runner(
        workdir,
        ["rev-parse", "--is-inside-work-tree"],
    )
    repo = repo_returncode == 0 and repo_stdout.strip().lower() == "true"
    if not repo:
        return GitCapabilities(
            git_available=True,
            repo=False,
            worktree_usable=False,
            mode="filesystem-only",
            remediation="Run ccollab inside a Git repository to enable git-aware safety features.",
        )

    worktree_returncode, _worktree_stdout, _worktree_stderr = runner(
        workdir,
        ["worktree", "list"],
    )
    return GitCapabilities(
        git_available=True,
        repo=True,
        worktree_usable=worktree_returncode == 0,
        mode="git-aware",
        remediation=(
            None
            if worktree_returncode == 0
            else "Enable git worktree support to keep write-isolated runs on the Git-backed path."
        ),
    )


def detect_runtime_capabilities(
    *,
    workdir: Path,
    os_name: str | None = None,
    command_exists: CommandExists | None = None,
    flag_probe: FlagProbe | None = None,
    run_git: RunGit | None = None,
) -> RuntimeCapabilities:
    return RuntimeCapabilities(
        python=detect_python_capability(
            os_name=os_name,
            command_exists=command_exists,
        ),
        claude=detect_claude_capabilities(
            command_exists=command_exists,
            flag_probe=flag_probe,
        ),
        git=detect_git_capabilities(
            workdir=workdir,
            command_exists=command_exists,
            run_git=run_git,
        ),
    )
