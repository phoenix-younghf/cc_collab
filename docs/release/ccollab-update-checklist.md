# ccollab Release Update Checklist

Use this checklist before publishing a draft release. Keep the release as draft until every item is complete.

## Native Windows Update Validation

- [ ] Validate native Windows update from outside the install root in PowerShell.
  Commands:
  ```powershell
  Set-Location $HOME
  ccollab version
  ccollab update
  ccollab version
  ```

- [ ] Validate native Windows update from inside the install root in PowerShell.
  Commands:
  ```powershell
  Set-Location $env:LOCALAPPDATA\cc_collab\install
  & $HOME\.local\bin\ccollab.cmd update
  ccollab version
  ```

- [ ] Validate native CMD launcher behavior after update.
  Commands:
  ```cmd
  cmd /c ccollab version
  cmd /c ccollab update
  cmd /c ccollab version
  ```

## Rollback And Lock Safety

- [ ] Run one forced verification failure and confirm rollback leaves the previous install usable.
  Procedure:
  1. Start from a successful baseline: `ccollab version`.
  2. Inject a verification failure with the team-approved fault hook for this release candidate.
  3. Run `ccollab update` and confirm it fails.
  4. Re-run `ccollab version` and verify the previous version/install still runs.
  5. Disable the fault hook and run `ccollab update` again to confirm recovery.

- [ ] Validate stale-lock recovery after helper handoff.
  Procedure:
  1. Create a stale lock using the team lockfile fixture/harness.
  2. Run `ccollab update` and confirm stale-lock detection removes or supersedes it.
  3. Re-run `ccollab update` and confirm the update path proceeds normally.

## Path And Shell Coverage

- [ ] Validate install paths containing spaces.
  Commands:
  ```powershell
  $spaceRoot = Join-Path $env:LOCALAPPDATA "ccollab install spaces"
  New-Item -ItemType Directory -Force -Path $spaceRoot | Out-Null
  $env:CCOLLAB_RUNTIME_ROOT = $spaceRoot
  ccollab update
  ccollab version
  Remove-Item Env:CCOLLAB_RUNTIME_ROOT
  ```

- [ ] Validate fresh shell startup after update in both PowerShell and CMD.
  Commands:
  ```powershell
  powershell -NoProfile -Command "ccollab version; ccollab update; ccollab version"
  ```
  ```cmd
  cmd /c "ccollab version && ccollab update && ccollab version"
  ```
