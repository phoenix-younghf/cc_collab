from __future__ import annotations

import os
import platform
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath

from runtime.constants import DEFAULT_CLAUDE_MODEL


@dataclass(frozen=True)
class ResolvedPaths:
    install_root: PurePath
    runtime_root: PurePath
    skill_dir: PurePath
    bin_path: PurePath
    config_dir: PurePath
    task_root: PurePath


def _resolve_path_factory(
    target_os: str,
    path_factory: type[PurePath] | None,
) -> type[PurePath]:
    if path_factory is not None:
        return path_factory
    if target_os == os.name:
        return Path
    if target_os == "nt":
        return PureWindowsPath
    return PurePosixPath


def _resolve_home(env: Mapping[str, str], target_os: str) -> str:
    if target_os == "nt":
        user_profile = env.get("USERPROFILE", "").strip()
        if user_profile:
            return user_profile
        home_drive = env.get("HOMEDRIVE", "").strip()
        home_path = env.get("HOMEPATH", "").strip()
        if home_drive and home_path:
            return f"{home_drive}{home_path}"
    home = env.get("HOME", "").strip()
    if home:
        return home
    return str(Path.home())


def _resolve_install_root(
    *,
    env: Mapping[str, str],
    target_os: str,
    home: PurePath,
    factory: type[PurePath],
) -> PurePath:
    if target_os == "nt":
        local_app_data = env.get("LOCALAPPDATA", "").strip()
        base = factory(local_app_data) if local_app_data else home / "AppData" / "Local"
        return base / "cc_collab" / "install"
    if platform.system() == "Darwin":
        return home / "Library" / "Application Support" / "cc_collab" / "install"
    return home / ".local" / "share" / "cc_collab" / "install"


def resolve_paths(
    env: Mapping[str, str] | None = None,
    os_name: str | None = None,
    path_factory: type[PurePath] | None = None,
) -> ResolvedPaths:
    current_env = os.environ if env is None else env
    target_os = os.name if os_name is None else os_name
    factory = _resolve_path_factory(target_os, path_factory)
    home = factory(_resolve_home(current_env, target_os))
    install_root = _resolve_install_root(
        env=current_env,
        target_os=target_os,
        home=home,
        factory=factory,
    )
    codex_home = (
        factory(current_env["CODEX_HOME"])
        if current_env.get("CODEX_HOME")
        else home / ".codex"
    )
    if target_os == "nt":
        app_data = current_env.get("APPDATA", "").strip()
        config_dir = (
            factory(app_data) / "cc_collab"
            if app_data
            else home / "AppData" / "Roaming" / "cc_collab"
        )
        bin_path = home / ".local" / "bin" / "ccollab.cmd"
    else:
        xdg_config_home = current_env.get("XDG_CONFIG_HOME", "").strip()
        config_dir = (
            factory(xdg_config_home) / "cc_collab"
            if xdg_config_home
            else home / ".config" / "cc_collab"
        )
        bin_path = home / ".local" / "bin" / "ccollab"
    return ResolvedPaths(
        install_root=install_root,
        runtime_root=install_root,
        skill_dir=codex_home / "skills" / "delegate-to-claude-code",
        bin_path=bin_path,
        config_dir=config_dir,
        task_root=home / "workspace" / "cc_collab" / "tasks",
    )


def resolve_claude_model(request: dict | None = None) -> str:
    requested_model = (
        request.get("claude_role", {}).get("model")
        if isinstance(request, dict)
        else None
    )
    if isinstance(requested_model, str) and requested_model.strip():
        return requested_model.strip()
    env_model = os.environ.get("CCOLLAB_CLAUDE_MODEL", "").strip()
    if env_model:
        return env_model
    return DEFAULT_CLAUDE_MODEL
