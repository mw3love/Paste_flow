"""이미지 미리보기 + 인라인 주석 편집 (통합 단일 창, Snipaste식).

평소엔 가벼운 뷰어(창이 이미지에 딱 맞게 리사이즈 = hug-zoom, 레터박스 없음). Space를 누르면
같은 창에서 편집 툴바가 펴진다(편집 모드). 편집 컴포넌트는 image_annotator.py에서 가져와 host.

- 뷰어 모드: 좌클릭 드래그 = 창 이동, 휠 = 줌(창 리사이즈), 더블클릭/ESC = 닫기,
  우클릭 = 복사/OCR/주석 편집/닫기.
- 편집 모드: 툴바·액션바 표시, 그리기/선택/크기조절, 창 이동은 상단 핸들로만. ESC = 편집 종료.
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QGraphicsScene, QGraphicsView, QFrame,
    QMenu, QApplication, QToolButton,
)
from PyQt6.QtCore import Qt, QPoint, QRect, QRectF, QSize, QEvent, pyqtSignal

from pasteflow.ui.theme import (
    BASE as _BG, SURFACE0 as _SURFACE0, SURFACE1 as _BORDER, SURFACE2 as _SURFACE2,
    TEXT as _TEXT, PEACH as _PEACH,
)
from pasteflow.models import ClipboardItem
from pasteflow.ui.image_annotator import (
    _EditorMixin, _AnnotatorView, _pixmap_from_data, _tool_icon,
    flatten_scene_to_png,
)

PREVIEW_MAX_W = 640
PREVIEW_MAX_H = 480
_PREVIEW_MARGIN = 8
_CASCADE_STEP = 24
_CASCADE_NEAR = 150  # 이 거리(px) 안에 있는 기존 창만 cascade 대상으로 셈

# 팝업 본체·chrome strip은 투명(뒤 비침), 툴바만 Snipaste식 밝은 pill로 띄운다.
# 공유 스타일시트의 `QWidget{background:_BG}`(검정)가 이것들까지 덮으므로 ID 선택자로 되돌린다.
# 툴바(toolbarhost)는 밝은 바 + 어두운 아이콘(플랫 버튼) — 활성 도구만 옅은 파랑으로 강조.
_CHROME_QSS = (
    "\nQWidget#previewroot, QWidget#previewchrome { background: transparent; }"
    "\nQWidget#toolbarhost {"
    " background-color: #f4f4f4; border: 1px solid #cfcfcf; border-radius: 8px; }"
    "\nQWidget#toolbarhost QToolButton {"
    " background-color: transparent; border: none; border-radius: 5px; padding: 3px; }"
    "\nQWidget#toolbarhost QToolButton:hover { background-color: #e2e2e2; }"
    "\nQWidget#toolbarhost QToolButton:checked { background-color: #cfe3ff; }"
)


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
                 native: bool = False, place_rect: QRect | None = None) -> "ImagePreviewPopup":
        # native=True면 이미지를 원본 픽셀 크기(1:1)로 띄운다(화면 초과 시만 축소).
        # 캡처(Alt+F2)→핀(Alt+F3) 시 "캡처한 크기 그대로" 보이게 하는 용도.
        # place_rect(논리 전역)가 주어지면 그 사각형에 이미지를 정확히 덮고 등장 반짝을 준다(핀 제자리).
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
        popup.show_preview(panel_geom, cascade_offset, native=native, place_rect=place_rect)
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
        # 화살표 방향 토글 버튼을 '선택된 화살표 근처'에 두므로, 선택·이동 시 재배치한다.
        # (selectionChanged=선택 변화, changed=아이템 이동/편집. 줌은 _apply_zoom에서 호출.)
        self._scene.selectionChanged.connect(self._update_arrow_dir_btn)
        self._scene.changed.connect(lambda *a: self._update_arrow_dir_btn())
        self.set_tool(None)  # 손 모드(도구 없음)로 시작 — 편집 진입 기본
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

        # chrome(툴바)은 레이아웃이 아니라 이미지 아래에 '떠 있는' 자식 컨테이너로 둔다.
        # 편집 토글 시 보이기/숨기기만 하므로 창 크기·위치가 안 바뀌고(= 잔상 0), 이미지도 안
        # 움직인다. Snipaste식으로 툴바는 이미지 하단 예약 strip에 뜬다. 타이틀바(드래그 핸들)는
        # 없앴다 — 창 이동은 휠(가운데)클릭 드래그가 담당(_AnnotatorView), 닫기는 우상단 floating.
        self._chrome = QWidget(self)
        self._chrome.setObjectName("previewchrome")
        self._chrome.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        chrome_l = QVBoxLayout(self._chrome)
        # 여백을 줘서 밝은 pill이 strip 안에 '떠 있게'(우측·상하 gap) 배치.
        chrome_l.setContentsMargins(0, 4, 6, 4)
        chrome_l.setSpacing(0)

        # 툴바 — AlignRight로 내용에 딱 맞는 compact pill을 우측에 띄운다(full-width 바 아님).
        self._toolbar_host = QWidget()
        self._toolbar_host.setObjectName("toolbarhost")
        self._toolbar_host.setLayout(self._build_toolbar())
        chrome_l.addWidget(self._toolbar_host, 0, Qt.AlignmentFlag.AlignRight)

        # 닫기 ✕ — 이미지 우상단 안쪽 floating(편집 모드에서만 표시). 반투명 검정 원 위 흰 X.
        self._edit_close_btn = QToolButton(self)
        self._edit_close_btn.setObjectName("editclose")
        self._edit_close_btn.setIcon(_tool_icon("close", neutral_override="#ffffff"))
        self._edit_close_btn.setIconSize(QSize(16, 16))
        self._edit_close_btn.setFixedSize(26, 26)
        self._edit_close_btn.setToolTip("닫기 (ESC)")
        self._edit_close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._edit_close_btn.clicked.connect(self.close)
        self._edit_close_btn.setVisible(False)

        # 이미지 뷰 — 레이아웃의 유일한 위젯이라 창이 이미지에 딱 맞는다(hug). chrome은 떠 있어
        # 레이아웃에 영향을 주지 않으므로, 편집 토글로 창 높이가 바뀌지 않는다.
        root.addWidget(self._view, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

        self._chrome.setVisible(False)  # 뷰어 모드 시작
        # 하단 strip(=chrome 높이)을 항상 예약(bottom 마진) — 이미지는 창 상단에 배치되고,
        # 빈 strip은 투명(WA_TranslucentBackground)이라 뷰어 모드에선 안 보인다. 편집 토글로
        # 창 크기·위치가 안 바뀌어 잔상이 없고, 툴바는 strip 안에 떠서 이미지를 덮지 않는다.
        self._chrome_h = self._chrome.sizeHint().height()
        root.setContentsMargins(0, 0, 0, self._chrome_h)

    # ------------------------------------------------------------------
    # 표시 + hug-zoom
    # ------------------------------------------------------------------
    def show_preview(self, panel_geom: QRect, cascade_offset: int = 0, native: bool = False,
                     place_rect: QRect | None = None):
        if not self._pixmap_ok:
            return
        if place_rect is not None and not place_rect.isEmpty():
            self._show_in_place(place_rect)
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
            # 이미지가 창 좌상단에 오고 투명 strip은 하단이라, 창 위치 = 이미지 위치.
            pos = compute_preview_pos(panel_geom, self.size(), screen, cascade_offset)
            self.move(pos)
        self.show()
        self.raise_()
        # 활성화 — 이후 Space(편집 토글)·ESC(닫기)를 미리보기가 직접 받도록
        self.activateWindow()

    def _show_in_place(self, place_rect: QRect):
        """캡처한 논리 전역 사각형(place_rect)에 이미지를 1:1로 정확히 덮어 띄운다(핀 제자리).

        줌은 픽맵 픽셀→place_rect 논리폭 비율로 잡아 DPI 배율과 무관하게 딱 맞춘다. 이미지는
        창 좌상단에 놓이고 투명 strip은 하단이라, 창 좌상단 = place_rect 좌상단이다.
        등장 시 테두리를 1회 반짝여 '떠 있는 핀'임을 알린다.
        """
        sr = self._scene.sceneRect()
        z = place_rect.width() / sr.width() if sr.width() > 0 else 1.0
        self._apply_zoom(z)
        # 이미지 좌상단 = 창 좌상단이므로 place_rect 좌상단에 그대로 배치.
        self.move(place_rect.left(), place_rect.top())
        self.show()
        self.raise_()
        self.activateWindow()
        self._flash_border()

    def _flash_border(self):
        """등장 시 1회 반짝 — 밝은 흰 테두리로 켰다가 코랄로 가라앉힌다(상시 깜빡임 아님).

        `_flashing` 동안 changeEvent(활성화 변화)가 흰 테두리를 코랄로 덮지 않게 막는다
        — activateWindow()가 큐잉하는 ActivationChange가 반짝을 즉시 지우는 것을 방지.
        """
        from PyQt6.QtCore import QTimer
        self._flashing = True
        self.setStyleSheet(
            self._editor_stylesheet("#ffffff")
            + _CHROME_QSS
        )
        QTimer.singleShot(160, self._end_flash)

    def _end_flash(self):
        self._flashing = False
        try:  # 160ms 안에 창을 닫으면(WA_DeleteOnClose) C++ 객체가 이미 삭제됨 — 무시
            self._apply_active_style(self.isActiveWindow())
        except RuntimeError:
            pass

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
        # 편집 중이면 떠 있는 chrome을 이미지 하단에 다시 배치(줌으로 크기가 바뀌었을 수 있음)
        if getattr(self, "_chrome", None) is not None and self._chrome.isVisible():
            self._layout_chrome()
            self._layout_edit_close()
            self._update_arrow_dir_btn()  # 줌으로 화살표 위치가 바뀌면 방향 버튼도 따라가게

    def _visible_global_rect(self, width: int) -> QRect:
        """창(=이미지) 전역 사각형(폭 width)과 이 창이 놓인 화면 가용영역(작업표시줄 제외)의
        교집합 = 실제로 보이는 부분(전역 좌표). 교집합이 비면(창이 화면 밖) 창 전체로 폴백."""
        win_global = QRect(self.x(), self.y(), width, self.height())
        screen = self.screen() or QApplication.primaryScreen()
        if screen is not None:
            inter = win_global.intersected(screen.availableGeometry())
            if not inter.isEmpty():
                return inter
        return win_global

    def _layout_chrome(self):
        """편집 모드에서 chrome(툴바)을 배치한다. chrome은 창의 자식이라 창 영역
        [이미지 top ~ 하단 strip] 밖(이미지 위 빈 공간)으로는 못 나가고 클리핑된다 → 그 안에서만
        배치한다. 이미지가 화면에 다 들어오면 이미지 아래 예약 strip(=따라가기), 이미지가 화면을
        넘쳐 그 strip이 화면 밖으로 밀리면 '이미지 ∩ 화면(보이는 영역)'의 바닥으로 끌어와 이미지
        하단에 겹쳐 띄운다(항상 화면 안=조작 가능). 가로는 pill 우측을 이미지 우측에 맞추되 화면
        밖으로 안 나가게 클램프. 툴바가 이미지보다 넓으면 창 '폭만' 늘린다(top-left 고정=잔상 없음).
        NOTE: Snipaste식 '이미지 밖 위쪽 빈 공간에 띄우기'는 자식 위젯이라 불가(별도 top-level 창 필요)."""
        pill_w = self._chrome.sizeHint().width()
        win_w = max(self._view.width(), pill_w)
        if self.width() != win_w:
            self.resize(win_w, self.height())  # 폭만, top-left 고정
        ch = self._chrome_h
        vis = self._visible_global_rect(win_w)          # 이미지 ∩ 화면(전역)
        # 세로: 기본은 이미지 아래(strip=창 하단). 그 strip이 화면 밖이면 보이는 영역 바닥으로 클램프.
        cy_g = min(self.y() + self.height(), vis.bottom() + 1) - ch
        # 가로: pill 우측을 이미지 우측에 맞추되 화면 밖으로 안 나가게.
        right_g = min(self.x() + win_w, vis.right() + 1)
        self._chrome.setGeometry(right_g - self.x() - win_w, cy_g - self.y(), win_w, ch)
        self._chrome.raise_()

    def _layout_edit_close(self):
        """닫기 ✕를 '보이는 영역(이미지∩화면)'의 우상단 안쪽에 배치. 이미지가 화면 위로
        넘쳐도(핀 오버플로) ✕가 화면 밖으로 올라가 안 눌리는 것을 막는다. 이미지가 다 보이면
        이미지 우상단(기존과 동일)."""
        btn = getattr(self, "_edit_close_btn", None)
        if btn is None:
            return
        vis = self._visible_global_rect(self._view.width())
        btn.move(vis.right() - self.x() - btn.width() - 8,
                 vis.top() - self.y() + 8)
        btn.raise_()

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
        self._edit_close_btn.setVisible(self._edit_mode)  # ✕는 편집 모드에서만
        if self._edit_mode:
            self.set_tool(None)  # 손 모드로 진입 — 빈곳 드래그=창 이동, 주석 위=선택·이동
            self.activateWindow()
        else:
            self._scene.clearSelection()
            self._view.setDragMode(QGraphicsView.DragMode.NoDrag)
        self._update_arrow_dir_btn()      # 뷰어 전환 시 floating 방향 토글 숨김
        self._update_text_opts_bar()      # 뷰어 전환 시 텍스트 옵션 바 숨김
        if self._edit_mode:
            self._layout_chrome()         # 이미지 하단 strip에 배치(+필요 시 폭만 확장)
            self._layout_edit_close()     # ✕를 이미지 우상단에 배치
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
            # 첫 실제 이동에서 툴바를 숨긴다 — 자식이라 창과 함께 딸려가 화면 밖으로 나갈 수
            # 있으므로(단순 클릭엔 이동이 없어 숨기지 않음 → 깜빡임 방지). 놓을 때 재배치·표시.
            if self._edit_mode and self._chrome.isVisible():
                self._chrome.hide()
                self._edit_close_btn.hide()
            self.move(global_pt - self._win_drag)

    def _win_drag_end(self):
        self._win_drag = None
        # 놓은 위치 기준으로 빈 공간을 다시 찾아 배치한 뒤 표시(Snipaste식 — 이동 중엔 숨어 있다
        # 놓을 때 제 위치를 찾아간다). 클릭만이라 숨긴 적 없으면 show/layout은 무해(idempotent).
        if self._edit_mode:
            self._layout_chrome()
            self._layout_edit_close()
            self._chrome.show()
            self._edit_close_btn.show()

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
        # 팝업 본체는 투명(뒤 비침), 툴바는 밝은 pill로 재지정한다(_CHROME_QSS — 아래 append).
        self.setStyleSheet(
            self._editor_stylesheet(_PEACH if active else _SURFACE2)
            + _CHROME_QSS
        )

    def changeEvent(self, event):
        if event.type() == QEvent.Type.ActivationChange and not getattr(self, "_flashing", False):
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
