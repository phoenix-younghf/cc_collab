from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time


def _coerce_timeout_stream(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


class ClaudeTimeoutError(RuntimeError):
    def __init__(
        self,
        timeout_seconds: int,
        *,
        stdout: str | bytes | None = None,
        stderr: str | bytes | None = None,
    ) -> None:
        super().__init__(f"claude command timed out after {timeout_seconds} seconds")
        self.timeout_seconds = timeout_seconds
        self.stdout = _coerce_timeout_stream(stdout)
        self.stderr = _coerce_timeout_stream(stderr)


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


def resolve_claude_launcher() -> str:
    resolved = shutil.which("claude")
    if resolved:
        return resolved
    resolved = shutil.which("claude.cmd")
    if resolved:
        return resolved
    return "claude"


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
        resolve_claude_launcher(),
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


def _is_windows_batch_launcher(command: str) -> bool:
    if os.name != "nt":
        return False
    return command.lower().endswith((".cmd", ".bat"))


def _prepare_subprocess_command(cmd: list[str]) -> list[str]:
    if not cmd or not _is_windows_batch_launcher(cmd[0]):
        return cmd
    return ["cmd.exe", "/d", "/s", "/c", subprocess.list2cmdline(cmd)]


def _read_stream(stream: object, chunks: list[str]) -> None:
    if stream is None:
        return
    while True:
        chunk = stream.read(4096)
        if not chunk:
            break
        chunks.append(chunk)


def _start_reader_thread(stream: object, chunks: list[str]) -> threading.Thread:
    thread = threading.Thread(target=_read_stream, args=(stream, chunks), daemon=True)
    thread.start()
    return thread


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            text=True,
            capture_output=True,
            check=False,
        )
    else:
        process.kill()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _looks_like_complete_json(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    try:
        json.loads(stripped)
    except json.JSONDecodeError:
        return False
    return True


def run_claude(
    cmd: list[str],
    *,
    timeout_seconds: int | None = None,
) -> tuple[str, str]:
    process = subprocess.Popen(
        _prepare_subprocess_command(cmd),
        text=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=(
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            if _is_windows_batch_launcher(cmd[0])
            else 0
        ),
    )
    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    stdout_thread = _start_reader_thread(process.stdout, stdout_chunks)
    stderr_thread = _start_reader_thread(process.stderr, stderr_chunks)
    timed_out = False
    deadline = None if timeout_seconds is None else time.monotonic() + timeout_seconds

    while process.poll() is None:
        if deadline is not None and time.monotonic() >= deadline:
            timed_out = True
            _terminate_process_tree(process)
            break
        time.sleep(0.1)

    process.wait()
    stdout_thread.join(timeout=5)
    stderr_thread.join(timeout=5)
    stdout = "".join(stdout_chunks)
    stderr = "".join(stderr_chunks)

    if timed_out:
        if _looks_like_complete_json(stdout):
            return stdout, stderr
        raise ClaudeTimeoutError(int(timeout_seconds or 0), stdout=stdout, stderr=stderr)
    if process.returncode != 0:
        raise RuntimeError(stderr or "claude failed")
    return stdout, stderr
