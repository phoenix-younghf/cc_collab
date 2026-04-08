## Quick Install

```bash
git clone <repo-url> ~/workspace/cc_collab
cd ~/workspace/cc_collab
./install/install-all.sh
ccollab doctor
```

## Quick Start

Use the Codex skill at `delegate-to-claude-code` when the task is suitable for local Claude delegation, then run `ccollab` commands for request execution and closeout verification.

If `ccollab` is not resolved in the current shell yet:

```bash
source ~/.zprofile
ccollab doctor
```

Fallback from the repo checkout:

```bash
cd ~/workspace/cc_collab
python3 -m runtime.cli doctor
```

## What Gets Installed

- Skill symlink: `$CODEX_HOME/skills/delegate-to-claude-code` (or `~/.codex/skills/delegate-to-claude-code`)
- CLI symlink: `~/.local/bin/ccollab`

## Troubleshooting

- `ccollab: command not found`: ensure `~/.local/bin` is in your `PATH`.
- Skill not discovered: verify `CODEX_HOME` and symlink target with `ls -l ~/.codex/skills/delegate-to-claude-code`.
- Doctor failures: run `python3 -m runtime.cli doctor` for actionable findings.
