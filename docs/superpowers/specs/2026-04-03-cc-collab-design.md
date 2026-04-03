# CC Collab Design Spec

**Date:** 2026-04-03
**Project Root:** `/Users/steven/workspace/cc_collab`
**Primary Goal:** Build a reusable collaboration layer that lets Codex orchestrate local Claude Code for review, research, planning support, and implementation work, with clear closeout rules and cross-device installation.

## Problem Statement

The user wants a durable way for Codex to delegate selected work to local Claude Code without giving up control of the main workflow. The solution must support:

- Codex-led orchestration and final acceptance
- Claude participation in design review, plan review, research, code review, and implementation
- Optional Claude-side multi-agent execution for larger tasks
- Safe task closeout so work does not get stranded or silently pollute repositories
- Cross-device reuse with low-friction installation for both humans and agents
- Compatibility with the user's Superpowers-heavy workflow

## Scope

This spec covers one subsystem: a local `cc_collab` repository that provides a Codex skill, a runtime runner, install scripts, task artifacts, and operating rules for Codex <-> Claude collaboration.

## Out of Scope

- Cloud-hosted orchestration services
- Multi-user shared web UI
- General remote job queueing across machines
- Replacing Superpowers with a new workflow system
- Direct dependency on an active Claude chat session as the primary execution path

## Success Criteria

The project is successful when all of the following are true:

1. Codex can invoke a local command that dispatches a structured task to Claude Code.
2. Claude can be used in `research`, `review`, `design-review`, `plan-review`, and `implementation` modes.
3. Large implementation tasks can opt into Claude-side multi-agent execution.
4. Every task ends in an explicit terminal state with an explicit closeout policy.
5. Installation on a new machine can be completed with a short README-driven flow.
6. The resulting system fits naturally into `brainstorming`, `writing-plans`, `subagent-driven-development`, and `verification-before-completion`.

## Platform and Dependency Baseline

Phase 1 explicitly supports:

- macOS
- Linux
- bash or zsh shells

Required local executables:

- `git`
- `python3` 3.11 or newer
- `claude` CLI 2.1.x or newer

Optional tools may be added later, but Phase 1 must not depend on `jq`.

Path selection rules:

- Codex skill install target: `$CODEX_HOME/skills/delegate-to-claude-code` if `$CODEX_HOME` is set, otherwise `~/.codex/skills/delegate-to-claude-code`
- command install target: `~/.local/bin/ccollab`
- config directory: `$XDG_CONFIG_HOME/cc_collab` if `$XDG_CONFIG_HOME` is set, otherwise `~/.config/cc_collab`
- task artifact root: `~/workspace/cc_collab/tasks/`

## Product Principles

1. Codex remains the orchestrator.
2. Claude is a powerful collaborator, not a second uncontrolled orchestrator.
3. Execution policies and closeout policies are first-class, not implicit.
4. Default experience must optimize for fast local use, not maximum isolation at all times.
5. Structured artifacts beat undocumented chat handoffs.
6. Installation must be easy enough that another agent can perform it from README.

## Users and Roles

### Primary Human User

A technical operator who uses Codex, Claude Code, and Superpowers heavily, often across multiple concurrent projects and machines.

### Codex Role

Codex is the controller. Codex owns:

- primary conversation context
- brainstorming mainline
- writing-plans mainline
- task routing
- acceptance criteria construction
- final verification and acceptance
- integration decisions

### Claude Role

Claude is the delegated collaborator. Claude may act as:

- design reviewer
- plan reviewer
- researcher
- code reviewer
- implementer

### Claude Subagents Role

Claude-side agent groups are optional internal accelerators used only when a task explicitly allows multi-agent execution. They do not become independent project owners.

## High-Level Architecture

The system has two layers.

### 1. Skill Layer

Installed into Codex skill discovery so future Codex sessions can recognize when to delegate to Claude.

Responsibilities:

- detect when Claude collaboration is beneficial
- prepare structured task requests
- encode task routing rules
- enforce acceptance and closeout expectations
- describe how to integrate with Superpowers

### 2. Runtime Layer

Lives in the `cc_collab` repository and executes tasks.

Responsibilities:

- validate task requests
- invoke local Claude Code CLI
- inject task contracts and mode-specific prompts
- capture artifacts and logs
- validate task results
- manage write policies and closeout outcomes

## Repository Layout

The repository will follow this structure:

```text
cc_collab/
  README.md
  AGENTS.md
  install/
    install-skill.sh
    install-bin.sh
    install-all.sh
  skill/
    delegate-to-claude-code/
      SKILL.md
      templates/
        task-routing.md
        acceptance-checklist.md
  bin/
    ccollab
  runtime/
    schemas/
      task-request.schema.json
      task-result.schema.json
    prompts/
      research.md
      implementation.md
      review.md
      design-review.md
      plan-review.md
    lib/
      task_builder.*
      claude_runner.*
      result_parser.*
      workspace_guard.*
      artifact_store.*
      closeout_manager.*
  examples/
    research-task.json
    implementation-task.json
    review-task.json
  docs/
    design/
    usage/
    superpowers/
      specs/
      plans/
  tasks/
    .gitkeep
```

## Task Types

The runtime must support these task types:

- `research`
- `review`
- `design-review`
- `plan-review`
- `implementation`

## Execution Modes

The runtime must support these execution modes:

- `single-worker`
- `multi-agent`

### Default Mapping

- `research` -> `single-worker`
- `review` -> `single-worker`
- `design-review` -> `single-worker`
- `plan-review` -> `single-worker`
- `implementation` -> `single-worker` by default, `multi-agent` when task size and separability justify it

## Write Policies

The runtime must treat write policy as explicit configuration, not as an inference.

Supported write policies:

- `read-only`
- `write-in-place`
- `write-isolated`

### Policy Rules

#### `read-only`

- Claude may read the target workspace.
- Claude may write only task artifacts under `cc_collab/tasks/<task-id>/`.
- No business code changes are allowed.
- Phase 1 enforcement is runtime-driven: request contract, prompt contract, and post-run diff detection must all agree. Any project write detected during a `read-only` task forces `inspection-required`.

#### `write-in-place`

- Claude writes directly in the target project workspace.
- This is the default write mode for normal local development.
- It is allowed only when the targeted file set is declared up front and safe baseline capture succeeds.
- Phase 1 enforcement is runtime-driven: Claude is told the declared file set, the runtime snapshots those files, and post-run diff checks fail closeout if undeclared files were modified.

#### `write-isolated`

- Claude writes in an isolated workspace copy of the target repository.
- This mode is required when the task is high risk, when the repository is already being modified elsewhere, or when the starting workspace is not safe for in-place execution.
- The isolation implementation may use git worktrees when appropriate.

## Closeout Policy

Every task request must declare closeout rules.

Each task has:

- execution mode
- write policy
- closeout policy

Closeout must be explicit for both success and failure.

### Required Terminal States

Only these terminal states are allowed:

- `archived`
- `integrated`
- `commit-ready`
- `patch-ready`
- `discarded`
- `inspection-required`

### Operational Meaning of Terminal States

#### `archived`

- The task produced only task artifacts.
- No project code changes were made or retained.
- The task directory remains available for later inspection.

#### `integrated`

- The task completed successfully in `write-in-place`.
- The accepted task changes remain applied in the target workspace.
- Fresh verification succeeded after execution.
- No commit is implied by this state.

#### `commit-ready`

- The task completed successfully in `write-isolated`.
- The accepted change set exists in an isolated branch or worktree as one or more task-owned commits.
- The result must include the isolated path plus commit SHA data needed for cherry-pick or merge.
- The main workspace remains untouched.

#### `patch-ready`

- The runtime produced a unified patch file under the task artifact directory, for example `tasks/<task-id>/changes.patch`.
- The result must include the patch path and the recommended apply command.
- This state does not claim the target workspace already includes the change.

#### `discarded`

- The task was abandoned without accepted code integration.
- The runtime may remove temporary isolated workspaces it created.
- Task artifacts remain for inspection unless cleanup is explicitly requested later.

#### `inspection-required`

- Automated execution or closeout could not safely finish.
- Artifacts and logs are preserved.
- Human action is required before further automation touches the affected task state.

### Closeout Rules by Policy

#### `read-only`

- success terminal state: `archived`
- failure terminal state: `inspection-required`
- output: result manifest, markdown summary, logs, optional cited materials

#### `write-in-place`

- success terminal state: `integrated`
- failure terminal state: `patch-ready` or `inspection-required`
- baseline capture is mandatory before writes begin
- runtime must attempt safe rollback only when it can prove the task exclusively owns the touched changes since baseline
- unsafe rollback attempts are forbidden

#### `write-isolated`

- success terminal state: `commit-ready` or `patch-ready`
- failure terminal state: `discarded` or `inspection-required`
- runtime must make reintegration path explicit, usually by commit or patch

### Allowed Closeout Mapping

| Write policy | Success closeout | Failure closeout |
|--------------|------------------|------------------|
| `read-only` | `archived` | `inspection-required` |
| `write-in-place` | `integrated` | `patch-ready` or `inspection-required` |
| `write-isolated` | `commit-ready` or `patch-ready` | `discarded` or `inspection-required` |

Any request that specifies a closeout combination outside this table must fail validation before execution.

## Safety Definitions

These definitions exist to keep planning and implementation bounded.

### Declared File Set

- For Phase 1, write tasks must declare explicit repository-relative file paths.
- Directory-level declarations and glob patterns are out of scope for Phase 1.
- Claude may not intentionally modify files outside the declared file set.

### Unsafe Dirty Starting State

A `write-in-place` task starts in an unsafe dirty state when any declared file already has uncommitted changes, is newly created but not task-owned, or cannot be snapshotted reliably before execution.

If a task starts in an unsafe dirty state, the runtime must either reject `write-in-place` or require `write-isolated`.

### Safe Baseline Capture

Safe baseline capture for `write-in-place` means the runtime records all of the following before Claude writes:

- current repository HEAD commit, if any
- `git status --porcelain=v1 --untracked-files=all`
- the existence state of each declared file
- a content hash for each declared file that already exists

If any baseline element cannot be captured, the runtime must fail before execution.

### Exclusive Ownership of Changes Since Baseline

The runtime may claim exclusive ownership only when:

- every changed file is in the declared file set
- each changed file matches the runtime's start snapshot and task journal expectations
- no unexpected external mutation is detected during the task

If exclusive ownership cannot be shown, automatic rollback is forbidden.

### Task Journal Expectations

At minimum, the runtime task journal must record:

- start-time baseline metadata for every declared file
- each detected write event or write attempt attributed to the task runner
- end-of-task hashes for every declared file that exists after execution

## Task Request Manifest

Every task must begin from a request manifest. The manifest must be both human-readable and machine-validatable.

Minimum fields:

```json
{
  "task_id": "2026-04-03-001",
  "task_type": "implementation",
  "execution_mode": "multi-agent",
  "write_policy": "write-in-place",
  "origin": {
    "controller": "codex",
    "workflow_stage": "implementation"
  },
  "workdir": "/absolute/path/to/project",
  "objective": "Implement the requested feature",
  "context_summary": "Compressed context prepared by Codex",
  "inputs": {
    "files": ["src/example.ts"],
    "constraints": ["Preserve current CLI output"],
    "acceptance_criteria": ["Adds command X"],
    "verification_commands": ["npm test"],
    "closeout": {
      "on_success": "integrated",
      "on_failure": "patch-ready"
    }
  },
  "claude_role": {
    "mode": "implementation",
    "allow_subagents": true
  }
}
```

### Manifest Design Rules

- `task_id` must be globally unique enough for local archival.
- `workflow_stage` must reflect the current Superpowers stage.
- `context_summary` must compress context instead of dumping full conversation history.
- `files` may be empty for open-ended research, but must be populated for write tasks.
- write-task `files` entries must be explicit repository-relative file paths in Phase 1.
- `acceptance_criteria` must be concrete enough to evaluate pass or fail.
- `allow_subagents` must be explicit.

## Task Result Contract

Claude must return a structured result that the runtime validates before Codex relies on it.

Minimum fields:

```json
{
  "task_id": "2026-04-03-001",
  "status": "completed",
  "summary": "Implemented the requested behavior",
  "decisions": ["Used existing parser rather than adding a new abstraction"],
  "changed_files": ["src/example.ts", "tests/example.test.ts"],
  "verification": {
    "commands_run": ["npm test tests/example.test.ts"],
    "results": ["PASS 1/1"],
    "all_passed": true
  },
  "open_questions": [],
  "risks": [],
  "follow_up_suggestions": [],
  "agent_usage": {
    "used_subagents": true,
    "notes": "Split implementation, review, and test checks"
  },
  "terminal_state": "integrated"
}
```

### Result Status Enum

Allowed result `status` values:

- `completed`
- `blocked`
- `failed`

### Result Rules

- `status` must distinguish success from blocked or failed execution.
- `changed_files` must be empty for `read-only` tasks.
- `verification.commands_run` must list actual commands run, not intended commands.
- `terminal_state` must be compatible with the request closeout rules.
- `agent_usage` must make Claude-side delegation visible.

## Artifact Model

Each task gets a directory under `cc_collab/tasks/<task-id>/`.

That directory stores collaboration artifacts only, not the project under execution.

Minimum artifacts:

- `request.json`
- `request.md`
- `result.json`
- `result.md`
- `run.log`
- optional diff summaries or helper files

## Command Interface

The runtime must expose a single stable entry point:

```bash
ccollab run --request /path/to/request.json
```

Supporting commands:

```bash
ccollab status --task <task-id>
ccollab open --task <task-id>
ccollab cleanup --task <task-id>
ccollab doctor
```

### Command Design Goals

- one obvious execution command
- machine-friendly output
- inspectable task directories
- easy recovery for humans

### Cleanup Rules

`ccollab cleanup` may delete only artifacts or temporary isolated workspaces created by `ccollab` itself.

It must never delete files from the target project workspace.

Phase 1 cleanup behavior:

- `archived`: may delete task artifacts only when explicitly requested
- `discarded`: may delete task-created isolated workspace plus artifacts when explicitly requested
- `commit-ready`: may delete isolated workspace only after patch or commit metadata has been preserved in task artifacts and the operator explicitly requests cleanup
- `patch-ready`: may delete temporary isolated workspace, but must preserve patch artifacts by default
- `inspection-required`: cleanup must refuse destructive deletion by default

### Doctor Checks

`ccollab doctor` must check at least:

- required executables are available at runnable paths
- Claude CLI exposes required flags
- skill install target is writable or already linked
- command install target is writable
- config directory can be created
- task artifact root can be created
- `~/.local/bin` is on `PATH`, or the command reports a precise fix

## Claude Invocation Strategy

The runtime will invoke local Claude Code CLI in non-interactive mode.

The design assumes the local machine has Claude Code available.

### Claude CLI Contract

Phase 1 assumes Claude Code CLI 2.1.x or newer and requires the following capabilities to be present:

- `-p` / `--print`
- `--output-format`
- `--json-schema`
- `--add-dir`
- `--append-system-prompt`
- `--agents`

`ccollab doctor` must fail if the installed Claude CLI does not expose this minimum contract.

The invocation path must support:

- model selection
- explicit tool and directory access
- appended system prompt contracts
- optional temporary agent pack injection
- structured output where practical

The design must not require that the user preconfigure custom Claude agents globally.

Instead, the runtime may inject task-scoped agent packs when needed.

### Phase 1 Invocation Shape

The runtime will build a Claude invocation equivalent to:

```bash
claude -p \
  --output-format json \
  --json-schema '<task-result-schema-json>' \
  --add-dir '<resolved-workdir>' \
  --append-system-prompt '<runtime-contract>' \
  [--agents '<task-scoped-agent-pack-json>'] \
  '<rendered-task-prompt>'
```

If the returned payload fails schema validation, the runtime may perform one structured repair attempt. If repair still fails, the task must end as `inspection-required`.

## Claude Agent Packs

The initial design includes two task-scoped agent pack concepts.

### Implementation Pack

Roles:

- `implementer`
- `reviewer`
- `tester`

### Research Pack

Roles:

- `researcher`
- `synthesizer`
- `critic`

These packs are internal execution aids. They do not alter the Codex-level contract.

## Routing Rules for Codex

The skill must encode practical routing rules.

### Keep in Codex

- final problem framing
- brainstorming mainline
- writing-plans mainline
- final acceptance
- high-context integration decisions
- final verification claims

### Delegate to Claude

- design review at major checkpoints
- plan review after plan drafting
- standalone research tasks
- independent code review
- bounded implementation tasks
- larger implementation tasks that benefit from Claude-side parallelism

### Refuse or Escalate

The skill should avoid automatic delegation when:

- task scope is still ambiguous
- acceptance criteria are missing
- write-in-place is requested against an unsafe dirty starting state
- the closeout path is unclear

## Integration with Superpowers

The collaboration layer must fit existing process skills instead of bypassing them.

### Brainstorming

- Codex runs the brainstorming mainline.
- Claude may be invoked for `design-review` on draft architecture or spec sections.
- Claude feedback informs the Codex-owned spec.

### Writing Plans

- Codex writes the plan.
- Claude may be invoked for `plan-review` to catch decomposition gaps, ambiguity, and execution risks.

### Subagent-Driven Development

- Codex remains responsible for the controller role described by the skill.
- Claude collaboration becomes an optional execution path for suitable tasks.
- Claude does not remove the need for spec compliance review and quality review.

### Verification Before Completion

- Claude reports are advisory until verified.
- Codex or the runtime must re-run declared verification commands before claiming success.

### Writing Skills

- The Codex skill content must itself be validated through pressure scenarios.
- The skill should teach when and how to delegate, not just describe the runtime.

## Installation and Cross-Device Reuse

Installation must be optimized for both humans and agents.

### Requirements

- README starts with quick install, not background narrative.
- install scripts must be non-interactive or support a non-interactive flag.
- installs must be idempotent.
- installation must end with a single verification command.

### Installation Model

- repository lives at `~/workspace/cc_collab`
- skill is installed into Codex skill discovery via symlink or equivalent safe link
- command entry point is linked into a user bin directory
- local-machine-specific config is stored outside the repo, for example under `~/.config/cc_collab/`

### Required Docs

- `README.md` with quick install and quick start sections
- `AGENTS.md` with minimal installation and verification steps for other agents
- troubleshooting section for common setup failures

## Error Handling and Guardrails

The runtime must fail loudly rather than silently degrade.

Required rules:

- invalid request manifest -> fail before invoking Claude
- unsafe write-in-place baseline -> fail or require alternate policy
- isolated write setup failure -> fail instead of silently switching to in-place
- invalid result manifest -> allow one structured repair attempt, then mark `inspection-required`
- closeout mismatch -> fail task closeout and mark `inspection-required`

## Verification Strategy

The project needs verification at three levels.

### 1. Runtime Verification

Verify installation and environment assumptions.

Examples:

- `ccollab doctor`
- schema validation checks
- Claude CLI availability checks

### 2. Contract Verification

Verify request and result contracts behave as designed.

Examples:

- dry-run request validation
- result schema validation
- compatibility checks for terminal state vs closeout policy

### 3. Workflow Verification

Verify the skill changes Codex behavior in meaningful scenarios.

Examples:

- without the skill, Codex does not delegate or does so inconsistently
- with the skill, Codex uses the runtime and enforces closeout rules
- with ambiguous scope, Codex does not delegate prematurely

## Initial Implementation Order

1. repository skeleton and install scripts
2. request/result schemas
3. `ccollab` command scaffold and `doctor`
4. artifact storage and request validation
5. Claude runner for read-only flows
6. review and research modes
7. write-in-place safeguards and closeout handling
8. implementation mode
9. multi-agent support
10. skill packaging and validation scenarios
11. README and AGENTS install polish

## Risks

### Over-delegation Risk

Codex may start delegating tasks that still need human-level framing or integration judgment.

Mitigation: strong routing rules in the skill and explicit refusal triggers.

### Stranded Work Risk

Claude may produce partial changes that are hard to trust or recover.

Mitigation: explicit closeout policy, baseline capture, terminal states, and artifact logging.

### Tool Drift Risk

Claude CLI or local agent behavior may change.

Mitigation: isolate CLI interaction in runtime, keep schemas stable, and make `doctor` detect broken assumptions early.

### Workflow Complexity Risk

The system could become more complex than the value it delivers.

Mitigation: keep a single runtime entry point, start with a clear default path, and treat advanced isolation and multi-agent behavior as opt-in.

## Design Decision Summary

This spec selects a hybrid of lightweight local invocation and structured task artifacts:

- Codex remains the orchestrator.
- Claude becomes a reusable local reviewer and executor.
- Structured manifests define the collaboration boundary.
- Write policies and closeout policies make task endings explicit.
- Cross-device reuse is handled by repo + install scripts + local config.
- Superpowers remains the governing workflow layer.

## Acceptance for Planning

This spec is ready for implementation planning when:

- no placeholders remain
- task types, execution modes, write policies, and terminal states are all explicit
- install model and Superpowers integration are explicit
- closeout behavior is specific enough to drive implementation tasks
