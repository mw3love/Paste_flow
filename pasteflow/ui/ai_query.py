"""AI 질의 입력 다이얼로그 — PowerToys Run/Raycast식 팔레트.

패널 우클릭 "AI에게 질문" → 이 다이얼로그로 질문을 입력받는다. 우클릭한 클립보드
항목 내용을 컨텍스트로 함께 보여줘 "무엇에 대해 묻는지" 확인할 수 있게 한다.

**타겟 팔레트(v1.59.0)** — 입력한 질문을 어디로 보낼지 여러 목적지(구글 AI 모드·
구글 드라이브·PasteFlow 자체 답변·사용자가 설정에서 추가한 웹사이트) 중 하나로 라우팅한다
(목록·URL 빌더는 `pasteflow/ai_palette.py`). 고르는 방법 셋을 동시에 지원한다:

- `Tab`/`Shift+Tab` — 하이라이트(칩)를 순환한다.
- `Alt+1~9` — 그 번호의 타겟으로 즉시 전송한다(하이라이트 이동 없이).
- 등록된 키워드+공백으로 문장을 시작하면(예: `"yt 고양이"`) 자동으로 그 타겟이
  하이라이트되고, 실행 시 키워드 접두어는 질의에서 잘려 나간다.
- `Enter` — 지금 하이라이트된 타겟으로 전송. `Ctrl+Enter` — 목록 중 첫 "PasteFlow
  답변(API)" 타겟으로 곧장 전송(옛 Ctrl+Enter 습관 보존).

Shift+Enter는 줄바꿈, Esc는 취소.
"""

import threading
from typing import Callable

from PyQt6.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPlainTextEdit,
    QPushButton, QCheckBox, QComboBox, QWidget,
)
from PyQt6.QtCore import Qt, QBuffer, QByteArray, QIODevice, QTimer, pyqtSignal
from PyQt6.QtGui import QCursor, QPixmap, QImage

from pasteflow import ai_palette
from pasteflow.ui.theme import COLORS, PEACH_HOVER, check_icon_url


class _QuestionEdit(QPlainTextEdit):
    """Tab/Shift+Tab=타겟 순환, Alt+1~9=즉시전송, Enter=실행, Ctrl+Enter=API로 바로,
    Shift+Enter=줄바꿈. Ctrl+V/드롭으로 이미지 첨부.
    """

    def __init__(self, on_submit, on_ctrl_submit, on_tab, on_alt_number,
                 on_image_paste=None, parent=None):
        super().__init__(parent)
        self._on_submit = on_submit
        self._on_ctrl_submit = on_ctrl_submit
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
            if mods & Qt.KeyboardModifier.ControlModifier:
                self._on_ctrl_submit()
            else:
                self._on_submit()
            return
        super().keyPressEvent(event)

    def insertFromMimeData(self, source):
        """붙여넣기(Ctrl+V)·드롭 시 이미지면 텍스트 대신 첨부한다 — 이 다이얼로그의
        유일한 첨부 경로(전용 버튼 없음, v1.49.1).

        - 원본 이미지(그림 캡처 등)면 그대로 첨부.
        - 로컬 이미지 '파일'을 복사/드롭했으면(경로만 들어옴) 전부 읽어 첨부한다
          (여러 파일을 한 번에 드롭/붙여넣기 가능 — 첫 장에서 멈추지 않음).
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
                attached = False
                for url in source.urls():
                    if url.isLocalFile():
                        fimg = QImage(url.toLocalFile())
                        if not fimg.isNull():
                            self._on_image_paste(fimg)
                            attached = True
                if attached:
                    return
        super().insertFromMimeData(source)


class AiQueryDialog(QDialog):
    """AI 질문 입력 — 컨텍스트(우클릭 항목)를 위에 보여주고, 질문과 보낼 타겟을 받는다."""

    _CTX_PREVIEW_CHARS = 300

    # 새로고침(↻)으로 전체 모델을 불러온 결과 — (models, error). 백그라운드
    # 스레드에서 메인 스레드로 안전하게 넘기기 위한 내부 시그널(settings_dialog와 동일 패턴).
    _models_fetched = pyqtSignal(list, str)

    # 마지막으로 실행한 타겟 인덱스 — 세션(프로세스) 내에서 다음 질문창의 초기 하이라이트로
    # 재사용한다(image_annotator의 "마지막 값 기억" 관례와 동일, DB 저장은 아님).
    _last_site_index: int = 0

    def __init__(self, context_text: str, parent=None, context_image: bytes | None = None,
                 compare_models: list[str] | None = None,
                 fetch_all_models: "Callable[[], list[str]] | None" = None,
                 open_history: "Callable[[QRect], None] | None" = None,
                 sites: "list[dict] | None" = None):
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
        # open_history(frame_geometry)는 트레이 'AI 기록'과 동일한 목록창을 여는 콜백(main
        # 제공) — 질문칸에서 바로 지난 대화를 훑어볼 수 있게 버튼 하나로 노출한다(v1.49.3).
        # 이 창의 프레임을 넘겨 기록창을 그 옆에(겹치지 않게) 열 수 있게 한다.
        self._open_history = open_history
        # compare_models는 설정된 모델명 목록(모델 1·2·3). 모델 선택 드롭다운의 기본 후보로도
        # 그대로 재사용한다.
        self._compare_models = [m for m in (compare_models or []) if m]
        # fetch_all_models()는 전체 모델명 리스트를 동기 반환하는 콜백(main이 제공,
        # DB/시크릿 접근은 main 쪽에 남긴다) — ↻ 클릭 시 백그라운드 스레드에서 호출한다.
        self._fetch_all_models = fetch_all_models
        self._model_options: list[str] = list(self._compare_models)  # combo와 인덱스 평행
        self._models_fetched.connect(self._on_models_fetched)
        self.setWindowTitle("AI에게 질문")
        # 항상 위 — 패널이 TOPMOST라(panel._set_always_on_top) 일반 창은 Windows Z-order상
        # 패널 아래에 깔려 클릭해도 앞으로 나오지 못한다(부모를 패널로 두던 시절엔 '소유 창은
        # 소유자 위'라는 별개 규칙에 얹혀 가려지지 않았지만, 그 소유 관계가 바로 패널을 못 누르게
        # 하던 원인이라 끊었다). 미리보기 팝업과 같은 TOPMOST 그룹에 올리면 그룹 안에서는
        # 활성화 순서가 통해 '클릭한 창이 앞으로' 온다.
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        # 비모달 — 질문창이 떠 있어도 패널·영역 캡처(Alt+F2)·AI 기록창을 그대로 쓸 수 있어야
        # 한다(질문 내용은 이 다이얼로그가 다 들고 있어 다른 창을 잠글 이유가 없다).
        # ⚠ 이 설정만으로는 부족하다 — **호출자가 `exec()`가 아니라 `show()`로 띄워야 한다.**
        # `exec()`는 창을 모달로 표시하는데, 모달리티가 NonModal이고 부모도 없으면 Qt가
        # ApplicationModal로 승격시켜 이 줄을 무력화한다(2026-07-13 PyQt6 실측). 그래서
        # main은 `_open_ai_dialog`에서 show() + finished 콜백으로 띄운다.
        # 포커스를 뺏기며 패널이 자동으로 숨는 것은 panel.py의 changeEvent 예외 목록
        # (AiQueryDialog 포함)이 막는다.
        self.setWindowModality(Qt.WindowModality.NonModal)
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
            QPushButton#history {{
                padding: 3px 10px;
                font-size: 11px;
                color: {COLORS['subtext0']};
            }}
            /* 비교 체크 시 흐려지는 모델 행 — 스타일시트로 색을 명시하면 Qt 기본 회색화가
               적용되지 않으므로 disabled 색을 직접 준다. */
            QLabel:disabled {{
                color: {COLORS['surface2']};
            }}
            QComboBox:disabled {{
                color: {COLORS['surface2']};
                border: 1px solid {COLORS['surface1']};
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
            QCheckBox::indicator:hover {{
                border-color: {COLORS['peach']};
            }}
            QCheckBox::indicator:checked {{
                border-color: {COLORS['peach']};
                image: url("{check_icon_url()}");
            }}
            QComboBox {{
                background-color: {COLORS['surface0']};
                color: {COLORS['text']};
                border: 1px solid {COLORS['surface2']};
                border-radius: 6px;
                padding: 4px 8px;
                font-size: 12px;
            }}
            QComboBox:focus {{
                border: 1px solid {COLORS['peach']};
            }}
            QComboBox QAbstractItemView {{
                background-color: {COLORS['surface0']};
                color: {COLORS['text']};
                selection-background-color: {COLORS['surface2']};
                selection-color: {COLORS['text']};
                border: 1px solid {COLORS['surface2']};
                outline: none;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(12, 12, 12, 12)

        # 첨부 이미지들(PNG bytes) — 우클릭 이미지 항목의 컨텍스트로 시작하거나, 질문칸
        # Ctrl+V·드롭으로 여러 장 붙일 수 있다. 질의 시 images로 멀티모달 전송된다
        # (여러 장이면 전부 첫 user 턴에 실린다).
        self._images: list[bytes] = [context_image] if context_image else []

        ctx = (context_text or "").strip()
        # 이미지 미리보기 — 첨부가 하나라도 있을 때만 보인다. 썸네일 스트립(각각 클릭 시 제거).
        self._img_caption = QLabel("")
        self._img_strip = QWidget()
        self._img_strip_layout = QHBoxLayout(self._img_strip)
        self._img_strip_layout.setContentsMargins(0, 0, 0, 0)
        self._img_strip_layout.setSpacing(6)
        layout.addWidget(self._img_caption)
        layout.addWidget(self._img_strip)

        self._refresh_image_preview()

        if not self._images and ctx:
            layout.addWidget(QLabel("선택한 항목(컨텍스트):"))
            preview = ctx[: self._CTX_PREVIEW_CHARS].replace("\n", " ")
            if len(ctx) > self._CTX_PREVIEW_CHARS:
                preview += " …"
            ctx_label = QLabel(preview)
            ctx_label.setObjectName("ctx")
            ctx_label.setWordWrap(True)
            layout.addWidget(ctx_label)

        # 질문칸 머리 행 — 오른쪽 끝에 '기록' 버튼. 안내문은 아래 입력칸의 placeholder로
        # 옮겼다(빈 칸일 때만 보이고 타이핑하면 사라진다 — 항상 한 줄을 먹던 라벨 제거).
        head_row = QHBoxLayout()
        head_row.setContentsMargins(0, 0, 0, 0)
        head_row.addStretch(1)
        if self._open_history is not None:
            history_btn = QPushButton("기록")
            history_btn.setObjectName("history")
            history_btn.setToolTip("저장된 AI 대화 기록 보기")
            history_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            history_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            # 이 질문창의 프레임을 넘겨 기록창이 그 옆에 이어 붙게 한다(main._on_ai_history_
            # requested가 compute_preview_pos로 배치) — 겹쳐서 옮겨야 하던 문제 해결.
            # clicked(bool)의 checked 인자는 버리고 프레임만 넘긴다.
            history_btn.clicked.connect(
                lambda _checked=False: self._open_history(self.frameGeometry()))
            head_row.addWidget(history_btn)
        layout.addLayout(head_row)

        self._editor = _QuestionEdit(
            self._on_submit, self._on_ctrl_submit, self._on_tab, self._on_alt_number,
            on_image_paste=self._on_image_pasted)
        self._editor.setPlaceholderText(
            "질문을 입력하세요 — Tab 타겟 전환 · Alt+숫자 즉시전송 · Enter 실행 · "
            "Shift+Enter 줄바꿈 · 이미지는 Ctrl+V/드래그로 첨부")
        self._editor.setFocus()
        layout.addWidget(self._editor, 1)

        # 타겟 칩 로우(PowerToys Run/Raycast식) — 번호(Alt+숫자)+라벨, 하이라이트=코랄.
        # 클릭하면 그 타겟으로 즉시 실행(모바일 없는 마우스 전용 경로).
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

        # 모델 선택 — 평소엔 설정된 모델 1·2·3만 보이고(하이브리드, v1.49.1), ↻를 누르면
        # 전체 모델을 불러와 콤보를 채운다. "PasteFlow 답변(API)" 타겟이 하이라이트일 때만
        # 보인다(그 외 타겟엔 무의미 — _update_kind_visibility).
        model_row = QHBoxLayout()
        model_row.setContentsMargins(0, 0, 0, 0)
        model_row.setSpacing(6)
        self._model_label = QLabel("모델:")
        model_row.addWidget(self._model_label)
        self._model_combo = QComboBox()
        self._model_combo.setToolTip("이 질문을 보낼 모델을 고릅니다(비워 두면 기본 모델 1 사용).")
        self._fill_model_combo(self._model_options)
        model_row.addWidget(self._model_combo, 1)
        self._model_refresh_btn = QPushButton()
        self._model_refresh_btn.setIcon(
            self.style().standardIcon(self.style().StandardPixmap.SP_BrowserReload))
        self._model_refresh_btn.setToolTip("전체 모델 목록 불러오기")
        self._model_refresh_btn.setFixedSize(24, 24)
        self._model_refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._model_refresh_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._model_refresh_btn.clicked.connect(self._on_refresh_models_clicked)
        model_row.addWidget(self._model_refresh_btn)
        layout.addLayout(model_row)
        self._model_status = QLabel("")
        self._model_status.setVisible(False)
        layout.addWidget(self._model_status)

        # 여러 모델 비교 체크박스 — 비교 모델이 2개 이상 설정됐을 때만 노출한다(1개면 무의미).
        # 켜면 이 질문을 설정된 모델들로 동시에 던져 답변창을 나란히 띄운다(위 단일 모델
        # 선택과는 별개 경로 — 켜져 있으면 단일 선택은 무시된다).
        self._compare_check: "QCheckBox | None" = None
        if len(self._compare_models) >= 2:
            self._compare_check = QCheckBox(
                f"여러 모델로 비교 ({len(self._compare_models)}개)")
            self._compare_check.setToolTip(
                "이 질문을 아래 모델들로 동시에 질의해 답변을 나란히 비교합니다:\n"
                + "\n".join(f"· {m}" for m in self._compare_models))
            # 비교를 켜면 단일 모델 선택은 무시된다 — 그 규칙을 콤보 비활성화로 눈에 보이게 한다
            # (예전엔 코드 주석에만 있어, 모델을 골라 놓고도 비교로 나가는지 알 수 없었다).
            self._compare_check.toggled.connect(self._on_compare_toggled)
            layout.addWidget(self._compare_check)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        cancel_btn = QPushButton("취소")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

        self._update_highlight()  # 초기 하이라이트 + 모델 행 표시 상태 반영

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

    def _on_ctrl_submit(self):
        """Ctrl+Enter — 옛 'API 질의' 습관 보존. 목록 중 첫 API 타겟으로 바로 보낸다."""
        for i, site in enumerate(self._sites):
            if site.get("kind") == ai_palette.KIND_API:
                self._execute(i)
                return
        self._on_submit()

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
        """칩 스타일 + 모델 행 표시를 지금 유효한 타겟에 맞춰 갱신한다."""
        idx = self._effective_index()
        for i, btn in enumerate(self._chips):
            self._style_chip(btn, i == idx)
        is_api = 0 <= idx < len(self._sites) and self._sites[idx].get("kind") == ai_palette.KIND_API
        self._update_kind_visibility(is_api)

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

    def _update_kind_visibility(self, is_api: bool):
        """모델 드롭다운·비교 체크박스는 'PasteFlow 답변(API)' 타겟일 때만 의미가 있다."""
        self._model_label.setVisible(is_api)
        self._model_combo.setVisible(is_api)
        self._model_refresh_btn.setVisible(is_api and self._fetch_all_models is not None)
        if not is_api:
            self._model_status.setVisible(False)
        if self._compare_check is not None:
            self._compare_check.setVisible(is_api)

    def get_result(self) -> "tuple[dict, str] | None":
        """실행하기로 한 (타겟 딕셔너리, 질의 텍스트). 취소했으면 `None`."""
        if self._result_index is None:
            return None
        return self._sites[self._result_index], self._result_query

    def _on_compare_toggled(self, checked: bool):
        """'여러 모델로 비교' on/off — 켜면 단일 모델 선택 행을 흐리게(비활성) 한다.

        비교가 켜져 있으면 `main._start_compare_query`가 설정된 모델 1·2·3으로 나가고 이
        콤보의 선택은 쓰이지 않는다. 흐려진 행이 그 사실을 그 자리에서 말해 준다.
        """
        self._model_label.setEnabled(not checked)
        self._model_combo.setEnabled(not checked)
        self._model_refresh_btn.setEnabled(not checked)

    def is_compare(self) -> bool:
        """'여러 모델로 비교' 체크 여부. 체크박스가 없으면(모델 미설정) 항상 False."""
        return self._compare_check is not None and self._compare_check.isChecked()

    def get_selected_model(self) -> str | None:
        """모델 드롭다운에서 고른 모델명. 후보가 없으면 None(=main이 기본값 사용)."""
        idx = self._model_combo.currentIndex()
        if 0 <= idx < len(self._model_options):
            return self._model_options[idx]
        return None

    # ── 모델 선택 ──────────────────────────────────────────────────────────────
    def _fill_model_combo(self, options: list[str]):
        """콤보를 모델명 목록으로 채운다 — 인덱스가 self._model_options와 평행."""
        self._model_options = options
        self._model_combo.clear()
        self._model_combo.addItems(options)

    def _on_refresh_models_clicked(self):
        """↻ — 전체 모델 목록을 백그라운드에서 불러와 콤보를 채운다."""
        if self._fetch_all_models is None:
            return
        self._model_refresh_btn.setEnabled(False)
        self._model_status.setText("모델 목록을 불러오는 중…")
        self._model_status.setVisible(True)

        def _worker():
            try:
                self._models_fetched.emit(self._fetch_all_models(), "")
            except Exception as e:
                self._models_fetched.emit([], str(e))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_models_fetched(self, models: list, err: str):
        self._model_refresh_btn.setEnabled(True)
        if err:
            self._model_status.setText(f"모델 목록을 불러오지 못했습니다 — {err}")
            self._model_status.setVisible(True)
            return
        options = sorted(models)
        self._fill_model_combo(options)
        self._model_status.setText(f"모델 {len(options)}종을 불러왔습니다.")
        self._model_status.setVisible(True)

    def get_images(self) -> list[bytes]:
        """질문과 함께 보낼 이미지들(PNG bytes 리스트). 없으면 빈 리스트."""
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
        """PNG bytes를 첨부 목록에 추가하고 미리보기를 갱신한다."""
        if not png:
            return False
        self._images.append(png)
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

    def _remove_at(self, index: int):
        if 0 <= index < len(self._images):
            del self._images[index]
            self._refresh_image_preview()

    def _refresh_image_preview(self):
        """첨부 목록에 맞춰 썸네일 스트립을 갱신한다 — 각 썸네일 우상단 ✕ 배지가 그 장만
        개별 제거한다(전체 제거 버튼은 없음 — 장 수가 많지 않아 개별 제거로 충분하고,
        배지를 썸네일에 직접 붙여야 "이걸 지운다"는 대상이 명확하다)."""
        # 기존 썸네일 위젯 제거
        while self._img_strip_layout.count():
            item = self._img_strip_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        has_img = bool(self._images)
        _THUMB = 64
        _BADGE = 18
        for i, png in enumerate(self._images):
            pix = QPixmap()
            if not (pix.loadFromData(png) and not pix.isNull()):
                continue
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
            remove_btn.setToolTip("이 이미지 제거")
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
            remove_btn.clicked.connect(lambda _c, idx=i: self._remove_at(idx))
            remove_btn.raise_()

            self._img_strip_layout.addWidget(cell)
        self._img_strip_layout.addStretch(1)

        if has_img:
            self._img_caption.setText(f"이미지 {len(self._images)}장 (질문과 함께 전송):")
        self._img_caption.setVisible(has_img)
        self._img_strip.setVisible(has_img)
