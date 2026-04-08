from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from runtime.constants import DEFAULT_CLAUDE_MODEL


@dataclass(frozen=True)
class ResolvedPaths:
    skill_dir: Path
    bin_path: Path
    config_dir: Path
    task_root: Path


def resolve_paths() -> ResolvedPaths:
    home = Path(os.environ["HOME"]).expanduser()
    codex_home = Path(os.environ.get("CODEX_HOME", home / ".codex")).expanduser()
    xdg_config_home = Path(
        os.environ.get("XDG_CONFIG_HOME", home / ".config")
    ).expanduser()
    return ResolvedPaths(
        skill_dir=codex_home / "skills" / "delegate-to-claude-code",
        bin_path=home / ".local" / "bin" / "ccollab",
        config_dir=xdg_config_home / "cc_collab",
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
