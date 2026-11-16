"""항목 고정·메모·삭제·순서·잠금·메모 지우기·히스토리 지우기 — pasteflow/main.py에서 글자 그대로 옮김(2026-11-16 폴더 구조 2편)."""

from pasteflow.ui.memo_window import MemoWindow
from pasteflow.app.dialog_style import _MSGBOX_DARK_STYLE


class ItemActionsMixin:
    def _on_pin_item(self, item_id: int):
        self.db.pin_item(item_id)
        self._refresh_panel()

    def _on_unpin_item(self, item_id: int):
        self.db.unpin_item(item_id)
        self._refresh_panel()

    def _on_memo_saved(self, item_id: int, text: str):
        """메모창 자동 저장 — DB에 쓰고 패널의 그 한 줄만 바로 바꾼다(전체 refresh는 깜빡임)."""
        self.db.update_item_text(item_id, text)
        self.panel.update_item_text(item_id, text)

    def _on_delete_item(self, item_id: int, notify: bool = True):
        """notify: 메모장 항목이면 '되돌리기' 토스트를 띄운다. 빈 메모창을 닫아 저절로
        지우는 경우(notify=False)는 매번 뜨면 거슬려서 띄우지 않는다."""
        snapshot = self.db.get_item(item_id) if notify else None
        if not self.db.delete_item(item_id):
            return  # 잠긴 메모장 항목 — 지우지 않았으니 큐도 그대로
        if snapshot is not None and snapshot.is_pinned:
            from pasteflow.ui.toast import ToastNotification, COPY_TOAST_DURATION_MS
            ToastNotification("메모를 지웠어요 — 눌러서 되돌리기", icon="↶",
                              duration_ms=COPY_TOAST_DURATION_MS,
                              on_click=lambda: self._undo_clear_memos([snapshot]))
        # 큐가 이 항목을 들고 있으면(캡처 스냅샷은 DB와 별개로 큐에 살아있다) 함께
        # 제거 — 안 그러면 히스토리에서 지운 항목이 진행 HUD·순차 붙여넣기에는
        # 그대로 남아 "몇 개가 남았는지"가 어긋난다(2026-08-01 사용자 리포트).
        removed_from_queue = self.queue.remove_item(item_id)
        self._refresh_panel()
        if removed_from_queue:
            pointer, total = self.queue.get_status()
            self.tray.update_queue_status(pointer, total)
            if total > 0:
                self.paste_hud.show_progress(self.queue.get_items(), pointer)
            else:
                self.paste_hud.dismiss()

    def _on_pin_reorder(self, id_order_list: list):
        self.db.update_pin_orders(id_order_list)
        self._refresh_panel()

    def _on_hist_reorder(self, id_order_list: list):
        self.db.update_history_orders(id_order_list)
        # 패널 레이아웃은 이미 라이브 스왑으로 반영됨 — refresh 불필요

    def _on_toggle_lock(self, item_id: int):
        item = self.db.get_item(item_id)
        if item:
            self.db.set_locked(item_id, not item.is_locked)
            self._refresh_panel()

    def _on_locked_notice(self):
        from pasteflow.ui.toast import ToastNotification
        ToastNotification("잠긴 메모예요 — L로 잠금을 풀면 지울 수 있어요", icon="🔒")

    def _on_clear_memos(self):
        """메모장 비우기 — 확인 후 잠기지 않은 메모장 항목을 지운다(열린 메모창도 닫음)"""
        from PyQt6.QtWidgets import QMessageBox
        unlocked, locked = self.db.count_memos()
        if unlocked == 0:
            return
        body = f"메모 {unlocked}개를 지울까요?"
        if locked:
            body += f"\n(잠긴 {locked}개는 남아요)"
        dlg = QMessageBox(self.panel)
        dlg.setStyleSheet(_MSGBOX_DARK_STYLE)
        dlg.setWindowTitle("메모장 비우기")
        dlg.setText(body + "\n\n지운 직후 뜨는 알림을 누르면 되돌릴 수 있어요.")
        yes = dlg.addButton("지우기", QMessageBox.ButtonRole.DestructiveRole)
        dlg.addButton("취소", QMessageBox.ButtonRole.RejectRole)
        dlg.exec()
        if dlg.clickedButton() is not yes:
            return
        # 되돌리기용 스냅샷 — 열린 메모창의 아직 저장 안 된 글자까지 먼저 저장하고 읽는다
        MemoWindow.flush_all()
        snapshot = [it for it in self.db.get_pinned_items() if not it.is_locked]
        for item_id in self.db.clear_memos():
            win = MemoWindow.get(item_id)
            if win is not None:
                win.blockSignals(True)  # 이미 지운 메모 — 닫으며 다시 저장하지 않게
                win.close()
            self.queue.remove_item(item_id)
        pointer, total = self.queue.get_status()
        self.tray.update_queue_status(pointer, total)
        if total > 0:
            self.paste_hud.show_progress(self.queue.get_items(), pointer)
        else:
            self.paste_hud.dismiss()
        self._refresh_panel()
        from pasteflow.ui.toast import ToastNotification, COPY_TOAST_DURATION_MS
        ToastNotification(f"메모 {len(snapshot)}개를 지웠어요 — 눌러서 되돌리기", icon="↶",
                          duration_ms=COPY_TOAST_DURATION_MS,
                          on_click=lambda: self._undo_clear_memos(snapshot))

    def _undo_clear_memos(self, snapshot: list):
        self.db.restore_memos(snapshot)
        self._refresh_panel()


    def _on_clear_history(self):
        self.db.clear_history()
        self.queue.clear()
        self.tray.update_queue_status(0, 0)
        self._refresh_panel()

