# Runs Stock Mover and keeps a log. Point Windows Task Scheduler (or any scheduler) at this file.
# Usage: powershell -ExecutionPolicy Bypass -File run_daily.ps1 [-Data <csv folder>]
param([string]$Data = "$PSScriptRoot\..\manyavar-vch3-data\csv")
$out = Join-Path $PSScriptRoot "output"
New-Item -ItemType Directory -Force $out | Out-Null
$log = Join-Path $out ("run_{0:yyyy-MM-dd_HHmm}.log" -f (Get-Date))
python "$PSScriptRoot\stock_mover.py" --data $Data --out $out *> $log
if ($LASTEXITCODE -ne 0) { Write-Error "Stock Mover failed or a rule check failed - see $log"; exit 1 }
Get-Content $log
