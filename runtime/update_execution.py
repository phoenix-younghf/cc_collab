from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Callable


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


class VerificationError(RuntimeError):
    """Raised when post-install verification fails."""

    def __init__(
        self,
        message: str,
        *,
        result: VerificationResult | None = None,
    ) -> None:
        super().__init__(message)
        self.result = result


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


def _coerce_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _lock_path(install_root: Path) -> Path:
    return install_root.parent / ".ccollab-update.lock"


def _lock_record_path(install_root: Path) -> Path:
    return install_root.parent / ".ccollab-update.lock.json"


def _handoff_record_path(install_root: Path) -> Path:
    return install_root.parent / ".ccollab-update.handoff.json"


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


def _verification_context_payload(context: VerificationContext) -> dict[str, object]:
    return {
        "os_name": context.os_name,
        "timeout_seconds": context.timeout_seconds,
    }


def _verification_result_payload(result: VerificationResult | None) -> dict[str, object] | None:
    if result is None:
        return None
    return {
        "command": list(result.command),
        "exit_code": result.exit_code,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def write_transaction_result(result_path: Path, result: UpdateTransactionResult) -> None:
    result_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ok": result.ok,
        "rollback_performed": result.rollback_performed,
        "rollback_succeeded": result.rollback_succeeded,
        "verification": _verification_result_payload(result.verification),
        "error": result.error,
    }
    result_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_transaction_result(result_path: Path) -> UpdateTransactionResult:
    payload = json.loads(result_path.read_text(encoding="utf-8-sig"))
    verification_payload = payload.get("verification")
    verification: VerificationResult | None = None
    if isinstance(verification_payload, dict):
        command = verification_payload.get("command")
        if not isinstance(command, list):
            raise ValueError("verification.command must be a list")
        verification = VerificationResult(
            command=tuple(str(part) for part in command),
            exit_code=int(verification_payload.get("exit_code", 0)),
            stdout=str(verification_payload.get("stdout", "")),
            stderr=str(verification_payload.get("stderr", "")),
        )
    return UpdateTransactionResult(
        ok=bool(payload.get("ok")),
        rollback_performed=bool(payload.get("rollback_performed")),
        rollback_succeeded=payload.get("rollback_succeeded"),
        verification=verification,
        error=payload.get("error"),
    )


def prepare_windows_swap(
    *,
    install_root: Path,
    staged_root: Path,
    backup_root: Path,
    helper_executable: Path,
    current_workdir: Path | None = None,
    verification_context: VerificationContext | None = None,
    result_path: Path | None = None,
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
    payload: dict[str, object] = {
        "install_root": str(install_root),
        "staged_root": str(staged_root),
        "backup_root": str(backup_root),
        "helper_executable": str(helper_executable),
        "created_at": _timestamp_utc(),
    }
    if verification_context is not None:
        payload["verification_context"] = _verification_context_payload(verification_context)
    if result_path is not None:
        payload["result_path"] = str(result_path)
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
        result = VerificationResult(
            command=tuple(command),
            exit_code=-1,
            stdout=_coerce_text(exc.stdout),
            stderr=_coerce_text(exc.stderr),
        )
        raise VerificationError(
            f"Post-install verification timed out after {verification_context.timeout_seconds} seconds.",
            result=result,
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
            detail or f"Post-install verification failed with exit code {completed.returncode}.",
            result=result,
        )
    return result


def _restore_backup(install_root: Path, backup_root: Path) -> bool:
    failed_install_root = backup_root.parent / f".ccollab-update-failed-{os.getpid()}"
    try:
        if failed_install_root.exists():
            shutil.rmtree(failed_install_root, ignore_errors=True)
        if install_root.exists():
            os.replace(install_root, failed_install_root)
        os.replace(backup_root, install_root)
        if failed_install_root.exists():
            shutil.rmtree(failed_install_root, ignore_errors=True)
        return True
    except OSError:
        return False


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
    install_renamed = False
    try:
        os.replace(install_root, backup_root)
        install_renamed = True
        os.replace(staged_root, install_root)
    except OSError as exc:
        rollback_succeeded = None
        if install_renamed:
            rollback_succeeded = _restore_backup(install_root, backup_root)
        return UpdateTransactionResult(
            ok=False,
            rollback_performed=install_renamed,
            rollback_succeeded=rollback_succeeded,
            verification=None,
            error=str(exc),
        )

    try:
        verification_result = verifier(
            install_root=install_root,
            verification_context=verification_context,
        )
    except VerificationError as exc:
        rollback_succeeded = _restore_backup(install_root, backup_root)
        return UpdateTransactionResult(
            ok=False,
            rollback_performed=True,
            rollback_succeeded=rollback_succeeded,
            verification=exc.result,
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


def _parse_verification_context(payload: object) -> VerificationContext:
    if not isinstance(payload, dict):
        return VerificationContext(os_name="windows", timeout_seconds=45)
    return VerificationContext(
        os_name=str(payload.get("os_name", "windows")),
        timeout_seconds=int(payload.get("timeout_seconds", 45)),
    )


def _cleanup_helper_paths(install_root: Path, *, intent_path: Path) -> None:
    for path in (
        intent_path,
        _handoff_record_path(install_root),
        _lock_record_path(install_root),
        _lock_path(install_root),
    ):
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        except OSError:
            continue


def _handoff_ready(install_root: Path, *, helper_pid: int) -> bool:
    handoff_path = _handoff_record_path(install_root)
    if not handoff_path.exists():
        return False
    try:
        payload = json.loads(handoff_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    return payload.get("helper_pid") == helper_pid and payload.get("transferred") is True


def _wait_for_handoff(install_root: Path, *, helper_pid: int, timeout_seconds: int = 30) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if _handoff_ready(install_root, helper_pid=helper_pid):
            return
        time.sleep(0.05)
    raise RuntimeError("Timed out waiting for Windows update handoff to become active.")


def await_transaction_result(
    result_path: Path,
    *,
    current_version: str,
    latest_version: str,
    progress_messages: list[str],
    timeout_seconds: int = 300,
) -> int:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if result_path.exists():
            result = read_transaction_result(result_path)
            if result.ok:
                print(f"Current version: {current_version}")
                print(f"Latest version: {latest_version}")
                for message in progress_messages:
                    print(message)
                print(f"Updated ccollab to {latest_version}")
                return 0

            print(f"Current version: {current_version}", file=sys.stderr)
            print(f"Latest version: {latest_version}", file=sys.stderr)
            for message in progress_messages:
                print(message, file=sys.stderr)
            print(
                f"Update failed: {result.error or 'post-install verification failed'}",
                file=sys.stderr,
            )
            if result.rollback_performed and result.rollback_succeeded:
                print("Previous installation was restored.", file=sys.stderr)
            elif result.rollback_performed and result.rollback_succeeded is False:
                print("Rollback failed; manual repair may be required.", file=sys.stderr)
            else:
                print("Existing installation was left unchanged.", file=sys.stderr)
            return 1
        time.sleep(0.05)

    print(
        f"Update failed: helper result {result_path} was not produced before timeout.",
        file=sys.stderr,
    )
    return 1


def run_helper_from_intent(intent_path: Path) -> int:
    payload = json.loads(intent_path.read_text(encoding="utf-8-sig"))
    install_root = Path(str(payload["install_root"]))
    staged_root = Path(str(payload["staged_root"]))
    backup_root = Path(str(payload["backup_root"]))
    verification_context = _parse_verification_context(payload.get("verification_context"))
    result_path_raw = payload.get("result_path")
    result_path = Path(str(result_path_raw)) if isinstance(result_path_raw, str) else None

    try:
        _wait_for_handoff(install_root, helper_pid=os.getpid())
        result = apply_update_transaction(
            install_root=install_root,
            staged_root=staged_root,
            backup_root=backup_root,
            verification_context=verification_context,
        )
        if result_path is not None:
            write_transaction_result(result_path, result)
        return 0 if result.ok else 1
    finally:
        _cleanup_helper_paths(install_root, intent_path=intent_path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m runtime.update_execution")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--swap-intent")
    mode.add_argument("--await-result")
    parser.add_argument("--current-version")
    parser.add_argument("--latest-version")
    parser.add_argument("--progress-message", action="append", default=[])
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.swap_intent:
        return run_helper_from_intent(Path(args.swap_intent))
    return await_transaction_result(
        Path(args.await_result),
        current_version=args.current_version or "unknown",
        latest_version=args.latest_version or "unknown",
        progress_messages=list(args.progress_message),
    )


if __name__ == "__main__":
    raise SystemExit(main())
