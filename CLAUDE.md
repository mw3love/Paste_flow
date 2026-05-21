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
├── ocr_engine.py           # OCR 추상화 — winocr(WinRT) 동기 래퍼
└── ui/
    ├── panel.py            # 전체 클립보드 패널
    ├── image_preview.py    # 이미지 미리보기 팝업 (다중 창 지원)
    ├── text_preview.py     # 텍스트 미리보기 팝업
    ├── toast.py            # 토스트 알림 (시작 알림 등)
    ├── settings_dialog.py  # 설정 화면
    ├── ocr_overlay.py      # OCR 영역 선택 오버레이
    └── tray.py             # 시스템 트레이

tests/
├── test_models.py
├── test_database.py
├── test_paste_queue.py
└── test_ocr_engine.py
```

---

## 아키텍처

### 모듈 역할

- **`main.py`** — 오케스트레이션 레이어. 모든 모듈을 연결하고 클립보드 모니터 → DB → 큐 → UI 간 이벤트 흐름 관리. 단일 인스턴스 보장(Windows 뮤텍스), 시작 알림 토스트 표시. 순차 붙여넣기 첫 발생 시 패널 자동 팝업(`_on_paste_queue_popped`), 큐 소진 시 1초 후 자동 숨기기(`_auto_hide_timer`). 시작 시 HKCU `Run` 키 실제 등록 상태를 DB `auto_start`에 동기화(`_sync_auto_start_from_registry`). **자동 시작 등록 방식**: HKCU `Software\Microsoft\Windows\CurrentVersion\Run`에 `PasteFlow` 값을 등록(`_set_auto_start`). 등록되는 명령은 **항상 `wscript.exe "%LOCALAPPDATA%\PasteFlow\autostart_launcher.vbs"`** 한 형태로, 이 launcher VBS는 `_write_autostart_launcher_vbs()`가 매번 새로 생성하며 `WScript.Sleep _AUTOSTART_DRIVE_WAIT_SEC*1000`(기본 15초) 대기 후 실제 PasteFlow를 hidden 모드로 실행한다. 대기 목적은 Drive(코드가 위치한 곳)가 부팅 직후 마운트되기 전에 실행되어 실패하는 문제 방지. 실행 대상은 빌드 모드별로 분기: exe 빌드는 `sys.executable` 절대경로, 스크립트 모드는 `pythonw.exe` + `run.pyw` 절대경로 — `run.pyw`가 `os.chdir`로 working directory를 자기 위치로 설정하고 예외를 `%LOCALAPPDATA%\PasteFlow\logs\error.log`에 기록한다. **주의**: `python.exe`나 `-m pasteflow.main` 형태로 등록하면 (1) 콘솔 창이 뜨고 (2) Run 키의 working dir이 시스템 기본(`C:\Windows\System32` 등)이라 `ModuleNotFoundError`가 발생하므로 사용 금지. **로컬 데이터 경로**: DB(`pasteflow.db`)·로그·launcher VBS 모두 `%LOCALAPPDATA%\PasteFlow\` 아래 저장. `_resolve_db_path()`가 로컬 DB 부재 + 레거시 Drive DB 존재 시 1회 복사 마이그레이션 수행. **Drive 공유 설정(`settings.json`)**: 코드와 같은 Drive 위치에 `settings.json` 저장 — `_SYNC_KEYS` 화이트리스트(OCR API 키·base_url·모델·언어·엔진, 패널/OCR 단축키, history_max, panel_auto_close)만 양방향 동기화. 앱 시작 시 `_load_shared_settings()`가 화이트리스트 키를 DB에 덮어쓰고, 설정 변경 시 `_save_shared_settings()`가 화이트리스트 키만 JSON에 병합 저장. **동기화 제외 키**: `auto_start`(레지스트리 바인딩), `panel_geometry`(모니터 종속), `ocr_gemini_model_cache`(네트워크 캐시). Named Pipe IPC(`\\.\pipe\PasteFlow_IPC`) 서버 운영 — 두 번째 인스턴스 실행 시 패널 토글 신호 수신 후 해당 인스턴스 즉시 종료. **드래그 붙여넣기 헬퍼 함수** (panel.py의 `drag_to_app_requested` 시그널 처리): `_find_deepest_child()` — 커서 위치 최하위 자식 HWND 재귀 탐색; `_get_explorer_subfolder_at_cursor()` — SysListView32에서 커서 위치 서브폴더 경로 반환(크로스 프로세스 LVM_HITTEST); `_get_explorer_folder()` — CabinetWClass HWND → 드롭 대상 폴더 경로; `_get_desktop_path()` — 사용자 바탕화면 경로; `_save_image_to_folder()` — image_data를 폴더에 PNG 파일로 저장; `_activate_and_send_ctrl_v()` — AttachThreadInput으로 포그라운드 잠금 우회 후 SendInput(Ctrl+V). `_start_foreground_tracker()`는 `SetWinEventHook(EVENT_SYSTEM_FOREGROUND)`으로 포그라운드 창을 연속 추적해 드래그 대상 창을 확인한다.
- **`models.py`** — `ClipboardItem` 데이터클래스 (id, content_type, text_content, image_data, html_content, rtf_content, preview_text, thumbnail, created_at, is_pinned, pin_order, extra_formats). `extra_formats`는 `{format_id: bytes}` dict — 노션 등 앱 전용 포맷 보존.
- **`database.py`** — SQLite(`pasteflow.db`). `clipboard_items`(50개 FIFO 히스토리)와 `settings` 두 테이블. 고정(pin) 항목은 50개 제한에서 제외. `history_order`는 DB 전용 컬럼(ClipboardItem 필드 아님) — 비고정 항목 표시 순서 관리.
- **`clipboard_monitor.py`** — `WM_CLIPBOARDUPDATE` Windows 이벤트 기반 백그라운드 감시. 텍스트, 이미지, HTML, RTF 캡처. `_self_triggered` 플래그로 자체 트리거 방지. `_compute_hash()`로 콘텐츠 해시를 계산해 직전 항목과 동일하면 중복 추가 방지(`content_hash == _last_hash` 비교). `_create_thumbnail()`로 DIB/PNG 데이터에서 썸네일 생성.
- **`paste_queue.py`** — 순차 붙여넣기 큐 관리. 붙여넣기 진행 중(pointer>0) 새 복사 → 큐 리셋 후 새 항목부터 시작. 붙여넣기 전 연속 복사 → 누적. 소진 시 None 반환. 추가 공개 메서드: `set_queue(items, pointer=0)` — 패널에서 특정 항목부터 시작할 때 큐를 직접 교체; `undo_last()` — 포인터를 1 감소시켜 마지막 붙여넣기를 1단계 되돌리기; `clear()` — 큐 및 포인터 초기화.
- **`paste_interceptor.py`** — WH_KEYBOARD_LL 저수준 키보드 훅으로 단축키 감지:
  - **Ctrl+Shift+V**: 큐에서 다음 항목 가져오기 → 클립보드 교체 → `_send_clean_key(VK_V)` 호출(현재 눌린 수정키 해제 → Ctrl+V SendInput → 수정키 복원) (suppress). 붙여넣기 직전 summary 항목이면 DB에서 전체 데이터 로드(인터셉터 생성 시 주입된 `get_full_item` 콜백 호출 → 실제 DB 메서드는 `db.get_item`).
  - **패널 토글 단축키** (기본 `ctrl+space`, 설정 가능): 패널 열기/닫기. `set_panel_hotkey()`로 런타임 변경 가능. RegisterHotKey 대신 WH_KEYBOARD_LL을 사용하므로 탐색기 등 모든 포그라운드 앱에서 동작.
  - **절대 일반 Ctrl+V 키 이벤트를 차단하지 않음.**
  - 추가 공개 메서드: `direct_paste(item)` — 순차 큐 포인터 변경 없이 즉시 붙여넣기(더블클릭·드래그 경로 사용); `send_ctrl_v_to(hwnd)` — 대상 윈도우 포커스 후 Ctrl+V 전송.
- **`hotkey_manager.py`** — Win32 RegisterHotKey + 히든 윈도우 기반 단축키 유틸. 현재 패널 토글이 interceptor로 이동되어 실제 등록된 단축키 없음. `_SPECIAL_KEY_MAP`(VK 코드 매핑)은 paste_interceptor가 공유 사용.
- **`ocr_engine.py`** — OCR 추상화. `OcrEngine(language="ko").recognize(PIL.Image) → str` 동기 API. `kind` 파라미터로 엔진 선택:
  - `"winrt"`(기본): winocr 패키지로 Windows WinRT OCR 래핑. 4096px 초과 이미지 자동 downscale, RGBA/L 모드 자동 변환, 언어팩 미설치 시 AssertionError → RuntimeError('언어팩') 변환.
  - `"gemini"`: Gemini API 직접 호출 (`_recognize_gemini()`). `base_url` 파라미터가 설정된 경우 OpenAI 호환 게이트웨이/프록시로 자동 분기 (`_recognize_openai_compat()`). `"openai_compat"`는 별도 kind 값이 아님. 모델명은 콜러가 `self.model`로 지정 — 빈 문자열이면 `gemini-3-flash-preview`(게이트웨이) / `gemini-2.5-flash`(공식 API) 폴백.
  - 모듈 함수 `_normalize_base_url(base_url)` — 사용자가 endpoint 전체 경로(`/chat/completions`, `/models`, `/completions`, `/embeddings`)나 trailing `/`를 붙여 입력해도 자동으로 SDK 표준 base 형태로 보정. 게이트웨이 호출(`_recognize_openai_compat`, `list_gemini_models`) 양쪽에서 사용.
  - 정적 메서드: `is_winrt_available()`, `is_winrt_language_supported(lang)`, `winrt_supported_languages()`, `list_gemini_models(api_key, base_url)` — 게이트웨이 `/models` 또는 `genai.list_models()`에서 `gemini-*` ID만 필터 반환, 설정창 모델 새로고침에 사용.
  - 호출자(main.py)가 `ThreadPoolExecutor(max_workers=1)`로 워커 스레드에서 실행해 UI 블로킹 방지.

### UI 컴포넌트 (`pasteflow/ui/`)

- **`panel.py`** — 고정 섹션 + 히스토리 패널 (검색 기능 없음 — 의도적으로 제거).
  - 항목 **단일 좌클릭**: 선택(하이라이트)만. Ctrl+클릭/Shift+클릭으로 다중 선택.
  - 항목 **더블클릭**: `paste_item_requested` → 즉시 붙여넣기.
  - **우클릭 컨텍스트 메뉴**: "큐에 추가"(`queue_select_requested`) / 고정·해제 / 복사 / 수정(텍스트만) / 삭제 / 미리보기(이미지→`preview_image_requested` emit, 텍스트→`preview_text_requested` emit). 둘 다 main에서 받아 `ImagePreviewPopup.open_new(...)` / `TextPreviewPopup.open_new(item, ...)` 호출 (동일 패턴).
  - 항목 **드래그 → 외부 앱**: fake drag(DragCopyCursor) 방식으로 붙여넣기.
    - **이미지 + Explorer(`CabinetWClass`) / 바탕화면(`Progman`, `WorkerW`)**: PNG 파일로 저장(`_save_image_to_folder()`). 서브폴더 아이콘 위에 드롭 시 해당 폴더에 저장.
    - **Win32/WinUI3 앱**: `WM_PASTE`.
    - **Electron/Chromium 앱**: `AttachThreadInput+SendInput`.
  - 고정 항목 **드래그 → 재정렬**: fake drag 방식 (QDrag 미사용). 커서 아래 고정 항목 하이라이트 후 마우스 업 시 순서 교환.
  - 히스토리 항목 **드래그 → 재정렬**: `history_reorder_requested` 시그널 → main이 DB 업데이트.
  - **`update_queue_highlight()`**: 위젯 재생성 없이 색상만 업데이트하는 빠른 경로 (큐 상태 변경 시 사용).
  - **`show_near_cursor()`**: 마우스 커서 우하단 +16px에 패널 표시. 화면 경계 초과 시 반전. 단축키/트레이/순차 붙여넣기 자동 팝업 모두 이 메서드 사용.
  - **자동 닫기 토글 버튼(📌)**: 헤더 우측에 배치. ctypes `SetWindowPos(HWND_TOPMOST/NOTOPMOST)`로 TOPMOST 플래그만 변경(창 재생성·깜빡임 없음). 기본값: 자동 닫기 OFF(항상 위에 ON). `_auto_close` 플래그로 관리 — `False`이면 포커스를 잃어도 자동 닫히지 않음(`changeEvent` 조건: `not self._auto_close`). DB `settings`에 저장. `set_auto_close(value)` 메서드로 외부에서 설정.
  - **`panel_hidden` 시그널**: `hideEvent`에서 emit → main이 자동 숨기기 타이머 취소 및 `_panel_opened_by_paste` 플래그 초기화.
  - **`auto_close_changed(bool)` 시그널**: 버튼 클릭 시 emit → main이 DB 저장.
  - **각 항목(PanelItemWidget)은 최대 5줄까지 표시**한다. 높이는 `label_h = visual_lines * fm.lineSpacing() + 8`, `widget_h = label_h + 12` 공식으로 계산. 창 리사이즈 시 `resizeEvent` + `_adjust_text_height()`로 동적 재계산. **레이블과 위젯 모두에 `setFixedHeight`를 명시적으로 설정**해야 한다(위젯에만 설정하면 레이블 높이가 따라오지 않아 클리핑 발생).
- **`image_preview.py`** — 이미지 미리보기 팝업. 다중 창 동시 표시 지원(`open_new()`로 생성). 휠 줌, 드래그 이동, 더블클릭 닫기, ESC 닫기. 커서가 있는 모니터에 배치(`screenAt()`). **활성/비활성 테두리**: 비활성 시 주황(`PEACH`)으로 창 존재 상시 노출, 클릭으로 활성될 때 파랑(`BLUE`)으로 강조 — QSS 동적 프로퍼티가 런타임에 재반영 안 돼 `_apply_active_style(active)`에서 스타일시트 직접 교체.
- **`text_preview.py`** — 텍스트 미리보기 팝업. 다중 창 동시 표시 지원(`open_new(item, panel_geom)` — `ClipboardItem` 전체를 받아 우클릭 메뉴의 복사·수정에 활용).
  - **표시 위젯**: `QPlainTextEdit` (QLabel+QScrollArea 아님). `setWordWrapMode(WrapAtWordBoundaryOrAnywhere)`로 공백 없는 긴 URL/해시도 문자 단위 wrap. QLabel은 word-boundary 없는 토큰을 절대 잘라주지 않아 폐기.
  - **"한 번에 다 보임" 정책**: 양쪽 스크롤바 영구 차단(`ScrollBarAlwaysOff`) + editor `FocusPolicy.NoFocus` — QPlainTextEdit 내부 가짜 vScroll(`vScrollMax≥1`)이 키보드(스페이스/PageDown)로 노출돼 빈 영역이 보이던 문제 차단. width는 `PREVIEW_INITIAL_MAX_W * scale` (zoom과 비례 확장, 화면 너비 cap), height는 화면 한계까지 자유 확장하여 모든 줄 표시.
  - **`LineWrapMode` 동적 전환**: 자연 너비가 popup width cap 안에 들어오면 `NoWrap` 강제 (QPlainTextEdit의 viewport에 내부 padding이 있어 textWidth를 맞춰도 sub-pixel 차이로 wrap이 새는 경우 발생 — 휠 줌마다 1↔2줄 깜빡임의 원인). 초과 시에만 `WidgetWidth` wrap.
  - **크기 계산**: 자연 너비/높이 측정에 독립 `QTextDocument` 사용 — QPlainTextEdit의 자체 document는 lazy layout이라 `setTextWidth` 직후 `size()`가 갱신 안 됨. `math.ceil(idealWidth())`로 sub-pixel 부족분 보정.
  - **전체 창 드래그**: 텍스트 부분 선택 미지원(`NoTextInteraction`). 부분 텍스트가 필요하면 우클릭 메뉴 `수정`으로 편집 다이얼로그에서 자연스럽게 선택. viewport + popup 본체 양쪽 모두에서 left-drag 이동 처리.
  - **우클릭 메뉴**: `전체 복사` / `수정` / `닫기` (패널 메뉴와 동일 명칭). 시그널 `copy_requested(ClipboardItem)` → main의 `_on_copy_item`, `edit_requested(item_id)` → main의 `_on_preview_edit_request`(`EditItemDialog` 띄우고 `_on_edit_item`으로 위임). QPlainTextEdit 기본 우클릭 메뉴는 `setContextMenuPolicy(NoContextMenu)`로 차단.
  - **활성/비활성 테두리**: 이미지 미리보기와 동일 정책(비활성 주황, 활성 파랑). 프레임리스 최상위 위젯에 건 테두리가 자식에 가려지는 문제를 피하기 위해 내부 `popup_container`에 적용.
  - **`_clamp_to_screen(avail)`**: resize 후 popup이 화면 밖으로 나가면 안쪽으로 끌어들임.
- **`toast.py`** — 토스트 알림. 시작 시 "PasteFlow 시작됨" 표시.
- **`tray.py`** — 시스템 트레이. 좌클릭 시 `panel_toggle_requested` 시그널 emit → main이 패널 토글.
- **`settings_dialog.py`** — 단축키 커스터마이징, 히스토리 제한, 자동 시작, 자동 닫기/숨기기 설정. OCR 언어 콤보는 `OcrEngine.winrt_supported_languages()`로 동적 채움(winocr 미설치 시 기본 목록 폴백). Gemini 모델 콤보 옆 ↻ **새로고침 버튼**(Qt 표준 아이콘 `SP_BrowserReload` — 폰트 무관 보장)으로 `OcrEngine.list_gemini_models()` 호출 → 결과를 콤보에 반영하고 `KEY_OCR_GEMINI_MODEL_CACHE`(JSON list)에 저장 → 다음 실행 시 캐시 로드. 네트워크 호출은 `threading.Thread` + 내부 시그널 `_models_fetched(list, str)`로 UI 스레드 안전 통신. 기본 모델 목록은 `_DEFAULT_GEMINI_MODELS = ("gemini-3-flash-preview", "gemini-3.1-pro-preview", "gemini-2.5-pro")`. 콤보는 **가격 순 정렬**(`_model_cost_rank`: flash-lite=0 < flash=1 < pro=2)로 저렴한 모델이 항상 위에 표시. 콤보 바로 아래 회색 힌트 라벨(`_model_hint`)이 `💡 가장 저렴: {모델명}`을 안내하며 콤보 갱신 시 `_update_model_hint()`로 자동 동기화.
- **`ocr_overlay.py`** — 모니터별 분리 오버레이. `OcrOverlay`는 매니저(QObject 베이스, QWidget 아님)이고 실제 위젯은 `_ScreenOverlay`로 각 QScreen마다 1개씩 생성. `start()` 호출 시 모니터 수만큼 `_ScreenOverlay`를 만들고 각각 자기 화면을 `screen.grabWindow(0, 0, 0, w, h)`로 캡처해 표시. 한 모니터에서 드래그 시작되면 `drag_started` 시그널로 매니저가 다른 오버레이를 `deactivate()`(마스크만 표시·입력 차단). ESC/우클릭은 어느 오버레이에서든 전체 취소. **다중 DPI 모니터 대응**: 가상 데스크톱 전체를 단일 위젯으로 덮으면 Qt 백킹 스토어 DPR이 하나로 고정돼, DPR이 다른 모니터에 진입할 때 좌표·크기가 어긋나 고DPI 노트북 화면이 좌상단 일부로 축소되는 증상이 발생한다. 모니터별 분리 위젯 + `setScreen()` 명시 바인딩으로 Qt가 모니터별 DPR을 독립 처리하므로 문제 자체가 발생하지 않는다. 공개 API(`region_captured(QPixmap)`, `cancelled()`, `start()`)는 호출부 변경 없이 유지.

### 단축키 체계

| 단축키 | 동작 | 감지 방식 |
|--------|------|-----------|
| Ctrl+Shift+V | 순차 붙여넣기 (suppress) | WH_KEYBOARD_LL (paste_interceptor) |
| ctrl+space *(기본값, 설정 가능)* | 패널 토글 (suppress) | WH_KEYBOARD_LL (paste_interceptor) |
| ctrl+shift+s *(기본값, 설정 가능)* | OCR 영역 선택 시작 (suppress) | WH_KEYBOARD_LL (paste_interceptor) |
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
  → _on_paste_from_hook() → pointer==1이면 paste_queue_popped 시그널 emit
                           → pointer>=total이면 paste_queue_done 시그널 emit

paste_queue_popped → 패널이 닫혀 있으면 show_near_cursor()로 팝업, _panel_opened_by_paste=True
paste_queue_done  → _panel_opened_by_paste이면 _auto_hide_timer(1초) 시작 → 패널 자동 숨기기
```

### 설계 규칙

- **색상 테마**: 전체 UI에 Catppuccin Mocha(다크 테마) 적용.
- **프레임리스 창**: 투명도 지원, Panel에 드래그 이동 구현.
- **Windows 전용**: 클립보드 접근에 `pywin32`와 `WM_CLIPBOARDUPDATE` 사용.
- **설정값**은 SQLite `settings` 테이블(키/값 형태)에 저장.
- **단일 인스턴스**: `main()`에서 Windows 뮤텍스(`PasteFlow_SingleInstance`)로 보장. 핸들은 `app._single_instance_mutex`에 저장(GC 방지). 두 번째 실행 시 Named Pipe(`\\.\pipe\PasteFlow_IPC`)로 첫 번째 인스턴스에 패널 토글 신호 전달 후 즉시 종료.
- **자동 닫기(Auto Close)**: 기본값 OFF(항상 위에 ON). `Qt.WindowType.WindowStaysOnTopHint`를 재설정하지 않고 ctypes `SetWindowPos(HWND_TOPMOST/NOTOPMOST)`로 TOPMOST 플래그만 조작하여 깜빡임 방지. `_auto_close`가 `False`이면 `changeEvent` 자동 닫기 조건에서 제외됨.
- **PanelItemWidget 표시 규칙**:
  - 각 항목은 최대 5줄까지 표시한다. 6줄 이상 word-wrap되는 경우 상단 5줄을 보이고 나머지는 하단 클립.
  - 높이 공식: `label_h = visual_lines * fm.lineSpacing() + 8`, `widget_h = label_h + 12`. `fm.lineSpacing()`을 사용해야 하며(`fm.height()` 사용 시 줄 간격 오차 발생), **레이블(`_text_label`)과 위젯 양쪽에 `setFixedHeight`를 모두 설정**해야 클리핑이 없다. 위젯에만 설정하면 Qt 레이아웃이 레이블 높이를 독립적으로 결정해 텍스트가 잘린다.

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
5. **OCR 결과를 클립보드에 넣을 때 `_self_triggered` 플래그 설정을 누락하지 않는다** — `interceptor._set_clipboard(item)` 호출이 내부적으로 `monitor.set_self_triggered(0.5)`를 처리하므로 반드시 이 경로를 사용할 것. 직접 `win32clipboard`를 쓰면 클립보드 모니터가 재감지해 동일 항목이 큐에 중복 추가됨.
6. 요청하지 않은 기능 임의 추가 또는 수정.
7. 여러 기능 동시 구현.
8. TDD 대상 모듈에서 테스트 없이 구현.
9. 다른 모듈에 영향을 줄 수 있는 변경을 사전 보고 없이 진행.

### 반드시 지켜야 할 것

1. **클립보드 교체 방식만 사용** — Ctrl+Shift+V 키다운 시점에 `win32clipboard`로 클립보드 내용을 교체하고, Ctrl+V를 SendInput으로 주입한다.
2. **`_self_triggered` 플래그** — PasteFlow가 클립보드에 쓸 때 반드시 이 플래그를 설정하여 자체 모니터가 재감지하지 않도록 한다.
3. **모든 클립보드 형식 보존** — 텍스트만이 아니라 HTML, RTF, 이미지 등 원본 형식을 그대로 클립보드에 복원해야 노션 등에서 서식이 유지된다.
4. **패널 드래그 → 외부 앱 붙여넣기 방식 (앱 종류에 따라 분기)**
   - **이미지 항목 + Explorer(`CabinetWClass`) / 바탕화면(`Progman`, `WorkerW`)**: `_save_image_to_folder()`로 PNG 파일 저장. 서브폴더 아이콘 위 드롭 시 해당 폴더에 저장(크로스 프로세스 `LVM_HITTEST`). 저장 성공 시 클립보드 경로 생략.
   - **Win32/WinUI3 앱** (메모장 등): `SendMessage(hwnd, WM_PASTE, 0, 0)`. 흐름: fake drag(DragCopyCursor) → 마우스 업 시 `_set_clipboard` → 재귀적 `ChildWindowFromPoint`로 최하위 자식 컨트롤 탐색 → `SendMessage(WM_PASTE)`.
   - **Electron/Chromium 앱** (노션, Slack 등): `AttachThreadInput` + `SetForegroundWindow` + `SendInput(Ctrl+V)`. 창 클래스명(`Chrome_*`, `CEF*` 등)으로 판별. 금지 항목 4의 예외에 해당.
5. **`_SPECIAL_KEY_MAP`은 `hotkey_manager.py`에 단일 정의** — `paste_interceptor.py`에서 import해 재사용. 중복 정의 금지.
