<#
.SYNOPSIS
    Compiles all active SPD Analysis Engine codebase files into a structured Markdown document.
#>

$ErrorActionPreference = "SilentlyContinue";

Write-Host "";
Write-Host "========================================================" -ForegroundColor Cyan;
Write-Host "    SPD ANALYSIS ENGINE -- CODEBASE BUNDLER (MD)        " -ForegroundColor Cyan;
Write-Host "========================================================" -ForegroundColor Cyan;
Write-Host "";

$engineRoot = if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path };
$outputFile = Join-Path -Path $engineRoot -ChildPath "codebase_bundle.md";

$targetDirs = @("scripts", "web");
$rootFiles  = @("start.py", "requirements.txt");

$excludePatterns = @(
    "__pycache__",
    "\.git",
    "shadow_git",
    "ai_data\\cache",
    "current",
    "last_run",
    "history",
    "trash",
    "mock_workspace"
);

$targetExtensions = @(".py", ".js", ".html", ".css", ".txt");
$collectedFiles = @();

# Use ASCII codes to completely bypass formatting and parsing errors
$pipe = [char]124;
$tick = [char]96;

for ($r = 0; $r -lt $rootFiles.Count; $r++) {
    $rf = $rootFiles[$r];
    $fullPath = Join-Path -Path $engineRoot -ChildPath $rf;
    if (Test-Path $fullPath) {
        $contentArray = @(Get-Content -Path $fullPath -Encoding UTF8);
        $lines = $contentArray.Count;
        if (-not $lines) { $lines = 0 };
        
        $sizeKB = [math]::Round((Get-Item $fullPath).Length / 1KB, 2);
        $extLower = [System.IO.Path]::GetExtension($rf).ToLower();
        
        $collectedFiles += [PSCustomObject]@{
            FullPath = $fullPath;
            RelPath  = $rf;
            Lines    = $lines;
            SizeKB   = $sizeKB;
            Ext      = $extLower;
        };
    };
};

for ($d = 0; $d -lt $targetDirs.Count; $d++) {
    $dir = $targetDirs[$d];
    $dirPath = Join-Path -Path $engineRoot -ChildPath $dir;
    if (Test-Path $dirPath) {
        $files = @(Get-ChildItem -Path $dirPath -Recurse -File);
        
        for ($fIdx = 0; $fIdx -lt $files.Count; $fIdx++) {
            $f = $files[$fIdx];
            $fExtLower = $f.Extension.ToLower();
            $matchedExt = $targetExtensions -contains $fExtLower;
            $isExcluded = $false;
            
            for ($pIdx = 0; $pIdx -lt $excludePatterns.Count; $pIdx++) {
                $p = $excludePatterns[$pIdx];
                if ($f.FullName -match $p) { 
                    $isExcluded = $true;
                    break;
                };
            };
            
            if ($matchedExt -and (-not $isExcluded)) {
                $contentArray = @(Get-Content -Path $f.FullName -Encoding UTF8);
                $lines = $contentArray.Count;
                if (-not $lines) { $lines = 0 };
                
                $sizeKB = [math]::Round($f.Length / 1KB, 2);
                $rel = $f.FullName.Replace($engineRoot, "").TrimStart("\");
                
                $collectedFiles += [PSCustomObject]@{
                    FullPath = $f.FullName;
                    RelPath  = $rel;
                    Lines    = $lines;
                    SizeKB   = $sizeKB;
                    Ext      = $fExtLower;
                };
            };
        };
    };
};

$collectedFiles = Sort-Object -InputObject $collectedFiles -Property RelPath;

$msgFound = "{0} {1} {2}" -f "Found", $collectedFiles.Count, "relevant source code files.";
Write-Host $msgFound -ForegroundColor Green;
Write-Host "Compiling Markdown document..." -ForegroundColor Yellow;

function Get-CodeTag ($ext) {
    switch ($ext) {
        ".py"   { return "python"; }
        ".js"   { return "javascript"; }
        ".html" { return "html"; }
        ".css"  { return "css"; }
        ".json" { return "json"; }
        Default { return "text"; }
    };
};

$sb = New-Object -TypeName System.Text.StringBuilder;

[void]$sb.AppendLine("# SPD Analysis Engine — Complete Codebase Source Bundle");
$genStr = "Generated on: {0} {1} Target Workspace: {2}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $pipe, $engineRoot;
[void]$sb.AppendLine($genStr);
[void]$sb.AppendLine("");
[void]$sb.AppendLine("---");
[void]$sb.AppendLine("");
[void]$sb.AppendLine("## 1. Codebase Summary & File Index");
[void]$sb.AppendLine("");

$th1 = "{0} Relative File Path {0} Language {0} Line Count {0} Size (KB) {0} Modularity Status {0}" -f $pipe;
[void]$sb.AppendLine($th1);

$th2 = "{0} :--- {0} :--- {0} :--- {0} :--- {0} :--- {0}" -f $pipe;
[void]$sb.AppendLine($th2);

$totalLines = 0;
$totalSizeKB = 0;

for ($c = 0; $c -lt $collectedFiles.Count; $c++) {
    $item = $collectedFiles[$c];
    $totalLines += $item.Lines;
    $totalSizeKB += $item.SizeKB;
    
    $status = "✓ Modular (<250L)";
    if ($item.Lines -gt 500) { $status = "⚠️ Monolith (>500L)"; }
    elseif ($item.Lines -gt 250) { $status = "Medium (250-500L)"; };
    
    $extClean = $item.Ext.TrimStart('.');
    
    # -f operator replaces {0} with pipe, {1} with backtick, etc.
    $tableRow = "{0} {1}{2}{1} {0} {3} {0} {4} {0} {5} KB {0} {6} {0}" -f $pipe, $tick, $item.RelPath, $extClean, $item.Lines, $item.SizeKB, $status;
    [void]$sb.AppendLine($tableRow);
};

[void]$sb.AppendLine("");
$totalMb = [math]::Round($totalSizeKB / 1024, 2);
$totalStr = "**Total Files:** {0} {1} **Total Code Lines:** {2} {1} **Total Uncompressed Size:** {3} MB" -f $collectedFiles.Count, $pipe, $totalLines, $totalMb;
[void]$sb.AppendLine($totalStr);
[void]$sb.AppendLine("");
[void]$sb.AppendLine("---");
[void]$sb.AppendLine("");
[void]$sb.AppendLine("## 2. Complete Source Code");
[void]$sb.AppendLine("");

for ($c = 0; $c -lt $collectedFiles.Count; $c++) {
    $item = $collectedFiles[$c];
    $counter = $c + 1;
    
    $logStr = "  [{0}/{1}] Bundling: {2} ({3} lines)" -f $counter, $collectedFiles.Count, $item.RelPath, $item.Lines;
    Write-Host $logStr -ForegroundColor DarkGray;
    
    $headStr = "### [{0}/{1}] File: {2}{3}{2}" -f $counter, $collectedFiles.Count, $tick, $item.RelPath;
    [void]$sb.AppendLine($headStr);
    
    $extClean2 = $item.Ext.TrimStart('.');
    $metaStr = "- **Lines:** {0} {1} **Size:** {2} KB {1} **Type:** {3}" -f $item.Lines, $pipe, $item.SizeKB, $extClean2;
    [void]$sb.AppendLine($metaStr);
    [void]$sb.AppendLine("");
    
    $lang = Get-CodeTag -ext $item.Ext;
    $langCode = "{0}{0}{0}{1}" -f $tick, $lang;
    [void]$sb.AppendLine($langCode);
    
    $contentArray = @(Get-Content -Path $item.FullPath -Raw -Encoding UTF8);
    $content = $contentArray -join "`n";
    if ($content) {
        $tripleTick = "{0}{0}{0}" -f $tick;
        $safeTick = "{0}{0} {0}" -f $tick;
        $content = $content.Replace($tripleTick, $safeTick);
        [void]$sb.AppendLine($content.TrimEnd());
    } else {
        [void]$sb.AppendLine("# (Empty file)");
    };
    
    $endCode = "{0}{0}{0}" -f $tick;
    [void]$sb.AppendLine($endCode);
    [void]$sb.AppendLine("");
    [void]$sb.AppendLine("---");
    [void]$sb.AppendLine("");
};

[System.IO.File]::WriteAllText($outputFile, $sb.ToString(), [System.Text.Encoding]::UTF8);

$finalSizeMB = [math]::Round((Get-Item $outputFile).Length / 1MB, 2);
Write-Host "";
Write-Host "========================================================" -ForegroundColor Cyan;
Write-Host "  SUCCESS! Codebase bundled into:" -ForegroundColor Green;
$outStr = "  {0} ({1} MB)" -f $outputFile, $finalSizeMB;
Write-Host $outStr -ForegroundColor White;
Write-Host "========================================================" -ForegroundColor Cyan;
Write-Host "";