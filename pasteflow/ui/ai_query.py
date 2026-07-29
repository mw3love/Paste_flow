"""AI 질의 입력 다이얼로그 — PowerToys Run/Raycast식 팔레트.

Alt+` 자유질문 단축키 → 이 다이얼로그로 질문을 입력받아 여러 목적지(기본은 Google AI
모드 하나, 사용자가 설정에서 추가한 웹사이트) 중 하나로 라우팅한다(목록·URL 빌더는
`pasteflow/ai_palette.py`). v1.6x에서 우클릭 "AI에게 질문"·여러 모델 비교·기록·구글
드라이브 연동을 통째로 제거했다 — 실사용 결과 Google AI 텍스트검색만 견고했고, 나머지는
과도했다(사용자 판단, 2026-07-29). 고르는 방법 셋을 동시에 지원한다:

- `Tab`/`Shift+Tab` — 하이라이트(칩)를 순환한다.
- `Alt+1~9` — 그 번호의 타겟으로 즉시 전송한다(하이라이트 이동 없이).
- 등록된 키워드+공백으로 문장을 시작하면(예: `"yt 고양이"`) 자동으로 그 타겟이
  하이라이트되고, 실행 시 키워드 접두어는 질의에서 잘려 나간다.
- `Enter`/`Ctrl+Enter` — 지금 하이라이트된 타겟으로 전송.

Shift+Enter는 줄바꿈, Esc는 취소. 이미지는 Ctrl+V/드래그로 첨부하되 **1장만** 유지한다
(Google AI 모드가 이미지 1장만 지원 — 2026-07-29 사용자 확인).
"""

from PyQt6.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPlainTextEdit,
    QPushButton, QWidget,
)
from PyQt6.QtCore import Qt, QBuffer, QByteArray, QIODevice, QTimer
from PyQt6.QtGui import QCursor, QPixmap, QImage

from pasteflow import ai_palette
from pasteflow.ui.theme import COLORS


class _QuestionEdit(QPlainTextEdit):
    """Tab/Shift+Tab=타겟 순환, Alt+1~9=즉시전송, Enter/Ctrl+Enter=실행,
    Shift+Enter=줄바꿈. Ctrl+V/드롭으로 이미지 첨부.
    """

    def __init__(self, on_submit, on_tab, on_alt_number,
                 on_image_paste=None, parent=None):
        super().__init__(parent)
        self._on_submit = on_submit
        self._on_tab = on_tab              # on_tab("up"/"down")
        self._on_alt_number = on_alt_number  # on_alt_number(1~9)
        self._on_image_paste = on_image_paste
        self.setAcceptDrops(True)

    def keyPressEvent(self, event):
        key = event.key()
        mods = event.modifiers()

        # Tab/Shift+Tab — 기본 동작(탭 문자 삽입·포커스 이동)을 막고 타겟 순환에 쓴다.
        # ⚠ Shift+Tab은 플랫폼에 따라 Key_Backtab으로 오거나 Key_Tab+ShiftModifier로도
        # 온다(헤드리스 QTest 실측 — 후자였음) — 둘 다 받아야 실제 키보드에서 안전하다.
        if key == Qt.Key.Key_Tab:
            self._on_tab("up" if mods & Qt.KeyboardModifier.ShiftModifier else "down")
            return
        if key == Qt.Key.Key_Backtab:
            self._on_tab("up")
            return

        # Alt+1~9 — 그 번호 타겟으로 즉시 전송.
        if (mods & Qt.KeyboardModifier.AltModifier
                and Qt.Key.Key_1 <= key <= Qt.Key.Key_9):
            self._on_alt_number(key - Qt.Key.Key_0)
            return

        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if mods & Qt.KeyboardModifier.ShiftModifier:
                super().keyPressEvent(event)
                return
            self._on_submit()
            return
        super().keyPressEvent(event)

    def insertFromMimeData(self, source):
        """붙여넣기(Ctrl+V)·드롭 시 이미지면 텍스트 대신 첨부한다 — 이 다이얼로그의
        유일한 첨부 경로(전용 버튼 없음, v1.49.1). 최신 1장만 유지한다(v1.6x).

        - 원본 이미지(그림 캡처 등)면 그대로 첨부.
        - 로컬 이미지 '파일'을 복사/드롭했으면(경로만 들어옴) 첫 장을 읽어 첨부한다.
        어느 쪽도 아니면 기본 동작(텍스트 삽입).
        """
        if self._on_image_paste is not None:
            if source.hasImage():
                img = source.imageData()
                if hasattr(img, "toImage"):  # QPixmap로 올 때
                    img = img.toImage()
                if isinstance(img, QImage) and not img.isNull():
                    self._on_image_paste(img)
                    return
            if source.hasUrls():
                for url in source.urls():
                    if url.isLocalFile():
                        fimg = QImage(url.toLocalFile())
                        if not fimg.isNull():
                            self._on_image_paste(fimg)
                            return
        super().insertFromMimeData(source)


class AiQueryDialog(QDialog):
    """AI 질문 입력 — 질문과 보낼 타겟을 받는다(단일 이미지 첨부 가능)."""

    # 마지막으로 실행한 타겟 인덱스 — 세션(프로세스) 내에서 다음 질문창의 초기 하이라이트로
    # 재사용한다(image_annotator의 "마지막 값 기억" 관례와 동일, DB 저장은 아님).
    _last_site_index: int = 0

    def __init__(self, parent=None, sites: "list[dict] | None" = None):
        super().__init__(parent)
        # 타겟 팔레트 — main이 ai_palette.load_sites()로 읽어 넘긴다. 여기선 필터링 없이
        # 하이라이트·실행만 담당(설정 편집은 settings_dialog 몫).
        self._sites: list[dict] = list(sites) if sites else ai_palette.load_sites("")
        self._tab_index = (
            type(self)._last_site_index
            if 0 <= type(self)._last_site_index < len(self._sites) else 0)
        self._chips: list[QPushButton] = []
        self._result_index: "int | None" = None
        self._result_query: str = ""
        self.setWindowTitle("Gemini에게 질문")
        # 항상 위 — 패널이 TOPMOST라(panel._set_always_on_top) 일반 창은 Windows Z-order상
        # 패널 아래에 깔려 클릭해도 앞으로 나오지 못한다(부모를 패널로 두던 시절엔 '소유 창은
        # 소유자 위'라는 별개 규칙에 얹혀 가려지지 않았지만, 그 소유 관계가 바로 패널을 못 누르게
        # 하던 원인이라 끊었다). 미리보기 팝업과 같은 TOPMOST 그룹에 올리면 그룹 안에서는
        # 활성화 순서가 통해 '클릭한 창이 앞으로' 온다.
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        # 비모달 — 질문창이 떠 있어도 패널·영역 캡처(Alt+F2)를 그대로 쓸 수 있어야 한다
        # (질문 내용은 이 다이얼로그가 다 들고 있어 다른 창을 잠글 이유가 없다).
        # ⚠ 이 설정만으로는 부족하다 — **호출자가 `exec()`가 아니라 `show()`로 띄워야 한다.**
        # `exec()`는 창을 모달로 표시하는데, 모달리티가 NonModal이고 부모도 없으면 Qt가
        # ApplicationModal로 승격시켜 이 줄을 무력화한다(2026-07-13 PyQt6 실측). 그래서
        # main은 `_open_ai_dialog`에서 show() + finished 콜백으로 띄운다.
        # 포커스를 뺏기며 패널이 자동으로 숨는 것은 panel.py의 changeEvent 예외 목록
        # (AiQueryDialog 포함)이 막는다.
        self.setWindowModality(Qt.WindowModality.NonModal)
        # 취소 버튼 제거(우상단 X·Esc로 충분)로 그만큼 최소 높이를 줄였다(200→160).
        self.setMinimumSize(420, 160)
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {COLORS['base']};
                color: {COLORS['text']};
            }}
            QLabel {{
                color: {COLORS['subtext0']};
                font-size: 11px;
            }}
            QPlainTextEdit {{
                background-color: {COLORS['surface0']};
                color: {COLORS['text']};
                border: 1px solid {COLORS['surface2']};
                border-radius: 6px;
                padding: 6px;
                font-size: 13px;
            }}
            QPlainTextEdit:focus {{
                border: 1px solid {COLORS['peach']};
            }}
            QPushButton {{
                background-color: {COLORS['surface1']};
                color: {COLORS['text']};
                border: none;
                border-radius: 6px;
                padding: 6px 16px;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background-color: {COLORS['surface2']};
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(12, 12, 12, 12)

        # 첨부 이미지(PNG bytes, 최대 1장) — 질문칸 Ctrl+V·드롭으로 붙일 수 있다.
        # 질의 시 멀티모달로 함께 전송된다(Google AI 모드는 1장만 지원).
        self._images: list[bytes] = []

        # 이미지 미리보기 — 첨부가 있을 때만 보인다.
        self._img_caption = QLabel("")
        self._img_strip = QWidget()
        self._img_strip_layout = QHBoxLayout(self._img_strip)
        self._img_strip_layout.setContentsMargins(0, 0, 0, 0)
        self._img_strip_layout.setSpacing(6)
        layout.addWidget(self._img_caption)
        layout.addWidget(self._img_strip)

        self._refresh_image_preview()

        self._editor = _QuestionEdit(
            self._on_submit, self._on_tab, self._on_alt_number,
            on_image_paste=self._on_image_pasted)
        # 설정창 "빠른 검색" 그룹의 설명 문구를 없앤 대신(2026-07-29), 실제로 쓰는 이
        # 자리에 사용법을 옮겨왔다 — 내용이 늘어난 만큼 불릿+들여쓰기로 가독성을 준다.
        self._editor.setPlaceholderText(
            "질문을 입력하세요\n\n"
            "  •  Tab / Shift+Tab — 타겟 전환\n"
            "  •  Alt+숫자 — 그 타겟으로 즉시 전송\n"
            "  •  Enter — 실행 (Shift+Enter는 줄바꿈)\n"
            "  •  Ctrl+V / 드래그 — 이미지 첨부(1장)\n"
            "  •  키워드+공백으로 시작 — 예: yt 강아지")
        self._editor.setFocus()
        layout.addWidget(self._editor, 1)

        # 타겟 칩 로우(PowerToys Run/Raycast식) — 번호(Alt+숫자)+라벨, 하이라이트=코랄.
        # 클릭하면 그 타겟으로 즉시 실행(마우스 전용 경로).
        chip_row = QHBoxLayout()
        chip_row.setContentsMargins(0, 0, 0, 0)
        chip_row.setSpacing(4)
        for i, site in enumerate(self._sites[:9]):
            btn = QPushButton(f"{i + 1} {site.get('label', '')}".strip())
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            kw = (site.get("keyword") or "").strip()
            tip = f"Alt+{i + 1}로 즉시 전송"
            if kw:
                tip += f" · \"{kw} \"로 문장을 시작하면 자동 선택"
            btn.setToolTip(tip)
            btn.clicked.connect(lambda _checked=False, idx=i: self._execute(idx))
            chip_row.addWidget(btn)
            self._chips.append(btn)
        chip_row.addStretch(1)
        layout.addLayout(chip_row)
        self._editor.textChanged.connect(self._update_highlight)

        # 취소 버튼은 두지 않는다 — 우상단 X(네이티브 타이틀바 닫기)와 Esc(QDialog 기본
        # reject())가 이미 같은 역할을 한다(2026-07-29). 버튼 행이 차지하던 세로 공간은
        # setMinimumSize에서 함께 줄였다.
        self._update_highlight()  # 초기 하이라이트 반영

    def showEvent(self, event):
        """첫 표시 시 커서가 있는 모니터 정중앙으로 이동하고, 즉시 타이핑 가능하도록
        창을 포그라운드로 활성화한 뒤 입력칸에 포커스를 준다.

        - 위치: 알림창처럼 커서가 있는 모니터 한복판(듀얼/트리플 모니터 대응). QDialog
          기본 부모(패널) 중앙 정렬이나 커서 옆 배치는 다른 모니터에서 띄울 때 시선이
          분산돼, 답변창과 동일하게 활성 모니터 중앙으로 통일한다.
        - 포커스: PasteFlow는 백그라운드 상주 앱이라 단축키로 띄운 창이 포그라운드를
          못 가져와 한 번 클릭해야 타이핑되던 문제를 강제 활성화로 해결한다.

        **Alt+백틱 두 번 눌러야 열리던 문제(2026-07-28)**: 첫 호출은 위젯 트리·거대한
        스타일시트를 새로 구성하는 '느린 경로'라, 그 사이 사용자가 Alt/백틱을 이미 뗀
        뒤에 `_force_foreground()`가 실행돼 포그라운드 획득 타이밍을 놓치는 경우가 있었다
        (재현: 두 번째 누름은 기존 인스턴스를 `raise_/activateWindow`만 하는 '빠른 경로'라
        항상 성공). 그 성공 패턴을 사용자의 두 번째 물리 입력 없이 자동으로 흉내내도록,
        표시 직후 짧은 지연을 두고 포그라운드 획득을 한 번 더 시도한다(``_retry_foreground``).
        """
        super().showEvent(event)
        if not getattr(self, "_positioned", False):
            self._positioned = True
            cursor = QCursor.pos()
            screen = QApplication.screenAt(cursor) or QApplication.primaryScreen()
            avail = screen.availableGeometry()
            w, h = self.width(), self.height()
            x = min(max(avail.center().x() - w // 2, avail.left()), avail.right() - w)
            y = min(max(avail.center().y() - h // 2, avail.top()), avail.bottom() - h)
            self.move(x, y)
        self._force_foreground()
        self.raise_()
        self.activateWindow()
        self._editor.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
        QTimer.singleShot(120, self._retry_foreground)

    def _retry_foreground(self):
        """showEvent 직후 한 번 더 포그라운드 획득을 시도한다(사용자의 '두 번째 누름'과
        동일한 효과 — 위 showEvent docstring 참고). 그 사이 창이 이미 닫혔으면 아무것도
        하지 않는다."""
        if not self.isVisible():
            return
        self._force_foreground()
        self.raise_()
        self.activateWindow()
        if not self._editor.hasFocus():
            self._editor.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

    def _force_foreground(self):
        """백그라운드 앱이 띄운 창에 포그라운드 포커스를 강제로 가져온다(Windows).

        Windows의 포그라운드 잠금(다른 앱이 포그라운드일 때 SetForegroundWindow 무시)을
        AttachThreadInput으로 우회한다 — 패널 드래그 붙여넣기에서 쓰는 것과 동일 기법.
        """
        try:
            import ctypes
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            hwnd = int(self.winId())
            fg = user32.GetForegroundWindow()
            cur_tid = kernel32.GetCurrentThreadId()
            fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
            if fg_tid and fg_tid != cur_tid:
                user32.AttachThreadInput(fg_tid, cur_tid, True)
                user32.SetForegroundWindow(hwnd)
                user32.AttachThreadInput(fg_tid, cur_tid, False)
            else:
                user32.SetForegroundWindow(hwnd)
        except Exception:
            pass

    # ── 타겟 팔레트 ───────────────────────────────────────────────────────────
    def _query_for(self, index: int) -> str:
        """이 타겟으로 보낼 질의 텍스트 — 그 타겟의 keyword+공백으로 시작하면 잘라낸다."""
        text = self._editor.toPlainText().strip()
        if not (0 <= index < len(self._sites)):
            return text
        kw = (self._sites[index].get("keyword") or "").strip()
        if kw and text.startswith(kw + " "):
            return text[len(kw) + 1:].strip()
        return text

    def _effective_index(self) -> int:
        """지금 이 순간 Enter를 누르면 갈 타겟 — 키워드 접두어가 매치하면 그쪽이
        Tab으로 고른 하이라이트보다 우선한다(타이핑만으로 자연스럽게 전환)."""
        m = ai_palette.match_keyword(self._sites, self._editor.toPlainText())
        if m is not None:
            return m[0]
        return self._tab_index

    def _on_tab(self, direction: str):
        n = len(self._sites)
        if n == 0:
            return
        self._tab_index = (self._tab_index + (1 if direction == "down" else -1)) % n
        self._update_highlight()

    def _on_alt_number(self, n: int):
        self._execute(n - 1)

    def _on_submit(self):
        self._execute(self._effective_index())

    def _execute(self, index: int):
        if not (0 <= index < len(self._sites)):
            return
        query = self._query_for(index)
        if not query:
            return
        self._result_index = index
        self._result_query = query
        type(self)._last_site_index = index
        self.accept()

    def _update_highlight(self):
        """칩 스타일을 지금 유효한 타겟에 맞춰 갱신한다."""
        idx = self._effective_index()
        for i, btn in enumerate(self._chips):
            self._style_chip(btn, i == idx)

    def _style_chip(self, btn: QPushButton, active: bool):
        if active:
            btn.setStyleSheet(
                f"QPushButton {{ background-color: {COLORS['peach']}; color: {COLORS['base']}; "
                f"border: none; border-radius: 6px; padding: 4px 10px; font-size: 11px; "
                f"font-weight: 600; }}")
        else:
            btn.setStyleSheet(
                f"QPushButton {{ background-color: {COLORS['surface1']}; color: {COLORS['text']}; "
                f"border: none; border-radius: 6px; padding: 4px 10px; font-size: 11px; }}"
                f"QPushButton:hover {{ background-color: {COLORS['surface2']}; }}")

    def get_result(self) -> "tuple[dict, str] | None":
        """실행하기로 한 (타겟 딕셔너리, 질의 텍스트). 취소했으면 `None`."""
        if self._result_index is None:
            return None
        return self._sites[self._result_index], self._result_query

    def get_images(self) -> list[bytes]:
        """질문과 함께 보낼 이미지(최대 1장, PNG bytes 리스트). 없으면 빈 리스트."""
        return list(self._images)

    # ── 이미지 첨부 ────────────────────────────────────────────────────────────
    @staticmethod
    def _qimage_to_png(image: QImage) -> bytes | None:
        """QImage → PNG bytes. 파이프라인이 PNG를 기대하므로 원본 포맷과 무관하게 통일."""
        if image is None or image.isNull():
            return None
        # ⚠ QByteArray를 지역변수로 잡아 살려둔다 — QBuffer는 이걸 참조로만 쓰므로,
        # QBuffer(QByteArray())처럼 임시객체를 넘기면 GC 후 dangling → save 시 크래시.
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        ok = image.save(buf, "PNG")
        buf.close()
        return bytes(ba) if ok else None

    def _add_image(self, png: bytes | None) -> bool:
        """PNG bytes를 첨부로 설정하고 미리보기를 갱신한다.

        **1장만 유지한다**(v1.6x) — Google AI 모드가 이미지를 1장만 받아 준다고
        확인됐다(2026-07-29 사용자 실사용). 새로 첨부하면 이전 것을 교체한다.
        """
        if not png:
            return False
        self._images = [png]
        self._refresh_image_preview()
        return True

    def attach_image_bytes(self, png: bytes) -> bool:
        """패널에서 이미지 항목을 이 창으로 드래그해 놓았을 때 직접 첨부한다.

        Ctrl+V/드롭(`_on_image_pasted`)과 달리 클립보드를 거치지 않는다 — 패널의
        드래그는 실제 OS 드래그앤드롭이 아니라 마우스 위치를 추적하는 자체 구현(fake
        drag)이라 Qt의 `insertFromMimeData`가 발동하지 않는다(main._on_drag_to_app이
        놓인 지점이 이 창 위인지 직접 판정해 호출).
        """
        return self._add_image(png)

    def _on_image_pasted(self, image: QImage):
        """질문칸에 Ctrl+V/드롭된 이미지를 첨부한다(_QuestionEdit 콜백)."""
        self._add_image(self._qimage_to_png(image))

    def _remove_image(self):
        self._images = []
        self._refresh_image_preview()

    def _refresh_image_preview(self):
        """첨부 이미지에 맞춰 썸네일을 갱신한다 — 썸네일 우상단 ✕ 배지로 제거한다."""
        # 기존 썸네일 위젯 제거
        while self._img_strip_layout.count():
            item = self._img_strip_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        has_img = bool(self._images)
        _THUMB = 64
        _BADGE = 18
        if has_img:
            pix = QPixmap()
            if pix.loadFromData(self._images[0]) and not pix.isNull():
                thumb = pix.scaled(_THUMB, _THUMB, Qt.AspectRatioMode.KeepAspectRatio,
                                   Qt.TransformationMode.SmoothTransformation)

                cell = QWidget()
                cell.setFixedSize(_THUMB, _THUMB)
                img_label = QLabel(cell)
                img_label.setGeometry(0, 0, _THUMB, _THUMB)
                img_label.setPixmap(thumb)
                img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

                remove_btn = QPushButton("✕", cell)
                remove_btn.setGeometry(_THUMB - _BADGE, 0, _BADGE, _BADGE)
                remove_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                remove_btn.setToolTip("이미지 제거")
                remove_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                remove_btn.setStyleSheet(f"""
                    QPushButton {{
                        background-color: rgba(0, 0, 0, 170);
                        color: white;
                        border: none;
                        border-radius: {_BADGE // 2}px;
                        font-size: 10px;
                        font-weight: 700;
                        padding: 0px;
                    }}
                    QPushButton:hover {{
                        background-color: {COLORS['peach']};
                        color: {COLORS['base']};
                    }}
                """)
                remove_btn.clicked.connect(lambda _c: self._remove_image())
                remove_btn.raise_()

                self._img_strip_layout.addWidget(cell)
            else:
                has_img = False
        self._img_strip_layout.addStretch(1)

        if has_img:
            self._img_caption.setText("이미지 1장 (질문과 함께 전송):")
        self._img_caption.setVisible(has_img)
        self._img_strip.setVisible(has_img)
