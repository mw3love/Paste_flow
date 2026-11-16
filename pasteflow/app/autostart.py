"""Windows 로그온 자동 시작(HKCU Run + VBS) — pasteflow/main.py에서 글자 그대로 옮김(2026-11-16 폴더 구조 2편)."""

import os
import sys

from pasteflow.app.data_paths import _get_local_data_dir


class AutoStartMixin:
    # 로그온 직후 대기 시간(초). 이 시간이 지난 뒤 launcher VBS가 PasteFlow를 실행한다.
    # 옛 15초는 "코드가 구글 드라이브 마운트 경로에 있어 그 마운트를 기다림"이 목적이었으나,
    # 지금은 코드 경로가 로컬 디스크(Dev\Paste_flow)라 그 이유가 더 이상 적용되지 않는다(2026-08-05 실측 확인).
    # 2초는 로그온 초반 COM/WScript.Shell 초기화 여유만 남긴 값.
    _AUTOSTART_DRIVE_WAIT_SEC = 2

    # 작업 관리자 → 시작프로그램 탭은 Run 명령이 가리키는 파일의 설명을 이름으로 보여준다.
    # `wscript.exe "x.vbs"`로 등록하면 다른 VBS 자동시작과 똑같이 "Microsoft ® Windows Based
    # Script Host"로 떠 구분이 안 되므로, Run 값이 .vbs를 직접 가리키게 하고 파일 이름으로
    # 식별한다(2026-11-04 작업 관리자 실측: .vbs 직접 등록 시 파일 이름이 그대로 표시됨).
    # 한글 파일명은 wscript 코드페이지 문제 전례가 있어 ASCII로 둔다.
    _AUTOSTART_VBS_NAME = "PasteFlow-autostart.vbs"
    _LEGACY_AUTOSTART_VBS_NAME = "autostart_launcher.vbs"

    def _write_autostart_launcher_vbs(self, target_cmd: str) -> str:
        """%LOCALAPPDATA%\\PasteFlow\\PasteFlow-autostart.vbs 생성/갱신.
        VBS는 _AUTOSTART_DRIVE_WAIT_SEC초 대기 후 target_cmd를 hidden으로 실행한다.
        wscript.exe로 실행되므로 콘솔 창이 일절 뜨지 않는다.
        실행 단계마다 %LOCALAPPDATA%\\PasteFlow\\logs\\autostart.log에 로그를 남겨
        boot 시 자동 시작 실패 원인(Drive 마운트 지연·경로 깨짐 등)을 추적 가능.
        """
        wait_ms = self._AUTOSTART_DRIVE_WAIT_SEC * 1000
        escaped = target_cmd.replace('"', '""')  # VBS 문자열 내 큰따옴표 이스케이프
        vbs = (
            'Dim fso, logPath, logDir\r\n'
            'Set fso = CreateObject("Scripting.FileSystemObject")\r\n'
            'logDir = CreateObject("WScript.Shell").ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\\PasteFlow\\logs"\r\n'
            'If Not fso.FolderExists(logDir) Then fso.CreateFolder(logDir)\r\n'
            'logPath = logDir & "\\autostart.log"\r\n'
            'Sub LogMsg(msg)\r\n'
            '    Dim f\r\n'
            '    Set f = fso.OpenTextFile(logPath, 8, True)\r\n'
            '    f.WriteLine Now & "  " & msg\r\n'
            '    f.Close\r\n'
            'End Sub\r\n'
            f'LogMsg "VBS triggered, sleeping {self._AUTOSTART_DRIVE_WAIT_SEC}s"\r\n'
            f'WScript.Sleep {wait_ms}\r\n'
            'On Error Resume Next\r\n'
            'Dim objShell\r\n'
            'Set objShell = CreateObject("WScript.Shell")\r\n'
            f'objShell.Run "{escaped}", 0, False\r\n'
            'If Err.Number <> 0 Then\r\n'
            '    LogMsg "Run FAILED: " & Err.Number & " " & Err.Description\r\n'
            'Else\r\n'
            '    LogMsg "Run dispatched OK"\r\n'
            'End If\r\n'
        )
        path = os.path.join(_get_local_data_dir(), self._AUTOSTART_VBS_NAME)
        # UTF-16 LE + BOM으로 저장해야 한국어 Windows의 wscript.exe가 한글 경로를
        # CP949로 잘못 해석해 objShell.Run이 조용히 실패하는 문제를 막을 수 있다.
        with open(path, "w", encoding="utf-16") as f:
            f.write(vbs)
        # 옛 이름 파일은 Run 값이 더 이상 가리키지 않으므로 정리.
        try:
            os.remove(os.path.join(_get_local_data_dir(), self._LEGACY_AUTOSTART_VBS_NAME))
        except OSError:
            pass
        return path

    def _set_auto_start(self, enable: bool, clear_approved: bool = True):
        import winreg
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        try:
            reg_key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE)
            if enable:
                if getattr(sys, "frozen", False):
                    target_cmd = f'"{sys.executable}"'
                else:
                    pythonw = sys.executable
                    if pythonw.lower().endswith("python.exe"):
                        pythonw = pythonw[:-len("python.exe")] + "pythonw.exe"
                    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
                    run_pyw = os.path.join(project_root, "run.pyw")
                    target_cmd = f'"{pythonw}" "{run_pyw}"'
                vbs_path = self._write_autostart_launcher_vbs(target_cmd)
                # .vbs를 직접 등록 — 로그온 시 Explorer가 연결 프로그램(wscript)으로 실행한다.
                cmd = f'"{vbs_path}"'
                winreg.SetValueEx(reg_key, "PasteFlow", 0, winreg.REG_SZ, cmd)
                # 작업 관리자 → 시작 앱 탭에서 disabled로 토글된 상태가 있으면 정리.
                # 이 플래그가 있으면 Run 키가 등록되어 있어도 logon 시 Windows가 차단한다.
                if clear_approved:
                    self._clear_startup_approved_flag()
            else:
                try:
                    winreg.DeleteValue(reg_key, "PasteFlow")
                except FileNotFoundError:
                    pass
            winreg.CloseKey(reg_key)
        except Exception as e:
            print(f"[Settings] 자동 시작 설정 실패: {e}")

    def _clear_startup_approved_flag(self):
        """HKCU\\...\\Explorer\\StartupApproved\\Run\\PasteFlow 값을 삭제.
        해당 값의 첫 바이트가 03이면 사용자가 작업 관리자에서 disabled 처리한 것이며,
        이 상태에서는 Run 키가 정상 등록되어도 Windows가 logon 시 발화시키지 않는다.
        값이 없으면 Windows는 enabled로 간주하므로 삭제만으로 충분하다.
        """
        import winreg
        approved_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, approved_path, 0, winreg.KEY_SET_VALUE)
            try:
                winreg.DeleteValue(key, "PasteFlow")
            except FileNotFoundError:
                pass
            winreg.CloseKey(key)
        except OSError:
            pass

    def _sync_auto_start_from_registry(self):
        import winreg
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        registered = False
        current_cmd = ""
        try:
            reg_key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ)
            try:
                current_cmd, _ = winreg.QueryValueEx(reg_key, "PasteFlow")
                registered = True
            except FileNotFoundError:
                pass
            winreg.CloseKey(reg_key)
        except OSError:
            pass
        self.db.set_setting("auto_start", "1" if registered else "0")
        # 옛 등록(`wscript.exe "...autostart_launcher.vbs"`)이면 새 이름·직접 등록으로 1회 이전.
        # 새 형식이면 조용히 스킵(idempotent). Run 값 이름("PasteFlow")은 그대로라 작업 관리자의
        # 사용/사용 안 함 상태(StartupApproved)도 그대로 유지돼야 하므로 clear_approved=False —
        # 작업 관리자에서 꺼 둔 자동 시작을 이전이 몰래 다시 켜면 안 된다.
        if registered and self._AUTOSTART_VBS_NAME not in str(current_cmd):
            self._set_auto_start(True, clear_approved=False)

