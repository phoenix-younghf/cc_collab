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
    if write_policy == "write-in-place":
        _require(files, "write-in-place requires explicit files")
    if write_policy in {"write-in-place", "write-isolated"}:
        for item in files:
            _require(isinstance(item, str) and item, "file path must be a non-empty string")
            candidate = PurePosixPath(item)
            _require(not candidate.is_absolute(), "file paths must be relative")
            _require(".." not in candidate.parts, "file paths must not traverse upward")
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


def validate_result(
    payload: dict,
    *,
    write_policy: str,
    allowed_terminal_state: str,
) -> None:
    _require(isinstance(payload, dict), "result must be a mapping")
    _require(payload.get("status") in RESULT_STATUSES, "invalid result status")
    _require(payload.get("terminal_state") in TERMINAL_STATES, "invalid terminal state")
    _require(
        payload.get("terminal_state") == allowed_terminal_state,
        "result terminal state mismatch",
    )
    _require(isinstance(payload.get("changed_files", []), list), "changed_files must be a list")
    if write_policy == "read-only":
        _require(not payload.get("changed_files"), "read-only tasks cannot change files")
    verification = payload.get("verification", {})
    _require(isinstance(verification.get("commands_run", []), list), "commands_run must be a list")
    _require(isinstance(verification.get("results", []), list), "results must be a list")
    agent_usage = payload.get("agent_usage", {})
    _require(isinstance(agent_usage, dict), "agent_usage must be a mapping")
