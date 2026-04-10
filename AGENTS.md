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
