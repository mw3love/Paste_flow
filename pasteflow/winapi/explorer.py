"""탐색기 폴더 도우미 — 커서 아래 탐색기 창의 폴더·하위 폴더 경로 알아내기
(2026-11-16 pasteflow/main.py에서 글자 그대로 옮김 — docs/plan/done/20261116-폴더-구조-1편.md)
"""
import ctypes
import os


def _get_explorer_subfolder_at_cursor(lv_hwnd: int, screen_pt: tuple, current_folder: str):
    """SysListView32에서 screen_pt 위치의 서브폴더 경로 반환 (크로스 프로세스 LVM_HITTEST).
    커서 위치에 폴더 아이콘이 없으면 None.
    """
    LVM_HITTEST = 0x1000 + 18
    LVM_GETITEMTEXTW = 0x1000 + 115
    LVHT_ONITEM = 0x000E
    LVIF_TEXT = 0x0001
    PROCESS_VM = 0x0008 | 0x0010 | 0x0020  # VM_OPERATION | VM_READ | VM_WRITE

    class _HT(ctypes.Structure):
        _fields_ = [
            ("x", ctypes.c_long), ("y", ctypes.c_long),
            ("flags", ctypes.c_uint),
            ("iItem", ctypes.c_int), ("iSubItem", ctypes.c_int),
        ]

    class _LVI(ctypes.Structure):
        _fields_ = [
            ("mask", ctypes.c_uint), ("iItem", ctypes.c_int),
            ("iSubItem", ctypes.c_int), ("state", ctypes.c_uint),
            ("stateMask", ctypes.c_uint),
            ("pszText", ctypes.c_void_p),   # 8 bytes on 64-bit (4 bytes padding before this)
            ("cchTextMax", ctypes.c_int), ("iImage", ctypes.c_int),
            ("lParam", ctypes.c_ssize_t),   # LPARAM
            ("iIndent", ctypes.c_int),
        ]

    HT_SZ = ctypes.sizeof(_HT)
    LVI_SZ = ctypes.sizeof(_LVI)
    TXT_SZ = 1024  # 512 wchars

    try:
        import win32gui, win32process
        pt = win32gui.ScreenToClient(lv_hwnd, screen_pt)
        _, pid = win32process.GetWindowThreadProcessId(lv_hwnd)

        k32 = ctypes.windll.kernel32
        h_proc = k32.OpenProcess(PROCESS_VM, False, pid)
        if not h_proc:
            return None

        TOTAL = HT_SZ + LVI_SZ + TXT_SZ
        remote = k32.VirtualAllocEx(h_proc, None, TOTAL, 0x3000, 0x04)
        if not remote:
            k32.CloseHandle(h_proc)
            return None

        try:
            w = ctypes.c_size_t(0)

            # LVHITTESTINFO 쓰기
            ht = _HT(); ht.x = pt[0]; ht.y = pt[1]; ht.iItem = -1
            k32.WriteProcessMemory(h_proc, remote, ctypes.byref(ht), HT_SZ, ctypes.byref(w))

            idx = ctypes.windll.user32.SendMessageW(lv_hwnd, LVM_HITTEST, 0, remote)
            if idx < 0:
                return None

            ht_out = _HT()
            k32.ReadProcessMemory(h_proc, remote, ctypes.byref(ht_out), HT_SZ, ctypes.byref(w))
            if not (ht_out.flags & LVHT_ONITEM):
                return None

            # LVITEMW + text buffer 쓰기
            lvi_remote = remote + HT_SZ
            txt_remote = remote + HT_SZ + LVI_SZ

            lvi = _LVI()
            lvi.mask = LVIF_TEXT
            lvi.iItem = idx
            lvi.pszText = txt_remote
            lvi.cchTextMax = 512
            k32.WriteProcessMemory(h_proc, lvi_remote, ctypes.byref(lvi), LVI_SZ, ctypes.byref(w))
            ctypes.windll.user32.SendMessageW(lv_hwnd, LVM_GETITEMTEXTW, idx, lvi_remote)

            raw = (ctypes.c_char * TXT_SZ)()
            k32.ReadProcessMemory(h_proc, txt_remote, ctypes.byref(raw), TXT_SZ, ctypes.byref(w))

            name = bytes(raw).decode('utf-16-le').rstrip('\x00')
            if not name:
                return None

            path = os.path.join(current_folder, name)
            return path if os.path.isdir(path) else None

        finally:
            k32.VirtualFreeEx(h_proc, remote, 0, 0x8000)
            k32.CloseHandle(h_proc)

    except Exception:
        return None


def _get_explorer_folder(hwnd: int, screen_pt: tuple = None):
    """CabinetWClass HWND → 드롭 대상 폴더 경로 반환. 실패 시 None.

    screen_pt 제공 시: 커서 위치에 서브폴더 아이콘이 있으면 그 경로,
    빈 공간이면 현재 탐색 중인 폴더 경로.
    Qt 메인 스레드 전용 (COM이 MTA로 이미 초기화된 환경에서 호출).
    """
    candidates = []  # [(location_name, path), ...]
    try:
        import win32com.client
        import win32gui as _wg
        import win32con as _wc
        shell = win32com.client.Dispatch("Shell.Application")
        for window in shell.Windows():
            try:
                w_hwnd = int(window.HWND)
                if w_hwnd == 0:
                    continue  # 유효하지 않은 HWND (IE 잔재 COM 항목 등) 건너뜀
                # 직접 HWND 일치 OR 탭 자체 HWND의 root ancestor가 일치 (탭 여러 개 케이스)
                w_root = _wg.GetAncestor(w_hwnd, _wc.GA_ROOT)
                if w_hwnd == hwnd or w_root == hwnd:
                    candidates.append((window.LocationName, window.Document.Folder.Self.Path))
            except Exception:
                continue
    except Exception:
        pass

    if not candidates:
        return None

    if len(candidates) == 1:
        current_folder = candidates[0][1]
    else:
        # 탭 여러 개: 창 제목 startswith + 최장 매칭으로 활성 탭 선택
        # 창 제목 형태: '{활성탭명} 및 추가 탭 N - 파일 탐색기'
        current_folder = None
        try:
            title = _wg.GetWindowText(hwnd)
            best_path, best_len = None, 0
            for loc_name, path in candidates:
                if title.startswith(loc_name) and len(loc_name) > best_len:
                    best_path, best_len = path, len(loc_name)
            current_folder = best_path  # 매칭 실패 시 None → 잘못된 폴더에 저장 방지
        except Exception:
            pass

    if not current_folder or not screen_pt:
        return current_folder

    # SysListView32 찾기 → 서브폴더 히트 테스트
    lv_hwnd = [None]
    def _cb(h, _):
        try:
            import win32gui as _wg
            if _wg.GetClassName(h) == 'SysListView32':
                lv_hwnd[0] = h
                return False
        except Exception:
            pass
        return True
    try:
        import win32gui
        win32gui.EnumChildWindows(hwnd, _cb, None)
    except Exception:
        pass

    if lv_hwnd[0]:
        sub = _get_explorer_subfolder_at_cursor(lv_hwnd[0], screen_pt, current_folder)
        if sub:
            print(f"[DBG] 서브폴더 히트: {sub!r}")
            return sub

    return current_folder
