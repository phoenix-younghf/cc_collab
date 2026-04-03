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

## Closeout Expectations

- Require explicit terminal state in task result metadata.
- Re-run declared verification commands before claiming completion.
- If verification cannot be reproduced, treat the task as `inspection-required`.
- Block closeout when result status and closeout policy mismatch.

## Working Templates

- Task routing template: `templates/task-routing.md`
- Acceptance checklist template: `templates/acceptance-checklist.md`
