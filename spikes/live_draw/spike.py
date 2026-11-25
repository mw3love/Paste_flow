"""라이브 화면 판서 0편 — 투명 전체 화면 창 성능 시험판.

계획: docs/plan/done/20261125-2040-라이브-화면-판서.md 「0편」. 앱 코드는 건드리지 않는다.

    python spikes/live_draw/spike.py --auto   # 모니터마다 혼자 그리고 재고 끝남 → result.txt
    python spikes/live_draw/spike.py          # 커서 모니터에 띄워 손으로 그려 봄(F8 클릭 통과, Esc 끝)

--auto가 재는 것
  1. 펜 끌기: 가짜 마우스 이벤트(QApplication.sendEvent — 운영체제 입력 주입 없음)를 보낸 때부터
     그 이벤트로 생긴 다시 그리기(processEvents 안의 paint + 화면 반영)가 끝날 때까지의 ms.
     선 1~20번째(처음)와 선 201~220번째(선 200개 쌓인 뒤)를 따로 센다.
  2. 클릭 받기: 배경 알파 1과 알파 0에서 WindowFromPoint가 이 창을 돌려주나(= 클릭이 이 창으로 오나).
  3. 클릭 통과: WS_EX_TRANSPARENT를 켜면 WindowFromPoint가 이 창을 건너뛰나, 끄면 돌아오나.
  4. 화면 읽기: screen.grabWindow(0, …)(녹화·영역 캡처와 같은 호출)가 이 창의 그림을 찍나.
"""
from __future__ import annotations

import ctypes
import math
import statistics
import sys
import time
from ctypes import wintypes
from pathlib import Path

from PyQt6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import QBrush, QColor, QCursor, QMouseEvent, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QApplication, QGraphicsScene, QGraphicsView, QWidget, QVBoxLayout, QFrame

_user32 = ctypes.WinDLL("user32")
_user32.WindowFromPoint.argtypes = [wintypes.POINT]
_user32.WindowFromPoint.restype = wintypes.HWND
_user32.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]
_user32.GetAncestor.restype = wintypes.HWND
_user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
_user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
_user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
_user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                 ctypes.c_int, ctypes.c_int, ctypes.c_uint]
_user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
_user32.GetAsyncKeyState.restype = ctypes.c_short

_GWL_EXSTYLE = -20
_WS_EX_TRANSPARENT = 0x20
_WS_EX_LAYERED = 0x80000
_GA_ROOT = 2
_HWND_TOPMOST = wintypes.HWND(-1)
_SWP = 0x0001 | 0x0002 | 0x0010 | 0x0020  # NOSIZE·NOMOVE·NOACTIVATE·FRAMECHANGED
_VK_F8, _VK_ESC = 0x77, 0x1B

HERE = Path(__file__).resolve().parent
STROKES_EARLY, STROKES_TOTAL, MOVES_PER_STROKE = 20, 220, 40


class _TimedView(QGraphicsView):
    """paintEvent 횟수를 세어 「재는 도구가 무엇을 봤나」를 확인한다."""

    def __init__(self, scene, parent):
        super().__init__(scene, parent)
        self.paints = 0

    def paintEvent(self, e):
        super().paintEvent(e)
        self.paints += 1


class Overlay(QWidget):
    def __init__(self, screen, bg_alpha: int = 1):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setGeometry(screen.geometry())
        self.screen_ = screen
        self.scene = QGraphicsScene(self)
        self.scene.setSceneRect(QRectF(0, 0, screen.geometry().width(), screen.geometry().height()))
        self.view = _TimedView(self.scene, self)
        self.view.setFrameShape(QFrame.Shape.NoFrame)
        self.view.setStyleSheet("background: transparent")
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.view.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.MinimalViewportUpdate)
        self.bg_alpha = bg_alpha
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.view)
        self.view.viewport().installEventFilter(self)
        self._path_item = None
        self._path = None

    # 배경 — 알파 0 화소는 Windows가 클릭을 아래 창으로 넘긴다(추정을 2번 측정이 확인)
    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, self.bg_alpha))

    def hwnd(self) -> int:
        return int(self.winId())

    def claim_topmost(self):
        _user32.SetWindowPos(self.hwnd(), _HWND_TOPMOST, 0, 0, 0, 0, _SWP)

    def set_click_through(self, on: bool):
        ex = _user32.GetWindowLongPtrW(self.hwnd(), _GWL_EXSTYLE)
        ex = (ex | _WS_EX_TRANSPARENT | _WS_EX_LAYERED) if on else (ex & ~_WS_EX_TRANSPARENT)
        _user32.SetWindowLongPtrW(self.hwnd(), _GWL_EXSTYLE, ex)
        _user32.SetWindowPos(self.hwnd(), _HWND_TOPMOST, 0, 0, 0, 0, _SWP)

    # 펜 — 누르면 경로 시작, 끌면 이어 붙임(주석 편집기의 펜과 같은 QGraphicsPathItem 방식)
    def eventFilter(self, obj, e):
        t = e.type()
        if t == QEvent.Type.MouseButtonPress:
            pos = self.view.mapToScene(e.position().toPoint())
            self._path = QPainterPath(pos)
            pen = QPen(QColor(255, 60, 60), 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                       Qt.PenJoinStyle.RoundJoin)
            self._path_item = self.scene.addPath(self._path, pen)
            return True
        if t == QEvent.Type.MouseMove and self._path_item is not None:
            self._path.lineTo(self.view.mapToScene(e.position().toPoint()))
            self._path_item.setPath(self._path)
            return True
        if t == QEvent.Type.MouseButtonRelease:
            self._path_item = None
            return True
        return False


def _send(view, kind, pt: QPointF):
    btn = Qt.MouseButton.LeftButton
    buttons = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseButtonRelease else btn
    gpt = view.viewport().mapToGlobal(pt.toPoint()).toPointF()
    ev = QMouseEvent(kind, pt, gpt, btn, buttons, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(view.viewport(), ev)


def _stroke_points(i: int, w: int, h: int):
    """선마다 다른 자리의 원 — 화면 곳곳에 선이 쌓인다."""
    cx = (i * 97) % max(1, w - 300) + 150
    cy = (i * 61) % max(1, h - 300) + 150
    r = 60 + (i % 5) * 20
    for k in range(MOVES_PER_STROKE):
        a = 2 * math.pi * k / (MOVES_PER_STROKE - 1)
        yield QPointF(cx + r * math.cos(a), cy + r * math.sin(a))


def _stats(xs):
    xs = sorted(xs)
    p95 = xs[int(len(xs) * 0.95) - 1]
    return f"평균 {statistics.mean(xs):.2f}ms · 95번째 {p95:.2f}ms · 최대 {xs[-1]:.2f}ms (n={len(xs)})"


def _hit_is(ov, local_x, local_y) -> bool:
    g = ov.mapToGlobal(ov.rect().topLeft())
    hit = _user32.WindowFromPoint(wintypes.POINT(g.x() + local_x, g.y() + local_y))
    root = _user32.GetAncestor(hit, _GA_ROOT) if hit else None
    return bool(root) and int(root) == ov.hwnd()


def measure_screen(app, screen, out: list[str]):
    g = screen.geometry()
    out.append(f"\n## 모니터 {screen.name()} {g.width()}x{g.height()} 배율 {screen.devicePixelRatio()}")

    # 4. 화면 읽기 전 기준 화소(판서 창을 띄우기 전)
    probe = QPointF(40, 40)
    before = screen.grabWindow(0, int(probe.x()), int(probe.y()), 1, 1).toImage().pixelColor(0, 0)

    ov = Overlay(screen, bg_alpha=1)
    ov.show()
    ov.claim_topmost()
    for _ in range(5):
        app.processEvents()
        time.sleep(0.02)

    # 2·3. 클릭 받기·통과 — WindowFromPoint로 판정(진짜 클릭 없음)
    cx, cy = g.width() // 2, g.height() // 2
    hit_alpha1 = _hit_is(ov, cx, cy)
    ov.set_click_through(True); app.processEvents(); time.sleep(0.05)
    hit_through = _hit_is(ov, cx, cy)
    ov.set_click_through(False); app.processEvents(); time.sleep(0.05)
    hit_back = _hit_is(ov, cx, cy)
    ov.bg_alpha = 0; ov.update(); app.processEvents(); time.sleep(0.1); app.processEvents()
    hit_alpha0 = _hit_is(ov, cx, cy)
    ov.bg_alpha = 1; ov.update(); app.processEvents()
    out.append(f"클릭 받기 — 배경 알파 1: {'이 창' if hit_alpha1 else '아래 창'} · 배경 알파 0: "
               f"{'이 창' if hit_alpha0 else '아래 창'}")
    out.append(f"클릭 통과 — WS_EX_TRANSPARENT 켬: {'이 창(실패)' if hit_through else '아래 창(통과)'} · "
               f"끔: {'이 창(돌아옴)' if hit_back else '아래 창(실패)'}")

    # 1. 펜 끌기 시간
    early, late = [], []
    paints0 = ov.view.paints
    for i in range(STROKES_TOTAL):
        pts = list(_stroke_points(i, g.width(), g.height()))
        _send(ov.view, QEvent.Type.MouseButtonPress, pts[0])
        app.processEvents()
        for pt in pts[1:]:
            t0 = time.perf_counter()
            _send(ov.view, QEvent.Type.MouseMove, pt)
            app.processEvents()
            dt = (time.perf_counter() - t0) * 1000
            if i < STROKES_EARLY:
                early.append(dt)
            elif i >= STROKES_TOTAL - STROKES_EARLY:
                late.append(dt)
        _send(ov.view, QEvent.Type.MouseButtonRelease, pts[-1])
        app.processEvents()
    paints = ov.view.paints - paints0
    moves = STROKES_TOTAL * (MOVES_PER_STROKE - 1)
    out.append(f"펜 끌기(선 1~{STROKES_EARLY}번째): {_stats(early)}")
    out.append(f"펜 끌기(선 {STROKES_TOTAL - STROKES_EARLY + 1}~{STROKES_TOTAL}번째, 선 200개 쌓인 뒤): {_stats(late)}")
    out.append(f"재는 도구 확인 — 이동 {moves}번에 paintEvent {paints}번 · 장면 아이템 {len(ov.scene.items())}개")

    # 4. 화면 읽기 — 빨간 사각형을 그리고 같은 화소를 다시 읽는다
    ov.scene.addRect(QRectF(0, 0, 120, 120), QPen(Qt.PenStyle.NoPen), QBrush(QColor(255, 0, 0)))
    for _ in range(5):
        app.processEvents()
        time.sleep(0.03)
    after = screen.grabWindow(0, int(probe.x()), int(probe.y()), 1, 1).toImage().pixelColor(0, 0)
    caught = after.red() > 200 and after.green() < 60 and after.blue() < 60
    out.append(f"화면 읽기(grabWindow) — 전 {before.name()} · 후 {after.name()} → {'찍힘' if caught else '안 찍힘'}")
    ov.close()
    app.processEvents()
    return caught


def run_auto(app):
    out = [f"# 라이브 판서 시험판 결과 — {time.strftime('%Y-%m-%d %H:%M')}",
           "가짜 마우스 이벤트(sendEvent)로 잼. 운영체제 입력 지연은 안 들어감."]
    for s in app.screens():
        measure_screen(app, s, out)
    text = "\n".join(out) + "\n"
    (HERE / "result.txt").write_text(text, encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")  # 콘솔이 cp949면 줄표(—)에서 멈춘다
    print(text)


def run_manual(app):
    screen = QApplication.screenAt(QCursor.pos()) or app.primaryScreen()
    ov = Overlay(screen, bg_alpha=1)
    ov.show()
    ov.claim_topmost()
    state = {"through": False, "f8": False}

    def poll():  # 통과 상태에선 키보드 포커스가 없으므로 F8·Esc를 직접 읽는다
        f8 = bool(_user32.GetAsyncKeyState(_VK_F8) & 0x8000)
        if f8 and not state["f8"]:
            state["through"] = not state["through"]
            ov.set_click_through(state["through"])
            print("클릭 통과" if state["through"] else "그리기")
        state["f8"] = f8
        if _user32.GetAsyncKeyState(_VK_ESC) & 0x8000:
            app.quit()

    t = QTimer(ov)
    t.timeout.connect(poll)
    t.start(30)
    app.exec()


if __name__ == "__main__":
    _app = QApplication(sys.argv)
    if "--auto" in sys.argv:
        run_auto(_app)
    else:
        run_manual(_app)
