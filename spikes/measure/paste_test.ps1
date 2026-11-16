# 자동 붙여넣기 · 초점 · 교체 지연 측정 — 언어 비교 시험판용
# 사용: pwsh -File paste_test.ps1 -Exe <시험판 exe> -Name <이름>
# ⚠ 진짜 키 입력(SendInput)이 들어간다. 맨 앞 창이 시험 창도 시험판도 아니면 즉시 중단한다.
# 대상 창은 이 스크립트가 띄우는 시험용 글상자 창(메모장 대신 — 사용자 메모장·세션 복원을 건드리지 않으려고).
param(
    [Parameter(Mandatory)] [string]$Exe,
    [Parameter(Mandatory)] [string]$Name
)
$ErrorActionPreference = "Stop"
$csv = Join-Path $PSScriptRoot "results.csv"
$header = "when,kind,name,size_mb,ws_mb,private_mb,start_ms,start_is_proxy,swap_max_ms,order_ok,focus_kept,read_by,note"
if (-not (Test-Path $csv)) { $header | Out-File $csv -Encoding utf8 }

Add-Type @"
using System; using System.Runtime.InteropServices;
public static class U32 {
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern void keybd_event(byte vk, byte scan, uint flags, UIntPtr extra);
  public static uint FgPid() { uint p; GetWindowThreadProcessId(GetForegroundWindow(), out p); return p; }
}
"@

$exeFull = (Resolve-Path $Exe).Path
$logPath = Join-Path (Split-Path $exeFull) "spike.log"
if (Test-Path $logPath) { Remove-Item $logPath -Force }
$outFile = Join-Path $env:TEMP "spike_target.txt"
if (Test-Path $outFile) { Remove-Item $outFile -Force }

function Get-Tree([int]$rootId) {
    $all = Get-CimInstance Win32_Process | Select-Object ProcessId, ParentProcessId
    $ids = [System.Collections.Generic.HashSet[int]]::new(); [void]$ids.Add($rootId)
    $changed = $true
    while ($changed) { $changed = $false; foreach ($p in $all) { if ($ids.Contains([int]$p.ParentProcessId) -and $ids.Add([int]$p.ProcessId)) { $changed = $true } } }
    return $ids
}

# 시험용 글상자 창 — 글이 바뀔 때마다 파일로 내보낸다
$targetScript = @"
Add-Type -AssemblyName System.Windows.Forms
`$f = New-Object Windows.Forms.Form; `$f.Text = 'SPIKE-PASTE-TARGET'; `$f.Width = 420; `$f.Height = 200; `$f.TopMost = `$true
`$t = New-Object Windows.Forms.TextBox; `$t.Multiline = `$true; `$t.Dock = 'Fill'; `$f.Controls.Add(`$t)
`$t.Add_TextChanged({ [IO.File]::WriteAllText('$outFile', `$t.Text, [Text.Encoding]::UTF8) })
`$f.Add_Shown({ `$f.Activate(); `$t.Focus() })
[Windows.Forms.Application]::Run(`$f)
"@
$encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($targetScript))

$spike = $null; $target = $null
$result = @{ order = ""; focus = "yes"; read = "file"; note = "" ; swap = "" }
try {
    $spike = Start-Process $exeFull -PassThru
    $deadline = (Get-Date).AddSeconds(20)
    while (-not ((Test-Path $logPath) -and (Select-String -Path $logPath -Pattern '^READY' -Quiet))) {
        if ((Get-Date) -gt $deadline) { throw "시험판 READY 없음(20초)" }
        Start-Sleep -Milliseconds 200
    }
    $target = Start-Process powershell -ArgumentList "-NoProfile -STA -EncodedCommand $encoded" -PassThru
    Start-Sleep -Milliseconds 1500
    $targetWin = (Get-Process -Id $target.Id).MainWindowHandle
    # 앞으로 가져오기 — 이미 맨 앞이면 건드리지 않는다. Alt로 전경 잠금을 풀었으면 Esc로 메뉴 모드를 빠져나온다
    # (Alt만 눌렀다 떼면 WinForms 창이 메뉴 모드가 되어 다음 키 하나를 먹는다 — 첫 붙여넣기가 사라졌던 원인)
    if ([int][U32]::FgPid() -ne $target.Id) {
        [U32]::keybd_event(0x12, 0, 0, [UIntPtr]::Zero); [U32]::keybd_event(0x12, 0, 2, [UIntPtr]::Zero)
        [void][U32]::SetForegroundWindow($targetWin); Start-Sleep -Milliseconds 300
        [U32]::keybd_event(0x1B, 0, 0, [UIntPtr]::Zero); [U32]::keybd_event(0x1B, 0, 2, [UIntPtr]::Zero)
    }
    Start-Sleep -Milliseconds 400

    $spikeIds = Get-Tree $spike.Id
    function Check-Fg([string]$stage) {
        $fg = [int][U32]::FgPid()
        if ($fg -eq $target.Id) { return "ok" }
        $script:spikeIds = Get-Tree $spike.Id
        if ($script:spikeIds.Contains($fg)) { return "stolen" }
        throw "중단 — 맨 앞 창이 바뀜($stage, pid=$fg)"
    }
    if ((Check-Fg "시작") -ne "ok") { throw "중단 — 시험 창을 맨 앞으로 못 가져옴" }

    $steals = 0; $skipPaste = $false
    foreach ($w in @("하나", "둘", "셋")) {
        Set-Clipboard -Value $w
        Start-Sleep -Milliseconds 600
        if ((Check-Fg "복사 $w") -eq "stolen") {
            $result.focus = "no"; $steals++
            if ($steals -ge 2) { $skipPaste = $true; break }
            [void][U32]::SetForegroundWindow($targetWin); Start-Sleep -Milliseconds 300
            if ((Check-Fg "되돌림") -ne "ok") { $skipPaste = $true; break }
        }
    }

    if ($skipPaste) {
        $result.order = "못 잼(초점)"
    } else {
        for ($i = 0; $i -lt 3; $i++) {
            if ((Check-Fg "붙여넣기 $i") -ne "ok") { throw "중단 — 붙여넣기 전 맨 앞 창이 바뀜" }
            [U32]::keybd_event(0x11, 0, 0, [UIntPtr]::Zero); [U32]::keybd_event(0x10, 0, 0, [UIntPtr]::Zero)
            [U32]::keybd_event(0x56, 0, 0, [UIntPtr]::Zero); [U32]::keybd_event(0x56, 0, 2, [UIntPtr]::Zero)
            [U32]::keybd_event(0x10, 0, 2, [UIntPtr]::Zero); [U32]::keybd_event(0x11, 0, 2, [UIntPtr]::Zero)
            Start-Sleep -Milliseconds 500
        }
        Start-Sleep -Milliseconds 600
        $text = if (Test-Path $outFile) { (Get-Content $outFile -Raw -Encoding utf8).Trim() } else { "" }
        $result.order = if ($text -eq "하나둘셋") { "yes" } else { "no" }
        $result.note = "got='$text'"
    }
    $swaps = Select-String -Path $logPath -Pattern '^SWAP (\d+) (\d+)' -ErrorAction SilentlyContinue |
        ForEach-Object { [int64]$_.Matches[0].Groups[2].Value - [int64]$_.Matches[0].Groups[1].Value }
    if ($swaps) { $result.swap = ($swaps | Measure-Object -Maximum).Maximum; $result.note += " swaps=$($swaps.Count)" }
}
catch { $result.note += " $($_.Exception.Message)" }
finally {
    if ($target) { taskkill /F /T /PID $target.Id 2>$null | Out-Null }
    if ($spike) { taskkill /F /T /PID $spike.Id 2>$null | Out-Null }
    if (Test-Path $outFile) { Remove-Item $outFile -Force -ErrorAction SilentlyContinue }
}

$note = ($result.note.Trim() -replace ',', ';')
$row = "{0},paste,{1},,,,,,{2},{3},{4},{5},{6}" -f (Get-Date -Format "yyyy-MM-dd HH:mm"), $Name, $result.swap, $result.order, $result.focus, $result.read, $note
$row | Out-File $csv -Append -Encoding utf8
Write-Output "PASTE $Name order=$($result.order) focus_kept=$($result.focus) swap_max=$($result.swap)ms note=$note"
