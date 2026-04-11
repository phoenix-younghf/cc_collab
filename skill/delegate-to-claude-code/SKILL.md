---
name: delegate-to-claude-code
description: Use when Codex should delegate bounded research, review, or implementation work to local Claude Code while keeping final acceptance and closeout in Codex.
---

# Delegate To Claude Code

Use this skill when Codex should orchestrate a bounded task through local Claude Code and keep Codex as the integration owner.

## Routing Rules

Delegate when one of these applies:
- Design review at major checkpoints
- Plan review after plan drafting
- Standalone research tasks
- Independent code review
- Bounded implementation tasks
- Larger implementation tasks that benefit from Claude-side parallelism

Do not delegate automatically when:
- Scope is ambiguous
- Acceptance criteria are missing
- Write-in-place starts from an unsafe dirty state
- Closeout path is unclear

## Controller Expectations

- Codex owns framing, acceptance criteria, and integration decisions.
- Claude output is advisory until verification is re-run locally.
- Use request/result artifacts produced by `ccollab` rather than ad-hoc summaries.

## Runtime Modes

`ccollab` may execute in either `Git-aware` or `filesystem-only` mode.

- Prefer `Git-aware` execution when the target workdir is inside a healthy Git repository.
- Expect `filesystem-only` execution when Git is unavailable or the workdir is outside Git.
- If the repo is healthy but `git worktree` support is degraded, execution remains `Git-aware` and write-isolated tasks fall back to filesystem-copy isolation.
- When using smoke requests from `examples/`, treat them as templates and rewrite `workdir` before invoking `ccollab run`.

## Bootstrap

Before exploring docs or building a request:

macOS / Linux:

```bash
command -v ccollab >/dev/null 2>&1 || source ~/.zprofile
ccollab doctor || (cd ~/workspace/cc_collab && python3 -m runtime.cli doctor)
```

Windows PowerShell:

```powershell
if (-not (Get-Command ccollab -ErrorAction SilentlyContinue)) { $env:Path = "$HOME\.local\bin;$env:Path" }
ccollab doctor
if ($LASTEXITCODE -ne 0) { Set-Location ~/workspace/cc_collab; py -3 -m runtime.cli doctor }
```

If `ccollab` is still unavailable, use the repository entrypoint directly from `~/workspace/cc_collab`:

```bash
cd ~/workspace/cc_collab
python3 -m runtime.cli run --request /path/to/request.json
```

```powershell
Set-Location ~/workspace/cc_collab
py -3 -m runtime.cli run --request C:\path\to\request.json
```

If `py` is unavailable on Windows, use `python` instead.

Prefer this bootstrap sequence over ad-hoc repo exploration.

## Closeout Expectations

- Require explicit terminal state in task result metadata.
- Re-run declared verification commands before claiming completion.
- If verification cannot be reproduced, treat the task as `inspection-required`.
- Block closeout when result status and closeout policy mismatch.

## Working Templates

- Task routing template: `templates/task-routing.md`
- Acceptance checklist template: `templates/acceptance-checklist.md`
