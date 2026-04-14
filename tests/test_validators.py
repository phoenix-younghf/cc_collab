from __future__ import annotations

import json
from pathlib import Path
from unittest import TestCase

from runtime.validators import ValidationError, validate_request, validate_result


def valid_result_payload(**overrides: object) -> dict:
    payload = {
        "task_id": "task-1",
        "status": "completed",
        "summary": "Done",
        "decisions": [],
        "changed_files": [],
        "verification": {"commands_run": [], "results": [], "all_passed": True},
        "open_questions": [],
        "risks": [],
        "follow_up_suggestions": [],
        "agent_usage": {"used_subagents": False, "notes": ""},
        "terminal_state": "archived",
        "runtime_mode": "filesystem-only",
        "artifact_type": "none",
        "capability_summary": {"status": "ready"},
        "degradation_notes": [],
    }
    payload.update(overrides)
    return payload


def valid_failed_result_payload(**overrides: object) -> dict:
    payload = valid_result_payload(
        status="failed",
        terminal_state="inspection-required",
        remediation="Install Claude CLI and re-run ccollab doctor.",
        capability_summary={"status": "preflight-failed"},
    )
    payload.update(overrides)
    return payload


class ValidatorTests(TestCase):
    def test_write_in_place_requires_explicit_files(self) -> None:
        request = {
            "task_id": "task-1",
            "task_type": "implementation",
            "execution_mode": "single-worker",
            "write_policy": "write-in-place",
            "origin": {"controller": "codex", "workflow_stage": "implementation"},
            "workdir": "/tmp/project",
            "objective": "Do work",
            "context_summary": "Summary",
            "inputs": {
                "files": [],
                "constraints": [],
                "acceptance_criteria": ["A"],
                "verification_commands": ["python3 -m unittest"],
                "closeout": {"on_success": "integrated", "on_failure": "patch-ready"},
            },
            "claude_role": {"mode": "implementation", "allow_subagents": False},
        }
        with self.assertRaises(ValidationError):
            validate_request(request)

    def test_invalid_closeout_mapping_is_rejected(self) -> None:
        request = {
            "task_id": "task-2",
            "task_type": "research",
            "execution_mode": "single-worker",
            "write_policy": "read-only",
            "origin": {"controller": "codex", "workflow_stage": "research"},
            "workdir": "/tmp/project",
            "objective": "Research",
            "context_summary": "Summary",
            "inputs": {
                "files": [],
                "constraints": [],
                "acceptance_criteria": ["A"],
                "verification_commands": [],
                "closeout": {"on_success": "integrated", "on_failure": "inspection-required"},
            },
            "claude_role": {"mode": "research", "allow_subagents": False},
        }
        with self.assertRaises(ValidationError):
            validate_request(request)

    def test_read_only_result_must_not_change_files(self) -> None:
        result = {
            "task_id": "task-1",
            "status": "completed",
            "summary": "Done",
            "decisions": [],
            "changed_files": ["src/a.py"],
            "verification": {"commands_run": [], "results": [], "all_passed": True},
            "open_questions": [],
            "risks": [],
            "follow_up_suggestions": [],
            "agent_usage": {"used_subagents": False, "notes": ""},
            "terminal_state": "archived",
        }
        with self.assertRaises(ValidationError):
            validate_result(result, write_policy="read-only", allowed_terminal_state="archived")

    def test_result_terminal_state_must_match_request(self) -> None:
        result = {
            "task_id": "task-1",
            "status": "completed",
            "summary": "Done",
            "decisions": [],
            "changed_files": [],
            "verification": {"commands_run": [], "results": [], "all_passed": True},
            "open_questions": [],
            "risks": [],
            "follow_up_suggestions": [],
            "agent_usage": {"used_subagents": False, "notes": ""},
            "terminal_state": "patch-ready",
        }
        with self.assertRaises(ValidationError):
            validate_result(result, write_policy="read-only", allowed_terminal_state="archived")

    def test_paths_must_not_traverse_or_start_with_dash(self) -> None:
        request = {
            "task_id": "task-4",
            "task_type": "implementation",
            "execution_mode": "single-worker",
            "write_policy": "write-in-place",
            "origin": {"controller": "codex", "workflow_stage": "implementation"},
            "workdir": "/tmp/project",
            "objective": "Do work",
            "context_summary": "Summary",
            "inputs": {
                "files": ["../secret.txt"],
                "constraints": [],
                "acceptance_criteria": ["A"],
                "verification_commands": ["python3 -m unittest"],
                "closeout": {"on_success": "integrated", "on_failure": "patch-ready"},
            },
            "claude_role": {"mode": "implementation", "allow_subagents": False},
        }
        with self.assertRaises(ValidationError):
            validate_request(request)

    def test_write_isolated_also_requires_explicit_files(self) -> None:
        request = {
            "task_id": "task-5",
            "task_type": "implementation",
            "execution_mode": "single-worker",
            "write_policy": "write-isolated",
            "origin": {"controller": "codex", "workflow_stage": "implementation"},
            "workdir": "/tmp/project",
            "objective": "Do work",
            "context_summary": "Summary",
            "inputs": {
                "files": [],
                "constraints": [],
                "acceptance_criteria": ["A"],
                "verification_commands": ["python3 -m unittest"],
                "closeout": {"on_success": "commit-ready", "on_failure": "inspection-required"},
            },
            "claude_role": {"mode": "implementation", "allow_subagents": False},
        }
        with self.assertRaises(ValidationError):
            validate_request(request)

    def test_timeout_seconds_must_be_positive_integer_when_present(self) -> None:
        request = {
            "task_id": "task-6",
            "task_type": "research",
            "execution_mode": "single-worker",
            "write_policy": "read-only",
            "origin": {"controller": "codex", "workflow_stage": "research"},
            "workdir": "/tmp/project",
            "objective": "Research",
            "context_summary": "Summary",
            "inputs": {
                "files": [],
                "constraints": [],
                "acceptance_criteria": ["A"],
                "verification_commands": [],
                "closeout": {"on_success": "archived", "on_failure": "inspection-required"},
            },
            "claude_role": {
                "mode": "research",
                "allow_subagents": False,
                "timeout_seconds": 0,
            },
        }
        with self.assertRaises(ValidationError):
            validate_request(request)


class ResultValidatorContractTests(TestCase):
    def test_task_result_schema_declares_runtime_metadata(self) -> None:
        schema = json.loads(
            (
                Path(__file__).resolve().parents[1]
                / "runtime"
                / "schemas"
                / "task-result.schema.json"
            ).read_text(encoding="utf-8")
        )
        self.assertIn("runtime_mode", schema["properties"])
        self.assertIn("artifact_type", schema["properties"])
        self.assertIn("capability_summary", schema["properties"])
        self.assertIn("degradation_notes", schema["properties"])
        self.assertIn("runtime_mode", schema["required"])
        self.assertIn("artifact_type", schema["required"])
        self.assertIn("capability_summary", schema["required"])
        self.assertIn("degradation_notes", schema["required"])
        self.assertIn("remediation", schema["properties"])

    def test_task_result_schema_accepts_runtime_metadata(self) -> None:
        payload = valid_result_payload(
            runtime_mode="filesystem-only",
            artifact_type="none",
            capability_summary={"mode": "filesystem-only"},
            degradation_notes=["Git not found; filesystem-only mode active"],
        )
        validate_result(payload, write_policy="read-only", allowed_terminal_state="archived")

    def test_task_result_schema_accepts_failed_result_remediation(self) -> None:
        payload = valid_failed_result_payload(
            remediation="Install Claude CLI and re-run ccollab doctor.",
            capability_summary={"status": "preflight-failed"},
        )
        validate_result(
            payload,
            write_policy="read-only",
            allowed_terminal_state="inspection-required",
        )

    def test_validator_rejects_missing_runtime_metadata(self) -> None:
        payload = valid_result_payload()
        del payload["runtime_mode"]
        with self.assertRaises(ValidationError):
            validate_result(payload, write_policy="read-only", allowed_terminal_state="archived")

    def test_validator_rejects_patch_artifact_for_read_only_result(self) -> None:
        payload = valid_result_payload(
            artifact_type="git-patch",
            patch_path="/tmp/changes.patch",
            apply_command="git apply /tmp/changes.patch",
        )
        with self.assertRaises(ValidationError):
            validate_result(payload, write_policy="read-only", allowed_terminal_state="archived")

    def test_validator_rejects_none_artifact_for_patch_ready_result(self) -> None:
        payload = valid_result_payload(
            terminal_state="patch-ready",
            artifact_type="none",
        )
        with self.assertRaises(ValidationError):
            validate_result(
                payload,
                write_policy="write-isolated",
                allowed_terminal_state="patch-ready",
            )

    def test_validator_requires_degradation_notes_field(self) -> None:
        payload = valid_result_payload()
        del payload["degradation_notes"]
        with self.assertRaises(ValidationError):
            validate_result(payload, write_policy="read-only", allowed_terminal_state="archived")

    def test_validator_rejects_runtime_mode_and_artifact_mismatch(self) -> None:
        payload = valid_result_payload(
            terminal_state="patch-ready",
            runtime_mode="filesystem-only",
            artifact_type="git-patch",
            patch_path="/tmp/changes.patch",
            apply_command="git apply /tmp/changes.patch",
        )
        with self.assertRaises(ValidationError):
            validate_result(
                payload,
                write_policy="write-isolated",
                allowed_terminal_state="patch-ready",
            )

    def test_validator_rejects_unresolved_runtime_mode(self) -> None:
        payload = valid_result_payload(runtime_mode="unresolved")
        with self.assertRaises(ValidationError):
            validate_result(payload, write_policy="read-only", allowed_terminal_state="archived")
