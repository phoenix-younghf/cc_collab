from __future__ import annotations

import json


def render_result_markdown(payload: dict) -> str:
    changed_files = payload.get("changed_files", [])
    risks = payload.get("risks", [])
    degradation_notes = payload.get("degradation_notes", [])
    capability_summary = payload.get("capability_summary", {})
    lines = [
        f"# Result {payload.get('task_id', 'unknown')}",
        "",
        f"- Status: {payload.get('status', 'unknown')}",
        f"- Terminal State: {payload.get('terminal_state', 'unknown')}",
        f"- Runtime Mode: {payload.get('runtime_mode', 'unknown')}",
        f"- Artifact Type: {payload.get('artifact_type', 'unknown')}",
        f"- Summary: {payload.get('summary', '')}",
    ]
    remediation = payload.get("remediation")
    if remediation:
        lines.append(f"- Remediation: {remediation}")
    lines.extend(
        [
            "",
            "## Capability Summary",
            "```json",
            json.dumps(capability_summary, indent=2, sort_keys=True),
            "```",
            "",
            "## Degradation Notes",
        ]
    )
    lines.extend([f"- {item}" for item in degradation_notes] or ["- None"])
    lines.append("")
    lines.append("## Changed Files")
    lines.extend([f"- {item}" for item in changed_files] or ["- None"])
    lines.append("")
    lines.append("## Risks")
    lines.extend([f"- {item}" for item in risks] or ["- None"])
    return "\n".join(lines) + "\n"
