$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

function Write-Stderr {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Message
    )

    [Console]::Error.WriteLine($Message)
}

function Get-InstallRoot {
    if ($env:LOCALAPPDATA) {
        return Join-Path $env:LOCALAPPDATA "cc_collab\install"
    }
    return Join-Path $HOME "AppData\Local\cc_collab\install"
}

function Test-PythonCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Command
    )

    try {
        $commandName = $Command[0]
        $commandArgs = @()
        if ($Command.Length -gt 1) {
            $commandArgs = $Command[1..($Command.Length - 1)]
        }
        & $commandName @commandArgs "-c" "import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)" | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

function Find-PythonCommand {
    $candidates = @(
        @("py", "-3"),
        @("python"),
        @("python3")
    )
    foreach ($candidate in $candidates) {
        if (Test-PythonCommand -Command $candidate) {
            return ,$candidate
        }
    }
    return $null
}

function Install-Python {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Output "Attempting to install Python via winget..."
        try {
            & winget install --exact --id Python.Python.3
            if ($LASTEXITCODE -eq 0) {
                return
            }
        } catch {
        }
        Write-Stderr "winget install Python failed."
    }
    Write-Stderr "Install Python 3.9 or newer and rerun .\install\install-all.ps1. If winget is available, try: winget install --exact --id Python.Python.3"
}

function Copy-Payload {
    param(
        [Parameter(Mandatory = $true)]
        [string]$InstallRoot
    )

    if (Test-Path $InstallRoot) {
        Remove-Item $InstallRoot -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $InstallRoot | Out-Null
    foreach ($relative in @("bin", "runtime", "skill", "install", "examples")) {
        Copy-Item -Path (Join-Path $Root $relative) -Destination (Join-Path $InstallRoot $relative) -Recurse
    }
    foreach ($relative in @("README.md", "AGENTS.md")) {
        Copy-Item -Path (Join-Path $Root $relative) -Destination (Join-Path $InstallRoot $relative)
    }
}

$PythonCommand = Find-PythonCommand
if (-not $PythonCommand) {
    Install-Python
    $PythonCommand = Find-PythonCommand
}
if (-not $PythonCommand) {
    exit 1
}

$InstallRoot = Get-InstallRoot
$env:CCOLLAB_RUNTIME_ROOT = $InstallRoot
Copy-Payload -InstallRoot $InstallRoot

& (Join-Path $Root "install\install-skill.ps1")
& (Join-Path $Root "install\install-bin.ps1")

$Launcher = Join-Path $HOME ".local\bin\ccollab.cmd"
& $Launcher doctor
$DoctorExit = $LASTEXITCODE
if ($DoctorExit -ne 0) {
    Write-Stderr "ccollab installed, but runtime readiness still needs attention."
}
exit 0
