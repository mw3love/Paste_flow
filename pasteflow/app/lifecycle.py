"""포그라운드 창 추적·종료·IPC 서버 — pasteflow/main.py에서 글자 그대로 옮김(2026-11-16 폴더 구조 2편)."""

import ctypes
import ctypes.wintypes
import json
import threading
import time

from pasteflow.ui.memo_window import MemoWindow


class LifecycleMixin:
    # ── 포그라운드 창 추적 ──

    # 트레이 클릭, 시작 메뉴 등 시스템 UI가 포그라운드를 가져갈 때 무시할 클래스명
    _FG_IGNORE_CLASSES = frozenset({
        "Shell_TrayWnd",           # 작업 표시줄
        "NotifyIconOverflowWindow", # 트레이 오버플로우
        "DV2ControlHost",          # 시작 메뉴 (Win10)
        "Windows.UI.Core.CoreWindow", # 시작 메뉴 / 액션 센터 (Win10/11)
        "TaskListThumbnailWnd",    # 작업 표시줄 썸네일
        "SysShadow",               # 그림자 창
        "#32768",                  # 시스템 드롭다운 메뉴
    })

    def _start_foreground_tracker(self):
        """SetWinEventHook(EVENT_SYSTEM_FOREGROUND)으로 포그라운드 창 연속 추적.

        시스템 창·자기 프로세스 창을 제외한 실제 앱 창이 포그라운드를 가져올 때마다
        _prev_foreground_hwnd를 갱신한다. WINEVENT_OUTOFCONTEXT이므로 _pump_messages가
        DispatchMessageW를 처리할 때 콜백이 호출된다.
        """
        my_pid = ctypes.windll.kernel32.GetCurrentProcessId()
        ignore = self._FG_IGNORE_CLASSES

        WinEventProc = ctypes.WINFUNCTYPE(
            None,
            ctypes.wintypes.HANDLE,
            ctypes.wintypes.DWORD,
            ctypes.wintypes.HWND,
            ctypes.wintypes.LONG,
            ctypes.wintypes.LONG,
            ctypes.wintypes.DWORD,
            ctypes.wintypes.DWORD,
        )

        def _on_fg_changed(hHook, event, hwnd, idObject, idChild, tid, ts):
            if not hwnd:
                return
            try:
                pid_buf = ctypes.wintypes.DWORD()
                ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid_buf))
                if pid_buf.value == my_pid:
                    return  # 패널·팝업 등 PasteFlow 자신의 창 무시
                import win32gui
                cls = win32gui.GetClassName(hwnd)
                if not cls or cls in ignore:
                    return
                self._prev_foreground_hwnd = hwnd
            except Exception:
                pass

        self._fg_proc = WinEventProc(_on_fg_changed)  # GC 방지
        EVENT_SYSTEM_FOREGROUND = 0x0003
        WINEVENT_OUTOFCONTEXT = 0x0000
        self._fg_hook = ctypes.windll.user32.SetWinEventHook(
            EVENT_SYSTEM_FOREGROUND, EVENT_SYSTEM_FOREGROUND,
            None, self._fg_proc, 0, 0, WINEVENT_OUTOFCONTEXT,
        )

    def _quit(self):
        # 패널 위치/크기 저장
        geo = self.panel.get_geometry_dict()
        self.db.set_setting("panel_geometry", json.dumps(geo))

        if self._fg_hook:
            ctypes.windll.user32.UnhookWinEvent(self._fg_hook)
            self._fg_hook = None

        MemoWindow.close_all()  # DB가 닫히기 전에 — 쓰던 메모 저장·빈 메모 삭제
        self._close_live_draw()  # 판서 창을 모두 닫기(숨긴 칠판 포함 — 그림은 버림)
        self.interceptor.stop()
        self.monitor.stop()
        self.tray.hide()
        self.db.close()
        self.app.quit()

    def _start_ipc_server(self):
        """Named pipe 서버 — 두 번째 인스턴스 실행 시 패널 토글 신호 수신."""
        threading.Thread(target=self._ipc_loop, name="ipc-server", daemon=True).start()

    def _ipc_loop(self):
        import win32pipe, win32file, pywintypes
        pipe_name = r"\\.\pipe\PasteFlow_IPC"
        while True:
            try:
                h = win32pipe.CreateNamedPipe(
                    pipe_name,
                    win32pipe.PIPE_ACCESS_INBOUND,
                    win32pipe.PIPE_TYPE_BYTE | win32pipe.PIPE_WAIT,
                    1, 64, 64, 0, None,
                )
                win32pipe.ConnectNamedPipe(h, None)
                win32file.CloseHandle(h)
                self._bridge.panel_toggle.emit()
            except pywintypes.error:
                time.sleep(0.1)

