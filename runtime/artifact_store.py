from __future__ import annotations

import json
import re
import shutil
from pathlib import Path, PurePosixPath

from runtime.constants import PATCH_FILE

TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def resolve_task_dir(task_root: Path, task_id: str) -> Path:
    if not TASK_ID_PATTERN.fullmatch(task_id):
        raise ValueError("invalid task id")
    return task_root / task_id


def create_task_dir(task_root: Path, task_id: str) -> Path:
    task_dir = resolve_task_dir(task_root, task_id)
    task_dir.mkdir(parents=True, exist_ok=True)
    return task_dir


def write_json_artifact(task_dir: Path, name: str, payload: dict) -> Path:
    target = task_dir / name
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return target


def write_text_artifact(task_dir: Path, name: str, content: str) -> Path:
    target = task_dir / name
    target.write_text(content, encoding="utf-8")
    return target


def write_log_artifact(task_dir: Path, name: str, content: str) -> Path:
    return write_text_artifact(task_dir, name, content)


def load_json_artifact(task_dir: Path, name: str) -> dict:
    return json.loads((task_dir / name).read_text(encoding="utf-8"))


def patch_path_for_task(task_dir: Path) -> Path:
    return task_dir / PATCH_FILE


def change_set_dir_for_task(task_dir: Path) -> Path:
    return task_dir / "file-change-set"


def change_set_manifest_path_for_task(task_dir: Path) -> Path:
    return change_set_dir_for_task(task_dir) / "manifest.json"


def change_set_storage_path_for_task(task_dir: Path, relative_path: str) -> Path:
    if not isinstance(relative_path, str) or not relative_path:
        raise ValueError("invalid change-set relative path")
    candidate = PurePosixPath(relative_path)
    if candidate.is_absolute() or ".." in candidate.parts or "." in candidate.parts:
        raise ValueError("invalid change-set relative path")
    return change_set_dir_for_task(task_dir) / "files" / Path(*candidate.parts)


def cleanup_task_dir(task_dir: Path) -> None:
    shutil.rmtree(task_dir)
