from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


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
