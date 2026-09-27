<#
.SYNOPSIS
    SPD Analysis Engine - System Resource & Codebase Audit Script
#>

Write-Host ""
Write-Host "========================================================" -ForegroundColor Cyan
Write-Host "    SPD ANALYSIS ENGINE -- SYSTEM & CODEBASE AUDIT      " -ForegroundColor Cyan
Write-Host "========================================================" -ForegroundColor Cyan
Write-Host ""

$engineRoot = if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path }

# ---------------------------------------------------------
# 1. CODEBASE SIZE & LINE COUNT AUDIT
# ---------------------------------------------------------
Write-Host "[1/3] CODEBASE MODULARITY & LINE COUNT AUDIT" -ForegroundColor Yellow

$targetDirs = @("scripts", "web", "tests")
$totalLines = 0
$totalFiles = 0
$largeFiles = @()

foreach ($dir in $targetDirs) {
    $dirPath = Join-Path -Path $engineRoot -ChildPath $dir
    if (Test-Path -Path $dirPath) {
        Write-Host "  Directory: $dir/" -ForegroundColor Green
        $files = Get-ChildItem -Path $dirPath -Recurse -File -ErrorAction SilentlyContinue | Where-Object { 
            $_.Extension -in @(".py", ".js", ".html", ".css") -and $_.FullName -notmatch "__pycache__" 
        }

        foreach ($file in $files) {
            $lines = (Get-Content -Path $file.FullName -ErrorAction SilentlyContinue | Measure-Object -Line).Lines
            if (-not $lines) { $lines = 0 }
            $totalLines += $lines
            $totalFiles += 1
            
            $relPath = $file.FullName.Replace($engineRoot, "").TrimStart("\")
            if ($lines -gt 500) {
                Write-Host "    [!] $relPath : $lines lines (Large Monolith)" -ForegroundColor Red
                $largeFiles += [PSCustomObject]@{ File = $relPath; Lines = $lines }
            } else {
                Write-Host "        $relPath : $lines lines" -ForegroundColor DarkGray
            }
        }
    }
}

Write-Host ""
Write-Host "  Total Code Files: $totalFiles" -ForegroundColor White
Write-Host "  Total Lines of Code: $totalLines" -ForegroundColor White
if ($largeFiles.Count -gt 0) {
    Write-Host "  Files Recommended for Modularization (>500 lines): $($largeFiles.Count)" -ForegroundColor Red
} else {
    Write-Host "  All files are modularized (<500 lines)!" -ForegroundColor Green
}

# ---------------------------------------------------------
# 2. DISK USAGE & STORAGE AUDIT
# ---------------------------------------------------------
Write-Host ""
Write-Host "[2/3] DISK USAGE & STORAGE FOOTPRINT" -ForegroundColor Yellow

function Format-ByteSize {
    param([double]$Bytes)
    if ($Bytes -ge 1GB) { return "$([math]::Round($Bytes / 1GB, 2)) GB" }
    if ($Bytes -ge 1MB) { return "$([math]::Round($Bytes / 1MB, 2)) MB" }
    if ($Bytes -ge 1KB) { return "$([math]::Round($Bytes / 1KB, 2)) KB" }
    return "$Bytes Bytes"
}

$storageTiers = @("current", "last_run", "history", "ai_data", "trash")

foreach ($tier in $storageTiers) {
    $tierPath = Join-Path -Path $engineRoot -ChildPath $tier
    if (Test-Path -Path $tierPath) {
        $measure = Get-ChildItem -Path $tierPath -Recurse -File -Force -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum
        $bytes = if ($measure.Sum) { $measure.Sum } else { 0 }
        $count = if ($measure.Count) { $measure.Count } else { 0 }
        $formatted = Format-ByteSize -Bytes $bytes
        Write-Host "  $tier/ : $formatted across $count files" -ForegroundColor White
    }
}

$cacheDir = Join-Path -Path $engineRoot -ChildPath "ai_data\cache"
if (Test-Path -Path $cacheDir) {
    $cacheItems = Get-ChildItem -Path $cacheDir -File -ErrorAction SilentlyContinue
    $cacheCount = if ($cacheItems) { $cacheItems.Count } else { 0 }
    Write-Host "  Cached AI Summaries: $cacheCount files in flat cache" -ForegroundColor Cyan
}

# ---------------------------------------------------------
# 3. RUNTIME PROCESSES & MEMORY CONSUMPTION
# ---------------------------------------------------------
Write-Host ""
Write-Host "[3/3] ACTIVE PROCESSES & MEMORY FOOTPRINT" -ForegroundColor Yellow

try {
    $procs = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { 
        $_.Name -match "^python" -and ($_.CommandLine -like "*spd_analysis_engine*" -or $_.CommandLine -like "*start.py*") 
    }
} catch {
    $procs = @()
}

if ($procs -and $procs.Count -gt 0) {
    Write-Host "  Active Engine Processes:" -ForegroundColor Green
    foreach ($p in $procs) {
        $psProc = Get-Process -Id $p.ProcessId -ErrorAction SilentlyContinue
        $memMB = if ($psProc) { [math]::Round($psProc.WorkingSet64 / 1MB, 2) } else { 0 }
        Write-Host "    PID $($p.ProcessId) ($($p.Name)): $memMB MB RAM" -ForegroundColor White
        Write-Host "      Command: $($p.CommandLine)" -ForegroundColor DarkGray
    }
} else {
    Write-Host "  No active engine worker/UI daemon detected on this machine." -ForegroundColor DarkGray
}

$ideNames = @("antigravity", "cursor", "code")
$ideProcs = Get-Process -ErrorAction SilentlyContinue | Where-Object { $ideNames -contains $_.ProcessName }
if ($ideProcs) {
    Write-Host ""
    Write-Host "  Monitored IDE Instances Detected:" -ForegroundColor Green
    $grouped = $ideProcs | Group-Object -Property ProcessName
    foreach ($g in $grouped) {
        $totalMem = [math]::Round(($g.Group | Measure-Object -Property WorkingSet64 -Sum).Sum / 1MB, 2)
        Write-Host "    $($g.Name) ($($g.Count) processes): $totalMem MB total RAM" -ForegroundColor White
    }
}

Write-Host ""
Write-Host "========================================================" -ForegroundColor Cyan
Write-Host "                   AUDIT COMPLETE                       " -ForegroundColor Cyan
Write-Host "========================================================" -ForegroundColor Cyan
Write-Host ""