"""설정창 디자인 베이크오프 하네스 — 실제 SettingsDialog를 띄우고 옆 전환 바로 후보 QSS를 바꿔 본다.

    python tools/settings_bakeoff.py

- 원본 코드는 건드리지 않는다. 후보는 다이얼로그 스타일시트 **뒤에** 덧붙이는 QSS 조각
  (뒤에 선언된 규칙이 이긴다)과, 필요할 때만 위젯 단위 후처리 함수로 만든다.
- 격리: 설정값은 로컬 DB에서 **읽기만** 한다(시크릿 키 제외). settings_changed를 어디에도
  연결하지 않으므로 [저장]을 눌러도 실제 설정은 바뀌지 않는다.
- 라운드마다 CANDIDATES를 채워 쓰고, 채택안을 원본에 반영한 뒤 다시 비운다.
"""
from __future__ import annotations

import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtWidgets import QApplication, QWidget, QPushButton, QHBoxLayout, QVBoxLayout, QLabel
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QShortcut, QKeySequence

from pasteflow.ui import settings_dialog as sd
from pasteflow.ui.theme import COLORS

PEACH = COLORS["peach"]
NEUTRAL_HOVER = "#5a5a5a"   # 호버 테두리 — 카드·입력칸보다 한 단계 밝은 중립
TITLE_NEUTRAL = "#e6e6e6"   # 카드 제목 — 본문보다 살짝 밝은 흰색


_state = {"neutral_hotkey_hover": False}
_orig_apply_style = sd.HotkeyEdit._apply_style


def _patched_apply_style(self, listening):
    """HotkeyEdit은 녹화 시작·끝마다 자기 스타일을 다시 칠한다 → 후보 상태를 매번 재적용."""
    _orig_apply_style(self, listening)
    if _state["neutral_hotkey_hover"]:
        self.setStyleSheet(self.styleSheet().replace(
            f"QPushButton:hover {{ border-color: {PEACH}; }}",
            f"QPushButton:hover {{ border-color: {NEUTRAL_HOVER}; }}"))


sd.HotkeyEdit._apply_style = _patched_apply_style


def _hotkey_neutral_hover(dialog):
    """HotkeyEdit은 자기 스타일시트(호버=코랄)를 따로 들고 있어 부모 QSS로 못 덮는다 → 위젯 단위로."""
    _state["neutral_hotkey_hover"] = True
    for w in dialog.findChildren(sd.HotkeyEdit):
        w._apply_style(False)


_NEUTRAL_CORE = f"""
    QGroupBox::title {{ color: {TITLE_NEUTRAL}; }}
    QLineEdit:hover, QSpinBox:hover {{ border-color: {NEUTRAL_HOVER}; }}
    QCheckBox::indicator:hover {{ border-color: {NEUTRAL_HOVER}; }}
    QTabBar::tab:selected {{ color: {TITLE_NEUTRAL}; }}
"""

# (id, 버튼 라벨, 설명, 덧붙일 QSS, 위젯 후처리)
def _realign_labels(dialog):
    """라벨 글자가 바뀌면 원본 _setup_ui의 '탭별 라벨 열 폭 맞춤'을 다시 계산한다."""
    from PyQt6.QtWidgets import QFormLayout
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


def _strip_labels(dialog, colon: bool):
    """라벨 앞 '•' 불릿을 떼고(체크박스 행의 불릿 라벨은 숨김), colon=True면 끝 ':'도 뗀다."""
    for lbl in dialog.findChildren(QLabel):
        t = lbl.text()
        if t == "•":
            lbl.setVisible(False)
            continue
        if t.startswith("•  "):
            t = t[3:]
        if colon and t.endswith(":"):
            t = t[:-1]
        lbl.setText(t)
    _realign_labels(dialog)


# 라운드2 — 라벨 꾸밈 (라운드1 결과: A1 코랄 유지 → 기준점 그대로)
# (id, 버튼 라벨, 설명, 덧붙일 QSS, 위젯 후처리)
CANDIDATES = [
    ("B1", "B1 지금", "•  라벨:  (불릿 + 콜론)", "", None),
    ("B2", "B2 불릿 제거", "라벨:  (콜론만)", "", lambda d: _strip_labels(d, colon=False)),
    ("B3", "B3 둘 다 제거", "라벨  (불릿·콜론 없음)", "", lambda d: _strip_labels(d, colon=True)),
]


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


class Switcher(QWidget):
    def __init__(self, dialog):
        super().__init__(None)
        self._dialog = dialog
        self._base_qss = dialog.styleSheet()
        self._hotkey_base = {w: w.styleSheet() for w in dialog.findChildren(sd.HotkeyEdit)}
        # 후보가 글자·표시를 바꿔도 되돌릴 수 있게 라벨 원본을 기억한다
        self._label_base = {lbl: (lbl.text(), not lbl.isHidden())
                            for lbl in dialog.findChildren(QLabel)}
        self.setWindowTitle("베이크오프 전환 바")
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.setStyleSheet("QWidget{background:#101010;color:#ddd;font-size:12px;}"
                           "QPushButton{background:#2a2a2a;border:1px solid #444;border-radius:5px;"
                           "padding:6px 12px;} QPushButton:checked{border-color:#fff;background:#3a3a3a;}")
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        self._buttons = {}
        for i, (cid, label, desc, _qss, _post) in enumerate(CANDIDATES, start=1):
            b = QPushButton(f"{label}  [{i}]")
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, c=cid: self.apply(c))
            row.addWidget(b)
            self._buttons[cid] = b
            QShortcut(QKeySequence(str(i)), self, activated=lambda c=cid: self.apply(c))
            QShortcut(QKeySequence(str(i)), dialog, activated=lambda c=cid: self.apply(c))
        lay.addLayout(row)
        self._desc = QLabel("")
        self._desc.setWordWrap(True)
        lay.addWidget(self._desc)
        lay.addWidget(QLabel("숫자키 1·2·3으로도 전환. 설정창의 [저장]은 실제 설정을 바꾸지 않습니다."))

    def apply(self, cid: str):
        for c, _label, desc, qss, post in CANDIDATES:
            if c != cid:
                continue
            _state["neutral_hotkey_hover"] = False
            for w in self._hotkey_base:
                w._apply_style(False)
            for lbl, (text, visible) in self._label_base.items():
                lbl.setText(text)
                lbl.setVisible(visible)
            _realign_labels(self._dialog)
            self._dialog.setStyleSheet(self._base_qss + qss)
            if post:
                post(self._dialog)
            self._desc.setText(f"{c} — {desc}")
        for c, b in self._buttons.items():
            b.setChecked(c == cid)


def _selfcheck(out_dir: str):
    """후보마다 네 탭을 캡처해 out_dir에 저장(사용자에게 넘기기 전 자체확인용)."""
    app = QApplication(sys.argv)
    dialog = sd.SettingsDialog(_load_settings())
    dialog.show()
    sw = Switcher(dialog)
    for cid, *_ in CANDIDATES:
        sw.apply(cid)
        for i in range(dialog._tabs.count()):
            dialog._tabs.setCurrentIndex(i)
            app.processEvents()
            dialog.grab().save(os.path.join(out_dir, f"{cid}_tab{i}.png"))
    print("saved", len(CANDIDATES) * dialog._tabs.count())


def main():
    if len(sys.argv) > 2 and sys.argv[1] == "--selfcheck":
        _selfcheck(sys.argv[2])
        return
    app = QApplication(sys.argv)
    dialog = sd.SettingsDialog(_load_settings())
    dialog.setModal(False)
    dialog.show()
    sw = Switcher(dialog)
    sw.apply(CANDIDATES[0][0])
    sw.adjustSize()
    g = dialog.frameGeometry()
    sw.move(g.right() + 12, g.top())
    sw.show()
    dialog.finished.connect(app.quit)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
