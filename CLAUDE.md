# CLAUDE.md

이 파일은 Claude Code(claude.ai/code)가 이 저장소에서 작업할 때 참고하는 안내 문서입니다.

## 프로젝트 개요

**PasteFlow** — 순차 붙여넣기 자동화 클립보드 매니저. Windows 10/11 전용. 복사한 순서대로 Ctrl+V를 누를 때마다 다음 항목이 붙여넣어지는 **항상 활성** 방식. PyQt6 기반. 전체 요구사항은 `PRD.md` 참고.

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
├── paste_interceptor.py    # Ctrl+V 감지 → 클립보드 교체 실행 (핵심)
├── hotkey_manager.py       # 글로벌 단축키 등록/해제
├── database.py             # SQLite CRUD (clipboard_items, settings)
├── models.py               # ClipboardItem 데이터 모델
└── ui/
    ├── mini_window.py      # 미니 클립보드 창 (우하단 플로팅)
    ├── panel.py            # 전체 클립보드 패널
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

- **`main.py`** — 오케스트레이션 레이어. 모든 모듈을 연결하고 클립보드 모니터 → DB → 큐 → UI 간 이벤트 흐름 관리.
- **`models.py`** — `ClipboardItem` 데이터클래스 (id, content_type, text_content, image_data, html_content, rtf_content, preview_text, thumbnail, created_at, is_pinned, pin_order).
- **`database.py`** — SQLite(`pasteflow.db`). `clipboard_items`(50개 FIFO 히스토리)와 `settings` 두 테이블. 고정(pin) 항목은 50개 제한에서 제외.
- **`clipboard_monitor.py`** — `WM_CLIPBOARDUPDATE` Windows 이벤트 기반 백그라운드 감시. 텍스트, 이미지, HTML, RTF 캡처. `_self_triggered` 플래그로 자체 트리거 방지.
- **`paste_queue.py`** — 순차 붙여넣기 큐 관리. 붙여넣기 진행 중(pointer>0) 새 복사 → 큐 리셋 후 새 항목부터 시작. 붙여넣기 전 연속 복사 → 누적. 소진 시 None 반환.
- **`paste_interceptor.py`** — Ctrl+V 키다운 감지 → 큐에서 다음 항목 가져오기 → 클립보드 내용 교체 → 키 이벤트 통과. **절대 키 이벤트를 차단하지 않음.**
- **`hotkey_manager.py`** — DB 설정에서 전역 단축키 등록. 기본 바인딩: Alt+V(패널 토글), Alt+1~9(직접 붙여넣기).

### UI 컴포넌트 (`pasteflow/ui/`)

- **`mini_window.py`** — 우하단 플로팅 창. **평소 숨김 → 복사 시 fade-in → 5초 후 fade-out 자동 사라짐** (마우스 오버 중에는 사라지지 않음). 최근 5개 항목 표시, 순차 상태 "N/M" 표시, 현재 대상 항목 청록색 좌측 바 강조. ▲ 버튼으로 전체 패널 열기.
- **`panel.py`** — 고정 섹션 + 검색이 있는 전체 히스토리 패널. 더블클릭 시 붙여넣기 후 닫힘. Ctrl+클릭/Shift+클릭으로 다중 선택. 비고정 항목 드래그 → 외부 앱에 `WM_PASTE`로 직접 붙여넣기 (포커스 불필요). 고정 항목 드래그 → `QDrag`로 pin 재정렬. **각 항목(PanelItemWidget)은 최대 2줄까지만 표시**하며, 좌측 컬러 바(bar)의 높이는 항목 위젯 높이와 완전히 동일해야 한다(`setFixedHeight`로 명시적 설정).
- **`tray.py`** — 시스템 트레이. 좌클릭으로 패널 열기.
- **`settings_dialog.py`** — 단축키 커스터마이징, 히스토리 제한, 자동 시작, 자동 닫기/숨기기 설정.

### 순차 붙여넣기 핵심 동작 (가장 중요)

```
사용자 복사 → WM_CLIPBOARDUPDATE → ClipboardMonitor
  → database.save(item)
  → paste_queue.add_item(item) → 진행 중이면 큐 리셋, 아니면 누적 + 포인터 0
  → mini_window.show_and_refresh() → 자동 사라짐 타이머 시작 (5초)
      (마우스 오버 시 타이머 일시정지, 벗어나면 재시작)

사용자 Ctrl+V (키다운) → PasteInterceptor.on_ctrl_v_keydown()
  → paste_queue.get_next()
  → 큐 소진이면 → 아무것도 안 함 (OS 기본 동작)
  → 항목 있으면 → win32clipboard로 클립보드 교체 → 키 이벤트 통과
  → OS 기본 Ctrl+V가 교체된 내용 붙여넣기
```

### 설계 규칙

- **색상 테마**: 전체 UI에 Catppuccin Mocha(다크 테마) 적용.
- **프레임리스 창**: 투명도 지원, MiniWindow과 Panel에 드래그 이동 구현.
- **Windows 전용**: 클립보드 접근에 `pywin32`와 `WM_CLIPBOARDUPDATE` 사용.
- **설정값**은 SQLite `settings` 테이블(키/값 형태)에 저장.
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
| `paste_interceptor.py` | Ctrl+V 키 감지 + 클립보드 교체, 실제 환경 필요 |
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

1. **클립보드 교체 방식만 사용** — Ctrl+V 키다운 시점에 `win32clipboard`로 클립보드 내용을 교체하고, 키 이벤트는 그대로 통과시킨다.
2. **`_self_triggered` 플래그** — PasteFlow가 클립보드에 쓸 때 반드시 이 플래그를 설정하여 자체 모니터가 재감지하지 않도록 한다.
3. **Phase 1에서 순차 붙여넣기부터 검증** — 다른 기능보다 순차 붙여넣기가 100% 동작하는 것을 최우선으로 확인한다.
4. **모든 클립보드 형식 보존** — 텍스트만이 아니라 HTML, RTF, 이미지 등 원본 형식을 그대로 클립보드에 복원해야 노션 등에서 서식이 유지된다.
5. **패널 드래그 → 외부 앱 붙여넣기 방식 (앱 종류에 따라 분기)**
   - **Win32/WinUI3 앱** (메모장 등): `SendMessage(hwnd, WM_PASTE, 0, 0)`. 흐름: fake drag(DragCopyCursor) → 마우스 업 시 `_set_clipboard` → 재귀적 `ChildWindowFromPoint`로 최하위 자식 컨트롤 탐색 → `SendMessage(WM_PASTE)`.
   - **Electron/Chromium 앱** (노션, Slack 등): `AttachThreadInput` + `SetForegroundWindow` + `SendInput(Ctrl+V)`. 창 클래스명(`Chrome_*`, `CEF*` 등)으로 판별. 금지 항목 4의 예외에 해당.
