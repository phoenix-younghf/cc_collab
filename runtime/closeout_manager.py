from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
from difflib import SequenceMatcher
from pathlib import Path, PurePosixPath

from runtime import artifact_store
from runtime.constants import PATCH_FILE


def choose_failure_terminal_state(allowed: list[str]) -> str:
    return "patch-ready" if "patch-ready" in allowed else "inspection-required"


def validate_terminal_state(actual: str, expected: str) -> None:
    if actual != expected:
        raise ValueError("terminal state mismatch")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_none_artifact_metadata() -> dict[str, str]:
    return {"artifact_type": "none"}


def build_patch_ready_metadata(task_dir: str) -> dict[str, str]:
    patch_path = f"{task_dir}/{PATCH_FILE}"
    return {
        "artifact_type": "git-patch",
        "patch_path": patch_path,
        "apply_command": f"git apply {patch_path}",
    }


def build_git_patch_metadata_for_workspace_pair(
    *,
    task_dir: Path,
    patch_path: Path | None = None,
) -> dict[str, str]:
    target_patch_path = artifact_store.patch_path_for_task(task_dir) if patch_path is None else patch_path
    return {
        "artifact_type": "git-patch",
        "patch_path": str(target_patch_path),
        "apply_command": f"git apply -p2 {target_patch_path}",
    }


def build_file_change_set_metadata(
    task_dir: Path,
    entries: list[dict[str, object]],
) -> dict[str, object]:
    changed_files = [
        str(entry.get("renamed_path") or entry.get("original_path") or "")
        for entry in entries
        if entry.get("renamed_path") or entry.get("original_path")
    ]
    manifest = {
        "manifest_path": str(artifact_store.change_set_manifest_path_for_task(task_dir)),
        "artifact_root": str(artifact_store.change_set_dir_for_task(task_dir)),
        "entries": entries,
        "inspect_instructions": (
            "Inspect the copied files under file-change-set/files/ and review manifest.json for hashes."
        ),
        "copy_back_instructions": (
            "After review, copy approved files from file-change-set/files/ back into the target workspace paths."
        ),
    }
    return {
        "artifact_type": "file-change-set",
        "changed_files": changed_files,
        "change_set_manifest": manifest,
    }


def _merge_renamed_entries(
    entries: list[dict[str, object]],
) -> list[dict[str, object]]:
    deleted_entries = [entry for entry in entries if entry["change_kind"] == "deleted"]
    added_entries = [entry for entry in entries if entry["change_kind"] == "added"]
    merged_entries = [entry for entry in entries if entry["change_kind"] not in {"deleted", "added"}]

    def as_renamed_entry(
        deleted_entry: dict[str, object],
        added_entry: dict[str, object],
    ) -> dict[str, object]:
        return {
            "original_path": deleted_entry["original_path"],
            "renamed_path": added_entry["original_path"],
            "stored_path": added_entry["stored_path"],
            "before_hash": deleted_entry["before_hash"],
            "after_hash": added_entry["after_hash"],
            "change_kind": "renamed",
        }

    deleted_by_hash: dict[str, list[dict[str, object]]] = {}
    for entry in deleted_entries:
        if isinstance(entry.get("before_hash"), str):
            deleted_by_hash.setdefault(entry["before_hash"], []).append(entry)

    unmatched_deleted: list[dict[str, object]] = []
    unmatched_added: list[dict[str, object]] = []
    for entry in added_entries:
        if isinstance(entry.get("after_hash"), str):
            candidates = deleted_by_hash.get(entry["after_hash"], [])
            if candidates:
                deleted_entry = candidates.pop(0)
                if not candidates:
                    deleted_by_hash.pop(entry["after_hash"], None)
                merged_entries.append(as_renamed_entry(deleted_entry, entry))
                continue
        unmatched_added.append(entry)
    for remaining in deleted_by_hash.values():
        unmatched_deleted.extend(remaining)

    def rename_score(deleted_entry: dict[str, object], added_entry: dict[str, object]) -> float:
        deleted_path = PurePosixPath(str(deleted_entry["original_path"]))
        added_path = PurePosixPath(str(added_entry["original_path"]))
        if deleted_path.suffix != added_path.suffix:
            return 0.0
        if len(deleted_path.stem) < 3 or len(added_path.stem) < 3:
            return 0.0

        before_path = deleted_entry.get("_before_path")
        after_path = added_entry.get("_after_path")
        content_score = 0.0
        if isinstance(before_path, Path) and isinstance(after_path, Path):
            content_score = SequenceMatcher(
                None,
                before_path.read_bytes()[:8192],
                after_path.read_bytes()[:8192],
            ).ratio()
        if content_score < 0.45:
            return 0.0

        score = (content_score * 0.75) + (
            SequenceMatcher(None, deleted_path.as_posix(), added_path.as_posix()).ratio() * 0.25
        )
        if deleted_path.parent == added_path.parent:
            score += 0.15
        return score

    while unmatched_deleted and unmatched_added:
        best_pair: tuple[int, int, float] | None = None
        for deleted_index, deleted_entry in enumerate(unmatched_deleted):
            for added_index, added_entry in enumerate(unmatched_added):
                score = rename_score(deleted_entry, added_entry)
                if best_pair is None or score > best_pair[2]:
                    best_pair = (deleted_index, added_index, score)
        if best_pair is None or best_pair[2] < 0.55:
            break
        deleted_index, added_index, _score = best_pair
        merged_entries.append(
            as_renamed_entry(
                unmatched_deleted.pop(deleted_index),
                unmatched_added.pop(added_index),
            )
        )

    merged_entries.extend(unmatched_added)
    merged_entries.extend(unmatched_deleted)
    return sorted(
        merged_entries,
        key=lambda entry: (
            str(entry.get("renamed_path") or entry.get("original_path") or ""),
            str(entry.get("change_kind") or ""),
        ),
    )


def collect_file_change_set_entries(
    *,
    original_root: Path,
    modified_root: Path,
    task_dir: Path,
    changed_paths: list[str],
) -> list[dict[str, object]]:
    candidate_paths = sorted(dict.fromkeys(changed_paths))
    if not candidate_paths:
        raise RuntimeError("no changed paths to capture")
    change_set_dir = artifact_store.change_set_dir_for_task(task_dir)
    change_set_dir.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, object]] = []
    for relative_path in candidate_paths:
        original_path = original_root / relative_path
        modified_path = modified_root / relative_path
        before_exists = original_path.exists()
        after_exists = modified_path.exists()
        if before_exists and not original_path.is_file():
            raise RuntimeError("change-set only supports file paths")
        if after_exists and not modified_path.is_file():
            raise RuntimeError("change-set only supports file paths")
        before_hash = sha256_file(original_path) if before_exists else None
        after_hash = sha256_file(modified_path) if after_exists else None
        if before_exists and after_exists and before_hash == after_hash:
            continue
        if before_exists and after_exists:
            change_kind = "modified"
        elif before_exists:
            change_kind = "deleted"
        else:
            change_kind = "added"
        stored_path: str | None = None
        if after_exists:
            target_path = artifact_store.change_set_storage_path_for_task(task_dir, relative_path)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(modified_path, target_path)
            stored_path = target_path.relative_to(task_dir).as_posix()
        entries.append(
            {
                "original_path": relative_path,
                "stored_path": stored_path,
                "before_hash": before_hash,
                "after_hash": after_hash,
                "change_kind": change_kind,
                "_before_path": original_path if before_exists else None,
                "_after_path": modified_path if after_exists else None,
            }
        )
    merged_entries = _merge_renamed_entries(entries)
    if not merged_entries:
        raise RuntimeError("no changed paths to capture")
    return [
        {
            key: value
            for key, value in entry.items()
            if not key.startswith("_")
        }
        for entry in merged_entries
    ]


def _stage_patch_paths(
    source_root: Path,
    stage_root: Path,
    relative_paths: list[str],
) -> None:
    for relative_path in relative_paths:
        source_path = source_root / relative_path
        if not source_path.exists():
            continue
        if not source_path.is_file():
            raise RuntimeError("patch paths must be files")
        target_path = stage_root / relative_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target_path)


def generate_patch(
    workdir: Path,
    task_dir: Path,
    paths_to_patch: list[str],
) -> dict[str, str]:
    if not paths_to_patch:
        raise RuntimeError("no paths to patch")
    patch_path = artifact_store.patch_path_for_task(task_dir)
    subprocess.run(
        ["git", "-C", str(workdir), "add", "-N", "--", *paths_to_patch],
        text=True,
        capture_output=True,
        check=False,
    )
    result = subprocess.run(
        ["git", "-C", str(workdir), "diff", "--binary", "--", *paths_to_patch],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("patch generation failed")
    patch_path.write_text(result.stdout, encoding="utf-8")
    return build_patch_ready_metadata(str(task_dir))


def generate_patch_from_workspace_pair(
    original_root: Path,
    modified_root: Path,
    task_dir: Path,
    paths_to_patch: list[str],
) -> dict[str, str]:
    candidate_paths = sorted(dict.fromkeys(paths_to_patch))
    if not candidate_paths:
        raise RuntimeError("no paths to patch")
    patch_path = artifact_store.patch_path_for_task(task_dir)
    with tempfile.TemporaryDirectory(prefix="ccollab-patch-") as tmp:
        temp_root = Path(tmp)
        before_root = temp_root / "before"
        after_root = temp_root / "after"
        before_root.mkdir()
        after_root.mkdir()
        _stage_patch_paths(original_root, before_root, candidate_paths)
        _stage_patch_paths(modified_root, after_root, candidate_paths)
        result = subprocess.run(
            ["git", "diff", "--no-index", "--binary", "--", "before", "after"],
            cwd=temp_root,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode not in {0, 1}:
            raise RuntimeError("patch generation failed")
        if not result.stdout.strip():
            raise RuntimeError("patch generation failed")
        patch_path.write_text(result.stdout, encoding="utf-8")
    return build_git_patch_metadata_for_workspace_pair(
        task_dir=task_dir,
        patch_path=patch_path,
    )


def generate_file_change_set(
    *,
    original_root: Path,
    modified_root: Path,
    task_dir: Path,
    changed_paths: list[str],
) -> dict[str, object]:
    change_set_dir = artifact_store.change_set_dir_for_task(task_dir)
    manifest_path = artifact_store.change_set_manifest_path_for_task(task_dir)
    entries = collect_file_change_set_entries(
        original_root=original_root,
        modified_root=modified_root,
        task_dir=task_dir,
        changed_paths=changed_paths,
    )
    metadata = build_file_change_set_metadata(task_dir, entries)
    artifact_store.write_json_artifact(change_set_dir, manifest_path.name, metadata["change_set_manifest"])
    return metadata
