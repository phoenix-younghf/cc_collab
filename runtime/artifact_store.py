from __future__ import annotations

import json
import shutil
from pathlib import Path

from runtime.constants import PATCH_FILE


def create_task_dir(task_root: Path, task_id: str) -> Path:
    task_dir = task_root / task_id
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


def cleanup_task_dir(task_dir: Path) -> None:
    shutil.rmtree(task_dir)
