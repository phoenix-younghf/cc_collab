from __future__ import annotations

import re
from pathlib import PurePosixPath

from runtime.constants import (
    CLOSEOUT_MAPPING,
    EXECUTION_MODES,
    RESULT_STATUSES,
    TASK_TYPES,
    TERMINAL_STATES,
    WRITE_POLICIES,
)


class ValidationError(ValueError):
    """Raised when a request or result contract is invalid."""

TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
RUNTIME_MODES = ("git-aware", "filesystem-only")
ARTIFACT_TYPES = ("none", "git-patch", "file-change-set")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def validate_request(payload: dict) -> None:
    _require(isinstance(payload, dict), "request must be a mapping")
    _require(payload.get("task_type") in TASK_TYPES, "invalid task type")
    _require(payload.get("execution_mode") in EXECUTION_MODES, "invalid execution mode")
    write_policy = payload.get("write_policy")
    _require(write_policy in WRITE_POLICIES, "invalid write policy")
    closeout = payload.get("inputs", {}).get("closeout", {})
    _require(
        closeout.get("on_success") in CLOSEOUT_MAPPING[write_policy]["success"],
        "invalid success closeout",
    )
    _require(
        closeout.get("on_failure") in CLOSEOUT_MAPPING[write_policy]["failure"],
        "invalid failure closeout",
    )
    files = payload.get("inputs", {}).get("files", [])
    _require(isinstance(files, list), "inputs.files must be a list")
    if write_policy in {"write-in-place", "write-isolated"}:
        _require(files, "write policies require explicit files")
        for item in files:
            _require(isinstance(item, str) and item, "file path must be a non-empty string")
            candidate = PurePosixPath(item)
            _require(not candidate.is_absolute(), "file paths must be relative")
            _require(".." not in candidate.parts, "file paths must not traverse upward")
            _require("." not in candidate.parts, "file paths must not contain '.' segments")
            _require(not item.startswith("-"), "file paths must not start with '-'")
    _require(payload.get("task_id"), "task_id is required")
    _require(
        isinstance(payload.get("task_id"), str)
        and TASK_ID_PATTERN.fullmatch(payload["task_id"]) is not None,
        "task_id is invalid",
    )
    _require(payload.get("workdir"), "workdir is required")
    _require(payload.get("objective"), "objective is required")
    _require(payload.get("context_summary"), "context_summary is required")
    _require(payload.get("origin", {}).get("controller") == "codex", "controller must be codex")
    _require(
        isinstance(payload.get("inputs", {}).get("acceptance_criteria", []), list),
        "acceptance_criteria must be a list",
    )
    timeout_seconds = payload.get("claude_role", {}).get("timeout_seconds")
    if timeout_seconds is not None:
        _require(
            isinstance(timeout_seconds, int) and not isinstance(timeout_seconds, bool),
            "claude_role.timeout_seconds must be an integer",
        )
        _require(timeout_seconds > 0, "claude_role.timeout_seconds must be >= 1")


def validate_result(
    payload: dict,
    *,
    write_policy: str,
    allowed_terminal_state: str,
) -> None:
    _require(isinstance(payload, dict), "result must be a mapping")
    _require(payload.get("status") in RESULT_STATUSES, "invalid result status")
    _require(payload.get("terminal_state") in TERMINAL_STATES, "invalid terminal state")
    _require(payload.get("runtime_mode") in RUNTIME_MODES, "invalid runtime mode")
    _require(payload.get("artifact_type") in ARTIFACT_TYPES, "invalid artifact type")
    _require(isinstance(payload.get("capability_summary"), dict), "capability_summary must be a mapping")
    _require("degradation_notes" in payload, "degradation_notes is required")
    _require(isinstance(payload.get("degradation_notes", []), list), "degradation_notes must be a list")
    for item in payload.get("degradation_notes", []):
        _require(isinstance(item, str), "degradation note must be a string")
    if "remediation" in payload:
        _require(isinstance(payload.get("remediation"), str), "remediation must be a string")
    _require(
        payload.get("terminal_state") == allowed_terminal_state,
        "result terminal state mismatch",
    )
    _require(isinstance(payload.get("changed_files", []), list), "changed_files must be a list")
    if write_policy == "read-only":
        _require(not payload.get("changed_files"), "read-only tasks cannot change files")
        _require(payload.get("artifact_type") == "none", "read-only tasks cannot emit closeout artifacts")
    verification = payload.get("verification", {})
    _require(isinstance(verification.get("commands_run", []), list), "commands_run must be a list")
    _require(isinstance(verification.get("results", []), list), "results must be a list")
    agent_usage = payload.get("agent_usage", {})
    _require(isinstance(agent_usage, dict), "agent_usage must be a mapping")
    artifact_type = payload.get("artifact_type")
    terminal_state = payload.get("terminal_state")
    if terminal_state == "patch-ready":
        _require(
            artifact_type in {"git-patch", "file-change-set"},
            "patch-ready results require closeout artifact metadata",
        )
    elif artifact_type in {"git-patch", "file-change-set"}:
        _require(terminal_state == "patch-ready", "closeout artifacts require patch-ready terminal state")
    if artifact_type == "git-patch":
        _require(payload.get("runtime_mode") == "git-aware", "git-patch artifacts require git-aware runtime")
        _require(isinstance(payload.get("patch_path"), str), "git-patch results require patch_path")
        _require(isinstance(payload.get("apply_command"), str), "git-patch results require apply_command")
    if artifact_type == "file-change-set":
        _require(
            payload.get("runtime_mode") == "filesystem-only",
            "file-change-set artifacts require filesystem-only runtime",
        )
        manifest = payload.get("change_set_manifest")
        _require(isinstance(manifest, dict), "file-change-set results require change_set_manifest")
        _require(isinstance(manifest.get("entries", []), list), "change_set_manifest entries must be a list")
