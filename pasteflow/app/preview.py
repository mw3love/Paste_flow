"""이미지·텍스트 미리보기와 주석 복사·내보내기, 메모창 — pasteflow/main.py에서 글자 그대로 옮김(2026-11-16 폴더 구조 2편)."""

import os
import threading

from pasteflow.models import ClipboardItem
from pasteflow.ui.image_preview import ImagePreviewPopup
from pasteflow.ui.memo_window import MemoWindow
from pasteflow.ui.text_preview import TextPreviewPopup


class PreviewMixin:
    def _on_preview_image(self, item_id: int):
        existing = self._image_preview_windows.get(item_id)
        if existing is not None:
            # 이미 열려 있으면 닫지 않고 편집 모드 토글(Space 두 번째 = 편집 진입). 닫기는 ESC.
            existing.activateWindow()
            existing.raise_()
            existing.toggle_edit_mode()
            return
        item = self.db.get_item(item_id)
        if item and item.image_data:
            popup = ImagePreviewPopup.open_new(item, self.panel.geometry())
            # 우클릭 메뉴·인라인 주석 편집(Space) 완료 액션 — 핀과 같은 연결
            self._connect_preview_popup(popup)
            self._image_preview_windows[item_id] = popup
            popup.destroyed.connect(lambda _=None, iid=item_id: self._image_preview_windows.pop(iid, None))

    def _on_annotation_copy(self, png: bytes):
        """주석본을 클립보드에 복사 + 히스토리에 새 항목으로 저장(썸네일 포함) — 백그라운드.

        썸네일 생성·DIB 변환·DB blob 커밋을 UI 스레드에서 동기 실행하면 큰 이미지에서
        복사 완료 토스트가 몇 초씩 늦게 뜨고 그동안 편집기 전체가 멈췄다(2026-07-31
        사용자 보고 — 5초 지연 실측). 클립보드 API는 스레드 무관하게 안전하고 db·queue는
        자체 락으로 스레드 안전(_persist_clipboard_item 내부에서 emit하는 new_item_saved도
        Qt가 스레드 간 큐잉을 자동 처리) — GIF 인코딩 워커(main._encode_gif_worker)와 동일 패턴.
        """
        threading.Thread(
            target=self._annotation_copy_worker, args=(png,),
            daemon=True, name="annotation-copy-worker",
        ).start()

    def _annotation_copy_worker(self, png: bytes):
        """워커 스레드: 클립보드 설정 + DB/큐 저장. 완료를 시그널로 메인에 되돌려 토스트를 띄운다."""
        thumb = None
        if self.interceptor.monitor is not None:
            thumb = self.interceptor.monitor._create_thumbnail(png)
        item = ClipboardItem(content_type="image", image_data=png, thumbnail=thumb)
        self.interceptor._set_clipboard(item)   # 클립보드
        self._persist_clipboard_item(item)       # 히스토리 + 큐
        self._bridge.annotation_copied.emit(png)

    def _on_annotation_copied(self, png: bytes):
        """메인 스레드: 주석 복사 완료 → 썸네일 토스트.

        png는 PNG라 QPixmap이 바로 로드 → 토스트에 썸네일을 실어 "무엇을 복사했나"를
        시각 확인(핀·경로 붙여넣기 토스트와 일관). 원본을 넘겨 96px 축소 시 선명.
        썸네일이 카테고리를 대신하므로 이모지 아이콘은 생략(icon="") — image_path 토스트
        5곳과 동일 규칙.
        """
        from pasteflow.ui.toast import ToastNotification
        ToastNotification("복사 + 히스토리 저장됨", icon="", image_bytes=png)

    def _on_annotation_export(self, png: bytes):
        """주석본을 PNG 파일로 저장(경로는 사용자 선택)."""
        from PyQt6.QtWidgets import QFileDialog
        from pasteflow.ui.toast import ToastNotification
        path, _ = QFileDialog.getSaveFileName(
            self.panel, "주석 이미지 저장", "annotation.png", "PNG 이미지 (*.png)")
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        try:
            with open(path, "wb") as f:
                f.write(png)
        except Exception as e:
            ToastNotification(f"저장 실패 — {e}", icon="🖼")
            return
        ToastNotification(f"저장됨: {os.path.basename(path)}", icon="", image_path=path)

    def _on_preview_text(self, item_id: int):
        existing = self._text_preview_windows.pop(item_id, None)
        if existing is not None:
            existing.close()
            return
        item = self.db.get_item(item_id)
        if not item:
            return
        popup = TextPreviewPopup.open_new(item, self.panel.geometry())
        popup.copy_requested.connect(self._on_copy_item)
        popup.edit_requested.connect(self._open_memo)
        self._text_preview_windows[item_id] = popup
        popup.destroyed.connect(lambda _=None, iid=item_id: self._text_preview_windows.pop(iid, None))

    def _open_memo(self, item_id: int | None = None):
        """메모창 열기 — item_id가 없으면 메모장 맨 위에 새 메모를 만들어 연다.

        이미 열린 메모면 그 창을 앞으로 가져온다. 클립보드·순차 큐는 건드리지 않는다.
        """
        if item_id is not None:
            existing = MemoWindow.get(item_id)
            if existing is not None:
                existing.bring_to_front()
                return
            item = self.db.get_item(item_id)
            if not item or item.content_type == "image":
                return
            text = item.text_content or ""
        else:
            item_id = self.db.create_memo("").id
            text = ""
            self._refresh_panel()
        win = MemoWindow(item_id, text)
        win.save_requested.connect(self._on_memo_saved)
        win.discard_requested.connect(lambda iid: self._on_delete_item(iid, notify=False))
        win.closed.connect(lambda _id: self._refresh_panel())
        win.show_centered()

