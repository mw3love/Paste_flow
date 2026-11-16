"""이미지 변환·저장·클립보드 이미지 읽기 도우미
(2026-11-16 pasteflow/main.py에서 글자 그대로 옮김 — docs/plan/done/20261116-폴더-구조-1편.md)
"""
import os
import time

_DIRECT_OPEN_SIGNATURES = (
    b'\xff\xd8\xff',      # JPEG
    b'GIF8',              # GIF
    b'RIFF',              # WebP (RIFF....WEBP)
    b'\x89PNG',           # PNG
    b'BM',                # BMP 파일 헤더 있는 경우
    b'\x00\x00\x01\x00',  # ICO
)

def _image_data_to_png_bytes(image_data: bytes) -> bytes:
    """image_data(PNG/JPEG/GIF/WebP/CF_DIB)를 PNG bytes로 변환.

    PIL이 직접 못 여는 CF_DIB raw는 BMP 파일 헤더를 조립해 인식시킨다(_create_thumbnail과
    동일 기법 — clipboard_monitor._dib_to_bmp 재사용). 파일 저장(_save_image_to_folder)·
    이미지 AI 질의가 공유한다.
    """
    import io
    from PIL import Image
    from pasteflow.clipboard_monitor import _dib_to_bmp

    if any(image_data.startswith(sig) for sig in _DIRECT_OPEN_SIGNATURES):
        # PIL이 직접 인식 가능한 포맷 (PNG/JPEG/GIF/WebP/BMP 파일 등)
        img = Image.open(io.BytesIO(image_data))
    else:
        # CF_DIB raw → BMP 파일 헤더 조립 → Pillow로 인식. _dib_to_bmp는 색상 테이블뿐
        # 아니라 BI_BITFIELDS(compression=3) RGB 마스크 12바이트까지 픽셀 오프셋에 더한다
        # — 손수 조립하던 옛 코드는 이걸 빠뜨려 32bpp BITFIELDS DIB에서 색이 뒤집혔다(빨강↔파랑).
        img = Image.open(io.BytesIO(_dib_to_bmp(image_data)))

    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()


def _save_image_to_folder(image_data: bytes, folder: str, seq: int = None) -> str:
    """image_data(PNG/JPEG/GIF/WebP/CF_DIB)를 folder에 PNG 파일로 저장. 저장 경로 반환.
    seq를 주면 clip_{시각}_{seq:02d}.png — 큐 묶음 저장이 탐색기에서 큐 순서대로 정렬되게."""
    from datetime import datetime

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    if seq is not None:
        ts = f"{ts}_{seq:02d}"
    base_path = os.path.join(folder, f"clip_{ts}.png")
    path = base_path
    suffix = 0
    while os.path.exists(path):
        suffix += 1
        path = os.path.join(folder, f"clip_{ts}_{suffix}.png")

    with open(path, 'wb') as f:
        f.write(_image_data_to_png_bytes(image_data))

    return path


def _save_image_to_drop_temp(image_data: bytes) -> str:
    """image_data를 %TEMP%\\PasteFlow\\ 아래 PNG로 저장하고 절대경로 반환.
    Alt+드래그·우클릭 "파일로 저장 후 경로 복사"에서 공유 사용한다.
    Claude Code CLI 등 "경로 텍스트"를 첨부로 받는 앱에 넘기기 위한 임시 파일.
    """
    import tempfile
    folder = os.path.join(tempfile.gettempdir(), "PasteFlow")
    os.makedirs(folder, exist_ok=True)
    return _save_image_to_folder(image_data, folder)


def _read_image_from_clipboard() -> bytes | None:
    """현재 클립보드에서 이미지 bytes를 읽는다(PNG 우선, 없으면 CF_DIB raw).

    반환값은 그대로 ``_save_image_to_drop_temp`` 에 넘길 수 있다(PNG·DIB 둘 다 처리).
    이미지가 없거나 OpenClipboard 실패 시 None.
    """
    import win32clipboard
    cf_png = win32clipboard.RegisterClipboardFormat("PNG")
    for _ in range(3):
        try:
            win32clipboard.OpenClipboard()
            break
        except Exception:
            time.sleep(0.01)
    else:
        return None
    try:
        if win32clipboard.IsClipboardFormatAvailable(cf_png):
            return win32clipboard.GetClipboardData(cf_png)
        if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_DIB):
            return win32clipboard.GetClipboardData(win32clipboard.CF_DIB)
        # CF_HDROP: 탐색기에서 이미지 파일 1개 복사 — 파일 바이트를 읽어 이미지로 처리.
        # 히스토리 캡처(clipboard_monitor)와 같은 규칙이라, 패널에 이미지로 남는 것은
        # 핀에서도 이미지로 뜬다(경로 텍스트가 아니라).
        if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_HDROP):
            _IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.bmp', '.gif', '.webp', '.tiff', '.tif'}
            files = win32clipboard.GetClipboardData(win32clipboard.CF_HDROP)
            if files and len(files) == 1:
                fpath = files[0]
                if os.path.splitext(fpath)[1].lower() in _IMAGE_EXTS and os.path.exists(fpath):
                    with open(fpath, 'rb') as f:
                        return f.read()
        return None
    except Exception:
        return None
    finally:
        try:
            win32clipboard.CloseClipboard()
        except Exception:
            pass


def _render_text_to_png(text: str) -> bytes:
    """클립보드 텍스트를 다크 배경 이미지(PNG bytes)로 렌더링한다.

    Snipaste의 'paste text to screen'처럼, 텍스트도 화면 핀·주석이 가능하도록
    이미지화한다. 줄바꿈은 보존하고, 최대폭을 넘으면 워드랩한다.
    앱 전체가 다크 테마이므로 배경도 어둡게(테마 BASE) + 밝은 글자(TEXT)로 렌더한다.
    """
    from PyQt6.QtGui import QPixmap, QPainter, QFont, QFontMetrics, QColor
    from PyQt6.QtCore import QRect, Qt, QBuffer, QByteArray
    from pasteflow.ui.theme import BASE, TEXT

    pad = 16
    max_w = 700
    # 한글 글리프 보장 — 기본 QFont는 폴백이 불확실해 두부(□)로 렌더될 수 있음.
    # 맑은 고딕(Win 한글 시스템 폰트) 우선, 영문은 Segoe UI로 폴백.
    font = QFont()
    font.setFamilies(["Malgun Gothic", "Segoe UI"])
    font.setPixelSize(18)
    fm = QFontMetrics(font)
    flags = (int(Qt.TextFlag.TextWordWrap)
             | int(Qt.AlignmentFlag.AlignLeft)
             | int(Qt.AlignmentFlag.AlignTop))

    bounds = fm.boundingRect(QRect(0, 0, max_w, 1_000_000), flags, text)
    w = max(1, bounds.width()) + pad * 2
    h = max(1, bounds.height()) + pad * 2

    pm = QPixmap(w, h)
    pm.fill(QColor(BASE))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    p.setFont(font)
    p.setPen(QColor(TEXT))  # 다크 배경 위 밝은 글자
    p.drawText(QRect(pad, pad, w - pad * 2, h - pad * 2), flags, text)
    p.end()

    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QBuffer.OpenModeFlag.WriteOnly)
    pm.save(buf, "PNG")
    return bytes(ba.data())


def _qpixmap_to_dib(pixmap) -> bytes:
    """QPixmap을 CF_DIB(BITMAPFILEHEADER 없는 BMP) 바이트로 변환.

    스크린샷은 불투명하므로 RGB32로 평탄화한다(알파 채널이 일부 앱에서 검게 처리되는 것 방지).
    클립보드 CF_DIB와 동일 포맷이라 _set_clipboard(PNG 시그니처 아니면 CF_DIB 처리)·
    _save_image_to_folder·_create_thumbnail에 그대로 넘길 수 있다.
    """
    from PyQt6.QtGui import QImage
    from PyQt6.QtCore import QBuffer, QByteArray
    img = pixmap.toImage().convertToFormat(QImage.Format.Format_RGB32)
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QBuffer.OpenModeFlag.WriteOnly)
    img.save(buf, "BMP")
    return bytes(ba.data())[14:]  # 14바이트 BITMAPFILEHEADER 제거 = CF_DIB


def _default_capture_folder() -> str:
    """캡처 기본 저장 폴더 — <사진>\\PasteFlow."""
    from PyQt6.QtCore import QStandardPaths
    pics = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.PicturesLocation)
    if not pics:
        pics = os.path.join(os.path.expanduser("~"), "Pictures")
    return os.path.join(pics, "PasteFlow")
