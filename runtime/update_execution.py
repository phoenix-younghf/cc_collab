from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Callable


class VerificationError(RuntimeError):
    """Raised when post-install verification fails."""


@dataclass(frozen=True)
class VerificationContext:
    os_name: str
    timeout_seconds: int = 45


@dataclass(frozen=True)
class VerificationResult:
    command: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class WindowsSwapPlan:
    requires_helper: bool
    working_directory: Path
    helper_command: tuple[str, ...] | None
    swap_intent_path: Path | None


@dataclass(frozen=True)
class UpdateTransactionResult:
    ok: bool
    rollback_performed: bool
    rollback_succeeded: bool | None
    verification: VerificationResult | None
    error: str | None


VerificationRunner = Callable[..., VerificationResult]


def current_working_directory() -> Path:
    return Path.cwd()


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _inside_any(path: Path, roots: tuple[Path, ...]) -> bool:
    for root in roots:
        if _is_relative_to(path, root):
            return True
    return False


def _neutral_workdir(*, roots: tuple[Path, ...]) -> Path:
    candidates = [Path(tempfile.gettempdir()), roots[0].parent]
    for candidate in candidates:
        if candidate.exists() and not _inside_any(candidate, roots):
            return candidate
    return Path(tempfile.mkdtemp(prefix="ccollab-update-neutral-"))


def _timestamp_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def prepare_windows_swap(
    *,
    install_root: Path,
    staged_root: Path,
    backup_root: Path,
    helper_executable: Path,
    current_workdir: Path | None = None,
) -> WindowsSwapPlan:
    roots = (install_root, staged_root, backup_root)
    cwd = current_working_directory() if current_workdir is None else current_workdir
    working_directory = _neutral_workdir(roots=roots)
    requires_helper = _inside_any(cwd, roots)
    if not requires_helper:
        return WindowsSwapPlan(
            requires_helper=False,
            working_directory=working_directory,
            helper_command=None,
            swap_intent_path=None,
        )

    intent_path = install_root.parent / ".ccollab-update.swap-intent.json"
    payload = {
        "install_root": str(install_root),
        "staged_root": str(staged_root),
        "backup_root": str(backup_root),
        "helper_executable": str(helper_executable),
        "created_at": _timestamp_utc(),
    }
    intent_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    helper_command = (
        sys.executable,
        str(helper_executable),
        "--swap-intent",
        str(intent_path),
    )
    return WindowsSwapPlan(
        requires_helper=True,
        working_directory=working_directory,
        helper_command=helper_command,
        swap_intent_path=intent_path,
    )


def build_windows_verification_command(install_root: Path) -> list[str]:
    launcher = str(PureWindowsPath(str(install_root)) / "bin" / "ccollab.cmd")
    return ["cmd", "/c", launcher, "doctor"]


def build_posix_verification_command(install_root: Path) -> list[str]:
    launcher = install_root / "bin" / "ccollab"
    return [str(launcher), "doctor"]


def run_post_install_verification(
    *,
    install_root: Path,
    verification_context: VerificationContext,
    env: dict[str, str] | None = None,
) -> VerificationResult:
    os_name = (verification_context.os_name or os.name).lower()
    if os_name.startswith("win"):
        command = build_windows_verification_command(install_root)
    else:
        command = build_posix_verification_command(install_root)

    child_env = dict(os.environ if env is None else env)
    child_env["CCOLLAB_RUNTIME_ROOT"] = str(install_root)
    working_directory = _neutral_workdir(roots=(install_root, install_root, install_root))

    try:
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
            timeout=verification_context.timeout_seconds,
            env=child_env,
            cwd=str(working_directory),
        )
    except subprocess.TimeoutExpired as exc:
        raise VerificationError(
            f"Post-install verification timed out after {verification_context.timeout_seconds} seconds."
        ) from exc

    result = VerificationResult(
        command=tuple(command),
        exit_code=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
    )
    if completed.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise VerificationError(
            detail or f"Post-install verification failed with exit code {completed.returncode}."
        )
    return result


def apply_update_transaction(
    *,
    install_root: Path,
    staged_root: Path,
    backup_root: Path,
    verification_context: VerificationContext,
    verification_runner: VerificationRunner | None = None,
) -> UpdateTransactionResult:
    verifier = run_post_install_verification if verification_runner is None else verification_runner
    verification_result: VerificationResult | None = None
    try:
        os.replace(install_root, backup_root)
        os.replace(staged_root, install_root)
    except OSError as exc:
        return UpdateTransactionResult(
            ok=False,
            rollback_performed=False,
            rollback_succeeded=None,
            verification=None,
            error=str(exc),
        )

    try:
        verification_result = verifier(
            install_root=install_root,
            verification_context=verification_context,
        )
    except VerificationError as exc:
        rollback_succeeded = False
        rollback_performed = True
        failed_install_root = backup_root.parent / f".ccollab-update-failed-{os.getpid()}"
        try:
            if failed_install_root.exists():
                shutil.rmtree(failed_install_root, ignore_errors=True)
            if install_root.exists():
                os.replace(install_root, failed_install_root)
            os.replace(backup_root, install_root)
            rollback_succeeded = True
        except OSError:
            rollback_succeeded = False
        return UpdateTransactionResult(
            ok=False,
            rollback_performed=rollback_performed,
            rollback_succeeded=rollback_succeeded,
            verification=None,
            error=str(exc),
        )

    shutil.rmtree(backup_root, ignore_errors=True)
    return UpdateTransactionResult(
        ok=True,
        rollback_performed=False,
        rollback_succeeded=None,
        verification=verification_result,
        error=None,
    )
