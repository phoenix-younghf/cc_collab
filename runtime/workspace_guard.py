from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FileBaseline:
    relative_path: str
    exists: bool
    sha256: str | None


@dataclass(frozen=True)
class WorkspaceBaseline:
    git_head: str | None
    git_status: str
    files: list[FileBaseline]


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def capture_git_head(project_root: Path) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(project_root), "rev-parse", "HEAD"],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def capture_git_status(project_root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(project_root), "status", "--porcelain=v1", "--untracked-files=all"],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("git status capture failed")
    return result.stdout


def capture_baseline(
    project_root: Path,
    files: list[str],
    *,
    git_head: str | None,
    git_status: str,
) -> WorkspaceBaseline:
    if git_status is None:
        raise RuntimeError("git status capture failed")
    captured: list[FileBaseline] = []
    for relative_path in files:
        full = project_root / relative_path
        captured.append(
            FileBaseline(
                relative_path=relative_path,
                exists=full.exists(),
                sha256=sha256_file(full) if full.exists() else None,
            )
        )
    return WorkspaceBaseline(git_head=git_head, git_status=git_status, files=captured)


def changed_paths_from_git_status(status: str) -> list[str]:
    paths: list[str] = []
    for line in status.splitlines():
        if len(line) >= 4:
            paths.append(line[3:])
    return paths


def detect_unsafe_dirty_state(baseline: WorkspaceBaseline) -> bool:
    declared = {item.relative_path for item in baseline.files}
    return any(path in declared for path in changed_paths_from_git_status(baseline.git_status))


def detect_post_run_changes(pre_status: str, post_status: str) -> list[str]:
    pre_paths = set(changed_paths_from_git_status(pre_status))
    post_paths = set(changed_paths_from_git_status(post_status))
    return sorted(post_paths.symmetric_difference(pre_paths) | (post_paths - pre_paths))


def undeclared_changed_files(changed_paths: list[str], declared_files: list[str]) -> list[str]:
    declared = set(declared_files)
    return sorted(path for path in changed_paths if path not in declared)
