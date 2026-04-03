from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from runtime import artifact_store
from runtime.claude_runner import build_command, run_claude, select_agent_pack, serialize_agent_pack
from runtime.closeout_manager import (
    build_patch_ready_metadata,
    choose_failure_terminal_state,
    generate_patch,
    validate_terminal_state,
)
from runtime.config import resolve_paths
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
from runtime.validators import ValidationError, validate_request, validate_result
from runtime.workspace_guard import (
    capture_baseline,
    capture_git_head,
    capture_git_status,
    detect_post_run_changes,
    detect_post_run_changes_with_snapshots,
    detect_unsafe_dirty_state,
    changed_paths_from_git_status,
    snapshot_paths,
    undeclared_changed_files,
)
from runtime.worktree_manager import create_isolated_worktree, create_task_owned_commit


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
    return parser


def resolve_task_root(override: str | None) -> Path:
    if override:
        return Path(override)
    return resolve_paths().task_root


def load_request(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _repair_result_once(
    *,
    workdir: Path,
    schema_json: str,
    broken_output: str,
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
    )
    repaired_stdout, _ = run_claude(repair_command)
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


def handle_run(args: argparse.Namespace) -> int:
    request_path = Path(args.request)
    task_root = resolve_task_root(args.task_root)
    request = load_request(request_path)
    validate_request(request)
    task_id = request["task_id"]
    task_dir = artifact_store.create_task_dir(task_root, task_id)
    artifact_store.write_json_artifact(task_dir, REQUEST_JSON, request)
    artifact_store.write_text_artifact(task_dir, REQUEST_MD, render_request_markdown(request))

    write_policy = request["write_policy"]
    success_terminal = request["inputs"]["closeout"]["on_success"]
    failure_terminal = request["inputs"]["closeout"]["on_failure"]
    verification_commands = request["inputs"].get("verification_commands", [])
    declared_files = request["inputs"].get("files", [])
    workdir = Path(request["workdir"])
    run_log_lines = [f"task_id={task_id}", f"write_policy={write_policy}"]

    baseline = None
    pre_status = None
    pre_status_snapshot: dict[str, str | None] = {}
    target_workdir = workdir
    try:
        if write_policy in {"read-only", "write-in-place"}:
            try:
                pre_status = capture_git_status(workdir)
                git_head = capture_git_head(workdir)
                pre_status_snapshot = snapshot_paths(
                    workdir,
                    changed_paths_from_git_status(pre_status),
                )
            except RuntimeError:
                pre_status = None
                git_head = None
            if write_policy == "read-only" and pre_status is None:
                failure = task_failure_result(
                    task_id,
                    "inspection-required",
                    "git status capture failed for read-only workspace",
                    verification_commands=verification_commands,
                )
                persist_result(task_dir, failure)
                artifact_store.write_log_artifact(task_dir, RUN_LOG, "\n".join(run_log_lines) + "\n")
                return 1
            if write_policy == "write-in-place":
                if pre_status is None:
                    failure = task_failure_result(
                        task_id,
                        "inspection-required",
                        "git baseline capture failed for write-in-place",
                        verification_commands=verification_commands,
                    )
                    persist_result(task_dir, failure)
                    artifact_store.write_log_artifact(task_dir, RUN_LOG, "\n".join(run_log_lines) + "\n")
                    return 1
                baseline = capture_baseline(workdir, declared_files, git_head=git_head, git_status=pre_status)
                if detect_unsafe_dirty_state(baseline):
                    terminal = choose_failure_terminal_state([failure_terminal])
                    failure = task_failure_result(
                        task_id,
                        terminal,
                        "write-in-place workspace is unsafe",
                        verification_commands=verification_commands,
                    )
                    if terminal == "patch-ready":
                        try:
                            failure.update(generate_patch(workdir, task_dir, declared_files))
                        except RuntimeError:
                            failure = task_failure_result(
                                task_id,
                                "inspection-required",
                                "patch generation failed",
                                verification_commands=verification_commands,
                            )
                    persist_result(task_dir, failure)
                    artifact_store.write_log_artifact(task_dir, RUN_LOG, "\n".join(run_log_lines) + "\n")
                    return 1
        elif write_policy == "write-isolated":
            target_workdir = create_isolated_worktree(workdir, task_dir, task_id)
            run_log_lines.append(f"isolated_workdir={target_workdir}")

        prompt_name = DEFAULT_PROMPT_BY_TASK[request["task_type"]]
        prompt = load_prompt(prompt_name)
        prompt = f"{prompt}\n\n{render_request_markdown(request)}"
        schema_json = load_schema_text("task-result.schema.json")
        runtime_contract = (
            f"task_id={task_id}\n"
            f"task_type={request['task_type']}\n"
            f"write_policy={write_policy}\n"
            f"allowed_success_terminal={success_terminal}\n"
            f"allowed_failure_terminal={failure_terminal}\n"
        )
        allow_subagents = bool(request.get("claude_role", {}).get("allow_subagents"))
        execution_mode = request["execution_mode"]
        agent_pack = select_agent_pack(
            request["task_type"],
            execution_mode,
            allow_subagents,
        )
        command = build_command(
            workdir=str(target_workdir),
            prompt=prompt,
            schema_json=schema_json,
            runtime_contract=runtime_contract,
            agent_pack_json=serialize_agent_pack(agent_pack),
        )
        stdout, stderr = run_claude(command)
        run_log_lines.append(stderr)
        try:
            result = parse_result(stdout)
        except json.JSONDecodeError:
            result = _repair_result_once(
                workdir=target_workdir,
                schema_json=schema_json,
                broken_output=stdout,
            )
        changed_files = result.get("changed_files", [])

        if write_policy == "read-only" and pre_status is not None:
            post_status = capture_git_status(workdir)
            read_only_changes = detect_post_run_changes_with_snapshots(
                workdir,
                pre_status,
                pre_status_snapshot,
                post_status,
            )
            if read_only_changes:
                failure = task_failure_result(
                    task_id,
                    "inspection-required",
                    "read-only task modified target workspace",
                    changed_files=read_only_changes,
                    verification_commands=verification_commands,
                )
                persist_result(task_dir, failure)
                artifact_store.write_log_artifact(
                    task_dir,
                    RUN_LOG,
                    "\n".join(run_log_lines) + "\n",
                )
                return 1

        if write_policy == "write-in-place" and pre_status is not None:
            post_status = capture_git_status(workdir)
            changed_paths = detect_post_run_changes_with_snapshots(
                workdir,
                pre_status,
                pre_status_snapshot,
                post_status,
            )
            undeclared = undeclared_changed_files(changed_paths, declared_files)
            if undeclared:
                terminal = choose_failure_terminal_state([failure_terminal])
                failure = task_failure_result(
                    task_id,
                    terminal,
                    "undeclared files were modified during write-in-place execution",
                    changed_files=undeclared,
                    verification_commands=verification_commands,
                )
                if terminal == "patch-ready":
                    try:
                        failure.update(generate_patch(workdir, task_dir, changed_paths))
                    except RuntimeError:
                        failure = task_failure_result(
                            task_id,
                            "inspection-required",
                            "patch generation failed",
                            changed_files=undeclared,
                            verification_commands=verification_commands,
                        )
                persist_result(task_dir, failure)
                artifact_store.write_log_artifact(
                    task_dir,
                    RUN_LOG,
                    "\n".join(run_log_lines) + "\n",
                )
                return 1

        if result.get("status") == "completed":
            if write_policy == "write-isolated" and success_terminal == "commit-ready":
                result.update(create_task_owned_commit(target_workdir, declared_files, task_id))
            elif success_terminal == "patch-ready":
                result.update(generate_patch(target_workdir, task_dir, declared_files))
            result["terminal_state"] = success_terminal
            validate_result(
                result,
                write_policy=write_policy,
                allowed_terminal_state=success_terminal,
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
        terminal = choose_failure_terminal_state([failure_terminal])
        metadata: dict | None = None
        if terminal == "patch-ready" and write_policy in {"write-in-place", "write-isolated"}:
            try:
                metadata = generate_patch(target_workdir, task_dir, declared_files)
            except RuntimeError:
                terminal = "inspection-required"
                metadata = None
        failure = task_failure_result(
            task_id,
            terminal,
            str(exc),
            verification_commands=verification_commands,
            metadata=metadata,
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
        parser.print_help()
        return 0
    except (FileNotFoundError, ValidationError, ValueError) as exc:
        print(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
