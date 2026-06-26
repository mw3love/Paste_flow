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

# 단독 실행 .exe 빌드 (진입점·onefile·windowed·아이콘·버전 메타데이터는 spec에 인코딩)
pyinstaller PasteFlow.spec    # 산출물: dist/PasteFlow-{버전}.exe
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
├── crypto.py               # DPAPI 시크릿 보호 (API 키 암호화 저장)
├── uia.py                  # Windows UI Automation hit-test (ElementFromPoint — 마그네틱 캡처용, comtypes)
├── ocr_engine.py           # OCR 추상화 — winocr(WinRT) 동기 래퍼
└── ui/
    ├── panel.py            # 전체 클립보드 패널
    ├── image_preview.py    # 이미지 미리보기 팝업 (다중 창 지원, Space로 인라인 주석 편집 진입)
    ├── image_annotator.py  # 이미지 주석 편집기 (QGraphicsScene — 도형·선·화살표·펜·텍스트·번호)
    ├── text_preview.py     # 텍스트 미리보기 팝업
    ├── toast.py            # 우하단 스택형 토스트 (복사 알림·시작·OCR)
    ├── paste_hud.py        # 순차 붙여넣기 진행 HUD (큐 목록·포인터 실시간)
    ├── settings_dialog.py  # 설정 화면
    ├── ocr_overlay.py      # OCR 영역 선택 오버레이
    ├── capture_overlay.py  # 마그네틱 영역 캡처 오버레이 (Snipaste식 — 요소 스냅·자유드래그·크로스모니터 합성)
    ├── ai_query.py         # AI 질의 입력 다이얼로그 (우클릭 "AI에게 질문")
    └── tray.py             # 시스템 트레이

tests/
├── test_models.py
├── test_database.py
├── test_paste_queue.py
├── test_ocr_engine.py
└── test_crypto.py
```

---

## 아키텍처

### 모듈 역할

- **`main.py`** — 오케스트레이션 레이어. 모든 모듈을 연결하고 클립보드 모니터 → DB → 큐 → UI 간 이벤트 흐름 관리. 단일 인스턴스 보장(Windows 뮤텍스), 시작 알림 토스트 표시. 순차 붙여넣기 시 진행 HUD(`PasteHud`)를 표시·실시간 갱신하고 큐 소진/중단 시 fade-out한다(`_update_paste_ui`/`_on_paste_queue_done`). 실제 복사(클립보드 모니터 경로) 시 우하단 스택 토스트 표시(`_on_copy_toast` — 설정 `notify_on_copy`로 on/off, OCR 결과는 자체 토스트가 있어 `_persist_clipboard_item`으로 우회). **일반 Ctrl+V 큐 비우기**: 인터셉터의 `on_plain_paste` 콜백 → `_bridge.plain_paste` 시그널 → 메인 스레드 슬롯 `_on_plain_paste()`에서 `queue.mark_plain_paste()` + 트레이/패널 큐 표시 초기화. **큐 idle 시간 주입**: 시작 시 DB `queue_idle_reset_sec`(기본 10초)를 읽어 `queue.set_idle_reset_sec()`로 주입하고, 설정 변경 시 즉시 반영. 시작 시 HKCU `Run` 키 실제 등록 상태를 DB `auto_start`에 동기화(`_sync_auto_start_from_registry`). **자동 시작 등록 방식**: HKCU `Software\Microsoft\Windows\CurrentVersion\Run`에 `PasteFlow` 값을 등록(`_set_auto_start`). 등록되는 명령은 **항상 `wscript.exe "%LOCALAPPDATA%\PasteFlow\autostart_launcher.vbs"`** 한 형태로, 이 launcher VBS는 `_write_autostart_launcher_vbs()`가 매번 새로 생성하며 `WScript.Sleep _AUTOSTART_DRIVE_WAIT_SEC*1000`(기본 15초) 대기 후 실제 PasteFlow를 hidden 모드로 실행한다. 대기 목적은 Drive(코드가 위치한 곳)가 부팅 직후 마운트되기 전에 실행되어 실패하는 문제 방지. 실행 대상은 빌드 모드별로 분기: exe 빌드는 `sys.executable` 절대경로, 스크립트 모드는 `pythonw.exe` + `run.pyw` 절대경로 — `run.pyw`가 `os.chdir`로 working directory를 자기 위치로 설정하고 예외를 `%LOCALAPPDATA%\PasteFlow\logs\error.log`에 기록한다. **주의**: `python.exe`나 `-m pasteflow.main` 형태로 등록하면 (1) 콘솔 창이 뜨고 (2) Run 키의 working dir이 시스템 기본(`C:\Windows\System32` 등)이라 `ModuleNotFoundError`가 발생하므로 사용 금지. **로컬 데이터 경로**: DB(`pasteflow.db`)·로그·launcher VBS 모두 `%LOCALAPPDATA%\PasteFlow\` 아래 저장. **모든 설정은 로컬 DB에만 저장** — 다중 PC `settings.json` 공유는 v1.4.0에서 폐기(DPAPI가 PC/계정 바인딩이라 시크릿 동기화와 양립 불가). `_resolve_db_path()`가 로컬 DB 부재 + 레거시 Drive DB 존재 시 1회 복사 마이그레이션 수행. **시크릿 처리**: `_SECRET_KEYS = {ocr_gemini_api_key_official, ocr_gemini_api_key_gateway}`는 DPAPI(`pasteflow/crypto.py`)로 암호화해 DB에 저장 — 저장 포맷 `enc:v1:<base64(CryptProtectData blob)>`. 읽기는 `_get_secret(key)`(= `unprotect ∘ db.get_setting`)로 복호화 후 사용, 쓰기는 `_on_settings_changed`에서 `_SECRET_KEYS`인 키만 `crypto.protect()`로 암호화한 뒤 저장. `crypto.protect/unprotect`는 빈값·이미 암호화된 값(`is_protected`)을 passthrough라 idempotent, `unprotect` 실패 시 `""`+경고(크래시 방지, 사용자는 설정창에서 재입력). **`_migrate_secrets(db)`**: 시작 시 1회 호출돼 ① `_SECRET_KEYS` 평문 → 암호화(`is_protected` 가드로 idempotent), ② 코드 어디서도 참조되지 않는 고아 설정 키 4종(`ocr_api_key`·`ocr_base_url`·`hotkey_settings`·`panel_always_on_top`) DELETE. **Gemini 키 분리 마이그레이션**(`_migrate_split_gemini_keys`): 시작 시 1회 호출돼 옛 단일 키(`ocr_gemini_api_key`/`ocr_gemini_model`/`ocr_gemini_model_cache`)를 `base_url` 유무 기준으로 backend별 슬롯(`_official`/`_gateway`)에 이전하고 옛 키를 DB에서 삭제. 새 api_key 슬롯에 쓸 때 `protect()`로 암호화. 옛 키는 한 번 삭제하면 다시 들어오지 않으므로 idempotent. Named Pipe IPC(`\\.\pipe\PasteFlow_IPC`) 서버 운영 — 두 번째 인스턴스 실행 시 패널 토글 신호 수신 후 해당 인스턴스 즉시 종료. **드래그 붙여넣기 헬퍼 함수** (panel.py의 `drag_to_app_requested` 시그널 처리): `_find_deepest_child()` — 커서 위치 최하위 자식 HWND 재귀 탐색; `_get_explorer_subfolder_at_cursor()` — SysListView32에서 커서 위치 서브폴더 경로 반환(크로스 프로세스 LVM_HITTEST); `_get_explorer_folder()` — CabinetWClass HWND → 드롭 대상 폴더 경로; `_get_desktop_path()` — 사용자 바탕화면 경로; `_image_data_to_png_bytes()` — image_data(PNG/JPEG/GIF/WebP/CF_DIB)를 PNG bytes로 인메모리 변환. PIL이 직접 못 여는 raw CF_DIB는 BMP 파일 헤더를 조립해 인식시킨다(`_create_thumbnail`과 동일 기법). `_save_image_to_folder`·이미지 AI 질의가 공유 — 영역 캡처(Alt+F2)처럼 raw DIB로 저장된 이미지도 정상 처리; `_save_image_to_folder()` — `_image_data_to_png_bytes`로 변환한 PNG를 폴더에 파일로 저장; `_save_image_to_drop_temp()` — `%TEMP%\PasteFlow\`에 임시 PNG 저장 후 절대경로 반환(Alt+드래그·우클릭 "파일로 저장 후 경로 복사"·`Ctrl+Shift+P` 이미지→경로 단축키 공용); `_read_image_from_clipboard()` — 현재 클립보드에서 이미지 bytes 반환(PNG 우선, 없으면 CF_DIB raw — 둘 다 `_save_image_to_drop_temp`가 그대로 처리); `_activate_and_send_ctrl_v(hwnd, sender=None)` — AttachThreadInput으로 포그라운드 잠금 우회 후 키 주입. `sender`(인자 없는 콜러블, 기본 `_send_ctrl_v_plain`)로 실제 주입 함수 교체 가능 — Alt+드래그처럼 호출 시점에 수정키가 눌린 경로는 `interceptor._release_modifiers_and_send_ctrl_v`를 넘긴다. AttachThreadInput은 **타겟 창 스레드**(`GetWindowThreadProcessId(hwnd)`)에 건다 — 드래그 시점 포그라운드는 PasteFlow 패널 자신(같은 스레드)이라 거기 거는 건 무효였고, 타겟 스레드에 붙어야 `SetForegroundWindow`+`SetFocus`가 포커스까지 첫 시도부터 넘긴다. 주입 직전 `GetAsyncKeyState(VK_MENU)`로 **Alt가 물리적으로 떨어질 때까지 QTimer 폴링**(25ms 간격·최대 1.5초) 후 Ctrl+V를 보낸다 — 드롭 순간 눌린 Alt는 가상 KEYUP으로 안 떨어지므로(`GetAsyncKeyState`는 물리 키 기준) 그냥 보내면 타겟이 `Ctrl+Alt+V`로 오인. **알려진 한계(cold 창)**: 타겟이 직전 비활성이던 Chromium 창은 프로그램적 활성화로 렌더러 텍스트 입력칸 포커스가 복원되지 않아 합성 Ctrl+V가 불안정(IME까지 겹치면 `ㅍ`로 입력되기도). 합성 클릭·간격 둔 Ctrl+V 등으로 시도했으나 불안정해 포기 — 사용자가 타겟 창을 먼저 클릭해 활성(warm) 상태로 만든 뒤 드래그하면 안정적이다. `_start_foreground_tracker()`는 `SetWinEventHook(EVENT_SYSTEM_FOREGROUND)`으로 포그라운드 창을 연속 추적해 드래그 대상 창을 확인한다. **이미지→경로 단축키 슬롯**: `_on_image_to_path_hotkey()` — `_read_image_from_clipboard()`로 현재 클립보드 이미지를 읽어 `_save_image_to_drop_temp()`로 PNG 저장 → 경로 텍스트 `ClipboardItem` 생성 → `interceptor._set_clipboard()` (규칙 5: `_self_triggered` 자동 처리) → 50ms 후 `interceptor._send_clean_key(VK_V)`로 현재 포그라운드 창에 Ctrl+V 주입. 성공 시 저장한 PNG의 **썸네일을 토스트에 함께 표시**(`ToastNotification(..., icon="", image_path=saved_path)`) — Claude Code CLI 등은 붙여넣은 경로를 `[Image #N]` 라벨로만 보여줘 실제 이미지가 안 보이므로, "의도한 이미지가 맞는지" 그 자리에서 시각 확인하기 위함. 썸네일이 카테고리를 대신하므로 `🔤` 아이콘은 생략(`icon=""`). **`_activate_and_send_ctrl_v`를 쓰지 않는 이유**: 발화 시점에 사용자가 Ctrl+Shift를 누른 상태인데 `_send_ctrl_v_plain`은 수정키 해제 없이 단순 Ctrl down/V/Ctrl up만 보내 OS가 `Ctrl+Shift+V`로 인식한다. `_send_clean_key`는 Ctrl+Shift+V 순차 붙여넣기와 동일하게 수정키 해제·복원·입력기 전환 마스킹을 처리한다. 클립보드에 이미지가 없으면 토스트만 표시. **OCR 워커 공통화**: `_start_ocr_worker(png_bytes)`가 영역 캡처 OCR(`_on_ocr_region_captured`)과 이미지 항목 OCR(`_on_ocr_image_item` / `_on_ocr_image_by_id`)을 공유한다. 이미지 항목 OCR은 panel/preview 우클릭에서 진입 — `image_data`(DIB 또는 PNG)를 PIL로 PNG bytes로 변환 후 동일 워커로 위임하며, 결과는 기존 `_on_ocr_done`(클립보드+DB+큐+정중앙 결과 칩) 흐름을 그대로 탄다. **Gemini backend 분기**: `_start_ocr_worker`가 `ocr_gemini_backend` 설정(미설정 시 `base_url` 유무로 추론)을 보고 official/gateway에 해당하는 `ocr_gemini_api_key_*`(암호화 저장 → `_get_secret`으로 복호화) / `ocr_gemini_model_*`을 골라 `OcrEngine`에 주입. official 분기는 `base_url=""`으로 강제(공식 API는 base_url 무시). **Gemini 백엔드 분기 공통화**: 위 backend→key/url/model 해석 로직을 `_resolve_gemini_cfg() → (api_key, base_url, model)` 헬퍼로 추출해 OCR 워커와 AI 질의 워커가 공유한다(DB 접근은 `_lock`으로 직렬화되어 워커 스레드 호출 안전). **AI 질의(우클릭 "AI에게 질문")**: panel이 `ai_query_requested(item_id)` emit → `_on_ai_query_requested`(id 기반 래퍼)가 DB 로드 후 `_ai_query_for_item(item)` 코어로 위임. 코어는 항목을 컨텍스트로 `AiQueryDialog`(질문 입력 모달)를 띄우고 — **텍스트 항목은 텍스트를, 이미지 항목은 `_image_data_to_png_bytes`로 변환한 PNG 썸네일을 컨텍스트로 표시** — 입력 시 `_start_ai_worker(question, context, image_png=None)`가 `_resolve_gemini_cfg()` 설정으로 `OcrEngine(kind="gemini").ask(question, context, image_png=...)`를 워커 스레드에서 호출(OCR 엔진 설정과 무관하게 항상 gemini 경로). **이미지 항목이면 이미지를 멀티모달로 전송(시각 질의)**, 텍스트면 텍스트 컨텍스트만. `_ai_query_for_item`은 화면 핀(Alt+F3)·미리보기 팝업 우클릭 "AI에게 질문"(`ImagePreviewPopup.ai_requested`)에서도 호출 — DB id 없는 임시 항목도 받는다. 결과/에러는 `_bridge.ai_done(question, answer)`/`ai_error` 시그널 → 슬롯 `_on_ai_done`이 답변을 **읽기 전용 `TextPreviewPopup`(editable=False, markdown=True, center=True, 수정 메뉴 숨김)**으로 표시(`**Q.** 질문` + `---` + 답변을 마크다운 렌더, DB 미저장 — 표시 전용), `_on_ai_error`는 토스트(`🤖`) + API 키 미설정 시 설정창 자동 오픈. **멀티모니터 위치**: AI 입력창(`AiQueryDialog`)은 `showEvent`에서 커서가 있는 모니터의 커서 옆으로 이동, 답변창은 `_ai_query_for_item`이 질문 제출 시점 커서를 `self._ai_anchor`에 저장하고 `_on_ai_done`이 `TextPreviewPopup.open_new(..., center=True)`로 **그 커서가 있던 모니터 정중앙**에 띄운다(읽기 편하고 가장자리 잘림 없음). **진행/결과 표시(커서 모니터 정중앙 칩)**: OCR·AI가 공유하는 `_start_cursor_progress(prefix, icon, anchor)`가 지속형 토스트(`ToastNotification(anchor=..., center=True)` — 클릭 통과, 앵커가 속한 모니터 정중앙)로 경과시간·점 애니메이션을 0.5초마다 갱신(`_tick_cursor_progress`). 종료는 둘로 갈린다: `_stop_cursor_progress`(칩 즉시 fade — AI는 답변창이 같은 정중앙에 펼쳐지므로 칩만 닫음), `_finish_cursor_progress(message)`(칩을 결과 메시지로 전환 후 1.5초 뒤 fade — OCR은 `✓ 인식 앞부분…` 앞 24자 미리보기로 "제대로 됐나" 확인). 옛 우하단 고정 진행 토스트(`_start_ai_progress`/`_tick`/`_stop`)는 이 공용 칩으로 대체됨. 모델 폴백 발생 시 `_bridge.ocr_fallback`를 재사용해 알림(이 경로만 토스트 아이콘이 OCR `🔤`). **알려진 한계**: 공식 Google API 이미지 첨부 경로(`_ask_google_genai`)는 신규라 실호출 검증 권장(게이트웨이 경로는 OCR과 동일 `image_url` 배관이라 안전).
- **`models.py`** — `ClipboardItem` 데이터클래스 (id, content_type, text_content, image_data, html_content, rtf_content, preview_text, thumbnail, created_at, is_pinned, pin_order, extra_formats). `extra_formats`는 `{format_id: bytes}` dict — 노션 등 앱 전용 포맷 보존.
- **`database.py`** — SQLite(`pasteflow.db`). `clipboard_items`(50개 FIFO 히스토리)와 `settings` 두 테이블. 고정(pin) 항목은 50개 제한에서 제외. `history_order`는 DB 전용 컬럼(ClipboardItem 필드 아님) — 비고정 항목 표시 순서 관리. **동시성**: 단일 커넥션을 모든 호출이 공유하며 `_lock`(RLock)으로 읽기·쓰기를 직렬화 — UI/훅/워커가 동시에 접근해도 안전(레이스로 인한 `InterfaceError` 방지). 외부에서 `with db._lock:` 블록을 잡고 다중 쿼리를 묶을 수 있다(`_migrate_secrets` 등에서 사용).
- **`clipboard_monitor.py`** — `WM_CLIPBOARDUPDATE` Windows 이벤트 기반 백그라운드 감시. 텍스트, 이미지, HTML, RTF 캡처. `_self_triggered` 플래그로 자체 트리거 방지. `_compute_hash()`로 콘텐츠 해시를 계산해 직전 항목과 동일하면 중복 추가 방지(`content_hash == _last_hash` 비교). `_create_thumbnail()`로 DIB/PNG 데이터에서 썸네일 생성. **앱 전용 포맷 캡처 제외 화이트리스트**: `extra_formats` 캡처 시 포맷 이름이 `_OLE_CLIPBOARD_FORMATS`(`DataObject`·`Ole Private Data`·`Object Descriptor` 등 OLE `IDataObject` 마샬링 포맷)·`_HWP_NATIVE_FORMATS`(`Hwp Native`·`Hwp_Native_Info`)에 들면 저장하지 않는다. 이 포맷들은 실제 콘텐츠가 아니라 원본 프로세스를 가리키는 참조라 평면 바이트로 복원하면 stale 참조가 되고, 한글(HWP)은 붙여넣기 시 자기 네이티브·OLE 포맷을 평면 포맷(HTML/RTF/텍스트)보다 우선 채택하므로 죽은 참조를 읽어 **한글→한글 붙여넣기가 통째로 실패**한다(증상: 팝업/큐는 정상인데 아무것도 안 붙음). 제외하면 한글이 평면 포맷으로 폴백해 정상 붙여넣기 — 한글 전용 객체(수식·글상자 등) 충실도는 포기하는 트레이드오프. `DOCX Format`은 유지(Word 충실도 보호, 한글에서도 무해 확인). 기존 DB에 캡처돼 있던 옛 항목은 여전히 이 포맷을 품으므로 한글에 붙이면 실패할 수 있고, 수정 후 새로 복사한 항목부터 정상.
- **`paste_queue.py`** — 순차 붙여넣기 큐 관리. **두 가지 큐 초기화 트리거**:
  - **트리거 A — `mark_plain_paste()`**: 일반 Ctrl+V 감지 시 paste_interceptor 콜백이 호출 → 큐를 즉시 비움(`_reset_unlocked`). 시간 무관. 사용자 명시적 시그널이라 즉시 반영.
  - **트리거 B — idle timeout**: `add_item()`에서 마지막 복사(`_last_copy_time`)로부터 `idle_reset_sec`(기본 10초, `DEFAULT_IDLE_RESET_SEC`) 이상 경과한 새 복사면 이전 큐를 버리고 새 항목 1개로 시작. 첫 복사(`_last_copy_time==0`)는 대상 아님.
  - `add_item()` 리셋 조건 = `pointer > 0` OR idle 만료. 그 외엔 누적. 매 호출마다 `_last_copy_time = time.monotonic()` 갱신.
  - 추가 공개 메서드: `set_queue(items, pointer=0)` — 패널에서 특정 항목부터 시작할 때 큐를 직접 교체; `undo_last()` — 포인터를 1 감소시켜 마지막 붙여넣기를 1단계 되돌리기; `clear()` — 큐 및 포인터 초기화(사용자 명시적 호출, 우클릭 큐 해제); `set_idle_reset_sec(sec)` — 런타임 idle 값 변경(설정 다이얼로그에서 호출).
  - 내부 헬퍼 `_reset_unlocked()` — `clear()`와 `mark_plain_paste()`가 공유. 호출자가 이미 `_lock`을 잡은 상태여야 함.
- **`paste_interceptor.py`** — WH_KEYBOARD_LL 저수준 키보드 훅으로 단축키 감지:
  - **Ctrl+Shift+V**: 큐에서 다음 항목 가져오기 → 클립보드 교체 → `_send_clean_key(VK_V)` 호출(현재 눌린 수정키 해제 → Ctrl+V SendInput → 수정키 복원) (suppress). `_send_clean_key`는 수정키 해제 직전·복원 직후 미할당 키 `VK_MASK`(0xE8)를 톡 쳐서, V가 suppress돼 "벌거벗은 Ctrl+Shift"로 오인되어 Windows 입력기 전환(한컴↔MS) 팝업이 뜨는 것을 막는다. 붙여넣기 직전 summary 항목이면 DB에서 전체 데이터 로드(인터셉터 생성 시 주입된 `get_full_item` 콜백 호출 → 실제 DB 메서드는 `db.get_item`).
  - **일반 Ctrl+V 관찰**(suppress 안 함 — fall through로 통과): `ctrl && !shift && !alt && vk==V`일 때 `KBDLLHOOKSTRUCT.flags & LLKHF_INJECTED(0x10)`로 PasteFlow 자체 주입(Ctrl+Shift+V·direct_paste·send_ctrl_v_to)을 제외하고, 물리 키이면 `on_plain_paste()` 콜백 호출. main이 이를 `_bridge.plain_paste` 시그널 emit으로 연결해 메인 스레드 슬롯 `_on_plain_paste()`에서 `queue.mark_plain_paste()` + 패널/트레이 큐 표시 갱신을 한 번에 수행. CLAUDE.md 금지 조항 1(Ctrl+V suppress 금지) 준수. 한계: `Ctrl+Insert`·`Shift+Insert` paste와 AHK 등 매크로 주입 paste는 감지 안 됨.
  - **패널 토글 단축키** (기본 `ctrl+space`, 설정 가능): 패널 열기/닫기. `set_panel_hotkey()`로 런타임 변경 가능. RegisterHotKey 대신 WH_KEYBOARD_LL을 사용하므로 탐색기 등 모든 포그라운드 앱에서 동작.
  - **이미지→경로 단축키** (기본 `ctrl+shift+p`, 설정 가능): suppress. `on_image_to_path` 콜백 호출 → main이 현재 클립보드 이미지를 임시 PNG로 저장하고 절대경로 텍스트로 클립보드 교체 후 포그라운드 창에 자동 Ctrl+V. Claude Code CLI 등 "경로 텍스트"를 첨부로 받는 앱에 한 키로 붙여넣기 위한 진입점. `set_image_to_path_hotkey()`로 런타임 변경 가능.
  - **절대 일반 Ctrl+V 키 이벤트를 차단하지 않음.**
  - 추가 공개 메서드: `direct_paste(item)` — 순차 큐 포인터 변경 없이 즉시 붙여넣기(더블클릭·드래그 경로 사용); `send_ctrl_v_to(hwnd)` — 대상 윈도우 포커스 후 Ctrl+V 전송.
- **`hotkey_manager.py`** — Win32 RegisterHotKey + 히든 윈도우 기반 단축키 유틸. 현재 패널 토글이 interceptor로 이동되어 실제 등록된 단축키 없음. `_SPECIAL_KEY_MAP`(VK 코드 매핑)은 paste_interceptor가 공유 사용.
- **`ocr_engine.py`** — OCR 추상화. `OcrEngine(language="ko").recognize(PIL.Image) → str` 동기 API. `kind` 파라미터로 엔진 선택:
  - `"winrt"`(기본): winocr 패키지로 Windows WinRT OCR 래핑. 4096px 초과 이미지 자동 downscale, RGBA/L 모드 자동 변환, 언어팩 미설치 시 AssertionError → RuntimeError('언어팩') 변환.
  - `"gemini"`: Gemini API 직접 호출 (`_recognize_gemini()`). `base_url` 파라미터가 설정된 경우 OpenAI 호환 게이트웨이/프록시로 자동 분기 (`_recognize_openai_compat()`). `"openai_compat"`는 별도 kind 값이 아님. 모델명은 콜러가 `self.model`로 지정 — 빈 문자열이면 `gemini-3.1-flash-lite`(게이트웨이) / `gemini-2.5-flash`(공식 API) 폴백. **`max_tokens=16384`** 사용: 게이트웨이가 reasoning(thinking) 토큰을 같은 max_tokens 예산에서 차감하므로 작게(2048) 잡으면 thinking을 많이 하는 모델(gemini-2.5-pro·gemini-3.1-pro-preview 등)에서 본문이 0~200자로 잘린다. 16384에서 6종 모델 모두 `finish_reason=stop`으로 정상 종료 확인. 청구는 실제 사용 토큰 기준이라 비용 영향 미세.
  - 모듈 함수 `_normalize_base_url(base_url)` — 사용자가 endpoint 전체 경로(`/chat/completions`, `/models`, `/completions`, `/embeddings`)나 trailing `/`를 붙여 입력해도 자동으로 SDK 표준 base 형태로 보정. 게이트웨이 호출(`_recognize_openai_compat`, `list_gemini_models`) 양쪽에서 사용.
  - 정적 메서드: `is_winrt_available()`, `is_winrt_language_supported(lang)`, `winrt_supported_languages()`, `list_gemini_models(api_key, base_url)` — 게이트웨이 `/models` 또는 `genai.list_models()`에서 `gemini-*` ID만 필터 반환, 설정창 모델 새로고침에 사용.
  - **모델 화이트리스트**(`_VERIFIED_MODELS`) — `(name, tier_rank, on_official, on_gateway)` 튜플. 코드 작성자가 실제로 호출되는 것을 확인한 모델만 등재. 게이트웨이가 광고는 하지만 호출 시 404를 던지는 모델(`gemini-3.1-flash-lite-preview` 사례) 같은 라인업 불일치를 흡수. 모듈 함수 3종: `sort_models_with_whitelist(candidates, backend)` — backend(`"gateway"`/`"official"`) 호환 화이트리스트 ∩ candidates는 tier 오름차순(저렴 우선) verified로, 나머지는 알파벳순 unverified로 분리. `whitelist_model_names(backend)` — 캐시 비어 있는 첫 실행용 초기 콤보 목록(backend 호환 화이트리스트, tier 오름차순). `select_fallback_model(failed_model, backend)` — 안전망 기본은 `_FALLBACK_DEFAULT='gemini-2.5-flash'`(어느 backend에서도 동작), 실패 모델이 그것이면 같은 backend의 다른 화이트리스트 모델 1개.
  - **자동 폴백**(`_call_with_fallback`) — `_recognize_gemini`/`_recognize_openai_compat`가 1차 호출에서 `_is_model_not_found` 휴리스틱(메시지에 `not found`·`model_not_found`·`404 ... model` 포함) 매칭 시 `select_fallback_model`로 1회 재시도. 인스턴스 필드 `last_used_model`(실제 응답 만든 모델) / `last_fallback_from`(원래 시도했다 실패한 모델, 폴백 없으면 `None`)에 결과 기록 → main 워커가 OCR 종료 후 `_bridge.ocr_fallback.emit(failed, used)` → 슬롯이 토스트 `{failed} 없음 → {used}로 폴백` 표시. DB의 `ocr_gemini_model` 자체는 수정하지 않음(잠깐 장애일 수 있음, 사용자 명시 선택 보존).
  - 호출자(main.py)가 `ThreadPoolExecutor(max_workers=1)`로 워커 스레드에서 실행해 UI 블로킹 방지.
  - **`ask(question, context_text="", image_png=None) → str`** — AI 질의(우클릭 "AI에게 질문"). OCR과 **동일한 Gemini 배관을 재사용**한다: `_recognize_gemini`와 같은 2갈래(게이트웨이=`_ask_openai_compat`(OpenAI 호환 chat.completions) / 공식 API=`_ask_google_genai`(google.generativeai))로 갈리고 `_call_with_fallback`(자동 폴백)·`max_tokens=16384`를 그대로 탄다. **`image_png`가 주어지면 질문과 함께 이미지를 멀티모달로 전송**(시각 질의) — 게이트웨이는 OCR(`_openai_compat_call`)에서 검증된 `image_url`(base64 PNG)+text content를, 공식 API는 `generate_content([{mime_type, data}, prompt])`를 쓴다(공식 이미지 경로는 신규·실호출 검증 권장). 이미지가 없으면 텍스트 컨텍스트+질문만 보낸다. 모듈 함수 `_ask_prompt(question, context_text)`가 컨텍스트가 있으면 클립보드 내용을 감싸 질문에 끼우고, 없으면 질문만 그대로 보낸다(자유 질문·이미지 질의). 동기 호출이라 호출자가 워커 스레드에서 실행해야 한다.

### UI 컴포넌트 (`pasteflow/ui/`)

- **`panel.py`** — 고정 섹션 + 히스토리 패널 (검색 기능 없음 — 의도적으로 제거).
  - 항목 **단일 좌클릭**: 선택(하이라이트)만. Ctrl+클릭/Shift+클릭으로 다중 선택.
  - 항목 **더블클릭**: `paste_item_requested` → 즉시 붙여넣기.
  - **우클릭 컨텍스트 메뉴**: "큐에 추가"(`queue_select_requested`) / 고정·해제 / 복사 / 수정(텍스트만) / AI에게 질문(텍스트·이미지 모두 — `ai_query_requested` emit. 텍스트는 텍스트를 컨텍스트로, 이미지는 이미지를 멀티모달로 질의) / 텍스트 추출(OCR)(이미지만 — `ocr_item_requested` emit) / 파일로 저장 후 경로 복사(이미지만 — `copy_image_as_path_requested` emit, main이 `%TEMP%\PasteFlow\`에 PNG 저장 후 절대경로를 클립보드 텍스트로 복사 + 저장한 PNG 썸네일 토스트 — 이미지→경로 단축키와 동일하게 `ToastNotification(..., icon="", image_path=saved_path)`로 "복사한 게 의도한 이미지 맞나" 시각 확인) / 삭제 / 미리보기(이미지→`preview_image_requested` emit, 텍스트→`preview_text_requested` emit). 둘 다 main에서 받아 `ImagePreviewPopup.open_new(item, ...)` / `TextPreviewPopup.open_new(item, ...)` 호출 (동일 패턴 — 둘 다 `ClipboardItem`을 인자로 받음).
  - 항목 **드래그 → 외부 앱**: fake drag(DragCopyCursor) 방식으로 붙여넣기. 마우스업 시점에 `QApplication.keyboardModifiers() & AltModifier`로 Alt 눌림 여부를 캡처해 `drag_to_app_requested(item_id, cursor_pos, alt_held)` 시그널에 같이 전달.
    - **Alt+드래그 + 이미지**: 임시 PNG로 저장(`_save_image_to_drop_temp()`) 후 절대경로 텍스트를 클립보드에 넣고 `_activate_and_send_ctrl_v(root_hwnd, sender=interceptor._release_modifiers_and_send_ctrl_v)`로 SendInput(Ctrl+V). **마우스 업 시점에 사용자가 Alt를 여전히 누르고 있으므로** 기본 `_send_ctrl_v_plain`(수정키 해제 없음)을 쓰면 OS가 `Ctrl+Alt+V`로 오인해 붙여넣기가 일어나지 않는다 — `_release_modifiers_and_send_ctrl_v`가 눌린 Alt/Ctrl/Shift를 해제 후 Ctrl+V를 주입하고 Alt를 복원한다(Ctrl+Shift+P 단축키가 `_send_clean_key`를 쓰는 것과 동일한 이유). Windows Terminal의 claude CLI 등 "파일 경로 텍스트"를 첨부로 받는 앱 대응. WM_PASTE는 터미널이 무시하므로 무조건 SendInput 경로로 통일. 저장 실패 시 토스트 에러 + 종료(폴백 없음).
    - **이미지 + Explorer(`CabinetWClass`) / 바탕화면(`Progman`, `WorkerW`)**: PNG 파일로 저장(`_save_image_to_folder()`). 서브폴더 아이콘 위에 드롭 시 해당 폴더에 저장.
    - **Win32/WinUI3 앱**: `WM_PASTE`.
    - **Electron/Chromium 앱**: `AttachThreadInput+SendInput`.
  - 고정 항목 **드래그 → 재정렬**: fake drag 방식 (QDrag 미사용). 커서 아래 고정 항목 하이라이트 후 마우스 업 시 순서 교환.
  - 히스토리 항목 **드래그 → 재정렬**: `history_reorder_requested` 시그널 → main이 DB 업데이트.
  - **`update_queue_highlight()`**: 위젯 재생성 없이 색상만 업데이트하는 빠른 경로 (큐 상태 변경 시 사용).
  - **`show_near_cursor()`**: 마우스 커서 우하단 +16px에 패널 표시. 화면 경계 초과 시 반전. 단축키/트레이로 패널을 열 때 사용.
  - **자동 닫기 토글 버튼(📌)**: 헤더 우측에 배치. ctypes `SetWindowPos(HWND_TOPMOST/NOTOPMOST)`로 TOPMOST 플래그만 변경(창 재생성·깜빡임 없음). 기본값: 자동 닫기 OFF(항상 위에 ON). `_auto_close` 플래그로 관리 — `False`이면 포커스를 잃어도 자동 닫히지 않음(`changeEvent` 조건: `not self._auto_close`). DB `settings`에 저장. `set_auto_close(value)` 메서드로 외부에서 설정.
  - **`panel_hidden` 시그널**: `hideEvent`에서 emit. (패널 자동 팝업이 제거되어 현재 main에서 소비하지 않음 — 향후 훅 용도로 시그널만 유지.)
  - **`auto_close_changed(bool)` 시그널**: 버튼 클릭 시 emit → main이 DB 저장.
  - **각 항목(PanelItemWidget)은 최대 5줄까지 표시**한다. 높이는 `label_h = visual_lines * fm.lineSpacing() + 8`, `widget_h = label_h + 12` 공식으로 계산. 창 리사이즈 시 `resizeEvent` + `_adjust_text_height()`로 동적 재계산. **레이블과 위젯 모두에 `setFixedHeight`를 명시적으로 설정**해야 한다(위젯에만 설정하면 레이블 높이가 따라오지 않아 클리핑 발생).
- **`image_preview.py`** — 이미지 미리보기 팝업. 다중 창 동시 표시 지원(`open_new(item, panel_geom, native=False)`로 생성 — text_preview와 동일하게 `ClipboardItem` 전체를 받음). 휠 줌, 드래그 이동, 더블클릭 닫기, ESC 닫기. 커서가 있는 모니터에 배치(`screenAt()`). **`native=True`**(핀 Alt+F3 경로가 사용)면 초기 줌을 원본 픽셀 1:1로 띄운다(화면 초과 시만 축소) — "캡처한 크기 그대로". 일반 미리보기는 `PREVIEW_MAX_W/H`(640×480)에 맞춰 축소. **cascade 위치**: `open_new`의 cascade offset은 "총 창 수"가 아니라 "새 앵커(커서/패널) 근처(`_CASCADE_NEAR`)에 이미 떠 있는 창 수"에만 비례 — 핀은 커서를 앵커로 쓰므로 커서를 옮겨 새로 핀하면 커서 바로 옆에 뜨고(드리프트 없음), 같은 자리 연속 핀만 어긋난다(겹침 방지). **우클릭 메뉴**: `복사` / `텍스트 추출(OCR)` / `AI에게 질문` / `파일로 저장 후 경로 복사` / `주석 편집` / `닫기`. 시그널 `copy_requested(ClipboardItem)` → main의 `_on_copy_item`, `ocr_requested(ClipboardItem)` → main의 `_on_ocr_image_item`, `ai_requested(ClipboardItem)` → main의 `_ai_query_for_item`(이미지 멀티모달 질의), `copy_as_path_requested(ClipboardItem)` → main의 `_copy_image_as_path_for_item`. **이 메뉴는 패널에서 연 미리보기(`_on_preview_image`)와 화면 핀(Alt+F3 `_on_pin_hotkey`) 양쪽에서 동일 연결** — 핀 항목은 DB id가 없으므로 main 핸들러는 id 기반 래퍼와 `ClipboardItem` 기반 코어(`_ai_query_for_item`/`_copy_image_as_path_for_item`)로 분리돼 있다. 메뉴는 `self._item`(원본)을 대상으로 하므로 주석을 그려도 평탄화 전 원본이 대상(복사·OCR과 동일 일관성). **활성/비활성 테두리**: 활성(보고 있는 창)=코랄(`PEACH`, 주인공), 비활성=중립 회색(`SURFACE2`, 존재만 표시·안 튐) — QSS 동적 프로퍼티가 런타임에 재반영 안 돼 `_apply_active_style(active)`에서 스타일시트 직접 교체. **인라인 주석 편집**: `image_annotator._EditorMixin`을 상속해 같은 창에서 Space로 편집 모드 진입(main의 `_on_preview_image`는 이미 열린 창이면 닫지 않고 `toggle_edit_mode()` 호출). 완료 시 `annotated_copy_requested(bytes)`(→ main `_on_annotation_copy`: 클립보드+히스토리 저장) / `export_file_requested(bytes)`(→ main `_on_annotation_export`: PNG 파일 저장) emit.
- **`image_annotator.py`** — 이미지 주석 편집기(CleanShot/Snipaste 스타일). `ImagePreviewPopup`이 상속하는 `_EditorMixin`(도구·색·두께·undo·스포이드) + `_AnnotatorView`(QGraphicsView, 그리기 인터랙션) + 도형 아이템(`_RectItem`/`_EllipseItem`/`_LineItem`/`_PathItem`/`_ArrowItem`/`_BadgeItem`/`_TextItem`, 전부 `_HandleResizeMixin`)로 구성. **도구**: 선택(V)·네모(R)·원(E)·선(L)·화살표(A)·펜(P)·텍스트(T)·번호(C). `QGraphicsScene` 기반이라 줌하면 주석이 이미지와 함께 스케일됨. **그리기 규칙**: 빈 영역 드래그로 생성(시작점→놓은 점 이동량<4px면 클릭으로 보고 폐기+선택 해제), 그린 직후 자동 선택(펜은 제외 — 연속 그리기). 펜은 기존 주석 위에서도 항상 그림(펜 선의 선택·이동은 V 도구로). Shift=정사각/정원/45° 스냅. **편집 단축키**: 화살표키로 선택 항목 이동(기본 10px·Shift/Ctrl=1px), Ctrl+A 전체 선택, Ctrl+C/V 주석 내부 복제(템플릿 clone, +12px cascade), Ctrl+Z undo(빈 텍스트 등 무의미 항목은 건너뜀). 휠(가운데)클릭 드래그=창 이동(`_win_drag_*` 재사용, 편집/뷰어 공용). **크기조절 핸들**: 선택 시 우하단 파란 사각, 드래그로 균일 스케일 — **선택(V) 도구일 때만 표시**(`_handle_active`가 `_owner_tool()` 확인). **텍스트**: 작성 후 텍스트 도구 유지(연속 배치), Ctrl+Enter로 마무리, 빈 텍스트는 focusOut 시 정리. 새 텍스트 시작 시 다른 선택 해제, 크기 조절은 편집 중 텍스트가 있으면 그것만(`_font_size_targets`). T 활성 시 T 아래 **수평 옵션 바**(`_text_opts_bar` — 배경 스와치 투명/반투명검정/흰/회/검 직접 선택 + 글자 크기 스테퍼). **번호(badge)**: C 활성 시 C 아래 크기 스테퍼(`_SizeStepper`, 값 유지→다음 번호 같은 크기·선택 번호에도 적용), 붙여넣기 시 새 번호 부여. 스테퍼 ▾/▴는 길게 누르면 연속 증감(auto-repeat). **스포이드**: 화면 픽셀 색 따오기(`_ColorLoupe` 미리보기, ctypes GetPixel). 씬→PNG 평탄화는 `flatten_scene_to_png(scene)`. main과는 시그널만 주고받고 실제 클립보드 복사/파일 저장은 main이 처리.
- **`text_preview.py`** — 텍스트 미리보기 팝업. 다중 창 동시 표시 지원(`open_new(item, panel_geom, editable=True, markdown=False, center=False)` — `ClipboardItem` 전체를 받아 우클릭 메뉴의 복사·수정에 활용). **`center=True`**면 `panel_geom` 옆이 아니라 `panel_geom`이 속한 **모니터 정중앙**에 띄운다(`show_preview`의 center 분기 — AI 답변 전용, `_ai_anchor`가 가리키는 커서 모니터 한복판). **`editable=False`**면 우클릭 "수정" 메뉴를 숨긴다 — AI 답변처럼 DB에 없는 임시 항목(id 없음)은 수정·저장 경로가 무력하므로 메뉴 자체를 제거. 인스턴스는 `_instances` 클래스 목록이 닫힐 때까지 참조를 유지(close 시 정리)하므로 별도 보관 없이 안전.
  - **마크다운 모드(`markdown=True`, AI 답변 전용)**: 평문용 `QPlainTextEdit` 대신 `QTextEdit`+`setMarkdown()`으로 서식 렌더링. 일반 미리보기는 원문 확인 용도라 평문 유지.
    - **요소별 색**(`_collect_syntax_spans` 1회 수집 → `_apply_marks`가 재적용): `setMarkdown` **직후(서식 변형 전)** Qt가 남긴 서식을 읽어 스팬(위치·색·밑줄여부)을 1회만 수집해 `_syntax_spans`에 저장하고, 형광펜 토글마다 그 위치로 색을 재적용한다. 색은 앱 전역 테마와 분리한 전용 상수(`_MD_HEADING/_MD_CODE/_MD_BOLD/_MD_ITALIC`) — 제목(`headingLevel`)=파랑, 굵게(`fontWeight≥700`)=코랄, **코드(`fontFixedPitch`)=파랑(`#38bdf8`)+밑줄+볼드**, 기울임=초록. 코드는 모노스페이스로 튀지 않게 본문 폰트(`_FONT_FAMILY`=맑은 고딕)로 통일(`setFontFixedPitch(False)`+`setFontFamily`). **수집을 1회만 하는 이유**: 코드 색칠이 `fixedPitch`를 끄므로 매번 재탐지하면 두 번째부터 코드를 못 찾아 형광펜 시 코드색이 증발한다(텍스트 불변이라 position 안정). 배경색은 형광펜 전용으로 비움(채널 분리). `setDefaultStyleSheet`는 마크다운에 안 먹어 이 방식 사용.
    - **형광펜**(좌드래그 선택→배경+빨강 글자+밑줄 토글): `TextSelectableByMouse`로 좌드래그=텍스트 선택→릴리스 시 `_HL_BG`(어두운 적갈색 칩 `#281414`)+`_HL_FG`(빨강 `#ff5a5a`)+빨강 밑줄 토글, 마크 클릭=해제, 우클릭 "형광펜 지우기"=전체 해제. 창 이동은 **가운데(휠)클릭 드래그**(annotator와 동일). 마크는 문서 position 범위로 보관, `_apply_marks`가 매번 문자서식 초기화→`_syntax_spans` 재적용→형광펜 재적용(토글 시 원래 색 복원). **복사 직렬화**(`_marked_markdown`): Qt `toMarkdown()`이 굵게·제목조차 못 보존(Qt 6.10)하므로 라운드트립 대신 **원본 소스에 백틱만 삽입**(렌더 position→소스 offset 두 포인터 정렬)해 모델 마크다운 100% 보존 + 형광펜만 `` `text` ``.
    - **마크다운 전처리**(`_fix_markdown_emphasis`): ① **따옴표볼드** — `**'X'**` 뒤에 공백 없이 글자가 오면 닫는 `**`가 flanking 규칙상 인정 안 돼 볼드가 풀린다(스펙 동작). `**'X'**`→`'**X**'`로 따옴표를 볼드 바깥에 옮겨 정상 렌더. ② **볼드+백틱 중첩 해소**(`_CODE_BOLD_RE`/`_BOLD_CODE_RE`) — Qt 마크다운은 볼드와 인라인코드의 중첩(`` `**X**` ``·`**` + 백틱)을 렌더 못 해 `**`가 글자로 노출된다(실측 확인). 중첩을 순수 코드(`` `X` ``)로 풀고, 코드 스팬 자체를 볼드로 그려(`_apply_marks`) '볼드+백틱'을 마크다운 문법 대신 포맷으로 살린다.
    - **리스트 불릿 통일**(`_set_list_bullets`): 모든 마크다운 리스트를 `•`(ListDisc)로. **문단 간격**(`_apply_block_spacing`): 줄간격 `_MD_LINE_HEIGHT`(135%)+문단 여백 `_MD_BLOCK_MARGIN`(7px), 측정용 tmp 문서에도 동일 적용해 높이 정확.
    - **고정 폰트+스크롤**: 폰트 `_MD_FONT_SIZE`(16px) 고정·폭 `_MD_INITIAL_MAX_W`(600). 휠=스크롤(글자 크기 일정), Ctrl+휠=줌. 길면 화면 80%(`_MD_MAX_H_FRAC`)에서 세로 스크롤(`ScrollBarAsNeeded`, 폭 `_SCROLLBAR_W` 보정).
  - **표시 위젯**: `QPlainTextEdit` (QLabel+QScrollArea 아님). `setWordWrapMode(WrapAtWordBoundaryOrAnywhere)`로 공백 없는 긴 URL/해시도 문자 단위 wrap. QLabel은 word-boundary 없는 토큰을 절대 잘라주지 않아 폐기.
  - **"한 번에 다 보임" 정책**: 양쪽 스크롤바 영구 차단(`ScrollBarAlwaysOff`) + editor `FocusPolicy.NoFocus` — QPlainTextEdit 내부 가짜 vScroll(`vScrollMax≥1`)이 키보드(스페이스/PageDown)로 노출돼 빈 영역이 보이던 문제 차단. width는 `PREVIEW_INITIAL_MAX_W * scale` (zoom과 비례 확장, 화면 너비 cap), height는 화면 한계까지 자유 확장하여 모든 줄 표시.
  - **`LineWrapMode` 동적 전환**: 자연 너비가 popup width cap 안에 들어오면 `NoWrap` 강제 (QPlainTextEdit의 viewport에 내부 padding이 있어 textWidth를 맞춰도 sub-pixel 차이로 wrap이 새는 경우 발생 — 휠 줌마다 1↔2줄 깜빡임의 원인). 초과 시에만 `WidgetWidth` wrap.
  - **크기 계산**: 자연 너비/높이 측정에 독립 `QTextDocument` 사용 — QPlainTextEdit의 자체 document는 lazy layout이라 `setTextWidth` 직후 `size()`가 갱신 안 됨. `math.ceil(idealWidth())`로 sub-pixel 부족분 보정.
  - **전체 창 드래그**: 텍스트 부분 선택 미지원(`NoTextInteraction`). 부분 텍스트가 필요하면 우클릭 메뉴 `수정`으로 편집 다이얼로그에서 자연스럽게 선택. viewport + popup 본체 양쪽 모두에서 left-drag 이동 처리.
  - **우클릭 메뉴**: `전체 복사` / `수정` / `닫기` (패널 메뉴와 동일 명칭). 시그널 `copy_requested(ClipboardItem)` → main의 `_on_copy_item`, `edit_requested(item_id)` → main의 `_on_preview_edit_request`(`EditItemDialog` 띄우고 `_on_edit_item`으로 위임). QPlainTextEdit 기본 우클릭 메뉴는 `setContextMenuPolicy(NoContextMenu)`로 차단.
  - **활성/비활성 테두리**: 이미지 미리보기와 동일 정책(활성=코랄, 비활성=중립 회색). 프레임리스 최상위 위젯에 건 테두리가 자식에 가려지는 문제를 피하기 위해 내부 `popup_container`에 적용.
  - **`_clamp_to_screen(avail)`**: resize 후 popup이 화면 밖으로 나가면 안쪽으로 끌어들임.
- **`toast.py`** — 토스트 알림. `_ToastStack` 싱글턴이 활성 토스트를 코너 기준 위로 스택(최신=맨 아래)하고 닫힐 때 재정렬, 최대 5개 동시 표시(초과 시 가장 오래된 것 즉시 제거). **스택 모니터**: 예전엔 항상 주 모니터 우하단에 깔았으나, 이제 스택이 비었다가 첫 토스트가 등록되는 순간의 **커서 모니터**에 고정(`_ToastStack._screen`) — 표시 중 커서가 다른 모니터로 가도 떠 있는 토스트가 튀지 않고, 보조 모니터 작업 시 시선을 돌릴 필요가 없다. `ToastNotification(message, icon, badge, badge_position, image_path, anchor, center)` — `badge_position`은 `"leading"`(아이콘과 본문 사이) 또는 `"trailing"`(본문 뒤, 기본). `image_path`가 주어지면 아이콘과 본문 사이에 그 PNG 파일의 썸네일(최대 96px, `QPixmap(path)` 직접 로드·로드 실패 시 조용히 생략)을 삽입 — 이미지→경로 붙여넣기 시 "의도한 이미지가 맞나" 시각 확인용. `icon`이 빈 문자열이면 아이콘 라벨 자체를 생략(썸네일이 카테고리 구분을 대신). `show_copy_toast(item, queue_count)`는 누적 큐 카운트를 `Q{n}` 형태 badge로 본문 앞(`leading`)에 배치해 2초간 표시. `reserve_bottom(px)`(HUD 등 우하단 위젯을 위해 스택 하단 여백 확보). 시작 알림·복사 알림 등 일반 토스트가 이 스택을 공유한다. **커서 앵커 모드**(`anchor=QPoint`): 우하단 스택 대신 그 지점을 기준으로 배치하고 `WindowTransparentForInput`로 클릭을 아래 앱에 통과시킨다(작업 방해 0). `center=True`면 앵커 옆(+16px·경계 반전) 대신 **앵커가 속한 모니터 정중앙**에 배치(`_place_anchored`). OCR·AI 진행/결과 칩이 이 모드를 쓴다(main `_start_cursor_progress`). **지속형 토스트**(`duration_ms=0`): 자동 fade-out 없이 호출자가 `dismiss()`로 닫는 모드 + `set_message(text)`로 본문 갱신(앵커 모드면 폭 변화 시 재중심) — AI 질의·OCR처럼 끝나는 시점을 미리 알 수 없는 작업의 진행 표시용. **OCR 토스트**: icon `🔤`로 통일, 본문에서 `OCR:` prefix는 제거(아이콘이 카테고리 구분을 담당). 모든 OCR 진입점(진행 중·결과·각종 에러)이 동일 아이콘을 사용해야 일관됨.
- **`paste_hud.py`** — 순차 붙여넣기 진행 HUD. 우하단 비활성 창(`WA_ShowWithoutActivating` — 포커스 미탈취), 큐 항목 목록을 `✓`(완료·흐림)·`▶`(다음·강조)·`·`(대기)로 표시하고 헤더에 `순차 붙여넣기 n/total`. 단일 인스턴스 재사용 — `show_progress(items, pointer)`로 표시·갱신, `finish()`로 1.2초 후 fade-out. 큐 10개 초과 시 "외 N개"로 축약. 표시 중 `toast.reserve_bottom()`을 호출해 복사 토스트가 HUD 위로 쌓이게 한다.
- **`tray.py`** — 시스템 트레이. 좌클릭 시 `panel_toggle_requested` 시그널 emit → main이 패널 토글.
- **`settings_dialog.py`** — 단축키 커스터마이징(패널 토글, OCR, 이미지→경로 — `KEY_IMAGE_TO_PATH_HOTKEY` = `hotkey_image_to_path`, 기본 `ctrl+shift+p`, 화면 핀 — `KEY_PIN_IMAGE_HOTKEY` = `hotkey_pin_image`, 기본 `alt+f3`, 영역 캡처 — `KEY_CAPTURE_HOTKEY` = `hotkey_capture`, 기본 `alt+f2`), 캡처 저장 폴더(`KEY_CAPTURE_FOLDER` = `capture_save_folder`, QLineEdit + 찾아보기 `QFileDialog`), 히스토리 제한, 순차 큐 자동 초기화 시간(`KEY_QUEUE_IDLE_RESET` = `queue_idle_reset_sec`, QSpinBox 1~3600초·기본 10초), 자동 시작, 복사 알림 on/off(`notify_on_copy`) 설정. **그룹 구성/배치**: `기본 단축키 (고정)`(복사/붙여넣기/순차붙여넣기 — 변경 불가, 맨 위) → `기능 단축키 (변경 가능)` → `OCR (화면 텍스트 인식)`(OCR 단축키·엔진·인식 언어) → `AI 연동 (Gemini API)` → `일반`. **AI(Gemini) API 그룹은 OCR 엔진과 무관하게 항상 표시** — AI 답변(우클릭 "AI에게 질문")이 OCR 엔진 선택과 무관하게 늘 Gemini를 쓰므로(WinRT OCR이어도 키 필요). `_on_engine_changed`는 '인식 언어'(WinRT 전용) 노출만 토글하고, `_on_save`는 Gemini 키/모델/캐시를 항상 저장한다. **레이아웃**: 전체 콘텐츠를 `QScrollArea`(`_finalize_size`가 창 크기를 콘텐츠+화면에 맞춰 산정·넘으면 스크롤)로 감싸 고정 크기가 콘텐츠를 압박해 word-wrap 라벨 heightForWidth가 진동하던 **드래그 떨림**과 작은 화면 오버플로를 해결. 버튼(취소/저장)은 스크롤 밖. OCR 언어 콤보는 `OcrEngine.winrt_supported_languages()`로 동적 채움(winocr 미설치 시 기본 목록 폴백). Gemini 모델 콤보 옆 ↻ **새로고침 버튼**(Qt 표준 아이콘 `SP_BrowserReload` — 폰트 무관 보장)으로 `OcrEngine.list_gemini_models()` 호출 → 결과를 콤보에 반영하고 현재 backend의 `KEY_OCR_GEMINI_MODEL_CACHE_OFFICIAL` 또는 `_GATEWAY`(JSON list)에 저장 — backend별로 모델 캐시 분리 → 다음 실행 시 캐시 로드. 네트워크 호출은 `threading.Thread` + 내부 시그널 `_models_fetched(list, str)`로 UI 스레드 안전 통신. **콤보 정렬**은 `ocr_engine.sort_models_with_whitelist(cached, backend)`에 위임. backend는 새로 추가된 **API 백엔드 콤보**(`_current_backend()` → "공식 Google AI Studio"/"학교 게이트웨이")로 사용자가 명시 선택. 콤보 전환 시 `_on_backend_changed()`가 이전 backend 입력값을 `self._settings`에 stash하고 새 backend의 키·URL·모델·캐시를 입력란에 다시 채워, 두 backend를 자유 전환해도 양쪽 값이 모두 보존된다. base_url 입력란은 gateway일 때만 노출. 저장 시 `_on_save`는 활성·비활성 backend의 키/모델/캐시를 모두 함께 emit해 한쪽이 사라지지 않게 한다. 결과를 `_fill_model_combo(verified, unverified)`가: verified는 상단(tier 오름차순), 검증/미검증 둘 다 있으면 `insertSeparator`로 구분선, unverified는 하단에 회색(`ForegroundRole=COLORS['subtext0']`) + 툴팁 "PasteFlow가 검증하지 않은 모델 — 게이트웨이가 광고하지만 호출 실패 가능". 모든 항목엔 전체 모델명 툴팁을 달고, `_adjust_model_popup_width()`로 드롭다운 팝업(view) 최소 폭을 최장 모델명에 맞춰 넓혀(콤보 본체·설정창 폭은 불변) 공통 접두사를 공유하는 긴 이름의 가운데 생략을 막는다. 캐시가 비어 있는 첫 실행은 `whitelist_model_names(backend)`로 초기 채움(unverified는 빈 리스트). `self._verified_models`에 verified 목록을 보관해 `_update_model_hint()`가 `💡 가장 저렴: {verified[0]}`(또는 검증 모델 없으면 `(검증된 모델 없음 — ↻ 새로고침을 시도하세요)`) 안내. ↻ 결과 머지(`_on_models_fetched`)도 동일 정렬 적용 후 캐시 갱신.
- **`ocr_overlay.py`** — 모니터별 분리 오버레이. `OcrOverlay`는 매니저(QObject 베이스, QWidget 아님)이고 실제 위젯은 `_ScreenOverlay`로 각 QScreen마다 1개씩 생성. `start()` 호출 시 모니터 수만큼 `_ScreenOverlay`를 만들고 각각 자기 화면을 `screen.grabWindow(0, 0, 0, w, h)`로 캡처해 표시. 한 모니터에서 드래그 시작되면 `drag_started` 시그널로 매니저가 다른 오버레이를 `deactivate()`(마스크만 표시·입력 차단). ESC/우클릭은 어느 오버레이에서든 전체 취소. **다중 DPI 모니터 대응**: 가상 데스크톱 전체를 단일 위젯으로 덮으면 Qt 백킹 스토어 DPR이 하나로 고정돼, DPR이 다른 모니터에 진입할 때 좌표·크기가 어긋나 고DPI 노트북 화면이 좌상단 일부로 축소되는 증상이 발생한다. 모니터별 분리 위젯 + `setScreen()` 명시 바인딩으로 Qt가 모니터별 DPR을 독립 처리하므로 문제 자체가 발생하지 않는다. 공개 API(`region_captured(QPixmap)`, `cancelled()`, `start()`)는 호출부 변경 없이 유지.

### 단축키 체계

| 단축키 | 동작 | 감지 방식 |
|--------|------|-----------|
| Ctrl+Shift+V | 순차 붙여넣기 (suppress) | WH_KEYBOARD_LL (paste_interceptor) |
| ctrl+space *(기본값, 설정 가능)* | 패널 토글 (suppress) | WH_KEYBOARD_LL (paste_interceptor) |
| ctrl+shift+s *(기본값, 설정 가능)* | OCR 영역 선택 시작 (suppress) | WH_KEYBOARD_LL (paste_interceptor) |
| ctrl+shift+p *(기본값, 설정 가능)* | 클립보드 이미지를 임시 PNG로 저장 후 경로 텍스트로 자동 Ctrl+V (suppress) | WH_KEYBOARD_LL (paste_interceptor) |
| alt+f3 *(기본값, 설정 가능)* | 클립보드 이미지/텍스트를 화면에 핀(떠 있는 창)으로 띄우기 (suppress) | WH_KEYBOARD_LL (paste_interceptor) |
| alt+f2 *(기본값, 설정 가능)* | 영역 캡처 → 클립보드(DIB)+파일 저장 (suppress) | WH_KEYBOARD_LL (paste_interceptor) |
| 트레이 좌클릭 | 패널 토글 | Qt 이벤트 |

> ⚠️ `Alt+1~9` 직접 붙여넣기, `Ctrl+Shift+X` 큐 초기화, `Ctrl+Shift+Z` 실수 복구는 **의도적으로 제거**됨.

### 화면 핀 · 영역 캡처 (Snipaste식, v1.8.0~)

Snipaste를 대체하는 캡처 기능군. 단축키 추가는 모두 기존 패턴(`set_*_hotkey` + 훅 감지 블록 + 브리지 시그널 + 설정 배선) 복제.

- **화면 핀 (Alt+F3)** — `main._on_pin_hotkey`: 클립보드 이미지를 읽어(`_read_image_from_clipboard`) `ImagePreviewPopup.open_new(..., native=True)`로 화면에 띄움(원본 1:1 크기, 화면 초과 시만 축소 / 다중 창·ESC 닫기·Space 주석 편집은 image_preview가 이미 제공). 이미지가 없으면 클립보드 **텍스트를 흰 배경 PNG로 렌더**(`_render_text_to_png` — 맑은 고딕 18px, 워드랩)해서 띄움(텍스트도 주석 가능 = 사실상 이미지화). 커서 우측에 배치. **`image_preview._bg_item.setTransformationMode(Smooth)`**: QGraphicsPixmapItem 기본 Fast(nearest)가 뷰의 SmoothPixmapTransform을 덮어써 비정수 배율에서 이미지·텍스트가 거칠게 보이던 것 수정(모든 미리보기 공통 개선).
- **영역 캡처 (Alt+F2)** — `main._on_capture_region`: 마그네틱 캡처 오버레이(`_capture_overlay` = `CaptureOverlay`)가 선택 영역 QPixmap을 emit → **DIB**(`_qpixmap_to_dib` — 24bpp BI_RGB, 붙여넣기 호환 최광)로 변환 → `interceptor._set_clipboard`+`_persist_clipboard_item`(OCR 결과와 동일 무중복 경로: 클립보드+히스토리+큐) → `_save_image_to_folder`(설정 `capture_save_folder`, 기본 `_default_capture_folder()` = `<Pictures>\PasteFlow`, 없으면 생성) → 썸네일 토스트(`📷`). 설정에 캡처 단축키 행 + 저장 폴더 행(`QFileDialog`).
- **마그네틱 캡처 (완료, v1.9.0)** — `uia.py`(`rect_at(x,y)` = UIA `ElementFromPoint`로 커서 아래 요소의 물리픽셀 사각형, comtypes·CUIAutomation 1회 생성 재사용) + `ui/capture_overlay.py`(`Qt.WindowType.WindowTransparentForInput` 클릭-통과 오버레이 — ElementFromPoint가 아래 실제 창을 짚도록. QTimer ~16ms(~60fps)로 `GetCursorPos`→`uia.rect_at`→물리→논리 변환(`MonitorFromPoint` 물리원점+QScreen DPR)→요소 하이라이트(무거운 hover UIA hit-test는 `_UIA_MIN_INTERVAL`≈30ms로 스로틀해 과호출 방지), ESC 폴링). **`_capture_overlay`로 main에 연결**(Alt+F2). 동작:
  - **요소 클릭 캡처(3b)**: WH_MOUSE_LL 마우스 훅을 캡처 시작 시 전용 데몬 스레드에 설치(`paste_interceptor`의 키보드 훅 패턴 복제)·종료 시 해제. **콜백은 trivial**(좌/우 버튼 flag만 세팅 후 suppress, 실제 UIA·crop은 메인 스레드 QTimer가 flag를 보고 처리 — 콜백이 무거우면 시스템 마우스가 끊김). 좌클릭=현재 하이라이트 요소 캡처, 우클릭=취소. **좌·우 모두 down/up을 suppress하고 동작은 up에서 트리거** — down에서 취소/캡처하면 훅이 up 전에 풀려 up이 아래 앱으로 새어(우클릭 시 컨텍스트 메뉴가 뜸) 버그가 됨.
  - **자유드래그 폴백(3c)**: 좌버튼 누른 채 `_DRAG_THRESHOLD`(4px) 이상 이동하면 클릭 대신 자유 사각형(시작점~현재 커서, 요소 무시). 안 움직이고 떼면 요소 클릭. 빈 영역을 드래그 없이 클릭하면 무시(`_capture(None)`).
  - **크로스 모니터 합성(`_crop_global`)**: 선택 영역이 단일 모니터면 그 화면 스크린샷에서 바로 crop, 여러 모니터에 걸치면 가장 높은 DPR을 타깃으로 빈 캔버스에 각 화면 조각을 제 위치에 그려 합성(배율 다른 조각은 타깃 DPR로 스케일 — 기하학 정확, 저DPI 조각은 약간 소프트). 100/125/150% 트리플 모니터 실조건 검증 완료.
  - 결과 QPixmap → `region_captured` → 기존 `_on_capture_region`(DIB+클립보드+히스토리+파일+토스트, 무수정).
  - **UX 다듬기 (v1.10.1)**: ① **십자 커서** — 캡처 진입 시 시스템 커서를 십자(`IDC_CROSS`)로 전역 교체(`SetSystemCursor`로 호버 시 자주 뜨는 `OCR_*` 슬롯 전부 덮어 텍스트필드·링크 위에서도 십자 유지)·종료 시 `SystemParametersInfoW(SPI_SETCURSORS)`로 복원. 클릭-통과(`WindowTransparentForInput`) 오버레이는 `setCursor`가 안 먹혀(커서는 아래 창이 정함) 시스템 커서 교체로 우회하며, `atexit` 등록 + idempotent 가드로 캡처 중 크래시 시 십자 잔존을 방지. ② **드래그 부드러움** — 어두운 마스크를 입힌 딤 스크린샷(`_dimmed`)을 `prepare()`에서 1회 사전합성하고, 하이라이트 변경 시 `set_highlight_global`이 이전∪현재 영역(+테두리 여유 `_INVAL_MARGIN`)만 `update(dirty)`로 부분 repaint → 프레임당 전체화면 알파합성 제거. `_POLL_MS`=16(~60fps)과 결합해 Snipaste급 드래그.
  - **미구현(선택)**: 휠/방향키로 부모 요소 확장(plan의 3c 선택 항목 — 가치 보고 후 결정). **엣지**: 배율 다른 두 모니터에 걸친 자유드래그는 합성으로 정상이나, 드래그 시작점의 논리 좌표 샘플은 첫 tick 기준(±16ms). 상세 설계는 plan 파일 `tidy-doodling-taco.md`.

### 순차 붙여넣기 핵심 동작 (가장 중요)

```
사용자 복사 → WM_CLIPBOARDUPDATE → ClipboardMonitor
  → database.save(item)
  → paste_queue.add_item(item)
       리셋 조건: (pointer>0) OR (직전 복사로부터 idle_reset_sec 경과 = idle 만료)
       그 외에는 누적. 매번 _last_copy_time = now, 포인터 0
  → panel이 열려 있으면 갱신 · 복사 토스트 표시(notify_on_copy 시) · 진행 HUD 정리

사용자 Ctrl+Shift+V (키다운) → PasteInterceptor._on_ctrl_shift_v()
  → paste_queue.get_next()
  → 큐 소진이면 → 아무것도 안 함 (suppress만, OS 기본 동작 없음)
  → 항목 있으면 → (필요 시 DB에서 전체 데이터 로드) → win32clipboard로 클립보드 교체
                → Ctrl+V SendInput 주입 → OS 기본 Ctrl+V가 교체된 내용 붙여넣기
  → _on_paste_from_hook() → paste_happened 시그널 emit
                           → pointer>=total이면 paste_queue_done 시그널 emit

사용자 일반 Ctrl+V (키다운, 물리 키) → 훅 통과(suppress 없음)
  → LLKHF_INJECTED 검사: 주입 키면 무시(자체 Ctrl+Shift+V·direct_paste 분 제외)
  → on_plain_paste() 콜백 → _bridge.plain_paste.emit (훅 스레드 → 메인 스레드)
  → _on_plain_paste(): queue.mark_plain_paste() + tray/panel 큐 표시 초기화
  → OS 기본 Ctrl+V 동작 (앱이 paste 처리)

paste_happened   → _update_paste_ui() → PasteHud.show_progress()로 진행 HUD 표시·갱신
paste_queue_done → PasteHud.finish() → 1.2초 후 HUD fade-out
```

### 설계 규칙

- **색상 테마**: 전체 UI에 중립 차콜 다크 테마 적용(`theme.py` — 배경 `BASE #121212` near-black, `MANTLE`/`CRUST`는 더 어둡게). **설정창은 예외**: 폼 가독성·정돈을 위해 전역 테마와 분리한 전용 팔레트를 쓴다(`settings_dialog.py` 상단 `_PAGE`/`_CARD`/`_INSET`/`_LINE`/`_BTN`/`_TITLE` — 어두운 페이지 위 한 톤 밝은 카드, 입력칸은 카드에 박힌 inset, 제목은 카드 안쪽 배치로 테두리 검정 얼룩 제거, 강조색 teal로 통일). `COLORS['base']`/`['mantle']`로 되돌리지 말 것.
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
6. **시크릿은 DPAPI로만 저장** — DB에 평문 API 키·토큰을 직접 넣지 않는다. 새 시크릿 키를 추가하면 `main.py`의 `_SECRET_KEYS` 화이트리스트에 등록하고, 쓰기는 `crypto.protect()`, 읽기는 `self._get_secret()` 헬퍼를 사용한다. DPAPI blob은 현재 Windows 계정에만 묶여 타 PC 복호화 불가 — 이로 인해 다중 PC 자동 동기화는 폐기됐다(설정은 PC별 독립).
