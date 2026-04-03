from __future__ import annotations


def render_result_markdown(payload: dict) -> str:
    changed_files = payload.get("changed_files", [])
    risks = payload.get("risks", [])
    lines = [
        f"# Result {payload.get('task_id', 'unknown')}",
        "",
        f"- Status: {payload.get('status', 'unknown')}",
        f"- Terminal State: {payload.get('terminal_state', 'unknown')}",
        f"- Summary: {payload.get('summary', '')}",
        "",
        "## Changed Files",
    ]
    lines.extend([f"- {item}" for item in changed_files] or ["- None"])
    lines.append("")
    lines.append("## Risks")
    lines.extend([f"- {item}" for item in risks] or ["- None"])
    return "\n".join(lines) + "\n"
