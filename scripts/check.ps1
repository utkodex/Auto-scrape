# Offline gate: ruff, then safety tests, then the full suite. Exit code 0 means green.
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }

& $py -m ruff check core agent tests scripts
if ($LASTEXITCODE -ne 0) { Write-Host "ruff failed"; exit $LASTEXITCODE }

& $py -m pytest -m safety -q
if ($LASTEXITCODE -ne 0) { Write-Host "safety tests failed"; exit $LASTEXITCODE }

# Browser-bound tests are serial per worker; two workers keep the gate under two minutes.
& $py -m pytest -q -n 2
exit $LASTEXITCODE
