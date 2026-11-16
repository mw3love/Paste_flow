"""패널 항목을 다른 앱·탐색기·바탕화면으로 끌어 놓기 — pasteflow/main.py에서 글자 그대로 옮김(2026-11-16 폴더 구조 2편)."""

import ctypes
import os

from PyQt6.QtCore import QTimer

from pasteflow.models import ClipboardItem
from pasteflow.winapi.desktop import (
    _find_desktop_listview, _get_desktop_path, _position_desktop_icon,
)
from pasteflow.winapi.drag_paste import (
    _activate_and_send_ctrl_v, _find_deepest_child, _is_chromium_window,
)
from pasteflow.winapi.explorer import _get_explorer_folder
from pasteflow.winapi.image_io import (
    _image_data_to_png_bytes, _save_image_to_drop_temp, _save_image_to_folder,
)


_EXPLORER_CLASSES = {"CabinetWClass"}
_DESKTOP_CLASSES = {"Progman", "WorkerW"}


class DragMixin:
    def _on_drag_to_app(self, item_id: int, cursor_pos, alt_held: bool = False):
        """패널 항목 드래그 → 외부 앱 붙여넣기.
        - Alt+드래그 + 이미지: 임시 PNG로 저장 후 경로 텍스트 붙여넣기 (Claude CLI 등)
        - 이미지 + Explorer/바탕화면: PNG 파일로 저장
        - Win32/WinUI3: 재귀 탐색으로 찾은 최하위 컨트롤에 WM_PASTE
        - Electron/Chromium: AttachThreadInput + SetForegroundWindow + SendInput(Ctrl+V)
        """
        full_item = self.db.get_item(item_id)
        if not full_item:
            return

        # AI 질문창이 떠 있고 그 위에 이미지를 놓았으면 클립보드/win32 경로를 타지 않고
        # 곧장 그 창에 첨부한다 — 같은 프로세스 안의 창이라 클립보드를 거칠 이유가 없고,
        # 패널의 드래그는 실제 OS 드래그앤드롭이 아니라 마우스 위치를 추적하는 자체 구현
        # (fake drag)이라 Qt의 드롭 이벤트로는 애초에 안 잡힌다 — 여기서 직접 판정해야 한다.
        ai_dialog = getattr(self, "_ai_dialog", None)
        if (ai_dialog is not None and full_item.content_type == "image"
                and full_item.image_data and ai_dialog.frameGeometry().contains(cursor_pos)):
            try:
                png = _image_data_to_png_bytes(full_item.image_data)
            except Exception:
                png = None
            if png and ai_dialog.attach_image_bytes(png):
                ai_dialog.raise_()
                ai_dialog.activateWindow()
                return

        import win32gui
        import win32con

        screen_pt = (cursor_pos.x(), cursor_pos.y())
        hwnd = win32gui.WindowFromPoint(screen_pt)
        if not hwnd:
            return

        # 최상위 창 클래스 확인
        root_hwnd = win32gui.GetAncestor(hwnd, win32con.GA_ROOT)
        root_class = ""
        try:
            root_class = win32gui.GetClassName(root_hwnd)
        except Exception:
            pass

        # Alt+드래그 + 이미지 → 임시 PNG 저장 + 경로 텍스트 클립보드 + SendInput(Ctrl+V)
        # Windows Terminal의 claude CLI 등 "파일 경로 텍스트"를 첨부로 받는 앱 대응.
        # WM_PASTE는 터미널이 무시하므로 무조건 SendInput 경로로 통일.
        if alt_held and full_item.image_data and full_item.content_type == "image":
            from pasteflow.ui.toast import ToastNotification
            try:
                saved_path = _save_image_to_drop_temp(full_item.image_data)
            except Exception as e:
                ToastNotification(f"임시 파일 저장 실패 — {e}", icon="🔤")
                return
            path_item = ClipboardItem(
                content_type="text",
                text_content=saved_path,
                preview_text=saved_path[:200],
            )
            self.interceptor._set_clipboard(path_item)
            # 마우스 업 시점에 사용자가 Alt를 여전히 누르고 있으므로 수정키 해제 후 주입
            # (해제 없이 plain Ctrl+V를 쏘면 OS가 Ctrl+Alt+V로 오인해 붙여넣기 실패)
            _activate_and_send_ctrl_v(
                root_hwnd, sender=self.interceptor._release_modifiers_and_send_ctrl_v
            )
            return

        # 이미지 항목 + Explorer/바탕화면 → PNG 파일 저장
        if full_item.image_data and full_item.content_type == "image":
            folder = None
            is_desktop = root_class in _DESKTOP_CLASSES
            if root_class in _EXPLORER_CLASSES:
                folder = _get_explorer_folder(root_hwnd, screen_pt=screen_pt)
            elif is_desktop:
                folder = _get_desktop_path()
            # 끌어 놓은 항목이 큐 안에 있고 큐에 이미지가 2개 이상이면 큐 전체를 한 번에 저장
            if folder and self._save_queue_images_to_folder(item_id, folder, is_desktop, screen_pt):
                return
            if folder:
                try:
                    saved_path = _save_image_to_folder(full_item.image_data, folder)
                except Exception:
                    pass
                else:
                    if is_desktop:
                        # 가짜 드래그라 Explorer가 드롭 좌표를 모름 — 저장된 파일 아이콘을
                        # 커서를 놓은 자리로 직접 재배치한다(_position_dropped_desktop_icon).
                        self._position_dropped_desktop_icon(os.path.basename(saved_path), screen_pt)
                    return  # 저장 성공 시에만 반환; 실패 시 클립보드 경로로 fall-through

        # 기존 붙여넣기 경로 (텍스트/기타 항목, 또는 이미지→일반 앱)
        # Enter와 같이 붙여넣기만 하고 히스토리 순서는 건드리지 않는다(맥판과 같음).
        self.interceptor._set_clipboard(full_item)

        target = _find_deepest_child(hwnd, screen_pt)
        class_name = ""
        try:
            class_name = win32gui.GetClassName(target)
        except Exception:
            pass

        # 이미지 fallthrough + Win32 경로: 2.0초로 연장 (0.5초 덮어쓰기 방지)
        # Chromium 경로 제외: SendInput 이후 사용자 복사가 억제되는 것 방지
        if full_item.content_type == "image" and self.interceptor.monitor and not _is_chromium_window(class_name):
            self.interceptor.monitor.set_self_triggered(2.0)

        if _is_chromium_window(class_name):
            # Electron/Chromium: 포그라운드 활성화 후 SendInput(Ctrl+V)
            top_hwnd = win32gui.GetAncestor(target, win32con.GA_ROOT)
            _activate_and_send_ctrl_v(top_hwnd)
        else:
            # Win32 / WinUI3: WM_PASTE 직접 전송
            win32gui.SendMessage(target, win32con.WM_PASTE, 0, 0)

    def _save_queue_images_to_folder(self, item_id: int, folder: str,
                                     is_desktop: bool, screen_pt: tuple) -> bool:
        """큐 묶음 저장 — item_id가 큐 안에 있으면 큐의 이미지 전부를 큐 순서 번호를 붙여 저장.
        텍스트 등 이미지가 아닌 항목은 건너뛴다. 큐는 건드리지 않는다(다른 폴더에 또 놓을 수 있게).
        묶음 저장을 했으면 True, 해당 없으면(큐 밖 항목·이미지 1개 이하) False → 기존 단일 저장."""
        queue_items = self.queue.get_items()
        if item_id not in {it.id for it in queue_items}:
            return False
        image_ids = [it.id for it in queue_items if it.content_type == "image"]
        if len(image_ids) < 2:
            return False

        from pasteflow.ui.toast import ToastNotification
        saved_names = []
        for seq, iid in enumerate(image_ids, 1):
            full = self.db.get_item(iid)
            if not full or not full.image_data:
                continue
            try:
                saved_names.append(
                    os.path.basename(_save_image_to_folder(full.image_data, folder, seq=seq)))
            except Exception:
                pass
        if not saved_names:
            return False

        if is_desktop:
            # 첫 파일은 놓은 자리, 나머지는 아이콘 한 칸씩 아래로
            step = 100
            lv_hwnd = _find_desktop_listview()
            if lv_hwnd:
                LVM_GETITEMSPACING = 0x1000 + 51
                spacing = ctypes.windll.user32.SendMessageW(lv_hwnd, LVM_GETITEMSPACING, 0, 0)
                step = ((spacing & 0xFFFFFFFF) >> 16) & 0xFFFF or step
            for i, name in enumerate(saved_names):
                self._position_dropped_desktop_icon(name, (screen_pt[0], screen_pt[1] + i * step))

        skipped = len(queue_items) - len(saved_names)
        msg = f"이미지 {len(saved_names)}개 저장"
        if skipped:
            msg += f" · {skipped}개 건너뜀"
        ToastNotification(msg, icon="📁")
        return True

    def _position_dropped_desktop_icon(self, filename: str, screen_pt: tuple, attempt: int = 0):
        """바탕화면에 방금 저장한 파일의 아이콘을 드롭 지점(screen_pt)에 배치한다.

        Explorer가 파일시스템 변경을 감지해 새 항목을 리스트뷰에 반영하기까지
        지연이 있어(수십~수백ms), 즉시 시도해 실패하면 짧은 간격으로 재시도한다.
        "아이콘 자동 정렬"이 켜져 있으면 결국 무효화되지만 실패해도 무해(시각적
        편의일 뿐)하므로 재시도 소진 후에도 조용히 포기한다.
        """
        lv_hwnd = _find_desktop_listview()
        if lv_hwnd and _position_desktop_icon(lv_hwnd, filename, screen_pt):
            return
        if attempt < 10:  # 최대 ~1.5초 — 실측상 Explorer 리스트뷰 반영에 ~1~1.2초 걸림
            QTimer.singleShot(
                150,
                lambda: self._position_dropped_desktop_icon(filename, screen_pt, attempt + 1),
            )

