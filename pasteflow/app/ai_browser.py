"""AI 질문 창과 브라우저 주입 — pasteflow/main.py에서 글자 그대로 옮김(2026-11-16 폴더 구조 2편)."""

from pasteflow.models import ClipboardItem


class AiBrowserMixin:
    def _open_ai_dialog(self, initial_image_png: bytes | None = None):
        """이미지 우클릭 "Gemini에게 질문" — 질문 입력창을 **비모달로** 띄우고, 질문을
        브라우저의 Google AI 모드로 보낸다.

        initial_image_png: 주어지면 창을 열 때 이미지를 미리 첨부한다(핀 이미지 우클릭
        "Gemini에게 질문" — 2026-08-02, `AiQueryDialog.attach_image_bytes`가 이미
        패널 드래그앤드롭용으로 있던 공개 메서드라 재사용). Ctrl+V로 나중에 다른 이미지를
        첨부하면 그걸로 교체된다(1장 제한은 기존 동작 그대로).

        **목적지는 Google AI 모드 하나뿐이다** — 설정창 "빠른 검색"(사용자 URL 타겟 편집)과
        자유질문 단축키(Alt+1)·캡처 후 질문 단축키(Alt+2)는 실사용이 없어 2026-09-25에
        제거했다. 질문창은 `ai_palette.DEFAULT_SITES`(Google AI 하나)로만 연다.

        ⚠ **`exec()`를 쓰지 않는다.** `QDialog.exec()`는 내부적으로 창을 모달로 표시하는데,
        모달리티가 `NonModal`이고 부모도 없으면 Qt가 이를 **ApplicationModal로 승격**시킨다
        (2026-07-13 PyQt6 실측: `setWindowModality(NonModal)` 후 `exec()` →
        `QWindow.modality == ApplicationModal`, `show()`만 NonModal 유지). 그래서 v1.49.4의
        `setWindowModality(NonModal)`은 무력했고, 질문창이 떠 있는 동안 **패널 클릭·영역
        캡처(Alt+F2)가 전부 앱 모달에 막혀 있었다**(사용자 보고). 결과 수신을 `finished`
        콜백으로 바꿔 `show()`로 띄운다.

        재진입 가드를 둔다 — 비모달이라 질문창이 떠 있는 채로 단축키가 다시 들어올 수 있고,
        그때 창을 또 만들면 앵커가 뒤엉킨다.
        """
        from pasteflow.ui.ai_query import AiQueryDialog
        from pasteflow import ai_palette

        existing = getattr(self, "_ai_dialog", None)
        if existing is not None:
            existing.raise_()
            existing.activateWindow()
            return

        sites = ai_palette.load_sites("")  # 빈 값 → DEFAULT_SITES 사본(Google AI 하나)
        # parent=None — self.panel을 부모로 주면 Windows가 이 창을 패널의 "소유 창"으로 취급해
        # 패널 위에 항상 떠 있게 고정한다(모달과 무관한 별개의 Z-order 규칙) — 미리보기 팝업과
        # 같은 독립 최상위 창 패턴으로 통일한다.
        dialog = AiQueryDialog(parent=None, sites=sites)
        if initial_image_png:
            dialog.attach_image_bytes(initial_image_png)
        self._ai_dialog = dialog

        def _finished(_code: int):
            self._ai_dialog = None
            result = dialog.get_result()
            if result is None:  # 취소(Esc·취소 버튼)
                dialog.deleteLater()
                return
            _site, question = result
            self._open_in_browser(question, dialog.get_images())
            dialog.deleteLater()

        dialog.finished.connect(_finished)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    # 페이지가 뜨고 입력칸에 포커스가 갈 때까지 기다리는 시간. 프로그램은 로드 완료 시점을
    # 알 수 없어(브라우저 밖에서 DOM을 못 본다) 고정 지연에 기댈 수밖에 없다 — 주입 경로가
    # URL 경로보다 본질적으로 약한 지점이다. 넉넉히 잡는 대신, 주입 직전 포그라운드가
    # 브라우저인지 검사해 엉뚱한 창에 쏘는 사고를 막는다.
    _BROWSER_LOAD_MS = 3500
    _BROWSER_LOAD_IMAGE_EXTRA_MS = 1500  # 이미지 포함 시 첫 붙여넣기 전 추가 대기
    _INJECT_STEP_MS = 700   # 붙여넣기 사이 간격(첨부 칩을 만들 시간)

    def _open_in_browser(self, question: str, images: "list[bytes] | None" = None):
        """질문을 구글 AI 모드로 브라우저에서 연다.

        API 검색 경로가 구조적으로 못 잡는 것을 위한 유일한 목적지다: 실시간 시세는 페이지
        본문에 없어 크롤링 텍스트를 읽는 검색 도구가 영영 볼 수 없는데, 브라우저로 열면
        구글이 금융 피드를 직접 물고 있어 정확한 값이 나온다(web_open.py 모듈 주석의
        2026-07-14 실측 참조).

        **이미지 첨부가 있으면 주입 경로로 갈린다.** 이미지는 URL에 실을 수 없지만 입력칸이
        Ctrl+V로 이미지를 받으므로, 홈 화면을 열고 클립보드+키를 주입한다(`_inject_to_google`).
        텍스트만이면 견고한 URL 경로를 그대로 쓴다 — 주입은 타이밍에 의존해 본질적으로
        약하므로 필요할 때만 내려간다. 구글 AI 모드는 이미지를 **1장만** 지원한다(2026-07-29
        사용자 실사용 확인 — `ai_query.py`의 첨부 UI가 이미 1장으로 제한한다).
        """
        from pasteflow import web_open
        from pasteflow.ui.toast import ToastNotification

        if images:
            self._inject_to_google(question, images)
            return

        if web_open.open_url(web_open.google_ai_url(question)):
            ToastNotification("구글 AI 모드로 검색 — 브라우저에서 여는 중", icon="🌐")
        else:
            ToastNotification("브라우저를 열지 못했습니다", icon="🌐")

    def _inject_to_google(self, question: str, images: "list[bytes]"):
        """구글 AI 모드 홈 화면을 열고 이미지 → 질문 → Enter를 순서대로 주입한다.

        `_open_in_browser`가 호출자 — 이미지가 있을 때만 이 경로를 탄다. 클립보드를 여러 번
        (이미지·텍스트) 갈아끼우는 방식이다 — 한 글자씩 타이핑하는 것보다 단순하고,
        `_set_clipboard`가 `_self_triggered`를 세워 주므로 이 임시 클립보드가 히스토리에
        쌓이지도 않는다(금지 조항 5와 같은 경로).

        각 단계는 QTimer로 이어 붙인다(sleep으로 UI를 막지 않는다). 매 단계 직전
        포그라운드가 브라우저인지 확인하고, 아니면 즉시 중단한다 — 사용자가 그 사이 다른
        창을 클릭했다면 우리가 쏜 Ctrl+V·Enter가 그 창에 들어가 버리기 때문이다.
        """
        from PyQt6.QtCore import QTimer
        from pasteflow import web_open
        from pasteflow.paste_interceptor import VK_RETURN, VK_V
        from pasteflow.ui.toast import ToastNotification

        images = images[:1]  # 구글 AI 모드는 이미지 1장만 지원(2026-07-29 사용자 확인)

        if not web_open.open_url(web_open.google_ai_home_url()):
            ToastNotification("브라우저를 열지 못했습니다", icon="🌐")
            return
        img_note = " — 이미지 포함" if images else ""
        ToastNotification(f"구글 AI 모드 여는 중{img_note}", icon="🌐")

        # 붙여넣을 것들을 순서대로 — 이미지 다음에 질문 텍스트.
        pastes: list[ClipboardItem] = [
            ClipboardItem(content_type="image", image_data=png) for png in images
        ]
        pastes.append(ClipboardItem(content_type="text", text_content=question))

        def _step(i: int):
            if not web_open.is_browser_foreground():
                # 사용자가 다른 창으로 갔다 — 남은 키를 쏘지 않고 조용히 멈춘다.
                ToastNotification("브라우저가 앞에 없어 중단했습니다 — 직접 붙여넣어 주세요",
                                  icon="🌐")
                return
            if i < len(pastes):
                self.interceptor._set_clipboard(pastes[i])
                self.interceptor._send_clean_key(VK_V)
                QTimer.singleShot(self._INJECT_STEP_MS, lambda: _step(i + 1))
            else:
                self.interceptor.send_plain_key(VK_RETURN)

        initial_delay = self._BROWSER_LOAD_MS
        if images:
            initial_delay += self._BROWSER_LOAD_IMAGE_EXTRA_MS
        QTimer.singleShot(initial_delay, lambda: _step(0))

