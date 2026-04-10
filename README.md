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

## Troubleshooting

- `ccollab: command not found`: ensure `~/.local/bin` is in your `PATH`.
- Windows `ccollab` not found: run `Get-Command ccollab` after refreshing `$env:Path`, or open a new PowerShell session.
- Skill not discovered: verify `CODEX_HOME` and the installed skill directory under `~/.codex/skills/delegate-to-claude-code`.
- Doctor failures: run `python3 -m runtime.cli doctor` on macOS / Linux, or `py -3 -m runtime.cli doctor` on Windows.
