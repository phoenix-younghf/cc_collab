from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from runtime.capabilities import detect_claude_capabilities, detect_python_capability
from runtime.constants import CCOLLAB_RELEASE_REPOSITORY
from runtime.release_manifest import ReleaseManifest, parse_release_manifest, validate_release_identity
from runtime import update_execution
from runtime.versioning import (
    InstallDiscovery,
    InstallDiscoveryError,
    MultipleInstallRootsError,
    build_install_metadata,
    canonical_install_root,
    discover_install_root,
    get_active_runtime_root,
    is_valid_install_payload,
    resolve_platform_identifier,
    write_install_metadata,
)


_SEMVER_TAG_PATTERN = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
_AUTH_ERROR_MARKERS = ("gh auth login", "not logged", "authentication failed")
_REPO_ACCESS_MARKERS = ("http 404", "http 403", "not found", "resource not accessible")
_DOWNLOAD_REPO_ACCESS_MARKERS = (
    "repository not found",
    "could not resolve to a repository",
    "resource not accessible by integration",
    "access denied to repository",
)
_ASSET_LOOKUP_REPO_ACCESS_MARKERS = ("http 404", "http 403", *_DOWNLOAD_REPO_ACCESS_MARKERS)

ReleaseListRunner = Callable[[str], list[dict[str, Any]]]
ReleaseDownloadRunner = Callable[[str, int, str, int | None], bytes]


@dataclass(frozen=True)
class ResolvedGitHubRelease:
    repo: str
    tag: str
    release_id: int
    published_at: str


class UpdaterError(RuntimeError):
    """Base class for updater failures."""


class GhPrerequisiteError(UpdaterError):
    """Raised when GitHub CLI is unavailable."""


class GhAuthenticationError(UpdaterError):
    """Raised when GitHub CLI needs authentication."""


class RepoAccessError(UpdaterError):
    """Raised when the repo cannot be read through GitHub CLI."""


class ReleaseLookupError(UpdaterError):
    """Raised when no usable stable release can be resolved."""


class DownloadError(UpdaterError):
    """Raised when a release asset download fails."""


class ReleaseIdentityError(UpdaterError):
    """Raised when manifest and release identity do not match."""


class UpdateLockedError(UpdaterError):
    """Raised when another update process currently owns the update lock."""


class ChecksumMismatchError(UpdaterError):
    """Raised when a downloaded release archive does not match the expected checksum."""


class SizeMismatchError(UpdaterError):
    """Raised when a downloaded release archive does not match the expected size."""


class InvalidArchiveError(UpdaterError):
    """Raised when the downloaded release archive cannot be extracted safely."""


class CompatibilityError(UpdaterError):
    """Raised when local runtime dependencies are incompatible with the staged release."""


class InvalidPayloadError(UpdaterError):
    """Raised when a staged release payload is missing required install structure."""


class BrokenLauncherError(UpdaterError):
    """Raised when the installed launcher is missing or unhealthy."""


class UpdateExecutionError(UpdaterError):
    """Raised when an in-flight update fails after version context is known."""

    def __init__(
        self,
        message: str,
        *,
        current_version: str,
        latest_version: str,
        progress_messages: tuple[str, ...] = (),
        rollback_succeeded: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.current_version = current_version
        self.latest_version = latest_version
        self.progress_messages = progress_messages
        self.rollback_succeeded = rollback_succeeded


@dataclass(frozen=True)
class UpdatePlan:
    install_root: Path
    current_version: str
    latest_version: str
    already_up_to_date: bool


@dataclass(frozen=True)
class UpdateResult:
    status: str
    current_version: str
    latest_version: str
    progress_messages: tuple[str, ...] = ()
    verification_stdout: str = ""
    verification_stderr: str = ""

    @classmethod
    def success(
        cls,
        *,
        current_version: str,
        latest_version: str,
        progress_messages: tuple[str, ...] = (),
        verification_stdout: str = "",
        verification_stderr: str = "",
    ) -> "UpdateResult":
        return cls(
            status="success",
            current_version=current_version,
            latest_version=latest_version,
            progress_messages=progress_messages,
            verification_stdout=verification_stdout,
            verification_stderr=verification_stderr,
        )

    @classmethod
    def noop(
        cls,
        *,
        current_version: str,
        latest_version: str,
        progress_messages: tuple[str, ...] = (),
    ) -> "UpdateResult":
        return cls(
            status="noop",
            current_version=current_version,
            latest_version=latest_version,
            progress_messages=progress_messages,
        )


# Re-export execution dataclasses to keep updater as the public facade.
VerificationContext = update_execution.VerificationContext
VerificationResult = update_execution.VerificationResult
VerificationError = update_execution.VerificationError
WindowsSwapPlan = update_execution.WindowsSwapPlan
UpdateTransactionResult = update_execution.UpdateTransactionResult


@dataclass(frozen=True)
class UpdateWorkArea:
    staging_root: Path
    backup_root: Path


@dataclass(frozen=True)
class UpdateLockRecord:
    pid: int
    hostname: str
    install_root: str
    acquired_at: str


@dataclass(frozen=True)
class UpdateHandoffRecord:
    owner_pid: int
    helper_pid: int
    install_root: str
    transferred_at: str
    transferred: bool


@dataclass
class UpdateLock:
    install_root: Path
    lock_path: Path
    record_path: Path
    handoff_path: Path
    owner_pid: int
    released: bool = False

    def release(self) -> None:
        if self.released:
            return
        self.released = True
        if lock_handoff_active(self.install_root):
            return
        for path in (self.handoff_path, self.record_path, self.lock_path):
            try:
                path.unlink()
            except FileNotFoundError:
                continue
            except OSError:
                continue


def _canonical_install_root(install_root: Path) -> Path:
    return canonical_install_root(install_root)


def _lock_path(install_root: Path) -> Path:
    return install_root.parent / ".ccollab-update.lock"


def _lock_record_path(install_root: Path) -> Path:
    return install_root.parent / ".ccollab-update.lock.json"


def _handoff_record_path(install_root: Path) -> Path:
    return install_root.parent / ".ccollab-update.handoff.json"


def _timestamp_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_lock_record(record_path: Path) -> UpdateLockRecord | None:
    if not record_path.exists():
        return None
    try:
        payload = json.loads(record_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    pid = payload.get("pid")
    hostname = payload.get("hostname")
    install_root = payload.get("install_root")
    acquired_at = payload.get("acquired_at")
    if (
        not isinstance(pid, int)
        or isinstance(pid, bool)
        or not isinstance(hostname, str)
        or not isinstance(install_root, str)
        or not isinstance(acquired_at, str)
    ):
        return None
    return UpdateLockRecord(
        pid=pid,
        hostname=hostname,
        install_root=install_root,
        acquired_at=acquired_at,
    )


def _write_lock_record(record_path: Path, record: UpdateLockRecord) -> None:
    record_path.write_text(
        json.dumps(
            {
                "pid": record.pid,
                "hostname": record.hostname,
                "install_root": record.install_root,
                "acquired_at": record.acquired_at,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _pid_is_alive(pid: int) -> bool:
    if pid < 1:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _lock_handoff_active_at_path(handoff_path: Path) -> bool:
    if not handoff_path.exists():
        return False
    try:
        payload = json.loads(handoff_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return True
    if not isinstance(payload, dict):
        return True
    helper_pid = payload.get("helper_pid")
    transferred = payload.get("transferred")
    if isinstance(helper_pid, int) and helper_pid > 0 and transferred is True:
        return _pid_is_alive(helper_pid)
    return True


def _read_handoff_record(handoff_path: Path) -> UpdateHandoffRecord | None:
    if not handoff_path.exists():
        return None
    try:
        payload = json.loads(handoff_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    owner_pid = payload.get("owner_pid")
    helper_pid = payload.get("helper_pid")
    install_root = payload.get("install_root")
    transferred_at = payload.get("transferred_at")
    transferred = payload.get("transferred")
    if (
        not isinstance(owner_pid, int)
        or isinstance(owner_pid, bool)
        or not isinstance(helper_pid, int)
        or isinstance(helper_pid, bool)
        or not isinstance(install_root, str)
        or not isinstance(transferred_at, str)
        or not isinstance(transferred, bool)
    ):
        return None
    return UpdateHandoffRecord(
        owner_pid=owner_pid,
        helper_pid=helper_pid,
        install_root=install_root,
        transferred_at=transferred_at,
        transferred=transferred,
    )


def _write_handoff_record(handoff_path: Path, record: UpdateHandoffRecord) -> None:
    handoff_path.write_text(
        json.dumps(
            {
                "owner_pid": record.owner_pid,
                "helper_pid": record.helper_pid,
                "install_root": record.install_root,
                "transferred_at": record.transferred_at,
                "transferred": record.transferred,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _claim_lock(
    *,
    install_root: Path,
    lock_path: Path,
    record_path: Path,
    handoff_path: Path,
    pid: int,
    hostname: str,
) -> UpdateLock:
    descriptor = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, f"{pid}\n".encode("utf-8"))
    finally:
        os.close(descriptor)

    try:
        _write_lock_record(
            record_path,
            UpdateLockRecord(
                pid=pid,
                hostname=hostname,
                install_root=str(install_root),
                acquired_at=_timestamp_utc(),
            ),
        )
    except Exception:
        for stale_path in (record_path, lock_path):
            try:
                stale_path.unlink()
            except FileNotFoundError:
                continue
            except OSError:
                continue
        raise
    return UpdateLock(
        install_root=install_root,
        lock_path=lock_path,
        record_path=record_path,
        handoff_path=handoff_path,
        owner_pid=pid,
    )


def recover_or_acquire_lock(
    install_root: Path,
    *,
    current_pid: int | None = None,
    hostname: str | None = None,
) -> UpdateLock:
    canonical_install_root = _canonical_install_root(install_root)
    lock_path = _lock_path(canonical_install_root)
    record_path = _lock_record_path(canonical_install_root)
    handoff_path = _handoff_record_path(canonical_install_root)
    owner_pid = os.getpid() if current_pid is None else current_pid
    owner_hostname = socket.gethostname() if hostname is None else hostname

    attempts = 0
    while True:
        attempts += 1
        try:
            return _claim_lock(
                install_root=canonical_install_root,
                lock_path=lock_path,
                record_path=record_path,
                handoff_path=handoff_path,
                pid=owner_pid,
                hostname=owner_hostname,
            )
        except FileExistsError:
            if _lock_handoff_active_at_path(handoff_path):
                raise UpdateLockedError(
                    f"Update lock for {canonical_install_root} is currently owned by a helper handoff."
                )
            record = _read_lock_record(record_path)
            if record is None:
                raise UpdateLockedError(
                    f"Update lock for {canonical_install_root} has no readable owner metadata; "
                    "cannot prove the existing owner is stale."
                )
            if _pid_is_alive(record.pid):
                raise UpdateLockedError(
                    f"Another updater instance is active for {canonical_install_root} "
                    f"(pid={record.pid}, host={record.hostname})."
                )
            if attempts >= 2:
                raise UpdateLockedError(
                    f"Unable to recover stale update lock for {canonical_install_root}."
                )
            for stale_path in (lock_path, record_path):
                try:
                    stale_path.unlink()
                except FileNotFoundError:
                    continue
                except OSError:
                    raise UpdateLockedError(
                        f"Unable to recover stale update lock for {canonical_install_root}."
                    ) from None


def acquire_update_lock(
    install_root: Path,
    *,
    pid: int | None = None,
    hostname: str | None = None,
) -> UpdateLock:
    return recover_or_acquire_lock(
        install_root,
        current_pid=pid,
        hostname=hostname,
    )


def read_update_lock_record(install_root: Path) -> UpdateLockRecord:
    canonical_install_root = _canonical_install_root(install_root)
    record = _read_lock_record(_lock_record_path(canonical_install_root))
    if record is None:
        raise UpdateLockedError(f"No update lock metadata exists for {canonical_install_root}.")
    return record


def begin_windows_handoff(
    install_root: Path,
    *,
    owner_pid: int,
    helper_pid: int,
) -> UpdateHandoffRecord:
    canonical_install_root = _canonical_install_root(install_root)
    lock_path = _lock_path(canonical_install_root)
    record_path = _lock_record_path(canonical_install_root)
    handoff_path = _handoff_record_path(canonical_install_root)
    if not lock_path.exists():
        raise UpdateLockedError(f"No active update lock exists for {canonical_install_root}.")
    lock_record = _read_lock_record(record_path)
    if lock_record is not None and lock_record.pid != owner_pid:
        raise UpdateLockedError(
            f"Update lock owner mismatch for {canonical_install_root}: expected pid {owner_pid}, "
            f"found pid {lock_record.pid}."
        )
    handoff_record = UpdateHandoffRecord(
        owner_pid=owner_pid,
        helper_pid=helper_pid,
        install_root=str(canonical_install_root),
        transferred_at=_timestamp_utc(),
        transferred=True,
    )
    _write_handoff_record(handoff_path, handoff_record)
    return handoff_record


def lock_handoff_active(install_root: Path) -> bool:
    canonical_install_root = _canonical_install_root(install_root)
    return _lock_handoff_active_at_path(_handoff_record_path(canonical_install_root))


def same_filesystem(path_a: Path, path_b: Path) -> bool:
    try:
        return path_a.stat().st_dev == path_b.stat().st_dev
    except OSError:
        return False


def create_update_work_area(install_root: Path) -> UpdateWorkArea:
    canonical_install_root = _canonical_install_root(install_root)
    parent_root = canonical_install_root.parent
    if not same_filesystem(canonical_install_root, parent_root):
        raise RuntimeError("Update swap requires install root and parent directory to share one volume.")
    staging_root = Path(
        tempfile.mkdtemp(
            prefix=".ccollab-update-staging-",
            dir=parent_root,
        )
    )
    backup_root = Path(
        tempfile.mkdtemp(
            prefix=".ccollab-update-backup-",
            dir=parent_root,
        )
    )
    for candidate in (staging_root, backup_root):
        if not same_filesystem(canonical_install_root, candidate):
            shutil.rmtree(staging_root, ignore_errors=True)
            shutil.rmtree(backup_root, ignore_errors=True)
            raise RuntimeError("Update staging must remain on the same filesystem as install root.")
    return UpdateWorkArea(
        staging_root=staging_root,
        backup_root=backup_root,
    )


def verify_downloaded_archive(
    *,
    archive_path: Path,
    expected_sha256: str,
    expected_size: int,
) -> None:
    actual_size = archive_path.stat().st_size
    if actual_size != expected_size:
        raise SizeMismatchError(
            f"Downloaded archive size mismatch: expected {expected_size}, got {actual_size}."
        )
    digest = hashlib.sha256()
    with archive_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    actual_sha256 = digest.hexdigest()
    if actual_sha256 != expected_sha256:
        raise ChecksumMismatchError(
            f"Downloaded archive checksum mismatch: expected {expected_sha256}, got {actual_sha256}."
        )


def stage_release_manifest(
    *,
    install_root: Path,
    downloader: Callable[..., dict[str, Any]] | None = None,
    repo: str = CCOLLAB_RELEASE_REPOSITORY,
    release_id: int | None = None,
    asset_name: str = "ccollab-manifest.json",
    work_area: UpdateWorkArea | None = None,
) -> tuple[dict[str, Any], Path]:
    selected_downloader = download_release_manifest if downloader is None else downloader
    if release_id is None and selected_downloader is download_release_manifest:
        raise ValueError("release_id is required when downloading a release manifest")
    area = create_update_work_area(install_root) if work_area is None else work_area
    kwargs: dict[str, Any] = {"repo": repo, "asset_name": asset_name}
    if release_id is not None:
        kwargs["release_id"] = release_id
    payload = selected_downloader(**kwargs)
    manifest_path = area.staging_root / asset_name
    manifest_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload, manifest_path


def stage_release_asset(
    *,
    install_root: Path,
    downloader: Callable[..., bytes] | None = None,
    repo: str = CCOLLAB_RELEASE_REPOSITORY,
    release_id: int | None = None,
    asset_id: int | None = None,
    asset_name: str = "payload.bin",
    work_area: UpdateWorkArea | None = None,
) -> Path:
    selected_downloader = download_release_asset if downloader is None else downloader
    if selected_downloader is download_release_asset and (release_id is None or asset_id is None):
        raise ValueError("release_id and asset_id are required when downloading a release asset")
    area = create_update_work_area(install_root) if work_area is None else work_area
    kwargs: dict[str, Any] = {"repo": repo, "asset_name": asset_name}
    if release_id is not None:
        kwargs["release_id"] = release_id
    if asset_id is not None:
        kwargs["asset_id"] = asset_id
    payload = selected_downloader(**kwargs)
    archive_path = area.staging_root / asset_name
    archive_path.write_bytes(payload)
    return archive_path


def extract_release_archive(archive_path: Path, stage_root: Path) -> None:
    def _validate_member_path(base_root: Path, member_name: str) -> None:
        normalized = member_name.replace("\\", "/")
        if normalized.startswith("/") or (len(normalized) >= 2 and normalized[1] == ":"):
            raise InvalidArchiveError(f"Archive member path escapes stage root: {member_name!r}")
        destination = (base_root / normalized).resolve()
        try:
            destination.relative_to(base_root)
        except ValueError as exc:
            raise InvalidArchiveError(
                f"Archive member path escapes stage root: {member_name!r}"
            ) from exc

    try:
        if stage_root.exists():
            shutil.rmtree(stage_root)
        stage_root.mkdir(parents=True, exist_ok=True)
        resolved_stage_root = stage_root.resolve()
        suffixes = tuple(archive_path.suffixes)
        if archive_path.suffix == ".zip":
            with zipfile.ZipFile(archive_path) as archive:
                for member_name in archive.namelist():
                    _validate_member_path(resolved_stage_root, member_name)
                archive.extractall(stage_root)
            return
        if suffixes[-2:] in {(".tar", ".gz")} or suffixes[-1:] in {(".tgz",)}:
            with tarfile.open(archive_path, mode="r:gz") as archive:
                for member in archive.getmembers():
                    _validate_member_path(resolved_stage_root, member.name)
                    if member.issym() or member.islnk():
                        raise InvalidArchiveError(
                            f"Tar archive member uses unsupported link type: {member.name!r}"
                        )
                archive.extractall(stage_root)
            return
        raise InvalidArchiveError(f"Unsupported archive format: {archive_path.name}")
    except (OSError, zipfile.BadZipFile, tarfile.TarError) as exc:
        raise InvalidArchiveError(f"Failed to extract archive: {archive_path}") from exc


def _parse_python_minimum(value: str) -> tuple[int, int, int]:
    parts = value.split(".")
    if len(parts) == 2:
        parts.append("0")
    if len(parts) != 3 or any(not piece.isdigit() for piece in parts):
        raise CompatibilityError(f"Manifest python_min value is invalid: {value!r}")
    return int(parts[0]), int(parts[1]), int(parts[2])


def _parse_python_version(value: str) -> tuple[int, int, int]:
    parts = value.strip().split(".")
    if len(parts) != 3 or any(not piece.isdigit() for piece in parts):
        raise CompatibilityError(f"Python version output is invalid: {value!r}")
    return int(parts[0]), int(parts[1]), int(parts[2])


def python_version_tuple(launcher: str) -> tuple[int, int, int]:
    result = subprocess.run(
        [
            launcher,
            "-c",
            "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}.{sys.version_info[2]}')",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise CompatibilityError(
            detail or f"Unable to read Python version from launcher {launcher!r}."
        )
    return _parse_python_version(result.stdout)


def run_compatibility_preflight(
    manifest: ReleaseManifest,
    *,
    os_name: str | None = None,
    command_exists: Callable[[str], bool] | None = None,
    flag_probe: Callable[[str], bool] | None = None,
) -> None:
    python_capability = detect_python_capability(
        os_name=os_name,
        command_exists=command_exists,
    )
    if not python_capability.available or not python_capability.launcher:
        raise CompatibilityError(
            python_capability.remediation
            or "Install Python 3.9 or newer and rerun ccollab update."
        )
    installed_python = python_version_tuple(python_capability.launcher)
    required_python = _parse_python_minimum(manifest.compatibility.python_min)
    if installed_python < required_python:
        installed_str = ".".join(str(piece) for piece in installed_python)
        required_str = ".".join(str(piece) for piece in required_python)
        raise CompatibilityError(
            f"Python {installed_str} does not satisfy manifest minimum {required_str}."
        )

    claude_capability = detect_claude_capabilities(
        command_exists=command_exists,
        flag_probe=flag_probe,
    )
    if not claude_capability.available:
        raise CompatibilityError(
            claude_capability.remediation or "Install Claude CLI before updating ccollab."
        )
    missing_required_flags = [
        flag
        for flag in manifest.compatibility.claude_required_flags
        if flag in claude_capability.missing_flags
    ]
    if missing_required_flags:
        remediation = claude_capability.remediation or (
            "Upgrade Claude CLI so it supports: " + ", ".join(missing_required_flags)
        )
        raise CompatibilityError(
            f"Claude CLI is missing required flags: {', '.join(missing_required_flags)}. {remediation}"
        )


def write_staged_install_metadata(
    *,
    staged_install_root: Path,
    manifest: ReleaseManifest,
    asset_name: str,
    asset_sha256: str,
    installed_at: str | None = None,
) -> None:
    metadata = build_install_metadata(
        staged_install_root,
        version=manifest.version,
        channel=manifest.channel,
        repo=manifest.repo,
        asset_name=asset_name,
        asset_sha256=asset_sha256,
        installed_at=installed_at,
    )
    write_install_metadata(staged_install_root, metadata)


def validate_staged_payload(staged_install_root: Path) -> None:
    required_directories = ("bin", "runtime", "skill", "install", "examples")
    required_files = ("README.md", "AGENTS.md")
    missing_required_paths: list[str] = []
    for required_name in required_directories:
        if not (staged_install_root / required_name).is_dir():
            missing_required_paths.append(required_name)
    for required_name in required_files:
        if not (staged_install_root / required_name).is_file():
            missing_required_paths.append(required_name)
    if missing_required_paths:
        raise InvalidPayloadError(
            "Staged payload is missing required entries: "
            + ", ".join(missing_required_paths)
        )
    if not is_valid_install_payload(staged_install_root):
        raise InvalidPayloadError(
            f"Staged payload at {staged_install_root} is not a valid ccollab install payload."
        )


def _coerce_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _command_text(exc: subprocess.CalledProcessError) -> str:
    pieces = [_coerce_text(exc.stderr), _coerce_text(exc.stdout)]
    return "\n".join(piece for piece in pieces if piece).strip()


def _default_release_list_runner(repo: str) -> list[dict[str, Any]]:
    result = subprocess.run(
        [
            "gh",
            "api",
            "--paginate",
            "--slurp",
            f"repos/{repo}/releases?per_page=100",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode,
            result.args,
            output=result.stdout,
            stderr=result.stderr,
        )
    payload = json.loads(result.stdout or "[]")
    if not isinstance(payload, list):
        raise ReleaseLookupError("gh release list returned an unexpected payload")
    items: list[dict[str, Any]] = []
    for page in payload:
        if not isinstance(page, list):
            raise ReleaseLookupError("gh release list returned an unexpected paginated payload")
        for item in page:
            if isinstance(item, dict):
                items.append(_normalize_release_payload(item))
    return items


def _run_gh_bytes(args: list[str]) -> bytes:
    result = subprocess.run(
        ["gh", *args],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode,
            result.args,
            output=result.stdout,
            stderr=(result.stderr or b"").decode("utf-8", errors="replace"),
        )
    return result.stdout


def _run_gh_json(args: list[str]) -> Any:
    raw_payload = _run_gh_bytes(args)
    return json.loads(raw_payload.decode("utf-8"))


def _normalize_release_payload(item: dict[str, Any]) -> dict[str, Any]:
    if "tagName" in item:
        return item
    normalized = dict(item)
    if "tag_name" in item:
        normalized["tagName"] = item.get("tag_name")
    if "draft" in item:
        normalized["isDraft"] = item.get("draft")
    if "prerelease" in item:
        normalized["isPrerelease"] = item.get("prerelease")
    if "published_at" in item:
        normalized["publishedAt"] = item.get("published_at")
    if "id" in item and "databaseId" not in item:
        normalized["databaseId"] = item.get("id")
    return normalized


def _resolve_named_asset_id(repo: str, release_id: int, asset_name: str) -> int:
    try:
        payload = _run_gh_json(["api", f"repos/{repo}/releases/{release_id}/assets"])
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise _translate_release_asset_lookup_error(repo, exc) from exc
    if not isinstance(payload, list):
        raise DownloadError(f"release {release_id} assets payload for {repo} was invalid")
    for item in payload:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        asset_id = item.get("id")
        if name == asset_name and isinstance(asset_id, int) and not isinstance(asset_id, bool):
            return asset_id
    raise DownloadError(f"release {release_id} does not contain asset {asset_name!r}")


def _validate_bound_asset(repo: str, release_id: int, asset_id: int, asset_name: str) -> None:
    try:
        payload = _run_gh_json(["api", f"repos/{repo}/releases/{release_id}/assets"])
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise _translate_release_asset_lookup_error(repo, exc) from exc
    if not isinstance(payload, list):
        raise DownloadError(f"release {release_id} assets payload for {repo} was invalid")
    for item in payload:
        if not isinstance(item, dict):
            continue
        if item.get("name") == asset_name and item.get("id") == asset_id:
            return
    raise DownloadError(
        f"release {release_id} does not contain asset {asset_name!r} with asset_id {asset_id}"
    )


def _default_release_download_runner(
    repo: str,
    release_id: int,
    asset_name: str,
    asset_id: int | None = None,
) -> bytes:
    bound_asset_id = asset_id
    if bound_asset_id is None:
        bound_asset_id = _resolve_named_asset_id(repo, release_id, asset_name)
    else:
        _validate_bound_asset(repo, release_id, bound_asset_id, asset_name)
    return _run_gh_bytes(
        [
            "api",
            "-H",
            "Accept: application/octet-stream",
            f"repos/{repo}/releases/assets/{bound_asset_id}",
        ]
    )


def _parse_semver(tag: str) -> tuple[int, int, int] | None:
    match = _SEMVER_TAG_PATTERN.match(tag)
    if match is None:
        return None
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch)


def _release_sort_key(item: dict[str, Any]) -> tuple[int, int, int]:
    tag_name = item["tagName"]
    version = _parse_semver(tag_name)
    if version is None:
        raise ReleaseLookupError(f"release tag {tag_name!r} is not a stable semantic version")
    return version


def _called_process_markers(exc: subprocess.CalledProcessError) -> str:
    return _command_text(exc).lower()


def _translate_release_resolution_error(
    repo: str,
    exc: FileNotFoundError | subprocess.CalledProcessError,
) -> UpdaterError:
    if isinstance(exc, FileNotFoundError):
        return GhPrerequisiteError("Install GitHub CLI and run 'gh auth login'.")
    markers = _called_process_markers(exc)
    if any(marker in markers for marker in _AUTH_ERROR_MARKERS):
        return GhAuthenticationError("Run 'gh auth login' for github.com, then retry.")
    if any(marker in markers for marker in _REPO_ACCESS_MARKERS):
        return RepoAccessError(f"Authenticated GitHub CLI could not access {repo} releases.")
    detail = _command_text(exc)
    if detail:
        return ReleaseLookupError(detail)
    return ReleaseLookupError(f"Unable to resolve releases for {repo}.")


def _translate_release_download_error(
    repo: str,
    exc: FileNotFoundError | subprocess.CalledProcessError,
) -> UpdaterError:
    if isinstance(exc, FileNotFoundError):
        return GhPrerequisiteError("Install GitHub CLI and run 'gh auth login'.")
    markers = _called_process_markers(exc)
    if any(marker in markers for marker in _AUTH_ERROR_MARKERS):
        return GhAuthenticationError("Run 'gh auth login' for github.com, then retry.")
    if any(marker in markers for marker in _DOWNLOAD_REPO_ACCESS_MARKERS):
        return RepoAccessError(f"Authenticated GitHub CLI could not access {repo} releases.")
    detail = _command_text(exc) if isinstance(exc, subprocess.CalledProcessError) else str(exc)
    return DownloadError(detail or f"Failed to download release asset from {repo}")


def _translate_release_asset_lookup_error(
    repo: str,
    exc: FileNotFoundError | subprocess.CalledProcessError,
) -> UpdaterError:
    if isinstance(exc, FileNotFoundError):
        return GhPrerequisiteError("Install GitHub CLI and run 'gh auth login'.")
    markers = _called_process_markers(exc)
    if any(marker in markers for marker in _AUTH_ERROR_MARKERS):
        return GhAuthenticationError("Run 'gh auth login' for github.com, then retry.")
    if any(marker in markers for marker in _ASSET_LOOKUP_REPO_ACCESS_MARKERS):
        return RepoAccessError(f"Authenticated GitHub CLI could not access {repo} releases.")
    detail = _command_text(exc)
    return DownloadError(detail or f"Failed to resolve release assets from {repo}")


def _release_id(payload: dict[str, Any]) -> int:
    raw_id = payload.get("databaseId", payload.get("id"))
    if not isinstance(raw_id, int) or isinstance(raw_id, bool) or raw_id < 1:
        raise ReleaseLookupError("gh release payload is missing a usable release id")
    return raw_id


def resolve_latest_stable_release(
    repo: str = CCOLLAB_RELEASE_REPOSITORY,
    runner: ReleaseListRunner | None = None,
) -> ResolvedGitHubRelease:
    selected_runner = _default_release_list_runner if runner is None else runner
    try:
        payload = selected_runner(repo)
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise _translate_release_resolution_error(repo, exc) from exc

    stable_releases = [
        item
        for item in payload
        if isinstance(item, dict)
        and not bool(item.get("isDraft"))
        and not bool(item.get("isPrerelease"))
        and isinstance(item.get("tagName"), str)
        and _parse_semver(item["tagName"]) is not None
    ]
    if not stable_releases:
        raise ReleaseLookupError(f"No stable releases were found for {repo}.")

    selected = max(stable_releases, key=_release_sort_key)
    return ResolvedGitHubRelease(
        repo=repo,
        tag=selected["tagName"],
        release_id=_release_id(selected),
        published_at=str(selected.get("publishedAt", "")),
    )


def download_release_manifest(
    *,
    repo: str = CCOLLAB_RELEASE_REPOSITORY,
    release_id: int,
    asset_name: str,
    runner: ReleaseDownloadRunner | None = None,
) -> dict[str, Any]:
    selected_runner = _default_release_download_runner if runner is None else runner
    try:
        payload = selected_runner(repo, release_id, asset_name, None)
    except (FileNotFoundError, subprocess.CalledProcessError, DownloadError) as exc:
        if isinstance(exc, DownloadError):
            raise
        raise _translate_release_download_error(repo, exc) from exc

    try:
        manifest_payload = json.loads(payload.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DownloadError(f"Downloaded manifest asset {asset_name} was not valid JSON") from exc
    if not isinstance(manifest_payload, dict):
        raise DownloadError(f"Downloaded manifest asset {asset_name} was not a JSON object")
    return manifest_payload


def download_release_asset(
    *,
    repo: str = CCOLLAB_RELEASE_REPOSITORY,
    release_id: int,
    asset_id: int,
    asset_name: str,
    runner: ReleaseDownloadRunner | None = None,
) -> bytes:
    selected_runner = _default_release_download_runner if runner is None else runner
    try:
        return selected_runner(repo, release_id, asset_name, asset_id)
    except (FileNotFoundError, subprocess.CalledProcessError, DownloadError) as exc:
        if isinstance(exc, DownloadError):
            raise
        raise _translate_release_download_error(repo, exc) from exc


def _validate_release_binding(
    release: ResolvedGitHubRelease,
    manifest: ReleaseManifest,
) -> None:
    try:
        validate_release_identity(
            manifest,
            repo=release.repo,
            tag=release.tag,
            release_id=release.release_id,
            expected_channel="stable",
        )
    except ValueError as exc:
        raise ReleaseIdentityError(str(exc).replace("manifest", "Manifest", 1)) from exc


def download_platform_asset(
    release: ResolvedGitHubRelease,
    manifest: ReleaseManifest,
    platform: str,
    *,
    runner: ReleaseDownloadRunner | None = None,
) -> bytes:
    _validate_release_binding(release, manifest)
    asset = manifest.asset_for(platform)
    return download_release_asset(
        repo=release.repo,
        release_id=release.release_id,
        asset_id=asset.asset_id,
        asset_name=asset.name,
        runner=runner,
    )


def current_working_directory() -> Path:
    return update_execution.current_working_directory()


def prepare_windows_swap(
    *,
    install_root: Path,
    staged_root: Path,
    backup_root: Path,
    helper_executable: Path,
    verification_context: VerificationContext | None = None,
    result_path: Path | None = None,
) -> WindowsSwapPlan:
    return update_execution.prepare_windows_swap(
        install_root=install_root,
        staged_root=staged_root,
        backup_root=backup_root,
        helper_executable=helper_executable,
        current_workdir=current_working_directory(),
        verification_context=verification_context,
        result_path=result_path,
    )


def build_windows_verification_command(install_root: Path) -> list[str]:
    return update_execution.build_windows_verification_command(install_root)


def run_post_install_verification(
    *,
    install_root: Path,
    verification_context: VerificationContext,
    env: dict[str, str] | None = None,
) -> VerificationResult:
    return update_execution.run_post_install_verification(
        install_root=install_root,
        verification_context=verification_context,
        env=env,
    )


def apply_update_transaction(
    *,
    install_root: Path,
    staged_root: Path,
    backup_root: Path,
    verification_context: VerificationContext,
    verification_runner: Callable[..., VerificationResult] | None = None,
) -> UpdateTransactionResult:
    runner = run_post_install_verification if verification_runner is None else verification_runner
    return update_execution.apply_update_transaction(
        install_root=install_root,
        staged_root=staged_root,
        backup_root=backup_root,
        verification_context=verification_context,
        verification_runner=runner,
    )


def _update_log_path(install_root: Path) -> Path:
    return install_root.parent / ".ccollab-update.log"


def _append_update_log(
    *,
    install_root: Path,
    current_version: str,
    latest_version: str,
    progress_messages: list[str],
    transaction: UpdateTransactionResult,
) -> None:
    log_path = _update_log_path(install_root)
    lines = [
        f"[{_timestamp_utc()}] ccollab update",
        f"Current version: {current_version}",
        f"Latest version: {latest_version}",
    ]
    lines.extend(progress_messages)
    if transaction.verification is not None:
        command_text = " ".join(transaction.verification.command)
        lines.extend(
            [
                f"Verification command: {command_text}",
                f"Verification exit code: {transaction.verification.exit_code}",
                "Verification stdout:",
                transaction.verification.stdout,
                "Verification stderr:",
                transaction.verification.stderr,
            ]
        )
    if transaction.error:
        lines.append(f"Update error: {transaction.error}")
    if transaction.rollback_performed:
        lines.append(f"Rollback succeeded: {transaction.rollback_succeeded}")
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _run_windows_helper_transaction(
    *,
    install_root: Path,
    staged_root: Path,
    backup_root: Path,
    verification_context: VerificationContext,
    current_version: str | None = None,
    latest_version: str | None = None,
    progress_messages: list[str] | None = None,
    allow_exec: bool = False,
) -> UpdateTransactionResult:
    helper_root = Path(
        tempfile.mkdtemp(
            prefix=".ccollab-update-helper-",
            dir=install_root.parent,
        )
    )
    helper_path = helper_root / "update_execution_helper.py"
    result_path = helper_root / "transaction-result.json"
    try:
        plan = prepare_windows_swap(
            install_root=install_root,
            staged_root=staged_root,
            backup_root=backup_root,
            helper_executable=helper_path,
            verification_context=verification_context,
            result_path=result_path,
        )
        if not plan.requires_helper:
            return apply_update_transaction(
                install_root=install_root,
                staged_root=staged_root,
                backup_root=backup_root,
                verification_context=verification_context,
            )

        shutil.copy2(Path(update_execution.__file__).resolve(), helper_path)
        os.chdir(plan.working_directory)
        try:
            process = subprocess.Popen(
                list(plan.helper_command or ()),
                cwd=str(plan.working_directory),
            )
        except OSError as exc:
            return UpdateTransactionResult(
                ok=False,
                rollback_performed=False,
                rollback_succeeded=None,
                verification=None,
                error=str(exc),
            )
        begin_windows_handoff(install_root, owner_pid=os.getpid(), helper_pid=process.pid)
        if allow_exec:
            waiter_command = [
                sys.executable,
                str(helper_path),
                "--await-result",
                str(result_path),
                "--current-version",
                current_version or "unknown",
                "--latest-version",
                latest_version or "unknown",
            ]
            for message in progress_messages or []:
                waiter_command.extend(["--progress-message", message])
            os.execv(sys.executable, waiter_command)
        return_code = process.wait()
        if not result_path.exists():
            return UpdateTransactionResult(
                ok=False,
                rollback_performed=False,
                rollback_succeeded=None,
                verification=None,
                error=(
                    "Windows helper did not produce a transaction result "
                    f"(exit code {return_code})."
                ),
            )
        return update_execution.read_transaction_result(result_path)
    finally:
        _safe_rmtree(helper_root)


def _launcher_path_for_install(install_root: Path, *, os_name: str) -> Path:
    if os_name == "nt":
        return install_root / "bin" / "ccollab.cmd"
    return install_root / "bin" / "ccollab"


def ensure_healthy_launcher(install_root: Path, *, os_name: str) -> None:
    launcher = _launcher_path_for_install(install_root, os_name=os_name)
    if not launcher.is_file():
        raise BrokenLauncherError(
            "Launcher is missing or unhealthy. Reinstall ccollab to repair the launcher, then retry."
        )
    command = [str(launcher), "--help"]
    if os_name == "nt":
        command = ["cmd", "/c", str(launcher), "--help"]
    try:
        result = subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise BrokenLauncherError(
            "Launcher is missing or unhealthy. Reinstall ccollab to repair the launcher, then retry."
        ) from exc
    if result.returncode != 0:
        raise BrokenLauncherError(
            "Launcher is missing or unhealthy. Reinstall ccollab to repair the launcher, then retry."
        )


def plan_update_for_install(
    *,
    install_discovery: InstallDiscovery,
    target_manifest: ReleaseManifest,
) -> UpdatePlan:
    current_version = install_discovery.version or "unknown"
    latest_version = target_manifest.version
    already_up_to_date = (
        current_version != "unknown"
        and current_version == latest_version
    )
    return UpdatePlan(
        install_root=canonical_install_root(install_discovery.install_root),
        current_version=current_version,
        latest_version=latest_version,
        already_up_to_date=already_up_to_date,
    )


def _resolved_staged_install_root(extracted_root: Path) -> Path:
    if is_valid_install_payload(extracted_root):
        return extracted_root
    children = [entry for entry in extracted_root.iterdir() if entry.is_dir()]
    if len(children) == 1 and is_valid_install_payload(children[0]):
        return children[0]
    return extracted_root


def _safe_rmtree(path: Path) -> None:
    try:
        shutil.rmtree(path, ignore_errors=True)
    except OSError:
        pass


def _raise_update_execution_error(
    *,
    message: str,
    plan: UpdatePlan,
    progress_messages: list[str],
    rollback_succeeded: bool | None = None,
) -> None:
    raise UpdateExecutionError(
        message,
        current_version=plan.current_version,
        latest_version=plan.latest_version,
        progress_messages=tuple(progress_messages),
        rollback_succeeded=rollback_succeeded,
    )


def run_update(
    *,
    env: dict[str, str] | None = None,
    os_name: str | None = None,
    repo: str = CCOLLAB_RELEASE_REPOSITORY,
) -> UpdateResult:
    runtime_env = dict(os.environ if env is None else env)
    runtime_os = os_name or os.name
    active_runtime_root = get_active_runtime_root(__file__)
    discovery = discover_install_root(
        active_runtime_root=active_runtime_root,
        env=runtime_env,
        os_name=runtime_os,
        reject_conflicting_roots=True,
    )
    install_root = canonical_install_root(discovery.install_root)
    ensure_healthy_launcher(install_root, os_name=runtime_os)

    lock = acquire_update_lock(install_root)
    work_area: UpdateWorkArea | None = None
    plan: UpdatePlan | None = None
    progress_messages: list[str] = []
    try:
        release = resolve_latest_stable_release(repo)
        manifest_payload = download_release_manifest(
            repo=release.repo,
            release_id=release.release_id,
            asset_name="ccollab-manifest.json",
        )
        manifest = parse_release_manifest(manifest_payload)
        plan = plan_update_for_install(
            install_discovery=discovery,
            target_manifest=manifest,
        )
        _validate_release_binding(release, manifest)
        run_compatibility_preflight(manifest, os_name=runtime_os)
        if plan.already_up_to_date:
            return UpdateResult.noop(
                current_version=plan.current_version,
                latest_version=plan.latest_version,
            )

        work_area = create_update_work_area(install_root)
        platform = resolve_platform_identifier()
        asset = manifest.asset_for(platform)
        progress_messages.append(f"Downloading {asset.name}...")
        archive_path = stage_release_asset(
            install_root=install_root,
            repo=release.repo,
            release_id=release.release_id,
            asset_id=asset.asset_id,
            asset_name=asset.name,
            work_area=work_area,
        )
        progress_messages.append("Verifying checksum...")
        verify_downloaded_archive(
            archive_path=archive_path,
            expected_sha256=asset.sha256,
            expected_size=asset.size_bytes,
        )
        extracted_root = work_area.staging_root / "extracted"
        extract_release_archive(archive_path, extracted_root)
        staged_install_root = _resolved_staged_install_root(extracted_root)
        validate_staged_payload(staged_install_root)
        write_staged_install_metadata(
            staged_install_root=staged_install_root,
            manifest=manifest,
            asset_name=asset.name,
            asset_sha256=asset.sha256,
        )

        verification_context = VerificationContext(
            os_name="windows" if runtime_os == "nt" else "posix",
            timeout_seconds=45,
        )
        progress_messages.append("Installing update...")
        if runtime_os == "nt":
            transaction = _run_windows_helper_transaction(
                install_root=install_root,
                staged_root=staged_install_root,
                backup_root=work_area.backup_root,
                verification_context=verification_context,
                current_version=plan.current_version,
                latest_version=plan.latest_version,
                progress_messages=progress_messages,
                allow_exec=True,
            )
        else:
            transaction = apply_update_transaction(
                install_root=install_root,
                staged_root=staged_install_root,
                backup_root=work_area.backup_root,
                verification_context=verification_context,
            )
        progress_messages.append("Running post-install verification...")
        _append_update_log(
            install_root=install_root,
            current_version=plan.current_version,
            latest_version=plan.latest_version,
            progress_messages=progress_messages,
            transaction=transaction,
        )
        if not transaction.ok:
            detail = transaction.error or "post-install verification failed"
            if transaction.rollback_performed and transaction.rollback_succeeded:
                _raise_update_execution_error(
                    message=detail,
                    plan=plan,
                    progress_messages=progress_messages,
                    rollback_succeeded=True,
                )
            if transaction.rollback_performed and transaction.rollback_succeeded is False:
                _raise_update_execution_error(
                    message=detail,
                    plan=plan,
                    progress_messages=progress_messages,
                    rollback_succeeded=False,
                )
            _raise_update_execution_error(
                message=detail,
                plan=plan,
                progress_messages=progress_messages,
            )

        verification = transaction.verification
        return UpdateResult.success(
            current_version=plan.current_version,
            latest_version=plan.latest_version,
            progress_messages=tuple(progress_messages),
            verification_stdout="" if verification is None else verification.stdout,
            verification_stderr="" if verification is None else verification.stderr,
        )
    except (
        BrokenLauncherError,
        CompatibilityError,
        GhAuthenticationError,
        GhPrerequisiteError,
        InstallDiscoveryError,
        MultipleInstallRootsError,
        RepoAccessError,
        UpdateExecutionError,
        UpdateLockedError,
    ):
        raise
    except UpdaterError as exc:
        if plan is None:
            raise
        _raise_update_execution_error(
            message=str(exc),
            plan=plan,
            progress_messages=progress_messages,
        )
    finally:
        if work_area is not None:
            _safe_rmtree(work_area.staging_root)
            if work_area.backup_root.exists():
                _safe_rmtree(work_area.backup_root)
        lock.release()
