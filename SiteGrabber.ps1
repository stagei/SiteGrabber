<#
.SYNOPSIS
    PowerShell convenience wrapper for SiteGrabber.

.DESCRIPTION
    Calls the Python SiteGrabber module with the specified parameters.
    Requires Python 3.10+ and the dependencies from requirements.txt.

.EXAMPLE
    .\run.ps1 -InputAddress "https://www.ibm.com/docs/en/db2/12.1.x" -OutputFolder "C:\opt\data\SiteGrabber\ibm-db2"

.EXAMPLE
    .\run.ps1 -InputAddress "https://www.ibm.com/docs/en/db2/12.1.x" -OutputFolder "C:\opt\data\SiteGrabber\ibm-db2" -InputLimitationType "class" -InputLimitationText "ibmdocs-toc-link" -Recursive
#>

param(
    [Parameter(Mandatory)]
    [string]$InputAddress,

    [Parameter(Mandatory)]
    [string]$OutputFolder,

    [string]$InputLimitationType,

    [string]$InputLimitationText,

    [switch]$Recursive,

    [switch]$NoRecursive,

    [double]$Delay = 0.5,

    [int]$MaxPages = 0,

    [int]$Timeout = 30,

    [switch]$Resume,

    [switch]$Verbose
)

$ErrorActionPreference = 'Stop'

# Locate Python - prefer py launcher, then explicit Python312 path, then PATH
$pythonExe = $null

# Try py launcher first (handles version selection)
$pyLauncher = Get-Command py -ErrorAction SilentlyContinue |
    Select-Object -First 1 -ExpandProperty Source
if ($pyLauncher) {
    $pythonExe = $pyLauncher
}

# Try well-known install location
if (-not $pythonExe) {
    $knownPath = "$($env:LOCALAPPDATA)\Programs\Python\Python312\python.exe"
    if (Test-Path $knownPath) {
        $pythonExe = $knownPath
    }
}

# Try PATH (excluding WindowsApps stub)
if (-not $pythonExe) {
    $pythonExe = Get-Command python -ErrorAction SilentlyContinue |
        Where-Object { $_.Source -notlike '*WindowsApps*' } |
        Select-Object -First 1 -ExpandProperty Source
}

if (-not $pythonExe) {
    Write-Error "Python not found. Install Python 3.10+ and ensure it is on PATH."
    exit 1
}

# Build argument list
$argList = @(
    '-m', 'sitegrabber',
    '--input-address', $InputAddress,
    '--output-folder', $OutputFolder
)

if ($InputLimitationType) {
    $argList += '--limitation-type', $InputLimitationType
}

if ($InputLimitationText) {
    $argList += '--limitation-text', $InputLimitationText
}

if ($NoRecursive) {
    $argList += '--no-recursive'
} elseif ($Recursive) {
    $argList += '--recursive'
}

if ($Delay -ne 0.5) {
    $argList += '--delay', $Delay.ToString()
}

if ($MaxPages -gt 0) {
    $argList += '--max-pages', $MaxPages.ToString()
}

if ($Timeout -ne 30) {
    $argList += '--timeout', $Timeout.ToString()
}

if ($Resume) {
    $argList += '--resume'
}

if ($Verbose) {
    $argList += '--verbose'
}

# Run SiteGrabber from the project directory so Python can find the module
$scriptDir = $PSScriptRoot
Write-Host "Running: $($pythonExe) $($argList -join ' ')" -ForegroundColor Cyan
& $pythonExe @argList
exit $LASTEXITCODE
