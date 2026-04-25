"""시스템 트레이 — 최소 구현 (Phase 1)"""
from PyQt6.QtWidgets import QSystemTrayIcon, QMenu
from PyQt6.QtGui import QIcon, QPixmap, QPainter, QColor
from PyQt6.QtCore import pyqtSignal, QObject
from pasteflow.__version__ import __version__
from pasteflow.ui.theme import TEAL, BASE, SURFACE0, SURFACE1, TEXT


def _create_default_icon() -> QIcon:
    """기본 트레이 아이콘 생성 (16x16 teal 사각형)"""
    pixmap = QPixmap(16, 16)
    pixmap.fill(QColor("transparent"))
    painter = QPainter(pixmap)
    painter.setBrush(QColor(TEAL))
    painter.setPen(QColor(BASE))
    painter.drawRoundedRect(1, 1, 14, 14, 3, 3)
    painter.drawText(3, 12, "P")
    painter.end()
    return QIcon(pixmap)


class TrayIcon(QObject):
    """시스템 트레이 아이콘"""

    panel_toggle_requested = pyqtSignal()
    settings_requested = pyqtSignal()
    quit_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tray = QSystemTrayIcon(_create_default_icon())
        self._tray.setToolTip(f"PasteFlow v{__version__}")
        self._setup_menu()
        self._tray.activated.connect(self._on_activated)

    def _setup_menu(self):
        """우클릭 메뉴"""
        self._menu = menu = QMenu()
        menu.setStyleSheet(f"""
            QMenu {{
                background-color: {SURFACE0};
                color: {TEXT};
                border: 1px solid {SURFACE1};
            }}
            QMenu::item:selected {{
                background-color: {SURFACE1};
            }}
        """)
        menu.addAction("📋 패널 열기", self.panel_toggle_requested.emit)
        menu.addSeparator()
        menu.addAction("⚙️ 설정", self.settings_requested.emit)
        menu.addAction("❌ 종료", self.quit_requested.emit)
        self._tray.setContextMenu(menu)

    def _on_activated(self, reason):
        """좌클릭 → 패널 토글"""
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.panel_toggle_requested.emit()

    def update_queue_status(self, pointer: int, total: int):
        """트레이 툴팁에 큐 상태 표시"""
        if total > 0:
            remaining = total - pointer
            self._tray.setToolTip(f"PasteFlow  |  큐 {pointer}/{total}  ({remaining}개 남음)")
        else:
            self._tray.setToolTip(f"PasteFlow v{__version__}")

    def show(self):
        self._tray.show()

    def hide(self):
        self._tray.hide()
