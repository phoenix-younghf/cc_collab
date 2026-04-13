# Agent Setup

Platform note:

- `macOS / Linux`: supported
- `Windows`: supported with native PowerShell / CMD, WSL not required

Install:

```bash
./install/install-all.sh
```

```powershell
powershell -ExecutionPolicy Bypass -File ./install/install-all.ps1
```

Installed-tool updates (run from any directory after install):

```bash
ccollab version
ccollab update
```

```powershell
ccollab version
ccollab update
```

Use install scripts for source/bootstrap setup, and use `ccollab update` for release-based upgrades of an existing install.

Verify:

```bash
ccollab doctor
```

If verification fails, run:

```bash
python3 -m runtime.cli doctor
```

```powershell
py -3 -m runtime.cli doctor
```

If `py` is unavailable, use `python`.

Release gate note:

- Draft releases must stay unpublished until the native Windows checks in `docs/release/ccollab-update-checklist.md` pass.

Install-root note:

- macOS: `~/Library/Application Support/cc_collab/install`
- Linux: `~/.local/share/cc_collab/install`
- Windows: `%LOCALAPPDATA%\cc_collab\install`

Smoke templates:

- `examples/filesystem-only-smoke-task.json`
- `examples/git-aware-smoke-task.json`

These example requests are templates. Rewrite their `workdir` before running them, then use `ccollab run --request ... --task-root ...` to verify either the filesystem-only path or the Git-aware path.
