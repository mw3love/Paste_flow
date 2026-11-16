"""핀(화면에 떠 있는 이미지 창) — pasteflow/main.py에서 글자 그대로 옮김(2026-11-16 폴더 구조 2편)."""

import os

from PyQt6.QtCore import QRect

from pasteflow.models import ClipboardItem
from pasteflow.ui.image_preview import ImagePreviewPopup
from pasteflow.winapi.image_io import _read_image_from_clipboard, _render_text_to_png


class PinMixin:
    def _on_pin_hotkey(self):
        """핀 단축키(기본 Alt+F3) — 순차 큐에 다음 항목이 있으면 그걸, 없으면 클립보드를
        화면에 떠 있는 창으로 띄운다(Snipaste의 'paste to screen').

        2026-09-25에 옛 '화면 핀'(Alt+F3, 클립보드만)과 '순차 핀'(Alt+Shift+F3, 큐만)을
        이 키 하나로 합쳤다 — 같은 걸 두 번 핀할 일이 거의 없고(사용자 판단), 필요하면
        핀 우클릭 "복제"로 한다. 큐는 순차 붙여넣기(Ctrl+Shift+V)와 **같은 포인터를
        공유**하므로 핀한 항목은 붙여넣기 큐에서도 소진된다(사용자가 고른 트레이드오프).
        """
        pointer, total = self.queue.get_status()
        if pointer < total and self._pin_next_from_queue():
            return
        self._pin_from_clipboard()

    def _open_pin(self, item: ClipboardItem, place_rect: QRect | None = None):
        """핀 창을 띄우고 우클릭 메뉴·주석 편집 시그널을 연결한다(핀·복제 공용).

        place_rect가 있으면 그 사각형에 1:1로 정확히 덮고, 없으면 커서 옆에 1:1로 띄운다.
        """
        from PyQt6.QtGui import QCursor

        # 커서 위치에 1px 앵커를 만들어 그 우측에 핀 창을 띄운다(compute_preview_pos 재사용).
        cursor_pos = QCursor.pos()
        anchor = QRect(cursor_pos.x(), cursor_pos.y(), 1, 1)
        popup = ImagePreviewPopup.open_new(item, anchor, native=True, place_rect=place_rect)
        self._connect_preview_popup(popup)
        return popup

    def _connect_preview_popup(self, popup):
        """미리보기·핀 창의 우클릭 메뉴와 주석 편집 완료 시그널을 main 핸들러에 연결."""
        popup.copy_requested.connect(self._on_copy_item)
        popup.copy_as_path_requested.connect(self._copy_image_as_path_for_item)
        popup.ask_ai_requested.connect(self._on_ask_ai_for_image)
        popup.ocr_requested.connect(self._on_ocr_image_item)
        popup.translate_requested.connect(self._on_translate_image_item)
        popup.annotated_copy_requested.connect(self._on_annotation_copy)
        popup.export_file_requested.connect(self._on_annotation_export)
        # 복제 — 원본 창 옆(같은 크기)에 새 핀을 띄운다.
        popup.duplicate_requested.connect(self._open_pin)

    def _pin_from_clipboard(self):
        """클립보드 이미지(없으면 텍스트를 이미지로 렌더)를 핀한다. 큐가 빈 경우의 폴백."""
        from pasteflow.ui.toast import ToastNotification

        image_bytes = _read_image_from_clipboard()
        text = self.app.clipboard().text() or ""
        # Ctrl+Shift+P·Ctrl+Shift+[로 방금 붙여넣은 이미지 경로가 아직 클립보드에 있으면,
        # 경로 문자열을 렌더하지 않고 그 원본 이미지를 핀한다(사용자 의도 = 이미지).
        if (not image_bytes and self._last_pasted_image_path
                and text.strip() == self._last_pasted_image_path
                and os.path.exists(self._last_pasted_image_path)):
            try:
                with open(self._last_pasted_image_path, "rb") as f:
                    image_bytes = f.read()
            except Exception:
                image_bytes = None
        # 방금 캡처한 이미지면(외부 복사로 무효화 안 됨) 캡처 자리에 그대로 덮는다.
        place_rect = self._pin_place_rect if image_bytes else None
        if not image_bytes:
            # 이미지가 없으면 클립보드 텍스트를 이미지로 렌더링해 핀(Snipaste 동작)
            if text.strip():
                try:
                    image_bytes = _render_text_to_png(text)
                except Exception as e:
                    ToastNotification(f"텍스트 렌더링 실패 — {e}", icon="📌")
                    return
            else:
                ToastNotification("클립보드에 이미지·텍스트가 없습니다", icon="📌")
                return

        self._open_pin(ClipboardItem(content_type="image", image_data=image_bytes), place_rect)

    def _pin_next_from_queue(self) -> bool:
        """큐에서 다음 항목을 꺼내 핀한다. 꺼낼 항목이 없었으면 False(호출자가 클립보드 폴백).

        이미지는 그대로, 텍스트는 _render_text_to_png로 이미지화해 핀한다. 방금 캡처한
        항목이면(Alt+F2 → Alt+F3) 캡처 자리에 1:1로 덮고, 나머지는 커서 옆에 띄운다 —
        여러 장을 커서를 옮겨 가며 나란히 펼쳐 볼 수 있게(제자리에 겹쳐 쌓지 않게).
        """
        from pasteflow.ui.toast import ToastNotification

        next_item = self.queue.get_next()
        if next_item is None:
            return False

        # summary 항목이면 전체 로드 (이미지 항목은 image_data가 인라인이라 대개 불필요)
        if not next_item.image_data and not next_item.extra_formats and next_item.id:
            full = self.db.get_item(next_item.id)
            if full:
                next_item = full

        if next_item.content_type == "image" and next_item.image_data:
            image_bytes = next_item.image_data
        elif next_item.text_content:
            try:
                image_bytes = _render_text_to_png(next_item.text_content)
            except Exception as e:
                ToastNotification(f"텍스트 렌더링 실패 — {e}", icon="📌")
                image_bytes = None
        else:
            image_bytes = None

        if image_bytes:
            place_rect = None
            if (next_item.content_type == "image" and next_item.id is not None
                    and next_item.id == self._pin_place_item_id):
                place_rect = self._pin_place_rect
            self._open_pin(ClipboardItem(content_type="image", image_data=image_bytes), place_rect)

        # 진행 HUD 갱신 + 큐 소진 시 정리 (Ctrl+Shift+V 경로와 동일 표시)
        self._update_paste_ui()
        pointer, total = self.queue.get_status()
        if pointer >= total and total > 0:
            self._on_paste_queue_done()
        return True

