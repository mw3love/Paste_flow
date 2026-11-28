"""PasteFlow 진입점 — 모듈 오케스트레이션

클립보드 모니터 → DB → 큐 → UI 간 이벤트 흐름 관리.
기능별 메서드는 pasteflow/app/의 믹스인 파일에 있다(2026-11-16 폴더 구조 2편).
"""
import ctypes
import ctypes.wintypes
import json
import sys
import threading

from PyQt6.QtCore import QRect, QTimer
from PyQt6.QtWidgets import QApplication

from pasteflow.clipboard_monitor import ClipboardMonitor
from pasteflow.database import Database
from pasteflow.paste_interceptor import PasteInterceptor
from pasteflow.paste_queue import PasteQueue
from pasteflow.ui.image_annotator import _EditorMixin
from pasteflow.ui.image_preview import ImagePreviewPopup
from pasteflow.ui.memo_window import MemoWindow
from pasteflow.ui.panel import ClipboardPanel
from pasteflow.ui.paste_hud import PasteHud
from pasteflow.ui.text_preview import TextPreviewPopup
from pasteflow.ui.theme import COLORS
from pasteflow.ui.tray import TrayIcon
from pasteflow.app.ai_browser import AiBrowserMixin
from pasteflow.app.autostart import AutoStartMixin
from pasteflow.app.capture import CaptureMixin
from pasteflow.app.clipboard_flow import ClipboardFlowMixin
from pasteflow.app.data_paths import _resolve_db_path
from pasteflow.app.drag import DragMixin
from pasteflow.app.image_items import ImageItemMixin
from pasteflow.app.items import ItemActionsMixin
from pasteflow.app.lifecycle import LifecycleMixin
from pasteflow.app.live_draw import LiveDrawMixin
from pasteflow.app.ocr import OcrMixin
from pasteflow.app.panel import PanelMixin
from pasteflow.app.pin import PinMixin
from pasteflow.app.preview import PreviewMixin
from pasteflow.app.progress import CursorProgressMixin
from pasteflow.app.recording import RecordingMixin
from pasteflow.app.settings import SettingsMixin
from pasteflow.app.signals import _SignalBridge
from pasteflow.app.stt import SttMixin


class PasteFlowApp(
    ClipboardFlowMixin,
    CaptureMixin,
    RecordingMixin,
    CursorProgressMixin,
    OcrMixin,
    ImageItemMixin,
    PinMixin,
    LiveDrawMixin,
    SttMixin,
    PanelMixin,
    ItemActionsMixin,
    PreviewMixin,
    AiBrowserMixin,
    DragMixin,
    SettingsMixin,
    AutoStartMixin,
    LifecycleMixin,
):
    """PasteFlow 앱 오케스트레이션"""

    def __init__(self):
        self.app = QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)
        # 다크 테마용 툴팁 스타일 (시스템 기본은 다크 배경 위 어두운 글자라 가독성 0)
        self.app.setStyleSheet(
            f"QToolTip {{"
            f" background-color: {COLORS['mantle']};"
            f" color: {COLORS['text']};"
            f" border: 1px solid {COLORS['surface1']};"
            f" padding: 4px 6px;"
            f"}}"
        )

        # 스레드 안전 시그널 브릿지
        self._bridge = _SignalBridge()
        self._bridge.paste_happened.connect(self._update_paste_ui)
        self._bridge.new_item_saved.connect(self._on_new_item_ui)
        self._bridge.copy_toast.connect(self._on_copy_toast)
        self._bridge.panel_toggle.connect(self._toggle_panel)
        self._bridge.paste_queue_done.connect(self._on_paste_queue_done)
        self._bridge.ocr_requested.connect(self._on_ocr_requested)
        self._bridge.ocr_done.connect(self._on_ocr_done)
        self._bridge.ocr_translated.connect(self._on_ocr_translated)
        self._bridge.ocr_error.connect(self._on_ocr_error)
        self._bridge.ocr_fallback.connect(self._on_ocr_fallback)
        self._bridge.plain_paste.connect(self._on_plain_paste)
        self._bridge.seq_image_to_path.connect(self._on_seq_image_to_path_hotkey)
        self._bridge.bulk_paste.connect(self._on_bulk_paste_hotkey)
        self._bridge.bulk_path_paste.connect(self._on_bulk_path_paste_hotkey)
        self._bridge.pin_image.connect(self._on_pin_hotkey)
        self._bridge.new_memo.connect(lambda: self._open_memo())
        self._bridge.live_draw.connect(self._on_live_draw_hotkey)
        self._bridge.capture_requested.connect(self._on_capture_requested)
        self._bridge.record.connect(self._on_record_hotkey)
        self._bridge.gif_saved.connect(self._on_gif_saved)
        self._bridge.gif_error.connect(self._on_gif_error)
        self._bridge.annotation_copied.connect(self._on_annotation_copied)
        self._bridge.panel_paste_done.connect(
            lambda: setattr(self.panel, '_paste_in_progress', False))
        self._bridge.stt_start.connect(self._on_stt_start)
        self._bridge.stt_stop.connect(self._on_stt_stop)
        self._bridge.stt_done.connect(self._on_stt_done)
        self._bridge.stt_error.connect(self._on_stt_error)

        self._saved_panel_geometry = None
        self._image_preview_windows: dict[int, ImagePreviewPopup] = {}
        self._text_preview_windows: dict[int, TextPreviewPopup] = {}

        # 코어 모듈
        db_path = _resolve_db_path()
        self.db = Database(db_path)

        # OCR 첫 호출 지연 완화: 실측 결과 `openai` 패키지 임포트(1.4~3.4초, httpx/pydantic
        # 등 무거운 의존성) + 매 호출 새로 만드는 TCP/TLS 커넥션이 합쳐져 첫 OCR이 유독
        # 느렸다(사용자 실측: 첫 8~9초 vs 이후 2~3초). ocr_engine의 클라이언트 캐시
        # (`_get_client`)가 커넥션 재사용을 맡고, 여기서는 앱 시작 직후 백그라운드
        # 스레드로 그 캐시를 미리 데워둔다(임포트 + 클라이언트 생성 + 가벼운 `/models`
        # GET 1회로 DNS/TCP/TLS까지 끝냄) — 실제 첫 OCR이 이 캐시된 커넥션을 그대로
        # 재사용하므로 커넥션 설정 비용 없이 모델 응답 시간만 남는다. 크리덴셜이 비어
        # 있거나 워밍업이 실패해도 조용히 무시(최선 노력형, 실제 OCR 호출엔 영향 없음).
        threading.Thread(target=self._prewarm_ocr_connection, daemon=True, name="ocr-prewarm").start()

        # 주석 편집기 도구별 마지막 값(색·두께·글자·번호·화살표 머리크기)을 DB에서 복원 +
        # 변경 시 저장 콜백 등록(재시작 후에도 유지 — 클래스 변수라 세션 중 이미지 간 공유는 그대로).
        _EditorMixin.load_last_values(self.db.get_setting("annot_tool_defaults", ""))
        _EditorMixin._persist_cb = self._save_annot_tool_defaults
        self.queue = PasteQueue()
        self.monitor = ClipboardMonitor(
            on_new_item=self._on_new_clipboard_item,
            on_duplicate=self._on_duplicate_clipboard_item,
        )
        self.interceptor = PasteInterceptor(
            paste_queue=self.queue,
            clipboard_monitor=self.monitor,
            on_paste=self._on_paste_from_hook,
            get_full_item=self.db.get_item,
            on_toggle_panel=self._bridge.panel_toggle.emit,
            on_ocr_trigger=self._bridge.ocr_requested.emit,
            on_plain_paste=self._bridge.plain_paste.emit,
            on_seq_image_to_path=self._bridge.seq_image_to_path.emit,
            on_bulk_paste=self._bridge.bulk_paste.emit,
            on_bulk_path_paste=self._bridge.bulk_path_paste.emit,
            on_pin_image=self._bridge.pin_image.emit,
            on_new_memo=self._bridge.new_memo.emit,
            on_live_draw=self._bridge.live_draw.emit,
            on_capture=self._bridge.capture_requested.emit,
            on_record=self._bridge.record.emit,
            on_stt_start=self._bridge.stt_start.emit,
            on_stt_stop=self._bridge.stt_stop.emit,
        )

        # UI (패널이 기본 UI — 미니창 없음)
        self.panel = ClipboardPanel()
        # 네이티브 윈도우 핸들을 미리 생성 (show/hide 없이, 깜빡임 방지)
        self.panel.winId()
        self.tray = TrayIcon()

        # 순차 붙여넣기 진행 HUD (단일 인스턴스 재사용)
        self.paste_hud = PasteHud()
        self.paste_hud.winId()
        self.paste_hud.cancel_requested.connect(self._on_cancel_paste_queue)

        # OCR 오버레이
        from pasteflow.ui.ocr_overlay import OcrOverlay
        self._ocr_overlay = OcrOverlay()
        self._ocr_overlay.region_captured.connect(self._on_ocr_region_captured)
        # cancelled 시그널은 내부 close()로 충분 — 별도 콜백 불필요

        # 영역 캡처 오버레이 (마그네틱 — 커서 아래 요소 클릭 캡처. region_captured → _on_capture_region 무수정)
        from pasteflow.ui.capture_overlay import CaptureOverlay
        self._capture_overlay = CaptureOverlay()
        self._capture_overlay.region_captured.connect(self._on_capture_region)
        # GIF 녹화: 같은 오버레이의 select_only 모드로 사각형만 받아 라이브 녹화 시작
        self._capture_overlay.region_selected.connect(self._on_record_region_selected)

        # GIF 녹화기 — 녹화 중 참조 유지(GC 방지), 인코딩은 전용 워커에서 UI 블로킹 방지
        from concurrent.futures import ThreadPoolExecutor
        self._gif_recorder = None
        self._gif_executor = ThreadPoolExecutor(max_workers=1)
        # 영상(MP4) 녹화기 — GIF와 같은 select_only 흐름을 공유하되 프레임을 즉시 파일에
        # 써서 별도 인코딩 워커가 필요 없다(video_recorder.py). GIF/영상은 영역 선택 직후
        # 뜨는 선택 바(RecordModeChooser)로 고른다 — 참조 유지(GC 방지).
        self._video_recorder = None
        self._record_chooser = None
        # 마지막 캡처 위치(논리 전역) — 그 직후 핀(Alt+F3)이 캡처 자리에 그대로 덮게 함.
        # 외부 복사가 들어오면 무효화(_on_new_clipboard_item)해 "방금 캡처한 그 이미지"일 때만 적용.
        self._pin_place_rect: QRect | None = None
        # 그 캡처가 저장된 히스토리 항목 id — 핀이 큐에서 이 항목을 꺼낼 때만 제자리 덮기.
        self._pin_place_item_id: int | None = None
        # 라이브 화면 판서 창(켜져 있을 때만)과 앱 쓰기 상태의 「판서 중」 칩 — app/live_draw.py
        self._live_draw = None
        self._live_draw_boards = {}   # 모니터 이름 → 판서 창(Esc로 숨긴 것 포함)
        self._live_draw_chip = None

        # OCR은 호출마다 새 스레드 — asyncio.run()을 재사용 스레드에서 반복 호출 시
        # WinRT 콜백 상태가 누적돼 두 번째 호출부터 빈 결과를 반환하는 문제 방지

        # 패널 열기 전 포커스된 윈도우 추적 (SetWinEventHook으로 연속 갱신)
        self._prev_foreground_hwnd = None
        self._fg_hook = None
        self._fg_proc = None

        # 직전 순차 경로 붙여넣기(Ctrl+Shift+P·Ctrl+Shift+[)로 클립보드에 올린 임시 PNG 경로.
        # Alt+F3 핀이 이 경로가 클립보드에 남아 있으면 경로 문자열이 아니라 원본 이미지를 핀한다.
        self._last_pasted_image_path: str | None = None

        # 벌크(전체 자동주입) 진행 중 플래그 — Ctrl+Shift+A/Ctrl+Shift+[ 둘이 공유.
        # 재진입(같은 키 연타·다른 벌크 키 겹눌림) 방지 + HUD ✕ 취소 시 루프를 멈추는 신호로도 쓴다.
        self._bulk_paste_active: bool = False

        # 음성 입력(STT) — Recorder는 첫 사용 시 지연 생성(sounddevice를 안 쓰는 세션에서
        # 임포트 비용을 피함), 단축키 keydown~keyup 사이 재사용.
        self._stt_recorder = None
        self._stt_auto_stop_timer = None  # 30초 상한 타이머 — keyup으로 정상 종료 시 취소
        self._stt_indicator = None        # 녹음 중 이퀄라이저 pill (ui/stt_indicator.py)
        self._stt_level_timer = None      # 위 pill에 음량을 흘려주는 폴링 타이머

        # DB에서 설정 로드 및 적용
        self._apply_settings_from_db()

        # 시그널 연결
        self._connect_signals()

    def _connect_signals(self):
        """모든 시그널 연결"""
        self.tray.quit_requested.connect(self._quit)
        self.tray.panel_toggle_requested.connect(self._toggle_panel)
        self.tray.settings_requested.connect(self._open_settings)
        self.tray.live_draw_requested.connect(self._on_tray_live_draw)

        self.panel.paste_item_requested.connect(self._on_panel_paste)
        self.panel.pin_item_requested.connect(self._on_pin_item)
        self.panel.unpin_item_requested.connect(self._on_unpin_item)
        self.panel.delete_item_requested.connect(self._on_delete_item)
        self.panel.pin_reorder_requested.connect(self._on_pin_reorder)
        self.panel.history_reorder_requested.connect(self._on_hist_reorder)
        self.panel.open_memo_requested.connect(self._open_memo)
        self.panel.new_memo_requested.connect(lambda: self._open_memo())
        self.panel.toggle_lock_requested.connect(self._on_toggle_lock)
        self.panel.clear_memos_requested.connect(self._on_clear_memos)
        self.panel.locked_notice.connect(self._on_locked_notice)
        self.panel.preview_image_requested.connect(self._on_preview_image)
        self.panel.preview_text_requested.connect(self._on_preview_text)
        self.panel.copy_image_as_path_requested.connect(self._on_copy_image_as_path)
        self.panel.ask_ai_item_requested.connect(self._on_ask_ai_item_by_id)
        self.panel.ocr_item_requested.connect(self._on_ocr_item_by_id)
        self.panel.translate_item_requested.connect(self._on_translate_item_by_id)
        self.panel.open_file_location_requested.connect(self._on_open_file_location)
        self.panel.open_settings_requested.connect(self._open_settings)
        self.panel.quit_requested.connect(self._quit)
        self.panel.clear_history_requested.connect(self._on_clear_history)
        self.panel.drag_to_app_requested.connect(self._on_drag_to_app)
        self.panel.queue_select_requested.connect(self._on_queue_select)
        self.panel.queue_deselect_requested.connect(self._on_queue_deselect)
        self.panel.queue_append_ids_requested.connect(self._on_queue_append_ids)
        self.panel.queue_toggle_requested.connect(self._on_queue_toggle)
        self.panel.queue_reverse_requested.connect(self._on_queue_reverse)
        self.panel.auto_close_changed.connect(self._on_auto_close_changed)

        panel_hotkey = self.db.get_setting("hotkey_panel_toggle", "ctrl+space")
        self.interceptor.set_panel_hotkey(panel_hotkey)

        ocr_hotkey = self.db.get_setting("hotkey_ocr_trigger", "ctrl+shift+s")
        self.interceptor.set_ocr_hotkey(ocr_hotkey)

        seq_img2path_hotkey = self.db.get_setting("hotkey_seq_image_to_path", "ctrl+shift+p")
        self.interceptor.set_seq_image_to_path_hotkey(seq_img2path_hotkey)

        bulk_paste_hotkey = self.db.get_setting("hotkey_bulk_paste", "ctrl+shift+a")
        self.interceptor.set_bulk_paste_hotkey(bulk_paste_hotkey)

        bulk_path_paste_hotkey = self.db.get_setting("hotkey_bulk_path_paste", "ctrl+shift+[")
        self.interceptor.set_bulk_path_paste_hotkey(bulk_path_paste_hotkey)

        pin_hotkey = self.db.get_setting("hotkey_pin_image", "alt+f3")
        self.interceptor.set_pin_hotkey(pin_hotkey)

        self.interceptor.set_memo_hotkey(self.db.get_setting("hotkey_new_memo", "alt+`"))
        self.interceptor.set_live_draw_hotkey(self.db.get_setting("hotkey_live_draw", "ctrl+shift+d"))

        # 메모창 글자 크기·마지막 창 자리 — 메모창이 바꿀 때마다 저장, 시작 시 되살림
        try:
            MemoWindow.set_prefs(json.loads(self.db.get_setting("memo_window_prefs", "{}")))
        except (ValueError, TypeError):
            pass
        MemoWindow.prefs_saver = lambda prefs: self.db.set_setting(
            "memo_window_prefs", json.dumps(prefs))

        capture_hotkey = self.db.get_setting("hotkey_capture", "alt+f2")
        self.interceptor.set_capture_hotkey(capture_hotkey)

        capture_use_printscreen = self.db.get_setting("capture_use_printscreen", "1") == "1"
        self.interceptor.set_capture_via_printscreen(capture_use_printscreen)

        record_hotkey = self.db.get_setting("hotkey_record", "ctrl+shift+r")
        self.interceptor.set_record_hotkey(record_hotkey)

        stt_hotkey = self.db.get_setting("hotkey_stt", "ctrl+win")
        self.interceptor.set_stt_hotkey(stt_hotkey)

    def run(self):
        self.monitor.start()
        self.interceptor.start()
        self._start_ipc_server()
        self._start_foreground_tracker()
        self.tray.show()

        self._msg_timer = QTimer()
        self._msg_timer.timeout.connect(self._pump_messages)
        self._msg_timer.start(1)

        # 시작 알림 토스트
        def _show_startup_toast():
            from pasteflow.ui.toast import ToastNotification
            self._startup_toast = ToastNotification("PasteFlow 시작됨  ·  Ctrl+Shift+V로 붙여넣기")

        QTimer.singleShot(500, _show_startup_toast)

        return self.app.exec()

    def _pump_messages(self):
        msg = ctypes.wintypes.MSG()
        while ctypes.windll.user32.PeekMessageW(
            ctypes.byref(msg), None, 0, 0, 1
        ):
            ctypes.windll.user32.TranslateMessage(ctypes.byref(msg))
            ctypes.windll.user32.DispatchMessageW(ctypes.byref(msg))


def main():
    # 단일 인스턴스 보장 — Windows 뮤텍스
    _mutex = ctypes.windll.kernel32.CreateMutexW(None, True, "PasteFlow_SingleInstance")
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        ctypes.windll.kernel32.CloseHandle(_mutex)
        # 실행 중인 인스턴스에 패널 토글 신호 전송
        try:
            import win32file
            h = win32file.CreateFile(
                r"\\.\pipe\PasteFlow_IPC",
                win32file.GENERIC_WRITE, 0, None,
                win32file.OPEN_EXISTING, 0, None,
            )
            win32file.CloseHandle(h)
        except Exception:
            pass
        sys.exit(0)

    app = PasteFlowApp()
    app._single_instance_mutex = _mutex  # GC 방지 — 프로세스 종료 시까지 유지
    sys.exit(app.run())


if __name__ == "__main__":
    main()
