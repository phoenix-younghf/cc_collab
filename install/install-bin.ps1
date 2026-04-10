$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$TargetDir = Join-Path $HOME ".local\bin"
$Target = Join-Path $TargetDir "ccollab.cmd"
$RepoLauncher = Join-Path $Root "bin\ccollab.cmd"

New-Item -ItemType Directory -Force -Path $TargetDir | Out-Null

$Launcher = @"
@echo off
setlocal
call "$RepoLauncher" %*
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
