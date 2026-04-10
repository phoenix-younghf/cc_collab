$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$CodexHome = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $HOME ".codex" }
$Source = Join-Path $Root "skill\delegate-to-claude-code"
$Target = Join-Path $CodexHome "skills\delegate-to-claude-code"

New-Item -ItemType Directory -Force -Path (Split-Path $Target -Parent) | Out-Null
if (Test-Path $Target) {
    Remove-Item $Target -Recurse -Force
}
Copy-Item -Path $Source -Destination $Target -Recurse
