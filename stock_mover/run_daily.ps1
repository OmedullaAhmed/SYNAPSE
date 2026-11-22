# Runs StockFlow and keeps a log. Point Windows Task Scheduler (or any scheduler) at this file.
# Usage: powershell -ExecutionPolicy Bypass -File run_daily.ps1 -Data <folder or workbook> [-Config <brand.json>]
param([string]$Data = "$PSScriptRoot\data\demo", [string]$Config = "$PSScriptRoot\brands\demo.json")
$out = Join-Path $PSScriptRoot "output"
New-Item -ItemType Directory -Force $out | Out-Null
$log = Join-Path $out ("run_{0:yyyy-MM-dd_HHmm}.log" -f (Get-Date))
python "$PSScriptRoot\engine.py" --data $Data --config $Config --out $out *> $log
if ($LASTEXITCODE -ne 0) { Write-Error "StockFlow failed or a rule check failed - see $log"; exit 1 }
Get-Content $log
