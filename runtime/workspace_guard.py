from __future__ import annotations

import hashlib
import os
import shutil
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
    git_status: str | None
    files: list[FileBaseline]
    status_snapshot: dict[str, str | None]


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
    git_status: str | None,
    task_root: Path | None = None,
) -> WorkspaceBaseline:
    captured: list[FileBaseline] = []
    for relative_path in files:
        full = project_root / relative_path
        if full.exists() and not full.is_file():
            raise RuntimeError("declared path must be a file")
        captured.append(
            FileBaseline(
                relative_path=relative_path,
                exists=full.exists(),
                sha256=sha256_file(full) if full.exists() else None,
            )
        )
    status_paths: dict[str, str | None]
    if git_status is None:
        status_paths = snapshot_workspace_tree(project_root, task_root=task_root)
    else:
        status_paths = snapshot_paths(project_root, changed_paths_from_git_status(git_status))
    return WorkspaceBaseline(
        git_head=git_head,
        git_status=git_status,
        files=captured,
        status_snapshot=status_paths,
    )


def changed_paths_from_git_status(status: str) -> list[str]:
    paths: list[str] = []
    for line in status.splitlines():
        if len(line) >= 4:
            raw_path = line[3:]
            if " -> " in raw_path:
                raw_path = raw_path.split(" -> ", 1)[1]
            paths.append(raw_path)
    return paths


def snapshot_paths(project_root: Path, relative_paths: list[str]) -> dict[str, str | None]:
    snapshot: dict[str, str | None] = {}
    for relative_path in relative_paths:
        full = project_root / relative_path
        snapshot[relative_path] = sha256_file(full) if full.exists() else None
    return snapshot


EXCLUDED_DIR_NAMES = {
    ".git",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".nox",
}


def _iter_workspace_files(
    project_root: Path,
    *,
    task_root: Path | None = None,
    destination_root: Path | None = None,
) -> list[tuple[Path, str]]:
    root = project_root.resolve()
    excluded_task_root = task_root.resolve() if task_root is not None else None
    excluded_destination = destination_root.resolve() if destination_root is not None else None
    files: list[tuple[Path, str]] = []
    for current_root, dirnames, filenames in os.walk(root):
        current_path = Path(current_root)
        filtered_dirnames: list[str] = []
        for dirname in sorted(dirnames):
            candidate = current_path / dirname
            if dirname in EXCLUDED_DIR_NAMES:
                continue
            if excluded_task_root is not None and candidate == excluded_task_root:
                continue
            if excluded_destination is not None and candidate == excluded_destination:
                continue
            filtered_dirnames.append(dirname)
        dirnames[:] = filtered_dirnames
        for filename in sorted(filenames):
            full_path = current_path / filename
            relative_path = full_path.relative_to(root).as_posix()
            files.append((full_path, relative_path))
    return files


def snapshot_workspace_tree(
    project_root: Path,
    *,
    task_root: Path | None = None,
) -> dict[str, str | None]:
    snapshot: dict[str, str | None] = {}
    for full_path, relative_path in _iter_workspace_files(project_root, task_root=task_root):
        snapshot[relative_path] = sha256_file(full_path)
    return snapshot


def copy_workspace_tree(
    project_root: Path,
    destination: Path,
    *,
    task_root: Path | None = None,
) -> list[str]:
    destination.mkdir(parents=True, exist_ok=True)
    copied_manifest: list[str] = []
    for full_path, relative_path in _iter_workspace_files(
        project_root,
        task_root=task_root,
        destination_root=destination,
    ):
        target = destination / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(full_path, target)
        copied_manifest.append(relative_path)
    return copied_manifest


def detect_unsafe_dirty_state(baseline: WorkspaceBaseline) -> bool:
    if baseline.git_status is None:
        return False
    declared = {item.relative_path for item in baseline.files}
    return any(path in declared for path in changed_paths_from_git_status(baseline.git_status))


def detect_post_run_changes(pre_status: str, post_status: str) -> list[str]:
    pre_paths = set(changed_paths_from_git_status(pre_status))
    post_paths = set(changed_paths_from_git_status(post_status))
    return sorted(post_paths.symmetric_difference(pre_paths) | (post_paths - pre_paths))


def detect_post_run_changes_with_snapshots(
    project_root: Path,
    pre_status: str | None,
    pre_snapshot: dict[str, str | None],
    post_status: str | None,
    *,
    task_root: Path | None = None,
) -> list[str]:
    if pre_status is None or post_status is None:
        post_snapshot = snapshot_workspace_tree(project_root, task_root=task_root)
        all_paths = sorted(set(pre_snapshot) | set(post_snapshot))
        return sorted(
            path
            for path in all_paths
            if pre_snapshot.get(path) != post_snapshot.get(path)
        )

    pre_paths = set(changed_paths_from_git_status(pre_status))
    post_paths = set(changed_paths_from_git_status(post_status))
    all_paths = sorted(pre_paths | post_paths)
    post_snapshot = snapshot_paths(project_root, all_paths)
    changed: set[str] = set()
    for path in all_paths:
        if pre_snapshot.get(path) != post_snapshot.get(path):
            changed.add(path)
    changed.update(post_paths - pre_paths)
    changed.update(pre_paths - post_paths)
    return sorted(changed)


def undeclared_changed_files(changed_paths: list[str], declared_files: list[str]) -> list[str]:
    declared = set(declared_files)
    return sorted(path for path in changed_paths if path not in declared)
