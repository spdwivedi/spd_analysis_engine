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

# Grabs the current folder path reliably, bypassing buggy VS Code / Conda terminal outputs
$engineRoot = (Get-Item .).FullName;
$outputFile = "$engineRoot\codebase_bundle.md";

$targetDirs = @("scripts", "web", "tests");

$rootFiles = @(
    "start.py",
    "requirements.txt",
    "audit_engine.ps1",
    "ai_data\models_config.example.json",
    "ai_data\git_config.example.json"
);

$excludePatterns = @(
    "__pycache__",
    "\\.git",
    "shadow_git",
    "\\.venvconda",
    "ai_data\\\\cache",
    "\\\\current\\\\",
    "\\\\last_run\\\\",
    "\\\\history\\\\",
    [regex]::Escape($engineRoot) + "\\\\trash\\\\",
    "mock_workspace",
    "models_config\\.json$",
    "git_config\\.json$",
    "\\.env",
    "\\.db$",
    "\\.db-wal$",
    "\\.db-shm$",
    "\\.log$",
    "\\.pid",
    "\\.lock$",
    "codebase_bundle\\.md",
    "pipeline_bundle\\.md"
);

$targetExtensions = @(".py", ".js", ".html", ".css", ".json", ".ps1");
$collectedFiles = @();

$pipe = [char]124;
$tick = [char]96;

for ($r = 0; $r -lt$rootFiles.Count; $r++) {$rf = $rootFiles[$r];
    $fullPath = "$engineRoot\$rf";
    
    if (Test-Path $fullPath) {
        $rawText = [System.IO.File]::ReadAllText($fullPath, [System.Text.Encoding]::UTF8);
        $linesArray = @($rawText -split "`r?`n");
        $lines =$linesArray.Count;
        $fileItem = Get-Item$fullPath;
        $sizeKB = [math]::Round($fileItem.Length / 1KB, 2);
        $extLower = [System.IO.Path]::GetExtension($rf).ToLower();

        $collectedFiles += [PSCustomObject]@{
            FullPath = $fullPath;
            RelPath  = $rf;
            Lines    = $lines;
            SizeKB   = $sizeKB;
            Ext      = $extLower;
            Content  = $rawText;
        };
    };
};

for ($d = 0; $d -lt$targetDirs.Count; $d++) {$dir = $targetDirs[$d];
    $dirPath = "$engineRoot\$dir";
    
    if (Test-Path $dirPath) {
        $files = @(Get-ChildItem -Path$dirPath -Recurse -File);

        for ($fIdx = 0; $fIdx -lt $files.Count; $fIdx++) {
            $f =$files[$fIdx];$fExtLower = $f.Extension.ToLower();$matchedExt = $targetExtensions -contains$fExtLower;
            $isExcluded =$false;

            for ($pIdx = 0; $pIdx -lt$excludePatterns.Count; $pIdx++) {$p = $excludePatterns[$pIdx];
                if ($f.FullName -match$p) {
                    $isExcluded =$true;
                    break;
                };
            };

            if ($matchedExt -and (-not$isExcluded)) {
                $rawText = [System.IO.File]::ReadAllText($f.FullName, [System.Text.Encoding]::UTF8);
                $linesArray = @($rawText -split "`r?`n");
                $lines =$linesArray.Count;
                $sizeKB = [math]::Round($f.Length / 1KB, 2);$rel = $f.FullName.Replace($engineRoot, "").TrimStart("\");

                $collectedFiles += [PSCustomObject]@{
                    FullPath = $f.FullName;
                    RelPath  = $rel;
                    Lines    = $lines;
                    SizeKB   = $sizeKB;
                    Ext      = $fExtLower;
                    Content  = $rawText;
                };
            };
        };
    };
};

$collectedFiles =$collectedFiles | Sort-Object -Property RelPath;

$msgFound = "Found {0} relevant source code files." -f $collectedFiles.Count;
Write-Host $msgFound -ForegroundColor Green;
Write-Host "Compiling Markdown document..." -ForegroundColor Yellow;

function Get-CodeTag ($ext) {
    switch ($ext) {
        ".py"   { return "python"; }
        ".js"   { return "javascript"; }
        ".html" { return "html"; }
        ".css"  { return "css"; }
        ".json" { return "json"; }
        ".ps1"  { return "powershell"; }
        Default { return "text"; }
    };
};

$sb = [System.Text.StringBuilder]::new();

[void]$sb.AppendLine("# SPD Analysis Engine — Complete Codebase Source Bundle");
$genStr = "Generated on: {0} {1} Target Workspace: {2}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $pipe,$engineRoot;
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

for ($c = 0; $c -lt$collectedFiles.Count; $c++) {$item = $collectedFiles[$c];
    $totalLines +=$item.Lines;
    $totalSizeKB +=$item.SizeKB;

    $status = "✓ Micro (<200L)";
    if ($item.Lines -gt 500) {$status = "⚠️ Monolith (>500L)"; }
    elseif ($item.Lines -gt 200) {$status = "Medium (200-500L)"; };

    $extClean =$item.Ext.TrimStart('.');
    $tableRow = "{0} {1}{2}{1} {0} {3} {0} {4} {0} {5} KB {0} {6} {0}" -f $pipe, $tick,$item.RelPath, $extClean,$item.Lines, $item.SizeKB, $status;
    [void]$sb.AppendLine($tableRow);
};

[void]$sb.AppendLine("");
$totalMb = [math]::Round($totalSizeKB / 1024, 2);$totalStr = "**Total Files:** {0} {1} **Total Code Lines:** {2} {1} **Total Uncompressed Size:** {3} MB" -f $collectedFiles.Count, $pipe, $totalLines,$totalMb;
[void]$sb.AppendLine($totalStr);
[void]$sb.AppendLine("");
[void]$sb.AppendLine("---");
[void]$sb.AppendLine("");
[void]$sb.AppendLine("## 2. Complete Source Code");
[void]$sb.AppendLine("");

for ($c = 0; $c -lt$collectedFiles.Count; $c++) {$item = $collectedFiles[$c];
    $counter =$c + 1;
    
    $logStr = "  [{0}/{1}] Bundling: {2} ({3} lines)" -f $counter,$collectedFiles.Count, $item.RelPath, $item.Lines;
    Write-Host $logStr -ForegroundColor DarkGray;

    $headStr = "### [{0}/{1}] File: {2}{3}{2}" -f $counter,$collectedFiles.Count, $tick,$item.RelPath;
    [void]$sb.AppendLine($headStr);$extClean2 = $item.Ext.TrimStart('.');$metaStr = "- **Lines:** {0} {1} **Size:** {2} KB {1} **Type:** {3}" -f $item.Lines, $pipe, $item.SizeKB, $extClean2;
    [void]$sb.AppendLine($metaStr);
    [void]$sb.AppendLine("");

    $lang = Get-CodeTag -ext$item.Ext;
    $langCode = "{0}{0}{0}{1}" -f $tick,$lang;
    [void]$sb.AppendLine($langCode);

    if ($item.Content) {
        $tripleTick = "{0}{0}{0}" -f $tick;
        $safeTick = "{0}{0} {0}" -f $tick;
        $sanitizedContent =$item.Content.Replace($tripleTick,$safeTick);
        [void]$sb.AppendLine($sanitizedContent.TrimEnd());
    } else {
        [void]$sb.AppendLine("# (Empty file)");
    };

    $endCode = "{0}{0}{0}" -f $tick;
    [void]$sb.AppendLine($endCode);
    [void]$sb.AppendLine("");
    [void]$sb.AppendLine("---");
    [void]$sb.AppendLine("");
};

[System.IO.File]::WriteAllText($outputFile,$sb.ToString(), [System.Text.Encoding]::UTF8);

$finalSizeItem = Get-Item$outputFile;
$finalSizeMB = [math]::Round($finalSizeItem.Length / 1MB, 2);
Write-Host "";
Write-Host "========================================================" -ForegroundColor Cyan;
Write-Host "  SUCCESS! Codebase bundled into:" -ForegroundColor Green;
$outStr = "  {0} ({1} MB)" -f $outputFile,$finalSizeMB;
Write-Host $outStr -ForegroundColor White;
Write-Host "========================================================" -ForegroundColor Cyan;
Write-Host "";