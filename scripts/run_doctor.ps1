param(
    [string]$BaseUrl = "http://127.0.0.1:8420",
    [string]$Symbol = "AAPL",
    [string]$ModelType = "lstm",
    [int]$Epochs = 1,
    [int]$Timeout = 300
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RepoRoot

$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
function Get-PythonBootstrapCommand {
    if (Get-Command python -ErrorAction SilentlyContinue) {
        return "python"
    }
    if (Get-Command py -ErrorAction SilentlyContinue) {
        return "py -3.11"
    }
    throw "Python launcher not found. Install Python 3.11+ first."
}

if (-not (Test-Path $VenvPython)) {
    Write-Host "[INFO] Creating virtual environment..."
    $bootstrap = Get-PythonBootstrapCommand
    Invoke-Expression "$bootstrap -m venv .venv"
}

if (-not (Test-Path $VenvPython)) {
    throw "Unable to find venv python at $VenvPython"
}

Write-Host "[INFO] Installing dependencies..."
& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -r requirements.txt

Write-Host "[INFO] Running doctor..."
& $VenvPython -m ops.doctor --base-url $BaseUrl --symbol $Symbol --model-type $ModelType --epochs $Epochs --timeout $Timeout
$exitCode = $LASTEXITCODE

$ReportsRoot = Join-Path $HOME "dpolaris_data\reports"
Write-Host "[INFO] Reports directory: $ReportsRoot"
Write-Host "[INFO] JSON report: $(Join-Path $ReportsRoot 'doctor_report.json')"
Write-Host "[INFO] Text report: $(Join-Path $ReportsRoot 'doctor_report.txt')"
Write-Host "[INFO] Tickets dir: $(Join-Path $ReportsRoot 'tickets')"

exit $exitCode
