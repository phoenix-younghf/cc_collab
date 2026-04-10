# Python-First Native Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `ccollab` install and run natively on Windows PowerShell/CMD and macOS shells with Python bootstrapping, `claude` preflight guidance, and a supported non-Git execution path.

**Architecture:** Keep launchers thin and move platform/runtime decisions into Python. Add an install-root model, a capability detector for Python/Claude/Git/worktree, and dual execution paths in `runtime.cli`: full Git-aware mode when available and filesystem-only mode when Git is absent or unusable. Extend closeout artifacts so Git-backed runs still produce patches while filesystem-only runs produce a file change set with explicit metadata.

**Tech Stack:** Python standard library, Bash, PowerShell, CMD batch wrappers, `unittest`

---

## Current State

- `install/*.sh` and `install/*.ps1` install launchers directly from the repo checkout; they do not create a durable install root.
- [`bin/ccollab`](/Users/steven/Workspace/cc_collab/bin/ccollab) and [`bin/ccollab.cmd`](/Users/steven/Workspace/cc_collab/bin/ccollab.cmd) launch from the checkout path instead of an installed runtime payload.
- [`runtime/doctor.py`](/Users/steven/Workspace/cc_collab/runtime/doctor.py) can detect Python and `claude`, but it only returns pass/fail checks and still treats Git-like assumptions as implicit.
- [`runtime/cli.py`](/Users/steven/Workspace/cc_collab/runtime/cli.py) requires Git status capture for `read-only` and `write-in-place`, and uses `git worktree` for all `write-isolated` tasks.
- [`runtime/closeout_manager.py`](/Users/steven/Workspace/cc_collab/runtime/closeout_manager.py) only knows how to emit Git patch metadata.
- Existing tests cover Windows path conventions and some CLI flows, but not install-root copying, filesystem-only execution, or degraded Git capability handling.

## File Structure

### New Production Files

- `runtime/capabilities.py`
  - Central capability detection for Python launchers, Claude CLI readiness, Git repo status, and `git worktree` usability.

### New Test Files

- `tests/test_capabilities.py`
  - Capability-detection unit tests, including missing Git, non-repo workdirs, and degraded `git worktree` behavior.
- `tests/test_installers.py`
  - Installer and launcher tests for install-root copying, Python detection order, post-install doctor execution, and thin launcher forwarding.
- `tests/test_installed_launcher_smoke.py`
  - Automated smoke coverage for installed-launcher `run` in filesystem-only and Git-aware modes.

### New Example Files

- `examples/filesystem-only-smoke-task.json`
  - Minimal installed-launcher smoke request for non-Git validation.
- `examples/git-aware-smoke-task.json`
  - Minimal installed-launcher smoke request for Git-aware validation.

### Modified Production Files

- `runtime/config.py`
  - Add install-root resolution and any runtime-root/launcher path helpers needed by installers and launchers.
- `runtime/doctor.py`
  - Report hard failures vs warnings and render actionable guidance.
- `runtime/cli.py`
  - Add preflight capability detection, Git-aware vs filesystem-only mode selection, and closeout metadata wiring.
- `runtime/workspace_guard.py`
  - Reuse and extend snapshot/hash tracking for non-Git execution modes.
- `runtime/worktree_manager.py`
  - Degrade isolated execution cleanly when Git exists but `git worktree` is unavailable.
- `runtime/closeout_manager.py`
  - Support Git patch artifacts and filesystem-only file change set artifacts.
- `runtime/artifact_store.py`
  - Add helpers for change-set artifact directories if needed.
- `runtime/result_renderer.py`
  - Render execution mode and artifact-type metadata in result markdown.
- `bin/ccollab`
  - Keep launcher thin and point it at installed runtime root rather than checkout root.
- `bin/ccollab.cmd`
  - Same as Unix launcher, but with Windows Python launcher discovery.
- `install/install-all.sh`
  - Detect/bootstrap Python, install runtime payload, skill, launcher, then run doctor.
- `install/install-all.ps1`
  - Windows-native bootstrap/install flow.
- `install/install-bin.sh`
  - Install launcher from the durable install root.
- `install/install-bin.ps1`
  - Same on Windows.
- `install/install-skill.sh`
  - Install skill from the durable install root.
- `install/install-skill.ps1`
  - Same on Windows.
- `README.md`
  - Update install/runtime behavior and non-Git support docs.
- `AGENTS.md`
  - Keep agent bootstrap instructions aligned with new installer contract.
- `skill/delegate-to-claude-code/SKILL.md`
  - Align bootstrap, doctor, and non-Git behavior guidance.

### Modified Test Files

- `tests/test_config.py`
- `tests/test_doctor.py`
- `tests/test_cli.py`
- `tests/test_closeout_manager.py`
- `tests/test_workspace_guard.py`
- `tests/test_worktree_manager.py`
- `tests/test_result_renderer.py`
- `tests/test_install_docs.py`

## Task Decomposition

### Task 1: Install Root And Launcher Bootstrap

**Files:**
- Create: `tests/test_installers.py`
- Create: `examples/filesystem-only-smoke-task.json`
- Create: `examples/git-aware-smoke-task.json`
- Modify: `tests/test_config.py`
- Modify: `runtime/config.py`
- Modify: `bin/ccollab`
- Modify: `bin/ccollab.cmd`
- Modify: `install/install-all.sh`
- Modify: `install/install-all.ps1`
- Modify: `install/install-bin.sh`
- Modify: `install/install-bin.ps1`
- Modify: `install/install-skill.sh`
- Modify: `install/install-skill.ps1`

- [ ] **Step 1: Write failing config tests for install-root resolution**

```python
class ConfigTests(TestCase):
    def test_resolve_paths_includes_install_root_for_windows(self) -> None:
        paths = resolve_paths(
            env={
                "USERPROFILE": r"C:\Users\steven",
                "LOCALAPPDATA": r"C:\Users\steven\AppData\Local",
                "APPDATA": r"C:\Users\steven\AppData\Roaming",
            },
            os_name="nt",
            path_factory=PureWindowsPath,
        )
        self.assertEqual(
            paths.install_root,
            PureWindowsPath(r"C:\Users\steven\AppData\Local\cc_collab\install"),
        )

    def test_resolve_paths_uses_macos_install_root(self) -> None:
        with patch("runtime.config.platform.system", return_value="Darwin"):
            paths = resolve_paths(
                env={"HOME": "/Users/steven"},
                os_name="posix",
                path_factory=PurePosixPath,
            )
        self.assertEqual(
            paths.install_root,
            PurePosixPath("/Users/steven/Library/Application Support/cc_collab/install"),
        )
```

- [ ] **Step 2: Write failing installer/launcher tests for durable install-root behavior**

```python
class InstallerTests(TestCase):
    def test_install_all_sh_copies_runtime_and_runs_doctor(self) -> None:
        with TemporaryDirectory() as tmp:
            result = run_install_script_with_fake_python(temp_root=tmp)
            self.assertEqual(result.returncode, 0)
            self.assertTrue((Path(tmp) / "install" / "runtime" / "cli.py").exists())
            self.assertIn("doctor", fake_python_log.read_text(encoding="utf-8"))

    def test_install_from_archive_tree_does_not_require_git_metadata(self) -> None:
        with TemporaryDirectory() as tmp:
            source_root = build_archive_style_source_tree(tmp)
            result = run_install_script_with_fake_python(temp_root=tmp, source_root=source_root)
            self.assertEqual(result.returncode, 0)
            self.assertTrue((Path(tmp) / "install" / "runtime" / "cli.py").exists())

    def test_installed_unix_launcher_forwards_arguments_from_install_root(self) -> None:
        with TemporaryDirectory() as tmp:
            result = run_installed_launcher_with_fake_python(["doctor"], temp_root=tmp)
            self.assertEqual(result.returncode, 0)
            self.assertIn("-m runtime.cli doctor", fake_python_log.read_text(encoding="utf-8"))

    @skipUnless(shutil.which("cmd"), "cmd required for Windows launcher forwarding")
    def test_installed_windows_launcher_forwards_arguments_from_install_root(self) -> None:
        with TemporaryDirectory() as tmp:
            result = run_installed_windows_launcher_with_fake_python(["doctor"], temp_root=tmp)
            self.assertEqual(result.returncode, 0)
            self.assertIn("-m runtime.cli doctor", fake_python_log.read_text(encoding="utf-8"))

    @skipUnless(shutil.which("pwsh"), "pwsh required for PowerShell bootstrap behavior")
    def test_windows_bootstrap_attempts_winget_then_surfaces_guidance_when_python_missing(self) -> None:
        with TemporaryDirectory() as tmp:
            result = run_install_all_ps1_without_python(
                temp_root=tmp,
                fake_winget=True,
                winget_succeeds=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("winget", result.stdout.lower())
            self.assertIn("install python", result.stderr.lower())

    def test_unix_bootstrap_attempts_brew_then_surfaces_guidance_when_python_missing(self) -> None:
        with TemporaryDirectory() as tmp:
            result = run_install_all_sh_without_python(
                temp_root=tmp,
                fake_brew=True,
                brew_succeeds=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("brew", result.stdout.lower())
            self.assertIn("install python", result.stderr.lower())

    def test_unix_bootstrap_surfaces_manual_guidance_when_python_and_brew_are_missing(self) -> None:
        with TemporaryDirectory() as tmp:
            result = run_install_all_sh_without_python(
                temp_root=tmp,
                fake_brew=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("python 3", result.stderr.lower())
            self.assertIn("brew", result.stderr.lower())

    def test_installers_refresh_path_for_current_session(self) -> None:
        with TemporaryDirectory() as tmp:
            result = run_install_script_with_fake_python(temp_root=tmp)
            self.assertIn(".local/bin", result.stdout)

    def test_install_succeeds_even_when_doctor_reports_missing_claude(self) -> None:
        with TemporaryDirectory() as tmp:
            result = run_install_script_with_fake_python(temp_root=tmp, fake_doctor_exit=1)
            self.assertEqual(result.returncode, 0)
            self.assertIn("claude", result.stdout.lower())
```

- [ ] **Step 3: Run the targeted tests and confirm they fail first**

Run: `python3 -m unittest tests.test_config tests.test_installers -v`  
Expected: FAIL with missing `install_root` expectations, launcher/install script assertions, archive-install expectations, and no-Python bootstrap/acquisition expectations.

- [ ] **Step 4: Implement install-root resolution in `runtime/config.py`**

```python
@dataclass(frozen=True)
class ResolvedPaths:
    install_root: PurePath
    runtime_root: PurePath
    skill_dir: PurePath
    bin_path: PurePath
    config_dir: PurePath
    task_root: PurePath
```

Use platform defaults from the spec:
- Windows: `%LOCALAPPDATA%\cc_collab\install`
- macOS: `~/Library/Application Support/cc_collab/install`
- Unix fallback: `~/.local/share/cc_collab/install`

- [ ] **Step 5: Make installers copy the runtime payload into the install root**

Implementation requirements:
- copy `bin/`, `runtime/`, `skill/`, `README.md`, and `AGENTS.md` into the install root
- avoid relying on `.git`
- keep launchers and skill install pointing at the copied payload
- detect Python before attempting copy/install
- if Python is missing on Windows, try `winget` first and then print exact manual install guidance if automatic acquisition is unavailable or fails
- if Python is missing on macOS, try Homebrew when available and then print exact manual install guidance if automatic acquisition is unavailable or fails
- make the bootstrap path behaviorally testable under `unittest` with fake `PATH` shims for missing Python and optional fake `brew` / `winget` commands, instead of relying only on script-text assertions
- ship smoke-request fixtures in `examples/` so installed launchers can be exercised without ad hoc request authoring
- refresh current-session PATH where the shell/platform allows it
- run `ccollab doctor` automatically at the end of install and surface its output
- treat “install succeeded but doctor reported missing `claude`” as successful installer completion with visible runtime-readiness guidance, not as installer failure
- ensure automated installer tests redirect install-related env vars (`HOME`, `CODEX_HOME`, `LOCALAPPDATA`, `APPDATA`) into temp paths so they never touch a developer’s real install locations
- support installation from an extracted source archive with no `.git` metadata, not just from a checkout

- [ ] **Step 6: Keep launchers thin and runtime-root based**

Implementation requirements:
- `bin/ccollab` should resolve runtime root from `CCOLLAB_RUNTIME_ROOT` or its installed location
- `bin/ccollab.cmd` should do the same with `py` / `python` / `python3`
- both launchers should only set `PYTHONPATH` and execute `python -m runtime.cli %*`

- [ ] **Step 7: Re-run the targeted tests and confirm they pass**

Run: `python3 -m unittest tests.test_config tests.test_installers -v`  
Expected: PASS

- [ ] **Step 8: Commit the bootstrap/install-root changes**

```bash
git add tests/test_installers.py tests/test_config.py examples/filesystem-only-smoke-task.json examples/git-aware-smoke-task.json runtime/config.py bin/ccollab bin/ccollab.cmd install/install-all.sh install/install-all.ps1 install/install-bin.sh install/install-bin.ps1 install/install-skill.sh install/install-skill.ps1
git commit -m "feat: add install-root bootstrap flow"
```

### Task 2: Capability Detection And Doctor Output

**Files:**
- Create: `runtime/capabilities.py`
- Create: `tests/test_capabilities.py`
- Modify: `tests/test_doctor.py`
- Modify: `runtime/doctor.py`

- [ ] **Step 1: Write failing capability tests for Git/non-Git/worktree detection**

```python
class CapabilityTests(TestCase):
    def test_detect_git_capabilities_degrades_when_worktree_missing(self) -> None:
        caps = detect_git_capabilities(
            workdir=Path("/tmp/project"),
            command_exists=lambda name: name == "git",
            run_git=FakeGit(repo=True, worktree=False),
        )
        self.assertEqual(caps.mode, "git-aware")
        self.assertFalse(caps.worktree_usable)
```

- [ ] **Step 2: Write failing doctor tests for hard failures vs warnings**

```python
class DoctorTests(TestCase):
    def test_doctor_fails_when_python_missing(self) -> None:
        report = run_doctor(
            command_exists=lambda name: name not in {"python", "python3", "py"},
            flag_probe=lambda _flag: True,
            writable_probe=lambda _path: True,
            path_probe=lambda _value: True,
            launcher_probe=lambda: (True, "launcher ok"),
        )
        self.assertFalse(report.ok)
        self.assertTrue(any(check.name == "python" and check.severity == "error" for check in report.checks))

    def test_doctor_warns_when_git_missing_but_still_reports_runtime_ready_checks(self) -> None:
        report = run_doctor(
            command_exists=lambda name: name not in {"git"},
            flag_probe=lambda _flag: True,
            writable_probe=lambda _path: True,
            path_probe=lambda _value: True,
            launcher_probe=lambda: (True, "launcher ok"),
        )
        self.assertTrue(report.ok)
        self.assertTrue(
            any(check.name == "git" and check.severity == "warning" for check in report.checks)
        )

    def test_doctor_warns_when_launcher_directory_is_not_on_path(self) -> None:
        report = run_doctor(
            command_exists=lambda _name: True,
            flag_probe=lambda _flag: True,
            writable_probe=lambda _path: True,
            path_probe=lambda _value: False,
            launcher_probe=lambda: (True, "launcher ok"),
        )
        self.assertTrue(any(check.name == "path" and check.severity == "warning" for check in report.checks))

    def test_doctor_renders_readiness_sections(self) -> None:
        report = run_doctor(
            command_exists=lambda _name: True,
            flag_probe=lambda _flag: True,
            writable_probe=lambda _path: True,
            path_probe=lambda _value: True,
            launcher_probe=lambda: (True, "launcher ok"),
        )
        rendered = render_doctor_report(report)
        self.assertIn("Install Readiness", rendered)
        self.assertIn("Runtime Readiness", rendered)
        self.assertIn("Enhanced Safety Capability", rendered)

    def test_doctor_fails_when_launcher_is_broken(self) -> None:
        report = run_doctor(
            command_exists=lambda _name: True,
            flag_probe=lambda _flag: True,
            writable_probe=lambda _path: True,
            path_probe=lambda _value: True,
            launcher_probe=lambda: (False, "launcher invocation failed"),
        )
        self.assertFalse(report.ok)
        self.assertTrue(
            any(check.name == "launcher" and check.severity == "error" for check in report.checks)
        )

    def test_doctor_fails_when_required_directory_is_unwritable(self) -> None:
        report = run_doctor(
            command_exists=lambda _name: True,
            flag_probe=lambda _flag: True,
            writable_probe=lambda path: path.name not in {"bin", "cc_collab"},
            path_probe=lambda _value: True,
            launcher_probe=lambda: (True, "launcher ok"),
        )
        self.assertFalse(report.ok)
        self.assertTrue(
            any(check.name in {"skill-dir", "bin-dir", "config-dir", "task-root"} and check.severity == "error" for check in report.checks)
        )
```

- [ ] **Step 3: Run the targeted tests and confirm red failures**

Run: `python3 -m unittest tests.test_capabilities tests.test_doctor -v`  
Expected: FAIL because `runtime.capabilities` does not exist and doctor lacks severity/guidance support.

- [ ] **Step 4: Implement `runtime/capabilities.py`**

Add focused helpers for:
- Python launcher discovery by platform
- Claude CLI readiness and flag support
- Git presence
- Git repo detection for a specific workdir
- `git worktree` usability detection
- user-facing remediation strings for Windows and macOS

- [ ] **Step 5: Refactor `runtime/doctor.py` to use capability objects**

Implementation requirements:
- add severity to checks, e.g. `error`, `warning`, `info`
- `claude` missing remains a hard failure
- missing Python remains a hard failure
- missing Git or non-repo state becomes warning/degraded
- separate checks into install readiness, runtime readiness, and enhanced safety capability sections
- surface degraded warnings for non-repo and missing `git worktree` states
- probe the installed launcher for basic usability so “launcher missing or broken” is a hard failure, not just a PATH check
- keep “launcher directory not on PATH” as an explicit warning/degraded state with guidance
- keep unwritable required directories (`skill-dir`, `bin-dir`, `config-dir`, `task-root`) as hard failures after the severity refactor
- render actionable, platform-aware next steps

- [ ] **Step 6: Re-run the targeted tests and confirm they pass**

Run: `python3 -m unittest tests.test_capabilities tests.test_doctor -v`  
Expected: PASS

- [ ] **Step 7: Commit the capability/doctor changes**

```bash
git add runtime/capabilities.py tests/test_capabilities.py tests/test_doctor.py runtime/doctor.py
git commit -m "feat: add capability-aware doctor checks"
```

### Task 3: Filesystem-Only Execution And Git-Aware Degradation

**Files:**
- Modify: `tests/test_cli.py`
- Modify: `tests/test_workspace_guard.py`
- Modify: `tests/test_worktree_manager.py`
- Modify: `runtime/cli.py`
- Modify: `runtime/workspace_guard.py`
- Modify: `runtime/worktree_manager.py`

- [ ] **Step 1: Write failing CLI tests for non-Git execution**

```python
class CliRuntimeModeTests(TestCase):
    def test_git_aware_read_only_runs_inside_repo(self) -> None:
        with TemporaryDirectory() as tmp:
            request = write_request(tmp, write_policy="read-only")
            with patch("runtime.cli.detect_runtime_capabilities", return_value=git_aware_caps()):
                exit_code = main(["run", "--request", str(request), "--task-root", tmp])
            result = json.loads((Path(tmp) / "task-1" / "result.json").read_text())
            self.assertEqual(exit_code, 0)
            self.assertEqual(result["runtime_mode"], "git-aware")

    def test_run_preflight_fails_when_claude_flags_are_unsupported(self) -> None:
        with TemporaryDirectory() as tmp:
            request = write_request(tmp, write_policy="read-only")
            with patch("runtime.cli.detect_runtime_capabilities", return_value=unsupported_claude_caps()):
                exit_code = main(["run", "--request", str(request), "--task-root", tmp])
            result = json.loads((Path(tmp) / "task-1" / "result.json").read_text())
            self.assertEqual(exit_code, 1)
            self.assertIn("flag", result["summary"].lower())
            self.assertIn("upgrade", result["remediation"].lower())

    def test_non_git_read_only_runs_in_filesystem_only_mode(self) -> None:
        with TemporaryDirectory() as tmp:
            request = write_request(tmp, write_policy="read-only")
            with patch("runtime.cli.detect_runtime_capabilities", return_value=filesystem_only_caps()):
                exit_code = main(["run", "--request", str(request), "--task-root", tmp])
            result = json.loads((Path(tmp) / "task-1" / "result.json").read_text())
            self.assertEqual(exit_code, 0)
            self.assertEqual(result["runtime_mode"], "filesystem-only")

    def test_run_preflight_persists_failure_when_claude_missing(self) -> None:
        with TemporaryDirectory() as tmp:
            request = write_request(tmp, write_policy="read-only")
            with patch("runtime.cli.detect_runtime_capabilities", return_value=missing_claude_caps()):
                exit_code = main(["run", "--request", str(request), "--task-root", tmp])
            result = json.loads((Path(tmp) / "task-1" / "result.json").read_text())
            self.assertEqual(exit_code, 1)
            self.assertEqual(result["status"], "failed")
            self.assertIn("claude", result["summary"].lower())
            self.assertIn("preflight", result["capability_summary"]["status"])
            self.assertIn("install", result["remediation"].lower())

    def test_run_preflight_fails_when_workdir_missing(self) -> None:
        with TemporaryDirectory() as tmp:
            request = write_request(tmp, workdir=str(Path(tmp) / "missing"))
            exit_code = main(["run", "--request", str(request), "--task-root", tmp])
            result = json.loads((Path(tmp) / "task-1" / "result.json").read_text())
            self.assertEqual(exit_code, 1)
            self.assertIn("workdir", result["summary"].lower())

    def test_run_preflight_writes_temp_diagnostics_when_task_root_unwritable(self) -> None:
        with TemporaryDirectory() as tmp:
            request = write_request(tmp)
            blocked_root = Path(tmp) / "blocked"
            with patch("runtime.cli.create_task_dir", side_effect=PermissionError("blocked")):
                exit_code = main(["run", "--request", str(request), "--task-root", str(blocked_root)])
            self.assertEqual(exit_code, 1)
            self.assertTrue(find_temp_diagnostic("task-1").exists())

    def test_non_git_write_isolated_uses_filesystem_copy(self) -> None:
        with TemporaryDirectory() as tmp:
            request = write_request(tmp, write_policy="write-isolated")
            with patch("runtime.cli.detect_runtime_capabilities", return_value=filesystem_only_caps()):
                exit_code = main(["run", "--request", str(request), "--task-root", tmp])
            result = json.loads((Path(tmp) / "task-1" / "result.json").read_text())
            self.assertEqual(result["runtime_mode"], "filesystem-only")

    def test_degraded_write_isolated_commit_ready_is_rewritten_to_patch_ready_before_claude(self) -> None:
        with TemporaryDirectory() as tmp:
            request = write_request(
                tmp,
                write_policy="write-isolated",
                success_terminal="commit-ready",
            )
            with patch(
                "runtime.cli.detect_runtime_capabilities",
                return_value=filesystem_only_caps(),
            ), patch("runtime.cli.build_command", return_value=["claude", "-p"]) as mock_build:
                main(["run", "--request", str(request), "--task-root", tmp])
            runtime_contract = mock_build.call_args.kwargs["runtime_contract"]
            self.assertIn("allowed_success_terminal=patch-ready", runtime_contract)
```

- [ ] **Step 2: Write failing tests for degraded `git worktree` isolation**

```python
class WorktreeFallbackTests(TestCase):
    def test_write_isolated_falls_back_when_worktree_unavailable(self) -> None:
        isolation = choose_isolation_strategy(git_available=True, repo=True, worktree_usable=False)
        self.assertEqual(isolation, "filesystem-copy")

    def test_degraded_git_aware_write_isolated_success_stays_git_aware(self) -> None:
        result = run_write_isolated_task(
            git_available=True,
            repo=True,
            worktree_usable=False,
            success_terminal="patch-ready",
        )
        self.assertEqual(result["runtime_mode"], "git-aware")
```

- [ ] **Step 3: Run the targeted tests and confirm they fail**

Run: `python3 -m unittest tests.test_cli tests.test_workspace_guard tests.test_worktree_manager -v`  
Expected: FAIL because CLI still aborts when Git capture fails and worktree manager has no degraded path.

- [ ] **Step 4: Extend `runtime/workspace_guard.py` for filesystem-only tracking**

Implementation requirements:
- support baseline snapshots without Git status text
- add helpers for directory copies or file manifests used by `write-isolated`
- preserve undeclared-file detection via hashes when Git is absent
- for filesystem-only `read-only` and `write-in-place`, snapshot the full workdir using the same exclusion list as isolated copies, then diff hashes within that bounded snapshot set instead of attempting an unbounded live tree scan
- exclude recursion hazards from filesystem copies, at minimum: the task root itself, `.git`, `__pycache__`, and transient cache directories
- for degraded Git-aware isolated runs, generate the final Git patch by diffing original repo paths against the completed filesystem copy outputs rather than by turning the copy itself into a Git repo

- [ ] **Step 5: Add explicit runtime capability selection in `runtime/cli.py`**

Implementation requirements:
- preflight with `runtime.capabilities`
- select `git-aware` or `filesystem-only`
- fail early with persisted failure artifacts when `claude` is missing or required flags are unsupported
- fail early with persisted failure artifacts when the workdir does not exist
- when the requested task root is not writable, write a diagnostic failure artifact under a temp fallback such as `tempfile.gettempdir()/ccollab-diagnostics/<task-id>/` and report that fallback path to stderr
- for `read-only` and `write-in-place`, stop treating missing Git as fatal
- for `write-isolated`, use Git worktree only when `worktree_usable` is true, otherwise create a task-owned filesystem copy
- for `write-isolated`, compute an effective success terminal before invoking Claude:
  - preserve requested `commit-ready` only in full `git-aware` mode with usable `git worktree`
  - rewrite requested `commit-ready` to `patch-ready` in degraded `git-aware` mode without usable `git worktree`
  - rewrite requested `commit-ready` to `patch-ready` in `filesystem-only` mode
  - validate completed results against this effective terminal, not the raw request value
- persist `runtime_mode` and degradation notes into result metadata
- persist a machine-readable preflight capability summary into result metadata for both success and failure paths
- persist actionable remediation text in preflight failure artifacts, including platform-specific next steps for missing/unsupported `claude`

- [ ] **Step 6: Re-run the targeted tests and confirm they pass**

Run: `python3 -m unittest tests.test_cli tests.test_workspace_guard tests.test_worktree_manager -v`  
Expected: PASS

- [ ] **Step 7: Commit the execution-mode changes**

```bash
git add tests/test_cli.py tests/test_workspace_guard.py tests/test_worktree_manager.py runtime/cli.py runtime/workspace_guard.py runtime/worktree_manager.py
git commit -m "feat: support filesystem-only task execution"
```

### Task 4: Closeout Artifacts And Result Metadata

**Files:**
- Modify: `tests/test_closeout_manager.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_result_renderer.py`
- Modify: `tests/test_validators.py`
- Modify: `runtime/closeout_manager.py`
- Modify: `runtime/artifact_store.py`
- Modify: `runtime/result_renderer.py`
- Modify: `runtime/schemas/task-result.schema.json`
- Modify: `runtime/validators.py`
- Modify: `runtime/cli.py`

- [ ] **Step 1: Write failing closeout tests for file change sets**

```python
class CloseoutManagerTests(TestCase):
    def test_file_change_set_metadata_records_artifact_type(self) -> None:
        metadata = build_file_change_set_metadata("/tmp/task-1", ["src/app.py"])
        self.assertEqual(metadata["artifact_type"], "file-change-set")
        self.assertEqual(metadata["changed_files"], ["src/app.py"])
        manifest = metadata["change_set_manifest"]
        self.assertEqual(manifest["entries"][0]["original_path"], "src/app.py")
        self.assertIsNotNone(manifest["entries"][0]["before_hash"])
        self.assertIsNotNone(manifest["entries"][0]["after_hash"])
        self.assertTrue(manifest["inspect_instructions"])
        self.assertTrue(manifest["copy_back_instructions"])

    def test_degraded_git_aware_write_isolated_closeout_still_emits_git_patch(self) -> None:
        metadata = build_git_patch_metadata_for_degraded_isolation(
            repo_root="/tmp/repo",
            isolated_copy="/tmp/task-copy",
            changed_files=["src/app.py"],
        )
        self.assertEqual(metadata["artifact_type"], "git-patch")

class ResultSchemaTests(TestCase):
    def test_task_result_schema_accepts_runtime_metadata(self) -> None:
        payload = valid_result_payload(
            runtime_mode="filesystem-only",
            artifact_type="file-change-set",
            capability_summary={"mode": "filesystem-only"},
            degraded_notes=["Git not found; filesystem-only mode active"],
        )
        validate_result(payload, write_policy="read-only", allowed_terminal_state="archived")

    def test_task_result_schema_accepts_failed_result_remediation(self) -> None:
        payload = valid_failed_result_payload(
            remediation="Install Claude CLI and re-run ccollab doctor.",
            capability_summary={"status": "preflight-failed"},
        )
        validate_result(payload, write_policy="read-only", allowed_terminal_state="inspection-required")

class CliArtifactContractTests(TestCase):
    def test_completed_result_persists_runtime_metadata(self) -> None:
        payload = run_and_load_result_for_success(write_policy="read-only", runtime_mode="filesystem-only")
        self.assertIn("artifact_type", payload)
        self.assertIn("capability_summary", payload)
        self.assertIn("degraded_notes", payload)

    def test_preflight_failure_persists_runtime_metadata_and_remediation(self) -> None:
        payload = run_and_load_result_for_preflight_failure(failure_kind="missing-claude")
        self.assertIn("capability_summary", payload)
        self.assertIn("remediation", payload)

    def test_runtime_generated_failure_result_persists_runtime_metadata(self) -> None:
        payload = run_and_load_result_for_runtime_failure(failure_kind="validation-error")
        self.assertIn("runtime_mode", payload)
        self.assertIn("artifact_type", payload)
        self.assertIn("capability_summary", payload)

    def test_normalized_unstructured_result_persists_runtime_metadata(self) -> None:
        payload = run_and_load_result_for_normalized_success(runtime_mode="filesystem-only")
        self.assertIn("runtime_mode", payload)
        self.assertIn("artifact_type", payload)
        self.assertIn("capability_summary", payload)

class ResultValidatorContractTests(TestCase):
    def test_validator_rejects_missing_runtime_metadata(self) -> None:
        payload = valid_result_payload()
        del payload["runtime_mode"]
        with self.assertRaises(ValidationError):
            validate_result(payload, write_policy="read-only", allowed_terminal_state="archived")
```

- [ ] **Step 2: Write failing renderer tests for runtime-mode visibility**

```python
class ResultRendererTests(TestCase):
    def test_result_markdown_shows_runtime_mode_and_artifact_type(self) -> None:
        markdown = render_result_markdown(
            {
                "task_id": "task-1",
                "summary": "Done",
                "runtime_mode": "filesystem-only",
                "artifact_type": "file-change-set",
                "capability_summary": {"mode": "filesystem-only"},
                "degraded_notes": ["Git not found; filesystem-only mode active"],
            }
        )
        self.assertIn("filesystem-only", markdown)
        self.assertIn("file-change-set", markdown)
        self.assertIn("Git not found", markdown)
        self.assertIn("capability", markdown.lower())
```

- [ ] **Step 3: Run the targeted tests and confirm they fail**

Run: `python3 -m unittest tests.test_closeout_manager tests.test_result_renderer tests.test_validators tests.test_cli -v`  
Expected: FAIL because file change set helpers, schema updates, CLI-persisted runtime metadata, and markdown fields do not exist yet.

- [ ] **Step 4: Implement artifact-type-aware closeout helpers**

Implementation requirements:
- define `artifact_type` semantics explicitly:
  - `git-patch` when a Git patch artifact exists
  - `file-change-set` when a filesystem-only artifact bundle exists
  - `none` when no closeout artifact is produced, such as successful `read-only` runs, successful `write-in-place` integrated runs, or failure paths that only persist diagnostic metadata
- enforce the closeout matrix below:
  - `write-isolated` + full `git-aware` + success `commit-ready` -> preserve `commit-ready`, emit commit metadata, `artifact_type=none`
  - `write-isolated` + degraded `git-aware` without usable `git worktree` + requested success `commit-ready` -> remap to `patch-ready`, emit `git-patch`
  - `write-isolated` + `filesystem-only` + requested success `commit-ready` -> remap to `patch-ready`, emit `file-change-set`
  - `write-isolated` + any mode + success `patch-ready` -> preserve `patch-ready`, emit mode-appropriate artifact
  - `read-only` + success -> preserve `archived`, `artifact_type=none`
  - `write-in-place` + success `integrated` -> preserve `integrated`, `artifact_type=none`
- keep Git patch behavior intact
- add file change set metadata for filesystem-only mode
- store changed-file copies or manifests under the task directory
- explicitly represent added, modified, deleted, and renamed files in the manifest
- include original path, stored artifact path, before hash, after hash, and change kind for each entry
- include top-level inspect/copy-back instructions in the manifest metadata so a human or downstream tool can understand how to review/apply the result
- for degraded Git-aware isolated runs, emit a Git patch by comparing original repo paths against the task-owned filesystem copy outputs rather than falling back to a file change set
- update `runtime/schemas/task-result.schema.json` so `runtime_mode`, `artifact_type`, `capability_summary`, `degraded_notes`, and remediation metadata are part of the published result contract

- [ ] **Step 5: Thread closeout metadata through `runtime/cli.py` and rendering**

Implementation requirements:
- include `artifact_type`
- include `runtime_mode` in results
- include preflight capability summary and degraded-mode notes in rendered markdown
- update `runtime/validators.py` so the runtime enforces the expanded result contract, not just the JSON schema
- keep existing terminal states compatible
- update all CLI-synthesized result paths to populate the expanded contract, including:
  - `task_failure_result(...)`
  - normalized unstructured-result success payloads
  - repair/fallback paths after malformed Claude output
  - temp-diagnostic failure artifacts written before the normal task directory exists
- ensure failed patch generation or change-set creation degrades to `inspection-required` when needed

- [ ] **Step 6: Re-run the targeted tests and confirm they pass**

Run: `python3 -m unittest tests.test_closeout_manager tests.test_result_renderer tests.test_validators tests.test_cli -v`  
Expected: PASS

- [ ] **Step 7: Commit the closeout/result changes**

```bash
git add tests/test_closeout_manager.py tests/test_cli.py tests/test_result_renderer.py tests/test_validators.py runtime/closeout_manager.py runtime/artifact_store.py runtime/result_renderer.py runtime/schemas/task-result.schema.json runtime/validators.py runtime/cli.py
git commit -m "feat: add filesystem-only closeout artifacts"
```

### Task 5: Docs, Regression Coverage, And Final Verification

**Files:**
- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `skill/delegate-to-claude-code/SKILL.md`
- Modify: `tests/test_install_docs.py`
- Create: `tests/test_installed_launcher_smoke.py`
- Modify: `examples/filesystem-only-smoke-task.json`
- Modify: `examples/git-aware-smoke-task.json`

- [ ] **Step 1: Write failing docs assertions for install-root and non-Git behavior**

```python
class InstallDocsTests(TestCase):
    def test_readme_mentions_git_optional_mode(self) -> None:
        readme = Path("README.md").read_text(encoding="utf-8")
        self.assertIn("Git is optional", readme)
        self.assertIn("filesystem-only", readme)
```

- [ ] **Step 2: Write failing installed-launcher smoke tests**

```python
class InstalledLauncherSmokeTests(TestCase):
    def test_installed_launcher_run_filesystem_only_smoke(self) -> None:
        with TemporaryDirectory() as tmp:
            result = run_installed_ccollab(
                "examples/filesystem-only-smoke-task.json",
                temp_root=tmp,
                rewrite_workdir=tmp,
                fake_claude=True,
            )
            self.assertIn(result.returncode, {0, 1})
            payload = json.loads((Path(tmp) / "smoke-filesystem" / "result.json").read_text())
            self.assertEqual(payload["runtime_mode"], "filesystem-only")

    @skipUnless(shutil.which("git"), "git required for git-aware installed-launcher smoke")
    def test_installed_launcher_run_git_aware_smoke(self) -> None:
        with TemporaryDirectory() as tmp:
            result = run_installed_ccollab(
                "examples/git-aware-smoke-task.json",
                temp_root=tmp,
                rewrite_workdir=str(Path(tmp) / "repo"),
                seed_git_repo=True,
                fake_claude=True,
            )
            self.assertIn(result.returncode, {0, 1})
            payload = json.loads((Path(tmp) / "smoke-git" / "result.json").read_text())
            self.assertEqual(payload["runtime_mode"], "git-aware")
```

- [ ] **Step 3: Run the docs and smoke tests and confirm they fail**

Run: `python3 -m unittest tests.test_install_docs tests.test_installed_launcher_smoke -v`  
Expected: FAIL because the docs do not yet describe install-root copying or filesystem-only mode, and the installed-launcher smoke harness does not exist yet.

- [ ] **Step 4: Update README, AGENTS, skill guidance, and smoke harness**

Implementation requirements:
- explain install-root behavior
- explain Python bootstrap vs `claude` guidance
- explain Git-aware vs filesystem-only runtime modes
- document manual Windows/macOS smoke validation steps
- keep the smoke request examples aligned with the shipped CLI contract
- treat `examples/*-smoke-task.json` as templates whose `workdir` field must be rewritten by the smoke harness or manual commands before execution
- make the smoke harness assert persisted result artifacts rather than shell-only success
- isolate installed-launcher smoke tests from real user directories by redirecting `HOME`, `CODEX_HOME`, `LOCALAPPDATA`, and `APPDATA` into temp roots
- skip the automated Git-aware installed-launcher smoke when `git` is unavailable, while keeping the filesystem-only smoke mandatory
- for Git-aware smoke, seed the disposable repository with an initial commit before invoking `ccollab run`
- inject a fake `claude` shim into the installed-launcher smoke harness so the suite is deterministic regardless of local Claude install/auth/version state

- [ ] **Step 5: Re-run the docs and smoke tests and confirm they pass**

Run: `python3 -m unittest tests.test_install_docs tests.test_installed_launcher_smoke -v`  
Expected: PASS

- [ ] **Step 6: Run the full automated verification suite**

Run: `python3 -m unittest discover -s tests -v`  
Expected: PASS

- [ ] **Step 7: Run automated installed-launcher smoke tests in the main suite**

Run: `python3 -m unittest tests.test_installed_launcher_smoke -v`  
Expected: PASS with the filesystem-only installed-launcher smoke case green; the Git-aware case should be PASS when `git` is available and SKIP when `git` is unavailable.

- [ ] **Step 8: Run local runtime smoke verification from this macOS workspace**

Run: `python3 -m runtime.cli doctor`  
Expected: Exit code reflects the real local environment; capture and report whether failures are due to missing local dependencies such as `claude`.

Run: `python3 -m unittest tests.test_cli -v`  
Expected: PASS with the new capability-aware CLI behavior.

Run: `bash install/install-all.sh`  
Expected: Installs the runtime payload into the Unix install root and leaves an installed `ccollab` launcher available in `~/.local/bin`.

Run: `python3 -c "import json, pathlib; src=pathlib.Path('examples/filesystem-only-smoke-task.json'); dst=pathlib.Path('/tmp/ccollab-filesystem-request.json'); data=json.loads(src.read_text()); data['workdir']='/tmp'; dst.write_text(json.dumps(data), encoding='utf-8')"`  
Expected: Writes a runnable filesystem-only smoke request with an explicit `workdir`.

Run: `~/.local/bin/ccollab run --request /tmp/ccollab-filesystem-request.json --task-root /tmp/ccollab-smoke-filesystem`  
Expected: Native installed-launcher execution reaches the filesystem-only path; if local `claude` is absent, it must fail early with a persisted preflight artifact rather than a shell error.

Run: `git init /tmp/ccollab-git-smoke && cp examples/git-aware-smoke-task.json /tmp/ccollab-git-smoke/request.template.json`  
Expected: Creates a disposable Git workdir for the Git-aware smoke path.

Run: `python3 -c "from pathlib import Path; Path('/tmp/ccollab-git-smoke/README.md').write_text('smoke\\n', encoding='utf-8')"`  
Expected: Adds a tracked file candidate for an initial commit.

Run: `git -C /tmp/ccollab-git-smoke config user.name 'ccollab smoke' && git -C /tmp/ccollab-git-smoke config user.email 'ccollab-smoke@example.com'`  
Expected: Sets repo-local Git identity so the initial commit does not depend on global developer config.

Run: `git -C /tmp/ccollab-git-smoke add README.md && git -C /tmp/ccollab-git-smoke commit -m 'init smoke repo'`  
Expected: Seeds the disposable repo with an initial commit so HEAD-dependent Git-aware checks are deterministic.

Run: `python3 -c "import json, pathlib; src=pathlib.Path('/tmp/ccollab-git-smoke/request.template.json'); dst=pathlib.Path('/tmp/ccollab-git-smoke/request.json'); data=json.loads(src.read_text()); data['workdir']='/tmp/ccollab-git-smoke'; dst.write_text(json.dumps(data), encoding='utf-8')"`  
Expected: Writes a runnable Git-aware smoke request that points at the disposable repo.

Run: `~/.local/bin/ccollab run --request /tmp/ccollab-git-smoke/request.json --task-root /tmp/ccollab-smoke-git`  
Expected: Installed-launcher execution reaches the Git-aware path or degraded-worktree path; if local `claude` is absent, the persisted failure artifact still records Git capability summary.

- [ ] **Step 9: Record manual native verification commands for Windows**

Document and execute manually outside this macOS session:
- `powershell -ExecutionPolicy Bypass -File .\\install\\install-all.ps1`
- `ccollab doctor`
- `Copy-Item .\\examples\\filesystem-only-smoke-task.json $env:TEMP\\ccollab-filesystem-request.template.json`
- `powershell -Command "$src='$env:TEMP\\ccollab-filesystem-request.template.json'; $dst='$env:TEMP\\ccollab-filesystem-request.json'; $data=Get-Content $src -Raw | ConvertFrom-Json; $data.workdir=$env:TEMP; $data | ConvertTo-Json -Depth 10 | Set-Content $dst -Encoding utf8"`
- `ccollab run --request $env:TEMP\\ccollab-filesystem-request.json --task-root $env:TEMP\\ccollab-smoke-filesystem`
- `git init $env:TEMP\\ccollab-git-smoke`
- `Set-Content -Path $env:TEMP\\ccollab-git-smoke\\README.md -Value "smoke"`
- `git -C $env:TEMP\\ccollab-git-smoke config user.name "ccollab smoke"`
- `git -C $env:TEMP\\ccollab-git-smoke config user.email "ccollab-smoke@example.com"`
- `git -C $env:TEMP\\ccollab-git-smoke add README.md`
- `git -C $env:TEMP\\ccollab-git-smoke commit -m "init smoke repo"`
- `Copy-Item .\\examples\\git-aware-smoke-task.json $env:TEMP\\ccollab-git-smoke\\request.template.json`
- `powershell -Command "$src='$env:TEMP\\ccollab-git-smoke\\request.template.json'; $dst='$env:TEMP\\ccollab-git-smoke\\request.json'; $data=Get-Content $src -Raw | ConvertFrom-Json; $data.workdir='$env:TEMP\\ccollab-git-smoke'; $data | ConvertTo-Json -Depth 10 | Set-Content $dst -Encoding utf8"`
- `ccollab run --request $env:TEMP\\ccollab-git-smoke\\request.json --task-root $env:TEMP\\ccollab-smoke-git`
- `cmd /c ccollab doctor`
- `cmd /c ccollab run --request %TEMP%\\ccollab-filesystem-request.json --task-root %TEMP%\\ccollab-smoke-filesystem-cmd`

Expected:
- install uses the Windows install root and launcher
- `doctor` reports capability status with warnings/errors
- `run` executes natively from both PowerShell and CMD and either reaches the correct mode or fails early with persisted preflight metadata

- [ ] **Step 10: Commit docs and verification-aligned changes**

```bash
git add README.md AGENTS.md skill/delegate-to-claude-code/SKILL.md tests/test_install_docs.py tests/test_installed_launcher_smoke.py examples/filesystem-only-smoke-task.json examples/git-aware-smoke-task.json
git commit -m "docs: describe python-first native runtime flow"
```

## Acceptance Matrix

| Spec success criterion | Covered by | Verified by |
|---|---|---|
| Native Windows install and run | Tasks 1, 2, 3, 5 | Task 5 manual Windows smoke commands |
| Native macOS install and run | Tasks 1, 2, 3, 5 | Task 5 local install + installed-launcher smoke |
| Install works from extracted source archive | Task 1 | `tests.test_installers` archive-install test |
| Missing Python handled by installer | Task 1 | `tests.test_installers` red/green cycle |
| Missing `claude` fails early with guidance | Tasks 2, 3 | `tests.test_doctor`, `tests.test_cli`, installed-launcher smoke |
| Git optional with degraded mode | Tasks 2, 3, 4 | `tests.test_capabilities`, `tests.test_cli`, `tests.test_closeout_manager` |
| Artifacts record execution mode and closeout type | Tasks 3, 4 | `tests.test_cli`, `tests.test_result_renderer` |
| Result contract matches persisted metadata | Task 4 | `runtime/schemas/task-result.schema.json`, `tests.test_validators` |
| Runtime behavior centralized in Python | Tasks 1, 2, 3, 4 | Code review plus targeted tests in each task |

## Execution Notes

- Follow TDD strictly for each task: write the failing test, run it red, implement the minimum code, run it green, then refactor.
- All new tests should follow the repo’s `unittest` style: implement assertions as `TestCase` methods, not bare pytest-style functions.
- Any installer or installed-launcher test must redirect `HOME`, `CODEX_HOME`, `LOCALAPPDATA`, `APPDATA`, and related install-location env vars into temp directories before invoking scripts or launchers.
- Because the current workspace already contains relevant uncommitted changes in target files, do not create a fresh worktree for this implementation unless those changes are first committed or intentionally excluded. Working in a new worktree from `HEAD` would drop task-relevant local context.
- Keep commits scoped to the task listed above; do not bundle unrelated pre-existing changes.
- After Task 5, request a final code review against the commits created by this plan before declaring the implementation complete.
