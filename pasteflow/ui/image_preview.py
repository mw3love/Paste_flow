"""이미지 미리보기 + 인라인 주석 편집 (통합 단일 창, Snipaste식).

평소엔 가벼운 뷰어(창이 이미지에 딱 맞게 리사이즈 = hug-zoom, 레터박스 없음). Space를 누르면
같은 창에서 편집 툴바가 펴진다(편집 모드). 편집 컴포넌트는 image_annotator.py에서 가져와 host.

- 뷰어 모드: 좌클릭 드래그 = 창 이동, 휠 = 줌(창 리사이즈), 더블클릭/ESC = 닫기,
  우클릭 = 복사/OCR/주석 편집/닫기.
- 편집 모드: 툴바·액션바 표시, 그리기/선택/크기조절, 창 이동은 상단 핸들로만. ESC = 편집 종료.
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGraphicsScene, QGraphicsView, QFrame,
    QMenu, QLabel, QApplication, QToolButton,
)
from PyQt6.QtCore import Qt, QPoint, QRect, QRectF, QSize, QEvent, pyqtSignal

from pasteflow.ui.theme import (
    BASE as _BG, SURFACE0 as _SURFACE0, SURFACE1 as _BORDER, SURFACE2 as _SURFACE2,
    TEXT as _TEXT, PEACH as _PEACH,
)
from pasteflow.models import ClipboardItem
from pasteflow.ui.image_annotator import (
    _EditorMixin, _AnnotatorView, _DragBar, _pixmap_from_data, _tool_icon,
    flatten_scene_to_png,
)

PREVIEW_MAX_W = 640
PREVIEW_MAX_H = 480
_PREVIEW_MARGIN = 8
_CASCADE_STEP = 24
_CASCADE_NEAR = 150  # 이 거리(px) 안에 있는 기존 창만 cascade 대상으로 셈


def compute_preview_pos(
    panel_geom: QRect,
    popup_size: QSize,
    screen,
    cascade_offset: int = 0,
) -> QPoint:
    """미리보기 팝업 위치 결정 — 패널 우측 우선, 부족하면 좌측, 그래도 안 되면 화면 내 fit.

    cascade_offset: 같은 위치에 연속해서 띄울 때 (x, y) 양쪽으로 어긋나는 양(px).
    """
    avail = screen.availableGeometry()
    w, h = popup_size.width(), popup_size.height()

    def _clamp_y(y: int) -> int:
        return max(avail.top(), min(y, avail.bottom() - h))

    right_x = panel_geom.right() + _PREVIEW_MARGIN + cascade_offset
    if right_x + w <= avail.right():
        return QPoint(right_x, _clamp_y(panel_geom.top() + cascade_offset))

    left_x = panel_geom.left() - _PREVIEW_MARGIN - w - cascade_offset
    if left_x >= avail.left():
        return QPoint(left_x, _clamp_y(panel_geom.top() + cascade_offset))

    x = max(avail.left(), min(panel_geom.right() + _PREVIEW_MARGIN, avail.right() - w))
    return QPoint(x, _clamp_y(panel_geom.top()))


def _dark_menu_style() -> str:
    return f"""
        QMenu {{
            background-color: {_SURFACE0};
            color: {_TEXT};
            border: 1px solid {_BORDER};
            border-radius: 6px;
            padding: 4px;
        }}
        QMenu::item {{
            padding: 6px 16px;
            border-radius: 4px;
        }}
        QMenu::item:selected {{
            background-color: {_SURFACE2};
        }}
    """


class ImagePreviewPopup(_EditorMixin, QWidget):
    """이미지 미리보기 + 인라인 주석 편집 통합 창 — 다중 창 동시 표시 지원."""

    _instances: list["ImagePreviewPopup"] = []

    # 뷰어 모드 우클릭 메뉴 → main 핸들러 (ClipboardItem)
    copy_requested = pyqtSignal(object)        # ClipboardItem
    ocr_requested = pyqtSignal(object)         # ClipboardItem
    ai_requested = pyqtSignal(object)          # ClipboardItem — AI에게 질문(시각 질의)
    copy_as_path_requested = pyqtSignal(object)  # ClipboardItem — 파일로 저장 후 경로 복사
    # 편집 완료 → main 핸들러 (PNG bytes)
    annotated_copy_requested = pyqtSignal(bytes)   # 클립보드 복사 + 히스토리 저장
    export_file_requested = pyqtSignal(bytes)      # 파일 저장

    # ------------------------------------------------------------------
    @classmethod
    def open_new(cls, item: ClipboardItem, panel_geom: QRect,
                 native: bool = False) -> "ImagePreviewPopup":
        # native=True면 이미지를 원본 픽셀 크기(1:1)로 띄운다(화면 초과 시만 축소).
        # 캡처(Alt+F2)→핀(Alt+F3) 시 "캡처한 크기 그대로" 보이게 하는 용도.
        # cascade는 "총 창 수"가 아니라 "새 앵커(커서/패널) 근처에 이미 떠 있는 창 수"에만
        # 비례한다. 핀(Alt+F3)은 매번 커서를 앵커로 쓰므로, 커서를 옮겨 새로 핀하면
        # 근처 창이 0개 → offset 0 → 커서 바로 옆에 뜨고, 같은 자리에 연속 핀할 때만
        # 어긋난다(겹침 방지). 패널 미리보기는 앵커가 고정이라 기존 cascade 그대로 유지.
        base = panel_geom.topLeft()
        near = sum(
            1 for p in cls._instances
            if (p.pos() - base).manhattanLength() < _CASCADE_NEAR
        )
        cascade_offset = near * _CASCADE_STEP
        popup = cls(item)
        cls._instances.append(popup)
        popup.show_preview(panel_geom, cascade_offset, native=native)
        return popup

    @classmethod
    def close_all(cls):
        for popup in list(cls._instances):
            popup.close()

    # ------------------------------------------------------------------
    def __init__(self, item: ClipboardItem):
        super().__init__(None)
        self._item = item
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        # 상단에 툴바용 공간을 '항상' 비워두고(레이아웃 top 마진), 그 빈 strip을 투명 처리해
        # 뷰어 모드에선 안 보이게 한다(= 이미지만 떠 있는 느낌). 편집 토글은 그 예약 공간에
        # chrome을 보였다/숨겼다 할 뿐이라 창 크기·위치가 안 바뀜 → 잔상 0, 이미지 안 가림.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setObjectName("previewroot")

        self._edit_mode = False
        self._zoom = 1.0
        self._win_drag: QPoint | None = None

        self._init_editor_state()

        # 씬 + 배경 이미지
        self._scene = QGraphicsScene(self)
        self._bg_item = None
        pm = _pixmap_from_data(item.image_data) if item.image_data else None
        self._pixmap_ok = pm is not None and not pm.isNull()
        if self._pixmap_ok:
            self._scene.setSceneRect(QRectF(pm.rect()))
            self._bg_item = self._scene.addPixmap(pm)
            # QGraphicsPixmapItem 기본 transformationMode는 Fast(nearest) — 뷰의
            # SmoothPixmapTransform 힌트를 아이템 paint가 덮어써, 비정수 배율(hug-zoom)에서
            # 이미지·텍스트가 거칠게(깨진 듯) 보인다. Smooth로 명시해 부드럽게 스케일.
            self._bg_item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
            self._bg_item.setZValue(0)

        self._view = _AnnotatorView(self._scene, self)
        self._view.setFrameShape(QFrame.Shape.NoFrame)
        self._view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)

        self._build_layout()
        self.set_tool("select")
        self._view.setDragMode(QGraphicsView.DragMode.NoDrag)  # 뷰어 시작
        self._set_color(self.current_color)
        self._apply_active_style(False)
        self.hide()

    # ------------------------------------------------------------------
    # 레이아웃 — 드래그핸들 / 툴바 / 뷰 / 액션바 (편집 모드만 chrome 표시)
    # ------------------------------------------------------------------
    def _build_layout(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # chrome(타이틀바+툴바)은 레이아웃이 아니라 이미지 위에 '떠 있는' 자식 컨테이너로 둔다.
        # 편집 토글 시 보이기/숨기기만 하므로 창 크기·위치가 안 바뀌고(= 잔상 0), 이미지도 안
        # 움직인다. 대신 편집 중에는 툴바가 이미지 상단 일부를 덮는다(CleanShot/Snipaste식).
        self._chrome = QWidget(self)
        self._chrome.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        chrome_l = QVBoxLayout(self._chrome)
        chrome_l.setContentsMargins(0, 0, 0, 0)
        chrome_l.setSpacing(0)

        # 상단 드래그 핸들 (편집 모드 창 이동) — 배경색은 활성/비활성(파랑/코랄)과 통일.
        # 제목은 굵게, 단축키 힌트는 작은 secondary 색, 우측에 닫기 버튼.
        self._dragbar = _DragBar(self)
        self._dragbar.setObjectName("dragbar")
        bar_l = QHBoxLayout(self._dragbar)
        bar_l.setContentsMargins(10, 0, 6, 0)
        bar_l.setSpacing(8)
        title = QLabel("주석 편집")
        title.setObjectName("title")
        bar_l.addWidget(title)
        hint = QLabel("드래그 이동 · Space 토글 · ESC 종료")
        hint.setObjectName("hint")
        bar_l.addWidget(hint)
        bar_l.addStretch(1)
        close_btn = QToolButton()
        close_btn.setObjectName("titleclose")
        close_btn.setIcon(_tool_icon("close", neutral_override=_BG))  # 밝은 바 위 어두운 X
        close_btn.setIconSize(QSize(18, 18))
        close_btn.setToolTip("닫기 (ESC)")
        close_btn.clicked.connect(self.close)
        bar_l.addWidget(close_btn)
        chrome_l.addWidget(self._dragbar)

        # 툴바
        self._toolbar_host = QWidget()
        self._toolbar_host.setLayout(self._build_toolbar())
        chrome_l.addWidget(self._toolbar_host)

        # 이미지 뷰 — 레이아웃의 유일한 위젯이라 창이 이미지에 딱 맞는다(hug). chrome은 떠 있어
        # 레이아웃에 영향을 주지 않으므로, 편집 토글로 창 높이가 바뀌지 않는다.
        root.addWidget(self._view, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

        # 완료 버튼(복사/저장/닫기)은 툴바 오른쪽에 합쳐졌으므로 별도 액션바 없음
        self._chrome.setVisible(False)  # 뷰어 모드 시작
        # 상단 strip(=chrome 높이)을 항상 예약(top 마진) — 이미지는 그 아래에 배치되고,
        # 빈 strip은 투명(WA_TranslucentBackground)이라 뷰어 모드에선 안 보인다. 편집 토글로
        # 창 크기·위치가 안 바뀌어 잔상이 없고, 툴바는 strip 안에 떠서 이미지를 덮지 않는다.
        self._chrome_h = self._chrome.sizeHint().height()
        root.setContentsMargins(0, self._chrome_h, 0, 0)

    # ------------------------------------------------------------------
    # 표시 + hug-zoom
    # ------------------------------------------------------------------
    def show_preview(self, panel_geom: QRect, cascade_offset: int = 0, native: bool = False):
        if not self._pixmap_ok:
            return
        screen = QApplication.screenAt(panel_geom.center()) or QApplication.primaryScreen()
        sr = self._scene.sceneRect()
        z0 = 1.0
        if sr.width() > 0 and sr.height() > 0:
            if native:
                # 원본 크기(1:1) — 화면(여유 40px)을 넘을 때만 축소.
                z0 = 1.0
                if screen:
                    avail = screen.availableGeometry()
                    z0 = min(1.0,
                             (avail.width() - 40) / sr.width(),
                             (avail.height() - 40) / sr.height())
            else:
                z0 = min(1.0, PREVIEW_MAX_W / sr.width(), PREVIEW_MAX_H / sr.height())
        self._apply_zoom(z0)

        if screen:
            # 창 상단엔 투명 strip(chrome_h)이 있으므로, 그만큼 창을 위로 올려 '이미지'가
            # 의도한 위치(패널/커서 근처)에 오게 한다.
            pos = compute_preview_pos(panel_geom, self.size(), screen, cascade_offset)
            pos.setY(max(screen.availableGeometry().top(), pos.y() - self._chrome_h))
            self.move(pos)
        self.show()
        self.raise_()
        # 활성화 — 이후 Space(편집 토글)·ESC(닫기)를 미리보기가 직접 받도록
        self.activateWindow()

    def _apply_zoom(self, z: float):
        """zoom factor z로 뷰 변환·크기를 맞추고 창을 이미지에 hug되게 리사이즈."""
        self._zoom = max(0.1, min(z, 8.0))
        self._view.resetTransform()
        self._view.scale(self._zoom, self._zoom)
        sr = self._scene.sceneRect()
        vw = max(1, round(sr.width() * self._zoom))
        vh = max(1, round(sr.height() * self._zoom))
        self._view.setFixedSize(vw, vh)
        self.adjustSize()  # 레이아웃 위젯은 뷰뿐 → 창이 이미지에 딱 맞는다(hug)
        # 편집 중이면 떠 있는 chrome을 이미지 상단에 다시 배치(줌으로 폭이 바뀌었을 수 있음)
        if getattr(self, "_chrome", None) is not None and self._chrome.isVisible():
            self._layout_chrome()

    def _layout_chrome(self):
        """편집 모드에서 chrome(타이틀바+툴바)을 상단 예약 strip에 배치한다(이미지 위가 아님).
        툴바가 이미지보다 넓으면 창 '폭만' 오른쪽으로 늘려 잘리지 않게 한다 — top-left를
        고정하고 높이는 그대로라, 이미지는 가로/세로 어느 쪽으로도 움직이지 않는다(잔상 없음).
        이미지가 툴바보다 넓은 일반적인 경우엔 창 크기 변화가 아예 없다."""
        win_w = max(self._view.width(), self._chrome.sizeHint().width())
        if self.width() != win_w:
            self.resize(win_w, self.height())  # 폭만, top-left 고정
        self._chrome.setGeometry(0, 0, win_w, self._chrome_h)
        self._chrome.raise_()

    def _on_wheel_zoom(self, delta: int):
        factor = 1.15 if delta > 0 else 1 / 1.15
        self._apply_zoom(self._zoom * factor)

    # ------------------------------------------------------------------
    # 모드 (뷰어 / 편집)
    # ------------------------------------------------------------------
    def is_edit_mode(self) -> bool:
        return self._edit_mode

    def toggle_edit_mode(self):
        # chrome은 레이아웃 밖 '떠 있는' 자식이라 보이기/숨기기만 해도 창 크기·위치가 안
        # 바뀐다 → 이미지가 움직이지 않고 잔상도 없다. 편집 진입 시 chrome을 이미지 상단에
        # 겹쳐 배치(_layout_chrome), 종료 시 숨기고 창을 이미지에 다시 딱 맞춘다.
        self._edit_mode = not self._edit_mode
        self._chrome.setVisible(self._edit_mode)
        if self._edit_mode:
            self.set_tool("select")  # RubberBandDrag
            self.activateWindow()
        else:
            self._scene.clearSelection()
            self._view.setDragMode(QGraphicsView.DragMode.NoDrag)
        self._update_arrow_dir_btn()      # 뷰어 전환 시 floating 방향 토글 숨김
        self._update_text_opts_bar()      # 뷰어 전환 시 텍스트 옵션 바 숨김
        self._update_badge_size_stepper()  # 뷰어 전환 시 번호 크기 스테퍼 숨김
        if self._edit_mode:
            self._layout_chrome()         # 이미지 상단에 겹쳐 배치(+필요 시 폭만 확장)
        else:
            self._apply_zoom(self._zoom)  # 뷰어 복귀 — 창을 이미지에 다시 hug(폭 확장 되돌림)
        self._view.setFocus()

    def _on_escape(self):
        if self._eyedrop_active:
            self._stop_eyedropper(False)
            return
        if self._edit_mode:
            self.toggle_edit_mode()
            return
        self.close()

    # ------------------------------------------------------------------
    # 창 이동 (뷰어 모드 본문 드래그 — _AnnotatorView가 호출)
    # ------------------------------------------------------------------
    def _win_drag_start(self, global_pt: QPoint):
        self.activateWindow()
        self._win_drag = global_pt - self.frameGeometry().topLeft()

    def _win_drag_move(self, global_pt: QPoint):
        if self._win_drag is not None:
            self.move(global_pt - self._win_drag)

    def _win_drag_end(self):
        self._win_drag = None

    # ------------------------------------------------------------------
    # 주석 반영 (비파괴) — 씬에 주석이 있으면 평탄화본을, 없으면 원본을 대상으로.
    # 원본 self._item·벡터 주석은 씬에 그대로 남아 재편집·undo 가능.
    # ------------------------------------------------------------------
    def _has_annotations(self) -> bool:
        return any(it is not self._bg_item for it in self._scene.items())

    def _effective_item(self) -> ClipboardItem:
        if self._pixmap_ok and self._has_annotations():
            png = flatten_scene_to_png(self._scene)
            return ClipboardItem(content_type="image", image_data=png)  # id=None(임시)
        return self._item

    # ------------------------------------------------------------------
    # 우클릭 메뉴 (뷰어 모드) — 복사 / OCR / 주석 편집 / 닫기
    # ------------------------------------------------------------------
    def contextMenuEvent(self, event):
        if self._edit_mode:
            return  # 편집 모드에선 그리기 우선 (메뉴 없음)
        target = self._effective_item()  # 주석 있으면 평탄화본, 없으면 원본
        menu = QMenu(self)
        menu.setStyleSheet(_dark_menu_style())
        if target is self._item:
            menu.addAction("복사").triggered.connect(lambda: self.copy_requested.emit(target))
        else:
            # 평탄화본(임시, id 없음)의 복사는 주석 편집 완료 복사와 동일 경로로 —
            # 클립보드 + 히스토리 저장 + 토스트. copy_requested는 DB 항목을 전제해
            # id 없는 항목이면 히스토리에 안 남고 피드백도 없다.
            menu.addAction("복사").triggered.connect(
                lambda: self.annotated_copy_requested.emit(target.image_data))
        menu.addAction("텍스트 추출(OCR)").triggered.connect(lambda: self.ocr_requested.emit(target))
        menu.addAction("AI에게 질문").triggered.connect(lambda: self.ai_requested.emit(target))
        menu.addAction("파일로 저장 후 경로 복사").triggered.connect(
            lambda: self.copy_as_path_requested.emit(target))
        menu.addAction("주석 편집").triggered.connect(self.toggle_edit_mode)
        menu.addSeparator()
        menu.addAction("닫기").triggered.connect(self.close)
        menu.exec(event.globalPos())

    # ------------------------------------------------------------------
    # 키 — Space/ESC는 뷰가 위임. 여기선 팝업이 포커스일 때의 백업 처리.
    # ------------------------------------------------------------------
    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Space:
            self.toggle_edit_mode()
            return
        if event.key() == Qt.Key.Key_Escape:
            self._on_escape()
            return
        super().keyPressEvent(event)

    # ------------------------------------------------------------------
    # 활성/비활성 테두리 (이미지 뷰 테두리 색)
    # ------------------------------------------------------------------
    def _apply_active_style(self, active: bool):
        # 활성(보고 있는 창) = 코랄(주인공), 비활성 = 중립 회색(존재만 표시, 안 튐).
        # 공유 스타일시트의 `QWidget{background:_BG}`가 팝업 자신에도 적용돼 투명 strip을 덮으므로,
        # ID 선택자로 팝업 본체 배경만 투명으로 되돌린다(자식 chrome·뷰 배경은 그대로 불투명).
        self.setStyleSheet(
            self._editor_stylesheet(_PEACH if active else _SURFACE2)
            + "\nQWidget#previewroot { background: transparent; }"
        )

    def changeEvent(self, event):
        if event.type() == QEvent.Type.ActivationChange:
            self._apply_active_style(self.isActiveWindow())
        super().changeEvent(event)

    # ------------------------------------------------------------------
    # 생명주기
    # ------------------------------------------------------------------
    def closeEvent(self, event):
        if self._eyedrop_active:
            self._stop_eyedropper(False)
        type(self)._instances = [p for p in type(self)._instances if p is not self]
        super().closeEvent(event)
