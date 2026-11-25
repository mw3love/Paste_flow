"""주석 편집기 툴바·옵션창 아이콘 — Phosphor Icons (MIT, https://phosphoricons.com).

menu_icons.py와 같은 방식: SVG를 문자열로 내장한다(파일로 두면 PyInstaller spec의 datas에
따로 넣어야 하고, 빠뜨리면 exe에서만 아이콘이 사라진다). `currentColor`를 원하는 색으로 바꿔 그린다.
2026-11-26 툴바 아이콘 시안 라운드2(docs/design.md 「주석 편집기 툴바 아이콘」)에서 고른 넷.
"""
from __future__ import annotations

from PyQt6.QtCore import QByteArray, Qt
from PyQt6.QtGui import QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication

from pasteflow.ui.menu_icons import _SVGS as _MENU_SVGS

_SVGS: dict[str, str] = {
    # 새 칠판 — trash 듀오톤(메뉴 아이콘과 같은 그림이라 그대로 가져다 쓴다)
    'trash': _MENU_SVGS['trash'],
    # 스포이드 — eyedropper 듀오톤
    'eyedropper': '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" fill="currentColor"><path d="M207.8,87.6l-25.37,25.53,4.89,4.88a16,16,0,0,1,0,22.64l-9,9a8,8,0,0,1-11.32,0l-60.68-60.7a8,8,0,0,1,0-11.32l9-9a16,16,0,0,1,22.63,0l4.88,4.89,25-25.11c10.79-10.79,28.37-11.45,39.45-1A28,28,0,0,1,207.8,87.6Z" opacity="0.2"/><path d="M224,67.3a35.79,35.79,0,0,0-11.26-25.66c-14-13.28-36.72-12.78-50.62,1.13L142.8,62.2a24,24,0,0,0-33.14.77l-9,9a16,16,0,0,0,0,22.64l2,2.06-51,51a39.75,39.75,0,0,0-10.53,38l-8,18.41A13.68,13.68,0,0,0,36,219.3a15.92,15.92,0,0,0,17.71,3.35L71.23,215a39.89,39.89,0,0,0,37.06-10.75l51-51,2.06,2.06a16,16,0,0,0,22.62,0l9-9a24,24,0,0,0,.74-33.18l19.75-19.87A35.75,35.75,0,0,0,224,67.3ZM97,193a24,24,0,0,1-24,6,8,8,0,0,0-5.55.31l-18.1,7.91L57,189.41a8,8,0,0,0,.25-5.75A23.88,23.88,0,0,1,63,159l51-51,33.94,34ZM202.13,82l-25.37,25.52a8,8,0,0,0,0,11.3l4.89,4.89a8,8,0,0,1,0,11.32l-9,9L112,83.26l9-9a8,8,0,0,1,11.31,0l4.89,4.89a8,8,0,0,0,5.65,2.34h0a8,8,0,0,0,5.66-2.36l24.94-25.09c7.81-7.82,20.5-8.18,28.29-.81a20,20,0,0,1,.39,28.7Z"/></svg>',
    # 화살표 방향 뒤집기 — arrows-left-right(선)
    'arrows-left-right': '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" fill="currentColor"><path d="M213.66,181.66l-32,32a8,8,0,0,1-11.32-11.32L188.69,184H48a8,8,0,0,1,0-16H188.69l-18.35-18.34a8,8,0,0,1,11.32-11.32l32,32A8,8,0,0,1,213.66,181.66Zm-139.32-64a8,8,0,0,0,11.32-11.32L67.31,88H208a8,8,0,0,0,0-16H67.31L85.66,53.66A8,8,0,0,0,74.34,42.34l-32,32a8,8,0,0,0,0,11.32Z"/></svg>',
    # 양쪽 머리 — arrows-horizontal(선)
    'arrows-horizontal': '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" fill="currentColor"><path d="M237.66,133.66l-32,32a8,8,0,0,1-11.32-11.32L212.69,136H43.31l18.35,18.34a8,8,0,0,1-11.32,11.32l-32-32a8,8,0,0,1,0-11.32l32-32a8,8,0,0,1,11.32,11.32L43.31,120H212.69l-18.35-18.34a8,8,0,0,1,11.32-11.32l32,32A8,8,0,0,1,237.66,133.66Z"/></svg>',
}


def annot_icon(name: str, color: str, size: int = 18, disabled_color: str | None = None) -> QIcon:
    """이름의 아이콘을 color로 칠한 QIcon(비활성 모양은 disabled_color, 없으면 Qt 기본 흐림).
    화면 배율(DPR)을 반영한다."""
    from PyQt6.QtSvg import QSvgRenderer

    screen = QApplication.primaryScreen()
    ratio = screen.devicePixelRatio() if screen else 1.0
    px = round(size * ratio)
    icon = QIcon()
    modes = [(QIcon.Mode.Normal, color)]
    if disabled_color is not None:
        modes.append((QIcon.Mode.Disabled, disabled_color))
    for mode, c in modes:
        pm = QPixmap(px, px)
        pm.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pm)
        QSvgRenderer(QByteArray(_SVGS[name].replace("currentColor", c).encode())).render(painter)
        painter.end()
        pm.setDevicePixelRatio(ratio)
        icon.addPixmap(pm, mode)
    return icon
