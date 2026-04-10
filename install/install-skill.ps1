$ErrorActionPreference = "Stop"

$CodexHome = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $HOME ".codex" }
$RuntimeRoot = if ($env:CCOLLAB_RUNTIME_ROOT) {
    $env:CCOLLAB_RUNTIME_ROOT
} elseif ($env:LOCALAPPDATA) {
    Join-Path $env:LOCALAPPDATA "cc_collab\install"
} else {
    Join-Path $HOME "AppData\Local\cc_collab\install"
}
$Source = Join-Path $RuntimeRoot "skill\delegate-to-claude-code"
$Target = Join-Path $CodexHome "skills\delegate-to-claude-code"

New-Item -ItemType Directory -Force -Path (Split-Path $Target -Parent) | Out-Null
if (Test-Path $Target) {
    Remove-Item $Target -Recurse -Force
}
Copy-Item -Path $Source -Destination $Target -Recurse
