"""설정창 디자인 베이크오프 하네스 — 실제 SettingsDialog를 띄우고 옆 전환 바로 후보를 바꿔 본다.

    python tools/settings_bakeoff.py                   # 비교 창
    python tools/settings_bakeoff.py --selfcheck DIR   # 후보별 탭 캡처(자체확인용)

- 원본 코드는 건드리지 않는다. 후보 = 다이얼로그 스타일시트 **뒤에** 덧붙일 QSS 조각
  (뒤 규칙이 이긴다) + 필요하면 위젯을 옮기는 후처리 함수.
- 후보를 바꿀 때마다 **설정창을 새로 만든다** — 위젯을 옮기는 후보도 되돌리기 코드 없이
  깨끗하게 비교된다. 탭·창 위치는 이어받는다.
- 격리: 설정값은 로컬 DB에서 **읽기만** 한다(시크릿 제외). settings_changed를 어디에도
  연결하지 않으므로 [저장]을 눌러도 실제 설정은 바뀌지 않는다.
- 라운드마다 CANDIDATES를 채워 쓰고, 채택안을 원본에 반영한 뒤 다시 비운다.
  라운드 기록: ~/.claude/design-system/projects/pasteflow.md
"""
from __future__ import annotations

import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtWidgets import (
    QApplication, QWidget, QPushButton, QHBoxLayout, QVBoxLayout, QLabel, QFormLayout,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QShortcut, QKeySequence

from pasteflow.ui import settings_dialog as sd


# ── 후보 공용 도구 ──────────────────────────────────────────────────────────────

def realign_labels(dialog):
    """라벨·행이 바뀌면 원본 _setup_ui의 '탭별 라벨 열 폭 맞춤'을 다시 계산한다."""
    for t in range(dialog._tabs.count()):
        page = dialog._tabs.widget(t).widget()
        labels = []
        for form in page.findChildren(QFormLayout):
            for row in range(form.rowCount()):
                item = form.itemAt(row, QFormLayout.ItemRole.LabelRole)
                if item is not None and item.widget() is not None:
                    labels.append(item.widget())
        for lbl in labels:
            lbl.setMinimumWidth(0)
        if labels:
            width = max(lbl.sizeHint().width() for lbl in labels)
            for lbl in labels:
                lbl.setMinimumWidth(width)


def find_row(widget) -> tuple[QFormLayout, int]:
    """위젯이 필드(또는 스팬)로 들어 있는 폼과 행 번호."""
    parent = widget.parentWidget()
    for form in parent.findChildren(QFormLayout):
        idx = form.indexOf(widget)
        if idx >= 0:
            row, _role = form.getItemPosition(idx)
            return form, row
    raise LookupError("폼 행을 찾지 못함")


def checkbox_row(check) -> QWidget:
    """원본 _bullet_checkbox_row가 만든 행 컨테이너(불릿 라벨 + 체크박스)."""
    return check.parentWidget()


# ── 이번 라운드 후보 ─────────────────────────────────────────────────────────────
# 라운드마다 여기에 후보 함수를 두고 CANDIDATES에 등록한다. 채택안을 원본에 반영한 뒤
# 다시 기준점 하나로 비운다. 지난 라운드: 1 코랄 범위, 2 라벨 꾸밈, 3 딸린 옵션 위치,
# 4 내비게이션(왼쪽 목록+아이콘 채택 — 원본 반영 완료).

# (id, 버튼 라벨, 설명, 덧붙일 QSS, 후처리)
CANDIDATES = [
    ("X0", "X0 지금", "현재 설정창(기준점)", "", None),
]


# ── 하네스 본체 ────────────────────────────────────────────────────────────────

def _load_settings() -> dict:
    """로컬 DB 설정을 읽기 전용으로 가져온다(시크릿 제외 — 화면 모양 판정엔 불필요)."""
    path = os.path.join(os.environ.get("LOCALAPPDATA", ""), "PasteFlow", "pasteflow.db")
    if not os.path.exists(path):
        return {}
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT key, value FROM settings").fetchall()
    finally:
        con.close()
    return {k: v for k, v in rows if k != "ocr_gemini_api_key_gateway"}


def build_dialog(cid: str):
    """후보 cid를 적용한 새 설정창."""
    d = sd.SettingsDialog(_load_settings())
    d.setModal(False)
    for c, _label, _desc, qss, post in CANDIDATES:
        if c == cid:
            if qss:
                d.setStyleSheet(d.styleSheet() + qss)
            if post:
                post(d)
    return d


class Switcher(QWidget):
    def __init__(self, app):
        super().__init__(None)
        self._app = app
        self._dialog = None
        self.setWindowTitle("베이크오프 전환 바")
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.setStyleSheet("QWidget{background:#101010;color:#ddd;font-size:12px;}"
                           "QPushButton{background:#2a2a2a;border:1px solid #444;border-radius:5px;"
                           "padding:6px 12px;} QPushButton:checked{border-color:#fff;background:#3a3a3a;}")
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        self._buttons = {}
        for i, (cid, label, *_rest) in enumerate(CANDIDATES, start=1):
            b = QPushButton(f"{label}  [{i}]")
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, c=cid: self.apply(c))
            row.addWidget(b)
            self._buttons[cid] = b
            QShortcut(QKeySequence(str(i)), self, activated=lambda c=cid: self.apply(c))
        lay.addLayout(row)
        self._desc = QLabel("")
        self._desc.setWordWrap(True)
        lay.addWidget(self._desc)
        lay.addWidget(QLabel(f"숫자키 1~{len(CANDIDATES)}로도 전환(설정창에서도 됨). "
                             "설정창의 [저장]은 실제 설정을 바꾸지 않습니다."))

    def apply(self, cid: str):
        old = self._dialog
        tab = old._tabs.currentIndex() if old is not None else 0
        geom = old.geometry() if old is not None else None
        d = build_dialog(cid)
        for i, (c, *_rest) in enumerate(CANDIDATES, start=1):
            QShortcut(QKeySequence(str(i)), d, activated=lambda c=c: self.apply(c))
        d._tabs.setCurrentIndex(tab)
        if geom is not None:
            d.setGeometry(geom)
        d.show()
        self._dialog = d
        if old is not None:
            old.finished.disconnect()
            old.close()
            old.deleteLater()
        d.finished.connect(self._app.quit)
        for c, _label, desc, *_rest in CANDIDATES:
            if c == cid:
                self._desc.setText(f"{c} — {desc}")
        for c, b in self._buttons.items():
            b.setChecked(c == cid)
        self.raise_()


def _selfcheck(out_dir: str):
    """후보마다 네 탭을 캡처해 out_dir에 저장(사용자에게 넘기기 전 자체확인용)."""
    app = QApplication(sys.argv)
    n = 0
    for cid, *_rest in CANDIDATES:
        d = build_dialog(cid)
        d.show()
        for i in range(d._tabs.count()):
            d._tabs.setCurrentIndex(i)
            app.processEvents()
            d.grab().save(os.path.join(out_dir, f"{cid}_tab{i}.png"))
            n += 1
        d.close()
    print("saved", n)


def main():
    if len(sys.argv) > 2 and sys.argv[1] == "--selfcheck":
        _selfcheck(sys.argv[2])
        return
    app = QApplication(sys.argv)
    sw = Switcher(app)
    sw.apply(CANDIDATES[0][0])
    sw.adjustSize()
    g = sw._dialog.frameGeometry()
    sw.move(g.right() + 12, g.top())
    sw.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
