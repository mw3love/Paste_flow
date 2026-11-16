# 크기 · 메모리 · 시작 시간 측정 — 언어 비교 시험판용
# 사용: pwsh -File measure.ps1 -Exe <exe 경로> -Name <이름> [-Seconds 30] [-DataDir <앱이 만드는 데이터 폴더>]
# 결과는 같은 폴더 results.csv에 한 줄 덧붙인다.
param(
    [Parameter(Mandatory)] [string]$Exe,
    [Parameter(Mandatory)] [string]$Name,
    [int]$Seconds = 30,
    [string]$DataDir = ""
)
$ErrorActionPreference = "Stop"
$csv = Join-Path $PSScriptRoot "results.csv"
$header = "when,kind,name,size_mb,ws_mb,private_mb,start_ms,start_is_proxy,swap_max_ms,order_ok,focus_kept,read_by,note"
if (-not (Test-Path $csv)) { $header | Out-File $csv -Encoding utf8 }

$exeFull = (Resolve-Path $Exe).Path
$sizeMb = [math]::Round((Get-Item $exeFull).Length / 1MB, 1)
$logPath = Join-Path (Split-Path $exeFull) "spike.log"
if (Test-Path $logPath) { Remove-Item $logPath -Force }
$dataExisted = $DataDir -and (Test-Path $DataDir)

function Get-Tree([int]$rootId) {
    $all = Get-CimInstance Win32_Process | Select-Object ProcessId, ParentProcessId
    $ids = [System.Collections.Generic.HashSet[int]]::new(); [void]$ids.Add($rootId)
    $changed = $true
    while ($changed) {
        $changed = $false
        foreach ($p in $all) { if ($ids.Contains([int]$p.ParentProcessId) -and $ids.Add([int]$p.ProcessId)) { $changed = $true } }
    }
    return $ids
}

$p = Start-Process $exeFull -PassThru
$startMs = ([DateTimeOffset]$p.StartTime).ToUnixTimeMilliseconds()
$samples = @()
for ($i = 1; $i -le $Seconds; $i++) {
    Start-Sleep -Milliseconds 1000
    $ids = Get-Tree $p.Id
    $procs = Get-Process -Id ([int[]]$ids) -ErrorAction SilentlyContinue
    $ws = ($procs | Measure-Object WorkingSet64 -Sum).Sum / 1MB
    $pr = ($procs | Measure-Object PrivateMemorySize64 -Sum).Sum / 1MB
    $samples += [pscustomobject]@{ t = $i * 1000; ws = $ws; pr = $pr; n = $procs.Count }
}
$last = $samples[-1]

# 시작 시간: READY 줄이 있으면 그것, 없으면 메모리가 3초 연속 1MB 안쪽으로 멈춘 첫 시각(대리값)
$startMsVal = ""; $proxy = ""
if (Test-Path $logPath) {
    $ready = Select-String -Path $logPath -Pattern '^READY (\d+)' | Select-Object -First 1
    if ($ready) { $startMsVal = [int64]$ready.Matches[0].Groups[1].Value - $startMs; $proxy = "no" }
}
if (-not $startMsVal) {
    for ($k = 0; $k -le $samples.Count - 4; $k++) {
        $win = $samples[$k..($k + 3)].ws
        if ((($win | Measure-Object -Max).Maximum - ($win | Measure-Object -Min).Minimum) -lt 1 -and $samples[$k].ws -gt 5) { $startMsVal = $samples[$k].t; $proxy = "yes"; break }
    }
}

taskkill /F /T /PID $p.Id | Out-Null
Start-Sleep -Milliseconds 800
# 되돌리기: 측정이 만든 데이터 폴더와 단일 exe 풀기 폴더
if ($DataDir -and -not $dataExisted -and (Test-Path $DataDir)) { Remove-Item $DataDir -Recurse -Force }
Get-ChildItem $env:TEMP -Directory -Filter "_MEI*" -ErrorAction SilentlyContinue |
    Where-Object { $_.CreationTime -ge $p.StartTime.AddSeconds(-2) } |
    ForEach-Object { Remove-Item $_.FullName -Recurse -Force -ErrorAction SilentlyContinue }

$note = "procs=$($last.n)"
$inv = [Globalization.CultureInfo]::InvariantCulture
$row = "{0},measure,{1},{2},{3},{4},{5},{6},,,,,{7}" -f (Get-Date -Format "yyyy-MM-dd HH:mm"), $Name,
    $sizeMb.ToString("0.0", $inv), $last.ws.ToString("0.0", $inv), $last.pr.ToString("0.0", $inv), $startMsVal, $proxy, $note
$row | Out-File $csv -Append -Encoding utf8
Write-Output "MEASURE $Name size=${sizeMb}MB ws=$([math]::Round($last.ws,1))MB private=$([math]::Round($last.pr,1))MB start=${startMsVal}ms proxy=$proxy $note"
