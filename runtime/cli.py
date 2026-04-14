from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

from runtime import artifact_store
from runtime.capabilities import RuntimeCapabilities, detect_runtime_capabilities
from runtime.claude_runner import (
    ClaudeTimeoutError,
    build_command,
    run_claude,
    select_agent_pack,
    serialize_agent_pack,
)
from runtime.closeout_manager import (
    build_file_change_set_metadata,
    build_git_patch_metadata_for_workspace_pair,
    collect_file_change_set_entries,
    choose_failure_terminal_state,
    generate_patch,
    validate_terminal_state,
)
from runtime.config import resolve_claude_model, resolve_claude_timeout_seconds, resolve_paths
from runtime.constants import (
    DEFAULT_PROMPT_BY_TASK,
    REQUEST_JSON,
    REQUEST_MD,
    RESULT_JSON,
    RESULT_MD,
    RUN_LOG,
)
from runtime.doctor import render_doctor_report, run_doctor
from runtime.prompt_loader import load_prompt
from runtime.request_renderer import render_request_markdown
from runtime.result_parser import parse_result
from runtime.result_renderer import render_result_markdown
from runtime.schema_loader import load_schema_text
from runtime.updater import (
    BrokenLauncherError,
    CompatibilityError,
    GhAuthenticationError,
    GhPrerequisiteError,
    RepoAccessError,
    UpdateResult,
    UpdaterError,
    run_update,
)
from runtime.validators import ValidationError, validate_request, validate_result
from runtime.versioning import (
    InstallDiscoveryError,
    MultipleInstallRootsError,
    discover_install_root,
    get_active_runtime_root,
)
from runtime.workspace_guard import (
    capture_baseline,
    capture_git_head,
    capture_git_status,
    copy_workspace_tree,
    detect_post_run_changes_with_snapshots,
    detect_unsafe_dirty_state,
    changed_paths_from_git_status,
    snapshot_paths,
    snapshot_workspace_tree,
    undeclared_changed_files,
)
from runtime.worktree_manager import (
    choose_isolation_strategy,
    create_filesystem_copy,
    create_isolated_worktree,
    create_task_owned_commit,
)


REQUIRED_RESULT_KEYS = {
    "task_id",
    "status",
    "summary",
    "decisions",
    "changed_files",
    "verification",
    "open_questions",
    "risks",
    "follow_up_suggestions",
    "agent_usage",
    "terminal_state",
}

create_task_dir = artifact_store.create_task_dir


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ccollab")
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--request", required=True)
    run_parser.add_argument("--task-root")

    for name in ("status", "open", "cleanup"):
        subparser = subparsers.add_parser(name)
        subparser.add_argument("--task", required=True)
        subparser.add_argument("--task-root")

    subparsers.add_parser("doctor")
    subparsers.add_parser("version")
    subparsers.add_parser("update")
    return parser


def resolve_task_root(override: str | None) -> Path:
    if override:
        return Path(override)
    return resolve_paths().task_root


def load_request(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _looks_like_task_result(payload: dict) -> bool:
    return isinstance(payload, dict) and REQUIRED_RESULT_KEYS.issubset(payload)


def _repair_source_output(parsed_output: dict, raw_output: str) -> str:
    nested = parsed_output.get("result") if isinstance(parsed_output, dict) else None
    if isinstance(nested, str) and nested.strip():
        return nested
    if isinstance(parsed_output, dict) and parsed_output:
        return json.dumps(parsed_output)
    return raw_output


def _summarize_unstructured_output(text: str, limit: int = 240) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."


def _normalize_unstructured_result(
    *,
    task_id: str,
    raw_output: str,
    verification_commands: list[str],
) -> dict:
    return {
        "task_id": task_id,
        "status": "completed",
        "summary": _summarize_unstructured_output(raw_output),
        "decisions": [],
        "changed_files": [],
        "verification": {
            "commands_run": [],
            "results": [],
            "all_passed": not verification_commands,
        },
        "open_questions": [],
        "risks": [],
        "follow_up_suggestions": [],
        "agent_usage": {
            "used_subagents": False,
            "notes": "normalized from unstructured Claude output",
        },
        "terminal_state": "archived",
        "raw_output": raw_output,
    }


def _repair_result_once(
    *,
    workdir: Path,
    schema_json: str,
    broken_output: str,
    model: str | None = None,
    timeout_seconds: int | None = None,
) -> dict:
    repair_prompt = (
        "Repair the following output into valid JSON that matches the schema. "
        "Return JSON only.\n\n"
        f"{broken_output}"
    )
    repair_command = build_command(
        workdir=str(workdir),
        prompt=repair_prompt,
        schema_json=schema_json,
        runtime_contract="repair_mode=true",
        agent_pack_json=None,
        model=model,
    )
    if timeout_seconds is None:
        repaired_stdout, _ = run_claude(repair_command)
    else:
        repaired_stdout, _ = run_claude(
            repair_command,
            timeout_seconds=timeout_seconds,
        )
    return parse_result(repaired_stdout)


def task_failure_result(
    task_id: str,
    terminal_state: str,
    summary: str,
    *,
    changed_files: list[str] | None = None,
    verification_commands: list[str] | None = None,
    metadata: dict | None = None,
) -> dict:
    payload = {
        "task_id": task_id,
        "status": "failed",
        "summary": summary,
        "decisions": [],
        "changed_files": changed_files or [],
        "verification": {
            "commands_run": verification_commands or [],
            "results": [],
            "all_passed": False,
        },
        "open_questions": [],
        "risks": [summary],
        "follow_up_suggestions": [],
        "agent_usage": {"used_subagents": False, "notes": ""},
        "terminal_state": terminal_state,
    }
    if metadata:
        payload.update(metadata)
    return payload


def persist_result(task_dir: Path, payload: dict) -> None:
    artifact_store.write_json_artifact(task_dir, RESULT_JSON, payload)
    artifact_store.write_text_artifact(task_dir, RESULT_MD, render_result_markdown(payload))


def _capability_summary(
    capabilities: RuntimeCapabilities | None,
    *,
    status: str,
) -> dict[str, object]:
    summary: dict[str, object] = {"status": status}
    if capabilities is None:
        return summary
    summary["python"] = asdict(capabilities.python)
    summary["claude"] = asdict(capabilities.claude)
    summary["git"] = asdict(capabilities.git)
    return summary


def _attach_runtime_metadata(
    payload: dict,
    *,
    runtime_mode: str,
    degradation_notes: list[str],
    capability_summary: dict[str, object],
    remediation: str | None = None,
) -> dict:
    payload["runtime_mode"] = runtime_mode
    payload.setdefault("artifact_type", "none")
    payload["degradation_notes"] = degradation_notes
    payload["capability_summary"] = capability_summary
    if remediation:
        payload["remediation"] = remediation
    return payload


def _persist_request(task_dir: Path, request: dict) -> None:
    artifact_store.write_json_artifact(task_dir, REQUEST_JSON, request)
    artifact_store.write_text_artifact(task_dir, REQUEST_MD, render_request_markdown(request))


def _persist_diagnostic_failure(
    *,
    task_id: str,
    request: dict,
    summary: str,
    remediation: str,
    verification_commands: list[str],
    run_log_lines: list[str],
    runtime_mode: str,
    degradation_notes: list[str],
    capabilities: RuntimeCapabilities,
) -> int:
    diagnostic_root = Path(tempfile.gettempdir()) / "ccollab-diagnostics"
    diagnostic_dir = artifact_store.create_task_dir(diagnostic_root, task_id)
    _persist_request(diagnostic_dir, request)
    failure = _attach_runtime_metadata(
        task_failure_result(
            task_id,
            "inspection-required",
            summary,
            verification_commands=verification_commands,
        ),
        runtime_mode=runtime_mode,
        degradation_notes=degradation_notes,
        capability_summary=_capability_summary(capabilities, status="preflight-failed"),
        remediation=remediation,
    )
    persist_result(diagnostic_dir, failure)
    artifact_store.write_log_artifact(diagnostic_dir, RUN_LOG, "\n".join(run_log_lines) + "\n")
    print(f"ccollab wrote diagnostics to {diagnostic_dir}", file=sys.stderr)
    return 1


def _preflight_failure(
    *,
    task_id: str,
    terminal_state: str,
    summary: str,
    verification_commands: list[str],
    runtime_mode: str,
    degradation_notes: list[str],
    capabilities: RuntimeCapabilities | None,
    remediation: str | None,
) -> dict:
    return _attach_runtime_metadata(
        task_failure_result(
            task_id,
            terminal_state,
            summary,
            verification_commands=verification_commands,
        ),
        runtime_mode=runtime_mode,
        degradation_notes=degradation_notes,
        capability_summary=_capability_summary(capabilities, status="preflight-failed"),
        remediation=remediation,
    )


def _effective_success_terminal(
    *,
    write_policy: str,
    requested: str,
    capabilities: RuntimeCapabilities,
) -> str:
    if write_policy != "write-isolated" or requested != "commit-ready":
        return requested
    if capabilities.git.mode == "git-aware" and capabilities.git.worktree_usable:
        return requested
    return "patch-ready"


def _runtime_degradation_notes(
    *,
    write_policy: str,
    requested_success_terminal: str,
    effective_success_terminal: str,
    capabilities: RuntimeCapabilities,
) -> list[str]:
    notes: list[str] = []
    if capabilities.git.mode == "filesystem-only":
        notes.append("Git-aware safety is unavailable; using filesystem-only safeguards.")
    elif write_policy == "write-isolated" and not capabilities.git.worktree_usable:
        notes.append("git worktree is unavailable; using a filesystem copy for isolated execution.")
    if effective_success_terminal != requested_success_terminal:
        notes.append(
            f"Success terminal degraded from {requested_success_terminal} to {effective_success_terminal}."
        )
    return notes


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


def generate_file_change_set_from_workspace_pair(
    original_root: Path,
    modified_root: Path,
    task_dir: Path,
    changed_paths: list[str],
) -> dict[str, object]:
    entries = collect_file_change_set_entries(
        original_root=original_root,
        modified_root=modified_root,
        task_dir=task_dir,
        changed_paths=changed_paths,
    )
    metadata = build_file_change_set_metadata(task_dir, entries)
    artifact_store.write_json_artifact(
        artifact_store.change_set_dir_for_task(task_dir),
        artifact_store.change_set_manifest_path_for_task(task_dir).name,
        metadata["change_set_manifest"],
    )
    return metadata


def handle_run(args: argparse.Namespace) -> int:
    request_path = Path(args.request)
    task_root = resolve_task_root(args.task_root)
    request = load_request(request_path)
    validate_request(request)
    task_id = request["task_id"]
    write_policy = request["write_policy"]
    success_terminal = request["inputs"]["closeout"]["on_success"]
    failure_terminal = request["inputs"]["closeout"]["on_failure"]
    verification_commands = request["inputs"].get("verification_commands", [])
    declared_files = request["inputs"].get("files", [])
    workdir = Path(request["workdir"])
    run_log_lines = [
        f"task_id={task_id}",
        f"write_policy={write_policy}",
        f"requested_success_terminal={success_terminal}",
        f"requested_failure_terminal={failure_terminal}",
    ]
    capabilities = detect_runtime_capabilities(workdir=workdir)
    runtime_mode = capabilities.git.mode
    effective_success_terminal = _effective_success_terminal(
        write_policy=write_policy,
        requested=success_terminal,
        capabilities=capabilities,
    )
    degradation_notes = _runtime_degradation_notes(
        write_policy=write_policy,
        requested_success_terminal=success_terminal,
        effective_success_terminal=effective_success_terminal,
        capabilities=capabilities,
    )
    run_log_lines.extend(
        [
            f"runtime_mode={runtime_mode}",
            f"effective_success_terminal={effective_success_terminal}",
        ]
    )

    try:
        task_dir = create_task_dir(task_root, task_id)
    except OSError as exc:
        run_log_lines.append(f"task_dir_error={exc}")
        return _persist_diagnostic_failure(
            task_id=task_id,
            request=request,
            summary=f"task root is not writable: {exc}",
            remediation=f"Choose a writable task root and rerun ccollab. Requested task root: {task_root}",
            verification_commands=verification_commands,
            run_log_lines=run_log_lines,
            runtime_mode=runtime_mode,
            degradation_notes=degradation_notes,
            capabilities=capabilities,
        )

    _persist_request(task_dir, request)

    isolation_strategy: str | None = None
    pre_status = None
    pre_status_snapshot: dict[str, str | None] = {}
    pre_full_snapshot: dict[str, str | None] = {}
    pre_run_copy_root: Path | None = None
    pre_git_head: str | None = None
    target_workdir = workdir
    try:
        if not workdir.is_dir():
            failure = _preflight_failure(
                task_id=task_id,
                terminal_state=failure_terminal,
                summary=f"preflight failed: workdir is missing: {workdir}",
                verification_commands=verification_commands,
                runtime_mode=runtime_mode,
                degradation_notes=degradation_notes,
                capabilities=capabilities,
                remediation="Create the requested workdir or update the request to point at an existing directory.",
            )
            persist_result(task_dir, failure)
            artifact_store.write_log_artifact(task_dir, RUN_LOG, "\n".join(run_log_lines) + "\n")
            return 1
        capability_status = "ready" if not degradation_notes else "degraded"
        capability_summary = _capability_summary(capabilities, status=capability_status)

        if not capabilities.claude.available:
            failure = _preflight_failure(
                task_id=task_id,
                terminal_state=failure_terminal,
                summary="preflight failed: claude CLI is unavailable",
                verification_commands=verification_commands,
                runtime_mode=runtime_mode,
                degradation_notes=degradation_notes,
                capabilities=capabilities,
                remediation=capabilities.claude.remediation,
            )
            persist_result(task_dir, failure)
            artifact_store.write_log_artifact(task_dir, RUN_LOG, "\n".join(run_log_lines) + "\n")
            return 1
        if capabilities.claude.missing_flags:
            failure = _preflight_failure(
                task_id=task_id,
                terminal_state=failure_terminal,
                summary=(
                    "preflight failed: claude is missing required flag support "
                    f"({', '.join(capabilities.claude.missing_flags)})"
                ),
                verification_commands=verification_commands,
                runtime_mode=runtime_mode,
                degradation_notes=degradation_notes,
                capabilities=capabilities,
                remediation=capabilities.claude.remediation,
            )
            persist_result(task_dir, failure)
            artifact_store.write_log_artifact(task_dir, RUN_LOG, "\n".join(run_log_lines) + "\n")
            return 1

        if write_policy in {"read-only", "write-in-place"}:
            pre_full_snapshot = snapshot_workspace_tree(workdir, task_root=task_root)
            if write_policy == "write-in-place" and failure_terminal == "patch-ready":
                pre_run_copy_root = task_dir / "pre-run-workspace"
                copy_workspace_tree(workdir, pre_run_copy_root, task_root=task_dir)
            try:
                if runtime_mode == "git-aware":
                    pre_status = capture_git_status(workdir)
                    pre_git_head = capture_git_head(workdir)
                    pre_status_snapshot = snapshot_paths(
                        workdir,
                        changed_paths_from_git_status(pre_status),
                    )
            except RuntimeError:
                pre_status = None
                pre_git_head = None
                if runtime_mode == "git-aware":
                    degradation_notes.append(
                        "git status capture failed at runtime; falling back to filesystem snapshots."
                    )
            if write_policy == "write-in-place":
                baseline = capture_baseline(
                    workdir,
                    declared_files,
                    git_head=pre_git_head,
                    git_status=pre_status,
                    task_root=task_root,
                )
                if detect_unsafe_dirty_state(baseline):
                    failure = _attach_runtime_metadata(
                        task_failure_result(
                            task_id,
                            "inspection-required",
                            "write-in-place workspace is unsafe",
                            verification_commands=verification_commands,
                        ),
                        runtime_mode=runtime_mode,
                        degradation_notes=degradation_notes,
                        capability_summary=capability_summary,
                    )
                    persist_result(task_dir, failure)
                    artifact_store.write_log_artifact(task_dir, RUN_LOG, "\n".join(run_log_lines) + "\n")
                    return 1
        elif write_policy == "write-isolated":
            isolation_strategy = choose_isolation_strategy(
                git_available=capabilities.git.git_available,
                repo=capabilities.git.repo,
                worktree_usable=capabilities.git.worktree_usable,
            )
            if isolation_strategy == "git-worktree":
                target_workdir = create_isolated_worktree(workdir, task_dir, task_id)
            else:
                target_workdir = create_filesystem_copy(workdir, task_dir)
            run_log_lines.append(f"isolation_strategy={isolation_strategy}")
            run_log_lines.append(f"isolated_workdir={target_workdir}")

        prompt_name = DEFAULT_PROMPT_BY_TASK[request["task_type"]]
        prompt = load_prompt(prompt_name)
        prompt = f"{prompt}\n\n{render_request_markdown(request)}"
        schema_json = load_schema_text("task-result.schema.json")
        runtime_contract = (
            f"task_id={task_id}\n"
            f"task_type={request['task_type']}\n"
            f"write_policy={write_policy}\n"
            f"allowed_success_terminal={effective_success_terminal}\n"
            f"allowed_failure_terminal={failure_terminal}\n"
        )
        allow_subagents = bool(request.get("claude_role", {}).get("allow_subagents"))
        execution_mode = request["execution_mode"]
        agent_pack = select_agent_pack(
            request["task_type"],
            execution_mode,
            allow_subagents,
        )
        model = resolve_claude_model(request)
        command = build_command(
            workdir=str(target_workdir),
            prompt=prompt,
            schema_json=schema_json,
            runtime_contract=runtime_contract,
            agent_pack_json=serialize_agent_pack(agent_pack),
            model=model,
        )
        timeout_seconds = resolve_claude_timeout_seconds(request)
        if timeout_seconds is not None:
            run_log_lines.append(f"claude_timeout_seconds={timeout_seconds}")
            stdout, stderr = run_claude(command, timeout_seconds=timeout_seconds)
        else:
            stdout, stderr = run_claude(command)
        run_log_lines.append(stderr)
        try:
            result = parse_result(stdout)
        except json.JSONDecodeError:
            result = _repair_result_once(
                workdir=target_workdir,
                schema_json=schema_json,
                broken_output=stdout,
                model=model,
                timeout_seconds=timeout_seconds,
            )
        if not _looks_like_task_result(result):
            raw_result_output = _repair_source_output(result, stdout)
            result = _repair_result_once(
                workdir=target_workdir,
                schema_json=schema_json,
                broken_output=raw_result_output,
                model=model,
                timeout_seconds=timeout_seconds,
            )
            if not _looks_like_task_result(result):
                result = _normalize_unstructured_result(
                    task_id=task_id,
                    raw_output=raw_result_output,
                    verification_commands=verification_commands,
                )
        result = _attach_runtime_metadata(
            result,
            runtime_mode=runtime_mode,
            degradation_notes=degradation_notes,
            capability_summary=capability_summary,
        )

        if write_policy == "read-only":
            post_status = None
            post_git_head = pre_git_head
            if pre_status is not None:
                try:
                    post_status = capture_git_status(workdir)
                    post_git_head = capture_git_head(workdir)
                except RuntimeError:
                    post_status = None
                    degradation_notes.append(
                        "git status capture failed after Claude ran; validating with filesystem snapshots."
                    )
            if pre_status is not None and post_status is not None and post_git_head != pre_git_head:
                failure = _attach_runtime_metadata(
                    task_failure_result(
                        task_id,
                        "inspection-required",
                        "read-only task changed repository HEAD",
                        verification_commands=verification_commands,
                    ),
                    runtime_mode=runtime_mode,
                    degradation_notes=degradation_notes,
                    capability_summary=capability_summary,
                )
                persist_result(task_dir, failure)
                artifact_store.write_log_artifact(
                    task_dir,
                    RUN_LOG,
                    "\n".join(run_log_lines) + "\n",
                )
                return 1
            snapshot_source = pre_status_snapshot if pre_status is not None and post_status is not None else pre_full_snapshot
            read_only_changes = detect_post_run_changes_with_snapshots(
                workdir,
                pre_status if post_status is not None else None,
                snapshot_source,
                post_status,
                task_root=task_root,
            )
            if read_only_changes:
                failure = _attach_runtime_metadata(
                    task_failure_result(
                        task_id,
                        "inspection-required",
                        "read-only task modified target workspace",
                        changed_files=read_only_changes,
                        verification_commands=verification_commands,
                    ),
                    runtime_mode=runtime_mode,
                    degradation_notes=degradation_notes,
                    capability_summary=capability_summary,
                )
                persist_result(task_dir, failure)
                artifact_store.write_log_artifact(
                    task_dir,
                    RUN_LOG,
                    "\n".join(run_log_lines) + "\n",
                )
                return 1

        if write_policy == "write-in-place":
            post_status = None
            post_git_head = pre_git_head
            if pre_status is not None:
                try:
                    post_status = capture_git_status(workdir)
                    post_git_head = capture_git_head(workdir)
                except RuntimeError:
                    post_status = None
                    degradation_notes.append(
                        "git status capture failed after Claude ran; validating write-in-place changes with filesystem snapshots."
                    )
            if pre_status is not None and post_status is not None and post_git_head != pre_git_head:
                failure = _attach_runtime_metadata(
                    task_failure_result(
                        task_id,
                        "inspection-required",
                        "write-in-place task changed repository HEAD",
                        verification_commands=verification_commands,
                    ),
                    runtime_mode=runtime_mode,
                    degradation_notes=degradation_notes,
                    capability_summary=capability_summary,
                )
                persist_result(task_dir, failure)
                artifact_store.write_log_artifact(
                    task_dir,
                    RUN_LOG,
                    "\n".join(run_log_lines) + "\n",
                )
                return 1
            snapshot_source = pre_status_snapshot if pre_status is not None and post_status is not None else pre_full_snapshot
            changed_paths = detect_post_run_changes_with_snapshots(
                workdir,
                pre_status if post_status is not None else None,
                snapshot_source,
                post_status,
                task_root=task_root,
            )
            undeclared = undeclared_changed_files(changed_paths, declared_files)
            if undeclared:
                terminal = choose_failure_terminal_state([failure_terminal])
                failure = _attach_runtime_metadata(
                    task_failure_result(
                        task_id,
                        terminal,
                        "undeclared files were modified during write-in-place execution",
                        changed_files=undeclared,
                        verification_commands=verification_commands,
                    ),
                    runtime_mode=runtime_mode,
                    degradation_notes=degradation_notes,
                    capability_summary=capability_summary,
                )
                if terminal == "patch-ready" and runtime_mode == "filesystem-only" and pre_run_copy_root is not None:
                    try:
                        failure.update(
                            generate_file_change_set_from_workspace_pair(
                                pre_run_copy_root,
                                workdir,
                                task_dir,
                                undeclared,
                            )
                        )
                    except (RuntimeError, ValueError):
                        failure = _attach_runtime_metadata(
                            task_failure_result(
                                task_id,
                                "inspection-required",
                                "patch generation failed",
                                changed_files=undeclared,
                                verification_commands=verification_commands,
                            ),
                            runtime_mode=runtime_mode,
                            degradation_notes=degradation_notes,
                            capability_summary=capability_summary,
                        )
                elif terminal == "patch-ready" and pre_status is not None and post_status is not None:
                    try:
                        failure.update(generate_patch(workdir, task_dir, changed_paths))
                    except (RuntimeError, ValueError):
                        failure = _attach_runtime_metadata(
                            task_failure_result(
                                task_id,
                                "inspection-required",
                                "patch generation failed",
                                changed_files=undeclared,
                                verification_commands=verification_commands,
                            ),
                            runtime_mode=runtime_mode,
                            degradation_notes=degradation_notes,
                            capability_summary=capability_summary,
                        )
                elif terminal == "patch-ready" and pre_run_copy_root is not None:
                    try:
                        failure.update(
                            generate_patch_from_workspace_pair(
                                pre_run_copy_root,
                                workdir,
                                task_dir,
                                undeclared,
                            )
                        )
                    except (RuntimeError, ValueError):
                        failure = _attach_runtime_metadata(
                            task_failure_result(
                                task_id,
                                "inspection-required",
                                "patch generation failed",
                                changed_files=undeclared,
                                verification_commands=verification_commands,
                            ),
                            runtime_mode=runtime_mode,
                            degradation_notes=degradation_notes,
                            capability_summary=capability_summary,
                        )
                persist_result(task_dir, failure)
                artifact_store.write_log_artifact(
                    task_dir,
                    RUN_LOG,
                    "\n".join(run_log_lines) + "\n",
                )
                return 1

        if result.get("status") == "completed":
            if write_policy == "write-isolated" and effective_success_terminal == "commit-ready":
                result.update(create_task_owned_commit(target_workdir, declared_files, task_id))
            elif effective_success_terminal == "patch-ready" and write_policy == "write-isolated":
                if runtime_mode == "filesystem-only":
                    result.update(
                        generate_file_change_set_from_workspace_pair(
                            workdir,
                            target_workdir,
                            task_dir,
                            declared_files,
                        )
                    )
                elif isolation_strategy == "git-worktree":
                    result.update(generate_patch(target_workdir, task_dir, declared_files))
                else:
                    result.update(
                        generate_patch_from_workspace_pair(
                            workdir,
                            target_workdir,
                            task_dir,
                            declared_files,
                        )
                    )
            result["terminal_state"] = effective_success_terminal
            validate_result(
                result,
                write_policy=write_policy,
                allowed_terminal_state=effective_success_terminal,
            )
            persist_result(task_dir, result)
            artifact_store.write_log_artifact(
                task_dir,
                RUN_LOG,
                "\n".join(run_log_lines) + "\n",
            )
            return 0

        validate_terminal_state(result.get("terminal_state"), failure_terminal)
        validate_result(
            result,
            write_policy=write_policy,
            allowed_terminal_state=failure_terminal,
        )
        persist_result(task_dir, result)
        artifact_store.write_log_artifact(task_dir, RUN_LOG, "\n".join(run_log_lines) + "\n")
        return 1
    except (ValidationError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        if isinstance(exc, ClaudeTimeoutError):
            run_log_lines.append(str(exc))
            if exc.stdout:
                run_log_lines.append(exc.stdout)
            if exc.stderr:
                run_log_lines.append(exc.stderr)
        terminal = choose_failure_terminal_state([failure_terminal])
        metadata: dict | None = None
        if terminal == "patch-ready" and write_policy == "write-isolated" and declared_files:
            try:
                if runtime_mode == "filesystem-only":
                    metadata = generate_file_change_set_from_workspace_pair(
                        workdir,
                        target_workdir,
                        task_dir,
                        declared_files,
                    )
                elif isolation_strategy == "git-worktree":
                    metadata = generate_patch(target_workdir, task_dir, declared_files)
                elif isolation_strategy == "filesystem-copy":
                    metadata = generate_patch_from_workspace_pair(
                        workdir,
                        target_workdir,
                        task_dir,
                        declared_files,
                    )
            except (RuntimeError, ValueError):
                terminal = "inspection-required"
                metadata = None
        elif terminal == "patch-ready" and write_policy == "write-in-place" and pre_run_copy_root is not None:
            try:
                before_paths = snapshot_workspace_tree(pre_run_copy_root)
                current_paths = snapshot_workspace_tree(workdir, task_root=task_dir)
                changed_paths = sorted(set(before_paths) | set(current_paths))
                if runtime_mode == "filesystem-only":
                    metadata = generate_file_change_set_from_workspace_pair(
                        pre_run_copy_root,
                        workdir,
                        task_dir,
                        changed_paths,
                    )
                else:
                    metadata = generate_patch_from_workspace_pair(
                        pre_run_copy_root,
                        workdir,
                        task_dir,
                        changed_paths,
                    )
            except (RuntimeError, ValueError):
                terminal = "inspection-required"
                metadata = None
        failure = _attach_runtime_metadata(
            task_failure_result(
                task_id,
                terminal,
                str(exc),
                verification_commands=verification_commands,
                metadata=metadata,
            ),
            runtime_mode=runtime_mode,
            degradation_notes=degradation_notes,
            capability_summary=_capability_summary(
                capabilities,
                status="failed",
            ),
        )
        persist_result(task_dir, failure)
        artifact_store.write_log_artifact(task_dir, RUN_LOG, "\n".join(run_log_lines) + "\n")
        return 1


def _task_dir_for(task_root: Path, task_id: str) -> Path:
    return artifact_store.resolve_task_dir(task_root, task_id)


def handle_status(args: argparse.Namespace) -> int:
    task_dir = _task_dir_for(resolve_task_root(args.task_root), args.task)
    result = artifact_store.load_json_artifact(task_dir, RESULT_JSON)
    print(f"{args.task}: {result.get('terminal_state', 'unknown')}")
    return 0


def handle_open(args: argparse.Namespace) -> int:
    task_dir = _task_dir_for(resolve_task_root(args.task_root), args.task)
    print(str(task_dir.resolve()))
    return 0


def handle_cleanup(args: argparse.Namespace) -> int:
    task_dir = _task_dir_for(resolve_task_root(args.task_root), args.task)
    result = artifact_store.load_json_artifact(task_dir, RESULT_JSON)
    terminal_state = result.get("terminal_state")
    if terminal_state == "inspection-required":
        print("cleanup refused: inspection-required")
        return 1
    if terminal_state in {"patch-ready", "commit-ready"}:
        print(f"cleanup preserved artifacts for {terminal_state}")
        return 0
    artifact_store.cleanup_task_dir(task_dir)
    print(f"cleanup removed {task_dir}")
    return 0


def handle_doctor() -> int:
    report = run_doctor()
    print(render_doctor_report(report), end="")
    return 0 if report.ok else 1


def _format_version_source(repo: str) -> str:
    if repo == "legacy-install" or repo.startswith("github.com/"):
        return repo
    return f"github.com/{repo}"


def handle_version() -> int:
    try:
        discovery = discover_install_root(
            active_runtime_root=get_active_runtime_root(__file__),
            env=os.environ,
            os_name=os.name,
            reject_conflicting_roots=True,
        )
    except MultipleInstallRootsError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except InstallDiscoveryError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"ccollab {discovery.version}")
    print(f"install root: {discovery.install_root}")
    print(f"source: {_format_version_source(discovery.repo)}")
    print(f"channel: {discovery.channel}")
    return 0


def _print_update_result(result: UpdateResult) -> int:
    print(f"Current version: {result.current_version}")
    print(f"Latest version: {result.latest_version}")
    if result.status == "noop":
        print("ccollab is already up to date.")
        return 0
    for message in result.progress_messages:
        print(message)
    print(f"Updated ccollab to {result.latest_version}")
    return 0


def _print_update_failure(exc: UpdaterError) -> int:
    current_version = getattr(exc, "current_version", None)
    latest_version = getattr(exc, "latest_version", None)
    if isinstance(current_version, str) and isinstance(latest_version, str):
        print(f"Current version: {current_version}", file=sys.stderr)
        print(f"Latest version: {latest_version}", file=sys.stderr)
    for message in getattr(exc, "progress_messages", ()):
        print(message, file=sys.stderr)
    print(f"Update failed: {exc}", file=sys.stderr)
    rollback_succeeded = getattr(exc, "rollback_succeeded", None)
    if rollback_succeeded is True:
        print("Previous installation was restored.", file=sys.stderr)
    elif rollback_succeeded is False:
        print("Rollback failed; manual repair may be required.", file=sys.stderr)
    else:
        print("Existing installation was left unchanged.", file=sys.stderr)
    return 1


def handle_update() -> int:
    try:
        return _print_update_result(run_update())
    except CompatibilityError as exc:
        print(
            "This release requires a newer local runtime dependency. "
            "Fix the reported Python or Claude requirement, then retry.",
            file=sys.stderr,
        )
        print(str(exc), file=sys.stderr)
        return 1
    except (
        MultipleInstallRootsError,
        InstallDiscoveryError,
        BrokenLauncherError,
        GhPrerequisiteError,
        GhAuthenticationError,
        RepoAccessError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except UpdaterError as exc:
        return _print_update_failure(exc)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            return handle_run(args)
        if args.command == "status":
            return handle_status(args)
        if args.command == "open":
            return handle_open(args)
        if args.command == "cleanup":
            return handle_cleanup(args)
        if args.command == "doctor":
            return handle_doctor()
        if args.command == "version":
            return handle_version()
        if args.command == "update":
            return handle_update()
        parser.print_help()
        return 0
    except (FileNotFoundError, ValidationError, ValueError) as exc:
        print(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
