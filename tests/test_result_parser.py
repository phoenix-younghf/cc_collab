from __future__ import annotations

from unittest import TestCase

from runtime.result_parser import parse_result


class ResultParserTests(TestCase):
    def test_parse_result_unwraps_claude_envelope_with_fenced_json(self) -> None:
        stdout = """{"type":"result","subtype":"success","is_error":false,"result":"Intro text\\n\\n```json\\n{\\n  \\"task_id\\": \\"task-1\\",\\n  \\"status\\": \\"completed\\",\\n  \\"summary\\": \\"ok\\",\\n  \\"decisions\\": [],\\n  \\"changed_files\\": [],\\n  \\"verification\\": {\\n    \\"commands_run\\": [],\\n    \\"results\\": [],\\n    \\"all_passed\\": true\\n  },\\n  \\"open_questions\\": [],\\n  \\"risks\\": [],\\n  \\"follow_up_suggestions\\": [],\\n  \\"agent_usage\\": {\\n    \\"used_subagents\\": false,\\n    \\"notes\\": \\"\\"\\n  },\\n  \\"terminal_state\\": \\"archived\\"\\n}\\n```"}"""

        result = parse_result(stdout)

        self.assertEqual(result["task_id"], "task-1")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["terminal_state"], "archived")
