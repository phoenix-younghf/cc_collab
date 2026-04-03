# CC Collab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Phase 1 `cc_collab` repository so Codex can delegate structured local tasks to Claude Code, validate the outcome, and install the workflow on another machine with minimal friction.

**Architecture:** The repo uses a thin shell entrypoint plus Python 3.11 standard-library modules under `runtime/` to validate manifests, render artifacts, invoke Claude Code, enforce write policies, and close tasks safely. Skills, install scripts, and docs live beside the runtime so the project remains self-contained and easy for other agents to install.

**Tech Stack:** Python 3.11 standard library, Bash install scripts, JSON schema files, Markdown docs, `unittest`, git, Claude Code CLI

---

## File Structure

**Create:**
- `README.md`
- `AGENTS.md`
- `bin/ccollab`
- `install/install-skill.sh`
- `install/install-bin.sh`
- `install/install-all.sh`
- `skill/delegate-to-claude-code/SKILL.md`
- `skill/delegate-to-claude-code/templates/task-routing.md`
- `skill/delegate-to-claude-code/templates/acceptance-checklist.md`
- `runtime/__init__.py`
- `runtime/constants.py`
- `runtime/cli.py`
- `runtime/config.py`
- `runtime/doctor.py`
- `runtime/schema_loader.py`
- `runtime/validators.py`
- `runtime/artifact_store.py`
- `runtime/request_renderer.py`
- `runtime/result_renderer.py`
- `runtime/prompt_loader.py`
- `runtime/claude_runner.py`
- `runtime/result_parser.py`
- `runtime/workspace_guard.py`
- `runtime/closeout_manager.py`
- `runtime/worktree_manager.py`
- `runtime/prompts/research.md`
- `runtime/prompts/review.md`
- `runtime/prompts/design-review.md`
- `runtime/prompts/plan-review.md`
- `runtime/prompts/implementation.md`
- `runtime/schemas/task-request.schema.json`
- `runtime/schemas/task-result.schema.json`
- `examples/research-task.json`
- `examples/review-task.json`
- `examples/implementation-task.json`
- `tasks/.gitkeep`
- `tests/test_cli.py`
- `tests/test_config.py`
- `tests/test_doctor.py`
- `tests/test_validators.py`
- `tests/test_artifact_store.py`
- `tests/test_request_renderer.py`
- `tests/test_result_renderer.py`
- `tests/test_prompt_loader.py`
- `tests/test_claude_runner.py`
- `tests/test_workspace_guard.py`
- `tests/test_closeout_manager.py`
- `tests/test_worktree_manager.py`
- `tests/test_status_tools.py`
- `tests/test_install_docs.py`

**Modify:**
- `docs/superpowers/specs/2026-04-03-cc-collab-design.md` only if implementation reveals a spec bug
- `docs/superpowers/plans/2026-04-03-cc-collab-implementation.md` only to check off progress if useful

## Shared Implementation Rules

- Use `unittest` consistently. Do not mix pytest-style free functions with `python3 -m unittest ...` commands.
- Every `run` path must create `request.json`, `request.md`, `result.json`, `result.md`, and `run.log` in the task directory.
- `status`, `open`, and `cleanup` are required CLI commands, not optional polish.
- `doctor` must check both binary presence and environment readiness.
- `write-in-place` must fail before execution if the declared file set is unsafe.
- `read-only` enforcement in Phase 1 is request contract + prompt contract + post-run diff detection + closeout failure.

### Task 1: Bootstrap The CLI Skeleton With Real `unittest` Coverage

**Files:**
- Create: `runtime/__init__.py`
- Create: `runtime/constants.py`
- Create: `runtime/cli.py`
- Create: `bin/ccollab`
- Create: `tests/test_cli.py`

- [ ] **Step 1: Write failing CLI smoke tests in `unittest` form**

```python
import subprocess
import sys
from pathlib import Path
from unittest import TestCase

ROOT = Path(__file__).resolve().parents[1]


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "runtime.cli", *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )


class CliSmokeTests(TestCase):
    def test_help_lists_core_commands(self) -> None:
        result = run_cli("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("run", result.stdout)
        self.assertIn("status", result.stdout)
        self.assertIn("cleanup", result.stdout)
        self.assertIn("doctor", result.stdout)

    def test_unknown_command_fails(self) -> None:
        result = run_cli("nope")
        self.assertNotEqual(result.returncode, 0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_cli -v`
Expected: FAIL because `runtime.cli` does not exist.

- [ ] **Step 3: Write the minimal CLI parser and shell entrypoint**

```python
# runtime/cli.py
from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ccollab")
    subparsers = parser.add_subparsers(dest="command")
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--request", required=True)
    run_parser.add_argument("--task-root")
    for name in ("status", "open", "cleanup"):
        command = subparsers.add_parser(name)
        command.add_argument("--task", required=True)
        command.add_argument("--task-root")
    subparsers.add_parser("doctor")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    parser.parse_args(argv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

```bash
# bin/ccollab
#!/usr/bin/env bash
set -euo pipefail
exec python3 -m runtime.cli "$@"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_cli -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add runtime/__init__.py runtime/constants.py runtime/cli.py bin/ccollab tests/test_cli.py
git commit -m "feat: add ccollab cli skeleton"
```

### Task 2: Add Path Resolution And Full `doctor` Coverage

**Files:**
- Create: `runtime/config.py`
- Create: `runtime/doctor.py`
- Create: `tests/test_config.py`
- Create: `tests/test_doctor.py`
- Modify: `runtime/cli.py`

- [ ] **Step 1: Write failing config and doctor tests**

```python
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from runtime.config import resolve_paths
from runtime.doctor import run_doctor


class ConfigTests(TestCase):
    def test_resolve_paths_prefers_codex_home_and_xdg(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "HOME": "/tmp/home",
                "CODEX_HOME": "/tmp/codex-home",
                "XDG_CONFIG_HOME": "/tmp/xdg-config",
            },
            clear=True,
        ):
            paths = resolve_paths()
        self.assertEqual(paths.skill_dir, Path("/tmp/codex-home/skills/delegate-to-claude-code"))
        self.assertEqual(paths.bin_path, Path("/tmp/home/.local/bin/ccollab"))
        self.assertEqual(paths.config_dir, Path("/tmp/xdg-config/cc_collab"))


class DoctorTests(TestCase):
    def test_doctor_fails_when_claude_missing(self) -> None:
        report = run_doctor(
            command_exists=lambda name: name != "claude",
            flag_probe=lambda _cmd: True,
            writable_probe=lambda _path: True,
            path_probe=lambda _value: True,
        )
        self.assertFalse(report.ok)

    def test_doctor_checks_required_claude_flags(self) -> None:
        report = run_doctor(
            command_exists=lambda _name: True,
            flag_probe=lambda flag: flag != "--json-schema",
            writable_probe=lambda _path: True,
            path_probe=lambda _value: True,
        )
        self.assertFalse(report.ok)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_config tests.test_doctor -v`
Expected: FAIL because `runtime.config` and `runtime.doctor` do not exist.

- [ ] **Step 3: Implement config resolution and comprehensive doctor checks**

```python
# runtime/config.py
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ResolvedPaths:
    skill_dir: Path
    bin_path: Path
    config_dir: Path
    task_root: Path


def resolve_paths() -> ResolvedPaths:
    home = Path(os.environ["HOME"])
    codex_home = Path(os.environ.get("CODEX_HOME", home / ".codex"))
    xdg_config_home = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
    return ResolvedPaths(
        skill_dir=codex_home / "skills" / "delegate-to-claude-code",
        bin_path=home / ".local" / "bin" / "ccollab",
        config_dir=xdg_config_home / "cc_collab",
        task_root=home / "workspace" / "cc_collab" / "tasks",
    )
```

```python
# runtime/doctor.py
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from runtime.config import resolve_paths

REQUIRED_CLAUDE_FLAGS = (
    "--print",
    "--output-format",
    "--json-schema",
    "--add-dir",
    "--append-system-prompt",
    "--agents",
)


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class DoctorReport:
    ok: bool
    checks: list[DoctorCheck]


def run_doctor(
    command_exists: Callable[[str], bool] | None = None,
    flag_probe: Callable[[str], bool] | None = None,
    writable_probe: Callable[[Path], bool] | None = None,
    path_probe: Callable[[str], bool] | None = None,
) -> DoctorReport:
    exists = command_exists or (lambda name: shutil.which(name) is not None)
    claude_exists = exists("claude")
    def default_flag_probe(flag: str) -> bool:
        if not claude_exists:
            return False
        help_text = subprocess.run(["claude", "--help"], text=True, capture_output=True, check=False).stdout
        return flag in help_text or (flag == "--print" and "-p" in help_text)
    flag_ok = flag_probe or default_flag_probe
    def default_writable_probe(path: Path) -> bool:
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".ccollab-write-test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return True
        except OSError:
            return False
    writable = writable_probe or default_writable_probe
    path_contains = path_probe or (lambda value: value in os.environ.get("PATH", ""))
    paths = resolve_paths()
    checks = [
        DoctorCheck("git", exists("git"), "git command available"),
        DoctorCheck("python3", exists("python3"), "python3 command available"),
        DoctorCheck("claude", exists("claude"), "claude command available"),
        *[DoctorCheck(flag, flag_ok(flag), f"claude supports {flag}") for flag in REQUIRED_CLAUDE_FLAGS],
        DoctorCheck("skill-dir", writable(paths.skill_dir.parent), "skill dir writable"),
        DoctorCheck("bin-dir", writable(paths.bin_path.parent), "bin dir writable"),
        DoctorCheck("config-dir", writable(paths.config_dir.parent), "config dir writable"),
        DoctorCheck("task-root", writable(paths.task_root.parent), "task root writable"),
        DoctorCheck("path", path_contains(str(paths.bin_path.parent)), "bin dir is on PATH"),
    ]
    return DoctorReport(ok=all(item.ok for item in checks), checks=checks)
```

- [ ] **Step 4: Wire `doctor` into the CLI and verify tests pass**

Run: `python3 -m unittest tests.test_config tests.test_doctor tests.test_cli -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add runtime/config.py runtime/doctor.py runtime/cli.py tests/test_config.py tests/test_doctor.py
git commit -m "feat: add path resolution and doctor checks"
```

### Task 3: Add Request And Result Schemas Plus Enforced Closeout Rules

**Files:**
- Create: `runtime/schemas/task-request.schema.json`
- Create: `runtime/schemas/task-result.schema.json`
- Create: `runtime/schema_loader.py`
- Create: `runtime/validators.py`
- Create: `examples/research-task.json`
- Create: `examples/review-task.json`
- Create: `examples/implementation-task.json`
- Create: `tests/test_validators.py`

- [ ] **Step 1: Write failing validator tests for mappings and invariants**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_validators -v`
Expected: FAIL because validators and schemas do not exist.

- [ ] **Step 3: Implement schema loading and validator enforcement**

```python
# runtime/validators.py
from __future__ import annotations

CLOSEOUT_MAPPING = {
    "read-only": {"success": {"archived"}, "failure": {"inspection-required"}},
    "write-in-place": {"success": {"integrated"}, "failure": {"patch-ready", "inspection-required"}},
    "write-isolated": {"success": {"commit-ready", "patch-ready"}, "failure": {"discarded", "inspection-required"}},
}


class ValidationError(ValueError):
    pass


def validate_request(payload: dict) -> None:
    write_policy = payload.get("write_policy")
    if write_policy not in CLOSEOUT_MAPPING:
        raise ValidationError("invalid write policy")
    closeout = payload.get("inputs", {}).get("closeout", {})
    success_state = closeout.get("on_success")
    failure_state = closeout.get("on_failure")
    if success_state not in CLOSEOUT_MAPPING[write_policy]["success"]:
        raise ValidationError("invalid success closeout")
    if failure_state not in CLOSEOUT_MAPPING[write_policy]["failure"]:
        raise ValidationError("invalid failure closeout")
    files = payload.get("inputs", {}).get("files", [])
    if write_policy == "write-in-place" and not files:
        raise ValidationError("write-in-place requires explicit files")


def validate_result(payload: dict, *, write_policy: str, allowed_terminal_state: str) -> None:
    if payload.get("terminal_state") != allowed_terminal_state:
        raise ValidationError("result terminal state mismatch")
    if write_policy == "read-only" and payload.get("changed_files"):
        raise ValidationError("read-only tasks cannot change files")
```

- [ ] **Step 4: Add full JSON schema files and example manifests, then run the suite**

Run: `python3 -m unittest tests.test_validators -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add runtime/schemas/task-request.schema.json runtime/schemas/task-result.schema.json runtime/schema_loader.py runtime/validators.py examples/research-task.json examples/review-task.json examples/implementation-task.json tests/test_validators.py
git commit -m "feat: add schemas and closeout validation"
```

### Task 4: Implement Artifact Storage, Markdown Rendering, And `run.log`

**Files:**
- Create: `runtime/artifact_store.py`
- Create: `runtime/request_renderer.py`
- Create: `runtime/result_renderer.py`
- Create: `tests/test_artifact_store.py`
- Create: `tests/test_request_renderer.py`
- Create: `tests/test_result_renderer.py`
- Modify: `runtime/cli.py`

- [ ] **Step 1: Write failing artifact and renderer tests**

```python
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from runtime.artifact_store import create_task_dir, write_json_artifact, write_log_artifact
from runtime.request_renderer import render_request_markdown
from runtime.result_renderer import render_result_markdown


class ArtifactStoreTests(TestCase):
    def test_task_dir_contains_json_and_log_artifacts(self) -> None:
        with TemporaryDirectory() as tmp:
            task_dir = create_task_dir(Path(tmp), "task-123")
            write_json_artifact(task_dir, "request.json", {"task_id": "task-123"})
            write_log_artifact(task_dir, "run.log", "hello\n")
            self.assertTrue((task_dir / "request.json").exists())
            self.assertTrue((task_dir / "run.log").exists())


class RendererTests(TestCase):
    def test_request_markdown_includes_objective(self) -> None:
        markdown = render_request_markdown({"task_id": "task-1", "objective": "Review plan"})
        self.assertIn("Review plan", markdown)

    def test_result_markdown_includes_summary(self) -> None:
        markdown = render_result_markdown({"task_id": "task-1", "summary": "Done"})
        self.assertIn("Done", markdown)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_artifact_store tests.test_request_renderer tests.test_result_renderer -v`
Expected: FAIL because artifact modules do not exist.

- [ ] **Step 3: Implement task directory creation and markdown/log artifact writers**

```python
# runtime/artifact_store.py
from __future__ import annotations

import json
from pathlib import Path


def create_task_dir(task_root: Path, task_id: str) -> Path:
    task_dir = task_root / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    return task_dir


def write_json_artifact(task_dir: Path, name: str, payload: dict) -> Path:
    target = task_dir / name
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return target


def write_text_artifact(task_dir: Path, name: str, content: str) -> Path:
    target = task_dir / name
    target.write_text(content, encoding="utf-8")
    return target


def write_log_artifact(task_dir: Path, name: str, content: str) -> Path:
    return write_text_artifact(task_dir, name, content)
```

- [ ] **Step 4: Wire `run` to persist `request.json`, `request.md`, placeholder `result.json`, placeholder `result.md`, and `run.log`**

Run: `python3 -m unittest tests.test_artifact_store tests.test_request_renderer tests.test_result_renderer tests.test_cli -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add runtime/artifact_store.py runtime/request_renderer.py runtime/result_renderer.py runtime/cli.py tests/test_artifact_store.py tests/test_request_renderer.py tests/test_result_renderer.py
git commit -m "feat: add task artifact and markdown rendering"
```

### Task 5: Implement Prompt Loading, Claude Runner, And Read-Only Execution

**Files:**
- Create: `runtime/prompt_loader.py`
- Create: `runtime/claude_runner.py`
- Create: `runtime/result_parser.py`
- Create: `runtime/prompts/research.md`
- Create: `runtime/prompts/review.md`
- Create: `runtime/prompts/design-review.md`
- Create: `runtime/prompts/plan-review.md`
- Create: `tests/test_prompt_loader.py`
- Create: `tests/test_claude_runner.py`
- Modify: `runtime/cli.py`

- [ ] **Step 1: Write failing prompt and runner tests**

```python
from unittest import TestCase
from unittest.mock import patch

from runtime.claude_runner import RESEARCH_AGENT_PACK, build_command, run_claude
from runtime.prompt_loader import load_prompt


class PromptLoaderTests(TestCase):
    def test_load_prompt_reads_named_prompt(self) -> None:
        prompt = load_prompt("research")
        self.assertIn("research", prompt.lower())


class ClaudeRunnerTests(TestCase):
    def test_build_command_includes_schema_and_add_dir(self) -> None:
        cmd = build_command(
            workdir="/tmp/project",
            prompt="Do work",
            schema_json='{"type":"object"}',
            runtime_contract="contract",
            agent_pack_json='{"researcher": {}}',
        )
        self.assertIn("--json-schema", cmd)
        self.assertIn("--agents", cmd)
        self.assertIn("/tmp/project", cmd)

    def test_research_agent_pack_contains_required_roles(self) -> None:
        self.assertIn("researcher", RESEARCH_AGENT_PACK)
        self.assertIn("synthesizer", RESEARCH_AGENT_PACK)
        self.assertIn("critic", RESEARCH_AGENT_PACK)

    @patch("runtime.claude_runner.subprocess.run")
    def test_run_claude_returns_stdout_and_log(self, mock_run) -> None:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = '{"status":"completed"}'
        mock_run.return_value.stderr = ""
        stdout, stderr = run_claude(["claude", "-p"])
        self.assertEqual(stdout, '{"status":"completed"}')
        self.assertEqual(stderr, "")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_prompt_loader tests.test_claude_runner -v`
Expected: FAIL because prompt loader and runner modules do not exist.

- [ ] **Step 3: Implement prompt loading, command building, and read-only execution plumbing**

```python
# runtime/claude_runner.py
from __future__ import annotations

import subprocess

RESEARCH_AGENT_PACK = {
    "researcher": {"description": "Gather evidence", "prompt": "Research the task and return findings."},
    "synthesizer": {"description": "Combine findings", "prompt": "Synthesize findings into a concise result."},
    "critic": {"description": "Challenge findings", "prompt": "Identify gaps and risks in the findings."},
}


def build_command(*, workdir: str, prompt: str, schema_json: str, runtime_contract: str, agent_pack_json: str | None) -> list[str]:
    cmd = [
        "claude",
        "-p",
        "--output-format",
        "json",
        "--json-schema",
        schema_json,
        "--add-dir",
        workdir,
        "--append-system-prompt",
        runtime_contract,
    ]
    if agent_pack_json:
        cmd.extend(["--agents", agent_pack_json])
    cmd.append(prompt)
    return cmd


def run_claude(cmd: list[str]) -> tuple[str, str]:
    result = subprocess.run(cmd, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or "claude failed")
    return result.stdout, result.stderr
```

- [ ] **Step 4: Wire read-only `run` through prompt loading, runner, parser, result validation, and artifact writes**

Add one more read-only safety test before implementation:

```python
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from runtime.cli import main


@patch("runtime.cli.detect_post_run_changes", return_value=["src/a.py"])
def test_read_only_change_detection_forces_inspection_required(self, _mock_changes) -> None:
@patch("runtime.claude_runner.run_claude", return_value=('{"task_id":"task-1","status":"completed","summary":"ok","decisions":[],"changed_files":[],"verification":{"commands_run":[],"results":[],"all_passed":true},"open_questions":[],"risks":[],"follow_up_suggestions":[],"agent_usage":{"used_subagents":false,"notes":""},"terminal_state":"archived"}', ""))
def test_read_only_change_detection_forces_inspection_required(self, _mock_run, _mock_changes) -> None:
    with TemporaryDirectory() as tmp:
        request_path = Path(tmp) / "request.json"
        request_path.write_text('{"task_id":"task-1","task_type":"research","execution_mode":"single-worker","write_policy":"read-only","origin":{"controller":"codex","workflow_stage":"research"},"workdir":"/tmp/project","objective":"Research","context_summary":"Summary","inputs":{"files":[],"constraints":[],"acceptance_criteria":["A"],"verification_commands":[],"closeout":{"on_success":"archived","on_failure":"inspection-required"}},"claude_role":{"mode":"research","allow_subagents":false}}', encoding="utf-8")
        exit_code = main(["run", "--request", str(request_path), "--task-root", tmp])
        result = json.loads((Path(tmp) / "task-1" / "result.json").read_text(encoding="utf-8"))
        self.assertNotEqual(exit_code, 0)
        self.assertEqual(result["terminal_state"], "inspection-required")
```

Then wire read-only `run` through prompt loading, runner, parser, result validation, post-run diff detection against the target workdir, and artifact writes.

Phase 1 diff rule:

- for git-backed workdirs, capture `git status --porcelain=v1 --untracked-files=all` before and after execution
- if the post-run status differs from the pre-run status during a `read-only` task, force `inspection-required`

Run: `python3 -m unittest tests.test_prompt_loader tests.test_claude_runner tests.test_validators tests.test_cli -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add runtime/prompt_loader.py runtime/claude_runner.py runtime/result_parser.py runtime/prompts/research.md runtime/prompts/review.md runtime/prompts/design-review.md runtime/prompts/plan-review.md runtime/cli.py tests/test_prompt_loader.py tests/test_claude_runner.py
git commit -m "feat: add read-only Claude execution flow"
```

### Task 6: Add Safe Baseline Capture For `write-in-place`

**Files:**
- Create: `runtime/workspace_guard.py`
- Create: `tests/test_workspace_guard.py`
- Modify: `runtime/cli.py`

- [ ] **Step 1: Write failing baseline and dirty-state tests**

```python
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from runtime.workspace_guard import capture_baseline, detect_unsafe_dirty_state


class WorkspaceGuardTests(TestCase):
    def test_capture_baseline_records_git_and_hashes(self) -> None:
        with TemporaryDirectory() as tmp:
            project = Path(tmp)
            target = project / "src.txt"
            target.write_text("hello", encoding="utf-8")
            baseline = capture_baseline(project, ["src.txt"], git_head="abc123", git_status="")
            self.assertEqual(baseline.git_head, "abc123")
            self.assertEqual(baseline.files[0].relative_path, "src.txt")
            self.assertTrue(baseline.files[0].sha256)

    def test_declared_dirty_file_is_unsafe(self) -> None:
        with TemporaryDirectory() as tmp:
            project = Path(tmp)
            target = project / "src.txt"
            target.write_text("hello", encoding="utf-8")
            baseline = capture_baseline(project, ["src.txt"], git_head="abc123", git_status=" M src.txt")
            self.assertTrue(detect_unsafe_dirty_state(baseline))

    def test_missing_git_capture_is_fatal(self) -> None:
        with self.assertRaises(RuntimeError):
            capture_baseline(Path("/tmp/project"), ["src.txt"], git_head=None, git_status=None)  # type: ignore[arg-type]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_workspace_guard -v`
Expected: FAIL because `runtime.workspace_guard` does not exist.

- [ ] **Step 3: Implement baseline capture and unsafe-state detection**

```python
# runtime/workspace_guard.py
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FileBaseline:
    relative_path: str
    exists: bool
    sha256: str | None


@dataclass(frozen=True)
class WorkspaceBaseline:
    git_head: str | None
    git_status: str
    files: list[FileBaseline]


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def capture_baseline(project_root: Path, files: list[str], *, git_head: str | None, git_status: str) -> WorkspaceBaseline:
    if git_status is None:
        raise RuntimeError("git status capture failed")
    captured = []
    for relative_path in files:
        full = project_root / relative_path
        captured.append(
            FileBaseline(
                relative_path=relative_path,
                exists=full.exists(),
                sha256=sha256_file(full) if full.exists() else None,
            )
        )
    return WorkspaceBaseline(git_head=git_head, git_status=git_status, files=captured)


def detect_unsafe_dirty_state(baseline: WorkspaceBaseline) -> bool:
    declared = {item.relative_path for item in baseline.files}
    return any(line[3:] in declared for line in baseline.git_status.splitlines() if len(line) >= 4)
```

- [ ] **Step 4: Wire `write-in-place` preflight checks into `run`**

Make this step concrete in `runtime.cli`:

- capture git head with `git rev-parse HEAD`
- capture dirty state with `git status --porcelain=v1 --untracked-files=all`
- fail closed if either capture fails for a git-backed workspace
- reject `write-in-place` before Claude runs when declared files are unsafe
- after Claude runs, compare the changed file set against the declared file set
- if undeclared files were modified, fail closeout and use the allowed failure terminal state instead of `integrated`

Run: `python3 -m unittest tests.test_workspace_guard tests.test_cli -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add runtime/workspace_guard.py runtime/cli.py tests/test_workspace_guard.py
git commit -m "feat: add write-in-place baseline capture"
```

### Task 7: Add Closeout Enforcement And Structured Repair Handling

**Files:**
- Create: `runtime/closeout_manager.py`
- Create: `tests/test_closeout_manager.py`
- Modify: `runtime/cli.py`
- Modify: `runtime/result_parser.py`

- [ ] **Step 1: Write failing closeout and repair tests**

```python
from unittest import TestCase

from runtime.closeout_manager import build_patch_ready_metadata, choose_failure_terminal_state, validate_terminal_state


class CloseoutManagerTests(TestCase):
    def test_failure_prefers_patch_ready_when_allowed(self) -> None:
        state = choose_failure_terminal_state(["patch-ready", "inspection-required"])
        self.assertEqual(state, "patch-ready")

    def test_terminal_state_must_match_allowed_value(self) -> None:
        with self.assertRaises(ValueError):
            validate_terminal_state("archived", "patch-ready")

    def test_patch_ready_metadata_uses_changes_patch(self) -> None:
        metadata = build_patch_ready_metadata("/tmp/task-1")
        self.assertEqual(metadata["patch_path"], "/tmp/task-1/changes.patch")
        self.assertIn("git apply", metadata["apply_command"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_closeout_manager -v`
Expected: FAIL because closeout manager does not exist.

- [ ] **Step 3: Implement closeout helpers and one repair-attempt path**

```python
# runtime/closeout_manager.py
from __future__ import annotations


def choose_failure_terminal_state(allowed: list[str]) -> str:
    return "patch-ready" if "patch-ready" in allowed else "inspection-required"


def validate_terminal_state(actual: str, expected: str) -> None:
    if actual != expected:
        raise ValueError("terminal state mismatch")


def build_patch_ready_metadata(task_dir: str) -> dict[str, str]:
    patch_path = f"{task_dir}/changes.patch"
    return {
        "patch_path": patch_path,
        "apply_command": f"git apply {patch_path}",
    }
```

- [ ] **Step 4: Wire result validation failures to one repair attempt, then `inspection-required`**

Also wire failure closeout so `patch-ready` produces `tasks/<task-id>/changes.patch` plus patch metadata in `result.json`.

Make patch creation concrete for Phase 1:

- require a git-backed workspace for automatic patch generation
- ensure declared new files appear in the diff with `git -C <workdir> add -N <declared files...>`
- generate the patch with `git -C <workdir> diff --binary -- <declared files...> > <task-dir>/changes.patch`
- if patch generation fails, downgrade closeout to `inspection-required`

Run: `python3 -m unittest tests.test_closeout_manager tests.test_validators tests.test_cli -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add runtime/closeout_manager.py runtime/result_parser.py runtime/cli.py tests/test_closeout_manager.py
git commit -m "feat: add closeout enforcement and repair flow"
```

### Task 8: Add Isolated Worktree And Multi-Agent Execution Support

**Files:**
- Create: `runtime/worktree_manager.py`
- Create: `runtime/prompts/implementation.md`
- Create: `tests/test_worktree_manager.py`
- Modify: `runtime/claude_runner.py`
- Modify: `runtime/cli.py`

- [ ] **Step 1: Write failing worktree and agent-pack tests**

```python
from unittest import TestCase

from runtime.claude_runner import IMPLEMENTATION_AGENT_PACK
from runtime.worktree_manager import build_commit_ready_metadata, build_worktree_add_command


class WorktreeManagerTests(TestCase):
    def test_build_worktree_add_command_uses_repo_and_branch(self) -> None:
        cmd = build_worktree_add_command("feature-1", "/tmp/repo", "/tmp/wt")
        self.assertEqual(cmd[:4], ["git", "-C", "/tmp/repo", "worktree"])
        self.assertIn("feature-1", cmd)

    def test_implementation_agent_pack_has_required_roles(self) -> None:
        self.assertIn("implementer", IMPLEMENTATION_AGENT_PACK)
        self.assertIn("reviewer", IMPLEMENTATION_AGENT_PACK)
        self.assertIn("tester", IMPLEMENTATION_AGENT_PACK)

    def test_commit_ready_metadata_records_path_and_commit(self) -> None:
        metadata = build_commit_ready_metadata("/tmp/wt", ["abc123"])
        self.assertEqual(metadata["isolated_path"], "/tmp/wt")
        self.assertEqual(metadata["commit_shas"], ["abc123"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_worktree_manager -v`
Expected: FAIL because worktree manager does not exist.

- [ ] **Step 3: Implement worktree command building and implementation agent pack**

```python
# runtime/worktree_manager.py
from __future__ import annotations


def build_worktree_add_command(branch_name: str, repo_root: str, worktree_path: str) -> list[str]:
    return ["git", "-C", repo_root, "worktree", "add", worktree_path, "-b", branch_name]


def build_commit_ready_metadata(isolated_path: str, commit_shas: list[str]) -> dict[str, object]:
    return {
        "isolated_path": isolated_path,
        "commit_shas": commit_shas,
    }
```

```python
# runtime/claude_runner.py
IMPLEMENTATION_AGENT_PACK = {
    "implementer": {"description": "Implementation worker", "prompt": "Implement only the declared task."},
    "reviewer": {"description": "Implementation reviewer", "prompt": "Review implementation against acceptance criteria."},
    "tester": {"description": "Verification worker", "prompt": "Run declared verification commands and summarize results."},
}
```

- [ ] **Step 4: Wire `write-isolated` and `multi-agent` request handling into `run`**

This step must also create task-owned commit metadata for `commit-ready` and store it in `result.json`.

Make commit creation concrete for Phase 1:

- after successful isolated execution, run `git -C <isolated-path> add <declared files...>`
- create a task-owned commit with `git -C <isolated-path> commit -m "ccollab: <task-id>"`
- collect commit SHA data with `git -C <isolated-path> rev-parse HEAD`
- store `isolated_path` and `commit_shas` in `result.json`

Run: `python3 -m unittest tests.test_worktree_manager tests.test_claude_runner tests.test_cli -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add runtime/worktree_manager.py runtime/prompts/implementation.md runtime/claude_runner.py runtime/cli.py tests/test_worktree_manager.py
git commit -m "feat: add isolated and multi-agent execution"
```

### Task 9: Implement `status`, `open`, And `cleanup` With Guardrails

**Files:**
- Create: `tests/test_status_tools.py`
- Modify: `runtime/cli.py`
- Modify: `runtime/artifact_store.py`

- [ ] **Step 1: Write failing tests for status/open/cleanup**

```python
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from runtime.cli import main


class StatusToolTests(TestCase):
    def test_status_reads_terminal_state_from_result_json(self) -> None:
        with TemporaryDirectory() as tmp:
            task_dir = Path(tmp) / "task-1"
            task_dir.mkdir(parents=True)
            (task_dir / "result.json").write_text('{"terminal_state": "archived"}', encoding="utf-8")
            exit_code = main(["status", "--task", "task-1", "--task-root", tmp])
            self.assertEqual(exit_code, 0)

    def test_cleanup_refuses_inspection_required(self) -> None:
        with TemporaryDirectory() as tmp:
            task_dir = Path(tmp) / "task-2"
            task_dir.mkdir(parents=True)
            (task_dir / "result.json").write_text('{"terminal_state": "inspection-required"}', encoding="utf-8")
            exit_code = main(["cleanup", "--task", "task-2", "--task-root", tmp])
            self.assertNotEqual(exit_code, 0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_status_tools -v`
Expected: FAIL because the command handlers do not exist.

- [ ] **Step 3: Implement task inspection helpers and guarded cleanup**

```python
# runtime/cli.py
# status: read result.json and print task id + terminal state
# open: print absolute task directory path
# cleanup: refuse inspection-required, preserve `changes.patch` and commit metadata, and never touch project workdirs
```

- [ ] **Step 4: Run status tool tests and the broader CLI suite**

Run: `python3 -m unittest tests.test_status_tools tests.test_cli -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add runtime/cli.py runtime/artifact_store.py tests/test_status_tools.py
git commit -m "feat: add task status and cleanup commands"
```

### Task 10: Package The Skill, Install Scripts, And Agent-Friendly Docs

**Files:**
- Create: `skill/delegate-to-claude-code/SKILL.md`
- Create: `skill/delegate-to-claude-code/templates/task-routing.md`
- Create: `skill/delegate-to-claude-code/templates/acceptance-checklist.md`
- Create: `install/install-skill.sh`
- Create: `install/install-bin.sh`
- Create: `install/install-all.sh`
- Create: `README.md`
- Create: `AGENTS.md`
- Create: `tests/test_install_docs.py`

- [ ] **Step 1: Write failing install/docs tests**

```python
from pathlib import Path
from unittest import TestCase


class InstallDocsTests(TestCase):
    def test_readme_starts_with_quick_install(self) -> None:
        readme = Path("README.md").read_text(encoding="utf-8")
        self.assertIn("## Quick Install", readme)
        self.assertIn("./install/install-all.sh", readme)

    def test_agents_doc_mentions_install_and_doctor(self) -> None:
        agents = Path("AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("install/install-all.sh", agents)
        self.assertIn("ccollab doctor", agents)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_install_docs -v`
Expected: FAIL because docs and install scripts do not exist.

- [ ] **Step 3: Implement idempotent install scripts and quick-start docs**

```bash
# install/install-skill.sh
#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CODEX_HOME_DIR="${CODEX_HOME:-$HOME/.codex}"
TARGET="$CODEX_HOME_DIR/skills/delegate-to-claude-code"
mkdir -p "$(dirname "$TARGET")"
ln -sfn "$ROOT/skill/delegate-to-claude-code" "$TARGET"
```

```bash
# install/install-bin.sh
#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="$HOME/.local/bin/ccollab"
mkdir -p "$(dirname "$TARGET")"
ln -sfn "$ROOT/bin/ccollab" "$TARGET"
```

```bash
# install/install-all.sh
#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
"$ROOT/install/install-skill.sh"
"$ROOT/install/install-bin.sh"
python3 -m runtime.cli doctor
```

```markdown
# README.md
## Quick Install

git clone <repo> ~/workspace/cc_collab
cd ~/workspace/cc_collab
./install/install-all.sh
ccollab doctor
```

- [ ] **Step 4: Verify install/doc tests and run a manual doctor check**

Run: `python3 -m unittest tests.test_install_docs -v`
Expected: PASS.
Run: `python3 -m runtime.cli doctor`
Expected: PASS or clear actionable findings.

- [ ] **Step 5: Commit**

```bash
git add skill/delegate-to-claude-code/SKILL.md skill/delegate-to-claude-code/templates/task-routing.md skill/delegate-to-claude-code/templates/acceptance-checklist.md install/install-skill.sh install/install-bin.sh install/install-all.sh README.md AGENTS.md tests/test_install_docs.py
git commit -m "feat: add skill packaging and installation flow"
```

### Task 11: Run End-To-End Dry Runs And Final Verification

**Files:**
- Modify: `tests/test_cli.py`
- Modify: `tests/test_claude_runner.py`
- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `bin/ccollab`
- Modify: runtime files as needed

- [ ] **Step 1: Write a failing dry-run integration test for the full `run` flow**

```python
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from runtime.cli import main


class CliIntegrationTests(TestCase):
    @patch(
        "runtime.claude_runner.run_claude",
        return_value=(
            '{"task_id":"task-1","status":"completed","summary":"ok","decisions":[],"changed_files":[],"verification":{"commands_run":[],"results":[],"all_passed":true},"open_questions":[],"risks":[],"follow_up_suggestions":[],"agent_usage":{"used_subagents":false,"notes":""},"terminal_state":"archived"}',
            "",
        ),
    )
    def test_run_writes_all_required_artifacts(self, _mock_run) -> None:
        with TemporaryDirectory() as tmp:
            request = Path(tmp) / "request.json"
            request.write_text('{"task_id":"task-1","task_type":"research","execution_mode":"single-worker","write_policy":"read-only","origin":{"controller":"codex","workflow_stage":"research"},"workdir":"/tmp/project","objective":"Research","context_summary":"Summary","inputs":{"files":[],"constraints":[],"acceptance_criteria":["A"],"verification_commands":[],"closeout":{"on_success":"archived","on_failure":"inspection-required"}},"claude_role":{"mode":"research","allow_subagents":false}}', encoding="utf-8")
            exit_code = main(["run", "--request", str(request), "--task-root", tmp])
            task_dir = Path(tmp) / "task-1"
            self.assertEqual(exit_code, 0)
            self.assertTrue((task_dir / "request.json").exists())
            self.assertTrue((task_dir / "request.md").exists())
            self.assertTrue((task_dir / "result.json").exists())
            self.assertTrue((task_dir / "result.md").exists())
            self.assertTrue((task_dir / "run.log").exists())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_cli -v`
Expected: FAIL because the full `run` flow is not complete yet.

- [ ] **Step 3: Complete the end-to-end plumbing and polish the docs**

```python
# runtime/cli.py
# final run flow:
# 1. load request JSON
# 2. validate request
# 3. create task dir
# 4. write request artifacts
# 5. capture git baseline when needed
# 6. run Claude and capture stderr into run.log
# 7. detect post-run target-workdir changes for read-only and force inspection-required if any exist
# 8. parse and validate result
# 9. write result artifacts, patch metadata, or commit metadata as required
# 10. return exit code based on terminal state
```

- [ ] **Step 4: Run the full verification suite**

Run: `python3 -m unittest discover -s tests -v`
Expected: PASS.
Run: `python3 -m runtime.cli doctor`
Expected: PASS or precise actionable failures.
Run: `git status --short`
Expected: only intended tracked files remain modified.

- [ ] **Step 5: Commit**

```bash
git add .
git commit -m "feat: complete cc collab phase one"
```

## Execution Notes

- The repository itself is already an isolated workspace, so no additional Superpowers worktree is required for this initial build.
- If implementation reveals a spec contradiction, update the spec first and re-run the relevant plan review if the contradiction changes scope.
- Do not claim completion without running the verification commands in Task 11.
- After Task 11, request a final code review focused on spec compliance, quality, install ergonomics, and task closeout safety.
