"""설정 불러오기·설정 창·설정 바뀜 반영 — pasteflow/main.py에서 글자 그대로 옮김(2026-11-16 폴더 구조 2편)."""

import json

from PyQt6.QtCore import Qt

from pasteflow.ui.panel import PANEL_MIN_HEIGHT, PANEL_MIN_WIDTH
from pasteflow.ui.settings_dialog import SettingsDialog
from pasteflow.winapi.image_io import _default_capture_folder
from pasteflow.app.migrations import (
    _migrate_drop_ai_query_feature, _migrate_drop_official_backend, _migrate_path_paste_hotkeys, _migrate_regen_thumbnails, _migrate_secrets, _migrate_split_ocr_ai_model,
)
from pasteflow.app.secret_keys import _SECRET_KEYS


class SettingsMixin:
    def _apply_saved_panel_size(self):
        if self._saved_panel_geometry:
            try:
                w = max(PANEL_MIN_WIDTH, int(self._saved_panel_geometry["w"]))
                h = max(PANEL_MIN_HEIGHT, int(self._saved_panel_geometry["h"]))
                if self.panel.width() != w or self.panel.height() != h:
                    self.panel.resize(w, h)
            except (KeyError, ValueError):
                pass

    # ── 설정 ──

    def _get_secret(self, key: str, default: str = "") -> str:
        """시크릿 키(DPAPI 암호화 저장) 복호화 읽기 헬퍼."""
        from pasteflow.crypto import unprotect
        return unprotect(self.db.get_setting(key, default) or default)

    def _save_annot_tool_defaults(self, json_str: str):
        """주석 편집기 도구별 마지막 색·두께/글자/번호 크기·화살표 머리크기·방향을 DB에
        저장 — 재시작 후에도 유지. _EditorMixin._persist_cb로 등록돼 값 변경(우클릭
        미니패널·주석 위 휠) 시 호출된다."""
        self.db.set_setting("annot_tool_defaults", json_str)

    def _apply_settings_from_db(self):
        """DB에서 설정 로드 → UI/동작에 적용."""
        # OCR/AI 모델 분리 마이그레이션 (기존 모델을 OCR 슬롯에 복사). 1회성·idempotent.
        _migrate_split_ocr_ai_model(self.db)

        # official(Google AI Studio) 백엔드 잔재 제거 (v1.50.0). 순서 주의: 위 OCR 슬롯
        # 초기화가 읽는 ocr_gemini_model_gateway는 안 건드리지만, 비교 슬롯의 backend 키를
        # 지우므로 그걸 읽는 마이그레이션보다 뒤에 와야 한다.
        _migrate_drop_official_backend(self.db)

        # AI 질의 기능(우클릭 질문·비교·드라이브 연동) 제거가 남긴 키 purge. 위 OCR 슬롯
        # 초기화보다 반드시 뒤에 와야 한다(ocr_gemini_model_gateway를 여기서 지운다).
        _migrate_drop_ai_query_feature(self.db)

        # 한 번짜리 경로 붙여넣기 제거 + 경로 단축키 재배치(P=하나씩, [=전체). Idempotent.
        _migrate_path_paste_hotkeys(self.db)

        # 시크릿 암호화 + 고아 키 purge. Idempotent.
        _migrate_secrets(self.db)

        # 썸네일 크기 변경 — 기존 이미지 항목의 썸네일을 한 번 다시 만든다. Idempotent.
        _migrate_regen_thumbnails(self.db, self.monitor._create_thumbnail)

        # 이미지 원본을 DB BLOB → blobs 파일로 1회 이전(처음에 DB 백업) + 아무도 안 쓰는 파일 청소. Idempotent.
        self.db.migrate_images_to_files()
        self.db.sweep_blobs()

        # 레지스트리 실제 상태로 auto_start DB 동기화
        self._sync_auto_start_from_registry()

        auto_close = self.db.get_setting("panel_auto_close", "1")
        self.panel.set_auto_close(auto_close == "1")

        self._notify_on_copy = self.db.get_setting("notify_on_copy", "1") == "1"

        # 큐 idle timeout (마지막 복사로부터 N초 지나면 다음 새 복사가 큐 첫 항목)
        try:
            idle_sec = float(self.db.get_setting("queue_idle_reset_sec", "10"))
        except (ValueError, TypeError):
            idle_sec = 10.0
        self.queue.set_idle_reset_sec(idle_sec)

        # 패널 위치/크기 — show_near_cursor() 시 적용하도록 저장만 해둠
        geo_json = self.db.get_setting("panel_geometry")
        if geo_json:
            try:
                self._saved_panel_geometry = json.loads(geo_json)
            except Exception:
                self._saved_panel_geometry = None
        else:
            self._saved_panel_geometry = None

    def _open_settings(self):
        """설정 다이얼로그 열기"""
        # 창-모달이라 트레이 메뉴·API 키 오류 자동 오픈이 안 막힌다 → 중첩 exec 방지
        dlg = getattr(self, "_settings_dialog", None)
        if dlg is not None:
            dlg.raise_()
            dlg.activateWindow()
            return
        current = {
            "hotkey_panel_toggle": self.db.get_setting("hotkey_panel_toggle", "ctrl+space"),
            "history_max": self.db.get_setting("history_max", "50"),
            "auto_start": self.db.get_setting("auto_start", "0"),
            "hotkey_ocr_trigger": self.db.get_setting("hotkey_ocr_trigger", "ctrl+shift+s"),
            "hotkey_seq_image_to_path": self.db.get_setting("hotkey_seq_image_to_path", "ctrl+shift+p"),
            "hotkey_bulk_paste": self.db.get_setting("hotkey_bulk_paste", "ctrl+shift+a"),
            "hotkey_bulk_path_paste": self.db.get_setting("hotkey_bulk_path_paste", "ctrl+shift+["),
            "hotkey_pin_image": self.db.get_setting("hotkey_pin_image", "alt+f3"),
            "hotkey_new_memo": self.db.get_setting("hotkey_new_memo", "alt+`"),
            "hotkey_capture": self.db.get_setting("hotkey_capture", "alt+f2"),
            "hotkey_record": self.db.get_setting("hotkey_record", "ctrl+shift+r"),
            "gif_show_cursor": self.db.get_setting("gif_show_cursor", "1"),
            "gif_fps": self.db.get_setting("gif_fps", "12"),
            "gif_max_seconds": self.db.get_setting("gif_max_seconds", "15"),
            "video_fps": self.db.get_setting("video_fps", "15"),
            "video_max_seconds": self.db.get_setting("video_max_seconds", "600"),
            "hotkey_stt": self.db.get_setting("hotkey_stt", "ctrl+win"),
            "stt_model_gateway": self.db.get_setting("stt_model_gateway", ""),
            "stt_mic_device": self.db.get_setting("stt_mic_device", ""),
            "capture_save_folder": self.db.get_setting("capture_save_folder", "") or _default_capture_folder(),
            "ocr_language": self.db.get_setting("ocr_language", "ko"),
            "ocr_engine": self.db.get_setting("ocr_engine", "gemini"),  # OCR은 항상 AI API(엔진 선택 제거)
            "ocr_gemini_base_url": self.db.get_setting("ocr_gemini_base_url", ""),
            "ocr_gemini_api_key_gateway": self._get_secret("ocr_gemini_api_key_gateway"),
            "ocr_gemini_model_cache_gateway": self.db.get_setting("ocr_gemini_model_cache_gateway", ""),
            # OCR 전용 모델 슬롯 (API 키를 쓰는 유일한 경로 — v1.6x에서 AI 질의 기능 제거)
            "ocr_model_gateway": self.db.get_setting("ocr_model_gateway", ""),
            "notify_on_copy": self.db.get_setting("notify_on_copy", "1"),
            "queue_idle_reset_sec": self.db.get_setting("queue_idle_reset_sec", "10"),
        }
        dlg = SettingsDialog(current, parent=self.panel)
        dlg.settings_changed.connect(self._on_settings_changed)
        # 단축키 녹화 중엔 전역 훅을 멈춰, 재바인딩하려는 옛 단축키를 눌러도 그
        # 동작이 실행되지 않고 키 이벤트가 그대로 HotkeyEdit에 도달하게 한다.
        dlg.recording_active.connect(self._on_hotkey_recording_active)
        dlg.raise_()
        dlg.activateWindow()
        # exec()는 기본이 application-modal이라 부모 없는 최상위 오버레이(캡처·OCR)까지
        # 입력을 막는다. 창-모달로 좁히면 부모(패널)만 잠기고 오버레이는 그대로 동작.
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        self._settings_dialog = dlg
        try:
            dlg.exec()
        finally:
            self._settings_dialog = None
            # 안전망 — 녹화 중 다이얼로그가 닫혀 종료 시그널을 못 받아도 훅이 영구히
            # suspend된 채 남지 않도록 항상 재개(멱등).
            self.interceptor.resume()

    def _on_hotkey_recording_active(self, active: bool):
        """설정창에서 단축키를 녹화하는 동안 전역 훅을 suspend, 끝나면 resume."""
        if active:
            self.interceptor.suspend()
        else:
            self.interceptor.resume()

    def _on_settings_changed(self, new_settings: dict):
        """설정 변경 적용"""
        # 단축키 비교는 DB 저장 전에 이전 값을 먼저 읽어야 함
        old_hotkey = self.db.get_setting("hotkey_panel_toggle", "ctrl+space")
        old_ocr_hotkey = self.db.get_setting("hotkey_ocr_trigger", "ctrl+shift+s")
        old_seq_img2path_hotkey = self.db.get_setting("hotkey_seq_image_to_path", "ctrl+shift+p")
        old_bulk_paste_hotkey = self.db.get_setting("hotkey_bulk_paste", "ctrl+shift+a")
        old_bulk_path_paste_hotkey = self.db.get_setting("hotkey_bulk_path_paste", "ctrl+shift+[")
        old_pin_hotkey = self.db.get_setting("hotkey_pin_image", "alt+f3")
        old_memo_hotkey = self.db.get_setting("hotkey_new_memo", "alt+`")
        old_capture_hotkey = self.db.get_setting("hotkey_capture", "alt+f2")
        old_capture_use_printscreen = self.db.get_setting("capture_use_printscreen", "1")
        old_record_hotkey = self.db.get_setting("hotkey_record", "ctrl+shift+r")
        old_stt_hotkey = self.db.get_setting("hotkey_stt", "ctrl+win")

        from pasteflow.crypto import protect
        for key, value in new_settings.items():
            if key in _SECRET_KEYS:
                value = protect(value)
            self.db.set_setting(key, value)

        # 패널 토글 단축키 재설정
        new_hotkey = new_settings.get("hotkey_panel_toggle", "ctrl+space")
        if old_hotkey != new_hotkey:
            self.interceptor.set_panel_hotkey(new_hotkey)

        # OCR 단축키 재설정
        new_ocr_hotkey = new_settings.get("hotkey_ocr_trigger", "ctrl+shift+s")
        if old_ocr_hotkey != new_ocr_hotkey:
            self.interceptor.set_ocr_hotkey(new_ocr_hotkey)

        # 순차 경로 붙여넣기 단축키 재설정
        new_seq_img2path_hotkey = new_settings.get("hotkey_seq_image_to_path", "ctrl+shift+p")
        if old_seq_img2path_hotkey != new_seq_img2path_hotkey:
            self.interceptor.set_seq_image_to_path_hotkey(new_seq_img2path_hotkey)

        # 순차 붙여넣기 전체 자동주입 단축키 재설정
        new_bulk_paste_hotkey = new_settings.get("hotkey_bulk_paste", "ctrl+shift+a")
        if old_bulk_paste_hotkey != new_bulk_paste_hotkey:
            self.interceptor.set_bulk_paste_hotkey(new_bulk_paste_hotkey)

        # 순차 경로 붙여넣기 전체 자동주입 단축키 재설정
        new_bulk_path_paste_hotkey = new_settings.get("hotkey_bulk_path_paste", "ctrl+shift+[")
        if old_bulk_path_paste_hotkey != new_bulk_path_paste_hotkey:
            self.interceptor.set_bulk_path_paste_hotkey(new_bulk_path_paste_hotkey)

        # 핀 단축키 재설정
        new_pin_hotkey = new_settings.get("hotkey_pin_image", "alt+f3")
        if old_pin_hotkey != new_pin_hotkey:
            self.interceptor.set_pin_hotkey(new_pin_hotkey)
        new_memo_hotkey = new_settings.get("hotkey_new_memo", "alt+`")
        if old_memo_hotkey != new_memo_hotkey:
            self.interceptor.set_memo_hotkey(new_memo_hotkey)

        # 영역 캡처 단축키 재설정
        new_capture_hotkey = new_settings.get("hotkey_capture", "alt+f2")
        if old_capture_hotkey != new_capture_hotkey:
            self.interceptor.set_capture_hotkey(new_capture_hotkey)

        # PrintScreen 대체 실행 재설정
        new_capture_use_printscreen = new_settings.get("capture_use_printscreen", "1")
        if old_capture_use_printscreen != new_capture_use_printscreen:
            self.interceptor.set_capture_via_printscreen(new_capture_use_printscreen == "1")

        # 녹화 단축키 재설정 (GIF/영상 공용)
        new_record_hotkey = new_settings.get("hotkey_record", "ctrl+shift+r")
        if old_record_hotkey != new_record_hotkey:
            self.interceptor.set_record_hotkey(new_record_hotkey)

        # 음성 입력(STT) 단축키 재설정
        new_stt_hotkey = new_settings.get("hotkey_stt", "ctrl+win")
        if old_stt_hotkey != new_stt_hotkey:
            self.interceptor.set_stt_hotkey(new_stt_hotkey)

        # 자동 시작
        auto_start = new_settings.get("auto_start", "0") == "1"
        self._set_auto_start(auto_start)

        # 복사 알림 토글
        if "notify_on_copy" in new_settings:
            self._notify_on_copy = new_settings["notify_on_copy"] == "1"

        # 큐 idle timeout 변경 즉시 반영
        if "queue_idle_reset_sec" in new_settings:
            try:
                self.queue.set_idle_reset_sec(float(new_settings["queue_idle_reset_sec"]))
            except (ValueError, TypeError):
                pass

