# news.avild.com - Prerequisites Installer (Windows)
# Run as Administrator in PowerShell:
#   Set-ExecutionPolicy Bypass -Scope Process; .\install-prereqs.ps1

#Requires -RunAsAdministrator

$ErrorActionPreference = "Stop"

function Write-Step  { param($msg) Write-Host "`n>> $msg" -ForegroundColor Cyan }
function Write-Ok    { param($msg) Write-Host "   [OK] $msg" -ForegroundColor Green }
function Write-Skip  { param($msg) Write-Host "   [SKIP] $msg already installed." -ForegroundColor DarkGray }
function Write-Fail  { param($msg) Write-Host "   [FAIL] $msg" -ForegroundColor Red }

# --- winget check -----------------------------------------------------------
if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    Write-Fail "winget is not available."
    Write-Host "   Install 'App Installer' from the Microsoft Store, then re-run this script." -ForegroundColor Yellow
    exit 1
}

Write-Host ""
Write-Host "======================================" -ForegroundColor Cyan
Write-Host "  news.avild.com  - prereqs installer" -ForegroundColor Cyan
Write-Host "======================================" -ForegroundColor Cyan

# --- helper -----------------------------------------------------------------
function Install-Prereq {
    param(
        [string]$Name,
        [string]$WingetId,
        [string]$TestCmd
    )
    Write-Step $Name
    if ($TestCmd -and (Get-Command $TestCmd -ErrorAction SilentlyContinue)) {
        $ver = & $TestCmd --version 2>&1
        Write-Skip "$Name ($ver)"
        return
    }
    winget install --id $WingetId --accept-source-agreements --accept-package-agreements --silent
    if ($LASTEXITCODE -eq 0) {
        Write-Ok "$Name installed."
    } else {
        Write-Fail "winget returned exit code $LASTEXITCODE for $Name. Check the output above."
    }
}

# --- prerequisites ----------------------------------------------------------
Install-Prereq -Name "Git"            -WingetId "Git.Git"              -TestCmd "git"
Install-Prereq -Name "Docker Desktop" -WingetId "Docker.DockerDesktop"  -TestCmd "docker"
Install-Prereq -Name "Python 3.13"    -WingetId "Python.Python.3.13"   -TestCmd "python"

# --- done -------------------------------------------------------------------
Write-Host ""
Write-Host "======================================" -ForegroundColor Green
Write-Host "  All prerequisites installed!"        -ForegroundColor Green
Write-Host "======================================" -ForegroundColor Green
Write-Host ""
Write-Host "NEXT STEPS:" -ForegroundColor Yellow
Write-Host "  1. Restart your machine (required for Docker Desktop)."
Write-Host "  2. Open Docker Desktop and wait for the engine to start."
Write-Host "  3. Follow the setup steps in README.md."
Write-Host ""
