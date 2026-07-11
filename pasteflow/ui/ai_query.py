"""AI 질의 입력 다이얼로그.

패널 우클릭 "AI에게 질문" → 이 다이얼로그로 질문을 입력받는다. 우클릭한 클립보드
항목 내용을 컨텍스트로 함께 보여줘 "무엇에 대해 묻는지" 확인할 수 있게 한다.
Enter로 전송, Shift+Enter 줄바꿈, Esc 취소.
"""

from PyQt6.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPlainTextEdit,
    QPushButton, QCheckBox, QFileDialog,
)
from PyQt6.QtCore import Qt, QBuffer, QByteArray, QIODevice
from PyQt6.QtGui import QCursor, QPixmap, QImage

from pasteflow.ui.theme import COLORS, PEACH_HOVER


class _QuestionEdit(QPlainTextEdit):
    """Enter=전송, Shift+Enter=줄바꿈. Ctrl+V/드롭으로 이미지 첨부."""

    def __init__(self, on_submit, on_image_paste=None, parent=None):
        super().__init__(parent)
        self._on_submit = on_submit
        self._on_image_paste = on_image_paste
        self.setAcceptDrops(True)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                super().keyPressEvent(event)
                return
            self._on_submit()
            return
        super().keyPressEvent(event)

    def insertFromMimeData(self, source):
        """붙여넣기(Ctrl+V)·드롭 시 이미지면 텍스트 대신 첨부한다.

        - 원본 이미지(그림 캡처 등)면 그대로 첨부.
        - 로컬 이미지 '파일'을 복사/드롭했으면(경로만 들어옴) 그 파일을 읽어 첨부.
        둘 다 아니면 기본 동작(텍스트 삽입).
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
    """AI 질문 입력 — 컨텍스트(우클릭 항목)를 위에 보여주고 질문을 받는다."""

    _CTX_PREVIEW_CHARS = 300

    def __init__(self, context_text: str, parent=None, context_image: bytes | None = None,
                 compare_models: list[dict] | None = None):
        super().__init__(parent)
        # compare_models는 (backend, model) spec dict 목록(v1.47.0 — 크로스 백엔드 비교).
        self._compare_models = [s for s in (compare_models or []) if s and s.get("model")]
        self.setWindowTitle("AI에게 질문")
        self.setMinimumSize(420, 240)
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {COLORS['base']};
                color: {COLORS['text']};
            }}
            QLabel {{
                color: {COLORS['subtext0']};
                font-size: 11px;
            }}
            QLabel#ctx {{
                background-color: {COLORS['surface0']};
                border: 1px solid {COLORS['surface2']};
                border-radius: 6px;
                padding: 6px;
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
            QPushButton[text="질문"] {{
                background-color: {COLORS['peach']};
                color: {COLORS['base']};
            }}
            QPushButton[text="질문"]:hover {{
                background-color: {PEACH_HOVER};
            }}
            QCheckBox {{
                color: {COLORS['subtext0']};
                font-size: 12px;
                spacing: 6px;
            }}
            QCheckBox::indicator {{
                width: 15px;
                height: 15px;
                border: 1px solid {COLORS['surface2']};
                border-radius: 3px;
                background-color: {COLORS['surface0']};
            }}
            QCheckBox::indicator:checked {{
                background-color: {COLORS['peach']};
                border-color: {COLORS['peach']};
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(12, 12, 12, 12)

        # 첨부 이미지(PNG bytes) — 우클릭 이미지 항목의 컨텍스트로 시작하거나, 아래 첨부
        # 버튼(클립보드/파일)으로 사용자가 붙인다. 질의 시 image_png로 멀티모달 전송된다.
        self._image: bytes | None = context_image

        ctx = (context_text or "").strip()
        # 이미지 미리보기 — self._image가 있을 때만 보인다. 첨부/제거 시 갱신.
        self._img_caption = QLabel("이미지 (질문과 함께 전송):")
        self._img_label = QLabel()
        self._img_label.setObjectName("ctx")
        self._img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._img_caption)
        layout.addWidget(self._img_label)

        # 첨부 버튼 행 — 클립보드 이미지 붙이기 / 파일에서 고르기 / 제거.
        attach_row = QHBoxLayout()
        attach_row.setContentsMargins(0, 0, 0, 0)
        attach_row.setSpacing(6)
        self._attach_clip_btn = QPushButton("📋 클립보드 이미지")
        self._attach_clip_btn.setToolTip("현재 클립보드의 이미지를 질문에 첨부합니다.")
        self._attach_clip_btn.clicked.connect(self._attach_from_clipboard)
        self._attach_file_btn = QPushButton("📁 파일…")
        self._attach_file_btn.setToolTip("이미지 파일을 골라 질문에 첨부합니다.")
        self._attach_file_btn.clicked.connect(self._attach_from_file)
        self._remove_img_btn = QPushButton("✕ 이미지 제거")
        self._remove_img_btn.clicked.connect(self._remove_image)
        for b in (self._attach_clip_btn, self._attach_file_btn, self._remove_img_btn):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
        attach_row.addWidget(self._attach_clip_btn)
        attach_row.addWidget(self._attach_file_btn)
        attach_row.addWidget(self._remove_img_btn)
        attach_row.addStretch(1)
        layout.addLayout(attach_row)

        self._refresh_image_preview()

        if not self._image and ctx:
            layout.addWidget(QLabel("선택한 항목(컨텍스트):"))
            preview = ctx[: self._CTX_PREVIEW_CHARS].replace("\n", " ")
            if len(ctx) > self._CTX_PREVIEW_CHARS:
                preview += " …"
            ctx_label = QLabel(preview)
            ctx_label.setObjectName("ctx")
            ctx_label.setWordWrap(True)
            layout.addWidget(ctx_label)

        layout.addWidget(QLabel("질문을 입력하세요 (Enter 전송 · Shift+Enter 줄바꿈):"))

        self._editor = _QuestionEdit(self._try_submit, on_image_paste=self._on_image_pasted)
        self._editor.setFocus()
        layout.addWidget(self._editor, 1)

        # 여러 모델 비교 체크박스 — 비교 모델이 2개 이상 설정됐을 때만 노출한다(1개면 무의미).
        # 켜면 이 질문을 설정된 모델들로 동시에 던져 답변창을 나란히 띄운다.
        self._compare_check: QCheckBox | None = None
        if len(self._compare_models) >= 2:
            self._compare_check = QCheckBox(
                f"🔀 여러 모델로 비교 ({len(self._compare_models)}개)")
            _blabel = {"official": "공식", "gateway": "게이트웨이"}
            self._compare_check.setToolTip(
                "이 질문을 아래 모델들로 동시에 질의해 답변을 나란히 비교합니다:\n"
                + "\n".join(
                    f"· {s['model']} ({_blabel.get(s.get('backend', ''), s.get('backend', ''))})"
                    for s in self._compare_models))
            layout.addWidget(self._compare_check)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("취소")
        cancel_btn.clicked.connect(self.reject)
        ask_btn = QPushButton("질문")
        ask_btn.setProperty("text", "질문")
        ask_btn.clicked.connect(self._try_submit)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(ask_btn)
        layout.addLayout(btn_row)

    def showEvent(self, event):
        """첫 표시 시 커서가 있는 모니터 정중앙으로 이동하고, 즉시 타이핑 가능하도록
        창을 포그라운드로 활성화한 뒤 입력칸에 포커스를 준다.

        - 위치: 알림창처럼 커서가 있는 모니터 한복판(듀얼/트리플 모니터 대응). QDialog
          기본 부모(패널) 중앙 정렬이나 커서 옆 배치는 다른 모니터에서 띄울 때 시선이
          분산돼, 답변창과 동일하게 활성 모니터 중앙으로 통일한다.
        - 포커스: PasteFlow는 백그라운드 상주 앱이라 단축키로 띄운 창이 포그라운드를
          못 가져와 한 번 클릭해야 타이핑되던 문제를 강제 활성화로 해결한다.
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

    def _try_submit(self):
        if self._editor.toPlainText().strip():
            self.accept()

    def get_question(self) -> str:
        return self._editor.toPlainText().strip()

    def is_compare(self) -> bool:
        """'여러 모델로 비교' 체크 여부. 체크박스가 없으면(모델 미설정) 항상 False."""
        return self._compare_check is not None and self._compare_check.isChecked()

    def get_image(self) -> bytes | None:
        """질문과 함께 보낼 이미지(PNG bytes). 없으면 None."""
        return self._image

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

    def _attach_from_clipboard(self):
        """현재 클립보드의 이미지를 첨부한다(없으면 안내)."""
        image = QApplication.clipboard().image()
        png = self._qimage_to_png(image) if image is not None else None
        if not png:
            self._img_caption.setText("클립보드에 이미지가 없습니다.")
            return
        self._image = png
        self._refresh_image_preview()

    def _attach_from_file(self):
        """이미지 파일을 골라 첨부한다(PNG로 정규화)."""
        path, _ = QFileDialog.getOpenFileName(
            self, "이미지 파일 선택", "",
            "이미지 (*.png *.jpg *.jpeg *.bmp *.gif *.webp);;모든 파일 (*.*)")
        if not path:
            return
        image = QImage(path)
        png = self._qimage_to_png(image)
        if not png:
            self._img_caption.setText("이미지를 읽지 못했습니다.")
            return
        self._image = png
        self._refresh_image_preview()

    def _on_image_pasted(self, image: QImage):
        """질문칸에 Ctrl+V/드롭된 이미지를 첨부한다(_QuestionEdit 콜백)."""
        png = self._qimage_to_png(image)
        if png:
            self._image = png
            self._refresh_image_preview()

    def _remove_image(self):
        self._image = None
        self._refresh_image_preview()

    def _refresh_image_preview(self):
        """self._image에 맞춰 미리보기·버튼 표시를 갱신한다."""
        has_img = self._image is not None
        if has_img:
            pix = QPixmap()
            if pix.loadFromData(self._image) and not pix.isNull():
                thumb = pix.scaled(
                    280, 180,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self._img_label.setPixmap(thumb)
                self._img_caption.setText("이미지 (질문과 함께 전송):")
            else:
                has_img = False
                self._image = None
        if not has_img:
            self._img_label.clear()
        self._img_caption.setVisible(has_img)
        self._img_label.setVisible(has_img)
        self._remove_img_btn.setVisible(has_img)
