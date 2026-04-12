# CLAUDE.md

이 파일은 Claude Code(claude.ai/code)가 이 저장소에서 작업할 때 참고하는 안내 문서입니다.

## 프로젝트 개요

**PasteFlow** — 순차 붙여넣기 자동화 클립보드 매니저. Windows 10/11 전용. 복사한 순서대로 Ctrl+Shift+V를 누를 때마다 다음 항목이 붙여넣어지는 **항상 활성** 방식. PyQt6 기반. 전체 요구사항은 `PRD.md` 참고.

## 명령어

```bash
# 앱 실행
python -m pasteflow.main

# 의존성 설치
pip install -r requirements.txt

# 테스트 실행
pytest tests/

# 단독 실행 .exe 빌드
pyinstaller --onefile --windowed pasteflow/main.py
```

---

## 프로젝트 구조

```
pasteflow/
├── main.py                 # 진입점, 앱 초기화 및 모듈 오케스트레이션
├── clipboard_monitor.py    # 클립보드 감시 (WM_CLIPBOARDUPDATE)
├── paste_queue.py          # 순차 붙여넣기 큐 & 포인터 관리 (핵심)
├── paste_interceptor.py    # Ctrl+Shift+V 감지 + 패널 토글 단축키 감지 (핵심)
├── hotkey_manager.py       # RegisterHotKey 유틸 (현재 미사용, 구조 유지)
├── database.py             # SQLite CRUD (clipboard_items, settings)
├── models.py               # ClipboardItem 데이터 모델
└── ui/
    ├── panel.py            # 전체 클립보드 패널
    ├── image_preview.py    # 이미지 미리보기 팝업 (다중 창 지원)
    ├── text_preview.py     # 텍스트 미리보기 팝업
    ├── toast.py            # 토스트 알림 (시작 알림 등)
    ├── settings_dialog.py  # 설정 화면
    └── tray.py             # 시스템 트레이

tests/
├── test_models.py
├── test_database.py
└── test_paste_queue.py
```

---

## 아키텍처

### 모듈 역할

- **`main.py`** — 오케스트레이션 레이어. 모든 모듈을 연결하고 클립보드 모니터 → DB → 큐 → UI 간 이벤트 흐름 관리. 단일 인스턴스 보장(Windows 뮤텍스), 시작 알림 토스트 표시.
- **`models.py`** — `ClipboardItem` 데이터클래스 (id, content_type, text_content, image_data, html_content, rtf_content, preview_text, thumbnail, created_at, is_pinned, pin_order).
- **`database.py`** — SQLite(`pasteflow.db`). `clipboard_items`(50개 FIFO 히스토리)와 `settings` 두 테이블. 고정(pin) 항목은 50개 제한에서 제외.
- **`clipboard_monitor.py`** — `WM_CLIPBOARDUPDATE` Windows 이벤트 기반 백그라운드 감시. 텍스트, 이미지, HTML, RTF 캡처. `_self_triggered` 플래그로 자체 트리거 방지.
- **`paste_queue.py`** — 순차 붙여넣기 큐 관리. 붙여넣기 진행 중(pointer>0) 새 복사 → 큐 리셋 후 새 항목부터 시작. 붙여넣기 전 연속 복사 → 누적. 소진 시 None 반환.
- **`paste_interceptor.py`** — WH_KEYBOARD_LL 저수준 키보드 훅으로 두 가지 단축키 감지:
  - **Ctrl+Shift+V**: 큐에서 다음 항목 가져오기 → 클립보드 교체 → 키 이벤트 통과 (suppress). 붙여넣기 직전 summary 항목이면 DB에서 전체 데이터 로드(`get_full_item`).
  - **패널 토글 단축키** (기본 `ctrl+space`, 설정 가능): 패널 열기/닫기. `set_panel_hotkey()`로 런타임 변경 가능. RegisterHotKey 대신 WH_KEYBOARD_LL을 사용하므로 탐색기 등 모든 포그라운드 앱에서 동작.
  - **절대 일반 Ctrl+V 키 이벤트를 차단하지 않음.**
- **`hotkey_manager.py`** — Win32 RegisterHotKey + 히든 윈도우 기반 단축키 유틸. 현재 패널 토글이 interceptor로 이동되어 실제 등록된 단축키 없음. `_SPECIAL_KEY_MAP`(VK 코드 매핑)은 paste_interceptor가 공유 사용.

### UI 컴포넌트 (`pasteflow/ui/`)

- **`panel.py`** — 고정 섹션 + 검색이 있는 전체 히스토리 패널.
  - 항목 **더블클릭**: 이미지 → `ImagePreviewPopup` 팝업. 텍스트 → `TextPreviewPopup` 팝업 토글.
  - Ctrl+클릭/Shift+클릭으로 다중 선택.
  - 항목 **드래그 → 외부 앱**: fake drag(DragCopyCursor) 방식으로 붙여넣기 (Win32 앱 `WM_PASTE`, Electron/Chromium 앱 `AttachThreadInput+SendInput`).
  - 고정 항목 **드래그 → 재정렬**: fake drag 방식 (QDrag 미사용). 커서 아래 고정 항목 하이라이트 후 마우스 업 시 순서 교환.
  - **`update_queue_highlight()`**: 위젯 재생성 없이 색상만 업데이트하는 빠른 경로 (항목 클릭 시 사용).
  - **각 항목(PanelItemWidget)은 최대 2줄까지만 표시**하며, 좌측 컬러 바(bar)의 높이는 항목 위젯 높이와 완전히 동일해야 한다(`setFixedHeight`로 명시적 설정).
- **`image_preview.py`** — 이미지 미리보기 팝업. 다중 창 동시 표시 지원(`open_new()`로 생성). 휠 줌, 드래그 이동, 닫기 버튼, 더블클릭 닫기. 커서가 있는 모니터에 배치(`screenAt()`).
- **`text_preview.py`** — 텍스트 미리보기 팝업. 싱글턴(`instance()`). 동일 항목 재클릭 시 토글.
- **`toast.py`** — 토스트 알림. 시작 시 "PasteFlow 시작됨" 표시.
- **`tray.py`** — 시스템 트레이. 좌클릭으로 패널 열기.
- **`settings_dialog.py`** — 단축키 커스터마이징, 히스토리 제한, 자동 시작, 자동 닫기/숨기기 설정.

### 단축키 체계

| 단축키 | 동작 | 감지 방식 |
|--------|------|-----------|
| Ctrl+Shift+V | 순차 붙여넣기 (suppress) | WH_KEYBOARD_LL (paste_interceptor) |
| ctrl+space *(기본값, 설정 가능)* | 패널 토글 (suppress) | WH_KEYBOARD_LL (paste_interceptor) |
| 트레이 좌클릭 | 패널 토글 | Qt 이벤트 |

> ⚠️ `Alt+1~9` 직접 붙여넣기, `Ctrl+Shift+X` 큐 초기화, `Ctrl+Shift+Z` 실수 복구는 **의도적으로 제거**됨.

### 순차 붙여넣기 핵심 동작 (가장 중요)

```
사용자 복사 → WM_CLIPBOARDUPDATE → ClipboardMonitor
  → database.save(item)
  → paste_queue.add_item(item) → 진행 중이면 큐 리셋, 아니면 누적 + 포인터 0
  → panel이 열려 있으면 갱신

사용자 Ctrl+Shift+V (키다운) → PasteInterceptor._on_ctrl_shift_v()
  → paste_queue.get_next()
  → 큐 소진이면 → 아무것도 안 함 (suppress만, OS 기본 동작 없음)
  → 항목 있으면 → (필요 시 DB에서 전체 데이터 로드) → win32clipboard로 클립보드 교체
                → Ctrl+V SendInput 주입 → OS 기본 Ctrl+V가 교체된 내용 붙여넣기
```

### 설계 규칙

- **색상 테마**: 전체 UI에 Catppuccin Mocha(다크 테마) 적용.
- **프레임리스 창**: 투명도 지원, Panel에 드래그 이동 구현.
- **Windows 전용**: 클립보드 접근에 `pywin32`와 `WM_CLIPBOARDUPDATE` 사용.
- **설정값**은 SQLite `settings` 테이블(키/값 형태)에 저장.
- **단일 인스턴스**: `main()`에서 Windows 뮤텍스(`PasteFlow_SingleInstance`)로 보장. 핸들은 `app._single_instance_mutex`에 저장(GC 방지).
- **PanelItemWidget 표시 규칙**:
  - 각 항목은 최대 2줄까지만 표시한다. 3줄 이상 word-wrap되는 경우 상단 2줄을 보이고 나머지는 하단 클립.
  - 좌측 컬러 바(bar)의 높이는 항목 위젯 높이(`setFixedHeight`)와 항상 동일하게 유지한다. layout의 Expanding 정책에 의존하지 말고 `self._bar.setFixedHeight(new_h)`로 명시적으로 설정한다.

---

## TDD 적용 범위

### TDD 적용 모듈 (테스트 필수)

| 모듈 | 이유 |
|------|------|
| `models.py` | 순수 데이터 구조, 외부 의존 없음 |
| `database.py` | CRUD 로직, 인메모리 SQLite로 격리 테스트 가능 |
| `paste_queue.py` | 큐 포인터 상태 관리 순수 로직, UI/OS 의존 없음 |

### 수동 확인 적합 모듈

| 모듈 | 이유 |
|------|------|
| `clipboard_monitor.py` | WM_CLIPBOARDUPDATE Windows 이벤트 의존 |
| `paste_interceptor.py` | Ctrl+Shift+V 키 감지 + 클립보드 교체, 실제 환경 필요 |
| `hotkey_manager.py` | 글로벌 단축키 OS 레벨 등록 |
| `ui/*` | GUI 렌더링, 수동 시각 확인 필요 |
| `main.py` | 통합 오케스트레이션 |

---

## 작업 규칙

### 기본 원칙

1. **한 번에 하나의 기능만 구현**한다.
2. 구현 전 반드시 **계획을 설명하고 승인을 받은 후** 진행한다.
3. 기능 완료 후 **진행 상태를 즉시 보고**한다.

### TDD 대상 모듈 작업 순서

```
1. Red   — 실패하는 테스트 먼저 작성
2. Green — 테스트가 통과하는 최소 구현
3. Refactor — 코드 정리 (테스트는 계속 통과 유지)
```

### 수동 확인 대상 모듈 작업 순서

```
1. 구현 계획 설명 → 승인
2. 구현
3. 실행 후 수동 동작 확인 항목 명시
```

---

## ⚠️ 이전 버전 실패 원인 & 반드시 지켜야 할 사항

### 절대 하지 말아야 할 것

1. **Ctrl+V 키 이벤트를 차단(block/suppress)하지 않는다** — 이전 버전에서 순차 붙여넣기가 전혀 동작하지 않은 핵심 원인. keyboard 라이브러리의 `suppress=True`나 `block_key()` 등을 사용하면 안 됨.
2. **키 이벤트를 먹는(consume) 방식으로 구현하지 않는다** — 키를 가로채고 대신 붙여넣기를 실행하는 방식은 타이밍 문제를 일으킴.
3. **패널 드래그에 `QDrag`(OLE D&D)를 사용하지 않는다** — `Qt.WindowType.Tool | WindowStaysOnTopHint` 창에서 Windows OLE 등록이 불완전해 모든 드롭 대상에 금지커서가 표시됨.
4. **드래그 붙여넣기에 백그라운드 스레드에서 `SetForegroundWindow` + `SendInput(Ctrl+V)` 조합을 사용하지 않는다** — 백그라운드 스레드에서 Windows 포그라운드 잠금에 막혀 실패함. **예외**: Qt 메인 스레드에서 `AttachThreadInput`으로 포그라운드 잠금을 우회하는 경우, Electron/Chromium 앱 전용 fallback으로 허용한다.
5. 요청하지 않은 기능 임의 추가 또는 수정.
6. 여러 기능 동시 구현.
7. TDD 대상 모듈에서 테스트 없이 구현.
8. 다른 모듈에 영향을 줄 수 있는 변경을 사전 보고 없이 진행.

### 반드시 지켜야 할 것

1. **클립보드 교체 방식만 사용** — Ctrl+Shift+V 키다운 시점에 `win32clipboard`로 클립보드 내용을 교체하고, Ctrl+V를 SendInput으로 주입한다.
2. **`_self_triggered` 플래그** — PasteFlow가 클립보드에 쓸 때 반드시 이 플래그를 설정하여 자체 모니터가 재감지하지 않도록 한다.
3. **모든 클립보드 형식 보존** — 텍스트만이 아니라 HTML, RTF, 이미지 등 원본 형식을 그대로 클립보드에 복원해야 노션 등에서 서식이 유지된다.
4. **패널 드래그 → 외부 앱 붙여넣기 방식 (앱 종류에 따라 분기)**
   - **Win32/WinUI3 앱** (메모장 등): `SendMessage(hwnd, WM_PASTE, 0, 0)`. 흐름: fake drag(DragCopyCursor) → 마우스 업 시 `_set_clipboard` → 재귀적 `ChildWindowFromPoint`로 최하위 자식 컨트롤 탐색 → `SendMessage(WM_PASTE)`.
   - **Electron/Chromium 앱** (노션, Slack 등): `AttachThreadInput` + `SetForegroundWindow` + `SendInput(Ctrl+V)`. 창 클래스명(`Chrome_*`, `CEF*` 등)으로 판별. 금지 항목 4의 예외에 해당.
5. **`_SPECIAL_KEY_MAP`은 `hotkey_manager.py`에 단일 정의** — `paste_interceptor.py`에서 import해 재사용. 중복 정의 금지.
