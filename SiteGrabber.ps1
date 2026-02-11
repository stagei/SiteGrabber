<#
.SYNOPSIS
    PowerShell wrapper for SiteGrabber; parameters map to Python sitegrabber CLI (--input-address, --output-folder, etc.).

.DESCRIPTION
    Runs the Python SiteGrabber module (python -m sitegrabber) with the given options.
    Requires Python 3.10+ and dependencies from requirements.txt.
    Output folder passed to Python is OutputFolder plus a sanitized subfolder from InputAddress.

.PARAMETER InputAddress
    Required. Passed to Python as --input-address. Starting URL to crawl (e.g. a docs root).

.PARAMETER OutputFolder
    Base folder for saved pages. Default: user's Downloads\SiteGrabber. Python receives this plus a subfolder derived from InputAddress (illegal path chars removed) as --output-folder.

.PARAMETER InputLimitationType
    Optional. Passed to Python as --limitation-type. Attribute name to filter on when extracting links (e.g. class, id, aria-label). Only divs with this attribute matching InputLimitationText are used for links.

.PARAMETER InputLimitationText
    Optional. Passed to Python as --limitation-text. Text to match in div attributes. With InputLimitationType, restricts link extraction to matching divs; useful for doc sites (e.g. ibmdocs-toc-link).

.PARAMETER Recursive
    Default True. In Python: --recursive (follow links) or --no-recursive (single page only).

.PARAMETER Delay
    Default 0.5. Passed to Python as --delay. Seconds to wait between HTTP requests (rate limiting).

.PARAMETER MaxPages
    Default 0. Passed to Python as --max-pages. Maximum pages to download; 0 means unlimited.

.PARAMETER Timeout
    Default 30. Passed to Python as --timeout. Request timeout in seconds.

.PARAMETER Resume
    Switch. Passed to Python as --resume. Skips already-downloaded files and continues from existing output.

.PARAMETER Verbose
    Switch. Passed to Python as --verbose. Enables verbose logging (out-of-scope URLs, skipped content, etc.).

.EXAMPLE
    .\SiteGrabber.ps1 -InputAddress "https://www.ibm.com/docs/en/db2/12.1.x"
    Crawls from that URL; saves under Downloads\SiteGrabber\<sanitized-URL>.

.EXAMPLE
    .\SiteGrabber.ps1 -InputAddress "https://www.ibm.com/docs/en/db2/12.1.x" -OutputFolder "C:\opt\data\SiteGrabber" -InputLimitationType "class" -InputLimitationText "ibmdocs-toc-link" -Recursive
    Same, with custom output base, HTML filter (only links inside divs with class ibmdocs-toc-link), and recursive crawl.
#>

param(
    [Parameter(Mandatory)]
    [string]$InputAddress,

    [string]$OutputFolder = (Join-Path ([Environment]::GetFolderPath('UserProfile')) 'Downloads\SiteGrabber'),

    [string]$InputLimitationType,

    [string]$InputLimitationText,

    [bool]$Recursive = $true,

    [double]$Delay = 0.5,

    [int]$MaxPages = 0,

    [int]$Timeout = 30,

    [switch]$Resume
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
# Subfolder from input-address with illegal path chars removed (Windows: \ / : * ? " < > |)
$safeInputName = $InputAddress -replace '[<>:"/\\|?*]', '_' -replace '_+', '_' -replace '^[\s._]+|[\s._]+$', ''
if (-not $safeInputName) { $safeInputName = 'sitegrabber' }
$effectiveOutputFolder = Join-Path $OutputFolder $safeInputName

if (-not (Test-Path $effectiveOutputFolder)) {
    New-Item -ItemType Directory -Path $effectiveOutputFolder | Out-Null
}
# Build argument list
$argList = @(
    '-m', 'sitegrabber',
    '--input-address', $InputAddress,
    '--output-folder', $effectiveOutputFolder
)

if ($InputLimitationType) {
    $argList += '--limitation-type', $InputLimitationType
}

if ($InputLimitationText) {
    $argList += '--limitation-text', $InputLimitationText
}

if (-not $Recursive) {
    $argList += '--no-recursive'
}
elseif ($Recursive) {
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
