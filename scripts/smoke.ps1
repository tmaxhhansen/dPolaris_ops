param(
    [string]$BaseUrl = "http://127.0.0.1:8420",
    [string]$Symbol = "AAPL",
    [string]$ModelType = "lstm",
    [int]$Epochs = 1,
    [int]$HealthTimeoutSec = 30,
    [int]$JobTimeoutSec = 120
)

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RepoRoot

$LogsDir = Join-Path $RepoRoot "logs"
New-Item -ItemType Directory -Force -Path $LogsDir | Out-Null
$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$LogPath = Join-Path $LogsDir ("smoke_{0}.log" -f $Stamp)

function Write-Log {
    param(
        [string]$Level,
        [string]$Message
    )
    $line = "{0} [{1}] {2}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Level, $Message
    Write-Host $line
    Add-Content -Path $LogPath -Value $line
}

function Test-Health {
    param([string]$Url)
    try {
        $resp = Invoke-RestMethod -Uri "$Url/health" -Method Get -TimeoutSec 3
        if ($null -eq $resp) { return $false }
        if ($resp.status -eq "healthy" -or $resp.status -eq "ok") { return $true }
        return $true
    } catch {
        return $false
    }
}

function Get-PortOwner {
    param([int]$Port = 8420)
    $rows = netstat -ano -p tcp | Select-String ":$Port" | Select-String "LISTENING"
    foreach ($row in $rows) {
        $parts = ($row.ToString() -split "\s+") | Where-Object { $_ -ne "" }
        if ($parts.Length -ge 5) {
            $pid = 0
            if ([int]::TryParse($parts[-1], [ref]$pid)) {
                return $pid
            }
        }
    }
    return $null
}

function Get-ProcessCommandLine {
    param([int]$Pid)
    try {
        $p = Get-CimInstance Win32_Process -Filter "ProcessId=$Pid" -ErrorAction Stop
        return [string]$p.CommandLine
    } catch {
        return ""
    }
}

function Invoke-Json {
    param(
        [string]$Method,
        [string]$Uri,
        [object]$Body = $null,
        [int]$TimeoutSec = 15
    )
    if ($null -eq $Body) {
        return Invoke-RestMethod -Uri $Uri -Method $Method -TimeoutSec $TimeoutSec
    }
    $json = $Body | ConvertTo-Json -Depth 20 -Compress
    return Invoke-RestMethod -Uri $Uri -Method $Method -ContentType "application/json" -Body $json -TimeoutSec $TimeoutSec
}

function Resolve-JobId {
    param([object]$Resp)
    if ($null -eq $Resp) { return $null }
    foreach ($k in @("id", "job_id", "jobId")) {
        try {
            $v = $Resp.$k
            if ($null -ne $v -and "$v".Trim() -ne "") { return "$v" }
        } catch {}
    }
    return $null
}

$summary = [ordered]@{
    health = $false
    api_status = $false
    deep_learning_status = $false
    job_started = $false
    job_final_status = "not_started"
    job_id = $null
}

Write-Log "INFO" "Smoke test started. Log: $LogPath"
Write-Log "INFO" "Base URL: $BaseUrl"

$BackendPython = "C:\my-git\dpolaris_ai\.venv\Scripts\python.exe"
if (-not (Test-Path $BackendPython)) {
    Write-Log "FAIL" "Missing backend venv python: $BackendPython"
    exit 1
}

$healthOk = Test-Health -Url $BaseUrl
if (-not $healthOk) {
    Write-Log "WARN" "/health is not healthy; checking port ownership..."
    $owner = Get-PortOwner -Port 8420
    if ($owner) {
        $cmd = Get-ProcessCommandLine -Pid $owner
        Write-Log "WARN" "Port 8420 is LISTENING by PID $owner"
        if ($cmd) {
            Write-Log "WARN" "Port owner command line: $cmd"
        } else {
            Write-Log "WARN" "Port owner command line unavailable (permission-limited)."
        }
    }

    Write-Log "INFO" "Starting backend server using existing command..."
    $backendProc = Start-Process -FilePath $BackendPython -ArgumentList @(
        "-m", "cli.main", "server", "--host", "127.0.0.1", "--port", "8420"
    ) -WorkingDirectory "C:\my-git\dpolaris_ai" -PassThru -WindowStyle Hidden
    Write-Log "INFO" "Started backend process PID=$($backendProc.Id)"
}

$deadline = (Get-Date).AddSeconds($HealthTimeoutSec)
while ((Get-Date) -lt $deadline) {
    if (Test-Health -Url $BaseUrl) {
        $healthOk = $true
        break
    }
    Start-Sleep -Milliseconds 800
}

if (-not $healthOk) {
    Write-Log "FAIL" "/health did not become healthy within $HealthTimeoutSec seconds."
    exit 1
}
$summary.health = $true
Write-Log "PASS" "GET /health"

try {
    $statusResp = Invoke-Json -Method "GET" -Uri "$BaseUrl/api/status" -TimeoutSec 20
    $summary.api_status = $true
    Write-Log "PASS" "GET /api/status"
    Add-Content -Path $LogPath -Value ("api/status response: " + ($statusResp | ConvertTo-Json -Depth 20))
} catch {
    Write-Log "FAIL" "GET /api/status failed: $($_.Exception.Message)"
}

try {
    $dlStatusResp = Invoke-Json -Method "GET" -Uri "$BaseUrl/api/deep-learning/status" -TimeoutSec 20
    $summary.deep_learning_status = $true
    Write-Log "PASS" "GET /api/deep-learning/status"
    Add-Content -Path $LogPath -Value ("api/deep-learning/status response: " + ($dlStatusResp | ConvertTo-Json -Depth 20))
} catch {
    Write-Log "FAIL" "GET /api/deep-learning/status failed: $($_.Exception.Message)"
}

$jobReq = @{
    symbol = $Symbol
    model_type = $ModelType
    epochs = $Epochs
}

try {
    $jobStart = Invoke-Json -Method "POST" -Uri "$BaseUrl/api/jobs/deep-learning/train" -Body $jobReq -TimeoutSec 30
    $jobId = Resolve-JobId -Resp $jobStart
    if (-not $jobId) {
        Write-Log "FAIL" "Job enqueue did not return a job id."
    } else {
        $summary.job_started = $true
        $summary.job_id = $jobId
        Write-Log "PASS" "POST /api/jobs/deep-learning/train (job_id=$jobId)"

        $jobDeadline = (Get-Date).AddSeconds($JobTimeoutSec)
        while ((Get-Date) -lt $jobDeadline) {
            try {
                $jobResp = Invoke-Json -Method "GET" -Uri "$BaseUrl/api/jobs/$jobId" -TimeoutSec 20
                $status = ""
                try { $status = [string]$jobResp.status } catch { $status = "" }
                if (-not $status) { $status = "unknown" }
                Write-Log "INFO" "Job $jobId status=$status"
                if ($status -eq "success") {
                    $summary.job_final_status = "success"
                    Write-Log "PASS" "Deep-learning job completed successfully."
                    break
                }
                if ($status -eq "failed") {
                    $summary.job_final_status = "failed"
                    $errText = ""
                    try { $errText = [string]$jobResp.error } catch {}
                    if (-not $errText) {
                        try { $errText = [string]$jobResp.detail } catch {}
                    }
                    if ($errText) {
                        Write-Log "FAIL" "Deep-learning job failed: $errText"
                    } else {
                        Write-Log "FAIL" "Deep-learning job failed."
                    }
                    break
                }
            } catch {
                Write-Log "WARN" "Polling /api/jobs/$jobId failed: $($_.Exception.Message)"
            }
            Start-Sleep -Seconds 2
        }

        if ($summary.job_final_status -eq "not_started") {
            $summary.job_final_status = "timeout"
            Write-Log "FAIL" "Deep-learning job timed out after $JobTimeoutSec seconds."
        }
    }
} catch {
    Write-Log "FAIL" "POST /api/jobs/deep-learning/train failed: $($_.Exception.Message)"
}

$allPass = (
    $summary.health -and
    $summary.api_status -and
    $summary.deep_learning_status -and
    $summary.job_started -and
    $summary.job_final_status -eq "success"
)

Write-Log "INFO" "----- SUMMARY -----"
Write-Log "INFO" ("health={0}" -f $summary.health)
Write-Log "INFO" ("api_status={0}" -f $summary.api_status)
Write-Log "INFO" ("deep_learning_status={0}" -f $summary.deep_learning_status)
Write-Log "INFO" ("job_started={0}" -f $summary.job_started)
Write-Log "INFO" ("job_id={0}" -f $summary.job_id)
Write-Log "INFO" ("job_final_status={0}" -f $summary.job_final_status)
Write-Log "INFO" ("log_file={0}" -f $LogPath)

if ($allPass) {
    Write-Log "PASS" "Smoke test PASSED."
    exit 0
}

Write-Log "FAIL" "Smoke test FAILED."
exit 1
