"""이미지 주석 편집기 — CleanShot/Snipaste 스타일.

미리보기 팝업에서 Space(또는 우클릭 "주석 편집")로 진입한다. QGraphicsScene 기반이라
줌하면 주석이 이미지와 함께 스케일되고, 그린 도형은 선택·이동·크기조절·삭제가 가능하다.

도구 단축키: V 선택 · R 네모 · E 원 · L 선 · A 화살표 · P 펜 · T 텍스트 · C 번호 · Ctrl+Z 되돌리기 · Ctrl+C/V 주석 복사·붙여넣기.
Shift: 정사각형/정원/45° 스냅(그리기 중) · 15° 스냅(회전 중) · 원래 가로세로 비율 유지(리사이즈 중).
선택 후 좌상단 고정으로 우하단 핸들 드래그하면 크기조절 — 네모·원은 가로/세로 독립(자유형),
텍스트·번호는 균일 스케일. 주석 위 Shift+휠은 두께/글자·번호 크기 조절(그냥 휠은 항상 줌).
완료 동작은 main이 처리한다(시그널만 emit): 클립보드 복사 / 새 히스토리 항목 / 파일 저장.
"""
import io
import json
import math
import struct
import time

from PyQt6.QtCore import (
    Qt, QPoint, QPointF, QRectF, QLineF, QSize, QBuffer, QIODevice, QTimer, pyqtSignal,
)
from PyQt6.QtGui import (
    QPixmap, QImage, QPainter, QPen, QBrush, QColor, QPainterPath,
    QPainterPathStroker, QPolygonF, QFont, QFontMetricsF, QIcon, QCursor,
)
from PyQt6.QtWidgets import (
    QWidget, QGraphicsScene, QGraphicsView, QGraphicsRectItem,
    QGraphicsEllipseItem, QGraphicsLineItem, QGraphicsPathItem,
    QGraphicsTextItem, QGraphicsItem, QHBoxLayout, QVBoxLayout,
    QPushButton, QToolButton, QButtonGroup, QLabel, QSlider,
    QStyle, QStyleOptionGraphicsItem,
)

from pasteflow.ui.theme import (
    BASE as _BG, SURFACE0 as _SURFACE0, SURFACE1 as _BORDER,
    SURFACE2 as _SURFACE2, TEXT as _TEXT, BLUE as _BLUE, SUBTEXT0 as _SUBTEXT,
    PEACH as _PEACH, GREEN as _GREEN,
)

_MIN_WIDTH, _MAX_WIDTH, _DEFAULT_WIDTH = 1, 40, 6
_MIN_FONT, _MAX_FONT, _DEFAULT_FONT = 2, 200, 16  # 휠 축소 하한을 2pt로(그 이하는 크기조절 점)
# 번호 마커 지름(px). 기본 30 = _BadgeItem._R(15) * 2, scale 1.0에 대응.
_MIN_BADGE, _MAX_BADGE, _DEFAULT_BADGE = 12, 120, 30
# 화살표 머리 크기(px, 독립 조절값 — 두께에서 자동계산되던 옛 방식 폐기).
# 기본값은 옛 공식(두께6 기준 head≈15)보다 크게 잡음(2026-07-31 사용자 요청).
_MIN_HEAD, _MAX_HEAD, _DEFAULT_HEAD = 8, 60, 22


def _clamp_int(v, lo, hi, default):
    """v를 int로 파싱해 [lo, hi]로 클램프. 파싱 실패(None·빈문자열 등)면 default."""
    try:
        n = int(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(n, hi))

# 대표 프리셋 색상 (빨강·주황·노랑·초록·파랑·검정·흰색)
_COLOR_PRESETS = [
    "#FF3B30", "#FF9500", "#FFCC00", "#34C759",
    "#007AFF", "#000000", "#FFFFFF",
]
_DEFAULT_COLOR = _COLOR_PRESETS[0]

# 밝은 툴바(Snipaste식 pill) 위 중립 아이콘 색 — 어두운 회색(선택·되돌리기·복사·저장).
# 그리기 도구 아이콘은 그 도구의 tool_defaults 색으로 칠해져 밝은 바에서도 보인다.
_ICON_DARK = "#3a3a3a"

# 그리기 도구가 만드는 도형(릴리스 시 너무 작으면 폐기 대상)
_SHAPE_TOOLS = ("rect", "ellipse", "line", "arrow")
# 현재 색으로 아이콘을 칠하는 도구(나머지는 중립색)
_DRAW_TOOLS = ("rect", "ellipse", "line", "arrow", "pen", "text", "badge")

# 텍스트 배경 선택지: 투명 / 흰 / 회 / 검 / 반투명 검 (자막·스티커 느낌). 스와치로 직접 선택.
_TEXT_BG_OPTIONS = [
    (None, "투명"),
    (QColor(0, 0, 0, 150), "반투명 검정"),
    (QColor("#FFFFFF"), "흰색"),
    (QColor("#808080"), "회색"),
    (QColor("#000000"), "검정"),
]

# 도구 정의: (key, 한글명, 단축키 라벨)
_TOOLS = [
    ("select", "선택", "1"), ("rect", "네모", "2"), ("arrow", "화살표", "3"),
    ("text", "텍스트", "4"), ("ellipse", "원", "5"), ("line", "선", "6"),
    ("pen", "펜", "7"), ("badge", "번호", "8"),
]


# ---------------------------------------------------------------------------
# 이미지 데이터 → QPixmap (PNG·파일바이트·raw DIB 모두 처리)
# ---------------------------------------------------------------------------

def _to_png_full(data: bytes) -> bytes | None:
    """클립보드 image_data(PNG / JPEG·BMP 등 / raw CF_DIB)를 풀 해상도 PNG로 변환."""
    try:
        from PIL import Image
        if data[:4] == b"\x89PNG":
            img = Image.open(io.BytesIO(data))
        else:
            try:
                img = Image.open(io.BytesIO(data))
            except Exception:
                # raw DIB(BITMAPINFOHEADER) → 14바이트 BMP 파일 헤더 부착 (clipboard_monitor와 동일 로직)
                if len(data) < 40:
                    return None
                bi_size = struct.unpack_from("<I", data, 0)[0]
                bi_bit = struct.unpack_from("<H", data, 14)[0]
                bi_clr = struct.unpack_from("<I", data, 32)[0]
                if bi_clr == 0 and bi_bit in (1, 4, 8):
                    bi_clr = 1 << bi_bit
                pixel_offset = 14 + bi_size + bi_clr * 4
                file_size = 14 + len(data)
                hdr = b"BM" + struct.pack("<IHHI", file_size, 0, 0, pixel_offset)
                img = Image.open(io.BytesIO(hdr + data))
        buf = io.BytesIO()
        img.convert("RGBA").save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return None


def _pixmap_from_data(data: bytes) -> QPixmap | None:
    pm = QPixmap()
    if pm.loadFromData(data):
        return pm
    png = _to_png_full(data)
    if png and pm.loadFromData(png):
        return pm
    return None


# ---------------------------------------------------------------------------
# 아이콘 (QPainter로 그린 도형 — 그리기 도구는 현재 색, 나머지는 중립색)
# ---------------------------------------------------------------------------

def _tool_icon(tool: str, color=None, neutral_override=None) -> QIcon:
    # neutral_override: 중립색을 바꿔야 할 때(예: 밝은 제목바 위 어두운 닫기 X).
    neutral = QColor(neutral_override) if neutral_override is not None else QColor(_TEXT)
    col = QColor(color) if (color is not None and tool in _DRAW_TOOLS) else neutral
    pm = QPixmap(22, 22)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    # 툴바 pill 배경이 밝은색(#f4f4f4 계열)이라, 흰색 등 밝은 색을 고르면 획만으로는
    # 아이콘이 배경에 묻힌다 — 그런 색일 때만 반투명 어두운 받침을 깔아 대비를 준다.
    if color is not None and tool in _DRAW_TOOLS and QColor(color).lightnessF() > 0.75:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 70))
        p.drawRoundedRect(1, 1, 20, 20, 4, 4)
    p.setPen(QPen(col, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.setBrush(Qt.BrushStyle.NoBrush)

    if tool == "select":
        poly = QPolygonF([
            QPointF(4, 3), QPointF(4, 18), QPointF(8, 14),
            QPointF(11, 20), QPointF(13, 19), QPointF(10, 13), QPointF(15, 13),
        ])
        p.setBrush(neutral)
        p.setPen(QPen(neutral, 1))
        p.drawPolygon(poly)
    elif tool == "rect":
        p.drawRect(4, 5, 14, 12)
    elif tool == "ellipse":
        p.drawEllipse(4, 4, 14, 14)
    elif tool == "line":
        p.drawLine(4, 18, 18, 4)
    elif tool == "arrow":
        p.drawLine(4, 18, 14, 8)
        p.setBrush(col)
        p.setPen(QPen(col, 1))
        p.drawPolygon(QPolygonF([QPointF(18, 4), QPointF(11, 7), QPointF(15, 11)]))
    elif tool == "pen":
        path = QPainterPath(QPointF(4, 16))
        path.cubicTo(8, 5, 14, 21, 18, 7)
        p.drawPath(path)
    elif tool == "text":
        f = QFont()
        f.setBold(True)
        f.setPointSize(12)
        p.setFont(f)
        p.setPen(col)
        p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, "T")
    elif tool == "badge":
        p.setBrush(col)
        p.setPen(QPen(col, 1))
        p.drawEllipse(3, 3, 16, 16)
        f = QFont()
        f.setBold(True)
        f.setPointSize(9)
        p.setFont(f)
        p.setPen(QColor(_BG))
        p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, "1")
    elif tool == "eyedrop":
        # 드로퍼(스포이드) — 외곽선 캡(bulb) + 대각 몸통 + 좌하단 뾰족 끝(끝점만 작은 채움)
        p.setPen(QPen(neutral, 1.6, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(12, 2, 8, 8, 3, 3)            # 캡(bulb) — 외곽선만
        p.setPen(QPen(neutral, 2.2, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.drawLine(14, 9, 7, 16)                         # 대각 몸통
        p.setBrush(neutral)                              # 촉(끝점)만 작게 채움
        p.setPen(QPen(neutral, 1))
        p.drawPolygon(QPolygonF([
            QPointF(8, 14), QPointF(4, 18), QPointF(9, 15)]))
    elif tool == "undo":
        # 반시계 곡선 화살표
        p.setPen(QPen(neutral, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        path = QPainterPath()
        path.arcMoveTo(QRectF(5, 5, 13, 13), 150)
        path.arcTo(QRectF(5, 5, 13, 13), 150, -250)
        p.drawPath(path)
        p.setBrush(neutral)
        p.setPen(QPen(neutral, 1))
        p.drawPolygon(QPolygonF([QPointF(5, 6), QPointF(10, 7), QPointF(7, 12)]))
    elif tool == "copy":
        # 겹친 두 문서 — 외곽선만(채움 없음). 뒤 문서는 보이는 가장자리(상단·좌측)만
        # 앞 문서 외곽선까지 이어 그려, 채움 없이도 '뒤에 겹친' 느낌을 낸다.
        p.setPen(QPen(neutral, 1.6, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(8, 7, 10, 12, 2, 2)            # 앞 문서(완전한 외곽선)
        back = QPainterPath()                            # 뒤 문서의 보이는 가장자리
        back.moveTo(14, 7)
        back.lineTo(14, 5)
        back.quadTo(14, 4, 13, 4)
        back.lineTo(6, 4)
        back.quadTo(5, 4, 5, 5)
        back.lineTo(5, 14)
        back.quadTo(5, 15, 6, 15)
        back.lineTo(8, 15)
        p.drawPath(back)
    elif tool == "save":
        # 플로피 디스크
        p.setPen(QPen(neutral, 1.6, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(4, 4, 14, 14, 1, 1)            # 본체
        p.setBrush(neutral)
        p.setPen(QPen(neutral, 1))
        p.drawRect(8, 4, 5, 4)                           # 상단 셔터
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(neutral, 1.4))
        p.drawRect(7, 12, 8, 5)                          # 하단 라벨
    elif tool == "close":
        p.setPen(QPen(neutral, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(6, 6, 16, 16)
        p.drawLine(16, 6, 6, 16)
    p.end()
    return QIcon(pm)


def _arrow_dir_icon(head_at_end: bool) -> QIcon:
    pm = QPixmap(24, 18)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    col = QColor(_TEXT)
    p.setPen(QPen(col, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawLine(5, 9, 19, 9)
    p.setBrush(col)
    p.setPen(QPen(col, 1))
    if head_at_end:
        p.drawPolygon(QPolygonF([QPointF(21, 9), QPointF(15, 5), QPointF(15, 13)]))
    else:
        p.drawPolygon(QPolygonF([QPointF(3, 9), QPointF(9, 5), QPointF(9, 13)]))
    p.end()
    return QIcon(pm)


def _bg_swatch_icon(bg) -> QIcon:
    """텍스트 배경 스와치 — 불투명색은 그대로 채움, 반투명색은 체커보드 위에 얹어(투명 표시
    관용) 회색 불투명과 헷갈리지 않게 한다. bg=None이면 투명(대각선)."""
    pm = QPixmap(20, 20)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    rect = QRectF(2, 2, 16, 16)
    if bg is None:
        p.setPen(QPen(QColor(_TEXT), 1.4))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect, 3, 3)
        p.drawLine(5, 15, 15, 5)                         # 투명 표시 대각선
    else:
        clip = QPainterPath()
        clip.addRoundedRect(rect, 3, 3)
        p.setClipPath(clip)
        p.fillRect(rect, QColor("white"))
        if bg.alpha() < 255:
            # 반투명 → 체커보드 바탕(칸 4px)을 깔아 '뒤가 비친다'를 시각화
            cell = 4
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#bfbfbf"))
            yy = 2
            while yy < 18:
                xx = 2
                while xx < 18:
                    if ((int(xx) // cell) + (int(yy) // cell)) % 2 == 0:
                        p.drawRect(QRectF(xx, yy, cell, cell))
                    xx += cell
                yy += cell
        p.setBrush(QBrush(bg))                           # 실제 배경색(반투명이면 체커가 비침)
        p.drawRect(rect)
        p.setClipping(False)
        p.setPen(QPen(QColor(_SUBTEXT), 1))              # 테두리
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect, 3, 3)
    p.end()
    return QIcon(pm)


_ROTATE_CURSOR = None


def _rotate_cursor() -> QCursor:
    """회전 핸들 hover용 커스텀 커서(곡선 화살표). Qt 기본에 회전 커서가 없어 픽스맵으로
    1회 생성·캐시. 검은 본체 + 흰 halo라 밝은/어두운 배경 모두에서 보인다."""
    global _ROTATE_CURSOR
    if _ROTATE_CURSOR is not None:
        return _ROTATE_CURSOR
    pm = QPixmap(32, 32)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    rect = QRectF(8, 8, 16, 16)               # 반지름 8, 중심 (16,16)
    path = QPainterPath()
    path.arcMoveTo(rect, 55)
    path.arcTo(rect, 55, 250)                 # 250° 열린 호
    pe = path.pointAtPercent(1.0)             # 호 끝 — 화살촉을 실제 두 점 방향으로(각도 규약 회피)
    pp = path.pointAtPercent(0.9)
    dx, dy = pe.x() - pp.x(), pe.y() - pp.y()
    L = math.hypot(dx, dy) or 1.0
    ux, uy = dx / L, dy / L
    nx, ny = -uy, ux
    b = QPointF(pe.x() - ux * 6.0, pe.y() - uy * 6.0)
    tri = QPolygonF([QPointF(pe),
                     QPointF(b.x() + nx * 4.0, b.y() + ny * 4.0),
                     QPointF(b.x() - nx * 4.0, b.y() - ny * 4.0)])
    for core, aw in ((QColor("white"), 5.0), (QColor("#111111"), 2.4)):  # 흰 halo → 검은 본체
        pen = QPen(core, aw)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        p.setBrush(QBrush(core))
        p.drawPolygon(tri)
    p.end()
    _ROTATE_CURSOR = QCursor(pm, 16, 16)
    return _ROTATE_CURSOR


# ---------------------------------------------------------------------------
# 크기조절 핸들 믹스인 — 선택 시 우하단 핸들 드래그로 균일 스케일
# ---------------------------------------------------------------------------

class _HandleResizeMixin:
    # 핸들(스케일 사각·회전 원·끝점 사각) 크기는 도형의 '획 두께'에 비례한다 — 얇은 선은
    # 작은 핸들, 굵은 선은 큰 핸들. 씬 단위로 [MIN,MAX] 클램프(못 잡을 만큼 작지도, 거슬릴
    # 만큼 크지도 않게). 획이 없는 도형(번호·텍스트)만 표시 크기 비례로 폴백한다.
    _HANDLE_FRAC = 0.22        # (폴백) 작은 변 대비 핸들 비율 — 번호·텍스트용
    _HANDLE_STROKE_FRAC = 1.4  # 획 두께 대비 핸들 비율 — 도형·선·화살표용
    _HANDLE_MIN = 8.0    # 씬 단위 하한(항상 잡히게) — 2026-07-31 사용자 피드백으로 확대(5→8)
    _HANDLE_MAX = 18.0   # 씬 단위 상한(12→18)
    _EDGE_HIT_MIN = 8.0  # 속 빈 도형 테두리 클릭 최소 히트폭(씬 단위) — 얇은 선도 잡히게

    def _stroke_width(self) -> float:
        """핸들 크기 기준이 되는 획 두께(로컬 단위). 없으면 0(→ 크기 비례 폴백)."""
        if hasattr(self, "_width"):   # _ArrowItem
            return float(self._width)
        if hasattr(self, "pen"):      # rect/ellipse/line/path
            return float(self.pen().widthF())
        return 0.0

    def _handle_px(self) -> float:
        """핸들 한 변(로컬 단위). 획 두께에 비례 + [MIN,MAX] 클램프(획 없으면 크기 비례)."""
        s = self._scale_or_1()
        w = self._stroke_width()
        if w > 0:
            h_scene = max(self._HANDLE_MIN,
                          min(w * s * self._HANDLE_STROKE_FRAC, self._HANDLE_MAX))
            return h_scene / s
        cr = self._content_rect()
        scene_dim = min(cr.width(), cr.height()) * s  # 주석 작은 변(씬 단위)
        h_scene = max(self._HANDLE_MIN, min(scene_dim * self._HANDLE_FRAC, self._HANDLE_MAX))
        return h_scene / s

    # ---- 잡기 판정(시각 점과 분리) --------------------------------------
    # 그려지는 점은 작게(_handle_px) 두되, '잡히는' 영역은 화면 고정 px로 넉넉히
    # — Figma·일러스트레이터식. 얇은 화살표의 bend/끝점 점이 화면상 5~12px라 커서를
    # 정확히 맞춰야 손가락 커서가 되던 문제를 없앤다(hover·press·shape 모두 이 rect 사용).
    _HIT_MIN_PX = 24.0   # 화면 px — 핸들 잡기 최소 지름(줌 무관)

    def _hit_pad_local(self) -> float:
        """잡기 판정 반지름(로컬 단위). 화면 고정 px를 현재 뷰·아이템 배율로 환산."""
        view_s = 1.0
        sc = self.scene()
        if sc is not None and sc.views():
            view_s = sc.views()[0]._view_scale()
        total = max(view_s * self._scale_or_1(), 1e-6)
        return (self._HIT_MIN_PX / total) / 2.0

    def _inflate_to_hit(self, rect: QRectF) -> QRectF:
        """핸들 시각 rect를 잡기 최소 지름까지 부풀린 판정용 rect(이미 크면 그대로)."""
        grow = self._hit_pad_local() - rect.width() / 2.0
        if grow <= 0.0:
            return rect
        return rect.adjusted(-grow, -grow, grow, grow)

    def _init_resize(self):
        self._resizing = False
        self._rotating = False
        self._drag_endpoint = None  # 끝점 드래그 중인 인덱스(0·1, None=없음) — 선·화살표만
        self._press_scale = 1.0
        self._press_dist = 1.0
        self._press_rot = 0.0
        self._press_angle = 0.0
        self._freeform_anchor_pt = QPointF()   # 자유형 리사이즈 고정점(로컬) — press에서 세팅
        self._freeform_press_size = (0.0, 0.0)  # 자유형 리사이즈 press 시점 (폭, 높이) — Shift 비율용

    # ---- 자유형(가로/세로 독립) 리사이즈 — 네모·원 전용 ----------------------
    # 우하단 핸들 하나로 가로/세로를 독립적으로 조절(2026-07-31 사용자 요청). 균일
    # 스케일 대신 rect() 자체를 바꾸는 도형만 켠다 — 텍스트(폰트 크기)·번호(원 비율
    # 유지가 의미 있음)·선·화살표(끝점 모드)는 그대로 균일 스케일/끝점 방식을 쓴다.
    def _uses_freeform_resize(self) -> bool:
        return False

    def _freeform_anchor(self) -> QPointF:
        """자유형 리사이즈의 고정점(로컬 좌표). _uses_freeform_resize()가 True인 도형만
        호출되므로 기본 구현은 QGraphicsRectItem/QGraphicsEllipseItem의 rect()를 그대로 쓴다."""
        return self.rect().topLeft()

    def _apply_freeform_size(self, rect: QRectF):
        """드래그로 계산된 새 rect를 도형에 반영. 위와 동일 이유로 기본은 setRect(rect)."""
        self.setRect(rect)

    # ---- 끝점(양끝 이동) 모드 -------------------------------------------
    # 선·화살표처럼 '2점으로 완전히 결정되는' 도형은 회전+균일스케일 핸들 대신
    # 양끝점 핸들을 쓴다(끝점 2개면 길이·각도가 모두 결정 → 회전/스케일 중복). 기본은 off라
    # 네모·원·번호·텍스트는 기존 회전+스케일 핸들을 그대로 쓴다.
    def _uses_endpoints(self) -> bool:
        return False

    def _endpoints(self):
        """끝점들의 로컬 좌표 리스트(선·화살표가 override)."""
        return []

    def _set_endpoint(self, idx: int, p: QPointF):
        """끝점 idx를 로컬 좌표 p로 이동(선·화살표가 override)."""
        pass

    def _endpoint_active(self) -> bool:
        # 선택돼 있으면 어떤 도구에서든 끝점 이동·재스냅 가능(회전·크기조절 핸들과 동일 정책).
        return self.isSelected()

    def _endpoint_rect(self, idx: int) -> QRectF:
        d = self._handle_px()
        c = self._endpoints()[idx]
        return QRectF(c.x() - d / 2, c.y() - d / 2, d, d)

    def _snap_endpoint(self, idx: int, p: QPointF) -> QPointF:
        """Shift 스냅: 반대쪽 끝점을 기준으로 0/45/90°에 스냅."""
        pts = self._endpoints()
        anchor = pts[1 - idx] if len(pts) == 2 else pts[idx]
        dx, dy = p.x() - anchor.x(), p.y() - anchor.y()
        dist = math.hypot(dx, dy)
        rad = math.radians(round(math.degrees(math.atan2(dy, dx)) / 45.0) * 45.0)
        return QPointF(anchor.x() + dist * math.cos(rad), anchor.y() + dist * math.sin(rad))

    def _connects_to_border(self) -> bool:
        """이 아이템의 끝점이 도형 테두리에 재스냅되는가(화살표만 override)."""
        return False

    def _endpoint_border_snap(self, local_p: QPointF):
        """끝점 드래그 중 근처 네모/원 테두리에 스냅(생성 때와 동일 _border_snap_at 재사용).
        스냅되면 (로컬 최근접점, 바깥 법선 scene), 아니면 None — 뗐다 다시 가져가도 붙는 경로."""
        if not self._connects_to_border():
            return None
        sc = self.scene()
        if sc is None or not sc.views():
            return None
        view = sc.views()[0]
        snap = getattr(view, "_border_snap_at", None)
        if snap is None:
            return None
        res = snap(view.mapFromScene(self.mapToScene(local_p)))
        if res is None:
            return None
        return self.mapFromScene(res[0]), res[1]

    def _move_endpoint_with_snap(self, idx: int, local_p: QPointF):
        """끝점 idx를 이동하되 테두리 근처면 스냅(기본: 점 스냅만. 화살표는 S자 곡선 재계산 override)."""
        snapped = self._endpoint_border_snap(local_p)
        if snapped is not None:
            local_p = snapped[0]
        self._set_endpoint(idx, local_p)

    def _paint_endpoint_handles(self, painter: QPainter):
        if not self._endpoint_active():
            return
        s = self._scale_or_1()
        painter.setPen(QPen(QColor("white"), 1.0 / s))
        painter.setBrush(QBrush(QColor(_BLUE)))
        for i in range(len(self._endpoints())):
            painter.drawRect(self._endpoint_rect(i))

    # 선택된 도형에 현재 색/두께 적용 — pen 기반(rect/ellipse/line/path) 공통 구현.
    # arrow/badge/text는 pen이 없거나 색 보관 방식이 달라 각자 오버라이드한다.
    def apply_color(self, color):
        if hasattr(self, "pen"):
            pen = self.pen()
            pen.setColor(QColor(color))
            self.setPen(pen)

    def apply_width(self, width):
        if hasattr(self, "pen"):
            pen = self.pen()
            pen.setWidthF(float(width))
            self.setPen(pen)

    # 복제 시 위치·스케일·회전·z·플래그(이동/선택 가능) 공통 복사. 타입별 기하/색은 각 clone()이 채운다.
    def _copy_common_to(self, dst):
        dst.setPos(self.pos())
        dst.setScale(self.scale())
        dst.setTransformOriginPoint(self.transformOriginPoint())
        dst.setRotation(self.rotation())
        dst.setZValue(self.zValue())
        dst.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
        )
        return dst

    def _scale_or_1(self) -> float:
        s = self.scale()
        return s if s else 1.0

    # 타이트 경계(선택박스·핸들 기준). 도형별로 override한다(기본은 Qt 기본 boundingRect).
    def _content_rect(self) -> QRectF:
        return super().boundingRect()

    # 핸들 hit-test의 기준 영역(선택 시 핸들 미포함). 기본은 Qt 기본 shape;
    # boundingRect 기반 shape를 쓰는 도형(arrow/badge)은 content_rect로 override해
    # 회전 핸들 여유분이 클릭 영역에 새는 것을 막는다.
    def _base_shape(self):
        return super().shape()

    # 실제 boundingRect = content ∪ 회전·크기조절 핸들의 '잡기' 영역(상시 예약 → 선택
    # 해제 시 핸들 잔상 방지). 시각 rect가 아니라 _inflate_to_hit() 결과까지 예약해야
    # 화면 고정 24px 히트 영역이 boundingRect 밖으로 나가 Qt에 컬링당하지 않는다
    # (끝점 핸들과 동일 원칙 — 옛 코드는 회전 핸들만, 그것도 비확대 rect로 예약해
    # 크기조절 핸들의 넓은 히트 영역이 shape()에 반영 안 되던 버그가 있었다).
    def boundingRect(self) -> QRectF:
        pad = 3.0 / self._scale_or_1()
        if self._uses_endpoints():
            r = self._content_rect()
            for i in range(len(self._endpoints())):
                r = r.united(self._inflate_to_hit(self._endpoint_rect(i)))
            return r.adjusted(-pad, -pad, pad, pad)
        r = self._content_rect()
        r = r.united(self._inflate_to_hit(self._rot_handle_rect()))
        r = r.united(self._inflate_to_hit(self._handle_local_rect()))
        return r.adjusted(-pad, -pad, pad, pad)

    def _handle_local_rect(self) -> QRectF:
        h = self._handle_px()
        c = self._content_rect().bottomRight()
        return QRectF(c.x() - h, c.y() - h, h, h)

    def _rot_handle_center(self) -> QPointF:
        # 좌상단 코너 안쪽 — 우하단 크기조절 점과 대각선 반대편(2026-07-31, 사용자 요청).
        # 리사이즈 앵커(좌상단 고정)와 같은 코너지만 핸들은 시각 요소일 뿐 앵커 자체를
        # 가리지 않는다 — 오히려 대각으로 떨어져 있어 두 핸들을 헷갈릴 일이 없다.
        cr = self._content_rect()
        r = self._handle_px() * 0.5  # 원 반지름(= 크기조절 사각 변의 절반 → 같은 지름)
        return QPointF(cr.left() + r, cr.top() + r)

    def _rot_handle_rect(self) -> QRectF:
        d = self._handle_px()  # 원 지름 = 크기조절 사각 변
        c = self._rot_handle_center()
        return QRectF(c.x() - d / 2, c.y() - d / 2, d, d)

    def _owner_tool(self):
        """현재 활성 도구를 뷰→owner 경로로 조회(없으면 None)."""
        sc = self.scene()
        if sc is not None and sc.views():
            owner = getattr(sc.views()[0], "_owner", None)
            if owner is not None:
                return getattr(owner, "current_tool", None)
        return None

    def _handle_active(self) -> bool:
        if not self.isSelected():
            return False
        # 선택돼 있으면 어떤 도구에서든 이동·회전·크기조절을 바로 할 수 있게 핸들을 띄운다
        # (선택 도구는 러버밴드 다중선택을 계속 담당). 도구 전환 없이 방금 그린 도형을 다듬기 위함.
        if isinstance(self, QGraphicsTextItem) and \
                self.textInteractionFlags() != Qt.TextInteractionFlag.NoTextInteraction:
            return False
        return True

    def _paint_handle(self, painter: QPainter):
        if self._uses_endpoints():
            self._paint_endpoint_handles(painter)
            return
        if not self._handle_active():
            return
        s = self._scale_or_1()
        # 회전 핸들 — 좌상단 코너 안쪽 코랄 점(줄기 없음, 우하단 크기조절 점과 대각선 반대편)
        rc = self._rot_handle_center()
        rh = self._handle_px() * 0.5  # 반지름 — 지름이 크기조절 사각 변과 같게
        painter.setPen(QPen(QColor("white"), 1.0 / s))
        painter.setBrush(QBrush(QColor(_PEACH)))
        painter.drawEllipse(rc, rh, rh)
        # 크기조절 핸들 — 우하단 파란 사각
        r = self._handle_local_rect()
        painter.setPen(QPen(QColor("white"), 1.0 / s))
        painter.setBrush(QBrush(QColor(_BLUE)))
        painter.drawRect(r)

    def _paint_base(self, painter, option, widget):
        # Qt 기본 paint의 자동 선택 점선(회전 핸들까지 확장된 boundingRect 둘레)을 막고
        # 베이스 도형만 그린다. 선택 표시는 호출자가 직접 그린다.
        opt = QStyleOptionGraphicsItem(option)
        opt.state &= ~QStyle.StateFlag.State_Selected
        super().paint(painter, opt, widget)

    def _paint_base_no_select(self, painter, option, widget):
        # 베이스 + 타이트 선택박스(_content_rect에만). 네모·원이 사용한다.
        self._paint_base(painter, option, widget)
        if self.isSelected():
            _draw_selection_box(painter, self._content_rect(), self._scale_or_1())

    def shape(self):
        # 선택 시 핸들 영역을 클릭 영역에 포함 — 속 빈 도형도 핸들을 잡을 수 있게.
        # 끝점·bend 핸들과 동일하게 시각 rect가 아니라 '잡기' rect(_inflate_to_hit)를
        # 써야 화면 고정 24px 히트 영역에서 실제로 mousePressEvent가 온다.
        base = self._base_shape()
        if self._uses_endpoints():
            if self._endpoint_active():
                hp = QPainterPath()
                for i in range(len(self._endpoints())):
                    hp.addRect(self._inflate_to_hit(self._endpoint_rect(i)))
                return base.united(hp)
            return base
        if self._handle_active():
            hp = QPainterPath()
            hp.addRect(self._inflate_to_hit(self._handle_local_rect()))
            hp.addEllipse(self._inflate_to_hit(self._rot_handle_rect()))
            return base.united(hp)
        return base

    def mousePressEvent(self, event):
        if self._uses_endpoints():
            if self._endpoint_active():
                for i in range(len(self._endpoints())):
                    if self._inflate_to_hit(self._endpoint_rect(i)).contains(event.pos()):
                        self._drag_endpoint = i
                        event.accept()
                        return
            super().mousePressEvent(event)
            return
        if self._handle_active():
            # 회전 핸들이 바깥쪽이라 먼저 검사한다. 둘 다 '잡기' rect(화면 고정 24px)로
            # 판정해야 시각 점보다 넉넉한 영역에서 mousePressEvent가 온다(shape()와 동일 rect).
            if self._inflate_to_hit(self._rot_handle_rect()).contains(event.pos()):
                self._rotating = True
                self.setTransformOriginPoint(self._content_rect().center())
                center = self.mapToScene(self._content_rect().center())
                self._press_angle = QLineF(center, event.scenePos()).angle()
                self._press_rot = self.rotation()
                event.accept()
                return
            if self._inflate_to_hit(self._handle_local_rect()).contains(event.pos()):
                self._resizing = True
                # 우하단 핸들을 끄는 것이므로 반대쪽 코너(좌상단)를 고정점으로 잡는다 —
                # 중심을 고정점으로 쓰면 드래그해도 도형이 화면 중앙으로 오그라드는
                # 느낌이 되어 포토샵·파워포인트 등 통상 편집기 동작과 어긋났다(2026-07-31).
                if self._uses_freeform_resize():
                    # 네모·원: 스케일이 아니라 rect() 자체를 바꿔 가로/세로 독립 조절
                    # (2026-07-31 사용자 요청 — 코너 핸들 하나로 자유형 리사이즈. Shift로
                    # press 시점 비율 유지 가능, 텍스트·번호는 균일 스케일 그대로 유지).
                    self._freeform_anchor_pt = self._freeform_anchor()
                    self._freeform_press_size = (self.rect().width(), self.rect().height())
                else:
                    anchor = self._content_rect().topLeft()
                    self.setTransformOriginPoint(anchor)
                    origin_scene = self.mapToScene(anchor)
                    d = QLineF(origin_scene, event.scenePos()).length()
                    self._press_dist = d if d > 1 else 1.0
                    self._press_scale = self._scale_or_1()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if getattr(self, "_drag_endpoint", None) is not None:
            self.prepareGeometryChange()  # 끝점이 boundingRect를 바꾼다
            p = event.pos()
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                # Shift = 각도 스냅(테두리 스냅과 상호배타)
                self._set_endpoint(self._drag_endpoint, self._snap_endpoint(self._drag_endpoint, p))
            else:
                # 근처 도형 테두리에 재스냅(뗐다 다시 붙이기). 화살표는 S자 곡선까지 복원.
                self._move_endpoint_with_snap(self._drag_endpoint, p)
            self.update()
            event.accept()
            return
        if getattr(self, "_rotating", False):
            center = self.mapToScene(self._content_rect().center())
            cur = QLineF(center, event.scenePos()).angle()
            # QLineF.angle()은 반시계(+)·setRotation은 시계(+) → 부호 반전
            new_rot = self._press_rot - (cur - self._press_angle)
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                new_rot = round(new_rot / 15.0) * 15.0  # 15° 스냅
            self.setRotation(new_rot % 360)
            event.accept()
            return
        if getattr(self, "_resizing", False):
            self.prepareGeometryChange()  # 회전 여유분이 scale 의존 → 경계 캐시 갱신
            if self._uses_freeform_resize():
                anchor = self._freeform_anchor_pt
                local = self.mapFromScene(event.scenePos())
                if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                    # Shift = press 시점 가로세로 비율 유지(각 축 스냅과 동일한 손 습관)
                    pw, ph = self._freeform_press_size
                    if pw > 0 and ph > 0:
                        dx, dy = local.x() - anchor.x(), local.y() - anchor.y()
                        ratio = pw / ph
                        if abs(dx) > abs(dy) * ratio:
                            dy = math.copysign(abs(dx) / ratio, dy or dx or 1.0)
                        else:
                            dx = math.copysign(abs(dy) * ratio, dx or dy or 1.0)
                        local = QPointF(anchor.x() + dx, anchor.y() + dy)
                self._apply_freeform_size(QRectF(anchor, local).normalized())
            else:
                anchor = self._content_rect().topLeft()  # press와 동일 고정점(좌상단)
                origin_scene = self.mapToScene(anchor)
                d = QLineF(origin_scene, event.scenePos()).length()
                new = self._press_scale * (d / self._press_dist)
                self.setScale(max(0.15, min(new, 25.0)))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if getattr(self, "_drag_endpoint", None) is not None:
            self._drag_endpoint = None
            event.accept()
            return
        if getattr(self, "_rotating", False) or getattr(self, "_resizing", False):
            was_resizing = getattr(self, "_resizing", False)
            self._rotating = False
            self._resizing = False
            if was_resizing:
                self._on_resize_end()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _on_resize_end(self):
        """코너 드래그 크기조절이 끝났을 때 훅(기본은 아무것도 안 함) — 텍스트가 override해
        scale()을 폰트 크기에 구워 넣는다(_TextItem._on_resize_end 참고)."""
        pass


# ---------------------------------------------------------------------------
# 그래픽스 아이템 (전부 믹스인으로 크기조절 지원)
# ---------------------------------------------------------------------------

def _draw_selection_box(painter: QPainter, rect: QRectF, scale: float = 1.0):
    painter.setPen(QPen(QColor(_BLUE), 1.0 / (scale or 1.0), Qt.PenStyle.DashLine))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRect(rect)


def _draw_selection_ellipse(painter: QPainter, rect: QRectF, scale: float = 1.0):
    # 원의 선택 표시는 네모 박스가 아니라 곡선을 따라가는 점선 타원(펜·획 밖을 살짝 감쌈).
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor(_BLUE), 1.0 / (scale or 1.0), Qt.PenStyle.DashLine))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawEllipse(rect)


class _RectItem(_HandleResizeMixin, QGraphicsRectItem):
    def __init__(self, *args):
        super().__init__(*args)
        self._init_resize()

    def _uses_freeform_resize(self) -> bool:
        return True

    def clone(self):
        c = _RectItem(QRectF(self.rect()))
        c.setPen(QPen(self.pen()))
        c.setBrush(QBrush(self.brush()))
        return self._copy_common_to(c)

    def _base_shape(self):
        # 속 빈 네모(NoBrush)는 '테두리 링'만 클릭 영역으로 — 내부를 통과시켜 네모 안에서
        # 다른 주석을 잡거나 새 도형(화살표 등)을 그릴 수 있게. 채움이 있으면 기본대로 전체.
        if self.brush().style() != Qt.BrushStyle.NoBrush:
            return super()._base_shape()
        path = QPainterPath()
        path.addRect(self.rect())
        stroker = QPainterPathStroker()
        stroker.setWidth(max(self.pen().widthF(), self._EDGE_HIT_MIN / self._scale_or_1()))
        return stroker.createStroke(path)

    def paint(self, painter, option, widget=None):
        self._paint_base_no_select(painter, option, widget)
        self._paint_handle(painter)


class _EllipseItem(_HandleResizeMixin, QGraphicsEllipseItem):
    def __init__(self, *args):
        super().__init__(*args)
        self._init_resize()

    def _uses_freeform_resize(self) -> bool:
        return True

    def clone(self):
        c = _EllipseItem(QRectF(self.rect()))
        c.setPen(QPen(self.pen()))
        c.setBrush(QBrush(self.brush()))
        return self._copy_common_to(c)

    def _content_rect(self):
        # _LineItem과 동일 사이클 방지: QGraphicsEllipseItem.boundingRect()는 펜 두께가
        # 0이 아니면 shape()를 호출하므로, 사각형 기하에서 직접 계산해 재귀를 끊는다.
        extra = self.pen().widthF() / 2.0 + 1.0
        return self.rect().adjusted(-extra, -extra, extra, extra)

    def _base_shape(self):
        # 속 빈 원(NoBrush)은 '테두리 링'만 클릭 영역으로(네모와 동일). QGraphicsEllipseItem
        # 기본 shape()는 boundingRect()를 부르지 않고 rect에서 직접 만드므로 재귀 없음.
        if self.brush().style() != Qt.BrushStyle.NoBrush:
            return super()._base_shape()
        path = QPainterPath()
        path.addEllipse(self.rect())
        stroker = QPainterPathStroker()
        stroker.setWidth(max(self.pen().widthF(), self._EDGE_HIT_MIN / self._scale_or_1()))
        return stroker.createStroke(path)

    def paint(self, painter, option, widget=None):
        # 네모와 달리 선택 표시를 곡선 따라가는 점선 타원으로 그린다(_paint_base_no_select의
        # 사각 박스 대신 _paint_base + 점선 타원).
        self._paint_base(painter, option, widget)
        if self.isSelected():
            _draw_selection_ellipse(painter, self._content_rect(), self._scale_or_1())
        self._paint_handle(painter)


class _LineItem(_HandleResizeMixin, QGraphicsLineItem):
    def __init__(self, *args):
        super().__init__(*args)
        self._init_resize()

    def clone(self):
        c = _LineItem(QLineF(self.line()))
        c.setPen(QPen(self.pen()))
        return self._copy_common_to(c)

    def _uses_endpoints(self):
        return True

    def _endpoints(self):
        line = self.line()
        return [line.p1(), line.p2()]

    def _set_endpoint(self, idx, p):
        line = self.line()
        if idx == 0:
            self.setLine(QLineF(QPointF(p), line.p2()))
        else:
            self.setLine(QLineF(line.p1(), QPointF(p)))

    def _content_rect(self):
        # Qt 기본 QGraphicsLineItem.boundingRect()는 펜 두께가 0이 아니면 내부적으로
        # shape()를 호출하는데, 믹스인 shape()가 핸들 계산에 다시 boundingRect()를 부르므로
        # 무한 재귀(스택 오버플로 → 프로세스 abort)가 된다. 선 기하에서 직접 계산해 사이클을 끊는다.
        line = self.line()
        extra = self.pen().widthF() / 2.0 + 1.0
        return QRectF(line.p1(), line.p2()).normalized().adjusted(-extra, -extra, extra, extra)

    def boundingRect(self):
        # 선택 외곽선(획+8)이 _content_rect보다 살짝 바깥으로 나가므로 여유를 더 준다
        # (안 그러면 수평/수직 선에서 점선 잔상이 남을 수 있음).
        pad = 5.0 / self._scale_or_1()
        return super().boundingRect().adjusted(-pad, -pad, pad, pad)

    def _paint_selection_outline(self, painter, scale):
        # 화살표와 동일하게 '선을 따라가는' 점선(네모 박스 아님). 획을 살짝 넓게 감싼다.
        line = self.line()
        body = QPainterPath()
        body.moveTo(line.p1())
        body.lineTo(line.p2())
        stroker = QPainterPathStroker()
        stroker.setWidth(self.pen().widthF() + 8)
        stroker.setCapStyle(Qt.PenCapStyle.RoundCap)
        outline = stroker.createStroke(body)
        painter.setPen(QPen(QColor(_BLUE), 1.0 / (scale or 1.0), Qt.PenStyle.DashLine))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(outline.simplified())

    def paint(self, painter, option, widget=None):
        self._paint_base(painter, option, widget)
        if self.isSelected():
            self._paint_selection_outline(painter, self._scale_or_1())
        self._paint_handle(painter)


class _PathItem(_HandleResizeMixin, QGraphicsPathItem):
    def __init__(self, *args):
        super().__init__(*args)
        self._init_resize()
        self._sel_outline = None  # 선택 점선 외곽선 캐시(획·펜 불변 → 이동 중 재계산 회피)

    def setPath(self, path):
        self._sel_outline = None
        super().setPath(path)

    def setPen(self, pen):
        self._sel_outline = None
        super().setPen(pen)

    def clone(self):
        c = _PathItem(QPainterPath(self.path()))
        c.setPen(QPen(self.pen()))
        return self._copy_common_to(c)

    def _content_rect(self):
        # _LineItem과 동일 사이클 방지: QGraphicsPathItem.boundingRect()는 brush가 NoBrush일 때
        # shape()를 호출하므로, 패스 기하에서 직접 계산해 믹스인 shape()와의 재귀를 끊는다.
        extra = self.pen().widthF() / 2.0 + 1.0
        return self.path().boundingRect().adjusted(-extra, -extra, extra, extra)

    def _handle_active(self):
        # 펜은 회전·확대 핸들을 두지 않는다 — 그리기 전용이라 잘못 그리면 삭제·되돌리기로
        # 수정하지 변형하지 않는다. 선택 시 획 따라가는 점선만, 이동은 획 잡아 끌기(movable).
        return False

    def _base_shape(self):
        # 클릭 영역은 '획 위'만 — Qt 기본 QGraphicsPathItem.shape()는 스트로크에 원본 패스를
        # addPath로 더해, 닫힌(감싸는) 펜 획의 안쪽 면까지 클릭 영역에 포함한다. 그러면 도형을
        # 빙 둘러 그린 펜이 안쪽 빈 공간의 클릭을 통째로 가로채 안쪽 도형이 선택되지 않는다.
        # 획만 두껍게 스트로크한 밴드를 반환해(안쪽은 비움) 루프 안 도형이 정상 선택되게 한다.
        stroker = QPainterPathStroker()
        stroker.setWidth(max(self.pen().widthF(), 10) + 4)   # 잡기 쉬운 폭
        stroker.setCapStyle(Qt.PenCapStyle.RoundCap)
        stroker.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        return stroker.createStroke(self.path())

    def boundingRect(self):
        # 선택 외곽선(획+8)이 _content_rect보다 살짝 바깥으로 나가므로 여유를 더 준다.
        pad = 5.0 / self._scale_or_1()
        return super().boundingRect().adjusted(-pad, -pad, pad, pad)

    def _paint_selection_outline(self, painter, scale):
        # 펜 획을 따라가는 점선(네모 박스 아님) — 획을 살짝 넓게 감싼다.
        # 스트로크 생성·단순화는 무겁고 획·펜이 안 바뀌면 결과가 동일하므로 캐시해
        # 이동(평행이동) 중 매 프레임 재계산을 피한다(버벅임 제거).
        if self._sel_outline is None:
            stroker = QPainterPathStroker()
            stroker.setWidth(self.pen().widthF() + 8)
            stroker.setCapStyle(Qt.PenCapStyle.RoundCap)
            stroker.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            self._sel_outline = stroker.createStroke(self.path()).simplified()
        painter.setPen(QPen(QColor(_BLUE), 1.0 / (scale or 1.0), Qt.PenStyle.DashLine))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(self._sel_outline)

    def paint(self, painter, option, widget=None):
        self._paint_base(painter, option, widget)
        if self.isSelected():
            self._paint_selection_outline(painter, self._scale_or_1())
        self._paint_handle(painter)


def _cubic_axis_extrema(p0: float, c1: float, c2: float, p3: float):
    """한 축(x 또는 y)에서 3차 베지어가 극값을 갖는 t(∈[0,1])들을 반환.
    B'(t)=0 → A t² + B t + C = 0 (A=−p0+3c1−3c2+p3의 미분 계수). 근만 반환(끝점 0·1은 콜러가 포함)."""
    a = c1 - p0
    b = c2 - c1
    c = p3 - c2
    A = a - 2 * b + c
    B = 2 * (b - a)
    C = a
    ts = []
    if abs(A) < 1e-9:
        if abs(B) > 1e-9:
            ts.append(-C / B)
    else:
        disc = B * B - 4 * A * C
        if disc >= 0:
            sq = math.sqrt(disc)
            ts.append((-B + sq) / (2 * A))
            ts.append((-B - sq) / (2 * A))
    return [t for t in ts if 0.0 < t < 1.0]


def _cubic_bezier_bbox(p1: QPointF, c1: QPointF, c2: QPointF, p2: QPointF) -> QRectF:
    """3차 베지어 곡선의 '타이트한' 경계 사각형(제어점 볼록껍질이 아니라 곡선이 실제로 지나는 범위).
    각 축에서 극값 t + 끝점(0·1)의 곡선 좌표를 모아 min/max."""
    def eval_at(t, a, b, cc, d):
        mt = 1.0 - t
        return (mt * mt * mt * a + 3 * mt * mt * t * b
                + 3 * mt * t * t * cc + t * t * t * d)

    xs = [p1.x(), p2.x()]
    ys = [p1.y(), p2.y()]
    for t in _cubic_axis_extrema(p1.x(), c1.x(), c2.x(), p2.x()):
        xs.append(eval_at(t, p1.x(), c1.x(), c2.x(), p2.x()))
    for t in _cubic_axis_extrema(p1.y(), c1.y(), c2.y(), p2.y()):
        ys.append(eval_at(t, p1.y(), c1.y(), c2.y(), p2.y()))
    return QRectF(QPointF(min(xs), min(ys)), QPointF(max(xs), max(ys)))


class _ArrowItem(_HandleResizeMixin, QGraphicsItem):
    """선 + 끝점 삼각형 화살촉. 머리 방향(head_at_end) 선택 가능."""

    def __init__(self, color: QColor, width: int, head_at_end: bool = True,
                 head_size: float = _DEFAULT_HEAD):
        super().__init__()
        self._p1 = QPointF(0, 0)
        self._p2 = QPointF(0, 0)
        self._ctrl1 = None     # 3차 베지어 제어점 2개(None,None=직선). 로컬(=씬) 좌표.
        self._ctrl2 = None
        self._bend_idx = 0     # 드래그 중인 bend 핸들(1·2, 0=없음)
        self._color = QColor(color)
        self._width = width
        self._head_at_end = head_at_end
        self._head_size_val = head_size  # 두께와 독립된 화살촉 크기(px) — 미니패널에서 조절
        self._init_resize()
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
        )

    def set_points(self, p1: QPointF, p2: QPointF):
        self.prepareGeometryChange()
        self._p1, self._p2 = p1, p2
        self.update()

    def set_head_at_end(self, value: bool):
        self._head_at_end = value
        self.update()

    def flip_head(self):
        self.set_head_at_end(not self._head_at_end)

    def apply_color(self, color):
        self._color = QColor(color)
        self.update()

    def apply_width(self, width):
        self.prepareGeometryChange()  # boundingRect가 _width에 의존
        self._width = width
        self.update()

    def apply_head_size(self, size: float):
        self.prepareGeometryChange()  # boundingRect가 화살촉 크기에 의존
        self._head_size_val = size
        self.update()

    def clone(self):
        c = _ArrowItem(QColor(self._color), self._width, self._head_at_end,
                        self._head_size_val)
        c.set_points(QPointF(self._p1), QPointF(self._p2))
        if self._ctrl1 is not None:
            c._ctrl1 = QPointF(self._ctrl1)
            c._ctrl2 = QPointF(self._ctrl2)
        return self._copy_common_to(c)

    # ---- 끝점(양끝 이동) 핸들 -------------------------------------------
    def _uses_endpoints(self):
        return True

    def _connects_to_border(self):
        return True  # 끝점을 뗐다 도형 테두리 근처로 다시 가져가면 재스냅

    def _endpoints(self):
        return [self._p1, self._p2]

    def _set_endpoint(self, idx, p):
        # 끝점을 옮길 때 곡선이면 그 쪽 제어점도 같은 delta로 따라가게 해 곡선 형태·접선을 유지.
        p = QPointF(p)
        if idx == 0:
            if self._ctrl1 is not None:
                self._ctrl1 = self._ctrl1 + (p - self._p1)
            self._p1 = p
        else:
            if self._ctrl2 is not None:
                self._ctrl2 = self._ctrl2 + (p - self._p2)
            self._p2 = p

    def _move_endpoint_with_snap(self, idx, local_p):
        # 끝점을 테두리에 재스냅하면 생성 때처럼 바깥 법선으로 제어점을 다시 잡아 S자(수직 도착/이탈)
        # 복원, 테두리 밖이면 끝점만 이동(수동으로 구부린 곡선은 delta 추종으로 보존).
        snapped = self._endpoint_border_snap(local_p)
        if snapped is None:
            self._set_endpoint(idx, local_p)
            return
        self._set_endpoint(idx, snapped[0])
        self._recompute_snap_curve(idx, snapped[1])

    def _scene_dir_to_local(self, d_scene: QPointF) -> QPointF:
        """scene 방향벡터 → 로컬 방향벡터(회전·스케일 반영, 위치 오프셋 제거)."""
        o = self.mapFromScene(QPointF(0.0, 0.0))
        v = self.mapFromScene(d_scene)
        return QPointF(v.x() - o.x(), v.y() - o.y())

    def _endpoint_border_normal(self, idx):
        """끝점 idx가 지금 도형 테두리 근처면 그 바깥 법선(scene), 아니면 None."""
        snapped = self._endpoint_border_snap(self._endpoints()[idx])
        return snapped[1] if snapped is not None else None

    def _recompute_snap_curve(self, dragged_idx, n_dragged_scene):
        # 두 끝의 바깥 법선(scene)을 모아 생성 때(_update_arrow_draw)와 같은 공식으로 제어점 재계산.
        # 드래그한 끝은 방금 스냅한 법선, 반대 끝은 여전히 테두리 위인지 재조회.
        normals = [None, None]
        normals[dragged_idx] = n_dragged_scene
        normals[1 - dragged_idx] = self._endpoint_border_normal(1 - dragged_idx)
        p1, p2 = self._p1, self._p2
        dx, dy = p2.x() - p1.x(), p2.y() - p1.y()
        dist = math.hypot(dx, dy)
        if (normals[0] is None and normals[1] is None) or dist < 8:
            self._ctrl1 = self._ctrl2 = None
            return
        k = max(30.0, min(dist * 0.5, 200.0))
        if normals[0] is not None:
            e1 = self._scene_dir_to_local(normals[0])          # 시작 테두리 이탈 접선(바깥 법선)
        else:
            e1 = QPointF(dx / dist, dy / dist)                 # tip 향해
        if normals[1] is not None:
            e2 = self._scene_dir_to_local(normals[1])          # tip 테두리 도착 접선(바깥 법선)
        else:
            e2 = QPointF(-e1.x(), -e1.y())                     # 시작과 평행(부드러운 S)
        self._ctrl1 = QPointF(p1.x() + e1.x() * k, p1.y() + e1.y() * k)
        self._ctrl2 = QPointF(p2.x() + e2.x() * k, p2.y() + e2.y() * k)

    # ---- 곡선(3차 베지어) 헬퍼 -------------------------------------------
    _BEND_TS = (1.0 / 3.0, 2.0 / 3.0)  # bend 핸들 2개의 곡선 파라미터(t)

    def _point_straight(self, t: float) -> QPointF:
        """직선(p1→p2) 위 파라미터 t 지점."""
        p1, p2 = self._p1, self._p2
        return QPointF(p1.x() + (p2.x() - p1.x()) * t,
                       p1.y() + (p2.y() - p1.y()) * t)

    def _point_at(self, t: float) -> QPointF:
        """곡선(직선이면 직선) 위 파라미터 t 지점."""
        if self._ctrl1 is None:
            return self._point_straight(t)
        p1, p2, c1, c2 = self._p1, self._p2, self._ctrl1, self._ctrl2
        mt = 1.0 - t
        a, b = mt * mt * mt, 3 * mt * mt * t
        c, d = 3 * mt * t * t, t * t * t
        return QPointF(a * p1.x() + b * c1.x() + c * c2.x() + d * p2.x(),
                       a * p1.y() + b * c1.y() + c * c2.y() + d * p2.y())

    def _bend_handle_rect(self, which: int) -> QRectF:
        d = self._handle_px()
        c = self._point_at(self._BEND_TS[which - 1])
        return QRectF(c.x() - d / 2, c.y() - d / 2, d, d)

    def _bend_handle_index_at(self, local_pos) -> int:
        """local 좌표가 어느 bend 핸들 안이면 그 인덱스(1·2), 아니면 0."""
        if not self._bend_active():
            return 0
        for which in (1, 2):
            if self._inflate_to_hit(self._bend_handle_rect(which)).contains(local_pos):
                return which
        return 0

    def _solve_ctrl(self, which: int, target: QPointF):
        """bend 핸들 which(1=t 1/3, 2=t 2/3)가 target을 지나도록 해당 제어점을 역산(다른 제어점 고정).
        B(1/3)=8/27·p1+12/27·c1+6/27·c2+1/27·p2, B(2/3)=1/27·p1+6/27·c1+12/27·c2+8/27·p2 에서 유도."""
        p1, p2 = self._p1, self._p2
        if which == 1:
            c2 = self._ctrl2
            self._ctrl1 = QPointF(
                (27 * target.x() - 8 * p1.x() - 6 * c2.x() - p2.x()) / 12.0,
                (27 * target.y() - 8 * p1.y() - 6 * c2.y() - p2.y()) / 12.0)
        else:
            c1 = self._ctrl1
            self._ctrl2 = QPointF(
                (27 * target.x() - p1.x() - 6 * c1.x() - 8 * p2.x()) / 12.0,
                (27 * target.y() - p1.y() - 6 * c1.y() - 8 * p2.y()) / 12.0)

    def _bend_active(self) -> bool:
        # 선택돼 있으면 어떤 도구에서든 곡선 조절 가능(끝점·회전·크기조절 핸들과 동일 정책).
        return self.isSelected()

    def _tip_and_angle(self):
        """화살촉이 놓이는 tip 점과 그 지점의 진행 방향 각도(paint와 동일 규칙)."""
        tail, tip = (self._p1, self._p2) if self._head_at_end else (self._p2, self._p1)
        if self._ctrl1 is None:
            length = math.hypot(tip.x() - tail.x(), tip.y() - tail.y())
            angle = math.atan2(tip.y() - tail.y(), tip.x() - tail.x()) if length > 1e-6 else 0.0
        else:
            C2, P3 = (self._ctrl2, self._p2) if self._head_at_end else (self._ctrl1, self._p1)
            angle = math.atan2(P3.y() - C2.y(), P3.x() - C2.x())
        return tip, angle

    def _head_size(self) -> float:
        """화살촉 크기 — 두께 미니패널의 '머리 크기' 슬라이더로 독립 조절(2026-07-31).
        옛 방식은 두께에서 자동 계산(width*2.5)했으나, 두께를 조절할 때마다 머리도
        함께 바뀌어 원하는 크기로 못 고정하는 문제가 있어 별도 값으로 분리했다."""
        return self._head_size_val

    def _head_points(self):
        """화살촉 삼각형 세 꼭짓점(tip + 뒤쪽 두 점)."""
        tip, angle = self._tip_and_angle()
        size = self._head_size()
        a1 = angle + math.radians(150)
        a2 = angle - math.radians(150)
        return [
            QPointF(tip),
            QPointF(tip.x() + size * math.cos(a1), tip.y() + size * math.sin(a1)),
            QPointF(tip.x() + size * math.cos(a2), tip.y() + size * math.sin(a2)),
        ]

    def _content_rect(self) -> QRectF:
        if self._ctrl1 is None:
            r = QRectF(self._p1, self._p2).normalized()
        else:
            # 곡선이 '실제로 지나는' 타이트 경계(제어점 볼록껍질은 S자에서 과도하게 넓어짐).
            r = _cubic_bezier_bbox(self._p1, self._ctrl1, self._ctrl2, self._p2)
        # 선 몸통은 획 반폭만 여유(둥근 캡), 화살촉은 tip에만 튀어나오므로 삼각형 꼭짓점만 합친다
        # (옛 방식은 화살촉 크기를 네 변 모두에 더해 박스가 곡선보다 과하게 넓었음).
        stroke = self._width / 2.0 + 2
        r = r.adjusted(-stroke, -stroke, stroke, stroke)
        hx = [p.x() for p in self._head_points()]
        hy = [p.y() for p in self._head_points()]
        head_r = QRectF(QPointF(min(hx), min(hy)), QPointF(max(hx), max(hy)))
        return r.united(head_r.adjusted(-2, -2, 2, 2))

    def _base_shape(self):
        # 클릭/hit 영역은 '실제 선+화살촉'만 감싼다(박스 전체가 아니라). 그래야 곡선 안쪽
        # 빈/오목 공간이 _is_empty_area에서 '비어 있음'으로 잡혀 거기에 새 주석을 그릴 수 있다.
        body = QPainterPath()
        body.moveTo(self._p1)
        if self._ctrl1 is None:
            body.lineTo(self._p2)
        else:
            body.cubicTo(self._ctrl1, self._ctrl2, self._p2)
        stroker = QPainterPathStroker()
        stroker.setWidth(max(self._width, 10) + 4)   # 잡기 쉬운 폭
        stroker.setCapStyle(Qt.PenCapStyle.RoundCap)
        stroker.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        shape = stroker.createStroke(body)
        shape.addPolygon(QPolygonF(self._head_points()))
        if self._bend_active():   # 초록 bend 핸들도 잡을 수 있게(넉넉한 잡기 영역)
            for which in (1, 2):
                shape.addEllipse(self._inflate_to_hit(self._bend_handle_rect(which)))
        return shape

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        tail, tip = (self._p1, self._p2) if self._head_at_end else (self._p2, self._p1)
        length = math.hypot(tip.x() - tail.x(), tip.y() - tail.y())
        if self._ctrl1 is None and length < 1:
            return  # 클릭만 한 0길이 직선 화살표는 머리도 그리지 않음(깜빡임 방지)

        size = self._head_size()
        pen = QPen(self._color, self._width, Qt.PenStyle.SolidLine,
                   Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)

        if self._ctrl1 is None:
            # 직선: 선은 화살촉 밑변까지만 그린다. 짧은 화살표에서 base가 tail 뒤로 넘어가
            # 선이 거꾸로 삐져나오지 않도록 tail~tip 구간 안으로 클램프한다.
            t = max(0.0, 1.0 - (size * 0.85) / length) if length > 1 else 0.0
            base = QPointF(tail.x() + (tip.x() - tail.x()) * t,
                           tail.y() + (tip.y() - tail.y()) * t)
            painter.setPen(pen)
            painter.drawLine(tail, base)
        else:
            # 곡선: p1→c1→c2→p2 3차 베지어. 머리 방향에 맞춰 그리기 순서(P0..P3)를 정렬한다
            # (head_at_end면 p1→p2, 아니면 곡선을 뒤집어 p2→p1 — 제어점도 c2·c1 순서로 뒤집음).
            # tip 쪽을 화살촉 밑변까지 잘라 그린다(안 자르면 굵은 선 끝이 화살촉 밖으로 삐져나옴):
            # tip 접선 크기 |B'(1)|=3·|P3−C2| 로 되돌릴 dt를 근사하고 De Casteljau로 [0,te] 분할.
            if self._head_at_end:
                P0, C1, C2, P3 = self._p1, self._ctrl1, self._ctrl2, self._p2
            else:
                P0, C1, C2, P3 = self._p2, self._ctrl2, self._ctrl1, self._p1
            seg = math.hypot(P3.x() - C2.x(), P3.y() - C2.y())
            dt = min(0.5, (size * 0.85) / (3 * seg)) if seg > 1e-6 else 0.0
            te = 1.0 - dt
            ax = P0.x() + (C1.x() - P0.x()) * te; ay = P0.y() + (C1.y() - P0.y()) * te
            bx = C1.x() + (C2.x() - C1.x()) * te; by = C1.y() + (C2.y() - C1.y()) * te
            cx = C2.x() + (P3.x() - C2.x()) * te; cy = C2.y() + (P3.y() - C2.y()) * te
            dx = ax + (bx - ax) * te; dyv = ay + (by - ay) * te
            ex = bx + (cx - bx) * te; ey = by + (cy - by) * te
            fx = dx + (ex - dx) * te; fy = dyv + (ey - dyv) * te  # 곡선 위 te 지점(화살촉 밑변)
            path = QPainterPath(P0)
            path.cubicTo(QPointF(ax, ay), QPointF(dx, dyv), QPointF(fx, fy))
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)

        head = QPolygonF(self._head_points())
        painter.setBrush(QBrush(self._color))
        painter.setPen(QPen(self._color, 1, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawPolygon(head)
        if self.isSelected():
            self._paint_selection_outline(painter, self._scale_or_1())
        self._paint_handle(painter)

    def _paint_selection_outline(self, painter, scale):
        # 선택 표시를 네모가 아니라 '선을 따라가는' 점선으로 — 선+화살촉을 살짝 넓게 감싼 외곽선.
        body = QPainterPath()
        body.moveTo(self._p1)
        if self._ctrl1 is None:
            body.lineTo(self._p2)
        else:
            body.cubicTo(self._ctrl1, self._ctrl2, self._p2)
        stroker = QPainterPathStroker()
        stroker.setWidth(self._width + 8)   # 선보다 살짝 넓게 감싸 점선이 선 양옆을 훑게
        stroker.setCapStyle(Qt.PenCapStyle.RoundCap)
        stroker.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        outline = stroker.createStroke(body)
        outline.addPolygon(QPolygonF(self._head_points()))
        painter.setPen(QPen(QColor(_BLUE), 1.0 / (scale or 1.0), Qt.PenStyle.DashLine))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(outline.simplified())

    def _paint_handle(self, painter):
        # 크기조절·회전 핸들(믹스인) + 곡선용 bend 핸들 2개(곡선 t=1/3·2/3 지점의 초록 원).
        super()._paint_handle(painter)
        if not self._bend_active():
            return
        s = self._scale_or_1()
        painter.setPen(QPen(QColor("white"), 1.0 / s))
        painter.setBrush(QBrush(QColor(_GREEN)))
        for which in (1, 2):
            painter.drawEllipse(self._bend_handle_rect(which))

    def shape(self):
        base = super().shape()  # 믹스인: base_shape + (선택 시)크기조절·회전 핸들
        if self._bend_active():
            hp = QPainterPath()
            for which in (1, 2):
                hp.addEllipse(self._inflate_to_hit(self._bend_handle_rect(which)))
            return base.united(hp)
        return base

    def boundingRect(self) -> QRectF:
        # 실제로 칠하는 것(선택 외곽선=선두께+8, 초록 bend 핸들)이 _content_rect보다 살짝
        # 바깥으로 나가므로 boundingRect에 모두 포함한다 — 안 그러면 bend 드래그 때 무효화가
        # 누락돼 초록점 궤적 잔상이 남는다(다음 전체 리페인트 전까지).
        r = super().boundingRect()
        if self._bend_active():
            for which in (1, 2):
                r = r.united(self._inflate_to_hit(self._bend_handle_rect(which)))
        pad = 4.0 + 4.0 / self._scale_or_1()   # 외곽선 초과분 + 점선 펜 + 안티에일리어싱 여유
        return r.adjusted(-pad, -pad, pad, pad)

    def mousePressEvent(self, event):
        # bend 핸들을 회전/크기조절보다 먼저 잡는다(곡선 조절점 2개).
        idx = self._bend_handle_index_at(event.pos())
        if idx:
            self._bend_idx = idx
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._bend_idx:
            self.prepareGeometryChange()  # 제어점이 boundingRect를 바꾼다
            m = event.pos()
            if self._ctrl1 is None:
                # 직선 → 곡선: 두 제어점을 직선의 1/3·2/3 지점에서 시작(그 순간엔 여전히 직선 모양).
                self._ctrl1 = self._point_straight(self._BEND_TS[0])
                self._ctrl2 = self._point_straight(self._BEND_TS[1])
            self._solve_ctrl(self._bend_idx, m)
            # 직선-복귀 스냅: 두 제어점이 모두 직선(1/3·2/3) 위(±thresh)면 직선으로 되돌린다.
            thresh = max(6.0, self._width * 2) / self._scale_or_1()
            s1, s2 = self._point_straight(self._BEND_TS[0]), self._point_straight(self._BEND_TS[1])
            if (math.hypot(self._ctrl1.x() - s1.x(), self._ctrl1.y() - s1.y()) < thresh
                    and math.hypot(self._ctrl2.x() - s2.x(), self._ctrl2.y() - s2.y()) < thresh):
                self._ctrl1 = self._ctrl2 = None
            self.update()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._bend_idx:
            self._bend_idx = 0
            event.accept()
            return
        super().mouseReleaseEvent(event)


class _BadgeItem(_HandleResizeMixin, QGraphicsItem):
    """원 배경 + 중앙 번호. 클릭 위치(pos)에 배치."""

    _R = 15

    def __init__(self, number: int, color: QColor):
        super().__init__()
        self._number = number
        self._color = QColor(color)
        self._init_resize()
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
        )

    def _content_rect(self) -> QRectF:
        r = self._R + 2
        return QRectF(-r, -r, 2 * r, 2 * r)

    def _base_shape(self):
        p = QPainterPath()
        p.addEllipse(self._content_rect())
        return p

    def apply_color(self, color):
        self._color = QColor(color)
        self.update()

    def clone(self):
        c = _BadgeItem(self._number, QColor(self._color))
        return self._copy_common_to(c)

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QBrush(self._color))
        painter.setPen(QPen(QColor("white"), 2))
        painter.drawEllipse(QPointF(0, 0), self._R, self._R)
        f = QFont()
        f.setBold(True)
        f.setPointSize(12)
        painter.setFont(f)
        painter.setPen(QPen(QColor("white")))
        painter.drawText(QRectF(-self._R, -self._R, 2 * self._R, 2 * self._R),
                         Qt.AlignmentFlag.AlignCenter, str(self._number))
        if self.isSelected():
            _draw_selection_box(painter, self._content_rect(), self._scale_or_1())
        self._paint_handle(painter)


class _TextItem(_HandleResizeMixin, QGraphicsTextItem):
    """편집 종료(focus out) 시 이동/크기조절 가능해지고, 더블클릭으로 다시 편집."""

    def __init__(self, color: QColor):
        super().__init__("")
        self._init_resize()
        self._bg = None  # None=투명 / QColor=배경 채움
        self.setDefaultTextColor(QColor(color))
        f = self.font()
        f.setPointSize(16)
        self.setFont(f)
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
        )

    def apply_color(self, color):
        self.setDefaultTextColor(QColor(color))

    def apply_font_size(self, size):
        f = self.font()
        f.setPointSize(int(size))
        self.setFont(f)

    def _on_resize_end(self):
        """코너 드래그 크기조절은 scale()만 바꿔 폰트 크기 자체(및 tool_defaults의 '마지막
        글자 크기' 기억)엔 반영되지 않는다 — 휠 조절(EditorMixin.adjust_item_property)만
        그 값을 갱신해 왔다. 드래그가 끝나면 scale을 폰트 크기에 구워 넣고
        스케일을 1로 되돌려, 도형 두께가 '휠로 조절 → 다음 도형도 같은 두께'로 이어지는
        것과 대칭되게 다음 텍스트도 방금 크기로 시작하게 한다(2026-07-31 사용자 피드백
        — 두께는 이어지는데 텍스트만 매번 기본 크기로 초기화됨)."""
        s = self.scale()
        if s == 1.0:
            return
        new_size = max(_MIN_FONT, min(round(self.font().pointSize() * s), _MAX_FONT))
        self.prepareGeometryChange()
        self.apply_font_size(new_size)
        self.setScale(1.0)
        sc = self.scene()
        if sc is not None and sc.views():
            owner = getattr(sc.views()[0], "_owner", None)
            if owner is not None:
                owner.tool_defaults["text"]["font"] = new_size
                owner._persist_tool_defaults()
        self.update()

    def set_bg(self, color):
        # color: QColor 또는 None(투명). 둥근 사각 배경으로 자막/스티커 느낌.
        self._bg = QColor(color) if color is not None else None
        self.update()

    def clone(self):
        c = _TextItem(self.defaultTextColor())
        c.setFont(QFont(self.font()))
        c.setPlainText(self.toPlainText())
        c.set_bg(self._bg)
        return self._copy_common_to(c)

    def boundingRect(self):
        # 편집 중(텍스트 입력)엔 회전 핸들 예약(우상단 여백)을 빼 Qt 편집 프레임이 글자에
        # 딱 맞게 한다 — 안 그러면 핸들 자리만큼 점선 프레임이 위·우로 크게 벌어진다.
        if self.textInteractionFlags() != Qt.TextInteractionFlag.NoTextInteraction:
            return self._content_rect()
        return super().boundingRect()

    def setTextInteractionFlags(self, flags):
        # 편집 진입/종료로 boundingRect가 바뀌므로 경계 캐시 갱신(프레임 잔상 방지).
        self.prepareGeometryChange()
        super().setTextInteractionFlags(flags)

    def focusOutEvent(self, event):
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        super().focusOutEvent(event)
        # 연속 텍스트 모드에서 빈 클릭으로 생긴 빈 텍스트는 정리(undo는 scene None 가드로 무해).
        if not self.toPlainText().strip():
            QTimer.singleShot(0, self._discard_if_empty)
        else:
            self.setSelected(False)  # 완료(ESC/Ctrl+Enter) 후 점선 없이 글자만 — 재편집은 V 도구로

    def _discard_if_empty(self):
        if not self.toPlainText().strip() and self.scene() is not None:
            self.scene().removeItem(self)

    def mouseDoubleClickEvent(self, event):
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction)
        self.setFocus()
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event):
        # Enter = 편집 종료(ESC와 동일), Shift+Enter = 줄바꿈. clearFocus → focusOut에서 정리.
        # (Ctrl+Enter도 종료로 유지 — 하위 호환.)
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                super().keyPressEvent(event)  # 줄바꿈 삽입
                return
            self.clearFocus()  # Enter / Ctrl+Enter = 완료
            return
        super().keyPressEvent(event)

    def paint(self, painter, option, widget=None):
        if self._bg is not None:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(self._bg))
            painter.drawRoundedRect(self._content_rect().adjusted(1, 1, -1, -1), 4, 4)
        self._paint_base_no_select(painter, option, widget)
        self._paint_handle(painter)


# ---------------------------------------------------------------------------
# 스포이드 루페 — 화면 픽셀 색 미리보기 (입력 투과)
# ---------------------------------------------------------------------------

class _ColorLoupe(QWidget):
    def __init__(self):
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowTransparentForInput,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._color = QColor("black")
        self._hex = ""
        self.setFixedSize(104, 74)

    def set_color(self, color: QColor):
        self._color = QColor(color)
        self._hex = self._color.name().upper()
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(_BG))
        p.setPen(QPen(QColor(_SURFACE2), 1))
        p.drawRect(0, 0, self.width() - 1, self.height() - 1)
        p.fillRect(8, 8, self.width() - 16, 38, self._color)
        p.setPen(QPen(QColor(_SURFACE2), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(8, 8, self.width() - 16, 38)
        p.setPen(QColor(_TEXT))
        p.drawText(QRectF(0, 48, self.width(), 22),
                   Qt.AlignmentFlag.AlignCenter, self._hex)


# ---------------------------------------------------------------------------
# 크기 스테퍼 — 도구별 floating(글자/번호 크기), 휠/▾▴ 클릭으로 조절
# ---------------------------------------------------------------------------

class _SizeStepper(QWidget):
    changed = pyqtSignal(int)

    _REPEAT_DELAY = 400   # 길게 누르기 시작 후 첫 반복까지(ms)
    _REPEAT_RATE = 60     # 이후 반복 간격(ms)

    def __init__(self, value: int, vmin: int, vmax: int, suffix: str = "", tooltip: str = ""):
        super().__init__()
        self._min, self._max = vmin, vmax
        self._s = value
        self._suffix = suffix
        self.setFixedSize(64, 24)
        self.setToolTip(tooltip or "크기 — 휠 또는 ▾ ▴ (길게 누르면 연속)")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # ▾/▴ 길게 누르면 연속 증감 — 누르고 있는 동안 반복
        self._repeat_dir = 0
        self._repeat_timer = QTimer(self)
        self._repeat_timer.timeout.connect(self._repeat_tick)

    def set_value(self, value: int):
        self._s = max(self._min, min(int(value), self._max))
        self.update()

    def _bump(self, delta: int):
        self.set_value(self._s + delta)
        self.changed.emit(self._s)

    def wheelEvent(self, event):
        if event.angleDelta().y() == 0:
            return
        self._bump(1 if event.angleDelta().y() > 0 else -1)

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        x = event.position().x()
        if x < self.width() * 0.28:
            self._repeat_dir = -1
        elif x > self.width() * 0.72:
            self._repeat_dir = 1
        else:
            return
        self._bump(self._repeat_dir)                 # 즉시 1단계
        self._repeat_timer.start(self._REPEAT_DELAY)  # 누르고 있으면 이후 연속

    def _repeat_tick(self):
        self._bump(self._repeat_dir)
        if self._repeat_timer.interval() != self._REPEAT_RATE:
            self._repeat_timer.setInterval(self._REPEAT_RATE)  # 첫 반복 후 가속

    def mouseReleaseEvent(self, event):
        self._repeat_timer.stop()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(_SURFACE0))
        p.setPen(QPen(QColor(_BORDER), 1))
        p.drawRect(0, 0, self.width() - 1, self.height() - 1)
        f = QFont()
        f.setPointSize(10)
        p.setFont(f)
        p.setPen(QColor(_SUBTEXT))
        p.drawText(QRectF(2, 0, 16, self.height()), Qt.AlignmentFlag.AlignCenter, "▾")
        p.drawText(QRectF(self.width() - 18, 0, 16, self.height()),
                   Qt.AlignmentFlag.AlignCenter, "▴")
        p.setPen(QColor(_TEXT))
        p.drawText(QRectF(16, 0, self.width() - 32, self.height()),
                   Qt.AlignmentFlag.AlignCenter, f"{self._s}{self._suffix}")


# ---------------------------------------------------------------------------
# 그래픽스 뷰 — 그리기 인터랙션 + 도구 단축키 (Shift 제약)
# ---------------------------------------------------------------------------

def _rect_nearest(r, p):
    """로컬 사각형 r 둘레에서 점 p 최근접점 + 바깥 단위 법선(로컬)."""
    left, right, top, bottom = r.left(), r.right(), r.top(), r.bottom()
    if left <= p.x() <= right and top <= p.y() <= bottom:
        # 내부 → 가장 가까운 변으로 투영
        dl, dr, dt, db = p.x() - left, right - p.x(), p.y() - top, bottom - p.y()
        m = min(dl, dr, dt, db)
        if m == dl:
            return QPointF(left, p.y()), QPointF(-1.0, 0.0)
        if m == dr:
            return QPointF(right, p.y()), QPointF(1.0, 0.0)
        if m == dt:
            return QPointF(p.x(), top), QPointF(0.0, -1.0)
        return QPointF(p.x(), bottom), QPointF(0.0, 1.0)
    # 외부 → 채운 사각형으로 클램프한 점이 최근접(모서리 밖이면 대각 법선)
    qx = min(max(p.x(), left), right)
    qy = min(max(p.y(), top), bottom)
    nx = -1.0 if (qx == left and p.x() < left) else (1.0 if (qx == right and p.x() > right) else 0.0)
    ny = -1.0 if (qy == top and p.y() < top) else (1.0 if (qy == bottom and p.y() > bottom) else 0.0)
    if nx == 0.0 and ny == 0.0:
        ny = -1.0  # 안전망(도달 안 함)
    L = math.hypot(nx, ny) or 1.0
    return QPointF(qx, qy), QPointF(nx / L, ny / L)


def _ellipse_nearest(r, p):
    """로컬 타원(사각형 r에 내접) 둘레에서 점 p 최근접점 + 바깥 단위 법선(로컬).
    파라미터 각 t에 대한 뉴턴 반복(초기값=방사각)으로 근사 — 테두리 근처에서 빠르게 수렴."""
    cx, cy = r.center().x(), r.center().y()
    a, b = r.width() / 2.0, r.height() / 2.0
    ux, uy = p.x() - cx, p.y() - cy
    if a < 1e-6 or b < 1e-6:
        return QPointF(cx, cy), QPointF(0.0, -1.0)
    t = math.atan2(a * uy, b * ux)
    for _ in range(4):
        ct, st = math.cos(t), math.sin(t)
        x, y = a * ct, b * st
        # f(t) = d/dt (½|(x,y)-u|²) = (x-ux)(-a·st) + (y-uy)(b·ct)
        f = (x - ux) * (-a * st) + (y - uy) * (b * ct)
        fp = (a * a) * st * st - a * ct * (x - ux) \
            + (b * b) * ct * ct - b * st * (y - uy)
        if abs(fp) < 1e-9:
            break
        t -= f / fp
    ct, st = math.cos(t), math.sin(t)
    q = QPointF(cx + a * ct, cy + b * st)
    nx, ny = ct / a, st / b   # 바깥 법선 ∝ (x/a², y/b²)
    L = math.hypot(nx, ny) or 1.0
    return q, QPointF(nx / L, ny / L)


def _nearest_border(item, scene_pt):
    """네모/원 테두리에서 scene_pt 최근접점 → (snap_scene, outward_unit_scene).
    회전·스케일은 아이템 변환으로 왕복 환산(바깥 법선도 씬 방향으로 변환)."""
    p = item.mapFromScene(scene_pt)
    r = item.rect()
    if isinstance(item, _EllipseItem):
        q, n = _ellipse_nearest(r, p)
    else:
        q, n = _rect_nearest(r, p)
    sp = item.mapToScene(q)
    nd = item.mapToScene(QPointF(q.x() + n.x(), q.y() + n.y())) - sp
    L = math.hypot(nd.x(), nd.y()) or 1.0
    return sp, QPointF(nd.x() / L, nd.y() / L)


class _AnnotatorView(QGraphicsView):
    _SHORTCUTS = {
        Qt.Key.Key_1: "select", Qt.Key.Key_2: "rect", Qt.Key.Key_3: "arrow",
        Qt.Key.Key_4: "text", Qt.Key.Key_5: "ellipse", Qt.Key.Key_6: "line",
        Qt.Key.Key_7: "pen", Qt.Key.Key_8: "badge",
    }

    def __init__(self, scene: QGraphicsScene, owner):
        super().__init__(scene)
        self._owner = owner  # _EditorMixin 인터페이스를 구현한 호스트 위젯
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setMouseTracking(True)
        self._drawing = False
        self._temp: QGraphicsItem | None = None
        self._start = QPointF()
        self._path: QPainterPath | None = None
        self._move_snap = None       # 드래그 이동 전 위치 스냅샷([(item, QPointF), ...]) — undo용
        # 테두리 스냅(화살표 도구 전용) — 도형 테두리 어디든 최근접점에 붙음
        self._snap_preview = None    # 화살표 도구 유휴 시 커서 근처 테두리 최근접점(마커 표시), 씬 좌표 or None
        self._arrow_snap_exit = None # 그리는 화살표 시작이 테두리에 스냅됐으면 그 바깥 법선(이탈 접선), or None
        self._arrow_tip_snap = None  # 그리는 화살표 tip이 테두리에 스냅된 지점(씬 좌표) or None
        self._none_win_dragging = False  # 손 모드(도구 없음) 빈영역 좌드래그 = 창 이동 중
        self._text_drag_item = None  # 편집 중인 텍스트를 테두리 band 드래그로 이동 중이면 그 아이템
        self._text_drag_last = None  # 위 드래그의 직전 씬 좌표(델타 계산용)

    def _is_empty_area(self, view_pos) -> bool:
        """클릭 위치에 선택 가능한 주석 아이템이 없으면(배경뿐) True."""
        for it in self.items(view_pos):
            if it is getattr(self._owner, "_bg_item", None):
                continue
            if it.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsSelectable:
                return False
        return True

    def _bend_handle_at(self, view_pos):
        """커서(view 좌표) 아래에 활성 bend 핸들이 있으면 그 화살표, 없으면 None.
        호버 커서를 몸통(이동)과 구분하는 데 쓴다. 선택된 아이템을 직접 순회하므로
        넉넉한 잡기 영역이 shape 컬링에 걸리지 않는다(끝점 판정과 동일 방식)."""
        scene_pt = self.mapToScene(view_pos)
        for it in self.scene().selectedItems():
            if isinstance(it, _ArrowItem) and it._bend_active() \
                    and it._bend_handle_index_at(it.mapFromScene(scene_pt)):
                return it
        return None

    def _rot_handle_at(self, view_pos) -> bool:
        """커서가 '선택된' 도형의 회전 점 '잡기' 영역 안이면 True — hover 회전 커서 판정용.
        시각 점이 아니라 _inflate_to_hit()로 넉넉히 판정(press와 동일 rect, 2026-07-31 —
        옛 비확대 판정은 손가락 하나 굵기라 이동 커서에서 잘 안 바뀐다는 피드백)."""
        scene_pt = self.mapToScene(view_pos)
        for it in self.scene().selectedItems():
            rr = getattr(it, "_rot_handle_rect", None)
            active = getattr(it, "_handle_active", None)
            if rr is None or active is None or not active():
                continue
            if it._uses_endpoints():   # 선·화살표는 회전 핸들 없음(끝점 핸들 사용)
                continue
            if it._inflate_to_hit(rr()).contains(it.mapFromScene(scene_pt)):
                return True
        return False

    def _scale_handle_at(self, view_pos) -> bool:
        """커서가 '선택된' 도형의 크기조절(우하단 파란 사각) 핸들 '잡기' 영역 안이면 True —
        hover 리사이즈 커서 판정용(_inflate_to_hit() 적용, 회전 핸들과 동일 이유)."""
        scene_pt = self.mapToScene(view_pos)
        for it in self.scene().selectedItems():
            hr = getattr(it, "_handle_local_rect", None)
            active = getattr(it, "_handle_active", None)
            if hr is None or active is None or not active():
                continue
            if it._uses_endpoints():   # 선·화살표는 크기조절 사각 없음(끝점 핸들 사용)
                continue
            if it._inflate_to_hit(hr()).contains(it.mapFromScene(scene_pt)):
                return True
        return False

    def _selected_endpoint_item(self, view_pos):
        """커서가 '선택된' 선·화살표의 끝점 핸들 안이면 그 아이템, 아니면 None."""
        scene_pt = self.mapToScene(view_pos)
        for it in self.scene().selectedItems():
            uses = getattr(it, "_uses_endpoints", None)
            if uses and it._uses_endpoints() and it._endpoint_active():
                local = it.mapFromScene(scene_pt)
                for i in range(len(it._endpoints())):
                    if it._inflate_to_hit(it._endpoint_rect(i)).contains(local):
                        return it
        return None

    def _over_selected_endpoint(self, view_pos) -> bool:
        """커서가 '선택된' 선·화살표의 끝점 핸들 안이면 True(hover 커서 판정용)."""
        return self._selected_endpoint_item(view_pos) is not None

    def _snapshot_movable(self):
        """드래그 이동 전 이동 가능 아이템들의 위치를 기록(release에서 변경분만 undo에 커밋)."""
        self._move_snap = [
            (it, QPointF(it.pos())) for it in self.scene().items()
            if it.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable
        ]

    def _commit_move(self):
        """release 시 실제로 위치가 바뀐 아이템만 이동 undo로 기록."""
        snap = self._move_snap
        self._move_snap = None
        if not snap:
            return
        moved = [(it, old) for it, old in snap
                 if it.scene() is not None and it.pos() != old]
        if moved:
            self._owner.push_undo_move(moved)

    # ---- 테두리 스냅 (화살표 도구가 네모/원 테두리에서 시작·도착하면 붙음) ----
    _BORDER_SNAP_PX = 14.0  # 커서~테두리 최근접점이 이 픽셀 이내면 스냅(시작·tip 공통, 뷰 픽셀)

    def _view_scale(self) -> float:
        m = self.transform().m11()
        return m if m > 1e-6 else 1.0

    def _view_dist(self, scene_pt, view_pos) -> float:
        vp = self.mapFromScene(scene_pt)
        return math.hypot(vp.x() - view_pos.x(), vp.y() - view_pos.y())

    def _conn_shapes(self):
        """씬의 네모·원 아이템(위→아래 순)."""
        return [it for it in self.scene().items()
                if isinstance(it, (_RectItem, _EllipseItem))]

    def _border_snap_at(self, view_pos):
        """커서가 어떤 네모/원 테두리에서 _BORDER_SNAP_PX 이내면 (snap_scene, exit_unit), 아니면 None.
        여러 도형이 후보면 가장 가까운 테두리점을 고른다."""
        scene_pt = self.mapToScene(view_pos)
        best = None
        bestd = self._BORDER_SNAP_PX
        bexit = None
        for sh in self._conn_shapes():
            sp, n = _nearest_border(sh, scene_pt)
            d = self._view_dist(sp, view_pos)
            if d <= bestd:
                bestd, best, bexit = d, sp, n
        if best is None:
            return None
        return best, bexit

    def _update_snap_preview(self, view_pos):
        """화살표 도구 유휴 시 커서 근처 테두리 최근접점을 마커로 예고(스냅 발동 가능 표시)."""
        prev = self._snap_preview
        new = None
        # 커서가 이미 선택된 화살표의 끝점/곡선 핸들 위면(= 이동·재스냅 모드, 손가락 커서)
        # '새 화살표 시작' 예고 마커를 띄우지 않는다 — 끝점이 도형 테두리에 붙어 있어
        # 생성-스냅점과 겹칠 때 큰 파란 점이 손가락 커서와 함께 남던 문제 방지.
        if (self._owner.is_edit_mode() and self._owner.current_tool == "arrow"
                and not self._drawing
                and self._selected_endpoint_item(view_pos) is None
                and self._bend_handle_at(view_pos) is None):
            snap = self._border_snap_at(view_pos)
            if snap is not None:
                new = snap[0]
        self._snap_preview = new
        if new != prev:
            self.viewport().update()

    def _update_arrow_draw(self, event):
        """화살표 그리기 갱신 — tip=커서(테두리 근처면 스냅). 시작·tip 중 하나라도 테두리에
        스냅되면 그 바깥 법선을 이탈/도착 접선으로 쓴 3차 베지어(자동 S자), 둘 다 자유면 직선."""
        it = self._temp
        view_pos = event.position().toPoint()
        tip = self._cur_point(event)   # Shift 각도 제약 반영(스냅되면 아래에서 덮어씀)
        # tip 스냅 — 도형 테두리 최근접점
        snap = self._border_snap_at(view_pos)
        back = None
        if snap is not None:
            tip, back = snap[0], snap[1]   # 타깃 바깥 법선 쪽에 ctrl2 → 수직 도착
        self._arrow_tip_snap = snap[0] if snap is not None else None
        start = self._start
        exit_dir = self._arrow_snap_exit
        dist = math.hypot(tip.x() - start.x(), tip.y() - start.y())
        it.prepareGeometryChange()
        it._p2 = QPointF(tip)
        if (exit_dir is None and back is None) or dist < 8:
            it._ctrl1 = it._ctrl2 = None   # 양끝 자유거나 너무 짧으면 직선
        else:
            k = max(30.0, min(dist * 0.5, 200.0))
            if exit_dir is not None:
                ex, ey = exit_dir.x(), exit_dir.y()          # 시작 테두리 이탈 접선
            else:
                ex, ey = (tip.x() - start.x()) / dist, (tip.y() - start.y()) / dist  # tip 향해
            if back is not None:
                bx, by = back.x(), back.y()                  # tip 테두리 도착 접선(바깥 법선)
            else:
                bx, by = -ex, -ey                            # 시작과 평행하게 도착(부드러운 S)
            it._ctrl1 = QPointF(start.x() + ex * k, start.y() + ey * k)
            it._ctrl2 = QPointF(tip.x() + bx * k, tip.y() + by * k)
        it.update()
        self.viewport().update()   # tip 마커 갱신

    def _draw_snap_marker(self, painter, sp, s):
        base = 5.0 / s
        painter.setPen(QPen(QColor("white"), 1.5 / s))
        painter.setBrush(QBrush(QColor(_BLUE)))
        painter.drawEllipse(sp, base, base)

    def leaveEvent(self, event):
        # 커서가 뷰를 벗어나면 스냅 예고 마커 정리(잔상 방지).
        if self._snap_preview is not None:
            self._snap_preview = None
            self.viewport().update()
        super().leaveEvent(event)

    def drawForeground(self, painter, rect):
        super().drawForeground(painter, rect)
        if not self._owner.is_edit_mode():
            return
        s = self._view_scale()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._drawing and self._temp is not None:
            # 그리는 중 — 스냅된 시작·tip에 마커
            if self._arrow_snap_exit is not None:
                self._draw_snap_marker(painter, self._start, s)
            if self._arrow_tip_snap is not None:
                self._draw_snap_marker(painter, self._arrow_tip_snap, s)
        elif self._snap_preview is not None:
            # 유휴 — 화살표 도구가 테두리 근처(스냅 발동 예고)
            self._draw_snap_marker(painter, self._snap_preview, s)

    # ---- 줌 (휠) — 주석 위면 속성 변경, 아니면 owner의 hug-zoom(창이 이미지에 맞게) ----
    def wheelEvent(self, event):
        dy = event.angleDelta().y()
        if dy == 0:
            return
        # Shift+휠 = 커서 아래 주석 속성 조절(도형=두께 / 텍스트·번호=크기), 그냥 휠은
        # 어디서든(주석 위여도) 이미지 줌 — 예전엔 주석 위 휠이 항상 조절이라 줌하려고
        # 스크롤했다가 의도치 않게 두께·크기가 바뀌곤 했다(2026-07-31 사용자 요청).
        if self._owner.is_edit_mode() and (event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            bg = getattr(self._owner, "_bg_item", None)
            for it in self.items(event.position().toPoint()):
                if it is bg:
                    continue
                if it.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsSelectable:
                    self._owner.adjust_item_property(it, 1 if dy > 0 else -1)
                    event.accept()
                    return
        self._owner._on_wheel_zoom(dy)

    # ---- Shift 제약 적용 ---------------------------------------------------
    @staticmethod
    def _constrain(start: QPointF, cur: QPointF, mode: str) -> QPointF:
        dx, dy = cur.x() - start.x(), cur.y() - start.y()
        if mode == "square":
            side = max(abs(dx), abs(dy))
            return QPointF(start.x() + (side if dx >= 0 else -side),
                           start.y() + (side if dy >= 0 else -side))
        if mode == "angle":
            length = math.hypot(dx, dy)
            snapped = round(math.atan2(dy, dx) / (math.pi / 4)) * (math.pi / 4)
            return QPointF(start.x() + length * math.cos(snapped),
                           start.y() + length * math.sin(snapped))
        return cur

    def _cur_point(self, event) -> QPointF:
        sp = self.mapToScene(event.position().toPoint())
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            tool = self._owner.current_tool
            if tool in ("rect", "ellipse"):
                return self._constrain(self._start, sp, "square")
            if tool in ("line", "arrow"):
                return self._constrain(self._start, sp, "angle")
        return sp

    # ---- 그리기 ------------------------------------------------------------
    def mousePressEvent(self, event):
        # 휠(가운데) 버튼 드래그 = 창(이미지) 이동 — 편집/뷰어 모두. 좌클릭은 그리기에 쓰이므로.
        if event.button() == Qt.MouseButton.MiddleButton:
            self._owner._win_drag_start(event.globalPosition().toPoint())
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        # 뷰어 모드: 좌클릭 드래그 = 창 이동 (그리기·선택 안 함)
        if not self._owner.is_edit_mode():
            if event.button() == Qt.MouseButton.LeftButton:
                self._owner._win_drag_start(event.globalPosition().toPoint())
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        # 캔버스를 클릭했다 = 미니패널 볼일이 끝난 것으로 보고 정리(그랩 없는 패널이라
        # Qt.Popup처럼 저절로 안 닫히므로 여기서 명시적으로 닫는다).
        self._owner._hide_tool_popups()
        vpos0 = event.position().toPoint()
        # 편집 중인 텍스트의 테두리 band 위 press = 이동 시작. QGraphicsTextItem은 편집
        # 모드(TextEditorInteraction)면 press를 그대로 super()에 넘길 경우 캐럿 배치/선택으로
        # 먼저 가로채 버려 이동이 아예 안 됐다 — 호버 커서만 이동으로 바뀌고 실제로는 못
        # 옮기는 불일치였다(2026-07-31 사용자 보고). 여기서 직접 가로채 수동으로 옮긴다.
        if self._editing_text_hover(vpos0) == "move":
            it = self._editing_text_item(vpos0)
            if it is not None:
                self._snapshot_movable()  # 다른 드래그 이동과 동일하게 Ctrl+Z 대상으로 기록
                self._text_drag_item = it
                self._text_drag_last = self.mapToScene(vpos0)
                event.accept()
                return
        # 이미 선택된 화살표/선의 끝점·곡선(bend) 조절 핸들 위 press는 겹친 도형 테두리보다 우선한다
        # (선택된 아이템의 핸들이 먼저 작동해야 함). 끝점/핸들은 도형 테두리에 딱 붙는 일이 잦아
        # Z-order 배달로는 아래 도형이 press를 가로챈다 → 그 아이템을 잠깐 최상단으로 올려 Qt가
        # 그 아이템에 press를 배달(=grab)하게 한 뒤 Z를 즉시 복원한다(grab은 Z와 무관하게 유지).
        # 끝점 우선은 "새 연결 화살표 생성"(arrow 도구)보다도 앞서야 겹칠 때 새 화살표가 안 생긴다.
        vpos = event.position().toPoint()
        grab = self._selected_endpoint_item(vpos) or self._bend_handle_at(vpos)
        if grab is not None:
            if self._snap_preview is not None:
                # 끝점/핸들 드래그 시작 → 유휴 테두리 스냅 예고 마커를 즉시 제거(드래그 중엔
                # 버튼 눌림으로 _update_snap_preview가 안 돌아 이전 마커가 도형에 남던 잔상 방지).
                self._snap_preview = None
                self.viewport().update()
            old_z = grab.zValue()
            grab.setZValue(1e9)
            super().mousePressEvent(event)
            grab.setZValue(old_z)
            return
        tool = self._owner.current_tool
        # 화살표 도구 + 도형 테두리 근처 press → 테두리에 스냅된 곡선 화살표 시작(도형 선택/이동보다 우선).
        if tool == "arrow":
            snap = self._border_snap_at(event.position().toPoint())
            if snap is not None:
                owner = self._owner
                ad = owner.tool_defaults["arrow"]
                it = _ArrowItem(QColor(ad["color"]), ad["width"], ad["head_at_end"], ad["head"])
                self._start = snap[0]
                self._arrow_snap_exit = snap[1]
                self._arrow_tip_snap = None
                it.set_points(self._start, self._start)
                self._begin_draw(it)
                return
        if tool is None:
            # 손 모드: 빈 영역 좌드래그 = 창 이동, 주석 위 = 단일 선택/이동(하이브리드).
            if self._is_empty_area(event.position().toPoint()):
                self._owner._win_drag_start(event.globalPosition().toPoint())
                self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
                self._none_win_dragging = True
                return
            self._snapshot_movable()   # 주석 드래그 이동을 undo로 되돌리기 위해
            return super().mousePressEvent(event)
        if tool == "select":
            # Qt 기본: 빈 영역 드래그 = 러버밴드 다중선택, 아이템 위 = 이동/선택.
            # 창 이동은 상단 코랄 드래그바로. (편집 모드 본문 pan은 제거)
            self._snapshot_movable()   # 아이템 드래그 이동을 undo로 되돌리기 위해
            return super().mousePressEvent(event)

        # 도형 도구는 기존 주석 위를 클릭하면 그리기 대신 선택/이동.
        # 단, 펜은 빽빽이 겹쳐 그리므로 항상 그린다(펜 선의 선택/이동은 V 도구로).
        if tool != "pen" and not self._is_empty_area(event.position().toPoint()):
            self._snapshot_movable()
            return super().mousePressEvent(event)

        sp = self.mapToScene(event.position().toPoint())
        self._start = sp
        owner = self._owner

        if tool == "rect":
            it = _RectItem(QRectF(sp, sp))
            it.setPen(owner.make_pen("rect"))
            it.setBrush(QBrush(Qt.BrushStyle.NoBrush))
            self._begin_draw(it)
        elif tool == "ellipse":
            it = _EllipseItem(QRectF(sp, sp))
            it.setPen(owner.make_pen("ellipse"))
            it.setBrush(QBrush(Qt.BrushStyle.NoBrush))
            self._begin_draw(it)
        elif tool == "line":
            it = _LineItem(QLineF(sp, sp))
            it.setPen(owner.make_pen("line"))
            self._begin_draw(it)
        elif tool == "arrow":
            ad = owner.tool_defaults["arrow"]
            it = _ArrowItem(QColor(ad["color"]), ad["width"], ad["head_at_end"], ad["head"])
            it.set_points(sp, sp)
            self._arrow_snap_exit = None   # 자유 시작(테두리 스냅 아님) → 직선/자유 곡선
            self._arrow_tip_snap = None
            self._begin_draw(it)
        elif tool == "pen":
            self._path = QPainterPath(sp)
            it = _PathItem(self._path)
            it.setPen(owner.make_pen("pen"))
            self._begin_draw(it)
        elif tool == "text":
            td = owner.tool_defaults["text"]
            it = _TextItem(QColor(td["color"]))
            it.apply_font_size(td["font"])
            it.set_bg(owner.current_text_bg)
            # I-beam(세로 막대 중심)이 클릭점 → 캐럿이 그 자리에 오도록 배치 보정.
            # documentMargin만큼 왼쪽, 첫 줄 높이 절반만큼 위로 당긴다(안 하면 글자가 처져 보임).
            margin = it.document().documentMargin()
            line_h = QFontMetricsF(it.font()).height()
            it.setPos(QPointF(sp.x() - margin, sp.y() - margin - line_h / 2))
            self.scene().addItem(it)
            owner.push_undo_add(it)
            it.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction)
            it.setFocus()
            # setFocus가 이전 편집 텍스트의 focusOut→재선택을 유발하므로, 그 뒤에 비운다.
            # (새 텍스트 시작 = 다른 항목 선택 해제. 새 텍스트는 selected 아닌 편집 상태로 둠)
            self.scene().clearSelection()
            # 다른 도구처럼 텍스트 도구를 유지해 연속 배치 가능(빈 텍스트는 focusOut 시 정리).
        elif tool == "badge":
            bd = owner.tool_defaults["badge"]
            it = _BadgeItem(owner.next_badge_number(), QColor(bd["color"]))
            it.setScale(bd["size"] / float(_DEFAULT_BADGE))
            it.setPos(sp)
            self.scene().addItem(it)
            owner.push_undo_add(it)
            self.scene().clearSelection()
            it.setSelected(True)

    def _begin_draw(self, item: QGraphicsItem):
        item.setZValue(1)
        self.scene().addItem(item)
        self._temp = item
        self._drawing = True
        self._snap_preview = None   # 그리기 시작 → 유휴 스냅 예고 마커 정리
        self.viewport().update()

    def _editing_text_item(self, view_pos):
        """편집 중(캐럿 활성)인 텍스트 아이템이 커서 아래 있으면 반환, 아니면 None."""
        for it in self.items(view_pos):
            if isinstance(it, _TextItem) and \
                    it.textInteractionFlags() != Qt.TextInteractionFlag.NoTextInteraction:
                return it
        return None

    def _editing_text_hover(self, view_pos) -> str | None:
        """편집 중인 텍스트 위 hover면 'text'(내부=캐럿) / 'move'(테두리 band=이동), 아니면 None.
        테두리 band는 화면 8px 두께로 잡아 뷰·아이템 스케일과 무관하게 일정하게 보이게 한다."""
        it = self._editing_text_item(view_pos)
        if it is None:
            return None
        scene_pt = self.mapToScene(view_pos)
        cr = it._content_rect()
        band = 8.0 / (self._view_scale() * it._scale_or_1())  # 화면 8px → 로컬 두께
        inner = cr.adjusted(band, band, -band, -band)
        if inner.width() <= 0 or inner.height() <= 0:
            return "text"  # 너무 작으면 전부 캐럿(편집 중이므로 I빔 우선)
        return "text" if inner.contains(it.mapFromScene(scene_pt)) else "move"

    def _update_hover_cursor(self, view_pos):
        """편집 모드 hover 커서: 주석 위=이동, 도형 도구+빈영역=십자, select+빈영역=손바닥.
        편집 중 텍스트는 예외 — 내부=캐럿(I빔), 테두리만 이동."""
        vp = self.viewport()
        tool = self._owner.current_tool
        edit_text = self._editing_text_hover(view_pos)
        if self._bend_handle_at(view_pos) is not None:
            vp.setCursor(Qt.CursorShape.PointingHandCursor)  # 곡선 조절 손잡이(이동과 구분)
        elif self._over_selected_endpoint(view_pos):
            vp.setCursor(Qt.CursorShape.PointingHandCursor)  # 끝점 핸들(이동/재스냅) — 곡선 핸들과 동일
        elif self._rot_handle_at(view_pos):
            vp.setCursor(_rotate_cursor())                   # 회전 점 — 곡선 화살표 커서
        elif self._scale_handle_at(view_pos):
            vp.setCursor(Qt.CursorShape.SizeFDiagCursor)     # 크기조절 점(우하단) — 대각 리사이즈(↖↘)
        elif edit_text == "text":
            vp.setCursor(Qt.CursorShape.IBeamCursor)         # 편집 중 텍스트 내부 — 캐럿
        elif edit_text == "move":
            vp.setCursor(Qt.CursorShape.SizeAllCursor)       # 편집 중 텍스트 테두리 — 이동
        elif tool == "arrow" and self._snap_preview is not None:
            vp.setCursor(Qt.CursorShape.CrossCursor)          # 테두리 스냅 — 화살표 시작(도형 위여도)
        elif tool == "pen":
            vp.setCursor(Qt.CursorShape.CrossCursor)         # 펜 — 주석 위에서도 항상 그리기
        elif not self._is_empty_area(view_pos):
            vp.setCursor(Qt.CursorShape.SizeAllCursor)       # 주석 위 — 선택/이동
        elif tool is None:
            vp.setCursor(Qt.CursorShape.OpenHandCursor)      # 손 모드 빈 영역 — 창 이동
        elif tool == "select":
            vp.setCursor(Qt.CursorShape.ArrowCursor)         # 빈 영역 — 러버밴드 선택
        elif tool == "text":
            vp.setCursor(Qt.CursorShape.IBeamCursor)         # 텍스트 — 캐럿 위치 표시
        else:
            vp.setCursor(Qt.CursorShape.CrossCursor)         # 도형 그리기

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.MiddleButton:
            self._owner._win_drag_move(event.globalPosition().toPoint())
            return
        if self._none_win_dragging:  # 손 모드 빈영역 좌드래그 = 창 이동
            self._owner._win_drag_move(event.globalPosition().toPoint())
            return
        if not self._owner.is_edit_mode():
            if event.buttons() & Qt.MouseButton.LeftButton:
                self._owner._win_drag_move(event.globalPosition().toPoint())
            else:
                self.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
            return
        if self._text_drag_item is not None:
            if not (event.buttons() & Qt.MouseButton.LeftButton):
                self._text_drag_item = None  # 방어적 정리(release를 놓친 경우)
            else:
                cur = self.mapToScene(event.position().toPoint())
                delta = cur - self._text_drag_last
                self._text_drag_item.moveBy(delta.x(), delta.y())
                self._text_drag_last = cur
                return
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            self._update_snap_preview(event.position().toPoint())
            self._update_hover_cursor(event.position().toPoint())
        if self._drawing and self._temp is not None:
            tool = self._owner.current_tool
            if tool == "arrow":
                self._update_arrow_draw(event)   # 테두리 스냅 + 자동 S자
                return
            sp = self._cur_point(event)
            if tool in ("rect", "ellipse"):
                self._temp.setRect(QRectF(self._start, sp).normalized())
            elif tool == "line":
                self._temp.setLine(QLineF(self._start, sp))
            elif tool == "pen" and self._path is not None:
                self._path.lineTo(sp)
                self._temp.setPath(self._path)
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton:
            self._owner._win_drag_end()
            self.viewport().unsetCursor()
            return
        if self._none_win_dragging:  # 손 모드 창 이동 종료
            self._owner._win_drag_end()
            self._none_win_dragging = False
            self.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
            return
        if not self._owner.is_edit_mode():
            self._owner._win_drag_end()
            return
        if self._text_drag_item is not None:
            self._text_drag_item = None
            self._text_drag_last = None
            self._commit_move()
            return
        if self._drawing and self._temp is not None:
            item = self._temp
            tool = self._owner.current_tool
            self._drawing = False
            self._temp = None
            self._path = None
            self._arrow_snap_exit = None
            self._arrow_tip_snap = None
            self.viewport().update()   # 스냅 마커 지우기
            # 드래그 없이 클릭만 한 경우 폐기. boundingRect는 펜 두께·화살촉만큼
            # 부풀어 클릭 판정에 못 쓰므로(특히 화살표), 시작점→놓은 점 이동량으로 본다.
            release = self.mapToScene(event.position().toPoint())
            moved = max(abs(release.x() - self._start.x()), abs(release.y() - self._start.y()))
            discard = tool in _SHAPE_TOOLS and moved < 4
            if discard:
                self.scene().removeItem(item)  # 클릭만 한 경우 폐기
                self.scene().clearSelection()  # 빈 공간 클릭 = 선택 해제
            else:
                item.setFlags(
                    QGraphicsItem.GraphicsItemFlag.ItemIsMovable
                    | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
                )
                self._owner.push_undo_add(item)
                # 방금 그린 주석을 바로 선택 — 추가 클릭 없이 이동/색·두께 수정 가능.
                # 단 펜은 연속 그리기라 선택 네모가 거슬리므로 선택하지 않는다.
                self.scene().clearSelection()
                if tool != "pen":
                    item.setSelected(True)
            return
        self._commit_move()   # 드래그 이동이 있었으면 undo에 기록
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        # 뷰어 모드: 더블클릭 = 닫기 (편집 모드는 텍스트 재편집 등 기본 동작 유지)
        if not self._owner.is_edit_mode():
            if event.button() == Qt.MouseButton.LeftButton:
                self._owner.close()
            return
        super().mouseDoubleClickEvent(event)

    # ---- 키 (Space 토글 / 도구 단축키 / Delete / Ctrl+Z / Esc) -------------
    def keyPressEvent(self, event):
        fi = self.scene().focusItem()
        editing_text = (
            isinstance(fi, QGraphicsTextItem)
            and fi.textInteractionFlags() != Qt.TextInteractionFlag.NoTextInteraction
        )
        key = event.key()
        mods = event.modifiers()
        if editing_text and key == Qt.Key.Key_Escape:
            # 텍스트 편집 중 ESC = 편집기 닫기가 아니라 텍스트 완료(=Ctrl+Enter와 동일).
            # clearFocus → focusOutEvent가 정리(빈 텍스트 폐기 / 비어있지 않으면 선택 해제).
            fi.clearFocus()
            return
        if not editing_text and key == Qt.Key.Key_Space:
            self._owner.toggle_edit_mode()
            return
        if not editing_text and key == Qt.Key.Key_Escape:
            # 선택된 주석이 있으면 ESC는 선택(파란 점선)만 해제 — 편집기는 안 닫는다.
            # 선택이 없을 때만 편집기 종료로 넘어간다(주석 → 뷰어 → 닫기 단계적 취소).
            if self.scene().selectedItems():
                self.scene().clearSelection()
                return
            self._owner._on_escape()
            return
        if self._owner.is_edit_mode() and not editing_text:
            # 화살표키 — 선택된 주석 이동. 기본은 넓게(10px), Shift/Ctrl로 세밀하게(1px). 도구와 무관.
            arrow = {
                Qt.Key.Key_Left: (-1, 0), Qt.Key.Key_Right: (1, 0),
                Qt.Key.Key_Up: (0, -1), Qt.Key.Key_Down: (0, 1),
            }.get(key)
            if arrow is not None:
                sel = self.scene().selectedItems()
                if sel:
                    # 이동 전 위치 기록(Ctrl+Z 원복). 같은 선택의 연속 nudge는 하나로 합쳐
                    # undo 폭주를 막는다(coalesce_key=선택 집합).
                    self._owner.push_undo_move(
                        [(it, QPointF(it.pos())) for it in sel],
                        coalesce_key=frozenset(sel))
                    fine = mods & (Qt.KeyboardModifier.ShiftModifier
                                   | Qt.KeyboardModifier.ControlModifier)
                    step = 1 if fine else 10
                    for it in sel:
                        it.moveBy(arrow[0] * step, arrow[1] * step)
                    return
            if (mods & Qt.KeyboardModifier.ControlModifier) and key == Qt.Key.Key_A:
                for it in self.scene().items():
                    if it.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsSelectable:
                        it.setSelected(True)
                return
            if (mods & Qt.KeyboardModifier.ControlModifier) and key == Qt.Key.Key_C:
                self._owner.copy_selection()
                return
            if (mods & Qt.KeyboardModifier.ControlModifier) and key == Qt.Key.Key_V:
                self._owner.paste_selection()
                return
            if key in self._SHORTCUTS and not (mods & (
                    Qt.KeyboardModifier.ControlModifier
                    | Qt.KeyboardModifier.AltModifier
                    | Qt.KeyboardModifier.ShiftModifier)):
                self._owner.set_tool(self._SHORTCUTS[key])
                return
            if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
                selected = list(self.scene().selectedItems())
                if selected:
                    for it in selected:
                        self.scene().removeItem(it)
                    self._owner.push_undo_delete(selected)
                    return
            if key == Qt.Key.Key_Z and (mods & Qt.KeyboardModifier.ControlModifier):
                self._owner.undo()
                return
        super().keyPressEvent(event)


# ---------------------------------------------------------------------------
# 드래그 핸들 (프레임리스 창 이동)
# ---------------------------------------------------------------------------

class _DragBar(QWidget):
    def __init__(self, win: QWidget):
        super().__init__()
        self._win = win
        self._press = None
        self.setFixedHeight(26)
        # plain QWidget은 QSS background-color가 기본 미적용 — 명시적으로 켠다
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press = event.globalPosition().toPoint() - self._win.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if self._press is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self._win.move(event.globalPosition().toPoint() - self._press)

    def mouseReleaseEvent(self, event):
        self._press = None


class _ToolSettingsPopup(QWidget):
    """도구 우클릭 미니패널(색·두께/글자·번호 크기, 화살표는 머리크기·방향도).

    ⚠ 처음엔 Qt.WindowType.Popup으로 만들었으나(색 팔레트 팝업과 같은 패턴), Popup은
    떠 있는 동안 마우스 입력을 통째로 그랩한다 — 그래서 패널이 열린 채로 캔버스에서
    Shift+휠로 두께를 조절하려 하면 휠 이벤트가 캔버스에 전혀 도달하지 않았다
    (2026-07-31 사용자 보고). Qt.WindowType.Tool(그랩 없음) + WA_ShowWithoutActivating
    (paste_hud.py의 비활성 창과 동일 패턴 — 클릭은 받되 편집기 창의 활성 상태·포커스는
    뺏지 않음)로 바꿔 해결했다. 대신 '바깥 클릭 시 자동으로 닫힘'을 Popup 그랩에 기대던
    것도 잃으므로, 도구 전환·캔버스 클릭 시 _EditorMixin._hide_tool_popups()가 명시적으로
    닫는다(같은 버튼 재우클릭=토글은 그대로 유효). hidden_at은 그 토글 디바운스에 쓰인다.
    `_refresh_from_defaults`(콜러블)는 _build_tool_settings_popup이 붙이는 속성 — 휠 조절
    등으로 바뀐 값을 다시 열 때 반영한다."""

    def __init__(self, parent):
        super().__init__(parent, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                          | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.hidden_at = 0.0

    def hideEvent(self, event):
        self.hidden_at = time.monotonic()
        super().hideEvent(event)


# ---------------------------------------------------------------------------
# 편집기 다이얼로그
# ---------------------------------------------------------------------------

def flatten_scene_to_png(scene: QGraphicsScene) -> bytes:
    """씬을 이미지 해상도 PNG bytes로 평탄화(주석 포함) — 화면에 보이는 그대로를
    래스터화한 진짜 이미지 데이터다(화면 캡처가 아니라 QGraphicsScene을 QImage에
    그려 넣는 것). 그래서 렌더 시점에 남아 있는 '편집 중' UI 상태도 그대로 픽셀로
    구워진다 — 선택 핸들은 scene.clearSelection()으로 지우지만, 텍스트를 드래그로
    선택한 채(파란 네이티브 텍스트 선택 하이라이트)로 복사 버튼을 누르면 그 하이라이트도
    함께 찍혔다(2026-07-31 사용자 보고). 텍스트 캐럿/선택은 QGraphicsScene의 아이템
    선택과 별개(QTextCursor 상태)라 clearSelection()이 못 건드리므로 여기서 편집 중인
    텍스트를 명시적으로 커밋(포커스 해제)해 없앤다.
    """
    for it in scene.items():
        if isinstance(it, QGraphicsTextItem) and \
                it.textInteractionFlags() != Qt.TextInteractionFlag.NoTextInteraction:
            cursor = it.textCursor()
            cursor.clearSelection()
            it.setTextCursor(cursor)
            it.clearFocus()  # focusOutEvent가 편집 종료 처리(인터랙션 해제 등)
    scene.clearSelection()
    rect = scene.sceneRect()
    img = QImage(int(round(rect.width())), int(round(rect.height())),
                 QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    painter = QPainter(img)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    scene.render(painter, QRectF(0, 0, img.width(), img.height()), rect)
    painter.end()
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(buf.data())


class _EditorMixin:
    """주석 편집 동작(도구·색·두께·스포이드·undo). 호스트 QWidget이 상속해 사용한다.

    호스트가 갖춰야 할 것:
      - self._scene(QGraphicsScene), self._view(_AnnotatorView) — _init_editor_state 전에 생성
      - 시그널 annotated_copy_requested(bytes) / export_file_requested(bytes)
      - 메서드 is_edit_mode()/toggle_edit_mode()/_on_escape()/_win_drag_start/_win_drag_move/
        _win_drag_end/_on_wheel_zoom/close — _AnnotatorView가 호출
    """

    # 도구별(rect/ellipse/line/pen/arrow/text/badge) 색·크기 기본값 — 우클릭 미니패널로 조절.
    # 하나의 전역 색/두께를 모든 도구가 공유하던 옛 방식(2026-07-31 이전)을 대체: 도구마다
    # 독립된 값이라 화살표 색을 바꿔도 네모·펜 등 다른 도구엔 새지 않는다.
    # 새 편집기(다음에 연 이미지)도 기본값이 아니라 이 값으로 시작 — DB에도 저장돼
    # 앱 재시작 후에도 유지: 시작 시 main이 load_last_values로 주입, 변경 시
    # _persist_cb(main 등록)로 DB에 기록. (미등록이면 세션 내 기억만.)
    _tool_defaults = None   # dict[str, dict] — 클래스 변수, 최초 사용 시 _hardcoded_tool_defaults()로 채움
    _persist_cb = None      # callable(json_str) → DB 저장 (main이 시작 시 1회 등록)

    @staticmethod
    def _hardcoded_tool_defaults() -> dict:
        return {
            "rect":    {"color": _DEFAULT_COLOR, "width": _DEFAULT_WIDTH},
            "ellipse": {"color": _DEFAULT_COLOR, "width": _DEFAULT_WIDTH},
            "line":    {"color": _DEFAULT_COLOR, "width": _DEFAULT_WIDTH},
            "pen":     {"color": _DEFAULT_COLOR, "width": _DEFAULT_WIDTH},
            "arrow":   {"color": _DEFAULT_COLOR, "width": _DEFAULT_WIDTH,
                        "head": _DEFAULT_HEAD, "head_at_end": True},
            "text":    {"color": _DEFAULT_COLOR, "font": _DEFAULT_FONT},
            "badge":   {"color": _DEFAULT_COLOR, "size": _DEFAULT_BADGE},
        }

    @classmethod
    def load_last_values(cls, json_str):
        """앱 시작 시 DB에 저장된 도구별 마지막 색·크기를 주입. 파싱 실패·손상은 도구
        단위로만 기본값 보강(일부 필드가 깨져도 나머지 도구 설정은 보존)."""
        defaults = cls._hardcoded_tool_defaults()
        try:
            saved = json.loads(json_str) if json_str else {}
        except (TypeError, ValueError):
            saved = {}
        if isinstance(saved, dict):
            for key, base in defaults.items():
                entry = saved.get(key)
                if not isinstance(entry, dict):
                    continue
                if isinstance(entry.get("color"), str) and QColor(entry["color"]).isValid():
                    base["color"] = entry["color"]
                if "width" in base:
                    base["width"] = _clamp_int(entry.get("width"), _MIN_WIDTH, _MAX_WIDTH, base["width"])
                if "font" in base:
                    base["font"] = _clamp_int(entry.get("font"), _MIN_FONT, _MAX_FONT, base["font"])
                if "size" in base:
                    base["size"] = _clamp_int(entry.get("size"), _MIN_BADGE, _MAX_BADGE, base["size"])
                if "head" in base:
                    base["head"] = _clamp_int(entry.get("head"), _MIN_HEAD, _MAX_HEAD, base["head"])
                if "head_at_end" in base:
                    base["head_at_end"] = bool(entry.get("head_at_end", base["head_at_end"]))
        cls._tool_defaults = defaults

    def _persist_tool_defaults(self):
        """도구별 값 변경 시 DB에 기록(콜백 미등록이면 세션 내 기억만)."""
        cb = _EditorMixin._persist_cb
        if cb is not None:
            cb(json.dumps(_EditorMixin._tool_defaults))

    @property
    def tool_defaults(self) -> dict:
        return _EditorMixin._tool_defaults

    def _init_editor_state(self):
        self.current_tool = "select"
        if _EditorMixin._tool_defaults is None:
            _EditorMixin._tool_defaults = _EditorMixin._hardcoded_tool_defaults()
        self.current_text_bg = None  # 새 텍스트의 기본 배경(None=투명) — 도구별 값과 별개로 유지
        self._undo: list[tuple[str, list]] = []
        self._last_move_key = None   # 직전 move undo의 합침 키(연속 화살표키 nudge 병합용)
        self._clip: list = []        # Ctrl+C로 담아둔 주석 복제 템플릿
        self._paste_seq = 0          # 연속 붙여넣기 오프셋 카운터
        # 스포이드 상태
        self._eyedrop_active = False
        self._eyedrop_timer = None
        self._loupe = None
        self._eyedrop_prev_lbtn = False
        self._eyedrop_last = None
        self._eyedrop_target_tool = None  # 스포이드로 고른 색을 적용할 도구(미니패널에서 진입 시 설정)
        self._tool_buttons: dict[str, QToolButton] = {}
        self._tool_popups: dict[str, QWidget] = {}          # 도구별 우클릭 미니패널(지연 생성·캐시)
        self._tool_preset_buttons: dict[str, list] = {}     # 도구별 색 스와치 버튼 목록(하이라이트 갱신용)
        self._arrow_dir_panel_btn = None  # 화살표 미니패널 안의 방향 토글 버튼

    # ---- 툴바 / 액션바 (호스트가 배치) -------------------------------------
    def _build_toolbar(self) -> QHBoxLayout:
        tools = QHBoxLayout()
        tools.setContentsMargins(6, 2, 6, 2)
        tools.setSpacing(3)

        # 우측 배치는 호스트(chrome_l AlignRight)가 담당 — pill이 내용에 딱 맞게 hug하도록 stretch 없음.

        # 도구 (아이콘)
        group = QButtonGroup(self)
        group.setExclusive(False)  # '0개 선택'(손 모드) 허용 — set_tool이 체크를 직접 관리
        for key, name, sc in _TOOLS:
            btn = QToolButton()
            btn.setIconSize(QSize(18, 18))
            btn.setCheckable(True)
            tip = f"{name} ({sc})"
            if key in _DRAW_TOOLS:
                tip += "\n우클릭: 색·크기 설정"
            btn.setToolTip(tip)
            # 활성 도구를 다시 누르면 손 모드(None)로 복귀 — 토글.
            btn.clicked.connect(lambda _c, k=key: self.set_tool(None if self.current_tool == k else k))
            if key in _DRAW_TOOLS:
                # 우클릭 미니패널 — 도구별 색·두께(화살표는 머리크기·방향도)를 독립 조절.
                btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                btn.customContextMenuRequested.connect(
                    lambda _pos, k=key, b=btn: self._show_tool_settings(k, b))
            group.addButton(btn)
            tools.addWidget(btn)
            self._tool_buttons[key] = btn

        # 되돌리기 — 도구 행 끝(번호 옆)
        undo_btn = QToolButton()
        undo_btn.setIcon(_tool_icon("undo", neutral_override=_ICON_DARK))
        undo_btn.setIconSize(QSize(18, 18))
        undo_btn.setToolTip("되돌리기 (Ctrl+Z)")
        undo_btn.clicked.connect(self.undo)
        tools.addWidget(undo_btn)

        tools.addWidget(self._vsep())

        # 색·두께는 이제 전역 버튼이 아니라 각 도구 아이콘 우클릭 미니패널로 도구별 독립 조절
        # (2026-07-31 개편 — 옛 무지개 버튼 1개가 모든 도구에 같은 색을 적용하던 방식 폐기).
        # 두께 조절도 주석 위 Shift+휠로 병행 가능(adjust_item_property).
        # (그냥 휠은 항상 줌 — 2026-07-31, 조절과 줌이 같은 휠을 다투던 것을 Shift로 분리)

        # 완료 액션 — 아이콘 버튼, 색 옆 고정 (이미지 줌으로 창이 넓어져도 위치 불변).
        # 복사/저장은 같은 중립색으로 통일. 닫기는 이미지 우상단 floating(호스트가 배치).
        copy_btn = QToolButton()
        copy_btn.setIcon(_tool_icon("copy", neutral_override=_ICON_DARK))
        copy_btn.setIconSize(QSize(18, 18))
        copy_btn.setToolTip("복사 — 클립보드에 복사 (히스토리에도 새 항목으로 저장)")
        copy_btn.clicked.connect(self._do_copy)
        tools.addWidget(copy_btn)

        export_btn = QToolButton()
        export_btn.setIcon(_tool_icon("save", neutral_override=_ICON_DARK))
        export_btn.setIconSize(QSize(18, 18))
        export_btn.setToolTip("저장 — PNG 파일로 저장")
        export_btn.clicked.connect(self._do_export)
        tools.addWidget(export_btn)

        # 화살표 방향 토글은 이제 화살표 도구 우클릭 미니패널 안에 있다(옛 floating 버튼 폐기).

        # 텍스트 하위 옵션 바 — 텍스트 도구 활성 시 T 버튼 위에 수평 floating(배경 스와치만).
        # 글자·번호 크기 스테퍼는 제거 — 크기는 주석 위 휠로 조절하고 마지막 값을 기억한다.
        self._text_opts_bar = self._build_text_opts_bar()
        self._text_opts_bar.setVisible(False)
        return tools

    # ---- 도구별 우클릭 미니패널 (색·두께/글자·번호 크기, 화살표는 머리크기·방향도) ------
    def _show_tool_settings(self, tool_key: str, btn: QToolButton):
        """도구 아이콘 우클릭 → 그 도구 전용 미니패널 토글. 그랩하지 않는 패널이라(위
        _ToolSettingsPopup 참고) 다른 패널이 열려 있으면 여기서 먼저 명시적으로 닫는다."""
        pop = self._tool_popups.get(tool_key)
        if pop is None:
            pop = self._build_tool_settings_popup(tool_key)
            self._tool_popups[tool_key] = pop
        if pop.isVisible():
            pop.hide()
            return
        if time.monotonic() - pop.hidden_at < 0.25:
            return
        self._hide_tool_popups()
        pop._refresh_from_defaults()  # 휠 조절 등으로 바뀐 값이 있으면 반영 후 표시
        pop.adjustSize()
        pos = btn.mapToGlobal(QPoint(0, -pop.height() - 4))
        pop.move(pos)
        pop.show()
        pop.raise_()

    def _hide_tool_popups(self):
        """열려 있는 도구 미니패널을 전부 닫는다 — 도구 전환·캔버스 클릭 시 호출해
        그랩 없는 패널(Qt.WindowType.Tool)이 화면에 방치되지 않게 한다."""
        for pop in self._tool_popups.values():
            if pop.isVisible():
                pop.hide()

    def _build_tool_settings_popup(self, tool_key: str) -> QWidget:
        """도구 하나의 미니패널: 색 프리셋+스포이드 한 줄 + 크기 슬라이더(들). 화살표는
        두께 슬라이더 아래에 머리 크기 슬라이더 + 방향 토글 버튼을 추가로 담는다."""
        pop = _ToolSettingsPopup(self)
        pop.setObjectName("toolsettings")
        pop.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        pop.setStyleSheet(
            f"QWidget#toolsettings {{ background-color: {_SURFACE0};"
            f" border: 1px solid {_BORDER}; border-radius: 6px; }}"
            f"QToolButton {{ background-color: {_SURFACE0}; border: 1px solid {_BORDER};"
            f" border-radius: 4px; padding: 2px; }}"
            f"QToolButton:hover {{ background-color: {_SURFACE2}; }}"
            f"QLabel {{ color: {_TEXT}; background: transparent; }}"
        )
        col = QVBoxLayout(pop)
        col.setContentsMargins(8, 8, 8, 8)
        col.setSpacing(6)

        preset_buttons: list[tuple[QColor, QToolButton]] = []
        color_row = QHBoxLayout()
        color_row.setSpacing(4)
        for hexs in _COLOR_PRESETS:
            c = QColor(hexs)
            b = QToolButton()
            b.setObjectName("swatch")
            b.setFixedSize(20, 20)
            b.setCheckable(True)
            b.setToolTip(hexs)
            b.clicked.connect(lambda _c, cc=c, k=tool_key: self._set_tool_color(k, cc))
            color_row.addWidget(b)
            preset_buttons.append((c, b))
        eyedrop_btn = QToolButton()
        eyedrop_btn.setIcon(_tool_icon("eyedrop"))
        eyedrop_btn.setIconSize(QSize(18, 18))
        eyedrop_btn.setToolTip("스포이드 — 화면에서 색 따오기 (클릭으로 선택, ESC 취소)")
        eyedrop_btn.clicked.connect(lambda _c=None, k=tool_key: self._pick_tool_eyedrop(k))
        color_row.addWidget(eyedrop_btn)
        col.addLayout(color_row)
        self._tool_preset_buttons[tool_key] = preset_buttons

        sliders: dict[str, tuple[QSlider, QLabel]] = {}
        dir_btn = None
        if tool_key == "text":
            row, slider, val_lab = self._build_slider_row(
                "크기", _MIN_FONT, _MAX_FONT, lambda v, k=tool_key: self._set_tool_size(k, "font", v))
            col.addLayout(row)
            sliders["font"] = (slider, val_lab)
        elif tool_key == "badge":
            row, slider, val_lab = self._build_slider_row(
                "크기", _MIN_BADGE, _MAX_BADGE, lambda v, k=tool_key: self._set_tool_size(k, "size", v))
            col.addLayout(row)
            sliders["size"] = (slider, val_lab)
        else:
            row, slider, val_lab = self._build_slider_row(
                "두께", _MIN_WIDTH, _MAX_WIDTH, lambda v, k=tool_key: self._set_tool_size(k, "width", v))
            col.addLayout(row)
            sliders["width"] = (slider, val_lab)
            if tool_key == "arrow":
                row2, slider2, val_lab2 = self._build_slider_row(
                    "머리 크기", _MIN_HEAD, _MAX_HEAD, self._set_arrow_head_size)
                col.addLayout(row2)
                sliders["head"] = (slider2, val_lab2)

                dir_row = QHBoxLayout()
                dir_row.setSpacing(6)
                dir_lab = QLabel("방향")
                dir_lab.setFixedWidth(52)
                dir_btn = QToolButton()
                dir_btn.setIconSize(QSize(28, 20))
                dir_btn.setToolTip("화살표 방향 바꾸기 (선택된 화살표도 뒤집음)")
                dir_btn.clicked.connect(self._toggle_arrow_dir_panel)
                dir_row.addWidget(dir_lab)
                dir_row.addWidget(dir_btn)
                dir_row.addStretch()
                col.addLayout(dir_row)
                self._arrow_dir_panel_btn = dir_btn

        def _refresh():
            d = self.tool_defaults[tool_key]
            cur_name = QColor(d["color"]).name().lower()
            for c, b in preset_buttons:
                sel = c.name().lower() == cur_name
                b.blockSignals(True)
                b.setChecked(sel)
                b.blockSignals(False)
                b.setStyleSheet(self._swatch_style(c, sel))
            for field, (slider, val_lab) in sliders.items():
                v = d[field]
                slider.blockSignals(True)
                slider.setValue(v)
                slider.blockSignals(False)
                val_lab.setText(str(v))
            if tool_key == "arrow" and dir_btn is not None:
                dir_btn.setIcon(_arrow_dir_icon(d["head_at_end"]))

        pop._refresh_from_defaults = _refresh
        _refresh()
        pop.adjustSize()
        return pop

    def _build_slider_row(self, label: str, lo: int, hi: int, on_change):
        """라벨 + 가로 슬라이더 + 값 표시로 이뤄진 한 줄. (layout, slider, value_label) 반환
        — 값 라벨·슬라이더는 미니패널이 다시 열릴 때 최신값으로 갱신(_refresh_from_defaults)하는 데 쓰인다."""
        row = QHBoxLayout()
        row.setSpacing(6)
        lab = QLabel(label)
        lab.setFixedWidth(52)
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setMinimum(lo)
        slider.setMaximum(hi)
        slider.setFixedWidth(120)
        val_lab = QLabel("")
        val_lab.setFixedWidth(28)

        def _changed(v):
            val_lab.setText(str(v))
            on_change(v)

        slider.valueChanged.connect(_changed)
        row.addWidget(lab)
        row.addWidget(slider)
        row.addWidget(val_lab)
        return row, slider, val_lab

    def _set_tool_color(self, tool_key: str, color):
        self.tool_defaults[tool_key]["color"] = QColor(color).name()
        self._persist_tool_defaults()
        for c, b in self._tool_preset_buttons.get(tool_key, []):
            sel = c.name().lower() == QColor(color).name().lower()
            b.setChecked(sel)
            b.setStyleSheet(self._swatch_style(c, sel))
        self._refresh_tool_icons()
        for it in self._scene.selectedItems():
            if self._tool_key_for_item(it) == tool_key and hasattr(it, "apply_color"):
                it.apply_color(QColor(color))

    def _set_tool_size(self, tool_key: str, field: str, value: int):
        self.tool_defaults[tool_key][field] = value
        self._persist_tool_defaults()
        for it in self._scene.selectedItems():
            if self._tool_key_for_item(it) != tool_key:
                continue
            if field == "width" and hasattr(it, "apply_width"):
                it.apply_width(value)
            elif field == "font" and isinstance(it, _TextItem):
                it.apply_font_size(value)
            elif field == "size" and isinstance(it, _BadgeItem):
                it.setScale(value / float(_DEFAULT_BADGE))

    def _set_arrow_head_size(self, value: int):
        self.tool_defaults["arrow"]["head"] = value
        self._persist_tool_defaults()
        for it in self._scene.selectedItems():
            if isinstance(it, _ArrowItem):
                it.apply_head_size(value)

    def _toggle_arrow_dir_panel(self):
        sel = [it for it in self._scene.selectedItems() if isinstance(it, _ArrowItem)]
        if sel:
            new_val = not sel[0]._head_at_end
            for it in sel:
                it.set_head_at_end(new_val)
        else:
            new_val = not self.tool_defaults["arrow"]["head_at_end"]
        self.tool_defaults["arrow"]["head_at_end"] = new_val
        self._persist_tool_defaults()
        btn = self._arrow_dir_panel_btn
        if btn is not None:
            btn.setIcon(_arrow_dir_icon(new_val))

    def _tool_key_for_item(self, item) -> str | None:
        if isinstance(item, _ArrowItem):
            return "arrow"
        if isinstance(item, _RectItem):
            return "rect"
        if isinstance(item, _EllipseItem):
            return "ellipse"
        if isinstance(item, _LineItem):
            return "line"
        if isinstance(item, _PathItem):
            return "pen"
        if isinstance(item, _TextItem):
            return "text"
        if isinstance(item, _BadgeItem):
            return "badge"
        return None

    def _pick_tool_eyedrop(self, tool_key: str):
        pop = self._tool_popups.get(tool_key)
        if pop is not None:
            pop.hide()
        self._eyedrop_target_tool = tool_key
        self._start_eyedropper()

    def _build_text_opts_bar(self) -> QWidget:
        bar = QWidget(self)
        bar.setObjectName("textopts")
        bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        row = QHBoxLayout(bar)
        row.setContentsMargins(5, 3, 5, 3)
        row.setSpacing(4)
        # 배경 스와치 — 투명/흰/회/검/반투명을 모두 펼쳐 한 번에 직접 선택
        self._bg_buttons: list[tuple] = []
        bg_group = QButtonGroup(bar)
        bg_group.setExclusive(True)
        for bg, label in _TEXT_BG_OPTIONS:
            btn = QToolButton()
            btn.setObjectName("bgswatch")
            btn.setCheckable(True)
            btn.setIcon(_bg_swatch_icon(bg))
            btn.setIconSize(QSize(20, 20))
            btn.setToolTip(f"텍스트 배경: {label} (선택된 텍스트에도 적용)")
            btn.clicked.connect(lambda _c, b=bg: self._set_text_bg(b))
            bg_group.addButton(btn)
            row.addWidget(btn)
            self._bg_buttons.append((bg, btn))
        bar.adjustSize()
        self._sync_bg_buttons()
        return bar

    def _vsep(self) -> QLabel:
        sep = QLabel()
        sep.setFixedWidth(1)
        # 밝은 툴바 pill 위 구분선 — 옅은 회색(어두운 _BORDER는 밝은 바에서 너무 튐).
        sep.setStyleSheet("background-color: #d0d0d0;")
        return sep

    @staticmethod
    def _swatch_style(color: QColor, selected: bool) -> str:
        border = f"2px solid {_BLUE}" if selected else f"1px solid {_BORDER}"
        return (f"QToolButton#swatch {{ background-color: {color.name()};"
                f" border: {border}; border-radius: 3px; }}")

    def _editor_stylesheet(self, view_border: str) -> str:
        """편집 UI 전체 스타일시트. view_border로 그래픽스뷰(이미지) 테두리 색을 바꿔
        호스트가 활성/비활성 테두리(코랄=활성/회색=비활성)를 표현한다."""
        return f"""
            QWidget {{
                background-color: {_BG};
                color: {_TEXT};
                font-size: 12px;
            }}
            QToolButton#editclose {{
                background-color: rgba(0, 0, 0, 0.45);
                border: none;
                border-radius: 13px;
                padding: 3px;
            }}
            QToolButton#editclose:hover {{ background-color: {_PEACH}; }}
            QToolButton {{
                background-color: {_SURFACE0};
                border: 1px solid {_BORDER};
                border-radius: 4px;
                padding: 2px;
            }}
            QToolButton:hover {{ background-color: {_SURFACE2}; }}
            QToolButton:checked {{
                background-color: {_BLUE};
                border: 1px solid {_BLUE};
            }}
            QWidget#textopts {{
                background-color: {_SURFACE0};
                border: 1px solid {_BORDER};
                border-radius: 5px;
            }}
            QToolButton#bgswatch {{
                background-color: {_SURFACE0};
                border: 1px solid {_BORDER};
                border-radius: 4px;
                padding: 2px;
            }}
            QToolButton#bgswatch:checked {{
                background-color: {_SURFACE0};
                border: 2px solid {_BLUE};
            }}
            QPushButton {{
                background-color: {_SURFACE0};
                border: 1px solid {_BORDER};
                border-radius: 4px;
                padding: 5px 12px;
            }}
            QPushButton:hover {{ background-color: {_SURFACE2}; }}
            QPushButton#primary {{
                background-color: {_BLUE};
                color: {_BG};
                border: 1px solid {_BLUE};
            }}
            QGraphicsView {{
                background-color: {_SURFACE0};
                border: 2px solid {view_border};
            }}
        """

    # ---- 도구/색/두께 상태 -------------------------------------------------
    def set_tool(self, tool):
        self.current_tool = tool
        self._hide_tool_popups()  # 도구를 바꾸면 열려 있던 미니패널은 정리
        if tool == "select":
            self._view.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        else:
            self._view.setDragMode(QGraphicsView.DragMode.NoDrag)
        # 버튼 체크 상태 직접 관리(그룹 비배타) — 손 모드(None)면 전부 해제.
        for k, b in self._tool_buttons.items():
            if b.isChecked() != (k == tool):
                b.setChecked(k == tool)
        # 도구 기본 커서 — hover 이벤트 전 stale 방지(주석 위 SizeAll은 다음 move에서 갱신)
        self._view.viewport().setCursor(
            Qt.CursorShape.OpenHandCursor if tool is None
            else Qt.CursorShape.ArrowCursor if tool == "select"
            else Qt.CursorShape.IBeamCursor if tool == "text"
            else Qt.CursorShape.CrossCursor
        )
        # 선택 항목 repaint — 핸들이 선택(V) 도구에서만 보이므로 도구 전환 시 즉시 반영
        for it in self._scene.selectedItems():
            it.update()
        self._update_text_opts_bar()

    def _update_text_opts_bar(self):
        """텍스트 도구가 활성이고 편집 모드일 때만 T 버튼 위에 텍스트 옵션 바 floating."""
        bar = getattr(self, "_text_opts_bar", None)
        if bar is None:
            return
        edit = self.is_edit_mode() if hasattr(self, "is_edit_mode") else True
        if self.current_tool == "text" and edit:
            text_btn = self._tool_buttons.get("text")
            if text_btn is not None:
                bar.adjustSize()
                # 툴바가 창 하단이라 버튼 '위'로 띄운다(아래면 창 밖으로 잘림).
                bar.move(text_btn.mapTo(self, QPoint(0, -bar.height() - 2)))
            bar.setVisible(True)
            bar.raise_()
        else:
            bar.setVisible(False)

    def _set_text_bg(self, bg):
        self.current_text_bg = QColor(bg) if bg is not None else None
        self._sync_bg_buttons()
        # 작성 중인 텍스트 우선, 없으면 선택된 텍스트에 즉시 적용(글자 크기와 동일 대상 규칙).
        for it in self._font_size_targets():
            it.set_bg(self.current_text_bg)

    def _sync_bg_buttons(self):
        """현재 배경(current_text_bg)에 해당하는 스와치만 체크 표시."""
        cur = self.current_text_bg
        for bg, btn in getattr(self, "_bg_buttons", []):
            same = (bg is None and cur is None) or (
                bg is not None and cur is not None and QColor(bg) == QColor(cur))
            btn.setChecked(same)

    def make_pen(self, tool_key: str) -> QPen:
        d = self.tool_defaults[tool_key]
        return QPen(QColor(d["color"]), d["width"], Qt.PenStyle.SolidLine,
                    Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)

    def next_badge_number(self) -> int:
        # 씬에 남은 번호 마커의 최대값+1 (삭제 후 재생성 시 빈 번호를 다시 씀)
        nums = [it._number for it in self._scene.items() if isinstance(it, _BadgeItem)]
        return max(nums, default=0) + 1

    def _refresh_tool_icons(self):
        # 각 도구 아이콘을 그 도구 자신의 색으로 칠한다 — 색이 도구별로 독립이라
        # 아이콘 색 자체가 "이 도구는 지금 이 색으로 그려진다"는 표시가 된다.
        for key, btn in self._tool_buttons.items():
            color = self.tool_defaults[key]["color"] if key in _DRAW_TOOLS else None
            btn.setIcon(_tool_icon(key, color, neutral_override=_ICON_DARK))

    def _font_size_targets(self) -> list:
        fi = self._scene.focusItem()
        if isinstance(fi, _TextItem) and \
                fi.textInteractionFlags() != Qt.TextInteractionFlag.NoTextInteraction:
            return [fi]  # 작성 중인 텍스트만 — 기존 선택 텍스트가 같이 커지지 않게
        return [it for it in self._scene.selectedItems() if isinstance(it, _TextItem)]

    def adjust_item_property(self, item, step: int):
        """주석 위 휠 — 도형은 두께(±1), 텍스트·번호는 크기(±2)를 step 방향으로 조절.
        조절값을 그 항목이 속한 도구의 기본값(tool_defaults)에도 반영해 다음에 그리는
        같은 도구의 주석도 같은 두께·크기가 되게 한다(undo는 색·두께 변경과 동일하게 미추적).
        화살표 머리 크기는 두께와 독립이라 여기서는 건드리지 않는다 — 미니패널 슬라이더 전용."""
        tool_key = self._tool_key_for_item(item)
        if isinstance(item, _TextItem):
            new = max(_MIN_FONT, min(item.font().pointSize() + step * 2, _MAX_FONT))
            item.apply_font_size(new)
            if tool_key:
                self.tool_defaults[tool_key]["font"] = new
        elif isinstance(item, _BadgeItem):
            cur = round(item.scale() * _DEFAULT_BADGE)
            new = max(_MIN_BADGE, min(cur + step * 2, _MAX_BADGE))
            item.setScale(new / float(_DEFAULT_BADGE))
            if tool_key:
                self.tool_defaults[tool_key]["size"] = new
        else:
            if isinstance(item, _ArrowItem):
                new = max(_MIN_WIDTH, min(item._width + step, _MAX_WIDTH))
            elif hasattr(item, "apply_width") and hasattr(item, "pen"):
                new = max(_MIN_WIDTH, min(int(round(item.pen().widthF())) + step, _MAX_WIDTH))
            else:
                return
            item.apply_width(new)
            if tool_key:
                self.tool_defaults[tool_key]["width"] = new
        self._persist_tool_defaults()   # 변경된 마지막 값을 DB에 기록(재시작 후 유지)

    # ---- 스포이드 (화면 픽셀 색 따오기) ------------------------------------
    def _start_eyedropper(self):
        if self._eyedrop_active:
            return
        import ctypes
        # 로컬 WinDLL 인스턴스 — 핸들 안전 restype/argtypes를 지정해도 전역
        # ctypes.windll.user32(paste_interceptor 등 공유)에 영향을 주지 않는다.
        # 64비트 Windows에서 HDC는 64비트이므로 기본 restype(c_int)이면 핸들이 잘린다.
        self._user32 = ctypes.WinDLL("user32")
        self._gdi32 = ctypes.WinDLL("gdi32")
        self._user32.GetDC.restype = ctypes.c_void_p
        self._user32.GetDC.argtypes = [ctypes.c_void_p]
        self._user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self._gdi32.GetPixel.restype = ctypes.c_uint  # COLORREF
        self._gdi32.GetPixel.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]

        self._eyedrop_active = True
        self._eyedrop_last = None
        self._loupe = _ColorLoupe()
        self._loupe.show()
        self._eyedrop_prev_lbtn = bool(self._user32.GetAsyncKeyState(0x01) & 0x8000)
        self._eyedrop_timer = QTimer(self)
        self._eyedrop_timer.setInterval(25)
        self._eyedrop_timer.timeout.connect(self._eyedrop_tick)
        self._eyedrop_timer.start()

    def _eyedrop_tick(self):
        import ctypes
        from ctypes import wintypes
        user32 = self._user32
        gdi32 = self._gdi32

        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        hdc = user32.GetDC(None)
        cref = gdi32.GetPixel(hdc, pt.x, pt.y)
        user32.ReleaseDC(None, hdc)
        if cref != 0xFFFFFFFF:  # CLR_INVALID
            r = cref & 0xFF
            g = (cref >> 8) & 0xFF
            b = (cref >> 16) & 0xFF
            col = QColor(r, g, b)
            self._eyedrop_last = col
            if self._loupe is not None:
                self._loupe.set_color(col)
                gp = QCursor.pos()
                self._loupe.move(gp.x() + 18, gp.y() + 18)

        if (user32.GetAsyncKeyState(0x1B) & 0x8000) or (user32.GetAsyncKeyState(0x02) & 0x8000):
            self._stop_eyedropper(False)
            return
        lbtn = bool(user32.GetAsyncKeyState(0x01) & 0x8000)
        if lbtn and not self._eyedrop_prev_lbtn:
            self._stop_eyedropper(True)
            return
        self._eyedrop_prev_lbtn = lbtn

    def _stop_eyedropper(self, picked: bool):
        self._eyedrop_active = False
        if self._eyedrop_timer is not None:
            self._eyedrop_timer.stop()
            self._eyedrop_timer = None
        if self._loupe is not None:
            self._loupe.close()
            self._loupe = None
        if picked and self._eyedrop_last is not None and self._eyedrop_target_tool is not None:
            self._set_tool_color(self._eyedrop_target_tool, self._eyedrop_last)
        self.activateWindow()
        self.raise_()

    # ---- Undo --------------------------------------------------------------
    def push_undo_add(self, item: QGraphicsItem):
        self._undo.append(("add", [item]))

    def push_undo_delete(self, items: list):
        self._undo.append(("delete", list(items)))

    def push_undo_move(self, pairs: list, coalesce_key=None):
        """이동(pos 변경) 되돌리기 기록. pairs=[(item, 이동 전 QPointF), ...].
        coalesce_key가 직전 move와 같으면(연속 화살표키 nudge) 새 항목을 쌓지 않아
        undo 폭주를 막는다 — 기존 항목이 더 오래된(원래) 위치를 이미 보유하므로."""
        if not pairs:
            return
        if coalesce_key is not None and self._undo \
                and self._undo[-1][0] == "move" and self._last_move_key == coalesce_key:
            return
        self._undo.append(("move", pairs))
        self._last_move_key = coalesce_key

    def undo(self):
        # 이미 사라진 빈 텍스트의 "add"처럼 무의미한 항목은 건너뛰고 실제 동작 1건을 되돌린다.
        self._last_move_key = None  # undo 후엔 다음 nudge를 새 그룹으로(합침 끊기)
        while self._undo:
            action, items = self._undo.pop()
            if action == "add":
                removed = [it for it in items if it.scene() is not None]
                for it in removed:
                    self._scene.removeItem(it)
                if removed:
                    return
                continue
            if action == "delete":
                for it in items:
                    self._scene.addItem(it)
                return
            if action == "move":
                # items = [(item, 이동 전 pos)]. 씬에 남은 항목만 원위치로.
                restored = False
                for it, old_pos in items:
                    if it.scene() is not None:
                        it.setPos(old_pos)
                        restored = True
                if restored:
                    return
                continue

    # ---- 복사 / 붙여넣기 (주석 내부 복제, OS 클립보드 아님) ------------------
    def copy_selection(self):
        sel = [it for it in self._scene.selectedItems() if hasattr(it, "clone")]
        if not sel:
            return
        self._clip = [it.clone() for it in sel]  # 분리된 클론을 템플릿으로 보관
        self._paste_seq = 0

    def paste_selection(self):
        if not self._clip:
            return
        self._scene.clearSelection()
        self._paste_seq += 1
        off = QPointF(12 * self._paste_seq, 12 * self._paste_seq)
        pasted = []
        for template in self._clip:
            it = template.clone()  # 반복 붙여넣기를 위해 템플릿에서 매번 새로 복제
            it.setPos(it.pos() + off)
            self._scene.addItem(it)
            if isinstance(it, _BadgeItem):
                it._number = self.next_badge_number()  # 중복 번호 방지(추가 후 계산)
                it.update()
            it.setSelected(True)
            pasted.append(it)
        if pasted:
            self._undo.append(("add", pasted))

    # ---- 완료 액션 (호스트 시그널 emit) ------------------------------------
    def _do_copy(self):
        self.annotated_copy_requested.emit(flatten_scene_to_png(self._scene))

    def _do_export(self):
        self.export_file_requested.emit(flatten_scene_to_png(self._scene))

    # ---- 키 / 생명주기 -----------------------------------------------------
    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            if self._eyedrop_active:
                self._stop_eyedropper(False)
            else:
                self.close()
            return
        if event.key() == Qt.Key.Key_Z and (event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self.undo()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event):
        if self._eyedrop_active:
            self._stop_eyedropper(False)
        type(self)._instances = [d for d in type(self)._instances if d is not self]
        super().closeEvent(event)
