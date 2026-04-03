from __future__ import annotations

from unittest import TestCase

from runtime.validators import ValidationError, validate_request, validate_result


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
