from __future__ import annotations

from pathlib import Path
from unittest import TestCase

from runtime.capabilities import (
    detect_python_capability,
    detect_claude_capabilities,
    detect_git_capabilities,
    detect_python_launcher,
)


class FakeGit:
    def __init__(self, *, repo: bool, worktree: bool) -> None:
        self.repo = repo
        self.worktree = worktree

    def __call__(self, _workdir: Path, args: list[str]) -> tuple[int, str, str]:
        if args == ["rev-parse", "--is-inside-work-tree"]:
            if self.repo:
                return 0, "true\n", ""
            return 1, "", "not a git repository"
        if args == ["worktree", "list"]:
            if self.worktree:
                return 0, "repo\n", ""
            return 1, "", "worktree unsupported"
        raise AssertionError(f"unexpected git args: {args!r}")


class CapabilityTests(TestCase):
    def test_detect_python_capability_includes_platform_remediation_when_missing(self) -> None:
        capability = detect_python_capability(
            os_name="posix",
            command_exists=lambda _name: False,
        )
        self.assertFalse(capability.available)
        self.assertIn("Homebrew", capability.remediation)

    def test_detect_python_launcher_prefers_windows_py(self) -> None:
        launcher = detect_python_launcher(
            os_name="nt",
            command_exists=lambda name: name in {"py", "python", "python3"},
        )
        self.assertEqual(launcher, "py")

    def test_detect_python_launcher_prefers_unix_python3(self) -> None:
        launcher = detect_python_launcher(
            os_name="posix",
            command_exists=lambda name: name in {"python", "python3"},
        )
        self.assertEqual(launcher, "python3")

    def test_detect_claude_capabilities_reports_missing_flags(self) -> None:
        capability = detect_claude_capabilities(
            command_exists=lambda name: name == "claude",
            flag_probe=lambda flag: flag != "--json-schema",
        )
        self.assertTrue(capability.available)
        self.assertIn("--json-schema", capability.missing_flags)
        self.assertIn("upgrade", capability.remediation.lower())

    def test_detect_git_capabilities_degrades_when_worktree_missing(self) -> None:
        caps = detect_git_capabilities(
            workdir=Path("/tmp/project"),
            command_exists=lambda name: name == "git",
            run_git=FakeGit(repo=True, worktree=False),
        )
        self.assertEqual(caps.mode, "git-aware")
        self.assertFalse(caps.worktree_usable)
        self.assertIn("git worktree", caps.remediation.lower())

    def test_detect_git_capabilities_uses_filesystem_only_when_git_missing(self) -> None:
        caps = detect_git_capabilities(
            workdir=Path("/tmp/project"),
            command_exists=lambda _name: False,
            run_git=FakeGit(repo=False, worktree=False),
        )
        self.assertEqual(caps.mode, "filesystem-only")
        self.assertFalse(caps.git_available)

    def test_detect_git_capabilities_uses_filesystem_only_outside_repo(self) -> None:
        caps = detect_git_capabilities(
            workdir=Path("/tmp/project"),
            command_exists=lambda name: name == "git",
            run_git=FakeGit(repo=False, worktree=False),
        )
        self.assertEqual(caps.mode, "filesystem-only")
        self.assertFalse(caps.repo)
        self.assertIn("repository", caps.remediation.lower())
