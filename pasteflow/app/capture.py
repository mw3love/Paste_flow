"""OCR 요청과 영역 캡처(Alt+F2) — pasteflow/main.py에서 글자 그대로 옮김(2026-11-16 폴더 구조 2편)."""

import os

from PyQt6.QtCore import QBuffer, QRect

from pasteflow.models import ClipboardItem
from pasteflow.winapi.image_io import (
    _default_capture_folder, _qpixmap_to_dib, _save_image_to_folder,
)


class CaptureMixin:
    # ── OCR ──

    def _on_ocr_requested(self):
        """메인 스레드: OCR 오버레이 시작"""
        self._ocr_overlay.start()

    def _on_ocr_region_captured(self, pixmap, translate: bool = False):
        """메인 스레드: 선택 영역 픽맵 → 워커 스레드에서 OCR(translate면 번역 — Shift를 누른 채 끈 경우)"""
        # QPixmap/QBuffer는 메인 스레드에서만 안전 — PNG 변환을 여기서 완료
        buf = QBuffer()
        buf.open(QBuffer.OpenModeFlag.WriteOnly)
        pixmap.save(buf, "PNG")
        png_bytes = bytes(buf.data())
        buf.close()
        self._start_ocr_worker(png_bytes, translate=translate)

    # ── 영역 캡처 (Alt+F2) ──

    def _on_capture_requested(self):
        """메인 스레드: 영역 캡처 오버레이 시작 (마그네틱 CaptureOverlay)"""
        self._capture_overlay.start()

    def _on_capture_region(self, pixmap, rect):
        """메인 스레드: 선택 영역 픽맵 → 클립보드(DIB) + 히스토리·큐 + 파일 저장 + 토스트.

        클립보드·히스토리 처리는 OCR 결과 경로와 동일(_set_clipboard로 모니터 재감지 방지 후
        _persist_clipboard_item). 이미지는 붙여넣기 호환성이 가장 넓은 CF_DIB로 넣는다.
        rect(캡처한 논리 전역 사각형)를 기억해 직후 핀(Alt+F3)이 그 자리에 그대로 덮게 한다.
        """
        from pasteflow.ui.toast import ToastNotification, show_copy_toast

        self._pin_place_rect = QRect(rect) if rect is not None else None
        dib = _qpixmap_to_dib(pixmap)
        item = ClipboardItem(
            content_type="image",
            image_data=dib,
            thumbnail=self.monitor._create_thumbnail(dib),
        )
        self.interceptor._set_clipboard(item)

        # 지정 폴더에 PNG 저장 (없으면 생성, 미설정 시 <사진>\PasteFlow)
        # DB 저장 전에 먼저 파일을 써서 saved_image_path를 item에 실어 보낸다 — 우클릭
        # "파일 위치 열기"가 참조할 경로를 처음부터 히스토리에 함께 남기기 위함
        # (클립보드 교체는 이미 위에서 끝나 있어 Ctrl+V 응답성엔 영향 없음).
        folder = self.db.get_setting("capture_save_folder", "") or _default_capture_folder()
        saved_path = None
        try:
            os.makedirs(folder, exist_ok=True)
            saved_path = _save_image_to_folder(dib, folder)
            item.saved_image_path = saved_path
        except Exception as e:
            ToastNotification(f"캡처 파일 저장 실패 — {e}", icon="📷")

        saved_item = self._persist_clipboard_item(item)
        self._pin_place_item_id = saved_item.id

        # 일반 복사와 동일한 토스트(Q{n} 배지+썸네일)로 통일 — "캡처됨" 문구·파일명
        # 노출 없이 텍스트/이미지 복사와 같은 UX로 몇 번째 큐에 걸렸는지 바로 보여준다.
        _, total = self.queue.get_status()
        show_copy_toast(saved_item, total)

