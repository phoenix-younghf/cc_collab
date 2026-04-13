# Release-Based Update Design

**Date:** 2026-04-13
**Project Root:** `/Users/steven/Workspace/cc_collab`
**Status:** Draft for user review

## Goal

Turn `ccollab` into a releasable internal tool that supports product-style upgrades on Windows, macOS, and Linux without requiring users to pull source code or rerun install scripts from a checkout.

The first supported release backend is a private GitHub.com repository. Client-side auth should reuse local `gh` login state. The update path should operate from any directory through `ccollab update`.

## Non-Goals

- Supporting arbitrary version selection in v1
- Updating Python automatically
- Updating Claude CLI automatically
- Requiring a local source checkout for upgrades
- Binding the client update protocol directly to GitHub API specifics

## Product Requirements

### User Experience

- Users can run `ccollab version` from any directory.
- Users can run `ccollab update` from any directory.
- `ccollab update` upgrades only `ccollab` itself.
- The update target is always the latest stable release.
- If the current install is a pre-versioned legacy install, `ccollab update` can still upgrade it.

### Distribution Model

- Releases are published from a private GitHub.com repository.
- Clients authenticate through the locally installed `gh` CLI.
- The release contract must be portable so the download source can later move to GitHub Enterprise or internal object storage without redesigning the client.

### Safety

- Failed downloads, checksum mismatches, bad archives, and failed validation must leave the existing installation unchanged.
- If replacement succeeds but post-install verification fails, the updater must roll back to the previous install root.
- Launchers remain thin shims. The replaceable payload lives under the install root.

## High-Level Design

The release system has five layers:

1. **Release manifest**
   - A machine-readable document describing the latest stable version and its platform assets.
   - The client reads this contract instead of speaking directly in GitHub-specific terms.

2. **Platform payload**
   - Each release publishes a full install-root payload per platform.
   - The payload layout matches what the installer currently copies into the install root.

3. **Installed metadata**
   - Every install writes a local metadata file describing the installed version and source.
   - `ccollab version` reads this metadata.

4. **Update client**
   - `ccollab update` checks GitHub release state through `gh`, downloads the correct asset, verifies it, stages it, replaces the install root, and rolls back on failure.

5. **Launcher contract**
   - `~/.local/bin/ccollab` and `~/.local/bin/ccollab.cmd` remain stable entrypoints.
   - They continue to point at the active install root payload.

## Release Contract

### Versioning

- Use semantic versions.
- Git tags follow `vX.Y.Z`, for example `v0.4.2`.
- v1 supports only the `stable` channel.

### Release Assets

Each stable release must include:

- `ccollab-manifest.json`
- `ccollab-windows-x64.zip`
- `ccollab-macos-universal.tar.gz`
- `ccollab-linux-x64.tar.gz`

### Manifest Shape

The manifest is the client-facing release contract. A representative payload:

```json
{
  "version": "0.4.2",
  "channel": "stable",
  "repo": "owner/cc_collab",
  "published_at": "2026-04-13T12:00:00Z",
  "assets": [
    {
      "platform": "windows-x64",
      "name": "ccollab-windows-x64.zip",
      "sha256": "..."
    },
    {
      "platform": "macos-universal",
      "name": "ccollab-macos-universal.tar.gz",
      "sha256": "..."
    },
    {
      "platform": "linux-x64",
      "name": "ccollab-linux-x64.tar.gz",
      "sha256": "..."
    }
  ]
}
```

### Payload Layout

Each platform archive contains the installable runtime payload, not a source checkout. The extracted root must contain:

- `bin/`
- `runtime/`
- `skill/`
- `install/`
- `examples/`
- `README.md`
- `AGENTS.md`

This keeps installation and update behavior aligned and lets the client validate archive structure before replacing the install root.

## Installed Metadata

Installations write `install-metadata.json` into the install root. Suggested fields:

```json
{
  "version": "0.4.2",
  "channel": "stable",
  "repo": "owner/cc_collab",
  "platform": "windows-x64",
  "installed_at": "2026-04-13T12:34:56Z",
  "asset_name": "ccollab-windows-x64.zip",
  "asset_sha256": "...",
  "install_root": "C:\\Users\\zengs\\AppData\\Local\\cc_collab\\install"
}
```

If this file is missing but the install root exists, the runtime treats the installation as legacy:

- `version`: `unknown`
- `channel`: `unknown`
- `source`: `legacy-install`

Legacy installs are still upgradeable.

## Command Behavior

### `ccollab version`

Responsibilities:

- Read installed metadata
- Print installed version, install root, source repo, and channel
- Gracefully degrade for legacy installs

Representative output:

```text
ccollab 0.4.2
install root: C:\Users\zengs\AppData\Local\cc_collab\install
source: github.com/owner/cc_collab
channel: stable
```

Legacy output:

```text
ccollab unknown
install root: C:\Users\zengs\AppData\Local\cc_collab\install
source: legacy-install
channel: unknown
```

### `ccollab update`

Responsibilities:

- Run from any directory
- Check latest stable release only
- Reuse `gh` login state
- Download the platform asset
- Verify checksum
- Replace the install root safely
- Rebuild launcher and skill install if needed
- Run post-install verification

Representative success output:

```text
Current version: 0.4.1
Latest version: 0.4.2
Downloading ccollab-windows-x64.zip...
Verifying checksum...
Installing update...
Running post-install verification...
Updated ccollab to 0.4.2
```

Representative no-op output:

```text
Current version: 0.4.2
Latest version: 0.4.2
ccollab is already up to date.
```

Representative failure output:

```text
Current version: 0.4.1
Latest version: 0.4.2
Downloading ccollab-windows-x64.zip...
Verifying checksum...
Update failed: checksum mismatch
Existing installation was left unchanged.
```

### Dependency Failure Messaging

- Missing `gh`:
  - `Install GitHub CLI and run 'gh auth login'.`
- Not authenticated:
  - `Run 'gh auth login' for github.com, then retry.`
- No access to the private repo:
  - `Authenticated GitHub CLI could not access owner/cc_collab releases.`
- Missing install root:
  - v1 returns an actionable error and directs the user to the normal install flow.

## Update Flow

1. Detect platform and install root.
2. Read `install-metadata.json` if present.
3. Verify `gh` exists and is authenticated.
4. Query the latest stable release for the configured repository.
5. Download `ccollab-manifest.json`.
6. Parse manifest and select the asset for the current platform.
7. Compare the installed version to the manifest version.
8. If already current, exit cleanly.
9. Download the platform archive to a temp directory.
10. Verify SHA256 against the manifest.
11. Extract into a staging directory.
12. Validate required payload structure.
13. Move the current install root to a backup location.
14. Move the staged payload into the install root.
15. Refresh launcher and skill installation.
16. Write new `install-metadata.json`.
17. Run `ccollab doctor`.
18. On success, delete the backup.
19. On failure after replacement, restore the backup and report the rollback.

## Rollback Model

### No-Touch Failures

These failures must not modify the current installation:

- manifest fetch failure
- asset download failure
- checksum mismatch
- archive extraction failure
- invalid payload structure

### Rollback Failures

If the install root has already been replaced, these failures trigger rollback:

- launcher refresh failure
- skill refresh failure
- metadata write failure
- post-install `ccollab doctor` failure

The updater should report both the upgrade failure and whether rollback succeeded.

## Implementation Structure

### New Modules

- `runtime/versioning.py`
  - metadata IO
  - legacy-install detection
  - platform identifier resolution

- `runtime/release_manifest.py`
  - manifest parsing
  - manifest validation
  - platform asset lookup

- `runtime/updater.py`
  - `gh` integration
  - download logic
  - checksum verification
  - staging, replacement, and rollback

### Modified Modules

- `runtime/cli.py`
  - add `version`
  - add `update`

- installer scripts
  - write `install-metadata.json` during install
  - keep launcher semantics unchanged

### New Supporting Automation

- `scripts/build_release_payload.py`
  - build platform archives
  - emit `ccollab-manifest.json`

- GitHub Actions release workflow
  - build release payloads
  - upload assets to a private release

## Testing Strategy

### Unit Tests

- `tests/test_versioning.py`
  - metadata read/write
  - legacy-install detection
  - platform resolution

- `tests/test_release_manifest.py`
  - manifest parsing
  - missing/invalid fields
  - platform selection

- `tests/test_updater.py`
  - fake `gh` success path
  - auth failure
  - repo access failure
  - checksum mismatch
  - invalid archive
  - rollback after post-install failure

### CLI Tests

- extend `tests/test_cli.py`
  - `ccollab version`
  - `ccollab update`
  - already-up-to-date path
  - legacy-install path

### Installer Tests

- extend `tests/test_installers.py`
  - metadata creation during install
  - launcher still points at install root

### Packaging Tests

- validate release payload structure
- validate manifest contents
- confirm archive names match the client platform mapping

All GitHub integration tests should use fake `gh` shims. The test suite should not require real network access or real GitHub credentials.

## Risks

### Risk: release and installer payloads drift apart

Mitigation:

- build release archives from the same payload contract the installer uses
- validate required paths during both packaging and update

### Risk: updater becomes GitHub-specific

Mitigation:

- keep GitHub logic confined to `runtime/updater.py`
- keep the client-facing contract centered on the manifest

### Risk: legacy installs produce confusing UX

Mitigation:

- surface `unknown` version explicitly
- allow upgrade from legacy installs without forcing manual cleanup

### Risk: broken release bricks local installs

Mitigation:

- require checksum validation
- require staging validation
- require rollback after failed post-install verification

## Rollout Plan

1. Add installed metadata and `ccollab version`.
2. Add release manifest support and updater internals.
3. Add `ccollab update` for latest stable only.
4. Add release packaging script and CI workflow.
5. Document install vs update semantics for Windows, macOS, and Linux.
6. Validate end-to-end with fake GitHub integration in tests and manual Windows verification.

## Success Criteria

- A Windows user with a prior install can run `ccollab update` from any directory.
- The updater downloads the latest stable private GitHub release using local `gh` auth.
- A successful update replaces the install root without requiring a source checkout.
- A failed update leaves the previous installation intact.
- `ccollab version` reports meaningful installed-version data, including legacy fallback.
