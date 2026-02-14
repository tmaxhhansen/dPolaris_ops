param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet("status", "up", "down", "smoke")]
    [string]$Command,
    [string]$Url = "http://127.0.0.1:8420",
    [Alias("Host")]
    [string]$ServerHost = "127.0.0.1",
    [int]$Port = 8420,
    [int]$Timeout = 30,
    [string]$Symbol = "AAPL",
    [string]$Model = "lstm",
    [int]$Epochs = 1,
    [int]$JobTimeout = 600,
    [switch]$NoDlJob,
    [string]$AiRoot = "C:\my-git\dpolaris_ai"
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RepoRoot

$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $Python = "py"
    } elseif (Get-Command python -ErrorAction SilentlyContinue) {
        $Python = "python"
    } else {
        throw "No python executable found in repo venv or PATH."
    }
}

$argsList = @("-m", "ops.main", $Command, "--url", $Url, "--timeout", "$Timeout")

if ($Command -in @("up", "down", "status")) {
    $argsList += @("--ai-root", $AiRoot, "--host", $ServerHost, "--port", "$Port")
}
if ($Command -eq "smoke") {
    $argsList += @("--symbol", $Symbol, "--model", $Model, "--epochs", "$Epochs", "--job-timeout", "$JobTimeout")
    if ($NoDlJob) { $argsList += "--no-dl-job" }
}

if ($Python -eq "py") {
    & py @argsList
} elseif ($Python -eq "python") {
    & python @argsList
} else {
    & $Python @argsList
}
exit $LASTEXITCODE
