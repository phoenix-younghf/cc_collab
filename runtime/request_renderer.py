from __future__ import annotations


def render_request_markdown(payload: dict) -> str:
    acceptance = payload.get("inputs", {}).get("acceptance_criteria", [])
    constraints = payload.get("inputs", {}).get("constraints", [])
    files = payload.get("inputs", {}).get("files", [])
    lines = [
        f"# Task {payload['task_id']}",
        "",
        f"- Type: {payload.get('task_type', 'unknown')}",
        f"- Execution Mode: {payload.get('execution_mode', 'unknown')}",
        f"- Write Policy: {payload.get('write_policy', 'unknown')}",
        f"- Objective: {payload.get('objective', '')}",
        "",
        "## Context",
        payload.get("context_summary", ""),
        "",
        "## Files",
    ]
    lines.extend([f"- {item}" for item in files] or ["- None"])
    lines.append("")
    lines.append("## Constraints")
    lines.extend([f"- {item}" for item in constraints] or ["- None"])
    lines.append("")
    lines.append("## Acceptance Criteria")
    lines.extend([f"- {item}" for item in acceptance] or ["- None"])
    return "\n".join(lines) + "\n"
