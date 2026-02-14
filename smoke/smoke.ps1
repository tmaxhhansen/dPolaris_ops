param(
    [Alias("Host")]
    [string]$ServerHost = "127.0.0.1",
    [int]$Port = 8420,
    [int]$TimeoutSeconds = 30,
    [string]$Symbol = "AAPL",
    [int]$Epochs = 1,
    [switch]$Json
)

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RepoRoot

$BaseUrl = "http://$ServerHost`:$Port"
$results = New-Object System.Collections.Generic.List[object]
$jobTimeoutSec = 300

function Add-Result {
    param(
        [string]$Step,
        [string]$Status,
        [string]$Message,
        [string]$Endpoint = ""
    )
    $results.Add([PSCustomObject]@{
        Step = $Step
        Status = $Status
        Endpoint = $Endpoint
        Message = $Message
    }) | Out-Null
}

function Read-ErrorBody {
    param([object]$ErrorRecord)
    try {
        $resp = $ErrorRecord.Exception.Response
        if ($null -eq $resp) {
            return ""
        }
        if ($resp.Content) {
            return [string]$resp.Content
        }
    } catch {}
    return ""
}

function Invoke-Json {
    param(
        [string]$Method,
        [string]$Uri,
        [object]$Body = $null,
        [int]$TimeoutSec = 20
    )
    if ($null -eq $Body) {
        return Invoke-RestMethod -Method $Method -Uri $Uri -TimeoutSec $TimeoutSec -ContentType "application/json"
    }
    $jsonBody = $Body | ConvertTo-Json -Compress -Depth 20
    return Invoke-RestMethod -Method $Method -Uri $Uri -TimeoutSec $TimeoutSec -ContentType "application/json" -Body $jsonBody
}

function Resolve-JobId {
    param([object]$Response)
    foreach ($key in @("id", "job_id", "jobId")) {
        try {
            $value = $Response.$key
            if ($null -ne $value -and "$value".Trim() -ne "") {
                return "$value"
            }
        } catch {}
    }
    return $null
}

function Tail-BackendLogs {
    $logRoot = Join-Path $HOME "dpolaris_data\logs"
    if (-not (Test-Path $logRoot)) {
        return @("No log directory found at $logRoot")
    }
    $latest = Get-ChildItem -Path $logRoot -File -Filter *.log -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($null -eq $latest) {
        return @("No .log files found in $logRoot")
    }
    $header = "Last 20 lines from $($latest.FullName):"
    $lines = Get-Content -Path $latest.FullName -Tail 20 -ErrorAction SilentlyContinue
    return @($header) + $lines
}

Write-Host "Running smoke test against $BaseUrl"

# A) GET /health retry until timeout
$healthOk = $false
$deadline = (Get-Date).AddSeconds([Math]::Max(1, $TimeoutSeconds))
while ((Get-Date) -lt $deadline) {
    try {
        $healthResp = Invoke-Json -Method "GET" -Uri "$BaseUrl/health" -TimeoutSec 5
        if ($null -ne $healthResp) {
            $healthOk = $true
            break
        }
    } catch {}
    Start-Sleep -Milliseconds 800
}

if ($healthOk) {
    Add-Result -Step "A_HEALTH" -Status "PASS" -Endpoint "GET /health" -Message "Backend is healthy."
} else {
    Add-Result -Step "A_HEALTH" -Status "FAIL" -Endpoint "GET /health" -Message "Backend did not become healthy within timeout."
}

# B) GET /api/status
try {
    $statusResp = Invoke-Json -Method "GET" -Uri "$BaseUrl/api/status" -TimeoutSec 15
    Add-Result -Step "B_STATUS" -Status "PASS" -Endpoint "GET /api/status" -Message "Received API status."
} catch {
    Add-Result -Step "B_STATUS" -Status "FAIL" -Endpoint "GET /api/status" -Message $_.Exception.Message
}

# C) GET /api/universe/list fallback /api/scan/universe/list on 404/collision text, else WARN
$universeOk = $false
$universeWarn = $false
$universeMsg = ""
try {
    $u1 = Invoke-Json -Method "GET" -Uri "$BaseUrl/api/universe/list" -TimeoutSec 15
    $universeOk = $true
    $universeMsg = "Primary endpoint succeeded."
} catch {
    $body = Read-ErrorBody -ErrorRecord $_
    $raw = ("{0} {1}" -f $_.Exception.Message, $body).ToLowerInvariant()
    $shouldFallback = $raw.Contains("404") -or $raw.Contains("collision")
    if ($shouldFallback) {
        try {
            $u2 = Invoke-Json -Method "GET" -Uri "$BaseUrl/api/scan/universe/list" -TimeoutSec 15
            $universeOk = $true
            $universeMsg = "Fallback endpoint succeeded."
        } catch {
            $universeWarn = $true
            $universeMsg = "Primary+fallback failed; continuing as WARN. Last error: $($_.Exception.Message)"
        }
    } else {
        $universeWarn = $true
        $universeMsg = "Primary failed without fallback trigger; continuing as WARN. Error: $($_.Exception.Message)"
    }
}

if ($universeOk) {
    Add-Result -Step "C_UNIVERSE_LIST" -Status "PASS" -Endpoint "GET /api/universe/list (+fallback)" -Message $universeMsg
} elseif ($universeWarn) {
    Add-Result -Step "C_UNIVERSE_LIST" -Status "WARN" -Endpoint "GET /api/universe/list (+fallback)" -Message $universeMsg
} else {
    Add-Result -Step "C_UNIVERSE_LIST" -Status "WARN" -Endpoint "GET /api/universe/list (+fallback)" -Message "Universe list check incomplete."
}

# D) POST /api/jobs/deep-learning/train then poll /api/jobs/{id}
$jobId = $null
$jobFinal = "timeout"
$jobFailure = ""
try {
    $trainBody = @{
        symbol = $Symbol
        model_type = "lstm"
        epochs = $Epochs
    }
    $trainResp = Invoke-Json -Method "POST" -Uri "$BaseUrl/api/jobs/deep-learning/train" -Body $trainBody -TimeoutSec 30
    $jobId = Resolve-JobId -Response $trainResp
    if (-not $jobId) {
        Add-Result -Step "D_DL_JOB_START" -Status "FAIL" -Endpoint "POST /api/jobs/deep-learning/train" -Message "Job ID missing in response."
    } else {
        Add-Result -Step "D_DL_JOB_START" -Status "PASS" -Endpoint "POST /api/jobs/deep-learning/train" -Message "Job ID: $jobId"
        $jobDeadline = (Get-Date).AddSeconds($jobTimeoutSec)
        while ((Get-Date) -lt $jobDeadline) {
            try {
                $jobResp = Invoke-Json -Method "GET" -Uri "$BaseUrl/api/jobs/$jobId" -TimeoutSec 15
                $status = ""
                try { $status = [string]$jobResp.status } catch {}
                $norm = $status.ToLowerInvariant()
                if ($norm -eq "completed" -or $norm -eq "success") {
                    $jobFinal = "completed"
                    break
                }
                if ($norm -eq "failed") {
                    $jobFinal = "failed"
                    try { $jobFailure = [string]$jobResp.error } catch {}
                    if (-not $jobFailure) {
                        try { $jobFailure = [string]$jobResp.detail } catch {}
                    }
                    break
                }
            } catch {
                $jobFailure = $_.Exception.Message
            }
            Start-Sleep -Seconds 2
        }
    }
} catch {
    Add-Result -Step "D_DL_JOB_START" -Status "FAIL" -Endpoint "POST /api/jobs/deep-learning/train" -Message $_.Exception.Message
}

if ($jobId) {
    if ($jobFinal -eq "completed") {
        Add-Result -Step "D_DL_JOB_POLL" -Status "PASS" -Endpoint "GET /api/jobs/{id}" -Message "Job completed."
    } elseif ($jobFinal -eq "failed") {
        Add-Result -Step "D_DL_JOB_POLL" -Status "FAIL" -Endpoint "GET /api/jobs/{id}" -Message ("Job failed: " + $jobFailure)
        $tail = Tail-BackendLogs
        foreach ($line in $tail) {
            Write-Host $line
        }
    } else {
        Add-Result -Step "D_DL_JOB_POLL" -Status "FAIL" -Endpoint "GET /api/jobs/{id}" -Message "Job polling timed out after 300 seconds."
    }
}

$failCount = @($results | Where-Object { $_.Status -eq "FAIL" }).Count
$warnCount = @($results | Where-Object { $_.Status -eq "WARN" }).Count
$passCount = @($results | Where-Object { $_.Status -eq "PASS" }).Count

Write-Host ""
Write-Host "Smoke Summary"
$results | Format-Table -AutoSize
Write-Host ("PASS={0} WARN={1} FAIL={2}" -f $passCount, $warnCount, $failCount)

if ($Json) {
    $outDir = Join-Path $RepoRoot "out"
    New-Item -ItemType Directory -Force -Path $outDir | Out-Null
    $jsonPath = Join-Path $outDir "smoke_result.json"
    $payload = [PSCustomObject]@{
        base_url = $BaseUrl
        host = $ServerHost
        port = $Port
        symbol = $Symbol
        epochs = $Epochs
        timeout_seconds = $TimeoutSeconds
        job_timeout_seconds = $jobTimeoutSec
        summary = [PSCustomObject]@{
            pass = $passCount
            warn = $warnCount
            fail = $failCount
        }
        results = $results
    }
    $payload | ConvertTo-Json -Depth 20 | Set-Content -Path $jsonPath -Encoding UTF8
    Write-Host "JSON written: $jsonPath"
}

if ($failCount -gt 0) {
    exit 2
}
exit 0
