# Release-Based Update Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add product-style `ccollab version` and `ccollab update` support backed by private GitHub releases, with safe install-root replacement, rollback, and a release pipeline that ships draft releases until manual Windows validation passes.

**Architecture:** Keep the launcher thin and continue treating the install root as the replaceable unit. Add three Python layers: install metadata and discovery, release-manifest parsing and GitHub release resolution, and an updater transaction engine that enforces same-volume staging, locking, compatibility preflight, helper-based Windows swaps, and direct post-install verification. Build release archives from the install payload contract and publish them as draft GitHub releases with a final manifest asset that binds the client to one immutable release instance.

**Tech Stack:** Python standard library, `unittest`, Bash, PowerShell, Windows CMD, GitHub Actions, GitHub CLI (`gh`)

---

## Current State

- [`runtime/cli.py`](/Users/steven/Workspace/cc_collab/runtime/cli.py) only supports `run`, `status`, `open`, `cleanup`, and `doctor`; there is no installed-version or update flow.
- [`install/install-all.sh`](/Users/steven/Workspace/cc_collab/install/install-all.sh) and [`install/install-all.ps1`](/Users/steven/Workspace/cc_collab/install/install-all.ps1) copy a payload into the install root, but they do not write version metadata.
- There is no release contract parser, no updater transaction module, no release payload builder, and no GitHub Actions workflow.
- Existing tests cover installers, launchers, and native runtime behavior, but they do not cover installed-version metadata, GitHub release selection, updater locking/rollback, or release payload generation.
- The approved spec is [`docs/superpowers/specs/2026-04-13-release-based-update-design.md`](/Users/steven/Workspace/cc_collab/docs/superpowers/specs/2026-04-13-release-based-update-design.md).

## File Structure

### New Production Files

- `runtime/versioning.py`
  - Install metadata dataclasses, JSON IO, install-root discovery, and legacy-install fallback.
- `runtime/release_manifest.py`
  - Manifest dataclasses, validation, asset lookup, and release-identity checks.
- `runtime/updater.py`
  - GitHub release resolution, lock handling, compatibility preflight, same-volume work-area creation, swap/rollback orchestration, and helper handoff.
- `scripts/build_release_payload.py`
  - Build per-platform archives and create the manifest input/final manifest payload used by the release workflow.
- `.github/workflows/release.yml`
  - Draft-release automation that builds assets, uploads archives, creates the final manifest, and keeps the release draft until manual Windows validation is complete.
- `tests/test_versioning.py`
  - Metadata and install-discovery unit tests.
- `tests/test_release_manifest.py`
  - Manifest parsing and validation tests.
- `tests/test_updater.py`
  - Updater unit tests for release selection, locking, compatibility preflight, same-volume staging, helper handoff, verification semantics, and rollback.
- `tests/test_release_payload.py`
  - Build-script tests for archive layout and manifest generation.
- `docs/release/ccollab-update-checklist.md`
  - Release-gate checklist with the required native Windows validation sequence.

### Modified Production Files

- `runtime/cli.py`
  - Add `version` and `update` subcommands and wire them to the new helpers.
- `runtime/constants.py`
  - Add versioning/update constants such as metadata filename and release asset names if needed.
- `runtime/doctor.py`
  - Reuse dependency checks from the updater or expose helpers if direct reuse is awkward.
- `install/install-all.sh`
  - Write install metadata after payload copy succeeds.
- `install/install-all.ps1`
  - Same on Windows.
- `README.md`
  - Document `ccollab version`, `ccollab update`, release/update semantics, and the draft-release Windows gate.
- `AGENTS.md`
  - Keep install/update instructions aligned with the new release-based workflow.

### Modified Test Files

- `tests/test_cli.py`
  - Add CLI-level `version` and `update` coverage.
- `tests/test_installers.py`
  - Assert install metadata creation and launcher compatibility with installed metadata.
- `tests/test_install_docs.py`
  - Assert the docs mention version/update usage, release semantics, and the Windows release gate.

## Task Decomposition

### Task 1: Install Metadata, Install Discovery, And `ccollab version`

**Files:**
- Create: `runtime/versioning.py`
- Create: `tests/test_versioning.py`
- Modify: `runtime/cli.py`
- Modify: `runtime/constants.py`
- Modify: `install/install-all.sh`
- Modify: `install/install-all.ps1`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_installers.py`

- [ ] **Step 1: Write failing unit tests for install metadata and install-root discovery**

```python
class VersioningTests(TestCase):
    def test_write_and_read_install_metadata_round_trip(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "install"
            root.mkdir()
            metadata = InstallMetadata(
                version="0.4.2",
                channel="stable",
                repo="owner/cc_collab",
                platform="windows-x64",
                installed_at="2026-04-13T12:34:56Z",
                asset_name="ccollab-windows-x64.zip",
                asset_sha256="abc123",
                install_root=str(root),
            )
            write_install_metadata(root, metadata)
            self.assertEqual(read_install_metadata(root), metadata)

    def test_discover_install_root_prefers_active_runtime_root(self) -> None:
        with TemporaryDirectory() as tmp:
            active_root = Path(tmp) / "active-install"
            (active_root / "runtime").mkdir(parents=True)
            (active_root / "bin").mkdir()
            write_install_metadata(
                active_root,
                InstallMetadata(
                    version="0.4.2",
                    channel="stable",
                    repo="owner/cc_collab",
                    platform="linux-x64",
                    installed_at="2026-04-13T12:34:56Z",
                    asset_name="unknown",
                    asset_sha256="unknown",
                    install_root=str(active_root),
                ),
            )
            discovery = discover_install_root(
                active_runtime_root=str(active_root),
                env={},
                os_name="posix",
            )
            self.assertEqual(discovery.install_root, active_root)
            self.assertEqual(discovery.status, "installed")
            self.assertEqual(discovery.version, "0.4.2")

    def test_discover_install_root_treats_metadata_less_active_payload_as_legacy_install(self) -> None:
        with TemporaryDirectory() as tmp:
            active_root = Path(tmp) / "active-install"
            (active_root / "runtime").mkdir(parents=True)
            (active_root / "bin").mkdir()
            discovery = discover_install_root(
                active_runtime_root=str(active_root),
                env={},
                os_name="posix",
            )
            self.assertEqual(discovery.install_root, active_root)
            self.assertEqual(discovery.status, "legacy-install")
            self.assertEqual(discovery.version, "unknown")

    def test_discover_install_root_returns_legacy_when_payload_exists_without_metadata(self) -> None:
        with TemporaryDirectory() as tmp:
            install_root = Path(tmp) / "install"
            (install_root / "runtime").mkdir(parents=True)
            (install_root / "bin").mkdir()
            discovery = discover_install_root(
                active_runtime_root=None,
                env={"HOME": tmp},
                os_name="posix",
                default_install_root=install_root,
            )
            self.assertEqual(discovery.status, "legacy-install")
            self.assertEqual(discovery.version, "unknown")

    def test_discover_install_root_rejects_conflicting_installs(self) -> None:
        with TemporaryDirectory() as tmp:
            default_root = Path(tmp) / "default-install"
            override_root = Path(tmp) / "override-install"
            (default_root / "runtime").mkdir(parents=True)
            (default_root / "bin").mkdir()
            (override_root / "runtime").mkdir(parents=True)
            (override_root / "bin").mkdir()
            with self.assertRaises(MultipleInstallRootsError):
                discover_install_root(
                    active_runtime_root=None,
                    env={"CCOLLAB_RUNTIME_ROOT": str(override_root), "HOME": tmp},
                    os_name="posix",
                    default_install_root=default_root,
                    reject_conflicting_roots=True,
                )

    def test_resolve_platform_identifier_maps_supported_platforms(self) -> None:
        cases = [
            (("win32", "AMD64"), "windows-x64"),
            (("linux", "x86_64"), "linux-x64"),
            (("darwin", "arm64"), "macos-universal"),
        ]
        for (system_name, machine_name), expected in cases:
            with self.subTest(system_name=system_name, machine_name=machine_name):
                with patch("runtime.versioning.sys.platform", system_name):
                    with patch("runtime.versioning.platform.machine", return_value=machine_name):
                        self.assertEqual(resolve_platform_identifier(), expected)
```

- [ ] **Step 2: Run the new versioning tests and confirm they fail**

Run: `python3 -m unittest tests.test_versioning -v`  
Expected: FAIL with missing `runtime.versioning` module and undefined metadata/discovery/platform-resolution helpers.

- [ ] **Step 3: Implement `runtime/versioning.py` with metadata IO and install discovery**

```python
@dataclass(frozen=True)
class InstallMetadata:
    version: str
    channel: str
    repo: str
    platform: str
    installed_at: str
    asset_name: str
    asset_sha256: str
    install_root: str


@dataclass(frozen=True)
class InstallDiscovery:
    install_root: Path
    status: str
    metadata: InstallMetadata | None
    version: str
    channel: str
    repo: str
```

Implementation requirements:
- keep the metadata filename in `runtime/constants.py`
- add `CCOLLAB_PROJECT_VERSION` in `runtime/constants.py` and use it everywhere this plan needs a source version
- treat an install root as valid only when required installed-payload paths are present, including both `runtime/` and `bin/`
- resolve the current runtime into the manifest platform identifiers `windows-x64`, `linux-x64`, and `macos-universal`
- treat `CCOLLAB_RUNTIME_ROOT` as an explicit override, not the default identity
- return one consistent `InstallDiscovery` object shape from `discover_install_root()`
- detect conflicting install roots and raise a dedicated error with remediation context instead of picking one heuristically
- surface `unknown` / `legacy-install` values exactly as the spec describes

- [ ] **Step 4: Write failing CLI tests for `ccollab version` output**

```python
class CliVersionTests(TestCase):
    def test_version_reports_installed_metadata(self) -> None:
        with TemporaryDirectory() as tmp:
            install_root = Path(tmp) / "install"
            install_root.mkdir()
            write_install_metadata(
                install_root,
                InstallMetadata(
                    version="0.4.2",
                    channel="stable",
                    repo="owner/cc_collab",
                    platform="linux-x64",
                    installed_at="2026-04-13T12:34:56Z",
                    asset_name="ccollab-linux-x64.tar.gz",
                    asset_sha256="abc123",
                    install_root=str(install_root),
                ),
            )
            discovery = InstallDiscovery(
                install_root=install_root,
                status="installed",
                metadata=read_install_metadata(install_root),
                version="0.4.2",
                channel="stable",
                repo="owner/cc_collab",
            )
            with patch("runtime.cli.discover_install_root", return_value=discovery):
                stdout = io.StringIO()
                with redirect_stdout(stdout):
                    exit_code = main(["version"])
            self.assertEqual(exit_code, 0)
            self.assertIn("ccollab 0.4.2", stdout.getvalue())

    def test_version_reports_legacy_install(self) -> None:
        discovery = InstallDiscovery(
            install_root=Path("/tmp/legacy-install"),
            status="legacy-install",
            metadata=None,
            version="unknown",
            channel="unknown",
            repo="legacy-install",
        )
        with patch("runtime.cli.discover_install_root", return_value=discovery):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = main(["version"])
        self.assertEqual(exit_code, 0)
        self.assertIn("ccollab unknown", stdout.getvalue())
        self.assertIn("legacy-install", stdout.getvalue())

    def test_version_reports_multiple_install_remediation(self) -> None:
        with patch("runtime.cli.discover_install_root", side_effect=MultipleInstallRootsError("Set CCOLLAB_RUNTIME_ROOT to the intended install and retry.")):
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                exit_code = main(["version"])
        self.assertNotEqual(exit_code, 0)
        self.assertIn("CCOLLAB_RUNTIME_ROOT", stderr.getvalue())
```

- [ ] **Step 5: Run the CLI version tests and confirm they fail**

Run: `python3 -m unittest tests.test_cli -v`  
Expected: FAIL because `version` is not a valid CLI command and the handler does not exist.

- [ ] **Step 6: Add the `version` command to `runtime/cli.py`**

Implementation requirements:
- add a `version` subparser
- print exactly the four user-facing lines from the spec
- return actionable non-zero failure only when no valid install root can be discovered
- surface the multiple-install-root remediation text from the spec when discovery raises that error
- keep `version` read-only and free of update-lock side effects

- [ ] **Step 7: Write failing installer tests for metadata creation**

```python
class InstallerTests(TestCase):
    def test_install_all_sh_writes_install_metadata(self) -> None:
        with TemporaryDirectory() as tmp:
            result = run_install_script_with_fake_python(temp_root=tmp)
            install_root = _platform_install_root(Path(tmp) / "home")
            self.assertEqual(result.returncode, 0)
            self.assertTrue((install_root / "install-metadata.json").exists())

    def test_install_all_ps1_writes_install_metadata(self) -> None:
        with TemporaryDirectory() as tmp:
            result = run_install_script_with_fake_windows_python(temp_root=tmp)
            install_root = (Path(tmp) / "home" / "AppData" / "Local" / "cc_collab" / "install")
            self.assertEqual(result.returncode, 0)
            self.assertTrue((install_root / "install-metadata.json").exists())
```

- [ ] **Step 8: Run the installer tests and confirm they fail**

Run: `python3 -m unittest tests.test_installers -v`  
Expected: FAIL because the install scripts do not write install metadata yet.

- [ ] **Step 9: Update the install scripts to write metadata after payload copy**

Implementation requirements:
- write `install-metadata.json` only after payload copy succeeds
- use the installed payload path, not the checkout path
- stamp `version` from the canonical project version constant
- record the platform asset name as `unknown` for source installs until release-based installs take over
- keep install success semantics unchanged when `doctor` later reports missing `claude`

- [ ] **Step 10: Re-run all Task 1 tests and confirm they pass**

Run: `python3 -m unittest tests.test_versioning tests.test_cli tests.test_installers -v`  
Expected: PASS with new metadata/discovery/platform-resolution coverage green and no installer regressions.

- [ ] **Step 11: Commit Task 1**

```bash
git add runtime/versioning.py runtime/cli.py runtime/constants.py install/install-all.sh install/install-all.ps1 tests/test_versioning.py tests/test_cli.py tests/test_installers.py
git commit -m "feat: add install metadata and version command"
```

### Task 2: Release Manifest Parsing And Stable Release Resolution

**Files:**
- Create: `runtime/release_manifest.py`
- Create: `runtime/updater.py`
- Create: `tests/test_release_manifest.py`
- Create: `tests/test_updater.py`
- Modify: `runtime/constants.py`

- [ ] **Step 1: Write failing manifest parsing tests**

```python
class ReleaseManifestTests(TestCase):
    def test_parse_manifest_requires_release_identity_fields(self) -> None:
        payload = {
            "version": "0.4.2",
            "channel": "stable",
            "repo": "owner/cc_collab",
            "tag": "v0.4.2",
            "release_id": 123,
            "published_at": "2026-04-13T12:00:00Z",
            "compatibility": {"python_min": "3.9", "claude_required_flags": ["--print"]},
            "assets": [
                {
                    "platform": "windows-x64",
                    "name": "ccollab-windows-x64.zip",
                    "asset_id": 111,
                    "size_bytes": 42,
                    "sha256": "abc123",
                }
            ],
        }
        manifest = parse_release_manifest(payload)
        self.assertEqual(manifest.release_id, 123)
        self.assertEqual(manifest.asset_for("windows-x64").asset_id, 111)

    def test_parse_manifest_rejects_missing_asset_id(self) -> None:
        with self.assertRaises(ValueError):
            parse_release_manifest(
                {
                    "version": "0.4.2",
                    "channel": "stable",
                    "repo": "owner/cc_collab",
                    "tag": "v0.4.2",
                    "release_id": 123,
                    "published_at": "2026-04-13T12:00:00Z",
                    "compatibility": {"python_min": "3.9", "claude_required_flags": ["--print"]},
                    "assets": [
                        {
                            "platform": "windows-x64",
                            "name": "ccollab-windows-x64.zip",
                            "size_bytes": 42,
                            "sha256": "abc123",
                        }
                    ],
                }
            )
```

- [ ] **Step 2: Run the manifest tests and confirm they fail**

Run: `python3 -m unittest tests.test_release_manifest -v`  
Expected: FAIL with missing `runtime.release_manifest` symbols.

- [ ] **Step 3: Implement `runtime/release_manifest.py`**

```python
@dataclass(frozen=True)
class ManifestCompatibility:
    python_min: str
    claude_required_flags: tuple[str, ...]


@dataclass(frozen=True)
class ReleaseAsset:
    platform: str
    name: str
    asset_id: int
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class ReleaseManifest:
    version: str
    channel: str
    repo: str
    tag: str
    release_id: int
    published_at: str
    compatibility: ManifestCompatibility
    assets: tuple[ReleaseAsset, ...]
```

Implementation requirements:
- validate every spec-required field
- expose `asset_for(platform: str)` and a release-identity validation helper

- [ ] **Step 4: Write failing updater tests for GitHub release selection**

```python
class UpdaterReleaseResolutionTests(TestCase):
    def test_resolve_latest_stable_release_reports_missing_gh(self) -> None:
        runner = FakeGh(error=FileNotFoundError("gh missing"))
        with self.assertRaisesRegex(GhPrerequisiteError, "Install GitHub CLI"):
            resolve_latest_stable_release("owner/cc_collab", runner.run_json)

    def test_resolve_latest_stable_release_reports_unauthenticated_gh(self) -> None:
        runner = FakeGh(stderr="gh auth login required", returncode=1)
        with self.assertRaisesRegex(GhAuthenticationError, "gh auth login"):
            resolve_latest_stable_release("owner/cc_collab", runner.run_json)

    def test_resolve_latest_stable_release_reports_repo_access_denied(self) -> None:
        runner = FakeGh(stderr="HTTP 404", returncode=1)
        with self.assertRaisesRegex(RepoAccessError, "could not access owner/cc_collab releases"):
            resolve_latest_stable_release("owner/cc_collab", runner.run_json)

    def test_resolve_latest_stable_release_ignores_drafts_and_prereleases(self) -> None:
        gh = FakeGh(
            release_list=[
                {"tagName": "v0.4.2", "isDraft": True, "isPrerelease": False},
                {"tagName": "v0.4.1", "isDraft": False, "isPrerelease": False},
            ]
        )
        release = resolve_latest_stable_release("owner/cc_collab", gh.run_json)
        self.assertEqual(release.tag, "v0.4.1")

    def test_resolve_latest_stable_release_prefers_highest_semver(self) -> None:
        gh = FakeGh(
            release_list=[
                {"tagName": "v0.4.2", "isDraft": False, "isPrerelease": False},
                {"tagName": "v0.10.0", "isDraft": False, "isPrerelease": False},
            ]
        )
        release = resolve_latest_stable_release("owner/cc_collab", gh.run_json)
        self.assertEqual(release.tag, "v0.10.0")

    def test_download_release_manifest_reports_fetch_failure(self) -> None:
        runner = FakeGh(stderr="manifest download failed", returncode=1)
        with self.assertRaisesRegex(DownloadError, "manifest download failed"):
            download_release_manifest(
                repo="owner/cc_collab",
                release_id=123,
                asset_name="ccollab-manifest.json",
                runner=runner.run_download,
            )

    def test_download_platform_asset_reports_fetch_failure(self) -> None:
        runner = FakeGh(stderr="asset download failed", returncode=1)
        with self.assertRaisesRegex(DownloadError, "asset download failed"):
            download_release_asset(
                repo="owner/cc_collab",
                release_id=123,
                asset_id=111,
                asset_name="ccollab-windows-x64.zip",
                runner=runner.run_download,
            )
```

- [ ] **Step 5: Run the updater tests and confirm they fail**

Run: `python3 -m unittest tests.test_updater -v`  
Expected: FAIL because release resolution and download helpers do not exist.

- [ ] **Step 6: Implement stable release resolution and download helpers in `runtime/updater.py`**

Implementation requirements:
- call `gh` through a small injectable runner
- resolve the GitHub repository slug from `CCOLLAB_RELEASE_REPOSITORY` in `runtime/constants.py` instead of ad hoc string literals
- resolve the latest stable release by semver among non-draft, non-prerelease releases
- model the resolved GitHub release separately from the manifest payload
- implement explicit manifest-download and platform-asset-download helpers that bind requests to the resolved repo/release/asset identity
- translate missing `gh`, unauthenticated `gh`, and repo access denial into dedicated updater errors with the spec’s remediation text
- reject tag/manifest/release ID mismatches before asset download

- [ ] **Step 7: Re-run Task 2 tests and confirm they pass**

Run: `python3 -m unittest tests.test_release_manifest tests.test_updater -v`  
Expected: PASS with manifest validation, semver release selection, release-download helper coverage, and `gh` remediation coverage green.

- [ ] **Step 8: Commit Task 2**

```bash
git add runtime/release_manifest.py runtime/updater.py runtime/constants.py tests/test_release_manifest.py tests/test_updater.py
git commit -m "feat: add release manifest and stable release resolution"
```

### Task 3: Update Transaction Primitives

**Files:**
- Modify: `runtime/updater.py`
- Modify: `runtime/versioning.py`
- Modify: `runtime/doctor.py`
- Modify: `tests/test_updater.py`

- [ ] **Step 1: Write failing tests for same-volume work areas, update locks, and no-touch download failures**

```python
class UpdaterTransactionTests(TestCase):
    def test_create_work_area_rejects_cross_volume_swap(self) -> None:
        with TemporaryDirectory() as tmp:
            install_root = Path(tmp) / "install"
            install_root.mkdir()
            with patch("runtime.updater.same_filesystem", return_value=False):
                with self.assertRaises(RuntimeError):
                    create_update_work_area(install_root)

    def test_acquire_update_lock_blocks_second_owner(self) -> None:
        with TemporaryDirectory() as tmp:
            install_root = Path(tmp) / "install"
            install_root.mkdir()
            lock = acquire_update_lock(install_root)
            with self.assertRaises(UpdateLockedError):
                acquire_update_lock(install_root)
            lock.release()

    def test_checksum_mismatch_leaves_install_root_unchanged(self) -> None:
        with TemporaryDirectory() as tmp:
            install_root = seed_install_root(tmp, version="0.4.1")
            work_area = seed_update_work_area(tmp)
            before = snapshot_tree(install_root)
            with self.assertRaises(ChecksumMismatchError):
                verify_downloaded_archive(
                    archive_path=work_area / "payload.zip",
                    expected_sha256="expected",
                    expected_size=42,
                )
            self.assertEqual(snapshot_tree(install_root), before)

    def test_manifest_fetch_failure_leaves_install_root_unchanged(self) -> None:
        with TemporaryDirectory() as tmp:
            install_root = seed_install_root(tmp, version="0.4.1")
            before = snapshot_tree(install_root)
            with self.assertRaises(DownloadError):
                stage_release_manifest(
                    install_root=install_root,
                    downloader=lambda *_args, **_kwargs: (_ for _ in ()).throw(DownloadError("manifest fetch failed")),
                )
            self.assertEqual(snapshot_tree(install_root), before)

    def test_asset_download_failure_leaves_install_root_unchanged(self) -> None:
        with TemporaryDirectory() as tmp:
            install_root = seed_install_root(tmp, version="0.4.1")
            before = snapshot_tree(install_root)
            with self.assertRaises(DownloadError):
                stage_release_asset(
                    install_root=install_root,
                    downloader=lambda *_args, **_kwargs: (_ for _ in ()).throw(DownloadError("asset download failed")),
                )
            self.assertEqual(snapshot_tree(install_root), before)

    def test_invalid_archive_leaves_install_root_unchanged(self) -> None:
        with TemporaryDirectory() as tmp:
            install_root = seed_install_root(tmp, version="0.4.1")
            before = snapshot_tree(install_root)
            with self.assertRaises(InvalidArchiveError):
                extract_release_archive(Path(tmp) / "broken.zip", Path(tmp) / "stage")
            self.assertEqual(snapshot_tree(install_root), before)

    def test_acquire_update_lock_records_owner_metadata(self) -> None:
        with TemporaryDirectory() as tmp:
            install_root = Path(tmp) / "install"
            install_root.mkdir()
            lock = acquire_update_lock(install_root, pid=1234, hostname="test-host")
            record = read_update_lock_record(install_root)
            self.assertEqual(record.pid, 1234)
            self.assertEqual(record.hostname, "test-host")
            self.assertEqual(record.install_root, str(install_root.resolve()))
            self.assertIsNotNone(record.acquired_at)
            lock.release()
```

- [ ] **Step 2: Run the transaction tests and confirm they fail**

Run: `python3 -m unittest tests.test_updater -v`  
Expected: FAIL because lock/work-area helpers, no-touch download staging, and lock-record helpers do not exist.

- [ ] **Step 3: Implement lock handling, same-volume work areas, and no-touch download staging**

Implementation requirements:
- derive the lock path only after canonical install-root discovery
- keep lock metadata in a file near the install root parent
- record PID, hostname, canonical install root, and timestamp in the lock metadata
- detect stale locks without reclaiming a live lock
- create staging and backup roots under the install-root parent only
- keep manifest fetch failure, asset download failure, checksum mismatch, size mismatch, archive extraction failure, and payload validation failure on the no-touch side of the transaction

- [ ] **Step 4: Write failing tests for compatibility preflight**

```python
class UpdaterCompatibilityTests(TestCase):
    def test_compatibility_preflight_rejects_old_python(self) -> None:
        manifest = make_manifest(python_min="3.12")
        with patch("runtime.updater.detect_python_capability", return_value=PythonCapability(True, "python3", None)):
            with patch("runtime.updater.python_version_tuple", return_value=(3, 11, 9)):
                with self.assertRaises(CompatibilityError):
                    run_compatibility_preflight(manifest)

    def test_compatibility_preflight_rejects_missing_claude_flag(self) -> None:
        manifest = make_manifest(required_flags=["--json-schema"])
        with patch("runtime.updater.detect_claude_capabilities", return_value=ClaudeCapability(True, ["--json-schema"], "upgrade")):
            with self.assertRaises(CompatibilityError):
                run_compatibility_preflight(manifest)
```

- [ ] **Step 5: Run the compatibility tests and confirm they fail**

Run: `python3 -m unittest tests.test_updater -v`  
Expected: FAIL because compatibility preflight does not exist.

- [ ] **Step 6: Implement compatibility preflight and staged metadata generation**

Implementation requirements:
- reuse the existing capability detectors where possible
- reject incompatible Python or Claude before any filesystem mutation
- write install metadata into the staged payload before swap
- keep Git optional; Git readiness must not gate updates

- [ ] **Step 7: Write failing tests for payload-structure validation before swap**

```python
class UpdaterPayloadValidationTests(TestCase):
    def test_missing_runtime_directory_rejects_staged_payload_before_swap(self) -> None:
        with TemporaryDirectory() as tmp:
            install_root = seed_install_root(tmp, version="0.4.1")
            staged_root = Path(tmp) / "staged-install"
            staged_root.mkdir()
            before = snapshot_tree(install_root)
            with self.assertRaises(InvalidPayloadError):
                validate_staged_payload(staged_root)
            self.assertEqual(snapshot_tree(install_root), before)
```

- [ ] **Step 8: Run the payload-validation tests and confirm they fail**

Run: `python3 -m unittest tests.test_updater -v`  
Expected: FAIL because staged payload validation does not exist yet.

- [ ] **Step 9: Implement staged payload validation**

Implementation requirements:
- validate required payload entries before any rename
- treat invalid payload structure as a no-touch failure
- prove via tests that install-root contents are unchanged on validation failure

- [ ] **Step 10: Write failing tests for Windows helper handoff lock ownership**

```python
class UpdaterHandoffTests(TestCase):
    def test_helper_handoff_marks_lock_as_transferred(self) -> None:
        with TemporaryDirectory() as tmp:
            install_root = Path(tmp) / "install"
            install_root.mkdir()
            record = begin_windows_handoff(install_root, owner_pid=123, helper_pid=456)
            self.assertEqual(record.helper_pid, 456)
            self.assertTrue(lock_handoff_active(install_root))

    def test_stale_lock_recovery_refuses_active_handoff(self) -> None:
        with TemporaryDirectory() as tmp:
            install_root = Path(tmp) / "install"
            install_root.mkdir()
            begin_windows_handoff(install_root, owner_pid=123, helper_pid=456)
            with self.assertRaises(UpdateLockedError):
                recover_or_acquire_lock(install_root, current_pid=999)
```

- [ ] **Step 11: Run the handoff tests and confirm they fail**

Run: `python3 -m unittest tests.test_updater -v`  
Expected: FAIL because helper handoff state is not modeled yet.

- [ ] **Step 12: Implement helper handoff state in `runtime/updater.py`**

Implementation requirements:
- persist the handoff record next to the lock file
- prevent stale-lock recovery from reclaiming an active helper-owned handoff
- keep lock transfer logic separate from the actual rename/swap implementation

- [ ] **Step 13: Re-run Task 3 tests and confirm they pass**

Run: `python3 -m unittest tests.test_updater -v`  
Expected: PASS with no-touch safety, lock, compatibility, same-volume, payload validation, and helper handoff coverage green.

- [ ] **Step 14: Commit Task 3**

```bash
git add runtime/updater.py runtime/versioning.py runtime/doctor.py tests/test_updater.py
git commit -m "feat: add updater transaction primitives"
```

### Task 4: `ccollab update` And Windows-Safe Swap Execution

**Files:**
- Modify: `runtime/cli.py`
- Modify: `runtime/updater.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_updater.py`

- [ ] **Step 1: Write failing CLI tests for the `update` command**

```python
class CliUpdateTests(TestCase):
    def test_update_reports_already_up_to_date(self) -> None:
        with patch("runtime.cli.run_update", return_value=UpdateResult.noop("0.4.2")):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = main(["update"])
        self.assertEqual(exit_code, 0)
        self.assertIn("already up to date", stdout.getvalue())

    def test_update_reports_broken_launcher_remediation(self) -> None:
        with patch("runtime.cli.run_update", side_effect=BrokenLauncherError("repair launcher")):
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                exit_code = main(["update"])
        self.assertNotEqual(exit_code, 0)
        self.assertIn("repair the launcher", stderr.getvalue())

    def test_update_reports_multiple_install_remediation(self) -> None:
        with patch("runtime.cli.run_update", side_effect=MultipleInstallRootsError("Set CCOLLAB_RUNTIME_ROOT to the intended install and retry.")):
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                exit_code = main(["update"])
        self.assertNotEqual(exit_code, 0)
        self.assertIn("CCOLLAB_RUNTIME_ROOT", stderr.getvalue())

    def test_update_reports_missing_install_root_remediation(self) -> None:
        with patch("runtime.cli.run_update", side_effect=InstallRootNotFoundError("Run the normal install flow first.")):
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                exit_code = main(["update"])
        self.assertNotEqual(exit_code, 0)
        self.assertIn("install flow", stderr.getvalue())

    def test_update_allows_legacy_install_upgrade(self) -> None:
        with patch("runtime.cli.run_update", return_value=UpdateResult.success(current_version="unknown", latest_version="0.4.2")):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = main(["update"])
        self.assertEqual(exit_code, 0)
        self.assertIn("Current version: unknown", stdout.getvalue())
        self.assertIn("Updated ccollab to 0.4.2", stdout.getvalue())
```

- [ ] **Step 2: Run the CLI tests and confirm they fail**

Run: `python3 -m unittest tests.test_cli -v`  
Expected: FAIL because `update` is not a CLI command and the result formatting does not exist.

- [ ] **Step 3: Add `update` parsing and result rendering to `runtime/cli.py`**

Implementation requirements:
- add an `update` subparser
- keep output aligned with the spec’s success / noop / failure examples
- route all heavy lifting through `runtime.updater`
- surface remediation for missing install roots, broken launchers, multiple install roots, missing `gh`, unauthenticated `gh`, repo access denial, and compatibility failures
- allow legacy installs with current version `unknown` to continue into the upgrade flow instead of treating them as semver-comparable current releases
- keep `version` and `update` free of task-artifact side effects

- [ ] **Step 4: Write failing updater tests for Windows-safe swap behavior and verification guardrails**

```python
class UpdaterSwapTests(TestCase):
    def test_update_invoked_from_inside_install_root_moves_to_neutral_workdir(self) -> None:
        with TemporaryDirectory() as tmp:
            install_root = Path(tmp) / "install"
            install_root.mkdir()
            with patch("runtime.updater.current_working_directory", return_value=install_root / "runtime"):
                plan = prepare_windows_swap(
                    install_root=install_root,
                    staged_root=Path(tmp) / "staged-install",
                    backup_root=Path(tmp) / "install-backup",
                    helper_executable=Path(tmp) / "helper.py",
                )
            self.assertNotEqual(plan.working_directory, install_root / "runtime")

    def test_windows_post_install_verification_uses_argument_vector(self) -> None:
        cmd = build_windows_verification_command(Path(r"C:\Users\Name With Spaces\AppData\Local\cc_collab\install"))
        self.assertEqual(
            cmd,
            ["cmd", "/c", r"C:\Users\Name With Spaces\AppData\Local\cc_collab\install\bin\ccollab.cmd", "doctor"],
        )

    def test_post_install_verification_sets_runtime_root_env_and_timeout(self) -> None:
        with TemporaryDirectory() as tmp:
            install_root = Path(tmp) / "install"
            completed = subprocess.CompletedProcess(args=["cmd"], returncode=0, stdout="ok", stderr="")
            with patch("runtime.updater.subprocess.run", return_value=completed) as run_mock:
                run_post_install_verification(
                    install_root=install_root,
                    verification_context=VerificationContext(os_name="windows", timeout_seconds=45),
                )
            self.assertEqual(
                run_mock.call_args.kwargs["env"]["CCOLLAB_RUNTIME_ROOT"],
                str(install_root),
            )
            self.assertEqual(run_mock.call_args.kwargs["timeout"], 45)

    def test_post_install_verification_allows_stderr_when_exit_code_is_zero(self) -> None:
        with TemporaryDirectory() as tmp:
            install_root = Path(tmp) / "install"
            completed = subprocess.CompletedProcess(
                args=["cmd"],
                returncode=0,
                stdout="doctor ok",
                stderr="warning output",
            )
            with patch("runtime.updater.subprocess.run", return_value=completed):
                result = run_post_install_verification(
                    install_root=install_root,
                    verification_context=VerificationContext(os_name="windows", timeout_seconds=45),
                )
            self.assertEqual(result.exit_code, 0)
            self.assertEqual(result.stderr, "warning output")
```

- [ ] **Step 5: Run the updater swap tests and confirm they fail**

Run: `python3 -m unittest tests.test_updater -v`  
Expected: FAIL because neutral-workdir prep, helper swap planning, and verification argument-vector / environment / timeout helpers do not exist.

- [ ] **Step 6: Implement helper-based Windows swap and direct post-install verification**

Implementation requirements:
- if the current process cannot safely rename the active install tree, persist swap intent and hand off to a helper process outside the tree
- ensure both parent and helper run outside install/staging/backup directories
- use rename-only semantics for backup/install swaps
- run post-install verification in a fresh child process directly from the newly installed payload, not through PATH
- set `CCOLLAB_RUNTIME_ROOT` to the newly installed root in the verification child environment
- enforce a bounded timeout for verification and treat timeout as verification failure
- capture verification stdout/stderr in the update log and do not fail on stderr alone when the exit code is `0`
- keep Windows verification as an argument vector end-to-end; do not rebuild it as a shell string

- [ ] **Step 7: Write failing tests for rollback after verification failure**

```python
class UpdaterRollbackTests(TestCase):
    def test_verification_failure_restores_backup(self) -> None:
        with TemporaryDirectory() as tmp:
            install_root = seed_install_root(tmp, version="0.4.1")
            staged_root = seed_staged_root(tmp, version="0.4.2")
            with patch("runtime.updater.run_post_install_verification", side_effect=VerificationError("doctor failed")):
                result = apply_update_transaction(
                    install_root=install_root,
                    staged_root=staged_root,
                    backup_root=Path(tmp) / "install-backup",
                    verification_context=VerificationContext(os_name="posix"),
                )
            self.assertEqual(read_install_version(install_root), "0.4.1")
            self.assertFalse(result.ok)

    def test_checksum_failure_preserves_original_install_without_backup_swap(self) -> None:
        with TemporaryDirectory() as tmp:
            install_root = seed_install_root(tmp, version="0.4.1")
            before = snapshot_tree(install_root)
            with self.assertRaises(ChecksumMismatchError):
                apply_pre_swap_download_validation(
                    install_root=install_root,
                    archive_path=Path(tmp) / "payload.zip",
                    expected_sha256="expected",
                    expected_size=42,
                )
            self.assertEqual(snapshot_tree(install_root), before)

    def test_legacy_install_uses_unknown_as_upgradeable_current_version(self) -> None:
        with TemporaryDirectory() as tmp:
            install_root = seed_legacy_install_root(tmp)
            result = plan_update_for_install(
                install_discovery=discover_install_root(
                    active_runtime_root=str(install_root),
                    env={},
                    os_name="posix",
                ),
                target_manifest=make_manifest(version="0.4.2"),
            )
            self.assertEqual(result.current_version, "unknown")
            self.assertFalse(result.already_up_to_date)
```

- [ ] **Step 8: Run the `update` and rollback tests and confirm they fail**

Run: `python3 -m unittest tests.test_cli tests.test_updater -v`  
Expected: FAIL because rollback-aware update transaction, no-touch failure guarantees, and CLI update result wiring are still incomplete.

- [ ] **Step 9: Finish the transaction integration and re-run Task 4 tests**

Run: `python3 -m unittest tests.test_cli tests.test_updater -v`  
Expected: PASS with `update` CLI behavior, remediation paths, helper handoff, argument-vector verification, verification env/timeout/output semantics, no-touch failures, and rollback coverage green.

- [ ] **Step 10: Commit Task 4**

```bash
git add runtime/cli.py runtime/updater.py tests/test_cli.py tests/test_updater.py
git commit -m "feat: add release update command"
```

### Task 5: Release Payload Builder And Draft-Release Workflow

**Files:**
- Create: `scripts/build_release_payload.py`
- Create: `.github/workflows/release.yml`
- Create: `tests/test_release_payload.py`

- [ ] **Step 1: Write failing tests for payload archive generation**

```python
class ReleasePayloadTests(TestCase):
    def test_build_payload_archives_expected_layout(self) -> None:
        with TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "dist"
            build_release_payload(output_dir=output_dir, version="0.4.2")
            self.assertTrue((output_dir / "ccollab-windows-x64.zip").exists())
            self.assertTrue((output_dir / "ccollab-linux-x64.tar.gz").exists())
            self.assertTrue((output_dir / "ccollab-macos-universal.tar.gz").exists())

    def test_build_payload_archives_include_required_runtime_entries(self) -> None:
        with TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "dist"
            build_release_payload(output_dir=output_dir, version="0.4.2")
            expected_entries = {
                "ccollab-windows-x64.zip": ["runtime/cli.py", "bin/ccollab.cmd", "README.md"],
                "ccollab-linux-x64.tar.gz": ["runtime/cli.py", "bin/ccollab", "README.md"],
                "ccollab-macos-universal.tar.gz": ["runtime/cli.py", "bin/ccollab", "README.md"],
            }
            for archive_name, required_entries in expected_entries.items():
                entries = list_archive_entries(output_dir / archive_name)
                for required_entry in required_entries:
                    self.assertIn(required_entry, entries)

    def test_build_payload_uses_spec_platform_names(self) -> None:
        with TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "dist"
            build_release_payload(output_dir=output_dir, version="0.4.2")
            names = sorted(path.name for path in output_dir.iterdir())
            self.assertIn("ccollab-windows-x64.zip", names)
            self.assertIn("ccollab-macos-universal.tar.gz", names)
            self.assertIn("ccollab-linux-x64.tar.gz", names)

    def test_write_manifest_binds_release_and_asset_identity(self) -> None:
        manifest = write_release_manifest(
            version="0.4.2",
            repo="owner/cc_collab",
            tag="v0.4.2",
            release_id=123,
            assets=[
                {
                    "platform": "windows-x64",
                    "name": "ccollab-windows-x64.zip",
                    "asset_id": 111,
                    "size_bytes": 42,
                    "sha256": "abc123",
                }
            ],
        )
        self.assertEqual(manifest["release_id"], 123)
        self.assertEqual(manifest["assets"][0]["asset_id"], 111)
```

- [ ] **Step 2: Run the payload tests and confirm they fail**

Run: `python3 -m unittest tests.test_release_payload -v`  
Expected: FAIL with missing build script helpers.

- [ ] **Step 3: Implement `scripts/build_release_payload.py`**

Implementation requirements:
- build the three platform archives from the install payload contract
- reuse one source payload layout for all archive formats
- emit an intermediate artifact manifest with asset names, sizes, and sha256 values
- validate the required payload entries across all three platform archives, not just one representative archive
- expose a second code path that can write the final `ccollab-manifest.json` after the workflow knows the GitHub release and asset IDs

- [ ] **Step 4: Add the GitHub Actions release workflow**

Workflow requirements:
- trigger on version tags
- create or resolve the tagged GitHub release as a draft
- build and upload the platform archives first
- capture release ID and asset IDs after archive upload
- generate and upload the final `ccollab-manifest.json`
- leave the release in draft state so `ccollab update` ignores it until manual Windows validation passes

- [ ] **Step 5: Re-run the payload tests and confirm they pass**

Run: `python3 -m unittest tests.test_release_payload -v`  
Expected: PASS with archive layout and manifest identity coverage green.

- [ ] **Step 6: Commit Task 5**

```bash
git add scripts/build_release_payload.py .github/workflows/release.yml tests/test_release_payload.py
git commit -m "feat: add release payload automation"
```

### Task 6: Documentation, Release Gate, And Final Verification Coverage

**Files:**
- Create: `docs/release/ccollab-update-checklist.md`
- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `tests/test_install_docs.py`

- [ ] **Step 1: Write failing documentation tests**

```python
class InstallDocsTests(TestCase):
    def test_readme_mentions_version_and_update_commands(self) -> None:
        readme = Path("README.md").read_text(encoding="utf-8")
        self.assertIn("ccollab version", readme)
        self.assertIn("ccollab update", readme)

    def test_readme_mentions_draft_release_windows_gate(self) -> None:
        readme = Path("README.md").read_text(encoding="utf-8")
        self.assertIn("draft", readme.lower())
        self.assertIn("Windows", readme)

    def test_agents_doc_points_to_release_checklist(self) -> None:
        agents = Path("AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("ccollab-update-checklist", agents)
```

- [ ] **Step 2: Run the doc tests and confirm they fail**

Run: `python3 -m unittest tests.test_install_docs -v`  
Expected: FAIL because the docs do not mention release-based update commands or the Windows release gate yet.

- [ ] **Step 3: Write the release checklist**

Checklist requirements:
- native Windows update from outside the install root
- native Windows update from inside the install root
- forced verification failure followed by successful rollback
- stale-lock recovery after helper handoff
- install paths containing spaces
- fresh PowerShell and CMD launcher validation after update

- [ ] **Step 4: Update README and AGENTS**

Documentation requirements:
- explain `ccollab version`
- explain `ccollab update`
- explain that releases stay draft until manual Windows validation passes
- link to the release checklist for maintainers
- keep source-install instructions and installed-tool update instructions clearly separated

- [ ] **Step 5: Re-run the documentation tests and confirm they pass**

Run: `python3 -m unittest tests.test_install_docs -v`  
Expected: PASS with new version/update docs and release-gate references present.

- [ ] **Step 6: Run the full automated test suite**

Run: `python3 -m unittest -v`  
Expected: PASS with the new versioning, manifest, updater, payload, installer, CLI, and documentation coverage green.

- [ ] **Step 7: Record the manual Windows release-gate commands in the checklist**

Required commands:

```powershell
ccollab version
ccollab update
Set-Location $env:LOCALAPPDATA\cc_collab\install
& $HOME\.local\bin\ccollab.cmd update
```

Add one rollback-injection procedure and one path-with-spaces verification procedure to the checklist.

- [ ] **Step 8: Commit Task 6**

```bash
git add docs/release/ccollab-update-checklist.md README.md AGENTS.md tests/test_install_docs.py
git commit -m "docs: add release update guidance and gates"
```

## Final Verification

- [ ] Run: `python3 -m unittest -v`
- [ ] Run: `python3 scripts/build_release_payload.py --output-dir /tmp/ccollab-release-smoke --version 0.4.2`
- [ ] Confirm the output directory contains the three platform archives plus a manifest input artifact
- [ ] Confirm the release workflow YAML references draft releases and final-manifest upload
- [ ] Confirm `docs/release/ccollab-update-checklist.md` includes the Windows release-gate checklist

## Execution Handoff

Plan complete once this document is approved in review. Implementation should proceed task-by-task with review after each task, not as one large batch.
