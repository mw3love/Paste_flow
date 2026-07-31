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

# OLE 클립보드 포맷 — IDataObject 인터페이스 마샬링 데이터라 실제 콘텐츠가 아니다.
# 평면 바이트로 복사해 되살리면 원본 앱이 클립보드를 놓는 순간 stale 참조가 되어,
# 한글(HWP)처럼 붙여넣기 시 OLE 데이터를 평면 포맷보다 우선 채택하는 앱에서
# "아무것도 안 붙음"을 유발한다. 캡처 단계에서 제외해 평면 포맷으로 폴백시킨다.
# (이름 소문자·trailing 공백 제거 후 비교)
_OLE_CLIPBOARD_FORMATS = {
    "dataobject",
    "ole private data",
    "object descriptor",
    "embed source",
    "embedded object",
    "link source",
    "link source descriptor",
}

# 한글(HWP) 네이티브 클립보드 포맷 — 한글은 붙여넣기 시 자기 고유 포맷을 평면
# 포맷(HTML/RTF/텍스트)보다 최우선 채택하는데, 우리가 복원한 네이티브 블롭은
# (동반 OLE 객체가 사라져) 한글이 읽으면 빈 내용이라 "아무것도 안 붙음"을 유발한다.
# 제외하면 한글이 평면 포맷으로 폴백해 정상 붙여넣기 — 단 한글 전용 객체 충실도는 포기.
_HWP_NATIVE_FORMATS = {
    "hwp native",
    "hwp_native_info",
}

# PIL이 시그니처만으로 바로 여는 파일 포맷들. raw CF_DIB(BITMAPINFOHEADER)는 이 중
# 무엇으로도 시작하지 않으므로(biSize=12/40/108/124) 이 판별로 둘을 가를 수 있다.
_ENCODED_IMAGE_SIGNATURES = (
    b'\x89PNG',           # PNG
    b'\xff\xd8\xff',      # JPEG
    b'GIF8',              # GIF
    b'RIFF',              # WebP (RIFF....WEBP)
    b'BM',                # BMP (파일 헤더 포함)
    b'II*\x00',           # TIFF (little endian)
    b'MM\x00*',           # TIFF (big endian)
    b'\x00\x00\x01\x00',  # ICO
)


def is_encoded_image(data: bytes) -> bool:
    """파일 포맷 이미지(PNG·JPEG·…)인가? False면 raw CF_DIB로 본다."""
    return any(data.startswith(sig) for sig in _ENCODED_IMAGE_SIGNATURES)


def _dib_to_bmp(dib_data: bytes) -> bytes:
    """raw CF_DIB(BITMAPINFOHEADER) → BMP 파일 바이트 (14바이트 파일 헤더 부착)."""
    import struct
    if len(dib_data) < 40:
        raise ValueError("DIB too short")
    bi_size = struct.unpack_from('<I', dib_data, 0)[0]
    bi_bit_count = struct.unpack_from('<H', dib_data, 14)[0]
    bi_compression = struct.unpack_from('<I', dib_data, 16)[0]
    bi_clr_used = struct.unpack_from('<I', dib_data, 32)[0]
    # 색상 테이블 크기 계산 (24/32bpp는 0)
    if bi_clr_used == 0 and bi_bit_count in (1, 4, 8):
        bi_clr_used = 1 << bi_bit_count
    # BI_BITFIELDS(3): BITMAPINFOHEADER(40) 뒤에 RGB 마스크 3×DWORD가 붙는다.
    # 이걸 빠뜨리면 픽셀 시작점이 12바이트 밀려 색이 뒤집혀 읽힌다(실측: 빨강→파랑).
    # 마스크를 헤더에 포함하는 V4/V5(108/124)는 해당 없음.
    mask_bytes = 12 if (bi_compression == 3 and bi_size == 40) else 0
    pixel_offset = 14 + bi_size + mask_bytes + bi_clr_used * 4
    file_size = 14 + len(dib_data)
    bmp_header = b'BM' + struct.pack('<IHHI', file_size, 0, 0, pixel_offset)
    return bmp_header + dib_data


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

    def mark_self_write(self, item: ClipboardItem, duration: float = 0.5):
        """자체 클립보드 쓰기 — 시간창 + 해시 백스톱을 함께 건다.

        set_self_triggered의 시간창(0.5초)은 WM_CLIPBOARDUPDATE가 그 안에 도착해야
        효력이 있는데, 대용량 이미지 쓰기·이벤트 배달 지연으로 늦게 오면 만료돼 뚫린다.
        그때 직접 저장 경로(_persist_clipboard_item)는 _last_hash를 안 갱신해 해시 방어도
        무력이라 같은 항목이 히스토리에 두 번 저장됐다(Alt+F2 캡처 이중 저장의 원인).
        여기서 _last_hash를 함께 세팅해, 늦은 이벤트가 시간창을 넘겨도 해시로 걸러지게 한다.
        """
        with self._lock:
            self._ignore_until = time.monotonic() + duration
            self._last_hash = self._compute_hash(item)

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

        items = self._read_clipboard()
        if not items:
            print("[Monitor] 클립보드 읽기 실패 (빈 목록)")
            return

        # 여러 파일을 한 번에 복사했으면 파일마다 순서대로 중복 체크 + 콜백
        # (일반적인 단일 항목 경로도 리스트 길이 1로 동일하게 흐른다).
        for item in items:
            content_hash = self._compute_hash(item)
            if content_hash == self._last_hash:
                print(f"[Monitor] 중복 해시 — 스킵")
                if self.on_duplicate:
                    self.on_duplicate()
                continue
            self._last_hash = content_hash

            preview = (item.preview_text or "")[:30]
            print(f"[Monitor] 새 항목: '{preview}'")
            if self.on_new_item:
                self.on_new_item(item)

    def _read_clipboard(self) -> list[ClipboardItem]:
        """클립보드에서 데이터 읽기. 탐색기에서 이미지 파일을 여러 개 복사하면 파일마다
        별도 항목을 반환한다(그 외 경로는 항상 0개 또는 1개)."""
        _opened = False
        try:
            win32clipboard.OpenClipboard()
            _opened = True
        except Exception:
            return []

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

            # CF_HDROP: 탐색기에서 이미지 파일 복사 — 파일 1개면 이미지로, 여러 개면
            # 파일마다 별도 항목을 만들어야 하므로 여기서 바로 리스트로 반환한다.
            _IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.bmp', '.gif', '.webp', '.tiff', '.tif'}
            if image_data is None and win32clipboard.IsClipboardFormatAvailable(win32con.CF_HDROP):
                try:
                    files = win32clipboard.GetClipboardData(win32con.CF_HDROP)
                    image_files = [f for f in files if os.path.splitext(f)[1].lower() in _IMAGE_EXTS]
                    if len(files) > 1 and image_files:
                        items = []
                        for fpath in image_files:
                            try:
                                with open(fpath, 'rb') as f:
                                    file_bytes = f.read()
                            except Exception:
                                continue
                            items.append(ClipboardItem(
                                content_type="image",
                                image_data=file_bytes,
                                thumbnail=self._create_thumbnail(file_bytes),
                            ))
                        return items
                    if len(files) == 1:
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
                try:
                    fmt = win32clipboard.EnumClipboardFormats(fmt)
                except Exception:
                    # 클립보드 소유권 상실(1418) 등 — 이미 읽은 데이터로 계속
                    break
                if fmt == 0:
                    break
                if fmt in known_fmts:
                    continue
                try:
                    fmt_name = win32clipboard.GetClipboardFormatName(fmt)
                except Exception:
                    fmt_name = ""
                # OLE 마샬링 포맷·한글 네이티브 포맷은 복원하면 한글 붙여넣기를 깨뜨림 → 제외
                _nm = fmt_name.strip().lower()
                if _nm in _OLE_CLIPBOARD_FORMATS or _nm in _HWP_NATIVE_FORMATS:
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
                return []  # 지원하지 않는 형식

            return [ClipboardItem(
                content_type=content_type,
                text_content=text_content,
                image_data=image_data,
                html_content=html_content,
                rtf_content=rtf_content,
                thumbnail=thumbnail,
                extra_formats=extra_formats or None,
            )]
        finally:
            if _opened:
                try:
                    win32clipboard.CloseClipboard()
                except Exception:
                    pass

    def _create_thumbnail(self, dib_data: bytes) -> Optional[bytes]:
        """이미지 데이터에서 썸네일 생성

        PIL이 직접 여는 파일 포맷(PNG·JPEG·GIF·WebP·BMP…)은 그대로 열고, 못 여는
        raw DIB(BITMAPINFOHEADER)만 BMP 파일 헤더를 조립해 인식시킨다.
        "PNG가 아니면 DIB"로 갈랐던 옛 로직은 탐색기에서 복사한 JPEG(CF_HDROP 경로가
        파일 바이트를 그대로 싣는다)에 가짜 BMP 헤더를 붙여 썸네일 생성이 실패했다.
        """
        try:
            try:
                img = Image.open(io.BytesIO(dib_data))
            except Exception:
                img = Image.open(io.BytesIO(_dib_to_bmp(dib_data)))
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
            # 전체 바이트를 해시한다. 앞부분만(예: [:4096]) 보면 DIB(비압축)는
            # 픽셀이 아래→위로 저장돼 첫 바이트가 이미지 맨 아래 줄이고, 같은 크기
            # 캡처에 주석만 덮은 이미지는 바이트 길이까지 동일해 오탐(중복)이 난다.
            h.update(item.image_data)
        if item.html_content:
            h.update(item.html_content.encode("utf-8"))
        return h.hexdigest()
