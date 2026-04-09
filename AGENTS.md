# Agent Setup

Platform note:

- `macOS / Linux`: supported
- `Windows`: use WSL for now; native PowerShell / CMD install is not supported yet

Install:

```bash
./install/install-all.sh
```

Verify:

```bash
ccollab doctor
```

If verification fails, run:

```bash
python3 -m runtime.cli doctor
```
