from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from runtime.config import resolve_paths
from runtime.constants import REQUIRED_CLAUDE_FLAGS


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    ok: bool
    detail: str


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


def _default_flag_probe(flag: str) -> bool:
    help_result = subprocess.run(
        ["claude", "--help"],
        text=True,
        capture_output=True,
        check=False,
    )
    help_text = (help_result.stdout or "") + "\n" + (help_result.stderr or "")
    return flag in help_text or (flag == "--print" and "-p" in help_text)


def run_doctor(
    command_exists: Callable[[str], bool] | None = None,
    flag_probe: Callable[[str], bool] | None = None,
    writable_probe: Callable[[Path], bool] | None = None,
    path_probe: Callable[[str], bool] | None = None,
) -> DoctorReport:
    exists = command_exists or (lambda name: shutil.which(name) is not None)
    claude_exists = exists("claude")
    flag_ok = flag_probe or _default_flag_probe
    writable = writable_probe or _default_writable_probe
    path_contains = path_probe or (
        lambda value: value in os.environ.get("PATH", "").split(os.pathsep)
    )
    paths = resolve_paths()
    checks = [
        DoctorCheck("git", exists("git"), "git command available"),
        DoctorCheck("python3", exists("python3"), "python3 command available"),
        DoctorCheck("claude", claude_exists, "claude command available"),
        DoctorCheck("ccollab", exists("ccollab"), "ccollab command available"),
    ]
    for flag in REQUIRED_CLAUDE_FLAGS:
        checks.append(
            DoctorCheck(
                flag,
                claude_exists and flag_ok(flag),
                f"claude supports {flag}",
            )
        )
    checks.extend(
        [
            DoctorCheck("skill-dir", writable(paths.skill_dir.parent), "skill dir writable"),
            DoctorCheck("bin-dir", writable(paths.bin_path.parent), "bin dir writable"),
            DoctorCheck("config-dir", writable(paths.config_dir.parent), "config dir writable"),
            DoctorCheck("task-root", writable(paths.task_root.parent), "task root writable"),
            DoctorCheck(
                "path",
                path_contains(str(paths.bin_path.parent)),
                "bin dir is on PATH",
            ),
        ]
    )
    return DoctorReport(ok=all(check.ok for check in checks), checks=checks)


def render_doctor_report(report: DoctorReport) -> str:
    lines = [
        "Doctor status: OK" if report.ok else "Doctor status: FAIL",
    ]
    for check in report.checks:
        prefix = "[ok]" if check.ok else "[fail]"
        lines.append(f"{prefix} {check.name}: {check.detail}")
    return "\n".join(lines) + "\n"
