from __future__ import annotations

import json
import os
import platform
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from runtime.config import resolve_paths
from runtime.constants import INSTALL_METADATA_FILENAME


@dataclass(frozen=True)
class InstallMetadata:
    version: str
    channel: str
    repo: str
    platform: str
    installed_at: str
    asset_name: str
    asset_sha256: str
    install_root: str


@dataclass(frozen=True)
class InstallDiscovery:
    install_root: Path
    status: str
    metadata: InstallMetadata | None
    version: str
    channel: str
    repo: str


class InstallDiscoveryError(RuntimeError):
    """Raised when ccollab cannot resolve a usable install root."""


class InstallRootNotFoundError(InstallDiscoveryError):
    """Raised when no install payload can be found."""


class MultipleInstallRootsError(InstallDiscoveryError):
    """Raised when multiple conflicting install roots are present."""


def _metadata_path(install_root: Path) -> Path:
    return install_root / INSTALL_METADATA_FILENAME


def write_install_metadata(install_root: Path, metadata: InstallMetadata) -> None:
    _metadata_path(install_root).write_text(
        json.dumps(asdict(metadata), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def read_install_metadata(install_root: Path) -> InstallMetadata | None:
    metadata_path = _metadata_path(install_root)
    if not metadata_path.exists():
        return None
    payload: dict[str, Any] = json.loads(metadata_path.read_text(encoding="utf-8"))
    return InstallMetadata(**payload)


def is_valid_install_payload(install_root: Path) -> bool:
    return (install_root / "runtime").is_dir() and (install_root / "bin").is_dir()


def resolve_platform_identifier() -> str:
    if sys.platform == "win32":
        machine = platform.machine().lower()
        if machine in {"amd64", "x86_64"}:
            return "windows-x64"
    if sys.platform.startswith("linux"):
        machine = platform.machine().lower()
        if machine in {"x86_64", "amd64"}:
            return "linux-x64"
    if sys.platform == "darwin":
        machine = platform.machine().lower()
        if machine in {"arm64", "x86_64"}:
            return "macos-universal"
    raise RuntimeError(
        f"Unsupported platform for release assets: {sys.platform}/{platform.machine()}"
    )


def _normalize_root(candidate: str | Path | None) -> Path | None:
    if candidate is None:
        return None
    value = str(candidate).strip()
    if not value:
        return None
    return Path(value).expanduser()


def get_active_runtime_root(module_file: str | Path | None = None) -> Path | None:
    module_path = Path(__file__ if module_file is None else module_file).expanduser()
    install_root = module_path.parent.parent
    if not is_valid_install_payload(install_root):
        return None
    return install_root


def _default_install_root(*, env: dict[str, str], os_name: str) -> Path:
    return Path(resolve_paths(env=env, os_name=os_name).install_root)


def _legacy_discovery(install_root: Path) -> InstallDiscovery:
    return InstallDiscovery(
        install_root=install_root,
        status="legacy-install",
        metadata=None,
        version="unknown",
        channel="unknown",
        repo="legacy-install",
    )


def _installed_discovery(install_root: Path, metadata: InstallMetadata) -> InstallDiscovery:
    return InstallDiscovery(
        install_root=install_root,
        status="installed",
        metadata=metadata,
        version=metadata.version,
        channel=metadata.channel,
        repo=metadata.repo,
    )


def discover_install_root(
    *,
    active_runtime_root: str | Path | None,
    env: dict[str, str] | None = None,
    os_name: str | None = None,
    default_install_root: str | Path | None = None,
    reject_conflicting_roots: bool = False,
) -> InstallDiscovery:
    current_env = dict(env or {})
    target_os = os_name or os.name
    candidate_pairs: list[tuple[str, Path]] = []

    for source, raw_root in (
        ("active", active_runtime_root),
        ("override", current_env.get("CCOLLAB_RUNTIME_ROOT")),
        (
            "default",
            default_install_root
            if default_install_root is not None
            else _default_install_root(env=current_env, os_name=target_os),
        ),
    ):
        root = _normalize_root(raw_root)
        if root is None:
            continue
        if all(existing_root != root for _, existing_root in candidate_pairs):
            candidate_pairs.append((source, root))

    valid_pairs = [(source, root) for source, root in candidate_pairs if is_valid_install_payload(root)]
    unique_valid_roots = {root for _, root in valid_pairs}
    if reject_conflicting_roots and len(unique_valid_roots) > 1:
        raise MultipleInstallRootsError(
            "Multiple ccollab installs were detected. "
            "Set CCOLLAB_RUNTIME_ROOT to the intended install and retry."
        )
    if not valid_pairs:
        raise InstallRootNotFoundError(
            "No valid ccollab install was found. Reinstall ccollab using the normal install flow, then retry."
        )

    _, install_root = valid_pairs[0]
    metadata = read_install_metadata(install_root)
    if metadata is None:
        return _legacy_discovery(install_root)
    return _installed_discovery(install_root, metadata)
