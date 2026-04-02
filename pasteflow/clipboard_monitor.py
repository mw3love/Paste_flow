"""클립보드 감시 — WM_CLIPBOARDUPDATE 이벤트 기반"""
import ctypes
import io
import os
import hashlib
import time
import threading
from typing import Optional, Callable

import win32clipboard
import win32con
import win32gui
import win32api
from PIL import Image

from pasteflow.models import ClipboardItem

# Windows 메시지 상수
WM_CLIPBOARDUPDATE = 0x031D
CF_HTML = win32clipboard.RegisterClipboardFormat("HTML Format")
CF_RTF = win32clipboard.RegisterClipboardFormat("Rich Text Format")
CF_PNG = win32clipboard.RegisterClipboardFormat("PNG")

# 썸네일 크기
THUMBNAIL_SIZE = (80, 60)


class ClipboardMonitor:
    """WM_CLIPBOARDUPDATE 기반 클립보드 감시

    클립보드 변경 시 콜백 호출. self_triggered 플래그로 자체 쓰기 무시.
    """

    def __init__(self, on_new_item: Optional[Callable[[ClipboardItem], None]] = None,
                 on_duplicate: Optional[Callable[[], None]] = None):
        self.on_new_item = on_new_item  # 모든 복사 경로: DB + 큐 추가
        self.on_duplicate = on_duplicate
        self._ignore_until: float = 0.0  # 시간 기반 무시
        self._lock = threading.Lock()
        self._last_hash: Optional[str] = None
        self._hwnd = None
        self._running = False

    def set_self_triggered(self, duration: float = 0.5):
        """클립보드 이벤트를 duration초 동안 무시"""
        with self._lock:
            self._ignore_until = time.monotonic() + duration

    def start(self):
        """클립보드 리스너 등록 (숨겨진 윈도우 생성)"""
        if self._running:
            return

        wc = win32gui.WNDCLASS()
        # wndproc dict를 인스턴스 변수로 저장 (GC 방지)
        self._wnd_proc_map = {WM_CLIPBOARDUPDATE: self._on_wm_clipboardupdate}
        wc.lpfnWndProc = self._wnd_proc_map
        wc.lpszClassName = "PasteFlowClipboardMonitor"
        wc.hInstance = win32api.GetModuleHandle(None)

        try:
            class_atom = win32gui.RegisterClass(wc)
        except Exception:
            return

        self._hwnd = win32gui.CreateWindow(
            class_atom, "PasteFlow Monitor",
            0, 0, 0, 0, 0, 0, 0, wc.hInstance, None
        )

        if self._hwnd:
            ctypes.windll.user32.AddClipboardFormatListener(self._hwnd)
            self._running = True

    def stop(self):
        """클립보드 리스너 해제"""
        if self._hwnd:
            ctypes.windll.user32.RemoveClipboardFormatListener(self._hwnd)
            win32gui.DestroyWindow(self._hwnd)
            self._hwnd = None
        self._running = False

    def _on_wm_clipboardupdate(self, hwnd, msg, wparam, lparam):
        """WM_CLIPBOARDUPDATE 핸들러"""
        self._on_clipboard_changed()
        return 0

    def _on_clipboard_changed(self):
        """클립보드 변경 이벤트 처리"""
        # 자체 트리거 무시 (시간 기반)
        with self._lock:
            if time.monotonic() < self._ignore_until:
                print("[Monitor] 자체 트리거 무시")
                return

        item = self._read_clipboard()
        if item is None:
            print("[Monitor] 클립보드 읽기 실패 (None)")
            return

        # 중복 체크
        content_hash = self._compute_hash(item)
        if content_hash == self._last_hash:
            print(f"[Monitor] 중복 해시 — 스킵")
            if self.on_duplicate:
                self.on_duplicate()
            return
        self._last_hash = content_hash

        preview = (item.preview_text or "")[:30]
        print(f"[Monitor] 새 항목: '{preview}'")
        if self.on_new_item:
            self.on_new_item(item)

    def _read_clipboard(self) -> Optional[ClipboardItem]:
        """클립보드에서 데이터 읽기"""
        try:
            win32clipboard.OpenClipboard()
        except Exception:
            return None

        try:
            text_content = None
            image_data = None
            html_content = None
            rtf_content = None
            thumbnail = None
            content_type = None

            # HTML 확인
            if win32clipboard.IsClipboardFormatAvailable(CF_HTML):
                try:
                    raw = win32clipboard.GetClipboardData(CF_HTML)
                    if isinstance(raw, bytes):
                        html_content = raw.decode("utf-8", errors="replace")
                    else:
                        html_content = raw
                except Exception:
                    pass

            # RTF 확인
            if win32clipboard.IsClipboardFormatAvailable(CF_RTF):
                try:
                    raw = win32clipboard.GetClipboardData(CF_RTF)
                    if isinstance(raw, bytes):
                        rtf_content = raw.decode("utf-8", errors="replace")
                    else:
                        rtf_content = raw
                except Exception:
                    pass

            # 텍스트 확인
            if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                try:
                    text_content = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
                except Exception:
                    pass

            # 이미지 확인 — PNG 우선 (노션 등), DIB 대체
            if win32clipboard.IsClipboardFormatAvailable(CF_PNG):
                try:
                    png_data = win32clipboard.GetClipboardData(CF_PNG)
                    image_data = bytes(png_data)
                    thumbnail = self._create_thumbnail(png_data)
                except Exception:
                    pass

            if image_data is None and win32clipboard.IsClipboardFormatAvailable(win32con.CF_DIB):
                try:
                    dib_data = win32clipboard.GetClipboardData(win32con.CF_DIB)
                    image_data = bytes(dib_data)
                    thumbnail = self._create_thumbnail(dib_data)
                except Exception:
                    pass

            # CF_HDROP: 탐색기에서 이미지 파일 1개 복사 — 파일을 읽어 이미지로 처리
            _IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.bmp', '.gif', '.webp', '.tiff', '.tif'}
            if image_data is None and win32clipboard.IsClipboardFormatAvailable(win32con.CF_HDROP):
                try:
                    files = win32clipboard.GetClipboardData(win32con.CF_HDROP)
                    if files and len(files) == 1:
                        fpath = files[0]
                        if os.path.splitext(fpath)[1].lower() in _IMAGE_EXTS:
                            with open(fpath, 'rb') as f:
                                file_bytes = f.read()
                            image_data = file_bytes
                            thumbnail = self._create_thumbnail(file_bytes)
                except Exception:
                    pass

            # 기타 모든 포맷 캡처 (노션 등 앱 전용 포맷 보존)
            extra_formats = {}
            known_fmts = {
                win32con.CF_UNICODETEXT, win32con.CF_TEXT, win32con.CF_OEMTEXT,
                win32con.CF_DIB, win32con.CF_DIBV5, win32con.CF_BITMAP,
                win32con.CF_ENHMETAFILE, win32con.CF_METAFILEPICT,
                win32con.CF_LOCALE, CF_HTML, CF_RTF, CF_PNG,
            }
            fmt = 0
            while True:
                fmt = win32clipboard.EnumClipboardFormats(fmt)
                if fmt == 0:
                    break
                if fmt in known_fmts:
                    continue
                try:
                    data = win32clipboard.GetClipboardData(fmt)
                    if isinstance(data, bytes):
                        extra_formats[fmt] = data
                    elif isinstance(data, str):
                        extra_formats[fmt] = data.encode("utf-8")
                except Exception:
                    pass

            # content_type 결정
            if html_content and text_content:
                content_type = "html"
            elif rtf_content and text_content:
                content_type = "richtext"
            elif image_data:
                content_type = "image"
            elif text_content:
                content_type = "text"
            else:
                return None  # 지원하지 않는 형식

            return ClipboardItem(
                content_type=content_type,
                text_content=text_content,
                image_data=image_data,
                html_content=html_content,
                rtf_content=rtf_content,
                thumbnail=thumbnail,
                extra_formats=extra_formats or None,
            )
        finally:
            win32clipboard.CloseClipboard()

    def _create_thumbnail(self, dib_data: bytes) -> Optional[bytes]:
        """DIB 또는 PNG 데이터에서 썸네일 생성

        PNG는 그대로 열고, DIB(raw BITMAPINFOHEADER)는 BMP 파일 헤더를 추가해 PIL이
        인식할 수 있도록 변환한다.
        """
        import struct
        try:
            if dib_data[:4] == b'\x89PNG':
                img_bytes = dib_data
            else:
                # DIB → BMP: 14바이트 BMP 파일 헤더 추가
                if len(dib_data) < 40:
                    return None
                bi_size = struct.unpack_from('<I', dib_data, 0)[0]
                bi_bit_count = struct.unpack_from('<H', dib_data, 14)[0]
                bi_clr_used = struct.unpack_from('<I', dib_data, 32)[0]
                # 색상 테이블 크기 계산 (24/32bpp는 0)
                if bi_clr_used == 0 and bi_bit_count in (1, 4, 8):
                    bi_clr_used = 1 << bi_bit_count
                pixel_offset = 14 + bi_size + bi_clr_used * 4
                file_size = 14 + len(dib_data)
                bmp_header = b'BM' + struct.pack('<IHHI', file_size, 0, 0, pixel_offset)
                img_bytes = bmp_header + dib_data
            img = Image.open(io.BytesIO(img_bytes))
            img.thumbnail(THUMBNAIL_SIZE)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()
        except Exception:
            return None

    def _compute_hash(self, item: ClipboardItem) -> str:
        """항목 내용 해시 (중복 감지용)"""
        h = hashlib.md5()
        if item.text_content:
            h.update(item.text_content.encode("utf-8"))
        if item.image_data:
            h.update(item.image_data[:1024])  # 이미지는 앞부분만
        if item.html_content:
            h.update(item.html_content.encode("utf-8"))
        return h.hexdigest()
