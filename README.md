## Quick Install

Current installer support:

- `macOS / Linux`: supported
- `Windows`: supported with native PowerShell / CMD, WSL not required

macOS / Linux:

```bash
git clone <repo-url> ~/workspace/cc_collab
cd ~/workspace/cc_collab
./install/install-all.sh
ccollab doctor
```

Windows PowerShell:

```powershell
git clone <repo-url> $HOME\workspace\cc_collab
Set-Location $HOME\workspace\cc_collab
powershell -ExecutionPolicy Bypass -File ./install/install-all.ps1
ccollab doctor
```

## Source Install vs Installed Tool Update

Use the repo installer only for first-time setup or when developing from source:

- `./install/install-all.sh` (macOS / Linux)
- `powershell -ExecutionPolicy Bypass -File ./install/install-all.ps1` (Windows)

Use release updates for already-installed users from any directory:

```bash
ccollab version
ccollab update
```

```powershell
ccollab version
ccollab update
```

`ccollab update` upgrades the installed tool payload only. It does not pull source from your current checkout.

## Release Gate

Release automation keeps GitHub releases in `draft` until manual native Windows validation passes. Maintainers must complete [docs/release/ccollab-update-checklist.md](docs/release/ccollab-update-checklist.md) before publishing the release.

## Quick Start

Use the Codex skill at `delegate-to-claude-code` when the task is suitable for local Claude delegation, then run `ccollab` commands for request execution and closeout verification.

If `ccollab` is not resolved in the current shell yet:

```bash
source ~/.zprofile
ccollab doctor
```

If `ccollab` is not resolved in PowerShell yet, open a new terminal or refresh the current session:

```powershell
$env:Path = "$HOME\.local\bin;$env:Path"
Get-Command ccollab
ccollab doctor
```

Fallback from the repo checkout:

```bash
cd ~/workspace/cc_collab
python3 -m runtime.cli doctor
```

```powershell
Set-Location ~/workspace/cc_collab
py -3 -m runtime.cli doctor
```

If `py` is unavailable, replace it with `python`.

## What Gets Installed

- macOS / Linux: skill symlink at `$CODEX_HOME/skills/delegate-to-claude-code` (or `~/.codex/skills/delegate-to-claude-code`), plus CLI symlink at `~/.local/bin/ccollab`
- Windows: skill copy at `%CODEX_HOME%\skills\delegate-to-claude-code` (or `%USERPROFILE%\.codex\skills\delegate-to-claude-code`), plus CLI shim at `%USERPROFILE%\.local\bin\ccollab.cmd`

The installer copies a self-contained runtime payload into a user install root and points the launcher at that installed payload instead of the original checkout.

- macOS: `~/Library/Application Support/cc_collab/install`
- Linux: `~/.local/share/cc_collab/install`
- Windows: `%LOCALAPPDATA%\cc_collab\install`

## Runtime Modes

Git is optional.

- `git-aware`: selected when `git` is available and the workdir is inside a repository
- `filesystem-only`: selected when `git` is missing or the workdir is outside a repository
- if a repository is available but `git worktree` is degraded, execution remains `git-aware` and `write-isolated` tasks fall back to filesystem-copy isolation

If `Python` is missing, the installer attempts platform-native bootstrap first. If `claude` is missing or missing required flags, `doctor` and `run` fail early with guidance, but the installer still completes.

## Debugging Templates

The files under `examples/` are debugging templates, not drop-in runnable requests and not part of the default install or release validation path.

- `examples/filesystem-only-smoke-task.json`
- `examples/git-aware-smoke-task.json`

Use `ccollab version`, `ccollab doctor`, `ccollab update`, and the Windows release checklist as the default validation path. Reach for the smoke templates only when you are actively debugging `ccollab run` or Claude structured-output behavior.

Rewrite each template's `workdir` before running it.
Both shipped smoke templates pin Claude to `sonnet` and set `claude_role.timeout_seconds` to `60` so debugging stays bounded instead of hanging indefinitely.

Local validation sequence on macOS / Linux:

```bash
python3 -m runtime.cli doctor
python3 -m unittest tests.test_cli -v
bash install/install-all.sh
```

If you need a filesystem-only debug request on macOS / Linux, rewrite the template's `workdir` first:

```bash
mkdir -p /tmp/ccollab-filesystem-workdir
python3 - <<'PY'
import json
from pathlib import Path
src = Path("examples/filesystem-only-smoke-task.json")
dst = Path("/tmp/ccollab-filesystem-request.json")
data = json.loads(src.read_text(encoding="utf-8"))
data["workdir"] = "/tmp/ccollab-filesystem-workdir"
dst.write_text(json.dumps(data), encoding="utf-8")
PY
```

If you need a Git-aware debug request on macOS / Linux, point the template at a disposable repo workdir before running it:

```bash
python3 - <<'PY'
import json
from pathlib import Path
src = Path("examples/git-aware-smoke-task.json")
dst = Path("/tmp/ccollab-git-smoke/request.json")
data = json.loads(src.read_text(encoding="utf-8"))
data["workdir"] = "/tmp/ccollab-git-smoke"
dst.write_text(json.dumps(data), encoding="utf-8")
PY
```

If you need a Windows debug request, keep the JSON rewrite BOM-safe:

```powershell
New-Item -ItemType Directory -Force -Path $env:TEMP\ccollab-filesystem-workdir | Out-Null
Copy-Item .\examples\filesystem-only-smoke-task.json $env:TEMP\ccollab-filesystem-request.template.json
powershell -Command "$src='$env:TEMP\ccollab-filesystem-request.template.json'; $dst='$env:TEMP\ccollab-filesystem-request.json'; $data=Get-Content $src -Raw | ConvertFrom-Json; $data.workdir='$env:TEMP\ccollab-filesystem-workdir'; $json=$data | ConvertTo-Json -Depth 10; [System.IO.File]::WriteAllText($dst, $json, [System.Text.UTF8Encoding]::new($false))"
```

## Troubleshooting

- `ccollab: command not found`: ensure `~/.local/bin` is in your `PATH`.
- Windows `ccollab` not found: run `Get-Command ccollab` after refreshing `$env:Path`, or open a new PowerShell session.
- Skill not discovered: verify `CODEX_HOME` and the installed skill directory under `~/.codex/skills/delegate-to-claude-code`.
- Doctor failures: run `python3 -m runtime.cli doctor` on macOS / Linux, or `py -3 -m runtime.cli doctor` on Windows.
