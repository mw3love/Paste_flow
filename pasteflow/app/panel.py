"""패널 열고 닫기·패널 붙여넣기·큐 고르기 — pasteflow/main.py에서 글자 그대로 옮김(2026-11-16 폴더 구조 2편)."""

import ctypes
import threading

from pasteflow.models import ClipboardItem


class PanelMixin:
    def _update_paste_ui(self):
        """메인 스레드에서 붙여넣기 UI 업데이트"""
        pointer, total = self.queue.get_status()
        self.tray.update_queue_status(pointer, total)
        if self.panel.isVisible():
            self.panel.update_queue_status(pointer, total)
        if total > 0:
            self.paste_hud.show_progress(self.queue.get_items(), pointer)

    def _toggle_panel(self):
        """패널 토글 — 단축키/트레이로 열 때 마우스 근처에 표시"""
        if self.panel.isVisible():
            self.panel.hide()
        else:
            # _prev_foreground_hwnd는 SetWinEventHook이 연속 추적 — 별도 캡처 불필요
            self.panel._user_activated = True
            self._apply_saved_panel_size()
            self.panel.show_near_cursor()
            self._refresh_panel()

    def _refresh_panel(self):
        """패널 데이터 갱신"""
        pinned = self.db.get_pinned_items_summary()
        history = self.db.get_recent_items_summary()
        pointer, total = self.queue.get_status()
        queue_item_ids = [item.id for item in self.queue.get_items()]
        self.panel.refresh(pinned, history, pointer, total, queue_item_ids)

    def _on_panel_paste(self, item: ClipboardItem):
        """패널 항목 붙여넣기(드래그·Enter) — auto_close 설정에 따라 패널 닫기 여부 결정.

        붙여넣기만 하고 히스토리 순서는 건드리지 않는다(맥판과 같음). 클립보드는
        direct_paste가 그 항목으로 바꾸지만, 최상단 이동은 복사(_on_copy_item)에서만 한다.
        """
        full_item = self.db.get_item(item.id) or item
        target_hwnd = self._prev_foreground_hwnd

        if self.panel._auto_close:
            # 자동 닫기 ON: 즉시 숨기고 붙여넣기 (fade 대기 시 포그라운드 잠금 문제 발생)
            self.panel.hide_immediate()
            def _do_paste():
                try:
                    self.interceptor.direct_paste(full_item, target_hwnd)
                except Exception as e:
                    print(f"[PanelPaste] Error: {e}")
        else:
            # 자동 닫기 OFF: 패널 유지
            # _paste_in_progress 먼저 → SetForegroundWindow 순서 보장 (changeEvent 선행 방지)
            self.panel._paste_in_progress = True
            # 메인 스레드에서 포커스 이동 (포그라운드 잠금 우회)
            if target_hwnd:
                ctypes.windll.user32.SetForegroundWindow(target_hwnd)
            def _do_paste():
                try:
                    # 포커스 이미 이동됐으므로 target_hwnd=None
                    self.interceptor.direct_paste(full_item, None)
                except Exception as e:
                    print(f"[PanelPaste] Error: {e}")
                finally:
                    # 워커 스레드엔 Qt 이벤트 루프가 없어 QTimer.singleShot은 영영 발화하지
                    # 않는다(실측) — 시그널로 메인 스레드에 넘긴다.
                    self._bridge.panel_paste_done.emit()

        threading.Thread(target=_do_paste, daemon=True).start()

    def _on_copy_item(self, item: ClipboardItem):
        """미리보기·핀 창의 '복사' → 클립보드 + 큐 추가 + 토스트.

        self-triggered `_set_clipboard`라 모니터가 재감지하지 않으므로 중복 항목은 생기지 않는다.
        옛날엔 히스토리 맨 위로도 올렸지만("맨 위 = 지금 클립보드") 2026-11-14에 뺐다 — 큐 번호
        칩이 무엇이 붙을지 보여 주고, 항목이 튀면 큐 순서(화면 위→아래)와 어긋난다.
        """
        full_item = self.db.get_item(item.id) or item
        self.interceptor._set_clipboard(full_item)
        self.queue.add_item(full_item)
        self._refresh_panel()
        if self._notify_on_copy:
            _, total = self.queue.get_status()
            self._on_copy_toast(full_item, total)

    def _on_queue_select(self, item_id: int):
        """Shift+C·우클릭 '여기부터 맨 위까지 큐' — 그 섹션 맨 위부터 고른 항목까지, 화면 위→아래 순서.
        (2026-11-14 사용자 요청으로 옛 '고른 항목→최신' 시간 순에서 바꿈)
        """
        history = self.panel.history_items
        hist_ids = [item.id for item in history]
        if item_id in hist_ids:
            i = hist_ids.index(item_id)
            queue_items = list(history[0 : i + 1])  # [맨 위(최신), ..., 고른 항목]
            self.queue.set_queue(queue_items)
        else:
            pinned = self.panel.pinned_items
            pin_ids = [item.id for item in pinned]
            if item_id not in pin_ids:
                return
            i = pin_ids.index(item_id)
            queue_items = list(pinned[0 : i + 1])  # [첫 고정, ..., 고른 항목]
            self.queue.set_queue(queue_items)
        self._show_queue_set()

    def _on_queue_append_ids(self, item_ids: list):
        """패널에서 Ctrl·Shift로 여러 개 고르고 c — 고른 것을 화면 위→아래 순서로 큐 맨 뒤에 붙인다.
        이미 큐에 있는 것은 건너뛴다. 일부를 붙여 넣은 큐(pointer>0)면 새 큐로 시작(c 한 개와 같음)."""
        wanted = set(item_ids)
        pointer, _ = self.queue.get_status()
        current = [] if pointer > 0 else self.queue.get_items()
        have = {it.id for it in current}
        shown = self.panel.pinned_items + self.panel.history_items
        added = [item for item in shown if item.id in wanted and item.id not in have]
        if not added:
            return
        self.queue.set_queue(current + added)
        self._show_queue_set()

    def _on_queue_toggle(self, item_id: int):
        """c: 큐 맨 뒤에 붙이기(번호가 하나씩 늘어남). 이미 큐에 있으면 그 항목만 뺀다.
        이미 일부를 붙여 넣은 큐(pointer>0)면 새 큐로 시작한다."""
        pointer, _ = self.queue.get_status()
        current = [] if pointer > 0 else self.queue.get_items()
        if any(it.id == item_id for it in current):
            current = [it for it in current if it.id != item_id]
            if not current:
                self._clear_queue_ui()
                return
        else:
            shown = self.panel.pinned_items + self.panel.history_items
            item = next((it for it in shown if it.id == item_id), None)
            if item is None:
                return
            current = current + [item]
        self.queue.set_queue(current)
        self._show_queue_set()

    def _on_queue_reverse(self):
        """r: 아직 안 붙인 큐 항목의 순서를 뒤집는다(이미 붙인 것은 버림)."""
        pointer, _ = self.queue.get_status()
        remaining = self.queue.get_items()[pointer:]
        if len(remaining) < 2:
            return
        self.queue.set_queue(list(reversed(remaining)))
        self._show_queue_set()

    def _show_queue_set(self):
        """큐를 새로 정한 뒤 클립보드 맞추기 + 트레이·패널 표시 갱신 + 클릭 선택 지우기."""
        self._sync_clipboard_to_queue()
        pointer, total = self.queue.get_status()
        queue_item_ids = [item.id for item in self.queue.get_items()]
        self.tray.update_queue_status(pointer, total)
        self.panel.update_queue_highlight(pointer, total, queue_item_ids)
        # 큐 지정에 쓴 클릭 선택은 여기서 바로 지운다 — 안 지우면 큐 진행 중 그 항목이
        # 소진(done)돼 큐 강조가 회색으로 바뀌어도 선택 강조(흰·코랄 굵은 테두리)가 남아 "첫 항목만
        # 안 끝난 것처럼" 보인다(전체 큐가 다 끝나야 clear_selection이 불려 없어짐).
        self.panel.clear_selection()

    def _sync_clipboard_to_queue(self):
        """클립보드 = 큐 맨 끝 항목(2026-11-14). 그래서 패널에서 큐에 넣은 것이 일반 Ctrl+V로도 붙는다.

        Ctrl+V는 가로채지 않으므로(절대 금지 1) 누르는 순간 바꿔 넣을 수 없고, 미리 넣어 둬야 한다.
        '처음'이 아니라 '끝'인 이유: 다른 앱에서 A·B·C를 복사하면 OS가 이미 C를 넣어 둔다 —
        처음으로 맞추면 그때마다 A로 덮어써 'C를 복사했는데 A가 붙는' 일이 생긴다.
        Ctrl+Shift+V로 붙인 뒤엔 방금 붙인 항목이 클립보드에 남는다(=마지막으로 손댄 큐 항목)."""
        items = self.queue.get_items()
        if not items:
            return
        last = items[-1]
        full_item = (self.db.get_item(last.id) if last.id is not None else None) or last
        try:
            self.interceptor._set_clipboard(full_item)
        except Exception as e:
            print(f"[QueueClipboard] Error: {e}")

    def _on_queue_deselect(self, item_id: int):
        self._clear_queue_ui()

    def _on_plain_paste(self):
        """일반 Ctrl+V 감지(훅 스레드 시그널 → 메인). 2026-11-14부터 큐를 비우지 않는다 —
        클립보드가 큐 맨 끝 항목이라(_sync_clipboard_to_queue) Ctrl+V도 큐 항목을 붙이는 것이고,
        비우면 c로 쌓은 큐가 Ctrl+V 한 번에 사라진다. 큐는 Esc·다 붙임·HUD ✕·새 복사 규칙으로 비워진다."""
        pass

