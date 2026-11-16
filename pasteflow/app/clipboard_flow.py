"""복사 항목 저장·큐 UI·벌크(전체 자동주입) 붙여넣기 — pasteflow/main.py에서 글자 그대로 옮김(2026-11-16 폴더 구조 2편)."""

from PyQt6.QtCore import QTimer

from pasteflow.models import ClipboardItem
from pasteflow.winapi.image_io import _save_image_to_drop_temp


class ClipboardFlowMixin:
    def _persist_clipboard_item(self, item: ClipboardItem) -> ClipboardItem:
        """DB 저장 + 큐 추가 + 패널 갱신 시그널. 복사 토스트는 띄우지 않는다.

        OCR 결과처럼 자체 알림이 있는 경로가 이 메서드를 직접 호출해
        복사 토스트 중복을 피한다.
        """
        saved = self.db.save_item(item)
        self.queue.add_item(saved)
        self._bridge.new_item_saved.emit(saved)
        return saved

    def _on_new_clipboard_item(self, item: ClipboardItem):
        """클립보드 모니터 콜백 (실제 Ctrl+C) — 백그라운드 스레드. 저장 + 복사 토스트."""
        # 외부 복사가 들어오면 클립보드가 더 이상 "방금 캡처한 이미지"가 아니므로, 핀을
        # 캡처 자리에 덮는 좌표를 무효화한다(이 콜백은 self-triggered 캡처 경로엔 안 옴).
        self._pin_place_rect = None
        saved = self._persist_clipboard_item(item)
        if self._notify_on_copy:
            _, total = self.queue.get_status()
            self._bridge.copy_toast.emit(saved, total)

    def _on_copy_toast(self, item: ClipboardItem, queue_count: int):
        """메인 스레드: 복사 알림 토스트 표시"""
        from pasteflow.ui.toast import show_copy_toast
        show_copy_toast(item, queue_count)

    def _on_duplicate_clipboard_item(self):
        """중복 클립보드 콜백 — 무시 (중복은 큐/DB에 추가하지 않음)."""
        pass

    def _on_new_item_ui(self, saved: ClipboardItem):
        """메인 스레드에서 UI 갱신 — 패널 갱신 + 진행 HUD 정리"""
        if self.panel.isVisible():
            self._refresh_panel()
        # 새 복사가 발생하면 진행 중이던 붙여넣기 시퀀스는 무효 — HUD 정리
        self.paste_hud.finish()

    def _on_paste_from_hook(self, item: ClipboardItem):
        """붙여넣기 콜백 — 훅 스레드에서 호출됨 → 시그널로 메인 스레드 전달"""
        self._bridge.paste_happened.emit()
        pointer, total = self.queue.get_status()
        if pointer >= total and total > 0:
            self._bridge.paste_queue_done.emit()

    def _clear_queue_ui(self):
        """큐를 비우고 tray·패널 하이라이트를 초기화 (큐 포기/클리어 공통 경로).

        '큐를 언제 비울지' 정책의 '포기/클리어' 범주가 공유: 큐 소진 완료,
        HUD ✕ 취소, 우클릭 큐 해제.
        """
        self.queue.clear()
        self.tray.update_queue_status(0, 0)
        self.panel.update_queue_highlight(0, 0, [])
        self.panel.clear_selection()

    def _on_paste_queue_done(self):
        """큐 소진 완료 — 큐/포인터를 클리어하고 진행 HUD를 잠시 뒤 페이드.

        소진 후에도 큐가 남아 있으면 패널이 항목을 '큐에 들어있음'으로 계속 표시해
        우클릭 메뉴가 '큐 해제'로 뜨는 찌꺼기가 생긴다 → 소진 시 클리어로 정리.
        """
        self._clear_queue_ui()
        self.paste_hud.finish()

    def _on_cancel_paste_queue(self):
        """HUD ✕ 클릭 — 남은 붙여넣기 취소: 큐 비우기 + 표시 초기화 + HUD 즉시 닫기

        벌크 자동주입(Ctrl+Shift+A/Ctrl+Shift+[) 진행 중이면 플래그를 내려 다음
        예약된 스텝(_bulk_paste_step)이 스스로 멈추게 한다(큐가 비어도 이미 예약된
        QTimer 콜백은 취소되지 않으므로, 콜백 시작 시 이 플래그를 확인해야 함).
        """
        self._bulk_paste_active = False
        self._clear_queue_ui()
        self.paste_hud.dismiss()

    def _on_auto_close_changed(self, value: bool):
        self.db.set_setting("panel_auto_close", "1" if value else "0")


    # 벌크(전체 자동주입) 스텝 사이 간격(ms) — 대상 앱이 이전 붙여넣기를 처리할
    # 시간을 확보하기 위한 고정값. 너무 짧으면 클립보드가 앱이 읽기 전에 다음
    # 항목으로 바뀌어 내용이 누락·중복될 수 있다(web_open.py의 브라우저 주입이
    # 겪는 고정지연 문제와 동일 계열 리스크 — 완전한 해결책은 없고 경험적 값).
    _BULK_PASTE_STEP_MS = 300

    def _on_bulk_paste_hotkey(self):
        """순차 붙여넣기 전체 자동주입 단축키(기본 Ctrl+Shift+A) — Ctrl+Shift+V의 '벌크' 버전.

        한 번 눌러 큐에 남은 항목 전체를 간격(_BULK_PASTE_STEP_MS)을 두고 순서대로
        자동 주입한다. 반복해 누를 필요 없이 "10개를 한 번에 붙여넣고 싶을 때"용.
        """
        self._start_bulk_paste(mode="normal")

    def _on_bulk_path_paste_hotkey(self):
        """순차 경로 붙여넣기 전체 자동주입 단축키(기본 Ctrl+Shift+[) — Ctrl+Shift+P의 '벌크' 버전.

        이미지 항목은 임시 PNG 경로 텍스트로, 그 외는 원본 그대로 순서대로 자동 주입한다.
        """
        self._start_bulk_paste(mode="path")

    def _start_bulk_paste(self, mode: str):
        """벌크 자동주입 시작 — 큐가 비었으면 토스트만, 아니면 첫 스텝을 즉시 실행."""
        from pasteflow.ui.toast import ToastNotification

        if self._bulk_paste_active:
            return  # 이미 진행 중(같은 키 연타·다른 벌크 키 겹눌림) — 무시

        pointer, total = self.queue.get_status()
        if pointer >= total:
            ToastNotification("순차 큐가 비었습니다", icon="🔤" if mode == "path" else "")
            return

        self._bulk_paste_active = True
        self._bulk_paste_step(mode)

    def _bulk_paste_step(self, mode: str):
        """벌크 자동주입 한 스텝 — 항목 하나를 주입하고 남았으면 다음 스텝을 예약한다.

        mode="normal"은 Ctrl+Shift+V와 동일하게 원본 그대로, mode="path"는
        Ctrl+Shift+P와 동일하게 이미지를 임시 PNG 경로 텍스트로 바꿔 주입한다.
        HUD ✕ 취소(_on_cancel_paste_queue)가 _bulk_paste_active를 False로 내리면
        다음 예약된 스텝이 여기서 조용히 멈춘다(이미 예약된 QTimer 콜백 자체는
        취소되지 않으므로, 실행 시점에 이 플래그를 확인하는 것으로 대신한다).
        """
        from pasteflow.ui.toast import ToastNotification
        from pasteflow.paste_interceptor import VK_V

        if not self._bulk_paste_active:
            return

        next_item = self.queue.get_next()
        if next_item is None:
            self._bulk_paste_active = False
            self._update_paste_ui()
            self._on_paste_queue_done()
            return

        # summary 항목이면 전체 로드 (이미지 항목은 image_data가 인라인이라 대개 불필요)
        if not next_item.image_data and not next_item.extra_formats and next_item.id:
            full = self.db.get_item(next_item.id)
            if full:
                next_item = full

        pointer, total = self.queue.get_status()
        is_last = pointer >= total

        if mode == "path" and next_item.content_type == "image" and next_item.image_data:
            try:
                saved_path = _save_image_to_drop_temp(next_item.image_data)
            except Exception as e:
                ToastNotification(f"임시 파일 저장 실패 — {e}", icon="🔤")
                saved_path = None
            if saved_path:
                self._last_pasted_image_path = saved_path
                path_item = ClipboardItem(
                    content_type="text",
                    text_content=saved_path,
                    preview_text=saved_path[:200],
                )
                self.interceptor._set_clipboard(path_item)
                QTimer.singleShot(50, lambda: self.interceptor._send_clean_key(VK_V))
                if is_last:
                    # 중간 항목에서 복원하면 바로 다음 스텝의 클립보드 교체와 타이밍이
                    # 겹쳐 꼬일 수 있어, 원본 이미지 복원은 마지막 항목에서만 한다
                    # (단발/순차 경로 붙여넣기와 동일한 250ms 복원 — 큐 소진 뒤 최종
                    # 클립보드 상태만 맞으면 되므로 중간 단계는 생략해도 무해).
                    QTimer.singleShot(250, lambda: self.interceptor._set_clipboard(next_item))
        else:
            self.interceptor._set_clipboard(next_item)
            QTimer.singleShot(50, lambda: self.interceptor._send_clean_key(VK_V))

        self._update_paste_ui()

        if is_last:
            self._bulk_paste_active = False
            self._on_paste_queue_done()
        else:
            QTimer.singleShot(self._BULK_PASTE_STEP_MS, lambda: self._bulk_paste_step(mode))

