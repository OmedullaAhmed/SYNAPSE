# Export every slide of a deck to PNG using the installed PowerPoint (read-only, no window).
param([string]$Deck, [string]$OutDir)
New-Item -ItemType Directory -Force $OutDir | Out-Null
Get-ChildItem $OutDir -Filter *.png -ErrorAction SilentlyContinue | Remove-Item -Force
$app = New-Object -ComObject PowerPoint.Application
try {
    $p = $app.Presentations.Open($Deck, $true, $false, $false)
    $i = 1
    foreach ($s in $p.Slides) { $s.Export((Join-Path $OutDir ("slide-{0:D2}.png" -f $i)), "PNG", 1600, 900); $i++ }
    $p.Close()
    "exported $($i - 1) slides"
} finally { $app.Quit(); [System.Runtime.InteropServices.Marshal]::ReleaseComObject($app) | Out-Null }
