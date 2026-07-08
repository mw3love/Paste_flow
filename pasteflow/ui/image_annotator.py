"""이미지 주석 편집기 — CleanShot/Snipaste 스타일.

미리보기 팝업에서 Space(또는 우클릭 "주석 편집")로 진입한다. QGraphicsScene 기반이라
줌하면 주석이 이미지와 함께 스케일되고, 그린 도형은 선택·이동·크기조절·삭제가 가능하다.

도구 단축키: V 선택 · R 네모 · E 원 · L 선 · A 화살표 · P 펜 · T 텍스트 · C 번호 · Ctrl+Z 되돌리기 · Ctrl+C/V 주석 복사·붙여넣기.
Shift: 정사각형/정원/45° 스냅. 선택 후 우하단 핸들 드래그로 크기조절(균일 스케일).
완료 동작은 main이 처리한다(시그널만 emit): 클립보드 복사 / 새 히스토리 항목 / 파일 저장.
"""
import io
import math
import struct
import time

from PyQt6.QtCore import (
    Qt, QPoint, QPointF, QRectF, QLineF, QSize, QBuffer, QIODevice, QTimer, pyqtSignal,
)
from PyQt6.QtGui import (
    QPixmap, QImage, QPainter, QPen, QBrush, QColor, QPainterPath,
    QPainterPathStroker, QPolygonF, QFont, QFontMetricsF, QIcon, QCursor,
    QConicalGradient,
)
from PyQt6.QtWidgets import (
    QWidget, QGraphicsScene, QGraphicsView, QGraphicsRectItem,
    QGraphicsEllipseItem, QGraphicsLineItem, QGraphicsPathItem,
    QGraphicsTextItem, QGraphicsItem, QHBoxLayout,
    QPushButton, QToolButton, QButtonGroup, QLabel,
    QStyle, QStyleOptionGraphicsItem,
)

from pasteflow.ui.theme import (
    BASE as _BG, SURFACE0 as _SURFACE0, SURFACE1 as _BORDER,
    SURFACE2 as _SURFACE2, TEXT as _TEXT, BLUE as _BLUE, SUBTEXT0 as _SUBTEXT,
    PEACH as _PEACH, GREEN as _GREEN,
)

_MIN_WIDTH, _MAX_WIDTH, _DEFAULT_WIDTH = 1, 40, 6
_MIN_FONT, _MAX_FONT, _DEFAULT_FONT = 6, 200, 16
# 번호 마커 지름(px). 기본 30 = _BadgeItem._R(15) * 2, scale 1.0에 대응.
_MIN_BADGE, _MAX_BADGE, _DEFAULT_BADGE = 12, 120, 30

# 대표 프리셋 색상 (빨강·주황·노랑·초록·파랑·검정·흰색)
_COLOR_PRESETS = [
    "#FF3B30", "#FF9500", "#FFCC00", "#34C759",
    "#007AFF", "#000000", "#FFFFFF",
]
_DEFAULT_COLOR = _COLOR_PRESETS[0]

# 밝은 툴바(Snipaste식 pill) 위 중립 아이콘 색 — 어두운 회색(선택·되돌리기·복사·저장).
# 그리기 도구 아이콘은 current_color(색)로 칠해져 밝은 바에서도 보인다.
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
    ("select", "선택", "V"), ("rect", "네모", "R"), ("ellipse", "원", "E"),
    ("line", "선", "L"), ("arrow", "화살표", "A"), ("pen", "펜", "P"),
    ("text", "텍스트", "T"), ("badge", "번호", "C"),
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


def _rainbow_icon(current: QColor | None = None, size: int = 20) -> QIcon:
    """무지개 색 버튼 아이콘 — 무지개 링 + 가운데 현재 색 점(팔레트 팝업 진입점)."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    g = QConicalGradient(size / 2, size / 2, 90)
    for stop, hexs in (
        (0.00, "#FF3B30"), (0.17, "#FF9500"), (0.34, "#FFCC00"),
        (0.50, "#34C759"), (0.67, "#007AFF"), (0.84, "#AF52DE"),
        (1.00, "#FF3B30"),
    ):
        g.setColorAt(stop, QColor(hexs))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(g)
    p.drawEllipse(1, 1, size - 2, size - 2)
    if current is not None:
        r = size * 0.30
        p.setBrush(QColor(current))
        p.setPen(QPen(QColor("#FFFFFF"), 1.4))
        p.drawEllipse(QPointF(size / 2, size / 2), r, r)
    p.end()
    return QIcon(pm)


def _bg_swatch_icon(bg) -> QIcon:
    """텍스트 배경 스와치 — 색 채움(반투명은 흰 바탕에 합성). bg=None이면 투명(대각선)."""
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
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("white"))                      # 반투명 색을 흰 바탕에 얹어 합성된 모습
        p.drawRoundedRect(rect, 3, 3)
        p.setBrush(QBrush(bg))
        p.drawRoundedRect(rect, 3, 3)
        p.setPen(QPen(QColor(_SUBTEXT), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect, 3, 3)
    p.end()
    return QIcon(pm)


# ---------------------------------------------------------------------------
# 크기조절 핸들 믹스인 — 선택 시 우하단 핸들 드래그로 균일 스케일
# ---------------------------------------------------------------------------

class _HandleResizeMixin:
    # 핸들(스케일 사각·회전 원·끝점 사각) 크기는 도형의 '획 두께'에 비례한다 — 얇은 선은
    # 작은 핸들, 굵은 선은 큰 핸들. 씬 단위로 [MIN,MAX] 클램프(못 잡을 만큼 작지도, 거슬릴
    # 만큼 크지도 않게). 획이 없는 도형(번호·텍스트)만 표시 크기 비례로 폴백한다.
    _HANDLE_FRAC = 0.22        # (폴백) 작은 변 대비 핸들 비율 — 번호·텍스트용
    _HANDLE_STROKE_FRAC = 1.4  # 획 두께 대비 핸들 비율 — 도형·선·화살표용
    _HANDLE_MIN = 5.0    # 씬 단위 하한(항상 잡히게)
    _HANDLE_MAX = 12.0   # 씬 단위 상한
    _ROT_GAP = 14.0  # 도형 윗변 ~ 회전 원 사이 빈 줄기(씬 단위, 원 크기와 무관하게 일정)
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

    def _init_resize(self):
        self._resizing = False
        self._rotating = False
        self._drag_endpoint = None  # 끝점 드래그 중인 인덱스(0·1, None=없음) — 선·화살표만
        self._press_scale = 1.0
        self._press_dist = 1.0
        self._press_rot = 0.0
        self._press_angle = 0.0

    # ---- 끝점(양끝 이동) 모드 -------------------------------------------
    # 선·화살표처럼 '2점으로 완전히 결정되는' 도형은 회전+균일스케일 핸들 대신
    # 양끝점 핸들을 쓴다(끝점 2개면 길이·각도가 모두 결정 → 회전/스케일 중복). 기본은 off라
    # 네모·원·번호·텍스트는 기존 회전+스케일 핸들을 그대로 쓴다.
    _ENDPOINT_TOOLS = (None, "select", "line", "arrow")

    def _uses_endpoints(self) -> bool:
        return False

    def _endpoints(self):
        """끝점들의 로컬 좌표 리스트(선·화살표가 override)."""
        return []

    def _set_endpoint(self, idx: int, p: QPointF):
        """끝점 idx를 로컬 좌표 p로 이동(선·화살표가 override)."""
        pass

    def _endpoint_active(self) -> bool:
        if not self.isSelected():
            return False
        return self._owner_tool() in self._ENDPOINT_TOOLS

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

    # 실제 boundingRect = content ∪ 회전 핸들 영역(상시 예약 → 선택 해제 시 핸들 잔상 방지).
    # 위쪽뿐 아니라 좌우도 덮어야 함 — 얇은 도형(세로선 등)은 핸들 원이 content보다 가로로
    # 넓어 좌우로 삐져나오므로. 여유분은 scale 의존이라, 크기조절 중 mouseMove에서
    # prepareGeometryChange로 갱신한다.
    def boundingRect(self) -> QRectF:
        pad = 3.0 / self._scale_or_1()
        if self._uses_endpoints():
            r = self._content_rect()
            for i in range(len(self._endpoints())):
                r = r.united(self._endpoint_rect(i))
            return r.adjusted(-pad, -pad, pad, pad)
        return self._content_rect().united(self._rot_handle_rect().adjusted(-pad, -pad, pad, pad))

    def _handle_local_rect(self) -> QRectF:
        h = self._handle_px()
        c = self._content_rect().bottomRight()
        return QRectF(c.x() - h, c.y() - h, h, h)

    def _rot_handle_center(self) -> QPointF:
        # 우상단 코너 바깥쪽으로 대각선(45°) 오프셋 — 크기조절(우하단)과 오른쪽 변에 위아래로 정렬.
        # 원이 가리는 부분(반지름)을 간격에 더해, 보이는 줄기(gap)가 핸들 크기와 무관하게 일정.
        cr = self._content_rect()
        r = self._handle_px() * 0.5  # 원 반지름(= 사각 변의 절반 → 사각과 같은 지름)
        off = (self._ROT_GAP / self._scale_or_1() + r) * 0.70710678  # 대각선 성분
        return QPointF(cr.right() + off, cr.top() - off)

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
        # 크기조절 핸들은 선택(V) 도구일 때만 — 그리기 중엔 거슬리므로 숨긴다.
        tool = self._owner_tool()
        if tool is not None and tool != "select":
            return False
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
        # 회전 핸들 — content 우상단 코너 바깥에 줄기 + 코랄 원
        cr = self._content_rect()
        corner = QPointF(cr.right(), cr.top())
        rc = self._rot_handle_center()
        rh = self._handle_px() * 0.5  # 반지름 — 지름이 크기조절 사각 변과 같게
        painter.setPen(QPen(QColor(_PEACH), 1.0 / s))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawLine(corner, rc)
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
        base = self._base_shape()
        if self._uses_endpoints():
            if self._endpoint_active():
                hp = QPainterPath()
                for i in range(len(self._endpoints())):
                    hp.addRect(self._endpoint_rect(i))
                return base.united(hp)
            return base
        if self._handle_active():
            hp = QPainterPath()
            hp.addRect(self._handle_local_rect())
            hp.addEllipse(self._rot_handle_rect())
            return base.united(hp)
        return base

    def mousePressEvent(self, event):
        if self._uses_endpoints():
            if self._endpoint_active():
                for i in range(len(self._endpoints())):
                    if self._endpoint_rect(i).contains(event.pos()):
                        self._drag_endpoint = i
                        event.accept()
                        return
            super().mousePressEvent(event)
            return
        if self._handle_active():
            # 회전 핸들이 바깥쪽이라 먼저 검사한다.
            if self._rot_handle_rect().contains(event.pos()):
                self._rotating = True
                self.setTransformOriginPoint(self._content_rect().center())
                center = self.mapToScene(self._content_rect().center())
                self._press_angle = QLineF(center, event.scenePos()).angle()
                self._press_rot = self.rotation()
                event.accept()
                return
            if self._handle_local_rect().contains(event.pos()):
                self._resizing = True
                self.setTransformOriginPoint(self._content_rect().center())
                center = self.mapToScene(self._content_rect().center())
                d = QLineF(center, event.scenePos()).length()
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
                p = self._snap_endpoint(self._drag_endpoint, p)
            self._set_endpoint(self._drag_endpoint, p)
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
            center = self.mapToScene(self._content_rect().center())
            d = QLineF(center, event.scenePos()).length()
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
            self._rotating = False
            self._resizing = False
            event.accept()
            return
        super().mouseReleaseEvent(event)


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

    def __init__(self, color: QColor, width: int, head_at_end: bool = True):
        super().__init__()
        self._p1 = QPointF(0, 0)
        self._p2 = QPointF(0, 0)
        self._ctrl1 = None     # 3차 베지어 제어점 2개(None,None=직선). 로컬(=씬) 좌표.
        self._ctrl2 = None
        self._bend_idx = 0     # 드래그 중인 bend 핸들(1·2, 0=없음)
        self._color = QColor(color)
        self._width = width
        self._head_at_end = head_at_end
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

    def clone(self):
        c = _ArrowItem(QColor(self._color), self._width, self._head_at_end)
        c.set_points(QPointF(self._p1), QPointF(self._p2))
        if self._ctrl1 is not None:
            c._ctrl1 = QPointF(self._ctrl1)
            c._ctrl2 = QPointF(self._ctrl2)
        return self._copy_common_to(c)

    # ---- 끝점(양끝 이동) 핸들 -------------------------------------------
    def _uses_endpoints(self):
        return True

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
            if self._bend_handle_rect(which).contains(local_pos):
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
        # bend 핸들은 크기조절·회전과 달리 arrow 도구에서도 활성 — 그리기 직후(자동 선택 상태)에
        # 도구 전환 없이 바로 곡선을 줄 수 있게. 크기조절·회전 핸들은 여전히 select 전용.
        if not self.isSelected():
            return False
        return self._owner_tool() in (None, "select", "arrow")

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

    def _head_points(self):
        """화살촉 삼각형 세 꼭짓점(tip + 뒤쪽 두 점)."""
        tip, angle = self._tip_and_angle()
        size = max(14, self._width * 3)
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
        if self._bend_active():   # 초록 bend 핸들도 잡을 수 있게
            for which in (1, 2):
                shape.addEllipse(self._bend_handle_rect(which))
        return shape

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        tail, tip = (self._p1, self._p2) if self._head_at_end else (self._p2, self._p1)
        length = math.hypot(tip.x() - tail.x(), tip.y() - tail.y())
        if self._ctrl1 is None and length < 1:
            return  # 클릭만 한 0길이 직선 화살표는 머리도 그리지 않음(깜빡임 방지)

        size = max(14, self._width * 3)
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
                hp.addEllipse(self._bend_handle_rect(which))
            return base.united(hp)
        return base

    def boundingRect(self) -> QRectF:
        # 실제로 칠하는 것(선택 외곽선=선두께+8, 초록 bend 핸들)이 _content_rect보다 살짝
        # 바깥으로 나가므로 boundingRect에 모두 포함한다 — 안 그러면 bend 드래그 때 무효화가
        # 누락돼 초록점 궤적 잔상이 남는다(다음 전체 리페인트 전까지).
        r = super().boundingRect()
        if self._bend_active():
            for which in (1, 2):
                r = r.united(self._bend_handle_rect(which))
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

def _shape_conn_points(item):
    """네모/원의 변 중앙 4점 → [(scene_point, scene_outward_unit_dir), ...]. 회전·스케일 반영.
    바깥 법선은 아이템 변환을 거쳐 씬 방향으로 환산(회전 화살표 진입/이탈 접선 계산에 씀)."""
    r = item.rect()
    cx, cy = r.center().x(), r.center().y()
    edges = (
        (QPointF(cx, r.top()),    QPointF(0.0, -1.0)),
        (QPointF(cx, r.bottom()), QPointF(0.0, 1.0)),
        (QPointF(r.left(), cy),   QPointF(-1.0, 0.0)),
        (QPointF(r.right(), cy),  QPointF(1.0, 0.0)),
    )
    out = []
    for pt, n in edges:
        sp = item.mapToScene(pt)
        nd = item.mapToScene(QPointF(pt.x() + n.x(), pt.y() + n.y())) - sp
        L = math.hypot(nd.x(), nd.y()) or 1.0
        out.append((sp, QPointF(nd.x() / L, nd.y() / L)))
    return out


class _AnnotatorView(QGraphicsView):
    _SHORTCUTS = {
        Qt.Key.Key_V: "select", Qt.Key.Key_R: "rect", Qt.Key.Key_E: "ellipse",
        Qt.Key.Key_L: "line", Qt.Key.Key_A: "arrow", Qt.Key.Key_P: "pen",
        Qt.Key.Key_T: "text", Qt.Key.Key_C: "badge",
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
        # 네모/원 테두리 연결점(호버 시 표시 → 드래그로 화살표 생성)
        self._conn_item = None       # 연결점을 표시 중인 도형(_RectItem/_EllipseItem)
        self._conn_pts: list = []    # [(scene_point, scene_outward_unit), ...]
        self._conn_hover_idx = -1    # 커서가 올라간 연결점(하이라이트·press 대상), -1=없음
        self._conn_drawing = False   # 연결점에서 화살표 드래그 중
        self._conn_exit = QPointF()  # 시작 접선(소스 변의 바깥 법선, 씬 단위)
        self._move_snap = None       # 드래그 이동 전 위치 스냅샷([(item, QPointF), ...]) — undo용
        # 연결점 dwell — 커서가 링에 잠깐 머물러야 표시(빠르게 지나가면 안 뜸 → 반짝임 제거)
        self._conn_dwell_item = None
        self._conn_dwell_pos = None
        self._conn_dwell_timer = QTimer(self)
        self._conn_dwell_timer.setSingleShot(True)
        self._conn_dwell_timer.timeout.connect(self._on_conn_dwell)

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
        호버 커서를 몸통(이동)과 구분하는 데 쓴다."""
        scene_pt = self.mapToScene(view_pos)
        for it in self.items(view_pos):
            if isinstance(it, _ArrowItem) and it._bend_active() \
                    and it._bend_handle_index_at(it.mapFromScene(scene_pt)):
                return it
        return None

    def _over_selected_endpoint(self, view_pos) -> bool:
        """커서가 '선택된' 선·화살표의 끝점 핸들 안이면 True(끝점 이동 우선 판정용)."""
        scene_pt = self.mapToScene(view_pos)
        for it in self.scene().selectedItems():
            uses = getattr(it, "_uses_endpoints", None)
            if uses and it._uses_endpoints() and it._endpoint_active():
                local = it.mapFromScene(scene_pt)
                for i in range(len(it._endpoints())):
                    if it._endpoint_rect(i).contains(local):
                        return True
        return False

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

    # ---- 테두리 연결점 (네모/원 → 자동 S자 화살표) --------------------------
    _CONN_SHOW_PX = 18.0   # 테두리 링 두께(안·밖 각 방향, 뷰 픽셀) — 이 안에서만 연결점 노출
    _CONN_HIT_PX = 12.0    # 연결점 하이라이트·press·스냅 대상 판정(뷰 픽셀)
    _CONN_DWELL_MS = 130   # 링 안에 이만큼 머물러야 표시(빠른 통과는 안 뜸)

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

    def _conn_candidate_at(self, view_pos):
        """커서가 어떤 네모/원의 '테두리 링' 안이면 (도형, 점목록, hover_idx), 아니면 (None,[],-1).

        표시 영역은 도형 '테두리를 감싸는 얇은 링'(안쪽 margin ~ 바깥쪽 margin)뿐이다 —
        채운 halo(내부까지)는 ① 테두리에 안 닿은 내부에서도 뜨고 ② 바깥으로 나가는 길에 반드시
        통과해 반짝였다. 링이라 둘레를 따라 움직여도 연속(개별 점 디스크 사이 빈틈 없음)이고
        속 빈 도형의 내부=빈 공간 설계와도 일치. 회전·스케일은 커서를 로컬로 변환해 반영."""
        if not self._owner.is_edit_mode() or self._conn_drawing:
            return None, [], -1
        scene_pt = self.mapToScene(view_pos)
        for it in self._conn_shapes():
            eff = self._view_scale() * (it.scale() or 1.0)
            margin = self._CONN_SHOW_PX / (eff if eff > 1e-6 else 1.0)
            cr = it._content_rect()
            lp = it.mapFromScene(scene_pt)
            outer = cr.adjusted(-margin, -margin, margin, margin)
            inner = cr.adjusted(margin, margin, -margin, -margin)  # 얇으면 음수→contains 항상 False
            if outer.contains(lp) and not inner.contains(lp):
                cand = _shape_conn_points(it)
                # HIT 판정: 개별 점 12px 이내면 그 점을 하이라이트·press 대상으로.
                near_i, near_d = -1, self._CONN_HIT_PX
                for i, (sp, _n) in enumerate(cand):
                    d = self._view_dist(sp, view_pos)
                    if d < near_d:
                        near_d, near_i = d, i
                return it, cand, near_i
        return None, [], -1

    def _set_conn_shown(self, item, pts, hover):
        changed = (item is not self._conn_item) or (hover != self._conn_hover_idx)
        self._conn_item, self._conn_pts, self._conn_hover_idx = item, pts, hover
        if changed:
            self.viewport().update()

    def _cancel_conn_dwell(self):
        self._conn_dwell_item = None
        self._conn_dwell_timer.stop()

    def _on_conn_dwell(self):
        # dwell 만료 — 커서가 후보 링에 계속 있으면(마우스 무브가 dwell 상태를 동기화하므로
        # 여기 도달했다면 아직 그 링 안) 그때 표시.
        item = self._conn_dwell_item
        self._conn_dwell_item = None
        if item is None or self._conn_dwell_pos is None:
            return
        cand, pts, hover = self._conn_candidate_at(self._conn_dwell_pos)
        if cand is item:
            self._set_conn_shown(item, pts, hover)

    def _update_conn_hover(self, view_pos):
        """커서 위치에 따라 연결점 표시를 갱신. 새 도형은 dwell(머무름) 후에만 표시."""
        cand, pts, hover = self._conn_candidate_at(view_pos)
        if cand is None:
            self._cancel_conn_dwell()
            self._set_conn_shown(None, [], -1)
            return
        if cand is self._conn_item:
            self._set_conn_shown(cand, pts, hover)  # 이미 표시 중 → 따라가기(hover 갱신)
            return
        # 미표시 새 후보 → dwell 대기(빠른 통과는 안 뜨고, 잠깐 머물러야 표시)
        self._conn_dwell_pos = view_pos
        if cand is not self._conn_dwell_item:
            self._conn_dwell_item = cand
            self._conn_dwell_timer.start(self._CONN_DWELL_MS)
            if self._conn_item is not None:      # 다른 도형이 떠 있었으면 즉시 숨김
                self._set_conn_shown(None, [], -1)

    def _begin_conn_arrow(self, scene_pt, exit_dir):
        owner = self._owner
        it = _ArrowItem(owner.current_color, owner.current_width, owner.arrow_head_at_end)
        it.set_points(scene_pt, scene_pt)
        self._start = scene_pt
        self._conn_exit = QPointF(exit_dir)
        self._conn_drawing = True
        self._cancel_conn_dwell()
        self._conn_item, self._conn_pts, self._conn_hover_idx = None, [], -1
        self.viewport().update()
        self._begin_draw(it)

    def _update_conn_arrow(self, view_pos):
        """연결점 화살표 드래그 갱신 — tip=커서(타깃 연결점 근처면 스냅) + 자동 S자 제어점.
        곡선 tip 접선: 스냅되면 그 변의 법선(수직 진입), 아니면 시작 접선과 평행(부드러운 S)."""
        it = self._temp
        tip = self.mapToScene(view_pos)
        ex, ey = self._conn_exit.x(), self._conn_exit.y()
        back = QPointF(-ex, -ey)  # tip→ctrl2 방향(=진행방향 반대). 기본은 시작과 평행하게 도착.
        for sh in self._conn_shapes():
            snapped = False
            for sp, nd in _shape_conn_points(sh):
                if self._view_dist(sp, view_pos) <= self._CONN_HIT_PX:
                    tip, back, snapped = sp, QPointF(nd), True  # 타깃 바깥 법선 쪽에 ctrl2(수직 도착)
                    break
            if snapped:
                break
        start = self._start
        dist = math.hypot(tip.x() - start.x(), tip.y() - start.y())
        it.prepareGeometryChange()
        it._p2 = QPointF(tip)
        if dist < 8:
            it._ctrl1 = it._ctrl2 = None  # 너무 짧으면 직선(엉킴 방지)
        else:
            k = max(30.0, min(dist * 0.5, 200.0))
            it._ctrl1 = QPointF(start.x() + ex * k, start.y() + ey * k)
            it._ctrl2 = QPointF(tip.x() + back.x() * k, tip.y() + back.y() * k)
        it.update()

    def leaveEvent(self, event):
        # 커서가 뷰를 벗어나면 연결점 표시·dwell 정리(잔상 방지).
        self._cancel_conn_dwell()
        if self._conn_item is not None:
            self._conn_item, self._conn_pts, self._conn_hover_idx = None, [], -1
            self.viewport().update()
        super().leaveEvent(event)

    def drawForeground(self, painter, rect):
        super().drawForeground(painter, rect)
        if self._conn_item is None or self._conn_drawing or not self._owner.is_edit_mode():
            return
        s = self._view_scale()
        base = 5.0 / s
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        for i, (sp, _n) in enumerate(self._conn_pts):
            hot = (i == self._conn_hover_idx)
            painter.setPen(QPen(QColor("white"), (2.0 if hot else 1.0) / s))
            painter.setBrush(QBrush(QColor(_BLUE)))
            r = base * (1.4 if hot else 1.0)
            painter.drawEllipse(sp, r, r)

    # ---- 줌 (휠) — 주석 위면 속성 변경, 아니면 owner의 hug-zoom(창이 이미지에 맞게) ----
    def wheelEvent(self, event):
        dy = event.angleDelta().y()
        if dy == 0:
            return
        # 편집 모드에서 커서 아래에 주석이 있으면 줌 대신 그 주석 속성을 조절
        # (도형=두께 / 텍스트·번호=크기). 없으면 기존대로 이미지 줌.
        if self._owner.is_edit_mode():
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
        # 이미 선택된 화살표/선의 끝점 핸들 위 press면 새 연결 화살표를 만들지 말고 그 끝점을
        # 이동(도형 테두리에 붙은 화살표 끝을 떼어내기). 연결점보다 끝점 이동을 우선 —
        # 안 그러면 끝점이 도형 연결점과 겹칠 때 매번 새 화살표가 생긴다.
        if self._over_selected_endpoint(event.position().toPoint()):
            return super().mousePressEvent(event)
        # 테두리 연결점 위에서 press → 현재 도구와 무관하게 자동 S자 화살표 그리기 시작.
        if self._conn_hover_idx >= 0 and self._conn_pts:
            sp, ndir = self._conn_pts[self._conn_hover_idx]
            self._begin_conn_arrow(sp, ndir)
            return
        tool = self._owner.current_tool
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
        pen = owner.make_pen()

        if tool == "rect":
            it = _RectItem(QRectF(sp, sp))
            it.setPen(pen)
            it.setBrush(QBrush(Qt.BrushStyle.NoBrush))
            self._begin_draw(it)
        elif tool == "ellipse":
            it = _EllipseItem(QRectF(sp, sp))
            it.setPen(pen)
            it.setBrush(QBrush(Qt.BrushStyle.NoBrush))
            self._begin_draw(it)
        elif tool == "line":
            it = _LineItem(QLineF(sp, sp))
            it.setPen(pen)
            self._begin_draw(it)
        elif tool == "arrow":
            it = _ArrowItem(owner.current_color, owner.current_width, owner.arrow_head_at_end)
            it.set_points(sp, sp)
            self._begin_draw(it)
        elif tool == "pen":
            self._path = QPainterPath(sp)
            it = _PathItem(self._path)
            it.setPen(pen)
            self._begin_draw(it)
        elif tool == "text":
            it = _TextItem(owner.current_color)
            it.apply_font_size(owner.current_font_size)
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
            it = _BadgeItem(owner.next_badge_number(), owner.current_color)
            it.setScale(owner.current_badge_size / float(_DEFAULT_BADGE))
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
        self._cancel_conn_dwell()
        if self._conn_item is not None:  # 그리기 시작하면 연결점 표시 정리
            self._conn_item, self._conn_pts, self._conn_hover_idx = None, [], -1
            self.viewport().update()

    def _update_hover_cursor(self, view_pos):
        """편집 모드 hover 커서: 주석 위=이동, 도형 도구+빈영역=십자, select+빈영역=손바닥."""
        vp = self.viewport()
        tool = self._owner.current_tool
        if self._bend_handle_at(view_pos) is not None:
            vp.setCursor(Qt.CursorShape.PointingHandCursor)  # 곡선 조절 손잡이(이동과 구분)
        elif self._conn_hover_idx >= 0:
            vp.setCursor(Qt.CursorShape.CrossCursor)          # 연결점 — 화살표 뽑기
        elif tool == "pen":
            vp.setCursor(Qt.CursorShape.CrossCursor)         # 펜 — 주석 위에서도 항상 그리기
        elif not self._is_empty_area(view_pos):
            vp.setCursor(Qt.CursorShape.SizeAllCursor)       # 주석 위 — 선택/이동
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
        if not self._owner.is_edit_mode():
            if event.buttons() & Qt.MouseButton.LeftButton:
                self._owner._win_drag_move(event.globalPosition().toPoint())
            else:
                self.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
            return
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            self._update_conn_hover(event.position().toPoint())
            self._update_hover_cursor(event.position().toPoint())
        if self._conn_drawing and self._temp is not None:
            self._update_conn_arrow(event.position().toPoint())
            return
        if self._drawing and self._temp is not None:
            sp = self._cur_point(event)
            tool = self._owner.current_tool
            if tool in ("rect", "ellipse"):
                self._temp.setRect(QRectF(self._start, sp).normalized())
            elif tool == "line":
                self._temp.setLine(QLineF(self._start, sp))
            elif tool == "arrow":
                self._temp.set_points(self._start, sp)
            elif tool == "pen" and self._path is not None:
                self._path.lineTo(sp)
                self._temp.setPath(self._path)
            return
        super().mouseMoveEvent(event)
        # 아이템 이동(좌드래그) 중엔 hover 재계산을 건너뛰므로(위 1707행) 캐시된 연결점이
        # 옛 위치에 남아 도형을 못 따라온다. 움직인 도형에서 실시간 재계산해 함께 따라오게 한다.
        if (event.buttons() & Qt.MouseButton.LeftButton) and self._conn_item is not None:
            self._conn_pts = _shape_conn_points(self._conn_item)
            self.viewport().update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton:
            self._owner._win_drag_end()
            self.viewport().unsetCursor()
            return
        if not self._owner.is_edit_mode():
            self._owner._win_drag_end()
            return
        if self._drawing and self._temp is not None:
            item = self._temp
            tool = self._owner.current_tool
            conn = self._conn_drawing
            self._drawing = False
            self._temp = None
            self._path = None
            self._conn_drawing = False
            # 드래그 없이 클릭만 한 경우 폐기. boundingRect는 펜 두께·화살촉만큼
            # 부풀어 클릭 판정에 못 쓰므로(특히 화살표), 시작점→놓은 점 이동량으로 본다.
            release = self.mapToScene(event.position().toPoint())
            moved = max(abs(release.x() - self._start.x()), abs(release.y() - self._start.y()))
            # 연결점 화살표는 도구가 select여도 이동량 기준으로 폐기(클릭만이면 무효).
            discard = moved < 4 if conn else (tool in _SHAPE_TOOLS and moved < 4)
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
                if conn or tool != "pen":
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
            if mods == Qt.KeyboardModifier.NoModifier and key in self._SHORTCUTS:
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


class _ColorPalettePopup(QWidget):
    """무지개 버튼 색 팔레트 팝업. 바깥 클릭 시 자동으로 닫히는 Qt.Popup이며,
    닫힌 시각(hidden_at)을 기록해 '버튼 재클릭=토글 off'를 안정적으로 구현하게 한다
    (팝업이 열린 상태로 버튼을 누르면 Popup이 먼저 닫히므로, 그 직후 재오픈을 막아야 함)."""

    def __init__(self, parent):
        super().__init__(parent, Qt.WindowType.Popup)
        self.hidden_at = 0.0

    def hideEvent(self, event):
        self.hidden_at = time.monotonic()
        super().hideEvent(event)


# ---------------------------------------------------------------------------
# 편집기 다이얼로그
# ---------------------------------------------------------------------------

def flatten_scene_to_png(scene: QGraphicsScene) -> bytes:
    """씬을 이미지 해상도 PNG bytes로 평탄화(주석 포함). 선택 핸들은 렌더 전 해제."""
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

    def _init_editor_state(self):
        self.current_tool = "select"
        self.current_color = QColor(_DEFAULT_COLOR)
        self.current_width = _DEFAULT_WIDTH
        self.current_font_size = _DEFAULT_FONT  # 새 텍스트의 기본 글자 크기(pt)
        self.current_badge_size = _DEFAULT_BADGE  # 새 번호 마커의 기본 지름(px)
        self.arrow_head_at_end = True
        self.current_text_bg = None  # 새 텍스트의 기본 배경(None=투명)
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
        self._tool_buttons: dict[str, QToolButton] = {}
        self._preset_buttons: list[tuple[QColor, QToolButton]] = []

    # ---- 툴바 / 액션바 (호스트가 배치) -------------------------------------
    def _build_toolbar(self) -> QHBoxLayout:
        tools = QHBoxLayout()
        tools.setContentsMargins(6, 2, 6, 2)
        tools.setSpacing(3)

        # 우측 배치는 호스트(chrome_l AlignRight)가 담당 — pill이 내용에 딱 맞게 hug하도록 stretch 없음.

        # 도구 (아이콘)
        group = QButtonGroup(self)
        group.setExclusive(True)
        for key, name, sc in _TOOLS:
            btn = QToolButton()
            btn.setIconSize(QSize(18, 18))
            btn.setCheckable(True)
            btn.setToolTip(f"{name} ({sc})")
            btn.clicked.connect(lambda _c, k=key: self.set_tool(k))
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

        # 색상: 무지개 버튼 1개 — 클릭하면 프리셋 7색 + 스포이드 팔레트 팝업(공간 절약).
        # 현재 색은 무지개 버튼 가운데 점으로 표시한다.
        self._color_palette = self._build_color_palette()
        self._color_btn = QToolButton()
        self._color_btn.setIcon(_rainbow_icon(self.current_color))
        self._color_btn.setIconSize(QSize(20, 20))
        self._color_btn.setToolTip("색 — 클릭하면 팔레트(프리셋·스포이드)")
        self._color_btn.clicked.connect(self._show_color_palette)
        tools.addWidget(self._color_btn)

        tools.addWidget(self._vsep())

        # 두께 조절은 주석 위에서 휠로 대체(adjust_item_property) — 별도 두께 위젯 제거.

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

        # 화살표 방향 토글 — 평소 숨김, 화살표 도구 활성 시 화살표 버튼 아래 floating
        self._arrow_dir_btn = QToolButton(self)
        self._arrow_dir_btn.setIcon(_arrow_dir_icon(self.arrow_head_at_end))
        self._arrow_dir_btn.setIconSize(QSize(24, 18))
        self._arrow_dir_btn.setToolTip("화살표 방향 바꾸기 (선택된 화살표도 뒤집음)")
        self._arrow_dir_btn.clicked.connect(self._toggle_arrow_dir)
        self._arrow_dir_btn.setVisible(False)

        # 텍스트 하위 옵션 바 — 텍스트 도구 활성 시 T 버튼 아래에 수평 floating.
        # (배경 스와치 직접 선택 + 글자 크기 스테퍼를 한 줄에)
        self._text_opts_bar = self._build_text_opts_bar()
        self._text_opts_bar.setVisible(False)

        # 번호 크기 스테퍼 — 번호(C) 도구 활성 시 C 버튼 아래 floating. 값이 유지돼 다음 번호도 같은 크기.
        self._badge_size_stepper = _SizeStepper(
            self.current_badge_size, _MIN_BADGE, _MAX_BADGE, "", "번호 크기 — 휠 또는 ▾ ▴ 클릭")
        self._badge_size_stepper.setParent(self)
        self._badge_size_stepper.changed.connect(lambda v: self._set_badge_size(v, from_stepper=True))
        self._badge_size_stepper.setVisible(False)
        return tools

    # ---- 색 팔레트 팝업 (무지개 버튼 클릭 시) -------------------------------
    def _build_color_palette(self) -> QWidget:
        """프리셋 7색 + 스포이드를 담은 팝업. 무지개 버튼 클릭 시 아래에 뜨고,
        Popup 플래그라 바깥을 클릭하면 자동으로 닫힌다."""
        pal = _ColorPalettePopup(self)
        pal.setObjectName("colorpalette")
        pal.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        pal.setStyleSheet(
            f"QWidget#colorpalette {{ background-color: {_SURFACE0};"
            f" border: 1px solid {_BORDER}; border-radius: 6px; }}"
            f"QToolButton {{ background-color: {_SURFACE0}; border: 1px solid {_BORDER};"
            f" border-radius: 4px; padding: 2px; }}"
            f"QToolButton:hover {{ background-color: {_SURFACE2}; }}"
        )
        row = QHBoxLayout(pal)
        row.setContentsMargins(6, 6, 6, 6)
        row.setSpacing(4)
        for hexs in _COLOR_PRESETS:
            color = QColor(hexs)
            btn = QToolButton()
            btn.setObjectName("swatch")
            btn.setFixedSize(20, 20)
            btn.setCheckable(True)
            btn.setToolTip(hexs)
            btn.setStyleSheet(self._swatch_style(color, False))
            btn.clicked.connect(lambda _c, cc=color: self._pick_palette_color(cc))
            row.addWidget(btn)
            self._preset_buttons.append((color, btn))
        self._eyedrop_btn = QToolButton()
        self._eyedrop_btn.setIcon(_tool_icon("eyedrop"))
        self._eyedrop_btn.setIconSize(QSize(18, 18))
        self._eyedrop_btn.setToolTip("스포이드 — 화면에서 색 따오기 (클릭으로 선택, ESC 취소)")
        self._eyedrop_btn.clicked.connect(self._pick_palette_eyedrop)
        row.addWidget(self._eyedrop_btn)
        pal.adjustSize()
        return pal

    def _show_color_palette(self):
        # 토글: 열려 있으면 닫는다. 또한 팝업이 열린 상태에서 버튼을 누르면 Qt.Popup이
        # 먼저 자동으로 닫히므로(hideEvent), 그 직후(<0.25s) 클릭은 재오픈하지 않아
        # '한 번 더 누르면 사라진다'가 성립한다.
        pal = self._color_palette
        if pal.isVisible():
            pal.hide()
            return
        if time.monotonic() - pal.hidden_at < 0.25:
            return
        pal.adjustSize()
        pos = self._color_btn.mapToGlobal(QPoint(0, self._color_btn.height() + 4))
        pal.move(pos)
        pal.show()
        pal.raise_()
        pal.activateWindow()

    def _pick_palette_color(self, color):
        self._set_color(color)
        self._color_palette.hide()

    def _pick_palette_eyedrop(self):
        self._color_palette.hide()
        self._start_eyedropper()

    def _update_color_btn(self):
        btn = getattr(self, "_color_btn", None)
        if btn is not None:
            btn.setIcon(_rainbow_icon(self.current_color))

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
        row.addWidget(self._vsep())
        # 글자 크기 스테퍼 — 같은 줄 오른쪽
        self._font_size_stepper = _SizeStepper(
            self.current_font_size, _MIN_FONT, _MAX_FONT, "pt", "글자 크기 — 휠 또는 ▾ ▴ 클릭")
        self._font_size_stepper.changed.connect(lambda s: self._set_font_size(s, from_stepper=True))
        row.addWidget(self._font_size_stepper)
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
    def set_tool(self, tool: str):
        self.current_tool = tool
        if tool == "select":
            self._view.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        else:
            self._view.setDragMode(QGraphicsView.DragMode.NoDrag)
        btn = self._tool_buttons.get(tool)
        if btn is not None and not btn.isChecked():
            btn.setChecked(True)
        # 도구 기본 커서 — hover 이벤트 전 stale 방지(주석 위 SizeAll은 다음 move에서 갱신)
        self._view.viewport().setCursor(
            Qt.CursorShape.ArrowCursor if tool == "select"
            else Qt.CursorShape.IBeamCursor if tool == "text"
            else Qt.CursorShape.CrossCursor
        )
        # 선택 항목 repaint — 핸들이 선택(V) 도구에서만 보이므로 도구 전환 시 즉시 반영
        for it in self._scene.selectedItems():
            it.update()
        self._update_arrow_dir_btn()
        self._update_text_opts_bar()
        self._update_badge_size_stepper()

    def _update_badge_size_stepper(self):
        """번호 도구가 활성이고 편집 모드일 때만 C 버튼 아래에 번호 크기 스테퍼 floating."""
        st = getattr(self, "_badge_size_stepper", None)
        if st is None:
            return
        edit = self.is_edit_mode() if hasattr(self, "is_edit_mode") else True
        if self.current_tool == "badge" and edit:
            c_btn = self._tool_buttons.get("badge")
            if c_btn is not None:
                st.move(c_btn.mapTo(self, QPoint(0, c_btn.height() + 2)))
            st.setVisible(True)
            st.raise_()
        else:
            st.setVisible(False)

    def _update_text_opts_bar(self):
        """텍스트 도구가 활성이고 편집 모드일 때만 T 버튼 아래에 텍스트 옵션 바 floating."""
        bar = getattr(self, "_text_opts_bar", None)
        if bar is None:
            return
        edit = self.is_edit_mode() if hasattr(self, "is_edit_mode") else True
        if self.current_tool == "text" and edit:
            text_btn = self._tool_buttons.get("text")
            if text_btn is not None:
                bar.adjustSize()
                bar.move(text_btn.mapTo(self, QPoint(0, text_btn.height() + 2)))
            bar.setVisible(True)
            bar.raise_()
        else:
            bar.setVisible(False)

    def _set_text_bg(self, bg):
        self.current_text_bg = QColor(bg) if bg is not None else None
        self._sync_bg_buttons()
        # 선택된 텍스트 항목에도 즉시 적용
        for it in self._scene.selectedItems():
            if isinstance(it, _TextItem):
                it.set_bg(self.current_text_bg)

    def _sync_bg_buttons(self):
        """현재 배경(current_text_bg)에 해당하는 스와치만 체크 표시."""
        cur = self.current_text_bg
        for bg, btn in getattr(self, "_bg_buttons", []):
            same = (bg is None and cur is None) or (
                bg is not None and cur is not None and QColor(bg) == QColor(cur))
            btn.setChecked(same)

    def _update_arrow_dir_btn(self):
        """방향 토글 버튼 배치: 선택된 화살표가 있으면 그 화살표 근처에(대상에서 멀지 않게),
        없고 화살표 도구가 활성이면 툴바 화살표 버튼 아래(새 화살표 기본 방향 토글)."""
        btn = getattr(self, "_arrow_dir_btn", None)
        if btn is None:
            return
        edit = self.is_edit_mode() if hasattr(self, "is_edit_mode") else True
        if not edit:
            btn.setVisible(False)
            return
        sel_arrows = [it for it in self._scene.selectedItems() if isinstance(it, _ArrowItem)]
        if sel_arrows:
            arrow = sel_arrows[0]
            # 화살표 중간점을 호스트(창) 좌표로 변환해 그 위쪽에 버튼 배치(대상 근처).
            scene_mid = arrow.mapToScene(arrow._point_at(0.5))
            vp_pt = self._view.mapFromScene(scene_mid)
            host = self.mapFromGlobal(self._view.viewport().mapToGlobal(vp_pt))
            btn.setIcon(_arrow_dir_icon(arrow._head_at_end))  # 그 화살표의 실제 방향 표시
            btn.resize(32, 24)
            x = max(2, min(host.x() + 12, self.width() - btn.width() - 2))
            y = max(2, min(host.y() - btn.height() - 12, self.height() - btn.height() - 2))
            btn.move(x, y)
            btn.setVisible(True)
            btn.raise_()
            return
        if self.current_tool == "arrow":
            arrow_btn = self._tool_buttons.get("arrow")
            if arrow_btn is not None:
                top_left = arrow_btn.mapTo(self, QPoint(0, arrow_btn.height() + 2))
                btn.move(top_left)
                btn.resize(arrow_btn.width(), 22)
            btn.setIcon(_arrow_dir_icon(self.arrow_head_at_end))
            btn.setVisible(True)
            btn.raise_()
            return
        btn.setVisible(False)

    def make_pen(self) -> QPen:
        return QPen(self.current_color, self.current_width, Qt.PenStyle.SolidLine,
                    Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)

    def next_badge_number(self) -> int:
        # 씬에 남은 번호 마커의 최대값+1 (삭제 후 재생성 시 빈 번호를 다시 씀)
        nums = [it._number for it in self._scene.items() if isinstance(it, _BadgeItem)]
        return max(nums, default=0) + 1

    def _refresh_tool_icons(self):
        for key, btn in self._tool_buttons.items():
            btn.setIcon(_tool_icon(key, self.current_color, neutral_override=_ICON_DARK))

    def _set_color(self, color: QColor):
        self.current_color = QColor(color)
        # 현재 색은 무지개 버튼 가운데 점으로 표시(팔레트 팝업 진입점)
        self._update_color_btn()
        name = self.current_color.name().lower()
        for c, btn in self._preset_buttons:
            sel = c.name().lower() == name
            btn.setChecked(sel)
            btn.setStyleSheet(self._swatch_style(c, sel))
        self._refresh_tool_icons()
        # 선택된 도형이 있으면 그 색도 즉시 변경
        for it in self._scene.selectedItems():
            if hasattr(it, "apply_color"):
                it.apply_color(self.current_color)

    def _set_font_size(self, size: int, from_stepper: bool = False):
        self.current_font_size = max(_MIN_FONT, min(int(size), _MAX_FONT))
        if not from_stepper:
            self._font_size_stepper.set_value(self.current_font_size)
        # 편집 중인 텍스트가 있으면 그 텍스트만, 없으면 선택된 텍스트들에 적용
        for it in self._font_size_targets():
            it.apply_font_size(self.current_font_size)

    def _font_size_targets(self) -> list:
        fi = self._scene.focusItem()
        if isinstance(fi, _TextItem) and \
                fi.textInteractionFlags() != Qt.TextInteractionFlag.NoTextInteraction:
            return [fi]  # 작성 중인 텍스트만 — 기존 선택 텍스트가 같이 커지지 않게
        return [it for it in self._scene.selectedItems() if isinstance(it, _TextItem)]

    def _set_badge_size(self, size: int, from_stepper: bool = False):
        self.current_badge_size = max(_MIN_BADGE, min(int(size), _MAX_BADGE))
        if not from_stepper:
            self._badge_size_stepper.set_value(self.current_badge_size)
        scale = self.current_badge_size / float(_DEFAULT_BADGE)
        # 선택된 번호 마커가 있으면 그 크기도 즉시 변경
        for it in self._scene.selectedItems():
            if isinstance(it, _BadgeItem):
                it.setScale(scale)

    def adjust_item_property(self, item, step: int):
        """주석 위 휠 — 도형은 두께(±1), 텍스트·번호는 크기(±2)를 step 방향으로 조절.
        조절값을 도구 기본값·툴바에도 반영해 다음에 그리는 주석도 같은 두께·크기가 되게 한다
        (undo는 색·두께 변경과 동일하게 미추적)."""
        if isinstance(item, _TextItem):
            new = max(_MIN_FONT, min(item.font().pointSize() + step * 2, _MAX_FONT))
            item.apply_font_size(new)
            self.current_font_size = new
            self._font_size_stepper.set_value(new)
        elif isinstance(item, _BadgeItem):
            cur = round(item.scale() * _DEFAULT_BADGE)
            new = max(_MIN_BADGE, min(cur + step * 2, _MAX_BADGE))
            item.setScale(new / float(_DEFAULT_BADGE))
            self.current_badge_size = new
            self._badge_size_stepper.set_value(new)
        else:
            if isinstance(item, _ArrowItem):
                new = max(_MIN_WIDTH, min(item._width + step, _MAX_WIDTH))
            elif hasattr(item, "apply_width") and hasattr(item, "pen"):
                new = max(_MIN_WIDTH, min(int(round(item.pen().widthF())) + step, _MAX_WIDTH))
            else:
                return
            item.apply_width(new)
            self.current_width = new

    def _toggle_arrow_dir(self):
        # 선택된 화살표가 있으면 각자 자기 방향을 뒤집고 기본값·아이콘을 첫 화살표에 맞춘다.
        # 없으면 새 화살표 기본 방향만 토글.
        sel = [it for it in self._scene.selectedItems() if isinstance(it, _ArrowItem)]
        if sel:
            for it in sel:
                it.flip_head()
            self.arrow_head_at_end = sel[0]._head_at_end
        else:
            self.arrow_head_at_end = not self.arrow_head_at_end
        self._arrow_dir_btn.setIcon(_arrow_dir_icon(self.arrow_head_at_end))

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
        if picked and self._eyedrop_last is not None:
            self._set_color(self._eyedrop_last)
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
