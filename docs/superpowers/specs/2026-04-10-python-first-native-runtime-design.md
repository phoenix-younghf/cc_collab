# Python-First Native Runtime Design Spec

**Date:** 2026-04-10
**Project Root:** `/Users/steven/Workspace/cc_collab`
**Primary Goal:** Make `ccollab` work as a native tool on Windows PowerShell/CMD and macOS shells, from installation through real task execution, without requiring WSL and without making Git a hard dependency.

## Problem Statement

The repository currently has partial Windows support artifacts, but the support boundary is still too shallow. The existing work covers install docs, some Windows paths, a PowerShell installer, and a CMD launcher, yet the system still behaves like a Unix-first tool that happens to carry Windows files beside it.

The user wants a stronger contract:

- Windows PowerShell and CMD must be first-class execution environments
- macOS must continue to work well with the same runtime
- missing `Python` must be handled by the installer, not assumed away
- missing `claude` must be detected and explained clearly, but not auto-installed
- missing `Git` must not block task execution
- `ccollab run` must work end-to-end, not only `install` and `doctor`

## Scope

This spec covers the native runtime support layer for `ccollab`:

- bootstrap installers for Windows and macOS
- thin platform launchers
- platform-aware runtime configuration
- dependency detection and user guidance
- Git-aware and non-Git task execution modes
- closeout artifacts for both Git and filesystem-only execution
- test coverage needed to validate native support

## Out of Scope

- automatically installing `claude`
- building a GUI installer
- remote orchestration or cloud execution
- replacing the current task schema with a new workflow model
- adding Linux-specific improvements beyond preserving the current Unix path
- full system package management beyond the dependencies needed to bootstrap `ccollab`

## Success Criteria

The project is successful when all of the following are true:

1. A Windows user can run native PowerShell or CMD install flow and reach a working `ccollab run` path without WSL.
2. A macOS user can run a native shell install flow and reach the same `ccollab run` path.
3. If `Python` is missing, the installer attempts to obtain or guide the user to a usable Python 3 runtime before continuing.
4. If `claude` is missing, `doctor` and `run` fail quickly with explicit, platform-appropriate installation guidance.
5. If `Git` is missing or the target directory is not a Git repo, `ccollab run` still works in a supported degraded mode.
6. Task artifacts clearly record whether execution used Git-aware safety features or filesystem-only fallbacks.
7. The runtime behavior is primarily defined in `runtime/`, not spread across shell-specific scripts.

## Product Contract

`ccollab` will follow these product rules:

1. The core runtime is Python-first. Once Python is available, all major behavior flows through `python -m runtime.cli`.
2. Installers are responsible for bootstrapping `Python` if it is missing.
3. Installers are not responsible for installing `claude`; they only detect and explain its absence.
4. `Git` is an optional capability. When available, it enables stronger safety and cleaner closeout artifacts. When unavailable, `ccollab` still runs through a filesystem-only mode.
5. Windows PowerShell/CMD and macOS shells are supported execution environments, not fallback environments.
6. Launchers must stay thin. Platform-specific business logic belongs in Python, not in shell or batch files.

## Current State

The repository already contains part of the required surface:

- Windows install docs in `README.md` and `AGENTS.md`
- PowerShell installers in `install/*.ps1`
- a Windows launcher in `bin/ccollab.cmd`
- path logic in `runtime/config.py`
- basic Windows checks in `runtime/doctor.py`

The gaps are structural:

- support is still framed around install-time compatibility rather than runtime parity
- `doctor` treats environment readiness too coarsely
- `run` is still implicitly Git- and Unix-shaped
- closeout metadata assumes Git patch delivery
- tests mostly validate docs and path handling rather than true native task execution

## Architecture Overview

The design keeps the current repository shape but changes responsibility boundaries.

### 1. Bootstrap Layer

Bootstrap remains platform-native:

- `install/install-all.sh`
- `install/install-all.ps1`

These scripts have one job: make the machine capable of launching the Python runtime and then install `ccollab` itself. They may detect, acquire, or guide acquisition of `Python`, but they do not contain runtime policy.

### 2. Thin Launcher Layer

Launchers stay intentionally minimal:

- `bin/ccollab`
- `bin/ccollab.cmd`

Responsibilities:

- locate a usable Python runtime
- resolve the repository root or installed runtime root
- forward all arguments to `python -m runtime.cli`

Non-responsibilities:

- platform policy
- dependency decision logic beyond Python discovery
- task execution branching

### 3. Runtime Services Layer

The authoritative behavior moves into Python modules under `runtime/`.

Responsibilities:

- resolve platform-aware paths
- classify environment capabilities
- render actionable doctor output
- run preflight checks before task execution
- select Git-aware or filesystem-only execution mode
- produce mode-appropriate closeout artifacts

## Platform and Dependency Model

Dependencies fall into three classes.

### Bootstrap Dependency

- `Python 3`

`Python` is required for the runtime, but it is not allowed to remain an undocumented prerequisite. The installer must first attempt to find Python. If it is absent, the installer must try a supported acquisition path before declaring failure.

Supported bootstrap behavior:

- Windows:
  - detect `py`, `python`, then `python3`
  - if missing, try a native acquisition path such as `winget` when available
  - if automatic acquisition cannot proceed, stop with precise install guidance
- macOS:
  - detect `python3`, then `python`
  - if missing, try a supported local acquisition path such as Homebrew when available
  - if automatic acquisition cannot proceed, stop with precise install guidance

The installer is allowed to fail when the machine lacks both Python and a supported acquisition mechanism, but the failure must be explicit and actionable.

### Runtime Hard Dependency

- `claude`

`claude` is required for task execution. `ccollab` does not install it, but must:

- detect whether it exists
- detect whether required flags are supported
- fail early in `doctor` and `run` when it is unavailable
- show platform-specific next steps rather than generic command-not-found errors

### Runtime Optional Capability

- `Git`

`Git` is not required for core task execution. When available in a Git repository, it enables stronger safeguards and better closeout artifacts. When absent, the runtime must switch to filesystem-only behavior instead of failing.

## Install Flow

The install flow is:

1. Detect whether a usable Python runtime already exists.
2. If Python is missing, attempt supported platform-native acquisition.
3. Install the Codex skill.
4. Install the platform launcher into the user-facing bin location.
5. Refresh current-session PATH where possible.
6. Run `ccollab doctor`.

Behavioral rules:

- installer success means `ccollab` itself was installed correctly
- missing `claude` does not invalidate installation, but must be surfaced immediately by `doctor`
- installer output must distinguish bootstrap failure from downstream runtime readiness failure

## Doctor Model

`doctor` becomes the authoritative readiness report and must classify results into:

- hard failures: block `run`
- warnings: degraded but supported
- informational checks

Hard failures:

- no usable Python runtime
- launcher missing or broken
- required writable directories unavailable
- `claude` missing
- `claude` missing required flags

Warnings:

- `Git` missing
- workdir is not inside a Git repository
- optional skill or PATH conveniences not fully configured

`doctor` output must clearly separate:

- install readiness
- runtime readiness
- enhanced safety capability

The rendered output should tell the user both what failed and what `ccollab` will do about it. Example: “Git not found; `ccollab` will run in filesystem-only mode.”

## Runtime Execution Modes

`ccollab run` must explicitly support two modes.

### Git-Aware Mode

Selected when:

- `git` is available
- the workdir is inside a valid Git repository

Capabilities:

- dirty workspace detection
- Git HEAD capture
- `git worktree` isolated execution when required
- Git diff / patch-based closeout artifacts
- Git-backed change auditing

### Filesystem-Only Mode

Selected when:

- `git` is unavailable, or
- the workdir is not a Git repository

Capabilities:

- task-local isolated workspace or artifact area
- file snapshot and hash tracking
- changed-file detection without Git
- delivery of changed file bundles and metadata

This is a first-class supported mode, not an error fallback.

## Run Preflight

Before invoking Claude, `run` must execute a lightweight preflight.

Preflight checks:

- Python runtime usable
- `claude` command exists
- required `claude` flags supported
- task root writable
- workdir exists
- Git capability detection and mode selection completed

If a hard dependency fails, `run` must abort before task execution and persist a failure artifact with actionable remediation text.

## Filesystem-Only Isolation Strategy

The non-Git path must still preserve safety and traceability.

Rules:

- execution mode is recorded in task artifacts
- declared files are snapshotted before execution
- runtime may snapshot additional touched files needed to detect undeclared edits
- isolated writes use a task-owned workspace copy when write isolation is requested
- read-only and write-in-place modes use pre/post file snapshots and hashes to detect changes

For `write-isolated`, the runtime should create a task-owned working copy under the task directory and exclude recursion hazards such as the task root itself, `.git`, and transient cache directories.

## Closeout Artifacts

Closeout must stop assuming Git patch delivery as the only output form.

### Git-Aware Closeout

Keep the current patch-oriented model:

- patch file artifact
- metadata with patch path
- apply command such as `git apply <patch>`

### Filesystem-Only Closeout

Produce a file change set:

- manifest describing changed files, original paths, and before/after hashes
- copies of changed files stored under task artifacts
- optional unified diff text for human inspection when it can be generated safely
- metadata describing how to inspect or copy the changed files back

Compatibility rule:

- existing terminal states may remain for compatibility
- artifact metadata must include an explicit type such as `git-patch` or `file-change-set`

## Result and Artifact Semantics

Task artifacts must record:

- execution mode: `git-aware` or `filesystem-only`
- closeout artifact type
- preflight capability summary
- any degraded-mode notes

This information belongs in machine-readable result metadata and in rendered markdown summaries so both automation and humans can understand what happened.

## Error Handling and User Guidance

Error messages must be specific, local, and actionable.

Requirements:

- platform-specific command guidance for Windows and macOS
- clear distinction between “not installed,” “installed but unsupported version,” and “optional capability unavailable”
- no bare subprocess stderr as the only user-facing explanation for common failures

Examples of required guidance:

- missing Python during install
- missing `claude` during doctor or run
- missing `Git` with note that filesystem-only mode will be used
- launcher present but not on PATH

## Testing Strategy

The test suite must prove native task execution, not just documentation correctness.

### Required Test Layers

1. Configuration tests
   - platform path resolution for Windows and macOS
   - launcher target selection
   - task root and config directory rules

2. Installer and launcher tests
   - Python detection order
   - Windows launcher command forwarding
   - Unix launcher command forwarding
   - installer handling for missing Python

3. Doctor tests
   - missing Python
   - missing `claude`
   - unsupported `claude` flags
   - missing `Git` as warning/degraded state
   - PATH and writable directory checks

4. Runtime mode tests
   - Git-aware mode selected inside a Git repo
   - filesystem-only mode selected outside Git or without Git
   - preflight failure writes clear failure artifacts

5. Closeout tests
   - Git patch metadata
   - file change set metadata
   - changed file detection in filesystem-only mode

6. End-to-end smoke tests
   - Windows semantics from request to result artifact
   - macOS/Unix semantics from request to result artifact

## Likely Implementation Surface

The work should remain focused on the following files:

- `install/install-all.sh`
- `install/install-all.ps1`
- `install/install-bin.sh`
- `install/install-bin.ps1`
- `install/install-skill.sh`
- `install/install-skill.ps1`
- `bin/ccollab`
- `bin/ccollab.cmd`
- `runtime/config.py`
- `runtime/doctor.py`
- `runtime/cli.py`
- `runtime/workspace_guard.py`
- `runtime/worktree_manager.py`
- `runtime/closeout_manager.py`
- tests covering config, doctor, CLI, launcher behavior, and closeout

## Risks and Mitigations

### Risk: Bootstrap logic becomes too shell-heavy again

Mitigation:

- keep installers limited to Python acquisition and `ccollab` installation
- move all runtime behavior decisions into Python modules

### Risk: Filesystem-only mode becomes a second-class path

Mitigation:

- model it as an explicit runtime mode
- include it in result metadata and test coverage
- treat lack of Git as warning, not unsupported state

### Risk: macOS Python bootstrap is less automatable than Windows

Mitigation:

- design the installer around supported acquisition mechanisms first
- distinguish “automatic acquisition unavailable” from generic install failure
- ensure the user always gets exact next steps

### Risk: Closeout metadata breaks old consumers

Mitigation:

- preserve terminal states where practical
- add artifact-type metadata rather than replacing all existing semantics at once

## Planning Readiness

This spec is ready for implementation planning because it fixes the product contract and system boundaries:

- Python bootstrapping belongs to installers
- runtime behavior belongs to Python
- `claude` is required but externally installed
- `Git` is optional and yields explicit mode selection
- closeout is artifact-type aware rather than Git-only
- acceptance requires install, doctor, and real task execution on native Windows and macOS paths
