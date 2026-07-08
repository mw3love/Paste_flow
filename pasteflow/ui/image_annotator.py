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
    PEACH as _PEACH,
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
    # 핸들 크기는 주석의 표시 크기에 비례(작은 변 기준)하되 씬 단위로 클램프한다 —
    # 작은 주석에서 핸들이 상대적으로 거대해 보이던 문제 해결. 너무 작아 못 잡는 일은
    # 하한으로, 우스꽝스럽게 커지는 일은 상한으로 막는다.
    _HANDLE_FRAC = 0.22  # 작은 변 대비 핸들 비율
    _HANDLE_MIN = 5.0    # 씬 단위 하한(항상 잡히게)
    _HANDLE_MAX = 12.0   # 씬 단위 상한
    _ROT_GAP = 14.0  # 도형 윗변 ~ 회전 원 사이 빈 줄기(씬 단위, 원 크기와 무관하게 일정)
    _EDGE_HIT_MIN = 8.0  # 속 빈 도형 테두리 클릭 최소 히트폭(씬 단위) — 얇은 선도 잡히게

    def _handle_px(self) -> float:
        """핸들 한 변(로컬 단위). 주석 표시 크기에 비례 + [MIN,MAX] 클램프."""
        s = self._scale_or_1()
        cr = self._content_rect()
        scene_dim = min(cr.width(), cr.height()) * s  # 주석 작은 변(씬 단위)
        h_scene = max(self._HANDLE_MIN, min(scene_dim * self._HANDLE_FRAC, self._HANDLE_MAX))
        return h_scene / s

    def _init_resize(self):
        self._resizing = False
        self._rotating = False
        self._press_scale = 1.0
        self._press_dist = 1.0
        self._press_rot = 0.0
        self._press_angle = 0.0

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

    def _paint_base_no_select(self, painter, option, widget):
        # Qt 기본 paint는 선택 시 (회전 핸들까지 확장된) boundingRect 둘레에 점선을 자동으로
        # 그려 위쪽으로 점선이 딸려 올라간다. State_Selected를 꺼서 그 자동 점선을 막고,
        # 선택박스는 _content_rect에만 직접 그린다(arrow/badge와 동일하게 타이트하게).
        opt = QStyleOptionGraphicsItem(option)
        opt.state &= ~QStyle.StateFlag.State_Selected
        super().paint(painter, opt, widget)
        if self.isSelected():
            _draw_selection_box(painter, self._content_rect(), self._scale_or_1())

    def shape(self):
        # 선택 시 핸들 영역을 클릭 영역에 포함 — 속 빈 도형도 핸들을 잡을 수 있게.
        base = self._base_shape()
        if self._handle_active():
            hp = QPainterPath()
            hp.addRect(self._handle_local_rect())
            hp.addEllipse(self._rot_handle_rect())
            return base.united(hp)
        return base

    def mousePressEvent(self, event):
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
        self._paint_base_no_select(painter, option, widget)
        self._paint_handle(painter)


class _LineItem(_HandleResizeMixin, QGraphicsLineItem):
    def __init__(self, *args):
        super().__init__(*args)
        self._init_resize()

    def clone(self):
        c = _LineItem(QLineF(self.line()))
        c.setPen(QPen(self.pen()))
        return self._copy_common_to(c)

    def _content_rect(self):
        # Qt 기본 QGraphicsLineItem.boundingRect()는 펜 두께가 0이 아니면 내부적으로
        # shape()를 호출하는데, 믹스인 shape()가 핸들 계산에 다시 boundingRect()를 부르므로
        # 무한 재귀(스택 오버플로 → 프로세스 abort)가 된다. 선 기하에서 직접 계산해 사이클을 끊는다.
        line = self.line()
        extra = self.pen().widthF() / 2.0 + 1.0
        return QRectF(line.p1(), line.p2()).normalized().adjusted(-extra, -extra, extra, extra)

    def paint(self, painter, option, widget=None):
        self._paint_base_no_select(painter, option, widget)
        self._paint_handle(painter)


class _PathItem(_HandleResizeMixin, QGraphicsPathItem):
    def __init__(self, *args):
        super().__init__(*args)
        self._init_resize()

    def clone(self):
        c = _PathItem(QPainterPath(self.path()))
        c.setPen(QPen(self.pen()))
        return self._copy_common_to(c)

    def _content_rect(self):
        # _LineItem과 동일 사이클 방지: QGraphicsPathItem.boundingRect()는 brush가 NoBrush일 때
        # shape()를 호출하므로, 패스 기하에서 직접 계산해 믹스인 shape()와의 재귀를 끊는다.
        extra = self.pen().widthF() / 2.0 + 1.0
        return self.path().boundingRect().adjusted(-extra, -extra, extra, extra)

    def paint(self, painter, option, widget=None):
        self._paint_base_no_select(painter, option, widget)
        self._paint_handle(painter)


class _ArrowItem(_HandleResizeMixin, QGraphicsItem):
    """선 + 끝점 삼각형 화살촉. 머리 방향(head_at_end) 선택 가능."""

    def __init__(self, color: QColor, width: int, head_at_end: bool = True):
        super().__init__()
        self._p1 = QPointF(0, 0)
        self._p2 = QPointF(0, 0)
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
        return self._copy_common_to(c)

    def _content_rect(self) -> QRectF:
        extra = self._width + max(14, self._width * 3) + 4
        return QRectF(self._p1, self._p2).normalized().adjusted(-extra, -extra, extra, extra)

    def _base_shape(self):
        # QGraphicsItem 기본 shape는 boundingRect(회전 여유 포함) 기반 → content로 한정해
        # 화살표 위쪽 빈 공간이 클릭 영역에 새지 않게 한다.
        p = QPainterPath()
        p.addRect(self._content_rect())
        return p

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        tail, tip = (self._p1, self._p2) if self._head_at_end else (self._p2, self._p1)
        length = math.hypot(tip.x() - tail.x(), tip.y() - tail.y())
        if length < 1:
            return  # 클릭만 한 0길이 화살표는 머리도 그리지 않음(깜빡임 방지)

        size = max(14, self._width * 3)
        angle = math.atan2(tip.y() - tail.y(), tip.x() - tail.x())
        # 선은 화살촉 밑변까지만 그린다. 짧은 화살표에서 base가 tail 뒤로 넘어가
        # 선이 거꾸로 삐져나오지 않도록 tail~tip 구간 안으로 클램프한다.
        t = max(0.0, 1.0 - (size * 0.85) / length) if length > 1 else 0.0
        base = QPointF(tail.x() + (tip.x() - tail.x()) * t,
                       tail.y() + (tip.y() - tail.y()) * t)
        pen = QPen(self._color, self._width, Qt.PenStyle.SolidLine,
                   Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawLine(tail, base)

        a1 = angle + math.radians(150)
        a2 = angle - math.radians(150)
        head = QPolygonF([
            tip,
            QPointF(tip.x() + size * math.cos(a1), tip.y() + size * math.sin(a1)),
            QPointF(tip.x() + size * math.cos(a2), tip.y() + size * math.sin(a2)),
        ])
        painter.setBrush(QBrush(self._color))
        painter.setPen(QPen(self._color, 1, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawPolygon(head)
        if self.isSelected():
            _draw_selection_box(painter, self._content_rect(), self._scale_or_1())
        self._paint_handle(painter)


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
        # Ctrl+Enter로 편집 종료(평범한 Enter는 줄바꿈 유지). clearFocus → focusOut에서 정리.
        if (event.modifiers() & Qt.KeyboardModifier.ControlModifier) and \
                event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.clearFocus()
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

    def _is_empty_area(self, view_pos) -> bool:
        """클릭 위치에 선택 가능한 주석 아이템이 없으면(배경뿐) True."""
        for it in self.items(view_pos):
            if it is getattr(self._owner, "_bg_item", None):
                continue
            if it.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsSelectable:
                return False
        return True

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
        tool = self._owner.current_tool
        if tool == "select":
            # Qt 기본: 빈 영역 드래그 = 러버밴드 다중선택, 아이템 위 = 이동/선택.
            # 창 이동은 상단 코랄 드래그바로. (편집 모드 본문 pan은 제거)
            return super().mousePressEvent(event)

        # 도형 도구는 기존 주석 위를 클릭하면 그리기 대신 선택/이동.
        # 단, 펜은 빽빽이 겹쳐 그리므로 항상 그린다(펜 선의 선택/이동은 V 도구로).
        if tool != "pen" and not self._is_empty_area(event.position().toPoint()):
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

    def _update_hover_cursor(self, view_pos):
        """편집 모드 hover 커서: 주석 위=이동, 도형 도구+빈영역=십자, select+빈영역=손바닥."""
        vp = self.viewport()
        tool = self._owner.current_tool
        if tool == "pen":
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
            self._update_hover_cursor(event.position().toPoint())
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
            self._drawing = False
            self._temp = None
            self._path = None
            # 드래그 없이 클릭만 한 경우 폐기. boundingRect는 펜 두께·화살촉만큼
            # 부풀어 클릭 판정에 못 쓰므로(특히 화살표), 시작점→놓은 점 이동량으로 본다.
            release = self.mapToScene(event.position().toPoint())
            moved = max(abs(release.x() - self._start.x()), abs(release.y() - self._start.y()))
            if tool in _SHAPE_TOOLS and moved < 4:
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
        """화살표 도구가 활성이고 편집 모드일 때만 화살표 버튼 아래에 방향 토글 floating."""
        btn = getattr(self, "_arrow_dir_btn", None)
        if btn is None:
            return
        edit = self.is_edit_mode() if hasattr(self, "is_edit_mode") else True
        if self.current_tool == "arrow" and edit:
            arrow_btn = self._tool_buttons.get("arrow")
            if arrow_btn is not None:
                # 화살표 버튼의 좌상단을 호스트 창 좌표로 변환해 그 아래에 배치
                top_left = arrow_btn.mapTo(self, QPoint(0, arrow_btn.height() + 2))
                btn.move(top_left)
                btn.resize(arrow_btn.width(), 22)
            btn.setVisible(True)
            btn.raise_()
        else:
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
        self.arrow_head_at_end = not self.arrow_head_at_end
        self._arrow_dir_btn.setIcon(_arrow_dir_icon(self.arrow_head_at_end))
        # 선택된 화살표도 즉시 뒤집기
        for it in self._scene.selectedItems():
            if isinstance(it, _ArrowItem):
                it.set_head_at_end(self.arrow_head_at_end)

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

    def undo(self):
        # 이미 사라진 빈 텍스트의 "add"처럼 무의미한 항목은 건너뛰고 실제 동작 1건을 되돌린다.
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
