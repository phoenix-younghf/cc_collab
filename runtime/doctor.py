from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from runtime.capabilities import (
    detect_claude_capabilities,
    detect_git_capabilities,
    detect_python_capability,
)
from runtime.config import resolve_paths
from runtime.constants import REQUIRED_CLAUDE_FLAGS


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    ok: bool
    severity: str
    section: str
    detail: str
    remediation: str | None = None


@dataclass(frozen=True)
class DoctorReport:
    ok: bool
    checks: list[DoctorCheck]


def _default_writable_probe(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".ccollab-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _default_launcher_probe(launcher_path: Path, os_name: str) -> tuple[bool, str]:
    if not launcher_path.exists():
        return False, f"launcher not found at {launcher_path}"
    command = [str(launcher_path), "--help"]
    if os_name == "nt":
        command = ["cmd", "/c", str(launcher_path), "--help"]
    try:
        result = subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        return False, str(exc)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip() or "launcher invocation failed"
        return False, detail
    return True, "launcher ok"

def _normalize_path_entry(value: str, os_name: str) -> str:
    normalized = value.strip()
    if os_name == "nt":
        return normalized.rstrip("\\/").lower()
    return normalized.rstrip("/")


def run_doctor(
    command_exists: Callable[[str], bool] | None = None,
    flag_probe: Callable[[str], bool] | None = None,
    writable_probe: Callable[[Path], bool] | None = None,
    path_probe: Callable[[str], bool] | None = None,
    launcher_probe: Callable[[], tuple[bool, str]] | None = None,
    os_name: str | None = None,
) -> DoctorReport:
    current_os = os.name if os_name is None else os_name
    exists = command_exists or (lambda name: shutil.which(name) is not None)
    python_capability = detect_python_capability(os_name=current_os, command_exists=exists)
    claude_capability = detect_claude_capabilities(
        command_exists=exists,
        flag_probe=flag_probe,
    )
    git_capability = detect_git_capabilities(
        workdir=Path.cwd(),
        command_exists=exists,
    )
    writable = writable_probe or _default_writable_probe
    path_separator = ";" if current_os == "nt" else os.pathsep
    paths = resolve_paths(os_name=current_os)
    probe_launcher = launcher_probe or (
        lambda: _default_launcher_probe(Path(paths.bin_path), current_os)
    )
    normalized_bin_dir = _normalize_path_entry(str(paths.bin_path.parent), current_os)
    path_contains = path_probe or (
        lambda value: any(
            _normalize_path_entry(entry, current_os) == _normalize_path_entry(value, current_os)
            for entry in os.environ.get("PATH", "").split(path_separator)
            if entry
        )
    )
    launcher_ok, launcher_detail = probe_launcher()
    checks = [
        DoctorCheck(
            "python",
            python_capability.available,
            "error",
            "Install Readiness",
            (
                f"python runtime available ({python_capability.launcher})"
                if python_capability.launcher
                else "python runtime unavailable"
            ),
            remediation=python_capability.remediation,
        ),
        DoctorCheck(
            "launcher",
            launcher_ok,
            "error",
            "Install Readiness",
            launcher_detail,
            remediation=(
                None
                if launcher_ok
                else "Reinstall ccollab to repair the launcher and rerun ccollab doctor."
            ),
        ),
        DoctorCheck(
            "claude",
            claude_capability.available,
            "error",
            "Runtime Readiness",
            "claude command available",
            remediation=claude_capability.remediation,
        ),
    ]
    for flag in REQUIRED_CLAUDE_FLAGS:
        flag_missing = flag in claude_capability.missing_flags
        checks.append(
            DoctorCheck(
                flag,
                claude_capability.available and not flag_missing,
                "error",
                "Runtime Readiness",
                f"claude supports {flag}",
                remediation=claude_capability.remediation if flag_missing else None,
            )
        )
    checks.extend(
        [
            DoctorCheck(
                "skill-dir",
                writable(paths.skill_dir.parent),
                "error",
                "Install Readiness",
                "skill dir writable",
            ),
            DoctorCheck(
                "bin-dir",
                writable(paths.bin_path.parent),
                "error",
                "Install Readiness",
                "bin dir writable",
            ),
            DoctorCheck(
                "config-dir",
                writable(paths.config_dir.parent),
                "error",
                "Install Readiness",
                "config dir writable",
            ),
            DoctorCheck(
                "task-root",
                writable(paths.task_root.parent),
                "error",
                "Install Readiness",
                "task root writable",
            ),
            DoctorCheck(
                "path",
                path_contains(normalized_bin_dir),
                "warning",
                "Install Readiness",
                "bin dir is on PATH",
                remediation=f"Add {paths.bin_path.parent} to PATH or open a new shell session.",
            ),
            DoctorCheck(
                "git",
                git_capability.git_available,
                "warning",
                "Enhanced Safety Capability",
                "git command available",
                remediation=None if git_capability.git_available else git_capability.remediation,
            ),
            DoctorCheck(
                "git-mode",
                git_capability.mode == "git-aware",
                "warning",
                "Enhanced Safety Capability",
                (
                    "git-aware mode available"
                    if git_capability.mode == "git-aware"
                    else "filesystem-only mode active"
                ),
                remediation=(
                    None
                    if git_capability.mode == "git-aware"
                    else git_capability.remediation
                ),
            ),
            DoctorCheck(
                "git-worktree",
                not git_capability.git_available or not git_capability.repo or git_capability.worktree_usable,
                "warning",
                "Enhanced Safety Capability",
                "git worktree available",
                remediation=(
                    None
                    if git_capability.worktree_usable or not git_capability.repo
                    else git_capability.remediation
                ),
            ),
        ]
    )
    return DoctorReport(
        ok=all(check.ok or check.severity != "error" for check in checks),
        checks=checks,
    )


def render_doctor_report(report: DoctorReport) -> str:
    lines = ["Doctor status: OK" if report.ok else "Doctor status: FAIL", ""]
    sections = ["Install Readiness", "Runtime Readiness", "Enhanced Safety Capability"]
    for index, section in enumerate(sections):
        lines.append(section)
        for check in report.checks:
            if check.section != section:
                continue
            if check.ok:
                prefix = "[ok]"
            elif check.severity == "warning":
                prefix = "[warn]"
            else:
                prefix = "[fail]"
            lines.append(f"{prefix} {check.name}: {check.detail}")
            if not check.ok and check.remediation:
                lines.append(f"  -> {check.remediation}")
        if index != len(sections) - 1:
            lines.append("")
    return "\n".join(lines) + "\n"
