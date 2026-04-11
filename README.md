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

## Smoke Templates

The files under `examples/` are templates, not drop-in runnable requests.

- `examples/filesystem-only-smoke-task.json`
- `examples/git-aware-smoke-task.json`

Rewrite each template's `workdir` before running it.

Local validation sequence on macOS / Linux:

```bash
python3 -m runtime.cli doctor
python3 -m unittest tests.test_cli -v
bash install/install-all.sh
```

Filesystem-only smoke on macOS / Linux:

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
~/.local/bin/ccollab run --request /tmp/ccollab-filesystem-request.json --task-root /tmp/ccollab-smoke-filesystem
```

Git-aware smoke on macOS / Linux:

```bash
git init /tmp/ccollab-git-smoke
python3 - <<'PY'
from pathlib import Path
Path("/tmp/ccollab-git-smoke/README.md").write_text("smoke\n", encoding="utf-8")
PY
git -C /tmp/ccollab-git-smoke config user.name "ccollab smoke"
git -C /tmp/ccollab-git-smoke config user.email "ccollab-smoke@example.com"
git -C /tmp/ccollab-git-smoke add README.md
git -C /tmp/ccollab-git-smoke commit -m "init smoke repo"
python3 - <<'PY'
import json
from pathlib import Path
src = Path("examples/git-aware-smoke-task.json")
dst = Path("/tmp/ccollab-git-smoke/request.json")
data = json.loads(src.read_text(encoding="utf-8"))
data["workdir"] = "/tmp/ccollab-git-smoke"
dst.write_text(json.dumps(data), encoding="utf-8")
PY
~/.local/bin/ccollab run --request /tmp/ccollab-git-smoke/request.json --task-root /tmp/ccollab-smoke-git
```

Windows manual smoke in PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\install\install-all.ps1
ccollab doctor
New-Item -ItemType Directory -Force -Path $env:TEMP\ccollab-filesystem-workdir | Out-Null
Copy-Item .\examples\filesystem-only-smoke-task.json $env:TEMP\ccollab-filesystem-request.template.json
powershell -Command "$src='$env:TEMP\ccollab-filesystem-request.template.json'; $dst='$env:TEMP\ccollab-filesystem-request.json'; $data=Get-Content $src -Raw | ConvertFrom-Json; $data.workdir='$env:TEMP\ccollab-filesystem-workdir'; $json=$data | ConvertTo-Json -Depth 10; [System.IO.File]::WriteAllText($dst, $json, [System.Text.UTF8Encoding]::new($false))"
ccollab run --request $env:TEMP\ccollab-filesystem-request.json --task-root $env:TEMP\ccollab-smoke-filesystem
git init $env:TEMP\ccollab-git-smoke
Set-Content -Path $env:TEMP\ccollab-git-smoke\README.md -Value "smoke"
git -C $env:TEMP\ccollab-git-smoke config user.name "ccollab smoke"
git -C $env:TEMP\ccollab-git-smoke config user.email "ccollab-smoke@example.com"
git -C $env:TEMP\ccollab-git-smoke add README.md
git -C $env:TEMP\ccollab-git-smoke commit -m "init smoke repo"
Copy-Item .\examples\git-aware-smoke-task.json $env:TEMP\ccollab-git-smoke\request.template.json
powershell -Command "$src='$env:TEMP\ccollab-git-smoke\request.template.json'; $dst='$env:TEMP\ccollab-git-smoke\request.json'; $data=Get-Content $src -Raw | ConvertFrom-Json; $data.workdir='$env:TEMP\ccollab-git-smoke'; $json=$data | ConvertTo-Json -Depth 10; [System.IO.File]::WriteAllText($dst, $json, [System.Text.UTF8Encoding]::new($false))"
ccollab run --request $env:TEMP\ccollab-git-smoke\request.json --task-root $env:TEMP\ccollab-smoke-git
```

Windows CMD smoke can reuse the same prepared request:

```cmd
cmd /c ccollab doctor
cmd /c ccollab run --request %TEMP%\ccollab-filesystem-request.json --task-root %TEMP%\ccollab-smoke-filesystem-cmd
```

## Troubleshooting

- `ccollab: command not found`: ensure `~/.local/bin` is in your `PATH`.
- Windows `ccollab` not found: run `Get-Command ccollab` after refreshing `$env:Path`, or open a new PowerShell session.
- Skill not discovered: verify `CODEX_HOME` and the installed skill directory under `~/.codex/skills/delegate-to-claude-code`.
- Doctor failures: run `python3 -m runtime.cli doctor` on macOS / Linux, or `py -3 -m runtime.cli doctor` on Windows.
