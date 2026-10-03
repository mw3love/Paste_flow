"""우클릭 메뉴 공통 스타일 — 히스토리·미리보기·핀·텍스트 미리보기·패널 빈 곳 메뉴가 공유.

예전엔 메뉴마다 QSS를 따로 들고 있어 모서리(8px/6px)·호버 색이 서로 달랐다(2026-10-03 통일).
`make_menu()`로 만들고 `menu.add(이름, 아이콘, danger=...)`로 항목을 넣는다.
아이콘(Phosphor 듀오톤)·위험 항목 호버 빨강은 2026-10-03 메뉴 베이크오프 A2+B3 채택안.

⚠ 메뉴는 QSS 없이 `_MenuProxyStyle`이 틀·항목을 전부 직접 그린다 — 단축키 글자
(`"복사\tCtrl+C"`의 탭 뒤)만 흐리게 하려면 이름과 따로 그려야 하는데, 메뉴에 걸리는 QSS 규칙이
하나라도 있으면(메뉴 자신의 것이든, 부모 패널의 선택자 없는 규칙이 내려온 것이든) Qt 스타일시트
엔진이 항목을 자기가 그리고 프록시의 drawControl을 아예 부르지 않는다(실측). 그래서 메뉴는
부모 없이 만든다. 앱 전역 QSS(툴팁 규칙뿐)는 메뉴에 해당하는 규칙이 없어 그대로 프록시로 넘어온다.
"""

from PyQt6.QtCore import Qt, QRect, QRectF, QSize
from PyQt6.QtGui import QAction, QColor
from PyQt6.QtWidgets import QMenu, QProxyStyle, QStyle, QStyleOptionMenuItem

from pasteflow.ui.menu_icons import ICON_SIZE as _ICON, menu_icon
from pasteflow.ui.theme import COLORS

_PAD_H = 16        # 항목 좌우 안쪽 여백
_PAD_V = 6         # 항목 위아래 안쪽 여백
_SHORTCUT_GAP = 32  # 이름과 단축키 사이 최소 간격
_ICON_GAP = 10     # 아이콘과 이름 사이


_DANGER_HOVER_BG = "#4a2530"  # 위험 항목 호버 배경 — 빨강을 어두운 면에 옅게 섞음
_DANGER_PROP = "pf_danger"     # QAction 프로퍼티: 위험 항목(삭제 등) 표시

_RADIUS = 8        # 메뉴 틀 모서리
_MARGIN = 4        # 틀 안쪽 여백(테두리 1px 포함)


class _MenuProxyStyle(QProxyStyle):
    """메뉴 항목을 직접 그린다 — 이름 > 단축키 위계(단축키는 흐린 색)."""

    def __init__(self):
        super().__init__("Fusion")  # 기반 스타일을 고정해 PC마다 행 높이·여백이 달라지지 않게

    def pixelMetric(self, metric, opt=None, widget=None):
        if metric in (QStyle.PixelMetric.PM_MenuPanelWidth,):
            return 1
        if metric in (QStyle.PixelMetric.PM_MenuHMargin, QStyle.PixelMetric.PM_MenuVMargin):
            return _MARGIN - 1
        return super().pixelMetric(metric, opt, widget)

    def drawPrimitive(self, element, opt, p, widget=None):
        if element in (QStyle.PrimitiveElement.PE_PanelMenu, QStyle.PrimitiveElement.PE_FrameMenu):
            if element == QStyle.PrimitiveElement.PE_PanelMenu:
                p.save()
                p.setRenderHint(p.RenderHint.Antialiasing)
                p.setPen(QColor(COLORS['surface1']))
                p.setBrush(QColor(COLORS['surface0']))
                p.drawRoundedRect(QRectF(opt.rect).adjusted(0.5, 0.5, -0.5, -0.5), _RADIUS, _RADIUS)
                p.restore()
            return
        super().drawPrimitive(element, opt, p, widget)

    def sizeFromContents(self, ct, opt, size, widget=None):
        if ct == QStyle.ContentsType.CT_MenuItem and isinstance(opt, QStyleOptionMenuItem):
            if opt.menuItemType == QStyleOptionMenuItem.MenuItemType.Separator:
                return QSize(size.width(), 9)
            fm = opt.fontMetrics
            label = opt.text.split("\t", 1)[0]
            w = _PAD_H * 2 + fm.horizontalAdvance(label)
            if opt.maxIconWidth > 0:
                w += _ICON + _ICON_GAP
            if opt.reservedShortcutWidth > 0:
                w += _SHORTCUT_GAP + opt.reservedShortcutWidth
            h = max(fm.height(), _ICON) + _PAD_V * 2
            return QSize(w, h)
        return super().sizeFromContents(ct, opt, size, widget)

    def drawControl(self, element, opt, p, widget=None):
        if element != QStyle.ControlElement.CE_MenuItem or not isinstance(opt, QStyleOptionMenuItem):
            return super().drawControl(element, opt, p, widget)
        r = opt.rect
        p.save()
        if opt.menuItemType == QStyleOptionMenuItem.MenuItemType.Separator:
            y = r.center().y()
            p.setPen(QColor(COLORS['surface1']))
            p.drawLine(r.left() + 8, y, r.right() - 8, y)
            p.restore()
            return
        enabled = bool(opt.state & QStyle.StateFlag.State_Enabled)
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        # 위험 항목은 평소엔 다른 항목과 같고, 마우스를 올렸을 때만 빨강(강조색은 의미 있는 순간에만).
        action = widget.actionAt(r.center()) if isinstance(widget, QMenu) else None
        danger_hover = bool(selected and enabled and action is not None and action.property(_DANGER_PROP))
        p.setRenderHint(p.RenderHint.Antialiasing)
        if selected and enabled:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(_DANGER_HOVER_BG if danger_hover else COLORS['surface1']))
            p.drawRoundedRect(r, 4, 4)
        x = r.left() + _PAD_H
        if opt.maxIconWidth > 0:
            if not opt.icon.isNull():
                mode = opt.icon.Mode.Normal if enabled else opt.icon.Mode.Disabled
                pm = opt.icon.pixmap(QSize(_ICON, _ICON), mode)
                p.drawPixmap(x, r.center().y() - _ICON // 2 + 1, pm)
            x += _ICON + _ICON_GAP
        label, _, shortcut = opt.text.partition("\t")
        align = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        if danger_hover:
            p.setPen(QColor(COLORS['red']))
        else:
            p.setPen(QColor(COLORS['text'] if enabled else COLORS['overlay0']))
        p.drawText(QRect(x, r.top(), r.right() - x, r.height()), align, label)
        if shortcut:
            p.setPen(QColor(COLORS['overlay0'] if enabled else COLORS['surface2']))
            sx = r.right() - _PAD_H - opt.reservedShortcutWidth
            p.drawText(QRect(sx, r.top(), opt.reservedShortcutWidth + _PAD_H, r.height()), align, shortcut)
        p.restore()


class Menu(QMenu):
    def add(self, text: str, icon: str | None = None, danger: bool = False) -> QAction:
        """항목 추가. text의 탭 뒤는 단축키 힌트. icon은 menu_icons의 이름."""
        action = self.addAction(text)
        if icon:
            action.setIcon(menu_icon(icon, COLORS['subtext0'], COLORS['surface2']))
        if danger:
            action.setProperty(_DANGER_PROP, True)
        return action


_proxy_style: "_MenuProxyStyle | None" = None


def make_menu() -> Menu:
    """공통 스타일이 적용된 메뉴."""
    global _proxy_style
    if _proxy_style is None:
        _proxy_style = _MenuProxyStyle()
    menu = Menu()  # parent 없이 — 위 모듈 설명(부모 QSS 상속 차단) 참고
    menu.setStyle(_proxy_style)
    # 둥근 모서리 바깥을 투명하게. 그림자는 Windows가 사각형으로 그려 모서리와 어긋나므로 끈다.
    menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    menu.setWindowFlags(menu.windowFlags() | Qt.WindowType.FramelessWindowHint
                        | Qt.WindowType.NoDropShadowWindowHint)
    return menu
