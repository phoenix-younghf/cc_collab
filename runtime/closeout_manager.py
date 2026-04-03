from __future__ import annotations

import subprocess
from pathlib import Path

from runtime.constants import PATCH_FILE


def choose_failure_terminal_state(allowed: list[str]) -> str:
    return "patch-ready" if "patch-ready" in allowed else "inspection-required"


def validate_terminal_state(actual: str, expected: str) -> None:
    if actual != expected:
        raise ValueError("terminal state mismatch")


def build_patch_ready_metadata(task_dir: str) -> dict[str, str]:
    patch_path = f"{task_dir}/{PATCH_FILE}"
    return {
        "patch_path": patch_path,
        "apply_command": f"git apply {patch_path}",
    }


def generate_patch(
    workdir: Path,
    task_dir: Path,
    paths_to_patch: list[str],
) -> dict[str, str]:
    if not paths_to_patch:
        raise RuntimeError("no paths to patch")
    patch_path = task_dir / PATCH_FILE
    subprocess.run(
        ["git", "-C", str(workdir), "add", "-N", "--", *paths_to_patch],
        text=True,
        capture_output=True,
        check=False,
    )
    result = subprocess.run(
        ["git", "-C", str(workdir), "diff", "--binary", "--", *paths_to_patch],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("patch generation failed")
    patch_path.write_text(result.stdout, encoding="utf-8")
    return build_patch_ready_metadata(str(task_dir))
