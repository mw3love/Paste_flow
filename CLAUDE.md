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
├── uia.py                  # 커서 아래 요소 hit-test — 창-스코프 rect_in_window_at(MSAA AccessibleObjectFromWindow+accHitTest — 마그네틱 캡처가 사용) + 점-기반 rect_at(MSAA AccessibleObjectFromPoint+UIA 폴백 — 현재 캡처 미사용), comtypes
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
    ├── capture_overlay.py  # 마그네틱 영역 캡처 오버레이 (Snipaste식 입력-소유 오버레이 — 얼린 최상위창+창-스코프 요소 스냅·자유드래그·크로스모니터 합성)
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

- **`main.py`** — 오케스트레이션 레이어. 모든 모듈을 연결하고 클립보드 모니터 → DB → 큐 → UI 간 이벤트 흐름 관리. 단일 인스턴스 보장(Windows 뮤텍스), 시작 알림 토스트 표시. 순차 붙여넣기 시 진행 HUD(`PasteHud`)를 표시·실시간 갱신하고 큐 소진/중단 시 fade-out한다(`_update_paste_ui`/`_on_paste_queue_done`). 실제 복사(클립보드 모니터 경로) 시 우하단 스택 토스트 표시(`_on_copy_toast` — 설정 `notify_on_copy`로 on/off, OCR 결과는 자체 토스트가 있어 `_persist_clipboard_item`으로 우회). **큐 클리어 공통 경로(`_clear_queue_ui`)**: 큐를 비우고 트레이·패널 하이라이트를 초기화하는 헬퍼 — '큐를 언제 비울지' 정책의 **포기/클리어** 범주(일반 Ctrl+V·큐 소진 완료·HUD ✕ 취소·우클릭 큐 해제·Ctrl+Shift+P 단발)가 공유한다. **일반 Ctrl+V 큐 비우기**: 인터셉터의 `on_plain_paste` 콜백 → `_bridge.plain_paste` 시그널 → 메인 스레드 슬롯 `_on_plain_paste()`가 `_clear_queue_ui()` 호출(`queue.clear()`는 `mark_plain_paste()`와 동일하게 `_reset_unlocked`). **큐 소진 완료**: `_on_paste_queue_done()`이 `_clear_queue_ui()` + `paste_hud.finish()` — 소진 후에도 큐가 남아 패널 우클릭이 '큐 해제'로 뜨는 찌꺼기 방지(Ctrl+Shift+V 신호 경로와 Ctrl+Shift+[ 직접 호출이 공유). **큐 idle 시간 주입**: 시작 시 DB `queue_idle_reset_sec`(기본 10초)를 읽어 `queue.set_idle_reset_sec()`로 주입하고, 설정 변경 시 즉시 반영. 시작 시 HKCU `Run` 키 실제 등록 상태를 DB `auto_start`에 동기화(`_sync_auto_start_from_registry`). **자동 시작 등록 방식**: HKCU `Software\Microsoft\Windows\CurrentVersion\Run`에 `PasteFlow` 값을 등록(`_set_auto_start`). 등록되는 명령은 **항상 `wscript.exe "%LOCALAPPDATA%\PasteFlow\autostart_launcher.vbs"`** 한 형태로, 이 launcher VBS는 `_write_autostart_launcher_vbs()`가 매번 새로 생성하며 `WScript.Sleep _AUTOSTART_DRIVE_WAIT_SEC*1000`(기본 15초) 대기 후 실제 PasteFlow를 hidden 모드로 실행한다. 대기 목적은 Drive(코드가 위치한 곳)가 부팅 직후 마운트되기 전에 실행되어 실패하는 문제 방지. 실행 대상은 빌드 모드별로 분기: exe 빌드는 `sys.executable` 절대경로, 스크립트 모드는 `pythonw.exe` + `run.pyw` 절대경로 — `run.pyw`가 `os.chdir`로 working directory를 자기 위치로 설정하고 예외를 `%LOCALAPPDATA%\PasteFlow\logs\error.log`에 기록한다. **주의**: `python.exe`나 `-m pasteflow.main` 형태로 등록하면 (1) 콘솔 창이 뜨고 (2) Run 키의 working dir이 시스템 기본(`C:\Windows\System32` 등)이라 `ModuleNotFoundError`가 발생하므로 사용 금지. **로컬 데이터 경로**: DB(`pasteflow.db`)·로그·launcher VBS 모두 `%LOCALAPPDATA%\PasteFlow\` 아래 저장. **모든 설정은 로컬 DB에만 저장** — 다중 PC `settings.json` 공유는 v1.4.0에서 폐기(DPAPI가 PC/계정 바인딩이라 시크릿 동기화와 양립 불가). `_resolve_db_path()`가 로컬 DB 부재 + 레거시 Drive DB 존재 시 1회 복사 마이그레이션 수행. **시크릿 처리**: `_SECRET_KEYS = {ocr_gemini_api_key_official, ocr_gemini_api_key_gateway}`는 DPAPI(`pasteflow/crypto.py`)로 암호화해 DB에 저장 — 저장 포맷 `enc:v1:<base64(CryptProtectData blob)>`. 읽기는 `_get_secret(key)`(= `unprotect ∘ db.get_setting`)로 복호화 후 사용, 쓰기는 `_on_settings_changed`에서 `_SECRET_KEYS`인 키만 `crypto.protect()`로 암호화한 뒤 저장. `crypto.protect/unprotect`는 빈값·이미 암호화된 값(`is_protected`)을 passthrough라 idempotent, `unprotect` 실패 시 `""`+경고(크래시 방지, 사용자는 설정창에서 재입력). **`_migrate_secrets(db)`**: 시작 시 1회 호출돼 ① `_SECRET_KEYS` 평문 → 암호화(`is_protected` 가드로 idempotent), ② 코드 어디서도 참조되지 않는 고아 설정 키 4종(`ocr_api_key`·`ocr_base_url`·`hotkey_settings`·`panel_always_on_top`) DELETE. **Gemini 키 분리 마이그레이션**(`_migrate_split_gemini_keys`): 시작 시 1회 호출돼 옛 단일 키(`ocr_gemini_api_key`/`ocr_gemini_model`/`ocr_gemini_model_cache`)를 `base_url` 유무 기준으로 backend별 슬롯(`_official`/`_gateway`)에 이전하고 옛 키를 DB에서 삭제. 새 api_key 슬롯에 쓸 때 `protect()`로 암호화. 옛 키는 한 번 삭제하면 다시 들어오지 않으므로 idempotent. Named Pipe IPC(`\\.\pipe\PasteFlow_IPC`) 서버 운영 — 두 번째 인스턴스 실행 시 패널 토글 신호 수신 후 해당 인스턴스 즉시 종료. **드래그 붙여넣기 헬퍼 함수** (panel.py의 `drag_to_app_requested` 시그널 처리): `_find_deepest_child()` — 커서 위치 최하위 자식 HWND 재귀 탐색; `_get_explorer_subfolder_at_cursor()` — SysListView32에서 커서 위치 서브폴더 경로 반환(크로스 프로세스 LVM_HITTEST); `_get_explorer_folder()` — CabinetWClass HWND → 드롭 대상 폴더 경로; `_get_desktop_path()` — 사용자 바탕화면 경로; `_image_data_to_png_bytes()` — image_data(PNG/JPEG/GIF/WebP/CF_DIB)를 PNG bytes로 인메모리 변환. PIL이 직접 못 여는 raw CF_DIB는 BMP 파일 헤더를 조립해 인식시킨다(`_create_thumbnail`과 동일 기법). `_save_image_to_folder`·이미지 AI 질의가 공유 — 영역 캡처(Alt+F2)처럼 raw DIB로 저장된 이미지도 정상 처리; `_save_image_to_folder()` — `_image_data_to_png_bytes`로 변환한 PNG를 폴더에 파일로 저장; `_save_image_to_drop_temp()` — `%TEMP%\PasteFlow\`에 임시 PNG 저장 후 절대경로 반환(Alt+드래그·우클릭 "파일로 저장 후 경로 복사"·`Ctrl+Shift+P` 이미지→경로 단축키 공용); `_read_image_from_clipboard()` — 현재 클립보드에서 이미지 bytes 반환(PNG 우선, 없으면 CF_DIB raw — 둘 다 `_save_image_to_drop_temp`가 그대로 처리); `_activate_and_send_ctrl_v(hwnd, sender=None)` — AttachThreadInput으로 포그라운드 잠금 우회 후 키 주입. `sender`(인자 없는 콜러블, 기본 `_send_ctrl_v_plain`)로 실제 주입 함수 교체 가능 — Alt+드래그처럼 호출 시점에 수정키가 눌린 경로는 `interceptor._release_modifiers_and_send_ctrl_v`를 넘긴다. AttachThreadInput은 **타겟 창 스레드**(`GetWindowThreadProcessId(hwnd)`)에 건다 — 드래그 시점 포그라운드는 PasteFlow 패널 자신(같은 스레드)이라 거기 거는 건 무효였고, 타겟 스레드에 붙어야 `SetForegroundWindow`+`SetFocus`가 포커스까지 첫 시도부터 넘긴다. 주입 직전 `GetAsyncKeyState(VK_MENU)`로 **Alt가 물리적으로 떨어질 때까지 QTimer 폴링**(25ms 간격·최대 1.5초) 후 Ctrl+V를 보낸다 — 드롭 순간 눌린 Alt는 가상 KEYUP으로 안 떨어지므로(`GetAsyncKeyState`는 물리 키 기준) 그냥 보내면 타겟이 `Ctrl+Alt+V`로 오인. **알려진 한계(cold 창)**: 타겟이 직전 비활성이던 Chromium 창은 프로그램적 활성화로 렌더러 텍스트 입력칸 포커스가 복원되지 않아 합성 Ctrl+V가 불안정(IME까지 겹치면 `ㅍ`로 입력되기도). 합성 클릭·간격 둔 Ctrl+V 등으로 시도했으나 불안정해 포기 — 사용자가 타겟 창을 먼저 클릭해 활성(warm) 상태로 만든 뒤 드래그하면 안정적이다. `_start_foreground_tracker()`는 `SetWinEventHook(EVENT_SYSTEM_FOREGROUND)`으로 포그라운드 창을 연속 추적해 드래그 대상 창을 확인한다. **이미지→경로 단축키 슬롯**: `_on_image_to_path_hotkey()` — **소스는 라이브 클립보드가 아니라 최신 히스토리 항목**(`db.get_recent_items(limit=1)[0]` — Ctrl+V의 '마지막 복사물'에 대응). 경로 텍스트는 히스토리에 안 남으므로(`_set_clipboard`의 self_triggered) 원본 이미지가 최신 자리에 유지돼 **이 키를 여러 번 눌러 같은 이미지를 무한히 경로로 붙일 수 있다**(Ctrl+V의 무한 반복과 대칭). 최신 항목이 이미지면 `_save_image_to_drop_temp()`로 PNG 저장(같은 항목 반복 시 `_img_to_path_cache=(item_id, path)`로 임시 PNG 재사용 → 디스크 재저장 회피, 새 이미지 복사 시 item.id가 달라 자동 캐시 미스) → 경로 텍스트 `ClipboardItem` 생성 → `interceptor._set_clipboard()` (규칙 5: `_self_triggered` 자동 처리) → 50ms 후 `interceptor._send_clean_key(VK_V)`로 현재 포그라운드 창에 Ctrl+V 주입 → `_clear_queue_ui()`로 큐 클리어(단발은 큐가 아닌 '이탈'이라 일반 Ctrl+V와 동일 취급 — 큐 기반은 Ctrl+Shift+[). 성공 시 저장한 PNG의 **썸네일을 토스트에 함께 표시**(`ToastNotification(..., icon="", image_path=saved_path)`) — Claude Code CLI 등은 붙여넣은 경로를 `[Image #N]` 라벨로만 보여줘 실제 이미지가 안 보이므로, "의도한 이미지가 맞는지" 그 자리에서 시각 확인하기 위함. 썸네일이 카테고리를 대신하므로 `🔤` 아이콘은 생략(`icon=""`). **`_activate_and_send_ctrl_v`를 쓰지 않는 이유**: 발화 시점에 사용자가 Ctrl+Shift를 누른 상태인데 `_send_ctrl_v_plain`은 수정키 해제 없이 단순 Ctrl down/V/Ctrl up만 보내 OS가 `Ctrl+Shift+V`로 인식한다. `_send_clean_key`는 Ctrl+Shift+V 순차 붙여넣기와 동일하게 수정키 해제·복원·입력기 전환 마스킹을 처리한다. 최신 항목이 이미지가 아니면 토스트만 표시. **순차 경로 붙여넣기 슬롯**: `_on_seq_image_to_path_hotkey()`(기본 Ctrl+Shift+[) — 위 단발 슬롯의 **큐 버전**. `queue.get_next()`로 다음 항목을 꺼내(Ctrl+Shift+V와 큐·포인터 공유) **이미지면** `_save_image_to_drop_temp(image_data)`로 PNG 저장→경로 텍스트 항목을 `_set_clipboard`+50ms 후 `_send_clean_key(VK_V)`로 주입(썸네일 토스트), **이미지가 아니면** 원본 그대로 `_set_clipboard`+주입(Ctrl+Shift+V와 동일). 캡처(Alt+F2)가 이미 큐에 이미지로 쌓이므로 캡처 여러 장을 순서대로 경로로 붙일 수 있다. summary 항목은 `db.get_item`으로 전체 로드(이미지 항목은 image_data 인라인이라 대개 불필요). 끝에 `_update_paste_ui()`로 진행 HUD 갱신, 큐 소진 시 `_on_paste_queue_done()`(큐 클리어 + HUD 페이드 — Ctrl+Shift+V 소진과 동일, 찌꺼기 방지). 큐가 비면 토스트만(단발 폴백은 Ctrl+Shift+P 담당 — '순차/일반'을 키로 구분). **OCR 워커 공통화**: `_start_ocr_worker(png_bytes)`가 영역 캡처 OCR(`_on_ocr_region_captured`)과 이미지 항목 OCR(`_on_ocr_image_item` / `_on_ocr_image_by_id`)을 공유한다. 이미지 항목 OCR은 panel/preview 우클릭에서 진입 — `image_data`(DIB 또는 PNG)를 PIL로 PNG bytes로 변환 후 동일 워커로 위임하며, 결과는 기존 `_on_ocr_done`(클립보드+DB+큐+정중앙 결과 칩) 흐름을 그대로 탄다. **Gemini backend 분기**: OCR은 v1.28.0부터 **항상 AI(Gemini/Mindlogic) API**로 처리한다(WinRT 엔진 분기 제거 — `_start_ocr_worker`가 `kind="gemini"` 고정). `_start_ocr_worker`가 `ocr_gemini_backend` 설정(미설정 시 `base_url` 유무로 추론)을 보고 official/gateway에 해당하는 `ocr_gemini_api_key_*`(암호화 저장 → `_get_secret`으로 복호화) / `ocr_gemini_model_*`을 골라 `OcrEngine`에 주입. official 분기는 `base_url=""`으로 강제(공식 API는 base_url 무시). **Gemini 백엔드 분기 공통화**: 위 backend→key/url/model 해석 로직을 `_resolve_gemini_cfg() → (api_key, base_url, model)` 헬퍼로 추출해 OCR 워커와 AI 질의 워커가 공유한다(DB 접근은 `_lock`으로 직렬화되어 워커 스레드 호출 안전). **AI 질의(우클릭 "AI에게 질문")**: panel이 `ai_query_requested(item_id)` emit → `_on_ai_query_requested`(id 기반 래퍼)가 DB 로드 후 `_ai_query_for_item(item)` 코어로 위임. 코어는 항목을 컨텍스트로 `AiQueryDialog`(질문 입력 모달)를 띄우고 — **텍스트 항목은 텍스트를, 이미지 항목은 `_image_data_to_png_bytes`로 변환한 PNG 썸네일을 컨텍스트로 표시** — 입력 시 `_start_ai_worker(question, context, image_png=None)`가 첫 대화 턴(`ocr_engine.build_ask_prompt`로 컨텍스트를 임베드한 user 프롬프트 + 표시용 원문 질문 `display`)을 만들어 `_run_ai_turn(conversation, image_png, popup)`(첫/후속 질문 공용)에 위임 → `_resolve_gemini_cfg()` 설정으로 `OcrEngine(kind="gemini").ask_messages(messages, image_png=...)`를 워커 스레드에서 호출(OCR 엔진 설정과 무관하게 항상 gemini 경로, **대화 히스토리 전체를 멀티턴으로 전송**). **이미지 항목이면 이미지를 멀티모달로 전송(시각 질의 — 첫 user 턴에만)**, 텍스트면 텍스트 컨텍스트만. `_ai_query_for_item`은 화면 핀(Alt+F3)·미리보기 팝업 우클릭 "AI에게 질문"(`ImagePreviewPopup.ai_requested`)에서도 호출 — DB id 없는 임시 항목도 받는다. 결과/에러는 `_bridge.ai_turn_done(payload dict)`/`ai_error` 시그널 → 슬롯 `_on_ai_turn_done`이 답변을 **읽기 전용 `TextPreviewPopup`(editable=False, markdown=True, center=True, 수정 메뉴 숨김)**으로 표시(DB 미저장 — 표시 전용). **AI 이어서 질문(멀티턴 대화)**: 답변창은 대화를 **'턴 탭'(Q1/Q2/…)으로 나눠** 보여주고(첫 답변은 탭 숨김·2번째부터 노출), 하단 **'이어서 질문' 입력칸**으로 후속 질문을 받는다 — Enter 시 `TextPreviewPopup.begin_followup`이 **펜딩 탭**(🤔 생각 중·경과시간)을 즉시 띄우고 `followup_requested` emit → `_on_ai_followup(popup, text)`이 답변창이 인스턴스별로 보관한 대화 히스토리(`popup._conversation`, 이미지는 `popup._image_png` 첫 턴에만)에 새 user 턴을 쌓아 `_run_ai_turn(popup=답변창)`으로 재질의(이전 문답을 인지한 답). 후속 답 도착 시 `popup.resolve_pending(answer)`로 펜딩 탭을 실제 답변으로 교체, 실패/빈 답이면 `popup.cancel_pending()`(펜딩 탭 제거·입력칸 재활성화). 진행 칩(`_start_cursor_progress`)은 첫 턴에만 뜨고 후속 턴은 펜딩 탭 자체가 진행 표시. `_on_ai_error`는 토스트(`🤖`) + API 키 미설정 시 설정창 자동 오픈(후속 질의 에러면 인플라이트 답변창 `cancel_pending`). **AI 자유질문(`` alt+` `` 단축키)**: `_on_ask_ai_hotkey()`가 컨텍스트 없이 `AiQueryDialog("", panel)`(질문칸만)을 띄우고 → `_start_ai_worker(question, "")`로 위임(컨텍스트 없는 자유 질문 — `_ask_prompt`가 질문 원문만 전송). 답변 표시는 `_on_ai_turn_done` 공유. **AI 답변 이미지로 복사**: `_on_ai_turn_done`이 답변 팝업에 `copy_as_image_requested` → `_on_answer_image_copy(pixmap)` 연결 — 렌더된 답변 픽맵을 영역 캡처와 동일 경로(`_qpixmap_to_dib`+`_set_clipboard`+`_persist_clipboard_item`)로 클립보드(DIB)+히스토리 저장. **멀티모니터 위치**: AI 입력창(`AiQueryDialog`)은 `showEvent`에서 **커서가 있는 모니터 정중앙**으로 이동하고, 백그라운드 상주 앱이 띄운 창이라 포그라운드를 못 가져와 한 번 클릭해야 타이핑되던 문제를 `_force_foreground()`(AttachThreadInput으로 Windows 포그라운드 잠금 우회 — 드래그 붙여넣기와 동일 기법) + `activateWindow` + `_editor.setFocus`로 해결해 **즉시 타이핑** 가능, 답변창은 `_ai_query_for_item`이 질문 제출 시점 커서를 `self._ai_anchor`에 저장하고 `_on_ai_turn_done`이 `TextPreviewPopup.open_new(..., center=True, initial_turn=(질문,답변))`로 **그 커서가 있던 모니터 정중앙**에 띄운다(읽기 편하고 가장자리 잘림 없음). **진행/결과 표시(커서 모니터 정중앙 칩)**: OCR·AI가 공유하는 `_start_cursor_progress(prefix, icon, anchor)`가 지속형 토스트(`ToastNotification(anchor=..., center=True)` — 클릭 통과, 앵커가 속한 모니터 정중앙)로 경과시간을 0.5초마다 갱신(`_tick_cursor_progress`). 옛 점(`●··`) 애니메이션은 `●`(넓음)·`·`(좁음) 폭 차이로 매 틱 칩 폭이 바뀌며 재중심(`_place_anchored`)이 좌우로 흔들려 제거(v1.27.0 — 경과시간만으로 "작업 중" 충분). 종료는 둘로 갈린다: `_stop_cursor_progress`(칩 즉시 fade — AI는 답변창이 같은 정중앙에 펼쳐지므로 칩만 닫음), `_finish_cursor_progress(message)`(칩을 결과 메시지로 전환 후 1.5초 뒤 fade — OCR은 `✓ 인식 앞부분…` 앞 24자 미리보기로 "제대로 됐나" 확인). 옛 우하단 고정 진행 토스트(`_start_ai_progress`/`_tick`/`_stop`)는 이 공용 칩으로 대체됨. 모델 폴백 발생 시 `_bridge.ocr_fallback`를 재사용해 알림(이 경로만 토스트 아이콘이 OCR `🔤`) — OCR 모델 not_found 폴백과 AI grounding 429 폴백(`gemini-3.1-flash-lite` 등 검색 무료 할당량 없는 모델 → `gemini-2.5-flash`) 양쪽이 이 토스트를 공유한다. **AI 웹 검색(grounding) 검증**(2026-06-27 실호출): 공식 경로(`_ask_google_genai`)가 신 SDK `google-genai`의 `google_search` 도구로 실시간 질문(날씨·날짜 등)에 정확히 답하는 것·이미지+grounding 동시·flash-lite 검색 429→2.5-flash 폴백을 실조건 검증 완료(구 SDK `google.generativeai`는 도구를 못 실어 미동작이었음).
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
  - **이미지→경로 단축키** (기본 `ctrl+shift+p`, 설정 가능): suppress. `on_image_to_path` 콜백 호출 → main이 **최신 히스토리 이미지**를 임시 PNG로 저장하고 절대경로 텍스트로 클립보드 교체 후 포그라운드 창에 자동 Ctrl+V(경로가 히스토리에 안 남아 같은 이미지를 여러 번 붙일 수 있음 — Ctrl+V의 무한 반복과 대칭). Claude Code CLI 등 "경로 텍스트"를 첨부로 받는 앱에 한 키로 붙여넣기 위한 진입점. `set_image_to_path_hotkey()`로 런타임 변경 가능.
  - **순차 경로 붙여넣기 단축키** (기본 `ctrl+shift+[`, 설정 가능): suppress. **이미지→경로의 '순차 버전'** — `on_seq_image_to_path` 콜백 호출 → main `_on_seq_image_to_path_hotkey`가 **순차 붙여넣기(Ctrl+Shift+V)와 같은 큐·포인터를 공유**하며 큐에서 다음 항목을 꺼내 **이미지면 임시 PNG 경로 텍스트로**, 아니면 원본 그대로 붙여넣는다. 캡처(Alt+F2)가 이미 큐에 이미지로 쌓이므로 캡처 여러 장을 이 키로 순서대로 경로 텍스트로 붙일 수 있다(예: 캡처1·2 → 이 키 두 번 → 경로1·2). 큐 소진 시 토스트만 표시(현재 클립보드 폴백은 일반 Ctrl+Shift+P가 담당 — '순차/일반'을 키로 구분). `set_seq_image_to_path_hotkey()`로 런타임 변경. 기본값 `[`(VK_OEM_4 `0xDB`)는 `_SPECIAL_KEY_MAP`에 있어 파싱되고, 설정창 녹화기(`_qt_key_to_name`)는 `Key_BracketLeft`/`Key_BraceLeft`(Shift 시 `{`)를 모두 `[`로 매핑해 재지정을 지원. Ctrl+Shift 조합이라 언어전환 마스킹은 기존 `_send_clean_key` 로직이 그대로 처리.
  - **AI 자유질문 단축키** (기본 `` alt+` ``, 설정 가능): suppress. `on_ask_ai` 콜백 호출 → main이 컨텍스트 없이 즉석에서 AI 질문 입력창을 띄운다(상시 켜진 PasteFlow에서 한 키로 AI 호출). `set_ask_ai_hotkey()`로 런타임 변경 가능. 백틱(`` ` ``)은 `_SPECIAL_KEY_MAP`에 `0xC0`(VK_OEM_3)로 매핑돼 파싱됨. Alt+key 조합은 기존 Alt+F2/F3와 동일 부류라 별도 마스킹 불필요.
  - **절대 일반 Ctrl+V 키 이벤트를 차단하지 않음.**
  - **이미지 클립보드 복원**: `_set_clipboard`는 이미지 데이터가 PNG면 `"PNG"` 등록 포맷과 함께 `_png_to_dib`(PIL, 24bpp·투명은 흰 배경 합성)로 변환한 CF_DIB를 병행 등재한다 — "PNG" 포맷을 못 읽는 앱(그림판·한글 등)에서 붙여넣기 무반응이던 문제 해결(v1.21.0). 변환은 훅 콜백 내 동기 실행(최악 2560×1440 비압축성 기준 150~200ms 실측).
  - 추가 공개 메서드: `direct_paste(item)` — 순차 큐 포인터 변경 없이 즉시 붙여넣기(더블클릭·드래그 경로 사용); `send_ctrl_v_to(hwnd)` — 대상 윈도우 포커스 후 Ctrl+V 전송.
- **`hotkey_manager.py`** — Win32 RegisterHotKey + 히든 윈도우 기반 단축키 유틸. 현재 패널 토글이 interceptor로 이동되어 실제 등록된 단축키 없음. `_SPECIAL_KEY_MAP`(VK 코드 매핑)은 paste_interceptor가 공유 사용.
- **`ocr_engine.py`** — OCR 추상화. `OcrEngine(language="ko").recognize(PIL.Image) → str` 동기 API. `kind` 파라미터로 엔진 선택(**단 앱 OCR 경로는 v1.28.0부터 항상 `kind="gemini"` — 설정에서 엔진 선택이 제거됨. 아래 `"winrt"` 엔진 코드는 남아 있으나 앱에서 호출되지 않는다**):
  - `"winrt"`(코드 잔존, 앱 미사용): winocr 패키지로 Windows WinRT OCR 래핑. 4096px 초과 이미지 자동 downscale, RGBA/L 모드 자동 변환, 언어팩 미설치 시 AssertionError → RuntimeError('언어팩') 변환.
  - `"gemini"`: Gemini API 직접 호출 (`_recognize_gemini()`). `base_url` 파라미터가 설정된 경우 OpenAI 호환 게이트웨이/프록시로 자동 분기 (`_recognize_openai_compat()`). `"openai_compat"`는 별도 kind 값이 아님. 모델명은 콜러가 `self.model`로 지정 — 빈 문자열이면 `gemini-3.1-flash-lite`(게이트웨이) / `gemini-2.5-flash`(공식 API) 폴백. **`max_tokens=16384`** 사용: 게이트웨이가 reasoning(thinking) 토큰을 같은 max_tokens 예산에서 차감하므로 작게(2048) 잡으면 thinking을 많이 하는 모델(gemini-2.5-pro·gemini-3.1-pro-preview 등)에서 본문이 0~200자로 잘린다. 16384에서 6종 모델 모두 `finish_reason=stop`으로 정상 종료 확인. 청구는 실제 사용 토큰 기준이라 비용 영향 미세.
  - 모듈 함수 `_normalize_base_url(base_url)` — 사용자가 endpoint 전체 경로(`/chat/completions`, `/models`, `/completions`, `/embeddings`)나 trailing `/`를 붙여 입력해도 자동으로 SDK 표준 base 형태로 보정. 게이트웨이 호출(`_recognize_openai_compat`, `list_gemini_models`) 양쪽에서 사용.
  - 정적 메서드: `is_winrt_available()`, `is_winrt_language_supported(lang)`, `winrt_supported_languages()`, `list_gemini_models(api_key, base_url)` — 게이트웨이 `/models` 또는 `genai.list_models()`에서 `gemini-*` ID만 필터 반환, 설정창 모델 새로고침에 사용.
  - **모델 화이트리스트**(`_VERIFIED_MODELS`) — `(name, tier_rank, on_official, on_gateway)` 튜플. 코드 작성자가 실제로 호출되는 것을 확인한 모델만 등재. 게이트웨이가 광고는 하지만 호출 시 404를 던지는 모델(`gemini-3.1-flash-lite-preview` 사례) 같은 라인업 불일치를 흡수. 모듈 함수 3종: `sort_models_with_whitelist(candidates, backend)` — backend(`"gateway"`/`"official"`) 호환 화이트리스트 ∩ candidates는 tier 오름차순(저렴 우선) verified로, 나머지는 알파벳순 unverified로 분리. `whitelist_model_names(backend)` — 캐시 비어 있는 첫 실행용 초기 콤보 목록(backend 호환 화이트리스트, tier 오름차순). `select_fallback_model(failed_model, backend)` — 안전망 기본은 `_FALLBACK_DEFAULT='gemini-2.5-flash'`(어느 backend에서도 동작), 실패 모델이 그것이면 같은 backend의 다른 화이트리스트 모델 1개.
  - **자동 폴백**(`_call_with_fallback`) — `_recognize_gemini`/`_recognize_openai_compat`가 1차 호출에서 `_is_model_not_found` 휴리스틱(메시지에 `not found`·`model_not_found`·`404 ... model` 포함) 매칭 시 `select_fallback_model`로 1회 재시도. 인스턴스 필드 `last_used_model`(실제 응답 만든 모델) / `last_fallback_from`(원래 시도했다 실패한 모델, 폴백 없으면 `None`)에 결과 기록 → main 워커가 OCR 종료 후 `_bridge.ocr_fallback.emit(failed, used)` → 슬롯이 토스트 `{failed} → {used}로 폴백` 표시(문구는 모델 not_found·grounding 429 폴백 양쪽에 중립). DB의 `ocr_gemini_model` 자체는 수정하지 않음(잠깐 장애일 수 있음, 사용자 명시 선택 보존).
  - 호출자(main.py)가 `ThreadPoolExecutor(max_workers=1)`로 워커 스레드에서 실행해 UI 블로킹 방지.
  - **`ask_messages(messages, image_png=None) → str`** — AI **멀티턴** 질의(우클릭 "AI에게 질문" + 이어서 질문). `messages`는 `[{"role":"user"/"assistant","content":str}, ...]` 대화 히스토리(마지막이 방금 던진 user 질문, 앞선 턴은 직전까지의 대화 → 웹 챗봇처럼 이전 문답을 인지한 답). **`ask(question, context_text="", image_png=None)`**는 단일 user 턴(`_ask_prompt`로 컨텍스트 임베드)을 만들어 `ask_messages`에 위임하는 단발 래퍼(하위 호환). 게이트웨이 경로는 OCR과 **동일한 Gemini 배관을 재사용**한다: 2갈래(게이트웨이=`_ask_openai_compat`(OpenAI 호환 chat.completions·`messages`에 system+대화 히스토리 그대로 매핑) / 공식 API=`_ask_google_genai`(신 SDK `google-genai`·대화 히스토리를 `types.Content` 리스트로 변환 user→"user"/assistant→"model"))로 갈리고 `_call_with_fallback`(모델 not_found 자동 폴백)·`max_tokens=16384`를 그대로 탄다. **공식 경로는 `google_search` 도구(grounding)를 항상 붙여 모델이 필요할 때만 웹 검색** → 실시간 날씨·뉴스 등에 답한다(구 SDK `google.generativeai` 0.8.x는 proto에 필드는 있으나 요청에 이 도구를 못 실어 grounding이 동작 안 함 → 신 SDK `google-genai`로 이전, 2026-06-27 실호출 검증). **게이트웨이 경로는 grounding 미지원**(OpenAI 호환 chat.completions에 검색 도구를 안 실음) — 실시간 질문에 답하려면 공식 백엔드 사용 필요. flash-lite 등 **검색 무료 할당량이 없는 모델은 grounding 호출이 429**(RESOURCE_EXHAUSTED)나므로, `ask_messages`(공식 경로)가 `_is_quota_error` 감지 시 안전망 모델(`_FALLBACK_DEFAULT`=`gemini-2.5-flash`)로 1회 재시도해 검색 답을 얻는다(OCR과 분리 — OCR은 검색 미사용이라 이 폴백이 불필요·유해. `last_fallback_from`/`last_used_model` 세팅 → main이 폴백 토스트 표시). **`image_png`가 주어지면 첫 user 턴에만 이미지를 멀티모달로 전송**(시각 질의·이미지는 한 번만) — 게이트웨이는 OCR(`_openai_compat_call`)에서 검증된 `image_url`(base64 PNG)+text content를, 공식 API는 `types.Part.from_bytes(data, mime_type="image/png")`를 첫 user 턴 parts에 넣는다(이미지+grounding 동시도 2026-06-27 실호출 검증 완료). 이미지가 없으면 텍스트 컨텍스트+질문만 보낸다. 모듈 함수 `_ask_prompt(question, context_text)`(공개 래퍼 `build_ask_prompt`)가 컨텍스트가 있으면 클립보드 내용을 감싸 질문에 끼우고, 없으면 질문만 그대로 보낸다(자유 질문·이미지 질의). **시스템 프롬프트**(`AI_SYSTEM_PROMPT` 모듈 상수 — 바이브 코딩 초보자용 개발 멘토 페르소나: 정직·비유 필수·답변 구조·조언 태도·마크다운 형식)를 모든 `ask*` 경로에 주입한다: 게이트웨이는 `messages`의 `role:"system"`으로, 공식 API는 `GenerateContentConfig(system_instruction=...)`으로. **OCR(`recognize`) 경로에는 적용하지 않는다**(텍스트 추출에 페르소나 개입 방지) — `_ask_*` 메서드에만 추가해 분리. 동기 호출이라 호출자가 워커 스레드에서 실행해야 한다.

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
- **`image_preview.py`** — 이미지 미리보기 팝업. 다중 창 동시 표시 지원(`open_new(item, panel_geom, native=False)`로 생성 — text_preview와 동일하게 `ClipboardItem` 전체를 받음). 휠 줌, 드래그 이동, 더블클릭 닫기, ESC 닫기. 커서가 있는 모니터에 배치(`screenAt()`). **`native=True`**(핀 Alt+F3 경로가 사용)면 초기 줌을 원본 픽셀 1:1로 띄운다(화면 초과 시만 축소) — "캡처한 크기 그대로". 일반 미리보기는 `PREVIEW_MAX_W/H`(640×480)에 맞춰 축소. **cascade 위치**: `open_new`의 cascade offset은 "총 창 수"가 아니라 "새 앵커(커서/패널) 근처(`_CASCADE_NEAR`)에 이미 떠 있는 창 수"에만 비례 — 핀은 커서를 앵커로 쓰므로 커서를 옮겨 새로 핀하면 커서 바로 옆에 뜨고(드리프트 없음), 같은 자리 연속 핀만 어긋난다(겹침 방지). **우클릭 메뉴**: `복사` / `텍스트 추출(OCR)` / `AI에게 질문` / `파일로 저장 후 경로 복사` / `주석 편집` / `닫기`. 시그널 `copy_requested(ClipboardItem)` → main의 `_on_copy_item`, `ocr_requested(ClipboardItem)` → main의 `_on_ocr_image_item`, `ai_requested(ClipboardItem)` → main의 `_ai_query_for_item`(이미지 멀티모달 질의), `copy_as_path_requested(ClipboardItem)` → main의 `_copy_image_as_path_for_item`. **이 메뉴는 패널에서 연 미리보기(`_on_preview_image`)와 화면 핀(Alt+F3 `_on_pin_hotkey`) 양쪽에서 동일 연결** — 핀 항목은 DB id가 없으므로 main 핸들러는 id 기반 래퍼와 `ClipboardItem` 기반 코어(`_ai_query_for_item`/`_copy_image_as_path_for_item`)로 분리돼 있다. 메뉴는 `_effective_item()`을 대상으로 한다 — 씬에 주석이 있으면 `flatten_scene_to_png`로 평탄화한 임시 항목(id 없음), 없으면 원본 `self._item`(비파괴 — 벡터 주석은 씬에 남아 재편집·undo 가능). 평탄화본의 `복사`는 `copy_requested`(DB 항목 전제 — id 없으면 히스토리 미저장·무피드백) 대신 `annotated_copy_requested`(주석 편집 완료 복사와 동일: 클립보드+히스토리 저장+토스트) 경로로 emit한다(v1.21.0). **활성/비활성 테두리**: 활성(보고 있는 창)=코랄(`PEACH`, 주인공), 비활성=중립 회색(`SURFACE2`, 존재만 표시·안 튐) — QSS 동적 프로퍼티가 런타임에 재반영 안 돼 `_apply_active_style(active)`에서 스타일시트 직접 교체. **인라인 주석 편집**: `image_annotator._EditorMixin`을 상속해 같은 창에서 Space로 편집 모드 진입(main의 `_on_preview_image`는 이미 열린 창이면 닫지 않고 `toggle_edit_mode()` 호출). **툴바(chrome) 배치 = 상단 예약 strip 방식**(v1.18.0): chrome(타이틀바+툴바)을 레이아웃이 아니라 floating 자식(`_chrome`)으로 두고, 창은 `WA_TranslucentBackground` + root layout top 마진(`_chrome_h`)으로 **상단에 chrome 높이만큼 투명 strip을 항상 예약**한다. 뷰어 모드엔 strip이 투명이라 안 보이고(이미지만 떠 있는 느낌), 편집 토글은 그 예약 공간에 chrome을 show/hide(`_layout_chrome`)할 뿐이라 **창 크기·위치가 안 바뀐다 → 토글 시 잔상 없음 + 툴바가 이미지를 안 덮음**(이미지는 strip 아래 배치). 팝업 본체 배경은 공유 스타일시트의 `QWidget{background:_BG}`가 strip을 덮지 않게 `_apply_active_style`에서 `QWidget#previewroot{background:transparent}`로 되돌린다(자식 chrome·뷰 배경은 불투명 유지). 툴바보다 좁은 이미지에선 편집 시 창 '폭만' 오른쪽으로 늘려 잘림 방지(세로·이동 없음). `show_preview`는 strip 높이만큼 창을 위로 올려 이미지가 의도 위치에 오게 보정. **이력**: 처음엔 토글 때 창을 키워 공간을 만들었으나(리사이즈+이동) translucent 프레임리스 창에서 잔상 발생 → "토글 시 지오메트리 불변"만이 해법이라 strip 상시 예약 방식으로 전환. 한계: 투명 strip은 클릭통과가 아니라 이미지 위 ~63px 클릭이 뒤 앱으로 안 넘어감. 완료 시 `annotated_copy_requested(bytes)`(→ main `_on_annotation_copy`: 클립보드+히스토리 저장) / `export_file_requested(bytes)`(→ main `_on_annotation_export`: PNG 파일 저장) emit.
- **`image_annotator.py`** — 이미지 주석 편집기(CleanShot/Snipaste 스타일). `ImagePreviewPopup`이 상속하는 `_EditorMixin`(도구·색·두께·undo·스포이드) + `_AnnotatorView`(QGraphicsView, 그리기 인터랙션) + 도형 아이템(`_RectItem`/`_EllipseItem`/`_LineItem`/`_PathItem`/`_ArrowItem`/`_BadgeItem`/`_TextItem`, 전부 `_HandleResizeMixin`)로 구성. **도구**: 선택(V)·네모(R)·원(E)·선(L)·화살표(A)·펜(P)·텍스트(T)·번호(C). `QGraphicsScene` 기반이라 줌하면 주석이 이미지와 함께 스케일됨. **그리기 규칙**: 빈 영역 드래그로 생성(시작점→놓은 점 이동량<4px면 클릭으로 보고 폐기+선택 해제), 그린 직후 자동 선택(펜은 제외 — 연속 그리기). 펜은 기존 주석 위에서도 항상 그림(펜 선의 선택·이동은 V 도구로). Shift=정사각/정원/45° 스냅. **편집 단축키**: 화살표키로 선택 항목 이동(기본 10px·Shift/Ctrl=1px), Ctrl+A 전체 선택, Ctrl+C/V 주석 내부 복제(템플릿 clone, +12px cascade), Ctrl+Z undo(빈 텍스트 등 무의미 항목은 건너뜀). 휠(가운데)클릭 드래그=창 이동(`_win_drag_*` 재사용, 편집/뷰어 공용). **크기조절·회전 핸들**(v1.19.0): 선택 시 **우하단 파란 사각=균일 스케일**, **상단 중앙 코랄 원=회전**(중심 피벗, Shift=15° 스냅) — 둘 다 **선택(V) 도구일 때만 표시**(`_handle_active`가 `_owner_tool()` 확인). 회전은 `setTransformOriginPoint(center)`+`setRotation()`, clone(`_copy_common_to`)이 회전·피벗 복사, 평탄화는 씬 transform 그대로 렌더라 회전 반영. **경계 분리**: `_content_rect()`=타이트 경계(선택박스·핸들·텍스트 배경 기준), `boundingRect()`=`_content_rect ∪ 회전 핸들 영역`(상시 예약 → 선택 해제 시 핸들 잔상 방지, 얇은 도형은 좌우로도 핸들이 삐져나오므로 위뿐 아니라 전방향 합집합). boundingRect 여유분이 scale 의존이라 크기조절 mouseMove에서 `prepareGeometryChange()`로 갱신. arrow/badge는 기본 shape가 boundingRect 기반이라 `_base_shape()`를 content로 override(여유분이 클릭영역에 새는 것 방지). **핸들 크기 가변**(`_handle_px`): 핸들 크기를 주석 작은 변에 비례(`_HANDLE_FRAC=0.22`)시키되 씬 단위 `[5,12]` 클램프 — 작은 주석에서 핸들이 거대해 보이던 문제 해결(item scale로 축소해도 표시 크기 기준이라 함께 작아짐). 회전 원 지름=사각 변(반지름=`_handle_px/2`), 줄기는 "중심 거리"가 아니라 "도형~원 빈 간격(`_ROT_GAP=14`)" 고정이라 핸들 크기와 무관하게 보이는 줄기 길이 일정. **텍스트**: 작성 후 텍스트 도구 유지(연속 배치), Ctrl+Enter로 마무리, 빈 텍스트는 focusOut 시 정리. 새 텍스트 시작 시 다른 선택 해제, 크기 조절은 편집 중 텍스트가 있으면 그것만(`_font_size_targets`). T 활성 시 T 아래 **수평 옵션 바**(`_text_opts_bar` — 배경 스와치 투명/반투명검정/흰/회/검 직접 선택 + 글자 크기 스테퍼). **번호(badge)**: C 활성 시 C 아래 크기 스테퍼(`_SizeStepper`, 값 유지→다음 번호 같은 크기·선택 번호에도 적용), 붙여넣기 시 새 번호 부여. 스테퍼 ▾/▴는 길게 누르면 연속 증감(auto-repeat). **스포이드**: 화면 픽셀 색 따오기(`_ColorLoupe` 미리보기, ctypes GetPixel). 씬→PNG 평탄화는 `flatten_scene_to_png(scene)`. main과는 시그널만 주고받고 실제 클립보드 복사/파일 저장은 main이 처리.
- **`text_preview.py`** — 텍스트 미리보기 팝업. 다중 창 동시 표시 지원(`open_new(item, panel_geom, editable=True, markdown=False, center=False, initial_turn=None)` — `ClipboardItem` 전체를 받아 우클릭 메뉴의 복사·수정에 활용). **`center=True`**면 `panel_geom` 옆이 아니라 `panel_geom`이 속한 **모니터 정중앙**에 띄운다(`show_preview`의 center 분기 — AI 답변 전용, `_ai_anchor`가 가리키는 커서 모니터 한복판). **`editable=False`**면 우클릭 "수정" 메뉴를 숨긴다 — AI 답변처럼 DB에 없는 임시 항목(id 없음)은 수정·저장 경로가 무력하므로 메뉴 자체를 제거. 인스턴스는 `_instances` 클래스 목록이 닫힐 때까지 참조를 유지(close 시 정리)하므로 별도 보관 없이 안전.
  - **AI 이어서 질문(멀티턴 대화, markdown 전용)**: 답변창은 대화를 **'턴 탭'(Q1/Q2/…)** 으로 나눠 각 탭이 그 문답 한 쌍만 상단부터 보여준다(스크롤이 무한정 길어지지 않게 — 모델은 전체 대화를 인지, 탭은 표시만 분리). 데이터는 `self._turns: list[(질문, 답변)]`, 현재 탭 `_current_tab`. 첫 답변은 탭 바 숨김(예전 모습), **두 번째 답변부터 탭 노출**(`_rebuild_tabs`가 `len>1`일 때만 표시). 현재 탭=코랄, 나머지=중립(`_update_tab_styles`). `open_new`의 `initial_turn=(질문,답변)`으로 첫 문답을 show 전에 넣어 깜빡임을 피한다. 하단 **'이어서 질문' 입력칸**(`QLineEdit`, 폰트를 위젯에 명시 후 그 메트릭으로 높이 계산 — 스타일시트 font-size는 sizeHint에 안 반영돼 한글 디센더가 잘렸던 버그 수정, 최소 32px): Enter → `_submit_followup`이 `begin_followup(질문)`으로 **펜딩 탭**을 즉시 만들고 `followup_requested(text)` emit. **펜딩 탭**: 답변 자리를 sentinel `_PENDING`으로 둔 턴을 추가하고 본문에 `🤔 AI가 생각하고 있어요… (경과시간)`을 `_think_timer`(0.5초)로 갱신(`_tick_pending`) — "멈춘 게 아니라 일하는 중" 표시. 옛 점(`●··`) 애니메이션은 폭 변화 노이즈로 제거(v1.27.0 — 진행 칩과 동일 이유, 경과시간만 남김). 펜딩 동안·펜딩 탭으로 전환할 땐 **크기 재산정을 건너뛰어 이전 답변 크기 유지**(본문이 짧아 창이 확 줄어드는 것 방지). 답 도착 → main이 `resolve_pending(answer)`(펜딩 탭을 실제 답변으로 교체·재산정·상단 스크롤), 실패/빈 답 → `cancel_pending()`(펜딩 탭 제거·직전 탭 복귀·입력칸 재활성화). `_render_current_turn`이 현재 탭을 `**Q.** 질문 --- 답변`으로 렌더하고 `_item.text_content`도 그 턴으로 맞춘다(보고 있는 탭 = 복사되는 것 — 펜딩 중엔 `_item` 미오염). 상단 탭 바(`_top_reserve`)·하단 입력칸(`_bottom_reserve`) 높이는 자동 크기 산정·그립 배치에서 예약. `_think_timer`는 `closeEvent`에서 정리.
  - **마크다운 모드(`markdown=True`, AI 답변 전용)**: 평문용 `QPlainTextEdit` 대신 `QTextEdit`+`setMarkdown()`으로 서식 렌더링. 일반 미리보기는 원문 확인 용도라 평문 유지.
    - **요소별 색**(`_collect_syntax_spans` 1회 수집 → `_apply_marks`가 재적용): `setMarkdown` **직후(서식 변형 전)** Qt가 남긴 서식을 읽어 스팬(위치·색·밑줄여부)을 1회만 수집해 `_syntax_spans`에 저장하고, 형광펜 토글마다 그 위치로 색을 재적용한다. 색은 앱 전역 테마와 분리한 전용 상수(`_MD_HEADING/_MD_CODE/_MD_BOLD/_MD_ITALIC/_MD_LINK`) — 제목(`headingLevel`)=파랑, 굵게(`fontWeight≥700`)=코랄, **코드(`fontFixedPitch`)=파랑(`#38bdf8`)+밑줄+볼드**, 기울임=초록, **하이퍼링크(`charFormat.isAnchor()`)=파랑(`#60a5fa`)+밑줄(비볼드)**. 코드는 모노스페이스로 튀지 않게 본문 폰트(`_MD_FONT_FAMILY`=Noto Sans KR)로 통일(`setFontFixedPitch(False)`+`setFontFamily`) — 단 이 볼드·폰트 강제는 `_apply_marks`에서 코드(`color==_MD_CODE`)에만 적용하고 링크는 색+밑줄만(링크는 비볼드). **수집을 1회만 하는 이유**: 코드 색칠이 `fixedPitch`를 끄므로 매번 재탐지하면 두 번째부터 코드를 못 찾아 형광펜 시 코드색이 증발한다(텍스트 불변이라 position 안정). 배경색은 형광펜 전용으로 비움(채널 분리). `setDefaultStyleSheet`는 마크다운에 안 먹어 이 방식 사용. **링크 클릭·호버**: `QTextEdit`는 `QTextBrowser`와 달리 anchor를 자동으로 열지 않으므로, 좌클릭 press 시점에 `anchorAt`으로 링크 URL을 `_pressed_anchor`에 저장해 두고 드래그 없이(선택 없음) 떼면 `webbrowser.open`으로 기본 브라우저에서 연다(형광펜·선택→복사 두 모드 공통, release에서 먼저 처리). 인터랙션 플래그에 `LinksAccessibleByMouse`를 더하고, 뷰포트 커서를 I빔으로 고정해 둔 탓에 Qt 자동 링크 호버 커서가 안 떠 MouseMove마다 `anchorAt`으로 링크 위면 손모양(`PointingHandCursor`)·아니면 I빔으로 직접 토글한다(창 드래그 중 제외).
    - **두 모드 토글(형광펜 ↔ 선택→복사)**: AI 답변창은 좌드래그 동작이 두 모드로 갈린다(`_highlight_mode`). **기본=선택→복사(OFF)** — 좌드래그는 순수 텍스트 선택만, **Ctrl+C**로 선택 부분을 복사(부분 발췌. `keyPressEvent`가 처리 → `copy_text_requested(str)` emit → main `_on_copy_selected_text`가 클립보드+히스토리 저장, U+2029→`\n` 변환). **형광펜(ON)** — 좌드래그 선택→릴리스 시 강조 토글. 전환은 **우상단 토글 버튼**(`_hl_btn`, 마크다운 전용 오버레이) 또는 **Shift+백틱**(`Key_QuoteLeft`/`Key_AsciiTilde`). **버튼 아이콘은 항상 🖍**(v1.28.0 — 옛 `✂`/`🖍` 교체 폐기)이고 ON/OFF는 배경으로만 표시한다(ON=코랄 채움 / OFF=중립 배경, `_update_hl_btn`). 선택 모드에서 먼저 드래그해 둔 선택 범위는 형광펜 모드로 진입하는 순간 즉시 형광펜 적용(`_toggle_highlight_mode`가 살아있는 선택을 마크로 변환). **형광펜 마크**: `_HL_BG`(어두운 적갈색 칩 `#281414`)+`_HL_FG`(빨강 `#ff5a5a`)+빨강 밑줄, 마크 클릭=해제, 우클릭 "형광펜 지우기"=전체 해제. 마크는 문서 position 범위로 보관, `_apply_marks`가 매번 문자서식 초기화→`_syntax_spans` 재적용→형광펜 재적용(토글 시 원래 색 복원). **복사 직렬화**(`_marked_markdown`): Qt `toMarkdown()`이 굵게·제목조차 못 보존(Qt 6.10)하므로 라운드트립 대신 **원본 소스에 백틱만 삽입**(렌더 position→소스 offset 두 포인터 정렬)해 모델 마크다운 100% 보존 + 형광펜만 `` `text` ``.
    - **우상단 버튼 행**(마크다운 전용 오버레이): 창 모서리부터 왼쪽으로 **닫기 ✕ · 최대화 ⛶ · 최소화 ▁ · 형광펜 🖍** 순서로 `_position_overlays`가 재배치(탭 바 우측 예약폭 128px). **닫기(✕, `_close_btn`)**=ESC와 동일(hover 코랄). **최대화(⛶↔❐, `_toggle_maximize`, v1.28.0)**=현재 모니터 작업영역을 꽉 채우고 재클릭 시 이전 크기·위치 복원(`_maximized`·`_pre_max_geom`; 다른 크기 변경(그립 리사이즈 등)이 일어나면 `_clear_maximized_state`로 버튼 상태를 실제와 정합). **최소화(▁, `_toggle_minimize`, v1.28.0)**=본체를 숨기고 화면에 **미니 핸들 막대**(`_MiniHandle`)만 남긴다 — 코랄 알약(`WA_TranslucentBackground`+내부 `#pill` 위젯으로 진짜 둥근 모서리), 🤖 아이콘 + **질문 앞 12자**(넘치면 …) + 툴팁=질문 전문(여러 개 최소화 시 구분), **제자리 클릭=복원 / 드래그=이동**(4px 임계로 클릭↔드래그 구분 — AI_Dictionary 확장의 📖 최소화 아이콘 기법 포팅, 옮긴 위치 기억). Tool 창이라 작업표시줄에 안 잡혀 이 막대 방식을 쓴다. 별도 top-level이라 `closeEvent`에서 명시 정리.
    - **답변 본문 폰트**(v1.27.0): `_apply_scale`이 마크다운(AI 답변)일 때 `_MD_FONT_FAMILY`=**Noto Sans KR** · 굵기 **Medium(500)** · **`PreferNoHinting`**을 적용한다 — 레귤러(400)+하드 힌팅은 Gemini 대비 획이 얇게 보여, 굵기를 올리고 하드 힌팅을 풀어(획이 픽셀 스냅되며 얇아지는 것 방지) 브라우저처럼 도톰·부드럽게 한다. 볼드(제목·`**굵게**`)는 `setMarkdown`이 700을 줘 500 본문과 대비 유지(`_collect_syntax_spans`의 볼드 판정 `≥700`도 500 본문을 오검출 안 함). 일반 텍스트 미리보기(12px)는 `_FONT_FAMILY`=맑은 고딕·400·`PreferFullHinting` 유지(작은 크기 또렷).
    - **창 이동(휠클릭 공통)**: 창 이동은 **가운데(휠)클릭 드래그**가 AI 답변·일반 미리보기 양쪽에서 동작한다(`_is_move_button` — 휠클릭은 항상 이동, 좌클릭은 일반 미리보기에서만 이동·AI 답변에선 텍스트 선택). 일반 미리보기(패널 Space)도 좌클릭·휠클릭 둘 다로 끌 수 있다.
    - **수동 리사이즈 그립**(`_ResizeGrip`, AI 답변 전용): **우하단 진짜 꼭짓점**에 flush 배치된 24px 코너 그립을 **좌클릭 드래그**로 자유 리사이즈(가운데클릭=이동과 충돌 없음). 리사이즈하면 `_manual_size=True`로 자동 크기 산정(`_resize_to_content`)을 중단하고 wrap on으로 내용 재배치(길면 세로 스크롤). 하단 '이어서 질문' 입력칸이 있을 때 v1.28.0에 그립을 입력칸 위로 밀었던 것을 **꼭짓점으로 되돌렸다**(입력칸 오른쪽 끝 24px를 살짝 덮지만, 코너=크기조절 관용을 지켜 커서 혼란 방지 — 입력칸 위로 올리면 코너에 크기조절 커서가 안 떠 이동 커서로 오인됐다).
    - **휠 동작(v1.28.0)**: **휠=페이지 스크롤(기본) / Ctrl+휠=글자 크기(웹 관례 — `_apply_scale`+`_apply_marks`, 창 크기 불변, 넘치면 스크롤) / Alt+휠=창 크기 조절**(`_apply_manual_resize`; Windows가 Alt+휠을 가로 스크롤로 바꿔 `y=0`이라 `angleDelta().x()` 폴백). 일반 텍스트 미리보기는 휠=줌+창 맞춤 유지. **코드/`<>` 스팬 줌 보정**: 코드 스팬은 폰트 패밀리가 명시돼(`_apply_marks`) 에디터 기본폰트의 (줌된) 픽셀크기를 안 물려받아 Ctrl휠에서 안 커지던 버그 → `_apply_marks`가 현재 배율 `FontPixelSize`를 직접 박고 줌마다 재적용(v1.28.0).
    - **옛 F 중앙 존 스냅 제거(v1.28.0)**: 후속 질문 입력칸에 `f`가 타이핑되는 충돌 + ⛶ 최대화 버튼·Alt휠 창 크기 조절이 그 역할을 대체 → **F 트리거 제거**. 관련 스냅 코드(`_toggle_snap_zone`·`_SNAP_PRESETS`·`configure_snap_presets`·`_commit_snap_preset`·`_orientation_of`·`_snapped` 플래그·main의 스냅 프리셋 로드/저장)는 이후 **일괄 제거 완료**(dead code 정리). `_ResizeGrip` 리사이즈는 이제 프리셋을 저장하지 않고, `_current_avail`만 최대화·수동 리사이즈용으로 유지.
    - **마크다운 전처리**(`_fix_markdown_emphasis`): ① **따옴표볼드** — `**'X'**` 뒤에 공백 없이 글자가 오면 닫는 `**`가 flanking 규칙상 인정 안 돼 볼드가 풀린다(스펙 동작). `**'X'**`→`'**X**'`로 따옴표를 볼드 바깥에 옮겨 정상 렌더. ② **볼드+백틱 중첩 해소**(`_CODE_BOLD_RE`/`_BOLD_CODE_RE`) — Qt 마크다운은 볼드와 인라인코드의 중첩(`` `**X**` ``·`**` + 백틱)을 렌더 못 해 `**`가 글자로 노출된다(실측 확인). 중첩을 순수 코드(`` `X` ``)로 풀고, 코드 스팬 자체를 볼드로 그려(`_apply_marks`) '볼드+백틱'을 마크다운 문법 대신 포맷으로 살린다.
    - **리스트 불릿 통일**(`_set_list_bullets`): 모든 마크다운 리스트를 `•`(ListDisc)로. **문단 간격**(`_apply_block_spacing`): 줄간격 `_MD_LINE_HEIGHT`(135%)+문단 여백 `_MD_BLOCK_MARGIN`(7px), 측정용 tmp 문서에도 동일 적용해 높이 정확.
    - **고정 폰트+스크롤**: 폰트 `_MD_FONT_SIZE`(16px) 고정·폭 `_MD_INITIAL_MAX_W`(600). 휠=스크롤(글자 크기 일정), Ctrl+휠=줌. 길면 화면 80%(`_MD_MAX_H_FRAC`)에서 세로 스크롤(`ScrollBarAsNeeded`, 폭 `_SCROLLBAR_W` 보정).
  - **표시 위젯**: `QPlainTextEdit` (QLabel+QScrollArea 아님). `setWordWrapMode(WrapAtWordBoundaryOrAnywhere)`로 공백 없는 긴 URL/해시도 문자 단위 wrap. QLabel은 word-boundary 없는 토큰을 절대 잘라주지 않아 폐기.
  - **"한 번에 다 보임" 정책**: 양쪽 스크롤바 영구 차단(`ScrollBarAlwaysOff`) + editor `FocusPolicy.NoFocus` — QPlainTextEdit 내부 가짜 vScroll(`vScrollMax≥1`)이 키보드(스페이스/PageDown)로 노출돼 빈 영역이 보이던 문제 차단. width는 `PREVIEW_INITIAL_MAX_W * scale` (zoom과 비례 확장, 화면 너비 cap), height는 화면 한계까지 자유 확장하여 모든 줄 표시.
  - **`LineWrapMode` 동적 전환**: 자연 너비가 popup width cap 안에 들어오면 `NoWrap` 강제 (QPlainTextEdit의 viewport에 내부 padding이 있어 textWidth를 맞춰도 sub-pixel 차이로 wrap이 새는 경우 발생 — 휠 줌마다 1↔2줄 깜빡임의 원인). 초과 시에만 `WidgetWidth` wrap.
  - **크기 계산**: 자연 너비/높이 측정에 독립 `QTextDocument` 사용 — QPlainTextEdit의 자체 document는 lazy layout이라 `setTextWidth` 직후 `size()`가 갱신 안 됨. `math.ceil(idealWidth())`로 sub-pixel 부족분 보정.
  - **전체 창 드래그**: 텍스트 부분 선택 미지원(`NoTextInteraction`). 부분 텍스트가 필요하면 우클릭 메뉴 `수정`으로 편집 다이얼로그에서 자연스럽게 선택. viewport + popup 본체 양쪽 모두에서 left-drag 이동 처리.
  - **우클릭 메뉴**: `전체 복사` / `수정` / `닫기` (패널 메뉴와 동일 명칭). 시그널 `copy_requested(ClipboardItem)` → main의 `_on_copy_item`, `edit_requested(item_id)` → main의 `_on_preview_edit_request`(`EditItemDialog` 띄우고 `_on_edit_item`으로 위임). QPlainTextEdit 기본 우클릭 메뉴는 `setContextMenuPolicy(NoContextMenu)`로 차단.
  - **이미지로 복사**(마크다운=AI 답변 창에서만 노출): 답변 *전체*(스크롤로 가려진 부분 포함)를 한 장의 이미지로. `_render_answer_pixmap()`이 살아있는 에디터 `QTextDocument`를 `clone`(형광펜·요소색 char 포맷 보존)해 현재 표시 폭으로 줄바꿈 고정 후 단독 렌더한다. 본문 기본색이 QSS로만 지정돼 단독 렌더 시 검게 나오므로 `QAbstractTextDocumentLayout.PaintContext`의 palette Text 색에 본문색(`_TEXT`)을 주입(명시 char 포맷은 우선 적용) + 배경·여백 입힘 + HiDPI는 `devicePixelRatio` 보정. 시그널 `copy_as_image_requested(QPixmap)` → main `_on_answer_image_copy`가 영역 캡처와 동일 경로(`_qpixmap_to_dib` → `_set_clipboard` + `_persist_clipboard_item`)로 클립보드(DIB)+히스토리 저장. 일반 텍스트 미리보기는 원문 확인용이라 미노출.
  - **활성/비활성 테두리**: 이미지 미리보기와 동일 정책(활성=코랄, 비활성=중립 회색). 프레임리스 최상위 위젯에 건 테두리가 자식에 가려지는 문제를 피하기 위해 내부 `popup_container`에 적용.
  - **`_clamp_to_screen(avail)`**: resize 후 popup이 화면 밖으로 나가면 안쪽으로 끌어들임.
- **`toast.py`** — 토스트 알림. `_ToastStack` 싱글턴이 활성 토스트를 코너 기준 위로 스택(최신=맨 아래)하고 닫힐 때 재정렬, 최대 5개 동시 표시(초과 시 가장 오래된 것 즉시 제거). **스택 모니터**: 스택 토스트(시작 알림·복사 알림 등 수동적 알림)는 항상 **주 모니터** 우하단에 고정(`_ToastStack._screen = primaryScreen`) — 예측 가능한 위치(커서를 따라 모니터를 옮겨다니지 않음). 능동적으로 기다리는 AI·OCR 진행/결과는 스택이 아니라 anchor/center 모드로 활성 모니터 중앙에 뜬다(역할 분리). (이력: 한때 커서 모니터 고정이었으나 시작·복사 알림은 주 모니터가 더 예측 가능해 되돌림.) `ToastNotification(message, icon, badge, badge_position, image_path, anchor, center)` — `badge_position`은 `"leading"`(아이콘과 본문 사이) 또는 `"trailing"`(본문 뒤, 기본). `image_path`가 주어지면 아이콘과 본문 사이에 그 PNG 파일의 썸네일(최대 96px, `QPixmap(path)` 직접 로드·로드 실패 시 조용히 생략)을 삽입 — 이미지→경로 붙여넣기 시 "의도한 이미지가 맞나" 시각 확인용. `icon`이 빈 문자열이면 아이콘 라벨 자체를 생략(썸네일이 카테고리 구분을 대신). `show_copy_toast(item, queue_count)`는 누적 큐 카운트를 `Q{n}` 형태 badge로 본문 앞(`leading`)에 배치해 2초간 표시. `reserve_bottom(px)`(HUD 등 우하단 위젯을 위해 스택 하단 여백 확보). 시작 알림·복사 알림 등 일반 토스트가 이 스택을 공유한다. **커서 앵커 모드**(`anchor=QPoint`): 우하단 스택 대신 그 지점을 기준으로 배치하고 `WindowTransparentForInput`로 클릭을 아래 앱에 통과시킨다(작업 방해 0). `center=True`면 앵커 옆(+16px·경계 반전) 대신 **앵커가 속한 모니터 정중앙**에 배치(`_place_anchored`). OCR·AI 진행/결과 칩이 이 모드를 쓴다(main `_start_cursor_progress`). **지속형 토스트**(`duration_ms=0`): 자동 fade-out 없이 호출자가 `dismiss()`로 닫는 모드 + `set_message(text)`로 본문 갱신(앵커 모드면 폭 변화 시 재중심) — AI 질의·OCR처럼 끝나는 시점을 미리 알 수 없는 작업의 진행 표시용. **OCR 토스트**: icon `🔤`로 통일, 본문에서 `OCR:` prefix는 제거(아이콘이 카테고리 구분을 담당). 모든 OCR 진입점(진행 중·결과·각종 에러)이 동일 아이콘을 사용해야 일관됨.
- **`paste_hud.py`** — 순차 붙여넣기 진행 HUD. 우하단 비활성 창(`WA_ShowWithoutActivating` — 포커스 미탈취), 큐 항목 목록을 `✓`(완료·흐림)·`▶`(다음·강조)·`·`(대기)로 표시하고 헤더에 `순차 붙여넣기 n/total`. 단일 인스턴스 재사용 — `show_progress(items, pointer)`로 표시·갱신, `finish()`로 1.2초 후 fade-out. 헤더 우측 **✕ 취소 버튼**(`_cancel_btn`, hover 시 코랄)은 `cancel_requested` 시그널을 emit → main `_on_cancel_paste_queue`가 `_clear_queue_ui()`(큐 비우기 + 표시 초기화) + `dismiss()`로 처리. **`dismiss()`**는 사용자 명시 취소용으로 `finish()`의 1.2초 linger 없이 즉시 fade-out(표시 중이 아니면 무시). 큐 10개 초과 시 "외 N개"로 축약. 표시 중 `toast.reserve_bottom()`을 호출해 복사 토스트가 HUD 위로 쌓이게 한다.
- **`tray.py`** — 시스템 트레이. 좌클릭 시 `panel_toggle_requested` 시그널 emit → main이 패널 토글.
- **`settings_dialog.py`** — 단축키 커스터마이징(패널 토글, OCR, 이미지→경로 — `KEY_IMAGE_TO_PATH_HOTKEY` = `hotkey_image_to_path`, 기본 `ctrl+shift+p`, 순차 경로 붙여넣기 — `KEY_SEQ_IMAGE_TO_PATH_HOTKEY` = `hotkey_seq_image_to_path`, 기본 `ctrl+shift+[` — `_qt_key_to_name`에 `Key_BracketLeft`/`Key_BraceLeft`→`[` 캡처 매핑 추가, 화면 핀 — `KEY_PIN_IMAGE_HOTKEY` = `hotkey_pin_image`, 기본 `alt+f3`, 영역 캡처 — `KEY_CAPTURE_HOTKEY` = `hotkey_capture`, 기본 `alt+f2`, AI 자유질문 — `KEY_ASK_AI_HOTKEY` = `hotkey_ask_ai`, 기본 `` alt+` `` — `HotkeyEdit._qt_key_to_name`에 백틱(`Key_QuoteLeft`/`Key_AsciiTilde`→`` ` ``) 캡처 매핑 추가), 캡처 저장 폴더(`KEY_CAPTURE_FOLDER` = `capture_save_folder`, QLineEdit + 찾아보기 `QFileDialog`), 히스토리 제한, 순차 큐 자동 초기화 시간(`KEY_QUEUE_IDLE_RESET` = `queue_idle_reset_sec`, QSpinBox 1~3600초·기본 10초), 자동 시작, 복사 알림 on/off(`notify_on_copy`) 설정. **그룹 구성/배치(v1.28.0 개편)**: `기본 단축키 (고정)`(복사/붙여넣기/순차붙여넣기 — 맨 위) → `기능 단축키 (변경 가능)` → `AI 연동 (Gemini / Mindlogic API)` → `일반`. **OCR 전용 그룹은 제거** — OCR은 별도 엔진·인식 언어 선택 없이 항상 AI(Gemini/Mindlogic) API로 처리하므로(WinRT 제거) OCR 단축키를 `AI OCR` 이름으로 기능 단축키 맨 아래로 옮기고, 엔진/언어 콤보(`_ocr_engine_combo`/`_ocr_lang_combo`)·`_on_engine_changed`를 삭제했다. `_on_save`는 `KEY_OCR_ENGINE="gemini"` 고정 + Gemini 키/모델/캐시를 항상 저장(OCR·AI 답변 공용). 기능 단축키는 **4개 하위 묶음**(①패널 ②경로류 ③영역 캡처·핀류 ④AI 호출·AI OCR)을 얇은 구분선으로 분리, 라벨은 `패널 불러오기`·`경로 붙여넣기`·`영역 캡처`·`영역 캡처 핀`·`AI 호출`·`AI OCR`로 통일(저장 KEY는 불변, 표시만). 그룹 제목은 **코랄**(강조), 단축키 행 라벨엔 `•` 불릿. **단축키 표시 대문자화**: `HotkeyEdit.format_hotkey`가 저장값(소문자 canonical — 파서용)을 표시만 `Ctrl + Shift + P` 형태로 변환(기본 단축키 표기와 통일). **레이아웃**: 전체 콘텐츠를 `QScrollArea`(`_finalize_size`가 창 크기를 콘텐츠+화면에 맞춰 산정·넘으면 스크롤)로 감싸 고정 크기가 콘텐츠를 압박해 word-wrap 라벨 heightForWidth가 진동하던 **드래그 떨림**과 작은 화면 오버플로를 해결. 버튼(취소/저장)은 스크롤 밖. **API 연결 테스트 버튼**(`_on_test_api`, v1.28.0): 현재 backend/키/URL로 `list_gemini_models`를 워커 스레드에서 호출해 키·연결 유효성을 확인(`_test_done` 시그널 → ✓/✗ 상태 라벨). 개수는 API 전체 gemini 수라 드롭다운(화이트리스트)과 달라 개수 대신 "연결 성공 — 키 유효"만 표기. Gemini 모델 콤보 옆 ↻ **새로고침 버튼**(Qt 표준 아이콘 `SP_BrowserReload` — 폰트 무관 보장)으로 `OcrEngine.list_gemini_models()` 호출 → 결과를 콤보에 반영하고 현재 backend의 `KEY_OCR_GEMINI_MODEL_CACHE_OFFICIAL` 또는 `_GATEWAY`(JSON list)에 저장 — backend별로 모델 캐시 분리 → 다음 실행 시 캐시 로드. 네트워크 호출은 `threading.Thread` + 내부 시그널 `_models_fetched(list, str)`로 UI 스레드 안전 통신. **콤보 정렬**은 `ocr_engine.sort_models_with_whitelist(cached, backend)`에 위임. backend는 **API 백엔드 콤보**(`_current_backend()` → "Google AI Studio"/"Mindlogic Gateway", v1.28.0에 "공식" 제거·"학교 게이트웨이"→"Mindlogic Gateway")로 사용자가 명시 선택. 콤보 전환 시 `_on_backend_changed()`가 이전 backend 입력값을 `self._settings`에 stash하고 새 backend의 키·URL·모델·캐시를 입력란에 다시 채워, 두 backend를 자유 전환해도 양쪽 값이 모두 보존된다. base_url 입력란은 gateway일 때만 노출. 저장 시 `_on_save`는 활성·비활성 backend의 키/모델/캐시를 모두 함께 emit해 한쪽이 사라지지 않게 한다. 결과를 `_fill_model_combo(verified, unverified)`가: verified는 상단(tier 오름차순), 검증/미검증 둘 다 있으면 `insertSeparator`로 구분선, unverified는 하단에 회색(`ForegroundRole=COLORS['subtext0']`) + 툴팁 "PasteFlow가 검증하지 않은 모델 — 게이트웨이가 광고하지만 호출 실패 가능". 모든 항목엔 전체 모델명 툴팁을 달고, `_adjust_model_popup_width()`로 드롭다운 팝업(view) 최소 폭을 최장 모델명에 맞춰 넓혀(콤보 본체·설정창 폭은 불변) 공통 접두사를 공유하는 긴 이름의 가운데 생략을 막는다. 캐시가 비어 있는 첫 실행은 `whitelist_model_names(backend)`로 초기 채움(unverified는 빈 리스트). `self._verified_models`에 verified 목록을 보관해 `_update_model_hint()`가 `💡 가장 저렴: {verified[0]}`(또는 검증 모델 없으면 `(검증된 모델 없음 — ↻ 새로고침을 시도하세요)`) 안내. ↻ 결과 머지(`_on_models_fetched`)도 동일 정렬 적용 후 캐시 갱신.
- **`ocr_overlay.py`** — 모니터별 분리 오버레이. `OcrOverlay`는 매니저(QObject 베이스, QWidget 아님)이고 실제 위젯은 `_ScreenOverlay`로 각 QScreen마다 1개씩 생성. `start()` 호출 시 모니터 수만큼 `_ScreenOverlay`를 만들고 각각 자기 화면을 `screen.grabWindow(0, 0, 0, w, h)`로 캡처해 표시. 한 모니터에서 드래그 시작되면 `drag_started` 시그널로 매니저가 다른 오버레이를 `deactivate()`(마스크만 표시·입력 차단). ESC/우클릭은 어느 오버레이에서든 전체 취소. **다중 DPI 모니터 대응**: 가상 데스크톱 전체를 단일 위젯으로 덮으면 Qt 백킹 스토어 DPR이 하나로 고정돼, DPR이 다른 모니터에 진입할 때 좌표·크기가 어긋나 고DPI 노트북 화면이 좌상단 일부로 축소되는 증상이 발생한다. 모니터별 분리 위젯 + `setScreen()` 명시 바인딩으로 Qt가 모니터별 DPR을 독립 처리하므로 문제 자체가 발생하지 않는다. 공개 API(`region_captured(QPixmap)`, `cancelled()`, `start()`)는 호출부 변경 없이 유지.

### 단축키 체계

| 단축키 | 동작 | 감지 방식 |
|--------|------|-----------|
| Ctrl+Shift+V | 순차 붙여넣기 (suppress) | WH_KEYBOARD_LL (paste_interceptor) |
| ctrl+space *(기본값, 설정 가능)* | 패널 토글 (suppress) | WH_KEYBOARD_LL (paste_interceptor) |
| ctrl+shift+s *(기본값, 설정 가능)* | OCR 영역 선택 시작 (suppress) | WH_KEYBOARD_LL (paste_interceptor) |
| ctrl+shift+p *(기본값, 설정 가능)* | 클립보드 이미지를 임시 PNG로 저장 후 경로 텍스트로 자동 Ctrl+V (suppress) | WH_KEYBOARD_LL (paste_interceptor) |
| ctrl+shift+[ *(기본값, 설정 가능)* | 순차 경로 붙여넣기 — 큐에서 다음 항목을 꺼내 이미지면 경로 텍스트로(Ctrl+Shift+V와 큐 공유) (suppress) | WH_KEYBOARD_LL (paste_interceptor) |
| alt+f3 *(기본값, 설정 가능)* | 클립보드 이미지/텍스트를 화면에 핀(떠 있는 창)으로 띄우기 (suppress) | WH_KEYBOARD_LL (paste_interceptor) |
| alt+f2 *(기본값, 설정 가능)* | 영역 캡처 → 클립보드(DIB)+파일 저장 (suppress) | WH_KEYBOARD_LL (paste_interceptor) |
| alt+\` *(기본값, 설정 가능)* | AI 자유질문 — 컨텍스트 없이 즉석에서 AI 질문 입력창 (suppress) | WH_KEYBOARD_LL (paste_interceptor) |
| 트레이 좌클릭 | 패널 토글 | Qt 이벤트 |

> ⚠️ `Alt+1~9` 직접 붙여넣기, `Ctrl+Shift+X` 큐 초기화, `Ctrl+Shift+Z` 실수 복구는 **의도적으로 제거**됨.

### 화면 핀 · 영역 캡처 (Snipaste식, v1.8.0~)

Snipaste를 대체하는 캡처 기능군. 단축키 추가는 모두 기존 패턴(`set_*_hotkey` + 훅 감지 블록 + 브리지 시그널 + 설정 배선) 복제.

- **화면 핀 (Alt+F3)** — `main._on_pin_hotkey`: 클립보드 이미지를 읽어(`_read_image_from_clipboard`) `ImagePreviewPopup.open_new(..., native=True)`로 화면에 띄움(원본 1:1 크기, 화면 초과 시만 축소 / 다중 창·ESC 닫기·Space 주석 편집은 image_preview가 이미 제공). 이미지가 없으면 클립보드 **텍스트를 다크 배경 PNG로 렌더**(`_render_text_to_png` — 맑은 고딕 18px, 워드랩, 배경 `theme.BASE`+글자 `theme.TEXT`로 앱 다크 테마와 통일)해서 띄움(텍스트도 주석 가능 = 사실상 이미지화). **배치(v1.23.0)**: 방금 Alt+F2로 캡처한 이미지면(중간에 다른 걸 복사 안 함) `place_rect`로 **캡처한 그 자리에 1:1로 정확히 덮고**(줌=사각형폭/픽맵폭이라 DPI 무관) **등장 시 테두리 1회 반짝**(`_flash_border` — 흰→코랄, `_flashing` 플래그로 활성화 이벤트가 안 덮게 가드), 아니면 커서 우측에 배치(폴백). **`image_preview._bg_item.setTransformationMode(Smooth)`**: QGraphicsPixmapItem 기본 Fast(nearest)가 뷰의 SmoothPixmapTransform을 덮어써 비정수 배율에서 이미지·텍스트가 거칠게 보이던 것 수정(모든 미리보기 공통 개선).
- **영역 캡처 (Alt+F2)** — `main._on_capture_region(pixmap, rect)`: 마그네틱 캡처 오버레이(`_capture_overlay` = `CaptureOverlay`)가 선택 영역 QPixmap + **캡처한 논리 전역 사각형**을 `region_captured(QPixmap, QRect)`로 emit → **DIB**(`_qpixmap_to_dib` — 24bpp BI_RGB, 붙여넣기 호환 최광)로 변환 → `interceptor._set_clipboard`+`_persist_clipboard_item`(OCR 결과와 동일 무중복 경로: 클립보드+히스토리+큐) → `_save_image_to_folder`(설정 `capture_save_folder`, 기본 `_default_capture_folder()` = `<Pictures>\PasteFlow`, 없으면 생성) → 썸네일 토스트(`📷`). 캡처 사각형은 `self._pin_place_rect`에 기억해 **직후 Alt+F3 핀이 제자리 덮기**에 쓰고, 외부 복사(`_on_new_clipboard_item`)가 들어오면 무효화(=방금 캡처한 이미지일 때만 적용). 설정에 캡처 단축키 행 + 저장 폴더 행(`QFileDialog`).
- **마그네틱 캡처 (v1.23.0 재작성 — 입력-소유 + 얼린 최상위창 + 창-스코프 요소 스냅)** — `uia.py`(`rect_in_window_at(hwnd,x,y)` = 특정 창 안 커서 아래 최말단 요소의 물리픽셀 사각형 — **MSAA `AccessibleObjectFromWindow`(창-스코프)로 IAccessible 루트를 얻고 `accHitTest`를 반복 하강**. 점-기반 `AccessibleObjectFromPoint`/UIA `ElementFromPoint`는 최상위 오버레이를 짚으므로 **창-스코프로 대체**. 크롬 최대화 북마크 개별 인식 = MSAA만 정확, Snipaste도 OLEACC. 옛 점-기반 `rect_at`은 남아 있으나 캡처 미사용) + `ui/capture_overlay.py`(**입력-소유(비클릭-통과) 오버레이** — 클릭-통과면 커서 밑 실제 창이 `WM_SETCURSOR`를 받아 HWP 등 커스텀 커서가 십자를 덮으므로, 오버레이가 입력을 소유해 `setCursor(CrossCursor)`로 어떤 앱 위에서든 십자 보장(Snipaste 모델). **옛 `WindowTransparentForInput`(클릭-통과)+`SetSystemCursor`(십자 전역변조)+`WH_MOUSE_LL`(마우스 훅)은 전부 폐기**). 캡처 시작 시 오버레이 show *전에* `GetTopWindow`+`GW_HWNDNEXT`로 **최상위 창 `(hwnd,사각형)`을 Z-order로 1회 얼림**(`_enum_top_windows` — 최소화·클로킹·자기 오버레이 제외). QTimer ~16ms로 `GetCursorPos`→얼린 목록에서 커서 밑 최상위 창→그 창에 한정해 `uia.rect_in_window_at`로 요소 하강(못 짚으면 창 전체 폴백)→물리→논리 변환(`MonitorFromPoint` 물리원점+QScreen DPR)→하이라이트(요소 hit-test는 `_UIA_MIN_INTERVAL`≈30ms 스로틀), ESC 폴링. **`_capture_overlay`로 main에 연결**(Alt+F2). 동작:
  - **클릭 캡처**: 오버레이가 입력을 소유하므로 Qt `mousePressEvent`/`mouseReleaseEvent`로 받음(**옛 WH_MOUSE_LL 데몬 훅 폐기** — 전역 마우스 훅 제거로 시스템 마우스 끊김 위험도 소멸, suppress 춤도 불필요). 좌클릭=현재 하이라이트 요소/창 캡처, 우클릭=취소(둘 다 매니저 플래그만 세팅하고 실제 처리는 tick).
  - **자유드래그 폴백(3c)**: 좌버튼 누른 채 `_DRAG_THRESHOLD`(4px) 이상 이동하면 클릭 대신 자유 사각형(시작점~현재 커서, 요소 무시). 안 움직이고 떼면 요소 클릭. 빈 영역을 드래그 없이 클릭하면 무시(`_capture(None)`).
  - **크로스 모니터 합성(`_crop_global`)**: 선택 영역이 단일 모니터면 그 화면 스크린샷에서 바로 crop, 여러 모니터에 걸치면 가장 높은 DPR을 타깃으로 빈 캔버스에 각 화면 조각을 제 위치에 그려 합성(배율 다른 조각은 타깃 DPR로 스케일 — 기하학 정확, 저DPI 조각은 약간 소프트). 100/125/150% 트리플 모니터 실조건 검증 완료.
  - 결과 QPixmap + 캡처 사각형 → `region_captured(QPixmap, QRect)` → `_on_capture_region`(DIB+클립보드+히스토리+파일+토스트 + `_pin_place_rect` 기억).
  - **십자 커서 (v1.23.0)** — 커서는 `setCursor(CrossCursor)` 하나로 처리(입력-소유라 위젯이 커서를 정함 → **HWP 포함 모든 앱에서 십자 유지**). 옛 `SetSystemCursor` 전역 커서 변조·`atexit` 복원은 폐기(전역 상태 변조 제거 = 크래시 시 십자 잔존 없음). **한계**: HWP는 MSAA 요소를 안 내줘 창 전체 스냅만 되고 요소 스냅은 안 됨(폴백).
  - **깜빡임 제거 (v1.23.0)** — 진입 시 화면 한 번 깜빡이던 것을 `WA_OpaquePaintEvent`(전 픽셀 직접 칠함 → 배경 지우기·반투명 레이어드 첫-프레임 검은 프레임 제거)+`show_overlay`의 `repaint()`(매핑 직후 동기 첫 페인트)로 제거. **원인 분석**: `WA_ShowWithoutActivating`으로 비활성 표시라 첫 페인트가 비동기로 밀려 한 프레임 빈 화면이 보였고, 동기 `repaint()`가 그 갭을 닫는 게 실제 해법(OCR 오버레이는 활성화하며 표시돼 이 증상 없음 — 그래서 미수정).
  - **드래그 부드러움** — 어두운 마스크를 입힌 딤 스크린샷(`_dimmed`)을 `prepare()`에서 1회 사전합성하고, 하이라이트 변경 시 `set_highlight_global`이 이전∪현재 영역(+테두리 여유 `_INVAL_MARGIN`)만 `update(dirty)`로 부분 repaint → 프레임당 전체화면 알파합성 제거. `_POLL_MS`=16(~60fps)과 결합해 Snipaste급 드래그.
  - **미구현(선택)**: HWP처럼 MSAA 빈 창에 UIA 트리 하강 폴백(단 UIA는 크롬도 거칠어 효과 불확실), 휠/방향키로 부모 요소 확장. **엣지**: 배율 다른 두 모니터에 걸친 자유드래그는 합성으로 정상이나, 드래그 시작점의 논리 좌표 샘플은 첫 tick 기준(±16ms). 상세 이력은 plan 파일 `tidy-doodling-taco.md`.

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
  → _on_plain_paste(): _clear_queue_ui() (큐 clear + tray/panel 큐 표시 초기화)
  → OS 기본 Ctrl+V 동작 (앱이 paste 처리)

paste_happened   → _update_paste_ui() → PasteHud.show_progress()로 진행 HUD 표시·갱신
paste_queue_done → _on_paste_queue_done(): _clear_queue_ui() + PasteHud.finish() → 1.2초 후 HUD fade-out
```

### 설계 규칙

- **색상 테마**: 전체 UI에 중립 차콜 다크 테마 적용(`theme.py` — 배경 `BASE #121212` near-black, `MANTLE`/`CRUST`는 더 어둡게). **강조색은 코랄(`PEACH`) 단일 액센트**(v1.18.0~) — "무채색=수동·기본, 코랄=활성·선택·주목"의 2톤 체계. 패널 항목 테두리는 큐밖/완료=무채색(`SURFACE2`), 큐 안=코랄(`PanelItemWidget`의 `in_queue` 파라미터로 생성 시점부터 구분). 옛 민트(teal)는 패널·설정창·AI질문창·OCR/캡처 오버레이·트레이에서 전부 코랄로 흡수하고 `theme.py`에서 `TEAL`/`TEAL_HOVER`/`COLORS['teal']`를 제거(버튼 hover용 `PEACH_HOVER` 추가). 단, 텍스트 미리보기 마크다운 요소색(제목 파랑/코드 파랑/볼드 코랄/기울임 초록)은 답변 가독성용 별도 체계라 액센트와 무관(건드리지 않음). **설정창은 예외**: 폼 가독성·정돈을 위해 전역 테마와 분리한 전용 팔레트를 쓴다(`settings_dialog.py` 상단 `_PAGE`/`_CARD`/`_INSET`/`_LINE`/`_BTN`/`_TITLE` — 어두운 페이지 위 한 톤 밝은 카드, 입력칸은 카드에 박힌 inset, 제목은 카드 안쪽 배치로 테두리 검정 얼룩 제거, 강조색 coral로 통일). `COLORS['base']`/`['mantle']`로 되돌리지 말 것.
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
