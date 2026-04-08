from __future__ import annotations

import json
import subprocess


RESEARCH_AGENT_PACK = {
    "researcher": {
        "description": "Gather evidence",
        "prompt": "Research the task and return findings.",
    },
    "synthesizer": {
        "description": "Combine findings",
        "prompt": "Synthesize findings into a concise result.",
    },
    "critic": {
        "description": "Challenge findings",
        "prompt": "Identify gaps and risks in the findings.",
    },
}

IMPLEMENTATION_AGENT_PACK = {
    "implementer": {
        "description": "Implementation worker",
        "prompt": "Implement only the declared task.",
    },
    "reviewer": {
        "description": "Implementation reviewer",
        "prompt": "Review implementation against acceptance criteria.",
    },
    "tester": {
        "description": "Verification worker",
        "prompt": "Run declared verification commands and summarize results.",
    },
}


def build_command(
    *,
    workdir: str,
    prompt: str,
    schema_json: str,
    runtime_contract: str,
    agent_pack_json: str | None,
    model: str | None = None,
) -> list[str]:
    cmd = [
        "claude",
        "-p",
    ]
    if model:
        cmd.extend(["--model", model])
    cmd.extend(
        [
        "--output-format",
        "json",
        "--json-schema",
        schema_json,
        "--add-dir",
        workdir,
        "--append-system-prompt",
        runtime_contract,
        ]
    )
    if agent_pack_json:
        cmd.extend(["--agents", agent_pack_json])
    cmd.append(prompt)
    return cmd


def serialize_agent_pack(agent_pack: dict | None) -> str | None:
    if not agent_pack:
        return None
    return json.dumps(agent_pack)


def select_agent_pack(task_type: str, execution_mode: str, allow_subagents: bool) -> dict | None:
    if not allow_subagents:
        return None
    if task_type == "research":
        return RESEARCH_AGENT_PACK
    if task_type == "implementation" and execution_mode == "multi-agent":
        return IMPLEMENTATION_AGENT_PACK
    return None


def run_claude(cmd: list[str]) -> tuple[str, str]:
    result = subprocess.run(cmd, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or "claude failed")
    return result.stdout, result.stderr
