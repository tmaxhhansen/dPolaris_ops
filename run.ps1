param(
    [Alias("Host")]
    [string]$ServerHost = "127.0.0.1",
    [int]$Port = 8420,
    [int]$TimeoutSeconds = 30,
    [string]$Symbol = "AAPL",
    [int]$Epochs = 1,
    [switch]$StartUI
)

$ErrorActionPreference = "Stop"

# PHASE 0: Banner + paths
$DPolaris = "C:\my-git\dpolaris"
$AI = "C:\my-git\dpolaris_ai"
$Ops = "C:\my-git\dpolaris_ops"
$BaseUrl = "http://$ServerHost`:$Port"
$RunRoot = Join-Path $HOME "dpolaris_data\run"
$LogRoot = Join-Path $HOME "dpolaris_data\logs"

function Write-Status {
    param(
        [ValidateSet("INFO", "PASS", "WARN", "FAIL")]
        [string]$Level,
        [string]$Message
    )
    Write-Host ("[{0}] {1}" -f $Level, $Message)
}

function Test-Health {
    param([string]$Url)
    try {
        $resp = Invoke-RestMethod -Method Get -Uri "$Url/health" -TimeoutSec 4
        if ($null -eq $resp) { return $false }
        $status = ""
        try { $status = [string]$resp.status } catch { $status = "" }
        if (-not $status) { return $true }
        return ($status -in @("healthy", "ok", "running"))
    } catch {
        return $false
    }
}

function Get-ProcessCommandLine {
    param([int]$ProcessId)
    try {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction Stop
        return [string]$proc.CommandLine
    } catch {
        return ""
    }
}

function Get-ListeningPid {
    param([int]$Port)
    $rows = netstat -ano -p tcp | Select-String (":{0}" -f $Port) | Select-String "LISTENING"
    foreach ($row in $rows) {
        $parts = ($row.ToString() -split "\s+") | Where-Object { $_ -ne "" }
        if ($parts.Length -ge 5) {
            $ownerPid = 0
            if ([int]::TryParse($parts[-1], [ref]$ownerPid)) {
                return $ownerPid
            }
        }
    }
    return $null
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
    $bodyJson = $Body | ConvertTo-Json -Compress -Depth 20
    return Invoke-RestMethod -Method $Method -Uri $Uri -TimeoutSec $TimeoutSec -ContentType "application/json" -Body $bodyJson
}

function Resolve-JobId {
    param([object]$Resp)
    foreach ($k in @("id", "job_id", "jobId")) {
        try {
            $v = $Resp.$k
            if ($null -ne $v -and "$v".Trim() -ne "") { return "$v" }
        } catch {}
    }
    return $null
}

function Read-LastLogs {
    param([int]$Lines = 20)
    if (-not (Test-Path $LogRoot)) {
        return @("No log directory: $LogRoot")
    }
    $latest = Get-ChildItem -Path $LogRoot -File -Filter *.log -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if ($null -eq $latest) {
        return @("No *.log files under $LogRoot")
    }
    $content = Get-Content -Path $latest.FullName -Tail $Lines -ErrorAction SilentlyContinue
    return @("Last $Lines lines from $($latest.FullName):") + $content
}

Write-Status INFO "dPolaris single-command runner"
Write-Status INFO "DPolaris path: $DPolaris"
Write-Status INFO "AI path: $AI"
Write-Status INFO "Ops path: $Ops"

$backendHealthy = $false
$jobCompleted = $false

# PHASE 1: Safe cleanup
Write-Status INFO "PHASE 1 - Safe cleanup"

$targetPathNorm = "c:\my-git\dpolaris_ai"
$matchA = "-m cli.main server"
$matchB = "--port $Port"

$killedCount = 0
$pythonProcs = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction SilentlyContinue
foreach ($p in $pythonProcs) {
    $cmd = [string]$p.CommandLine
    if (-not $cmd) { continue }
    $cmdNorm = $cmd.ToLowerInvariant().Replace("/", "\\")
    if ($cmdNorm.Contains($targetPathNorm) -and $cmdNorm.Contains($matchA) -and $cmdNorm.Contains($matchB)) {
        try {
            Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop
            $killedCount += 1
            Write-Status PASS "Stopped repo-owned backend server PID=$($p.ProcessId)"
        } catch {
            Write-Status WARN "Failed to stop matched backend PID=$($p.ProcessId): $($_.Exception.Message)"
        }
    }
}
if ($killedCount -eq 0) {
    Write-Status INFO "No matching repo-owned backend server process found for cleanup."
}

$portOwner = Get-ListeningPid -Port $Port
if ($portOwner) {
    $ownerCmd = Get-ProcessCommandLine -ProcessId $portOwner
    Write-Status WARN "Port $Port currently LISTENING by PID $portOwner"
    if ($ownerCmd) {
        Write-Status WARN "Owner command line: $ownerCmd"
    }
}

$runtimeFiles = @(
    (Join-Path $RunRoot "backend.pid"),
    (Join-Path $RunRoot "orchestrator.pid"),
    (Join-Path $RunRoot "orchestrator.heartbeat.json")
)
foreach ($rf in $runtimeFiles) {
    if (Test-Path $rf) {
        try {
            Remove-Item -Path $rf -Force -ErrorAction Stop
            Write-Status PASS "Removed runtime file: $rf"
        } catch {
            Write-Status WARN "Could not remove runtime file ${rf}: $($_.Exception.Message)"
        }
    }
}

# PHASE 2: Start backend
Write-Status INFO "PHASE 2 - Start backend"
$pythonExe = Join-Path $AI ".venv\Scripts\python.exe"
if (-not (Test-Path $pythonExe)) {
    Write-Status FAIL "Missing backend venv python: $pythonExe"
    exit 2
}

$prevLlm = $env:LLM_PROVIDER
$hadLlm = Test-Path Env:LLM_PROVIDER
$env:LLM_PROVIDER = "none"
try {
    $backendProc = Start-Process -FilePath $pythonExe -ArgumentList @(
        "-m", "cli.main", "server", "--host", $ServerHost, "--port", "$Port"
    ) -WorkingDirectory $AI -PassThru -WindowStyle Hidden
} finally {
    if ($hadLlm) {
        $env:LLM_PROVIDER = $prevLlm
    } else {
        Remove-Item Env:LLM_PROVIDER -ErrorAction SilentlyContinue
    }
}
Write-Status INFO "Started backend PID=$($backendProc.Id)"

$deadline = (Get-Date).AddSeconds([Math]::Max(1, $TimeoutSeconds))
while ((Get-Date) -lt $deadline) {
    if (Test-Health -Url $BaseUrl) {
        $backendHealthy = $true
        break
    }
    Start-Sleep -Milliseconds 800
}
if ($backendHealthy) {
    Write-Status PASS "Backend /health is healthy."
} else {
    Write-Status FAIL "Backend /health did not become healthy within $TimeoutSeconds seconds."
}

# PHASE 3: Optional deps sanity + DL job
Write-Status INFO "PHASE 3 - API sanity + deep-learning job"

try {
    $apiStatus = Invoke-Json -Method "GET" -Uri "$BaseUrl/api/status" -TimeoutSec 20
    Write-Status PASS "GET /api/status"
    Write-Host ($apiStatus | ConvertTo-Json -Depth 8)
} catch {
    Write-Status WARN "GET /api/status failed: $($_.Exception.Message)"
}

try {
    $uList = Invoke-Json -Method "GET" -Uri "$BaseUrl/api/universe/list" -TimeoutSec 20
    Write-Status PASS "GET /api/universe/list"
} catch {
    Write-Status WARN "GET /api/universe/list unavailable: $($_.Exception.Message)"
}

$jobId = $null
$jobState = "unknown"
$jobError = ""
try {
    $trainReq = @{ symbol = $Symbol; model_type = "lstm"; epochs = $Epochs }
    $startResp = Invoke-Json -Method "POST" -Uri "$BaseUrl/api/jobs/deep-learning/train" -Body $trainReq -TimeoutSec 30
    $jobId = Resolve-JobId -Resp $startResp
    if (-not $jobId) {
        Write-Status FAIL "Deep-learning train enqueue did not return job id."
    } else {
        Write-Status PASS "Enqueued deep-learning job id=$jobId"
        $jobDeadline = (Get-Date).AddSeconds(300)
        while ((Get-Date) -lt $jobDeadline) {
            try {
                $jobResp = Invoke-Json -Method "GET" -Uri "$BaseUrl/api/jobs/$jobId" -TimeoutSec 20
                $stateRaw = ""
                try { $stateRaw = [string]$jobResp.status } catch { $stateRaw = "" }
                $jobState = $stateRaw.ToLowerInvariant()

                if ($jobState -in @("completed", "success")) {
                    $jobCompleted = $true
                    break
                }
                if ($jobState -eq "failed") {
                    try { $jobError = [string]$jobResp.error } catch {}
                    if (-not $jobError) {
                        try { $jobError = [string]$jobResp.detail } catch {}
                    }
                    break
                }
            } catch {
                $jobError = $_.Exception.Message
            }
            Start-Sleep -Seconds 2
        }

        if ($jobCompleted) {
            Write-Status PASS "Deep-learning job completed (id=$jobId)."
        } elseif ($jobState -eq "failed") {
            Write-Status FAIL "Deep-learning job failed (id=$jobId): $jobError"
            foreach ($line in (Read-LastLogs -Lines 20)) {
                Write-Host $line
            }
        } else {
            Write-Status FAIL "Deep-learning job timed out after 5 minutes (id=$jobId)."
        }
    }
} catch {
    Write-Status FAIL "Deep-learning train call failed: $($_.Exception.Message)"
}

# PHASE 4: Start Java app (optional)
Write-Status INFO "PHASE 4 - Java Control Center"
if ($StartUI) {
    Write-Status INFO "Starting UI via Gradle run..."
    Push-Location $DPolaris
    try {
        & .\gradlew.bat --no-daemon run
    } finally {
        Pop-Location
    }
} else {
    Write-Status INFO "Backend ready. Start UI via: cd C:\my-git\dpolaris; .\gradlew.bat run"
}

if ($backendHealthy -and $jobCompleted) {
    Write-Status PASS "Overall result: PASS"
    exit 0
}

Write-Status FAIL "Overall result: FAIL"
exit 2
