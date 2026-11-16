"""바탕화면 도우미 — 바탕화면 경로·아이콘 목록 창 찾기·떨어뜨린 파일 아이콘 자리 잡기
(2026-11-16 pasteflow/main.py에서 글자 그대로 옮김 — docs/plan/done/20261116-폴더-구조-1편.md)
"""
import ctypes
import os


def _get_desktop_path() -> str:
    """사용자 바탕화면 경로 반환.
    Qt 메인 스레드 전용 (COM이 MTA로 이미 초기화된 환경에서 호출).
    """
    try:
        import win32com.client
        return win32com.client.Dispatch("WScript.Shell").SpecialFolders("Desktop")
    except Exception:
        return os.path.expanduser("~/Desktop")


def _find_desktop_listview():
    """바탕화면 아이콘 SysListView32 핸들 반환(못 찾으면 None).

    보통 Progman 아래 SHELLDLL_DefView\\SysListView32이지만, 배경화면 변경 등으로
    SHELLDLL_DefView가 별도 최상위 WorkerW로 옮겨가는 잘 알려진 Windows 동작이 있어
    Progman에서 못 찾으면 최상위 WorkerW들도 함께 뒤진다.
    """
    import win32gui

    def _find_in(top_hwnd):
        result = [None]
        def _cb(h, _):
            try:
                if win32gui.GetClassName(h) == "SHELLDLL_DefView":
                    def _cb2(h2, _):
                        try:
                            if win32gui.GetClassName(h2) == "SysListView32":
                                result[0] = h2
                                return False
                        except Exception:
                            pass
                        return True
                    win32gui.EnumChildWindows(h, _cb2, None)
                    if result[0]:
                        return False
            except Exception:
                pass
            return True
        try:
            win32gui.EnumChildWindows(top_hwnd, _cb, None)
        except Exception:
            pass
        return result[0]

    try:
        progman = win32gui.FindWindow("Progman", None)
    except Exception:
        progman = None
    if progman:
        lv = _find_in(progman)
        if lv:
            return lv

    found = [None]
    def _enum_top(h, _):
        try:
            if win32gui.GetClassName(h) == "WorkerW":
                lv = _find_in(h)
                if lv:
                    found[0] = lv
                    return False
        except Exception:
            pass
        return True
    try:
        win32gui.EnumWindows(_enum_top, None)
    except Exception:
        pass
    return found[0]


def _position_desktop_icon(lv_hwnd: int, filename: str, screen_pt: tuple) -> bool:
    """바탕화면 리스트뷰에서 filename 아이콘을 screen_pt(전역 스크린 좌표) 근처에 배치한다.

    가짜 드래그(파일시스템 직접 쓰기)라 Explorer가 실제 드롭 좌표를 모른 채 자기
    기본 배치 규칙(빈 격자 순서 등)을 쓰는 문제를 보완 — 실제 탐색기 드래그가 쓰는
    것과 같은 LVM_SETITEMPOSITION32(크로스 프로세스, LVM_HITTEST와 동일 기법)로
    좌표를 직접 지정해 진짜 OS 드래그와 같은 자리에 뜨게 한다. "아이콘 자동 정렬"이
    켜져 있으면 Explorer가 다시 정렬해 무효화되지만, 실패해도 무해(시각적 편의일 뿐)
    하므로 항상 bool만 반환하고 예외를 삼킨다.
    """
    LVM_GETITEMCOUNT = 0x1000 + 4
    LVM_GETITEMTEXTW = 0x1000 + 115
    LVM_SETITEMPOSITION32 = 0x1000 + 49
    LVM_GETITEMSPACING = 0x1000 + 51
    LVM_GETORIGIN = 0x1000 + 41
    LVIF_TEXT = 0x0001
    PROCESS_VM = 0x0008 | 0x0010 | 0x0020  # VM_OPERATION | VM_READ | VM_WRITE

    class _LVI(ctypes.Structure):
        _fields_ = [
            ("mask", ctypes.c_uint), ("iItem", ctypes.c_int),
            ("iSubItem", ctypes.c_int), ("state", ctypes.c_uint),
            ("stateMask", ctypes.c_uint),
            ("pszText", ctypes.c_void_p),
            ("cchTextMax", ctypes.c_int), ("iImage", ctypes.c_int),
            ("lParam", ctypes.c_ssize_t),
            ("iIndent", ctypes.c_int),
        ]

    class _POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    LVI_SZ = ctypes.sizeof(_LVI)
    TXT_SZ = 1024  # 512 wchars
    PT_SZ = ctypes.sizeof(_POINT)

    try:
        import win32gui
        user32 = ctypes.windll.user32
        k32 = ctypes.windll.kernel32

        count = user32.SendMessageW(lv_hwnd, LVM_GETITEMCOUNT, 0, 0)
        if count <= 0:
            return False

        import win32process
        _, pid = win32process.GetWindowThreadProcessId(lv_hwnd)
        h_proc = k32.OpenProcess(PROCESS_VM, False, pid)
        if not h_proc:
            return False

        TOTAL = LVI_SZ + TXT_SZ + PT_SZ
        remote = k32.VirtualAllocEx(h_proc, None, TOTAL, 0x3000, 0x04)
        if not remote:
            k32.CloseHandle(h_proc)
            return False

        try:
            w = ctypes.c_size_t(0)
            lvi_remote = remote
            txt_remote = remote + LVI_SZ
            pt_remote = remote + LVI_SZ + TXT_SZ

            # filename과 일치하는 아이템 인덱스 찾기 (새로 생긴 파일이라 정렬 위치 예측 불가).
            # "파일 확장자 숨기기"(Windows 기본값)가 켜져 있으면 리스트뷰 텍스트에 확장자가
            # 없다(실측 확인) — 양쪽 다 확장자를 떼고 비교해야 켜짐/꺼짐 무관하게 매칭된다.
            target_stem = os.path.splitext(filename)[0]
            target_idx = -1
            for i in range(count):
                lvi = _LVI()
                lvi.mask = LVIF_TEXT
                lvi.iItem = i
                lvi.pszText = txt_remote
                lvi.cchTextMax = 512
                k32.WriteProcessMemory(h_proc, lvi_remote, ctypes.byref(lvi), LVI_SZ, ctypes.byref(w))
                user32.SendMessageW(lv_hwnd, LVM_GETITEMTEXTW, i, lvi_remote)

                # 버퍼를 매 반복 재사용하므로 이전(더 긴) 문자열의 잔여 바이트가 널 뒤에
                # 남을 수 있다 — rstrip이 아니라 첫 널에서 끊어야 한다(실측으로 발견).
                raw = (ctypes.c_char * TXT_SZ)()
                k32.ReadProcessMemory(h_proc, txt_remote, ctypes.byref(raw), TXT_SZ, ctypes.byref(w))
                name = bytes(raw).decode('utf-16-le', errors='ignore').split('\x00', 1)[0]
                if os.path.splitext(name)[0] == target_stem:
                    target_idx = i
                    break

            if target_idx < 0:
                return False  # Explorer가 아직 새 파일을 리스트뷰에 반영 안 함 — 호출측 재시도

            client_pt = win32gui.ScreenToClient(lv_hwnd, screen_pt)

            # 스크롤 오프셋(대개 0,0) 보정 — LVM_SETITEMPOSITION32는 스크롤 무관 레이아웃 좌표 기준
            origin = _POINT()
            k32.WriteProcessMemory(h_proc, pt_remote, ctypes.byref(origin), PT_SZ, ctypes.byref(w))
            user32.SendMessageW(lv_hwnd, LVM_GETORIGIN, 0, pt_remote)
            k32.ReadProcessMemory(h_proc, pt_remote, ctypes.byref(origin), PT_SZ, ctypes.byref(w))

            # 아이콘 간격으로 대략적인 아이콘 크기를 추정해 커서가 아이콘 중앙에 오게 보정
            spacing = user32.SendMessageW(lv_hwnd, LVM_GETITEMSPACING, 0, 0) & 0xFFFFFFFF
            icon_w = spacing & 0xFFFF
            icon_h = (spacing >> 16) & 0xFFFF

            dest = _POINT()
            dest.x = client_pt[0] + origin.x - icon_w // 2
            dest.y = client_pt[1] + origin.y - icon_h // 2
            k32.WriteProcessMemory(h_proc, pt_remote, ctypes.byref(dest), PT_SZ, ctypes.byref(w))

            user32.SendMessageW(lv_hwnd, LVM_SETITEMPOSITION32, target_idx, pt_remote)
            return True
        finally:
            k32.VirtualFreeEx(h_proc, remote, 0, 0x8000)
            k32.CloseHandle(h_proc)
    except Exception:
        return False
