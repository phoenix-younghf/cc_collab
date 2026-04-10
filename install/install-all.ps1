$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

& (Join-Path $Root "install\install-skill.ps1")
& (Join-Path $Root "install\install-bin.ps1")
& (Join-Path $HOME ".local\bin\ccollab.cmd") doctor
