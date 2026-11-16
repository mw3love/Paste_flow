# 언어 비교 시험판 (spikes)

PasteFlow를 다른 언어로 다시 만들지 정하려고, **순차 붙여넣기 하나만** 두 언어로 작게 만들어 지금 앱과 나란히 잰다.
계획: `docs/plan/done/20261116-언어-비교-시험판.md`(구현 뒤 자리) · 결과 표: `docs/remodel/20261116-진단.md` 「시험판 실측」.

| 폴더 | 무엇 |
|---|---|
| `csharp/` | C#(.NET 10 + WPF), 트레이는 WinForms `NotifyIcon` |
| `tauri/` | Rust + TypeScript(Tauri 2) — 키 훅·클립보드·주입·트레이는 Rust, 작은 창 화면은 TypeScript |
| `measure/` | 측정 스크립트 둘과 결과 `results.csv` |

두 시험판은 같은 일만 한다: 텍스트 복사를 줄에 쌓고(규칙은 `pasteflow/paste_queue.py`와 같음), Ctrl+Shift+V를 삼킨 뒤 클립보드를 다음 항목으로 바꾸고 깨끗한 Ctrl+V를 주입한다(`pasteflow/paste_interceptor.py`와 같은 방식). 평범한 Ctrl+V는 통과시키고 줄만 비운다. 복사하면 초점을 빼앗지 않는 작은 창이 뜬다. exe 옆 `spike.log`에 `READY`(트레이 등록 시각)와 `SWAP`(키 받은 시각·클립보드 쓰기 끝난 시각)을 남긴다.

## 필요한 도구

```bash
winget install --id Microsoft.DotNet.SDK.10
winget install --id Rustlang.Rustup && rustup default stable-msvc
winget install --id Microsoft.VisualStudio.2022.BuildTools --override "--quiet --wait --norestart --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
# Node.js와 WebView2는 이미 있다고 가정
```

## 빌드

```bash
cd spikes/csharp
dotnet publish -c Release -r win-x64 --self-contained true -p:PublishSingleFile=true -p:IncludeNativeLibrariesForSelfExtract=true -o publish/single
dotnet publish -c Release -r win-x64 --self-contained false -o publish/fdd

cd ../tauri
npm install
npx tauri build --no-bundle      # 결과: src-tauri/target/release/spike-tauri.exe
```

## 측정 (PowerShell 7)

```powershell
# 크기 · 메모리 · 시작 시간 (앱을 30초 띄웠다 끔)
.\measure\measure.ps1 -Exe ..\dist\PasteFlow-1.49.0.exe -Name "PasteFlow(python)" -DataDir "$env:LOCALAPPDATA\PasteFlow"
.\measure\measure.ps1 -Exe .\csharp\publish\single\spike-cs.exe -Name "C# single"
.\measure\measure.ps1 -Exe .\csharp\publish\fdd\spike-cs.exe -Name "C# fdd"
.\measure\measure.ps1 -Exe .\tauri\src-tauri\target\release\spike-tauri.exe -Name "Tauri"

# 자동 붙여넣기 · 초점 · 교체 지연 — ⚠ 진짜 키 입력이 들어간다(시작 전에 사람에게 알릴 것)
.\measure\paste_test.ps1 -Exe .\csharp\publish\single\spike-cs.exe -Name "C# single"
.\measure\paste_test.ps1 -Exe .\tauri\src-tauri\target\release\spike-tauri.exe -Name "Tauri"
```

- 크기는 MiB(1024×1024) 단위다.
- `paste_test.ps1`은 메모장 대신 자기가 띄우는 시험용 글상자 창에 붙인다(사용자 메모장과 Windows 11 메모장의 세션 복원을 건드리지 않으려고). 맨 앞 창이 시험 창도 시험판도 아니면 즉시 멈춘다.
- 지금 PasteFlow는 `READY`를 남기지 않아 시작 시간이 「대리값」(메모리가 3초 연속 1MB 안쪽으로 멈춘 첫 시각, 1초 간격)이다.
