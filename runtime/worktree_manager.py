from __future__ import annotations

import subprocess
from pathlib import Path

from runtime.workspace_guard import copy_workspace_tree


def build_worktree_add_command(branch_name: str, repo_root: str, worktree_path: str) -> list[str]:
    return ["git", "-C", repo_root, "worktree", "add", worktree_path, "-b", branch_name]


def build_commit_ready_metadata(isolated_path: str, commit_shas: list[str]) -> dict[str, object]:
    return {
        "isolated_path": isolated_path,
        "commit_shas": commit_shas,
    }


def choose_isolation_strategy(
    *,
    git_available: bool,
    repo: bool,
    worktree_usable: bool,
) -> str:
    if git_available and repo and worktree_usable:
        return "git-worktree"
    return "filesystem-copy"


def create_filesystem_copy(repo_root: Path, task_dir: Path) -> Path:
    copied_root = task_dir / "isolated-copy"
    copy_workspace_tree(
        repo_root,
        copied_root,
        task_root=task_dir.parent,
    )
    return copied_root


def create_isolated_worktree(repo_root: Path, task_dir: Path, task_id: str) -> Path:
    worktree_path = task_dir / "isolated-worktree"
    branch_name = f"ccollab-{task_id}"
    cmd = build_worktree_add_command(branch_name, str(repo_root), str(worktree_path))
    result = subprocess.run(cmd, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or "worktree creation failed")
    return worktree_path


def create_task_owned_commit(
    isolated_path: Path,
    declared_files: list[str],
    task_id: str,
) -> dict[str, object]:
    add_result = subprocess.run(
        ["git", "-C", str(isolated_path), "add", "--", *declared_files],
        text=True,
        capture_output=True,
        check=False,
    )
    if add_result.returncode != 0:
        raise RuntimeError(add_result.stderr or "git add failed")
    commit_result = subprocess.run(
        ["git", "-C", str(isolated_path), "commit", "-m", f"ccollab: {task_id}"],
        text=True,
        capture_output=True,
        check=False,
    )
    if commit_result.returncode != 0:
        raise RuntimeError(commit_result.stderr or "git commit failed")
    sha_result = subprocess.run(
        ["git", "-C", str(isolated_path), "rev-parse", "HEAD"],
        text=True,
        capture_output=True,
        check=False,
    )
    if sha_result.returncode != 0:
        raise RuntimeError(sha_result.stderr or "git rev-parse failed")
    return build_commit_ready_metadata(str(isolated_path), [sha_result.stdout.strip()])
