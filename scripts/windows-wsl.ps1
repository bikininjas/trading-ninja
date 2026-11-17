<#
.SYNOPSIS
Runs the existing Linux management commands from Windows PowerShell via WSL2.
.DESCRIPTION
Distribution and Linux repository path are explicit to avoid operating on a
different installation. This does not implement a native Windows service.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$Distribution,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^/')]
    [string]$Repository,

    [Parameter(Mandatory = $true)]
    [ValidateSet('check', 'status', 'logs', 'start', 'stop', 'restart')]
    [string]$Action,

    [ValidateSet('api', 'dashboard', 'engine', 'intel', 'all')]
    [string]$Component = 'all'
)

if (($Action -eq 'check' -or $Action -eq 'status') -and $Component -ne 'all') {
    Write-Error 'check and status do not accept a component.'
    exit 2
}
if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
    Write-Error 'wsl.exe not found. Install and configure WSL2 first.'
    exit 1
}

$enterRepository = 'cd -- "$1" || exit 1; shift; exec "$@"'
if ($Action -eq 'check') {
    & wsl.exe --distribution $Distribution --exec bash -c $enterRepository -- $Repository bash scripts/check.sh
} elseif ($Action -eq 'status') {
    & wsl.exe --distribution $Distribution --exec bash -c $enterRepository -- $Repository bash scripts/manage.sh status
} else {
    & wsl.exe --distribution $Distribution --exec bash -c $enterRepository -- $Repository bash scripts/manage.sh $Action $Component
}
exit $LASTEXITCODE
