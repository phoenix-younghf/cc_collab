$ErrorActionPreference = "Stop"

$TargetDir = Join-Path $HOME ".local\bin"
$Target = Join-Path $TargetDir "ccollab.cmd"
$RuntimeRoot = if ($env:CCOLLAB_RUNTIME_ROOT) {
    $env:CCOLLAB_RUNTIME_ROOT
} elseif ($env:LOCALAPPDATA) {
    Join-Path $env:LOCALAPPDATA "cc_collab\install"
} else {
    Join-Path $HOME "AppData\Local\cc_collab\install"
}
$PayloadLauncher = Join-Path $RuntimeRoot "bin\ccollab.cmd"

New-Item -ItemType Directory -Force -Path $TargetDir | Out-Null

$Launcher = @"
@echo off
setlocal
set "CCOLLAB_RUNTIME_ROOT=$RuntimeRoot"
call "$PayloadLauncher" %*
"@

Set-Content -Path $Target -Value $Launcher -Encoding ascii

$NormalizedTargetDir = $TargetDir.TrimEnd('\')
$UserPath = [Environment]::GetEnvironmentVariable("PATH", "User")
$UserEntries = @()
if ($UserPath) {
    $UserEntries = $UserPath -split ";" | Where-Object { $_ }
}
if (-not ($UserEntries | Where-Object { $_.TrimEnd('\') -ieq $NormalizedTargetDir })) {
    $UpdatedUserPath = if ($UserPath) { "$UserPath;$TargetDir" } else { $TargetDir }
    [Environment]::SetEnvironmentVariable("PATH", $UpdatedUserPath, "User")
}

$SessionEntries = $env:PATH -split ";" | Where-Object { $_ }
if (-not ($SessionEntries | Where-Object { $_.TrimEnd('\') -ieq $NormalizedTargetDir })) {
    $env:PATH = if ($env:PATH) { "$TargetDir;$env:PATH" } else { $TargetDir }
}

Write-Output "Current session PATH includes: $TargetDir"
