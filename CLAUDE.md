# CLAUDE.md

이 파일은 Claude Code(claude.ai/code)가 이 저장소에서 작업할 때 참고하는 안내 문서입니다.

## 목표
- 처음 의도: 복사한 순서대로 Ctrl+Shift+V마다 다음 항목이 붙는 순차 붙여넣기 클립보드 매니저(이전 두 번의 시도가 실패해서 시작). 3월에 달성했고, 7~9월에 캡처·녹화·OCR·음성 입력까지 넓어졌다 — 이 확장은 의도 변경으로 인정한다.
- 끝난 모습: 클립보드·캡처·녹화·음성 입력을 단축키로 다루는 "내 Windows 작업 허브"를, 지금 기능 그대로 매일 쓰는 단계.
- 완료 기준:
  - 매일 쓰면서 막히는 버그가 없다
  - 쓰다가 나온 불편은 그때그때 고친다
- 범위: 현재 기능 전체(순차·경로 붙여넣기, 패널, 캡처·핀, GIF·영상 녹화, 주석 편집, OCR, 음성 입력). 이후 추가·제거는 정해진 기준 없이 그때그때 판단한다(지웠다 다시 넣는 일이 생길 수 있음을 감수).
- 정한 날: 2026-10-03
- 이 의도나 끝난 모습을 바꾸게 되는 결정은 혼자 정하지 말고 사용자에게 묻는다.

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

> 모델이 실제로 되는지는 **설정창 `연결 테스트` 버튼**이 그 자리에서 실호출해 확인한다
> (v1.42.0). 빌드타임 전수 스윕(`tools/sweep_models.py` + `model_matrix.json`)은 폐기됐다.

---

## 프로젝트 구조

```
pasteflow/
├── main.py                 # 진입점, 앱 초기화 및 모듈 오케스트레이션
├── clipboard_monitor.py    # 클립보드 감시 (WM_CLIPBOARDUPDATE)
├── paste_queue.py          # 순차 붙여넣기 큐 & 포인터 관리 (핵심)
├── paste_interceptor.py    # Ctrl+Shift+V 감지 + 패널 토글 단축키 감지 (핵심)
├── hotkey_diag.py          # 임시 진단 — 새 메모 단축키(Alt+`)가 가끔 안 먹는 원인 추적용 로그(`logs\memo_hotkey.log`). 원인을 찾으면 이 모듈과 호출부(훅·main `_on_new_memo_hotkey`)를 지운다
├── hotkey_manager.py       # `_SPECIAL_KEY_MAP`(특수 키 이름 → VK 코드 표)만 남음 — paste_interceptor가 import. 옛 RegisterHotKey `HotkeyManager`는 2026-10-03 제거
├── database.py             # SQLite CRUD (clipboard_items, settings)
├── models.py               # ClipboardItem 데이터 모델
├── crypto.py               # DPAPI 시크릿 보호 (API 키 암호화 저장)
├── uia.py                  # 커서 아래 요소 hit-test — 창-스코프 rect_in_window_at(최말단 자식 HWND를 루트로 MSAA accHitTest 하강, 창 밖 오배치 과대사각형은 담긴 창으로 보정=HWP 편집영역/툴바 대응, 전용 WinDLL로 argtypes 격리 — 마그네틱 캡처가 사용), comtypes
├── ocr_engine.py           # OCR — 게이트웨이 AI API(OCR 전용, v1.6x부터 — AI 질의 경로 제거)
├── gdrive.py               # 구글 드라이브 OAuth (루프백+PKCE·TokenCache·커넥터 도구 스펙) — v1.6x에서 PasteFlow 앱 자체는 더 이상 쓰지 않는다(우클릭 "AI에게 질문" 등 AI 질의 기능 통째 제거). `webchat/`(별개 도구)이 계속 import하므로 모듈은 남겨둔다 — 지우면 webchat이 깨진다.
├── web_open.py             # 질문을 브라우저에서 직접 열기 — 구글 검색 AI 모드(udm=50) URL + 이미지 첨부 시 클립보드 주입, 키 주입 전 브라우저 포그라운드 검사
├── ai_palette.py           # AI 팔레트(이미지 우클릭 "Gemini에게 질문" 질문창) 타겟 목록 — 기본은 Google AI 모드 하나뿐(v1.6x, 실사용 결과 가장 견고했음), URL 빌더·keyword 매칭, 설정창에서 사용자가 추가/편집
├── gif_recorder.py         # GIF 녹화 — 캡처 오버레이 select_only로 영역만 받아 라이브 연속 grab → Pillow 애니메이션 GIF (GifRecorder + 정지 컨트롤러 + encode_gif). GDI 커서 합성(composite_cursor)도 여기 있고 video_recorder.py가 재사용한다. 저수준 조각(sample_cursor·cursor_hotspot·blit_icon_at)은 capture_overlay.py의 정지 캡처 '커서 포함'(Space 토글)도 공유한다.
├── video_recorder.py       # 영상(MP4) 녹화 — GIF와 같은 select_only 흐름을 공유하되 프레임을 cv2.VideoWriter로 즉시 파일에 흘려써 메모리 상한이 없다(VideoRecorder, gif_recorder의 _RecordController·composite_cursor 재사용)
├── stt_engine.py           # 음성 입력(STT) — Recorder(sounddevice 녹음+RMS 음량)·transcribe(게이트웨이 Gemini 계열 화이트리스트, ocr_engine 패턴 재사용)·마이크 장치 목록/기본값 조회
└── ui/
    ├── panel.py            # 전체 클립보드 패널
    ├── image_preview.py    # 이미지 미리보기 팝업 (다중 창 지원, Space로 인라인 주석 편집 진입)
    ├── image_annotator.py  # 이미지 주석 편집기 (QGraphicsScene — 도형·선·화살표·펜·텍스트·번호)
    ├── text_preview.py     # 텍스트 미리보기 팝업
    ├── memo_window.py      # 빠른 메모창 — 메모장 텍스트 항목을 쓰는 동안 자동 저장(일반 창, Esc·Ctrl+W=닫기, Ctrl+T·오른쪽 위 핀=항상 위, Ctrl+휠/Ctrl+0=글자 크기, 우클릭=공통 메뉴)
    ├── toast.py            # 우하단 스택형 토스트 (복사 알림·시작·OCR)
    ├── paste_hud.py        # 순차 붙여넣기 진행 HUD (큐 목록·포인터 실시간)
    ├── settings_dialog.py  # 설정 화면
    ├── ocr_overlay.py      # OCR 영역 선택 오버레이
    ├── capture_overlay.py  # 마그네틱 영역 캡처 오버레이 (Snipaste식 입력-소유 오버레이 — 얼린 최상위창+창-스코프 요소 스냅·자유드래그·크로스모니터 합성. select_only 모드=자르지 않고 사각형만 emit → GIF 녹화용. Space로 '실제 커서 포함' 토글 + 그 Space·ESC를 삼키는 세션 전용 저수준 키보드 훅)
    ├── ai_query.py         # AI 팔레트 질문 입력 다이얼로그 (이미지 우클릭 "Gemini에게 질문"으로만 열림)
    ├── stt_indicator.py    # 음성 입력 녹음 중 표시되는 음량 반응 이퀄라이저 pill (커서 옆, 클릭하면 녹음 종료)
    ├── nav_icons.py        # 설정창 왼쪽 목록 아이콘 4종 (Phosphor 듀오톤, SVG 문자열 내장 — spec datas 불필요)
    ├── menu_style.py       # 우클릭 메뉴 공통 모양 — make_menu()·menu.add(이름, 아이콘, danger=). 항목을 QSS 대신 프록시 스타일이 직접 그림(단축키 흐리게·위험 항목 호버 빨강)
    ├── menu_icons.py       # 우클릭 메뉴 아이콘 22종 (Phosphor 듀오톤, SVG 문자열 내장)
    ├── record_chooser.py   # 녹화 영역 선택 직후 뜨는 [GIF] [영상] 선택 바 (G/V/Enter=지난 선택/ESC)
    └── tray.py             # 시스템 트레이

tests/
├── test_models.py
├── test_database.py
├── test_paste_queue.py
├── test_ocr_engine.py
├── test_web_open.py
├── test_ai_palette.py
├── test_clipboard_monitor.py
├── test_text_preview.py
└── test_crypto.py

tools/
└── make_icon.py

docs/
└── architecture-notes.md  # 모듈별 상세 설명·결정 근거·실패 기록 (Grep으로 해당 항목만 읽기)
```

---

## 아키텍처

### 모듈 요약

> 모듈마다 **지금 상태**만 한두 줄로 적는다. 결정 근거·실측 수치·기각한 대안·실패 기록·옛 동작은 **`docs/architecture-notes.md`**에 모듈별로 있다 — 그 모듈을 고치기 전에 해당 항목을 Grep으로 찾아 읽을 것(통째로 읽지 말 것, 10만 자). 새 결정 근거는 그 문서의 해당 모듈 항목 안에 흡수해 적고, 여기에는 요약이 바뀔 때만 손댄다.

**핵심 흐름**

- **`main.py`** — 오케스트레이션. 모듈 연결, 단일 인스턴스(뮤텍스 + Named Pipe `\\.\pipe\PasteFlow_IPC`), 자동 시작(HKCU `Run` → `wscript.exe` + `%LOCALAPPDATA%\PasteFlow\autostart_launcher.vbs` → `pythonw run.pyw` 또는 exe; `python.exe`/`-m` 등록 금지), 시작 시 마이그레이션(`_migrate_secrets`·`_ORPHAN_KEYS` purge·`_migrate_drop_official_backend` 등), 시크릿(`_SECRET_KEYS`, `_get_secret`). 큐 UI 공통 클리어는 `_clear_queue_ui`, 히스토리 삭제 시 `queue.remove_item`으로 큐도 동기화. 단축키 슬롯: 순차 경로(`_on_seq_image_to_path_hotkey` — 이미지면 임시 PNG 경로를 붙이고 250ms 뒤 원본 이미지로 클립보드 복원), 벌크(`_start_bulk_paste`/`_bulk_paste_step`, 300ms 간격 QTimer 체인, `_bulk_paste_active`로 재진입·취소), 핀(`_on_pin_hotkey`), 캡처(`_on_capture_region`), 녹화(`_on_record_hotkey` → `RecordModeChooser`), STT(`_on_stt_start`/`_on_stt_stop`/`_finish_stt_recording`), OCR(`_start_ocr_worker` — 영역·우클릭 공용, 시작 시 `_prewarm_ocr_connection`). 패널 드래그 붙여넣기 헬퍼(`_activate_and_send_ctrl_v`, 탐색기/바탕화면 저장, `_position_desktop_icon`), 이미지 변환(`_image_data_to_png_bytes`, `_read_image_from_clipboard` — CF_HDROP 이미지 파일 폴백 포함). 진행 칩은 `_start_cursor_progress`/`_finish_cursor_progress`.
- **`clipboard_monitor.py`** — `WM_CLIPBOARDUPDATE` 감시 → `_read_clipboard()`가 항상 리스트 반환(탐색기 다중 이미지 파일은 파일마다 항목). 자체 쓰기는 `mark_self_write`(0.5초 시간창 + `SELF_WRITE_BACKSTOP_SEC` 2초 해시). 직전과 같은 내용은 `DUPLICATE_WINDOW_SEC`(1.5초) 안에서만 중복으로 거른다. OLE·HWP 네이티브 포맷은 `extra_formats`에서 제외(한글→한글 붙여넣기 실패 방지). 썸네일은 PIL 우선, 실패 시 raw DIB로 보고 `_dib_to_bmp`. `is_encoded_image`는 인터셉터와 공유.
- **`paste_queue.py`** — 순차 큐·포인터. 리셋 트리거: 일반 Ctrl+V(`mark_plain_paste`) / idle 만료(`idle_reset_sec`, 기본 10초) / `pointer>0`인 상태의 새 복사. `set_queue`·`clear`·`remove_item`·`undo_last`(테스트만 사용). TDD 대상.
- **`paste_interceptor.py`** — `WH_KEYBOARD_LL` 훅 하나로 모든 전역 단축키 감지(표는 아래 「단축키 체계」). Ctrl+Shift+V는 클립보드 교체 → `_send_clean_key(VK_V)`(수정키 해제·복원 + `VK_MASK` 입력기 전환 방지). 일반 Ctrl+V는 통과시키며 물리 키만 `on_plain_paste`로 알림. suppress한 키는 짝 **물리** keyup도 막음. STT는 keydown·keyup 푸시투토크(`_stt_mod_only`면 Ctrl+Win처럼 수식키만). 설정창 녹화 중엔 `suspend()`로 전부 통과. `_set_clipboard`는 텍스트·HTML·RTF·이미지(PNG면 PNG+DIB, 그 밖의 파일 포맷은 DIB로 변환, raw DIB는 그대로)·`extra_formats` 전부 복원.
- **`database.py`** — SQLite(`clipboard_items` FIFO 50개·고정 제외 / `settings` / 사용 안 하는 `ai_history`). 단일 커넥션 + `_lock`(RLock). `history_order`(DB 전용)로 표시 순서, `bump_history_to_top`으로 복사한 항목을 최상단에(패널 Enter·드래그 붙여넣기는 순서 안 바꿈). summary 쿼리는 컬럼을 명시 나열하므로 새 컬럼은 거기에도 추가할 것. 열 때 빈 페이지가 절반 넘고 5MB 이상이면 `VACUUM`(`_compact_if_bloated`), 그다음 `PRAGMA mmap_size`로 연다(큰 이미지 행 뒤 칸 읽기 140ms → 1ms). 메모장: `create_memo`(맨 위)·`set_locked`·`count_memos`·`clear_memos`(잠긴 것 제외, 지운 id 반환)·`restore_memos`(비우기 되돌리기) — 잠긴 항목은 `delete_item`/`unpin_item`이 False로 거부. TDD 대상.
- **`models.py`** — `ClipboardItem`(… `extra_formats` dict, `saved_image_path` = Alt+F2 캡처 파일 경로, `is_locked` = 메모장 삭제 방지). TDD 대상.
- **`hotkey_manager.py`** — `_SPECIAL_KEY_MAP`(특수 키 이름 → VK) 단일 정의만 남음.
- **`crypto.py`** — DPAPI `protect`/`unprotect`(`enc:v1:` 접두, 멱등, 실패 시 `""`).

**OCR·AI·음성**

- **`ocr_engine.py`** — 게이트웨이(OpenAI 호환 chat.completions) OCR. `OcrEngine(kind="gemini")`, `max_tokens=16384`(thinking 모델 잘림 방지), `_get_client` 클라이언트 캐시 + `warm_connection`, `_normalize_base_url`. 모델 목록(`list_gemini_models`)은 거르지 않음. 설정창 `연결 테스트`용 라이브 프로브(`probe_connection`/`probe_ocr_model`, `ProbeResult` ok/weak/fail/retry — fail과 retry를 섞지 말 것, 프로브는 폴백을 거치지 않음). not-found면 `_FALLBACK_CHAIN`(`gemini-3.5-flash-lite` → `gemini-3.1-flash-lite`)으로 1회 재시도. 계열 묶기 `family_of`/`group_models`. 크레딧 조회 `get_credit_balance`(certifi CA 명시). `STT_FALLBACK_DEFAULT`.
- **`stt_engine.py`** — 음성 입력. `Recorder`(sounddevice 16kHz mono wav, RMS 레벨, 피크 `last_peak_rms`, MME 장치만 나열, 없는 마이크면 기본 장치로 폴백), `transcribe`(Gemini 계열만 오디오 입력 가능), 30초 상한. `SILENCE_RMS_THRESHOLD` 아래면 API를 부르지 않음(무음 환각 방지 — 설정창 마이크 테스트와 같은 상수).
- **`web_open.py`** — 브라우저에서 Google AI 모드 열기(`google_ai_url` / 이미지 주입용 `google_ai_home_url`), 키 주입 전 `is_browser_foreground()` 확인, `open_url`은 기본 브라우저.
- **`ai_palette.py`** — 질문창 타겟 데이터. 지금은 `DEFAULT_SITES`(Google AI 하나)만 쓴다. 편집용 함수는 테스트만 사용.
- **`gdrive.py`** — PasteFlow 앱은 안 씀. `webchat/`이 import하므로 지우지 말 것.
- **`uia.py`** — 마그네틱 캡처용 `rect_in_window_at(hwnd,x,y)`(창-스코프 MSAA `accHitTest` 하강, 최말단 자식 HWND부터, 창 밖 오배치 사각형 보정). 전용 `WinDLL`.

**녹화**

- **`gif_recorder.py`** — `GifRecorder`(select_only 영역을 fps마다 grab, `max_seconds` 상한), `_RecordController`(REC·정지 바), `encode_gif`(워커 스레드), 커서 합성 `composite_cursor`(GDI `DrawIconEx`)와 저수준 조각(`sample_cursor` 등 — 캡처의 '커서 포함'도 공유).
- **`video_recorder.py`** — `VideoRecorder`(cv2 `mp4v`로 매 틱 디스크에 흘려씀, `isOpened()` 실패 시 `RuntimeError`), `extract_first_frame_png`(토스트 썸네일). 의존성 `opencv-python-headless`.
- 녹화 단축키는 하나(`hotkey_record`) → 영역 선택 → `ui/record_chooser.py`의 `[GIF] [영상]` 바(G/V/Enter=지난 선택/ESC). 저장 파일 경로를 클립보드·히스토리에 넣는다.

**UI (`pasteflow/ui/`)**

- **`panel.py`** — 메모장(고정) + 히스토리 패널(검색 없음). 메모장 제목 옆 `+`=새 메모, 제목 우클릭=새 메모·메모장 비우기(확인은 main). 텍스트 `수정`과 메모장 텍스트 항목의 `Space`·더블클릭은 메모창으로 연다(히스토리는 미리보기 — 열기만 하고 붙여넣지 않음). 메모창에서 쓰는 내용은 `update_item_text`로 그 한 줄만 실시간 반영(전체 refresh는 깜빡임). 메모 삭제·메모장 비우기 직후 토스트(2초, 복사 알림과 같음)를 누르면 되돌린다(`db.restore_memos`, 토스트 `on_click`) — 빈 메모창을 닫아 저절로 지울 때는 안 띄움. 잠긴 항목은 글자 칸 안 자물쇠 표시, `l`로 잠금 토글, `Del`·`p`는 막고 토스트. 클릭=선택(코랄), 키(`Ctrl+C`·`c` 큐 토글·`Space`·`Enter` 붙여넣기·`p`·`l`·`s`·`o`·`g`·`Del`·`↑↓`). 단축키 대상 `_kbd_focus_id`는 클릭·방향키로만 정함(hover 금지). 더블클릭 붙여넣기 없음(더블클릭은 열기). fake drag로 외부 앱 붙여넣기(Alt+드래그 이미지=경로 텍스트) 및 재정렬. 자동 닫기 📌(기본 OFF). 항목 최대 5줄(높이 공식은 「설계 규칙」). 항목 더블클릭=열기(붙여넣기는 여전히 없음).
- **`menu_style.py`** / **`menu_icons.py`** — 우클릭 메뉴 공통: `make_menu()`·`menu.add(이름\t단축키, 아이콘, danger=)`. 조건부 항목은 숨기지 말고 비활성. 묶음 순서: 보기·큐 | 꺼내기 | 다루기 | 고정 | 삭제·닫기.
- **`image_preview.py`** — 이미지 미리보기·핀 창(다중 창, `native=True`면 1:1, `place_rect`로 제자리 덮기). 우클릭: 복사·경로 복사·OCR·Gemini 질문·복제·주석 편집·닫기 — 주석이 있으면 `_effective_item()` 평탄화본 대상. Space로 인라인 주석 편집, 툴바는 하단 예약 strip(토글 시 창 크기 불변).
- **`image_annotator.py`** — 주석 편집기(`_EditorMixin`·`_AnnotatorView`·도형 아이템 + `_HandleResizeMixin`). 도구 1~8(수식키 없이), 도구별 우클릭 미니패널(색·크기·화살표 머리/방향, DB `annot_tool_defaults`), 3차 베지어 화살표 + 도형 테두리 스냅, 크기조절(우하단)·회전(좌상단) 핸들(잡기 판정 24px), `flatten_scene_to_png`.
- **`text_preview.py`** — 평문 미리보기(`QPlainTextEdit`, 스크롤 없이 전부 보이게 크기 계산, 우클릭 전체 복사·수정(→메모창)·닫기).
- **`memo_window.py`** — 빠른 메모창(일반 창). 0.5초 멈추면 저장, 닫을 때 저장, 빈 채로 닫으면 삭제. Esc·Ctrl+W 닫기, Ctrl+T·오른쪽 위 핀 버튼 항상 위(창마다, 저장 안 함 — 전용 WinDLL `SetWindowPos`), 우클릭은 `make_menu`(Qt 기본 메뉴는 창 배경을 물려받아 글자가 묻혔음), Ctrl+휠·Ctrl+0 글자 크기. 글자 크기·마지막 창 자리(`saveGeometry`)는 DB `memo_window_prefs`(JSON)에 저장돼 다음에도 그대로 연다(`set_prefs`/`prefs_saver`) — 단, 마지막 자리는 마우스가 있는 모니터로 옮겨 쓴다(그 모니터 안 상대 위치·크기 유지, `_move_to_cursor_screen`). 열 때 `AttachThreadInput`으로 포그라운드 잠금을 우회해 맨 앞에 뜨고, Windows 열림 효과(~0.3초)는 `DWMWA_TRANSITIONS_FORCEDISABLED`로 끈다. DB는 시그널로 main에 넘김. `_quit`에서 `close_all()`을 `db.close()`보다 먼저.
- **`toast.py`** — 우하단 스택 토스트(주 모니터, 최대 5개, 클릭 시 빠른 닫기 — `on_click`을 주면 클릭 때 한 번 호출), `image_path`/`image_bytes` 썸네일, 커서 앵커·중앙 칩(`anchor`, `center=True`, 클릭 통과), 지속형(`duration_ms=0` + `set_message`/`dismiss`). 복사 알림은 `Q{n}` 배지 + 썸네일(원본 `image_data` 우선), 아이콘 없음.
- **`paste_hud.py`** — 순차 붙여넣기 진행 HUD(비활성 창, ✓▶·, ✕ 취소 → `_on_cancel_paste_queue`).
- **`settings_dialog.py`** — 왼쪽 목록 내비(`일반`/`붙여넣기`/`캡처·녹화`/`AI`, 실제 페이지는 탭 바를 숨긴 `QTabWidget`), 기능 카드마다 그 기능의 단축키·옵션. AI 탭: `AI 연결 (OpenAI 호환 API)`(Base URL·API 키·모델조회·연결 테스트 = 연결+OCR 모델+크레딧), OCR, 음성 입력(STT 모델은 Gemini만, 마이크 테스트). 창-모달(`WindowModal`) + `_open_settings` 재진입 가드. 녹화 중 훅 정지는 `recording_active` → `done()`/`closeEvent`에서도 해제. 모델 콤보는 editable이라 표시 텍스트 = 저장값(계열 헤더는 비활성, 들여쓰기는 델리게이트로). 디자인 비교 하네스 `tools/settings_bakeoff.py`.
- **`nav_icons.py`** — 설정창 아이콘(SVG 문자열 내장 — spec datas 불필요).
- **`capture_overlay.py`** — 마그네틱 영역 캡처(아래 「캡처 동작」). **`ocr_overlay.py`** — OCR 영역 선택(모니터별 위젯). **`record_chooser.py`** — 녹화 종류 선택 바. **`ai_query.py`** — 이미지 우클릭 "Gemini에게 질문" 입력창(비모달·TOPMOST, 이미지 1장, Tab 순환·키워드 접두어). **`stt_indicator.py`** — 녹음 중 이퀄라이저 pill(클릭=정지). **`tray.py`** — 트레이(좌클릭 패널, 우클릭 패널·설정·종료).

### 캡처 동작 (Alt+F2 · Alt+F3)

- **영역 캡처** — 오버레이가 시작 전에 최상위 창 목록을 Z-order로 얼리고, 커서가 움직인 tick에만 그 창 안을 `uia.rect_in_window_at`로 하강해 요소를 하이라이트한다(비클라이언트 영역=창 전체). 좌클릭=요소/창, 4px 이상 드래그=자유 사각형, 우클릭·ESC=취소, Space=실제 커서 포함(오버레이가 뜨기 전에 얼린 커서를 굽는다). 여러 모니터에 걸치면 가장 높은 DPR로 합성. 결과는 DIB로 클립보드+히스토리+큐, 캡처 폴더(`capture_save_folder`)에 PNG 저장(`saved_image_path`), 복사와 같은 토스트. `PrintScreen` 단독 입력도 같은 동작(설정, 기본 켜짐).
- **핀** — 큐에 다음 항목이 있으면 그걸(순차 붙여넣기와 포인터 공유), 없으면 클립보드 이미지/텍스트(텍스트는 PNG로 렌더)를 핀. 방금 캡처한 항목이면 캡처 자리에 1:1로 덮고, 클립보드가 직전 순차 경로 붙여넣기의 임시 PNG 경로면 그 이미지로 핀한다. 복제는 핀 우클릭.

### 꼭 지킬 함정 (실측으로 확인된 것)

자세한 경위는 `docs/architecture-notes.md`에서 각 키워드로 찾는다.

- **워커 스레드에서 Qt 타이머·위젯을 건드리지 않는다** — Python 스레드엔 Qt 이벤트 루프가 없어 `QTimer.singleShot`이 영영 발화하지 않는다(2026-10-03 실측). 결과는 `_SignalBridge` 시그널로 메인 스레드에 넘긴다.
- **ctypes `argtypes`를 공유 `ctypes.windll.user32`에 걸지 않는다** — 모듈끼리 서로 덮어써 `ArgumentError`가 난다. 전용 `ctypes.WinDLL("user32")` 인스턴스를 쓴다(`uia`·`gif_recorder`·`record_chooser`).
- **suppress한 단축키는 짝 물리 keyup도 막고, 주입(`LLKHF_INJECTED`) keyup은 통과시킨다** — 아니면 브라우저 버튼이 Space keyup에 클릭되거나, 주입한 V가 눌린 채 남는다.
- **수정키가 눌린 채 일반키를 삼키면 "벌거벗은 수정키"로 오인된다**(Ctrl+Shift → 입력기 전환, Alt → 메뉴바, Win → 시작 메뉴). 미할당 키 `VK_MASK`를 눌렀다 떼서 막는다. 수정키가 눌린 상태에서 Ctrl+V를 주입할 땐 `_send_clean_key`/`_release_modifiers_and_send_ctrl_v`를 쓴다.
- **키 주입 전에 포그라운드를 확인한다** — 엉뚱한 창(원격 데스크톱 포함)에 Ctrl+V·Enter가 들어간다(`web_open.is_browser_foreground`).
- **우클릭 메뉴는 부모 없이 만들고 QSS를 걸지 않는다** — QSS가 하나라도 걸리면 프록시 스타일(단축키 흐림·위험 호버)이 무시된다.
- **`QWidget` 서브클래스에 배경·테두리 QSS를 주려면 `WA_StyledBackground`를 켠다.** 설정창은 부모 패널의 선택자 없는 배경 규칙을 물려받으므로 `DIALOG_STYLE` 맨 앞의 투명 규칙을 지우지 말고, 확인 렌더도 패널 스타일 부모 아래에서 한다.
- **설정창 모델 콤보는 표시 텍스트가 곧 저장값** — 헤더는 비활성, 텍스트에 공백·배지·불릿을 넣지 않는다.
- **비활성(`WA_ShowWithoutActivating`) TOPMOST 창은 `raise_()`만으로 다른 TOPMOST(패널)를 못 이긴다** — 캡처 오버레이처럼 `SetWindowPos(HWND_TOPMOST)`를 명시하고 한 번 더 재확인한다.
- **떠 있는 보조 패널은 `Qt.Popup`이 아니라 `Tool` + `WA_ShowWithoutActivating`** — Popup은 휠 등 마우스를 그랩한다.
- **캡처 세션의 Space·ESC 억제 훅으로 삼킨 키는 `GetAsyncKeyState`에 안 잡힌다** — 판정은 훅 콜백에서 한다.
- **`explorer /select,` 경로는 `os.path.normpath`로 정규화** — 슬래시가 섞이면 엉뚱한 폴더가 열린다.
- **`cv2.VideoWriter`는 실패해도 예외가 없다** — `isOpened()`를 확인한다.
- **`_dib_to_bmp`는 BI_BITFIELDS면 마스크 12바이트를 오프셋에 더한다** — 빠뜨리면 색이 뒤집힌다.
- **게이트웨이 `/models` 목록에도 404인 유령 모델이 있다** — 모델 이름 상수(폴백 사슬 등)는 실호출로 확인한다.
- **마이그레이션 순서** — `_migrate_split_ocr_ai_model`은 `_migrate_drop_official_backend`보다 먼저.

### 단축키 체계

| 단축키 | 동작 | 감지 방식 |
|--------|------|-----------|
| Ctrl+Shift+V | 순차 붙여넣기 (suppress) | WH_KEYBOARD_LL (paste_interceptor) |
| ctrl+space *(기본값, 설정 가능)* | 패널 토글 (suppress) | WH_KEYBOARD_LL (paste_interceptor) |
| ctrl+shift+s *(기본값, 설정 가능)* | OCR 영역 선택 시작 (suppress) | WH_KEYBOARD_LL (paste_interceptor) |
| ctrl+shift+p *(기본값, 설정 가능 — 2026-09-25부터; 예전엔 ctrl+shift+[)* | 순차 경로 붙여넣기 — 큐에서 다음 항목을 꺼내 이미지면 경로 텍스트로(Ctrl+Shift+V와 큐 공유) (suppress). ⚠ 옛 '한 번짜리' 이미지→경로(최신 히스토리 이미지, 옛 기본 ctrl+shift+p)는 2026-09-25 제거 | WH_KEYBOARD_LL (paste_interceptor) |
| ctrl+shift+a *(기본값, 설정 가능 — 2026-09-06 도입)* | 순차 붙여넣기 전체 자동주입 — Ctrl+Shift+V의 '벌크' 버전. 큐에 남은 항목 전체를 간격(300ms) 두고 순서대로 자동 Ctrl+V(같은 큐 공유) (suppress) | WH_KEYBOARD_LL (paste_interceptor) — 감지만, 반복 주입 루프는 main._bulk_paste_step |
| ctrl+shift+[ *(기본값, 설정 가능 — 2026-09-25부터; 예전엔 ctrl+shift+])* | 순차 경로 붙여넣기 전체 자동주입 — Ctrl+Shift+P의 '벌크' 버전. 이미지는 경로 텍스트로, 그 외는 원본 그대로 순서대로 자동 주입 (suppress) | WH_KEYBOARD_LL (paste_interceptor) — 감지만, 반복 주입 루프는 main._bulk_paste_step |
| alt+f3 *(기본값, 설정 가능)* | 핀 — 순차 큐에 다음 항목이 있으면 그걸(Ctrl+Shift+V와 큐 공유), 없으면 클립보드 이미지/텍스트를 화면에 떠 있는 창으로 띄우기. 2026-09-25 옛 순차 핀(Alt+Shift+F3)을 흡수 (suppress) | WH_KEYBOARD_LL (paste_interceptor) |
| alt+\` *(기본값, 설정 가능 — 2026-10-05 도입)* | 새 메모 — 메모장 맨 위에 새 메모를 만들고 메모창을 연다 (suppress) | WH_KEYBOARD_LL (paste_interceptor) |
| alt+f2 *(기본값, 설정 가능)* | 영역 캡처 → 클립보드(DIB)+파일 저장 (suppress). 오버레이 안에서 Space=실제 커서 포함 토글 | WH_KEYBOARD_LL (paste_interceptor) |
| PrintScreen *(설정 체크박스, 기본 켜짐 — 2026-08-12 도입, 2026-08-25 기본값 켜짐으로 전환)* | 영역 캡처(Alt+F2)의 대체 트리거 — 수식키(Ctrl/Shift/Alt/Win) 없이 단독으로 눌렸을 때만 동일한 `on_capture` 콜백 발동, Alt+PrtScn·Win+PrtScn 등 OS 조합은 그대로 통과. 켜면 OS 기본 PrtScn(전체화면 클립보드 복사) 동작을 suppress로 대체 | WH_KEYBOARD_LL (paste_interceptor) |
| ctrl+shift+r *(기본값, 설정 가능 — 2026-09-25 GIF·영상 통합)* | 녹화 — 영역 선택 후 뜨는 [GIF] [영상] 바에서 골라 라이브 녹화 → 파일 저장·경로 복사 (G/V/Enter=지난 선택/ESC) (suppress) | WH_KEYBOARD_LL (paste_interceptor) |
| ctrl+win *(기본값, 설정 가능)* | 음성 입력(STT) 푸시투토크 — 누르는 동안 녹음, 떼면 인식 후 자동 붙여넣기 (keydown+keyup 처리, suppress 안 함) | WH_KEYBOARD_LL (paste_interceptor) |
| 트레이 좌클릭 | 패널 토글 | Qt 이벤트 |

> ⚠️ `Alt+1~9` 직접 붙여넣기, `Ctrl+Shift+X` 큐 초기화, `Ctrl+Shift+Z` 실수 복구는 **의도적으로 제거**됨.

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
5. **OCR 결과를 클립보드에 넣을 때 `_self_triggered` 플래그 설정을 누락하지 않는다** — `interceptor._set_clipboard(item)` 호출이 내부적으로 `monitor.mark_self_write(item)`를 처리하므로 반드시 이 경로를 사용할 것. 직접 `win32clipboard`를 쓰면 클립보드 모니터가 재감지해 동일 항목이 큐에 중복 추가됨. `mark_self_write`는 **시간창(0.5초 `_ignore_until`)과 해시 백스톱(`_last_hash`)을 함께** 건다 — 직접 저장 경로(`_persist_clipboard_item`)는 `_last_hash`를 갱신하지 않아, 늦게 도착한 `WM_CLIPBOARDUPDATE`가 0.5초 창을 넘기면 해시 방어가 무력이라 히스토리에 이중 저장됐다(Alt+F2 캡처 등에서 간헐 재현). `mark_self_write`가 쓴 항목의 해시를 미리 등록해 늦은 이벤트도 걸러낸다.
6. 요청하지 않은 기능 임의 추가 또는 수정.
7. 여러 기능 동시 구현.
8. TDD 대상 모듈에서 테스트 없이 구현.
9. 다른 모듈에 영향을 줄 수 있는 변경을 사전 보고 없이 진행.

### 반드시 지켜야 할 것

1. **클립보드 교체 방식만 사용** — Ctrl+Shift+V 키다운 시점에 `win32clipboard`로 클립보드 내용을 교체하고, Ctrl+V를 SendInput으로 주입한다.
2. **`_self_triggered` 플래그** — PasteFlow가 클립보드에 쓸 때 반드시 이 플래그를 설정하여 자체 모니터가 재감지하지 않도록 한다.
3. **모든 클립보드 형식 보존** — 텍스트만이 아니라 HTML, RTF, 이미지 등 원본 형식을 그대로 클립보드에 복원해야 노션 등에서 서식이 유지된다.
4. **패널 드래그 → 외부 앱 붙여넣기 방식 (앱 종류에 따라 분기)**
   - **이미지 항목 + Explorer(`CabinetWClass`) / 바탕화면(`Progman`, `WorkerW`)**: `_save_image_to_folder()`로 PNG 파일 저장. 서브폴더 아이콘 위 드롭 시 해당 폴더에 저장(크로스 프로세스 `LVM_HITTEST`). 저장 성공 시 클립보드 경로 생략. 바탕화면은 저장 직후 `_position_dropped_desktop_icon()`이 `LVM_SETITEMPOSITION32`로 저장된 파일 아이콘을 드롭 좌표로 재배치(가짜 드래그라 Explorer가 실제 드롭 좌표를 몰라 기본 배치를 쓰던 문제 보완 — "아이콘 자동 정렬" 켜짐 시 무효, 자세한 내용은 `docs/architecture-notes.md`의 `main.py` 항목).
   - **Win32/WinUI3 앱** (메모장 등): `SendMessage(hwnd, WM_PASTE, 0, 0)`. 흐름: fake drag(DragCopyCursor) → 마우스 업 시 `_set_clipboard` → 재귀적 `ChildWindowFromPoint`로 최하위 자식 컨트롤 탐색 → `SendMessage(WM_PASTE)`.
   - **Electron/Chromium 앱** (노션, Slack 등): `AttachThreadInput` + `SetForegroundWindow` + `SendInput(Ctrl+V)`. 창 클래스명(`Chrome_*`, `CEF*` 등)으로 판별. 금지 항목 4의 예외에 해당.
5. **`_SPECIAL_KEY_MAP`은 `hotkey_manager.py`에 단일 정의** — `paste_interceptor.py`에서 import해 재사용. 중복 정의 금지.
6. **시크릿은 DPAPI로만 저장** — DB에 평문 API 키·토큰을 직접 넣지 않는다. 새 시크릿 키를 추가하면 `main.py`의 `_SECRET_KEYS` 화이트리스트에 등록하고, 쓰기는 `crypto.protect()`, 읽기는 `self._get_secret()` 헬퍼를 사용한다. DPAPI blob은 현재 Windows 계정에만 묶여 타 PC 복호화 불가 — 이로 인해 다중 PC 자동 동기화는 폐기됐다(설정은 PC별 독립).
