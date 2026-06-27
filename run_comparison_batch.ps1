<#
.SYNOPSIS
    Run N independent comparison batches (CSV only) and average them into the
    final multihop-mode comparison plots.

.DESCRIPTION
    For each batch this invokes:  uv run src/run.py -n --tag run<NN>
      -n      CSV only, skip all per-run plotting (every mode in
              simulation.multihop_modes is swept regardless)
      --tag   write under metrics/run<NN>/ so batches do not overwrite each other

    After all batches it runs avg_metrics.py over every metrics/run* directory,
    producing under <AveragedDir>:
      results_interval-*_random_<mode>/   seed-averaged CSVs (with StdDev)
      plots_interval-*_random_<mode>/     per-mode averaged plots (with error bars)
      comparison_interval-*_random/       cross-mode comparison figures (the final plot)

    WHICH modes are compared (e.g. forwarded vs append) is controlled by
    simulation.multihop_modes in config.yaml, NOT by this script.

.PARAMETER Runs
    Number of independent batches to run. Default 30.

.PARAMETER AveragedDir
    Output directory for the averaged results/plots. Default metrics/averaged.

.PARAMETER Clean
    Remove existing metrics/run* directories and the averaged dir before starting.
    Recommended when starting a fresh experiment so stale runs are not averaged in.

.EXAMPLE
    ./run_comparison_batch.ps1
.EXAMPLE
    ./run_comparison_batch.ps1 -Runs 50 -Clean
#>
[CmdletBinding()]
param(
    [int]$Runs = 30,
    [string]$AveragedDir = "metrics/averaged",
    [switch]$Clean
)

# Always operate from the repo root (this script's own location)
Set-Location $PSScriptRoot

if ($Runs -lt 1) { throw "Runs must be >= 1 (got $Runs)" }

# Zero-pad tags to the width of the largest run number (min 2): run01, run02, ...
$pad = [Math]::Max(2, "$Runs".Length)

if ($Clean) {
    Write-Host "Cleaning previous run dirs and averaged output..." -ForegroundColor Yellow
    Get-ChildItem -Directory -Path "metrics" -Filter "run*" -ErrorAction SilentlyContinue |
        Remove-Item -Recurse -Force
    if (Test-Path $AveragedDir) { Remove-Item -Recurse -Force $AveragedDir }
}

$startTime = Get-Date
Write-Host "Starting $Runs comparison batches (CSV only)..." -ForegroundColor Cyan

for ($i = 1; $i -le $Runs; $i++) {
    $tag = "run" + $i.ToString("D$pad")
    Write-Host "`n=== Batch $i / $Runs  (tag: $tag) ===" -ForegroundColor Cyan

    uv run src/run.py -n --tag $tag
    if ($LASTEXITCODE -ne 0) {
        throw "Batch $i (tag $tag) failed with exit code $LASTEXITCODE. Aborting."
    }
}

# Collect every run dir produced above (sorted for deterministic ordering)
$runDirs = Get-ChildItem -Directory -Path "metrics" -Filter "run*" |
           Sort-Object Name | Select-Object -ExpandProperty FullName

if (-not $runDirs) { throw "No metrics/run* directories found to average." }

if (@($runDirs).Count -ne $Runs) {
    Write-Host ("WARNING: found {0} run dirs but ran {1} batches; averaging will include " +
                "all of them. Use -Clean for a fresh experiment." -f @($runDirs).Count, $Runs) -ForegroundColor Yellow
}

Write-Host "`nAveraging $(@($runDirs).Count) runs into $AveragedDir ..." -ForegroundColor Cyan
uv run src/script/avg_metrics.py --input-dirs $runDirs --output-dir $AveragedDir
if ($LASTEXITCODE -ne 0) {
    throw "avg_metrics.py failed with exit code $LASTEXITCODE."
}

$elapsed = (Get-Date) - $startTime
Write-Host "`nDone in $([int]$elapsed.TotalMinutes)m $($elapsed.Seconds)s." -ForegroundColor Green
Write-Host "Averaged results & plots : $AveragedDir" -ForegroundColor Green
Write-Host "Final comparison figures : $AveragedDir/comparison_interval-*_random/" -ForegroundColor Green
