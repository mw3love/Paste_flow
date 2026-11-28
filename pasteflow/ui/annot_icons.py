"""주석 편집기 툴바·옵션창 아이콘 — Phosphor Icons (MIT, https://phosphoricons.com).

menu_icons.py와 같은 방식: SVG를 문자열로 내장한다(파일로 두면 PyInstaller spec의 datas에
따로 넣어야 하고, 빠뜨리면 exe에서만 아이콘이 사라진다). `currentColor`를 원하는 색으로 바꿔 그린다.
고른 시안: docs/design.md 「주석 편집기 툴바 아이콘」(2026-11-26) — 새 칠판 trash 듀오톤 ·
스포이드 eyedropper 듀오톤 · 뒤집기 arrows-left-right(선) · 양쪽 머리 arrows-horizontal(선).
계획: docs/plan/20261126-0030-주석-편집기-툴바-다듬기.md.
"""
from __future__ import annotations

from PyQt6.QtCore import QByteArray, QSize, Qt
from PyQt6.QtGui import QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication

_HEAD = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" fill="currentColor">'

_SVGS: dict[str, str] = {
    "trash": _HEAD
    + '<path d="M200,56V208a8,8,0,0,1-8,8H64a8,8,0,0,1-8-8V56Z" opacity="0.2"/>'
    '<path d="M216,48H176V40a24,24,0,0,0-24-24H104A24,24,0,0,0,80,40v8H40a8,8,0,0,0,0,16h8V208a16,16,0,0,0,'
    '16,16H192a16,16,0,0,0,16-16V64h8a8,8,0,0,0,0-16ZM96,40a8,8,0,0,1,8-8h48a8,8,0,0,1,8,8v8H96Zm96,168H64V64H192Z'
    'M112,104v64a8,8,0,0,1-16,0V104a8,8,0,0,1,16,0Zm48,0v64a8,8,0,0,1-16,0V104a8,8,0,0,1,16,0Z"/></svg>',
    "eyedropper": _HEAD
    + '<path d="M207.8,87.6l-25.37,25.53,4.89,4.88a16,16,0,0,1,0,22.64l-9,9a8,8,0,0,1-11.32,0l-60.68-60.7'
    'a8,8,0,0,1,0-11.32l9-9a16,16,0,0,1,22.63,0l4.88,4.89,25-25.11c10.79-10.79,28.37-11.45,39.45-1A28,28,0,0,1,'
    '207.8,87.6Z" opacity="0.2"/>'
    '<path d="M224,67.3a35.79,35.79,0,0,0-11.26-25.66c-14-13.28-36.72-12.78-50.62,1.13L142.8,62.2a24,24,0,0,0-'
    '33.14.77l-9,9a16,16,0,0,0,0,22.64l2,2.06-51,51a39.75,39.75,0,0,0-10.53,38l-8,18.41A13.68,13.68,0,0,0,36,'
    '219.3a15.92,15.92,0,0,0,17.71,3.35L71.23,215a39.89,39.89,0,0,0,37.06-10.75l51-51,2.06,2.06a16,16,0,0,0,'
    '22.62,0l9-9a24,24,0,0,0,.74-33.18l19.75-19.87A35.75,35.75,0,0,0,224,67.3ZM97,193a24,24,0,0,1-24,6,8,8,0,0,'
    '0-5.55.31l-18.1,7.91L57,189.41a8,8,0,0,0,.25-5.75A23.88,23.88,0,0,1,63,159l51-51,33.94,34ZM202.13,82'
    'l-25.37,25.52a8,8,0,0,0,0,11.3l4.89,4.89a8,8,0,0,1,0,11.32l-9,9L112,83.26l9-9a8,8,0,0,1,11.31,0l4.89,4.89'
    'a8,8,0,0,0,5.65,2.34h0a8,8,0,0,0,5.66-2.36l24.94-25.09c7.81-7.82,20.5-8.18,28.29-.81a20,20,0,0,1,.39,'
    '28.7Z"/></svg>',
    "arrows-left-right": _HEAD
    + '<path d="M213.66,181.66l-32,32a8,8,0,0,1-11.32-11.32L188.69,184H48a8,8,0,0,1,0-16H188.69l-18.35-18.34'
    'a8,8,0,0,1,11.32-11.32l32,32A8,8,0,0,1,213.66,181.66Zm-139.32-64a8,8,0,0,0,11.32-11.32L67.31,88H208a8,8,0,'
    '0,0,0-16H67.31L85.66,53.66A8,8,0,0,0,74.34,42.34l-32,32a8,8,0,0,0,0,11.32Z"/></svg>',
    "arrows-horizontal": _HEAD
    + '<path d="M237.66,133.66l-32,32a8,8,0,0,1-11.32-11.32L212.69,136H43.31l18.35,18.34a8,8,0,0,1-11.32,'
    '11.32l-32-32a8,8,0,0,1,0-11.32l32-32a8,8,0,0,1,11.32,11.32L43.31,120H212.69l-18.35-18.34a8,8,0,0,1,'
    '11.32-11.32l32,32A8,8,0,0,1,237.66,133.66Z"/></svg>',
}

_DISABLED_ALPHA = 0.35   # 흐린 버튼(양쪽 머리 켠 동안의 뒤집기) — 시안의 .dis 불투명도


def annot_icon(name: str, color: str, size: int = 18) -> QIcon:
    """이름의 아이콘을 color로 칠한 QIcon(비활성 모양은 같은 색을 흐리게). 화면 배율(DPR)을 반영한다."""
    from PyQt6.QtSvg import QSvgRenderer

    screen = QApplication.primaryScreen()
    ratio = screen.devicePixelRatio() if screen else 1.0
    px = round(size * ratio)
    icon = QIcon()
    for mode, alpha in ((QIcon.Mode.Normal, 1.0), (QIcon.Mode.Disabled, _DISABLED_ALPHA)):
        pm = QPixmap(px, px)
        pm.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pm)
        painter.setOpacity(alpha)
        QSvgRenderer(QByteArray(_SVGS[name].replace("currentColor", color).encode())).render(painter)
        painter.end()
        pm.setDevicePixelRatio(ratio)
        icon.addPixmap(pm, mode)
    return icon


ICON_QSIZE = QSize(18, 18)
