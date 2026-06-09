# Contention test – jeden skrypt, bez zakłóceń od AdminUser / starych Locustów.
#
# Użycie (z katalogu hotel_reservation):
#   .\tests\load\run_contention.ps1              # czysty wyścig (domyślnie)
#   .\tests\load\run_contention.ps1 -Mixed       # book + cancel + search
#
# Parametry opcjonalne:
#   -Users 20 -SpawnRate 20 -Duration 30s -Rooms 1 -Host http://localhost:8000

param(
    [string]$Host_ = "http://localhost:8000",
    [int]$Users = 20,
    [int]$SpawnRate = 20,
    [string]$Duration = "30s",
    [int]$Rooms = 1,
    [switch]$Mixed
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $Root

if ($Mixed) {
    $LocustFile = "tests/load/locustfile_contention.py"
    $VerifyArgs = @("--seeded-rooms", $Rooms)
    $Mode = "mixed (book + cancel + search)"
} else {
    $LocustFile = "tests/load/locustfile_pure_contention.py"
    $VerifyArgs = @("--seeded-rooms", $Rooms, "--hotel", "h-waw-1", "--room-type", "single")
    $Mode = "pure race (h-waw-1/single, tylko book)"
}

Write-Host "`n=== Contention test: $Mode ===" -ForegroundColor Yellow

Write-Host "`n=== 1/5  Zatrzymuję stare procesy Locust ===" -ForegroundColor Cyan
Get-Process locust -ErrorAction SilentlyContinue | Stop-Process -Force
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like '*locust*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 1
Write-Host "  OK`n"

Write-Host "=== 2/5  Seed hoteli (--rooms $Rooms) ===" -ForegroundColor Cyan
python tests/load/seed_hotels.py --host $Host_ --rooms $Rooms
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "`n=== 3/5  Baseline (timestamp) ===" -ForegroundColor Cyan
python tests/load/verify_contention.py --host $Host_ --save-baseline
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "=== 4/5  Locust: $LocustFile ===" -ForegroundColor Cyan
Write-Host "  -u $Users -r $SpawnRate -t $Duration`n"
locust -f $LocustFile --host $Host_ `
    --headless -u $Users -r $SpawnRate -t $Duration `
    --csv tests/load/results/contention
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "`n=== 5/5  Weryfikacja ===" -ForegroundColor Cyan
python tests/load/verify_contention.py --host $Host_ @VerifyArgs
