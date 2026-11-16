"""OCR 설정·워커·결과 처리 — pasteflow/main.py에서 글자 그대로 옮김(2026-11-16 폴더 구조 2편)."""

import ctypes
import threading

from PyQt6.QtCore import QRect, QTimer

from pasteflow.models import ClipboardItem
from pasteflow.ui.text_preview import TextPreviewPopup
from pasteflow.winapi.image_io import _image_data_to_png_bytes
from pasteflow.app.dialog_style import _MSGBOX_DARK_STYLE


class OcrMixin:
    def _resolve_gemini_cfg(self) -> tuple[str, str, str]:
        """게이트웨이 설정을 해석해 (api_key, base_url, model) 반환 — OCR 전용.

        v1.6x에서 AI 질의(우클릭 "AI에게 질문"·비교·드라이브 연동) 기능을 통째로 제거해,
        이제 API 키를 쓰는 경로는 OCR 하나뿐이다. DB 접근은 _lock으로 직렬화되어 워커
        스레드에서 호출해도 안전.
        """
        base_url_saved = self.db.get_setting("ocr_gemini_base_url", "")
        model = self.db.get_setting("ocr_model_gateway", "")
        return (self._get_secret("ocr_gemini_api_key_gateway"), base_url_saved, model)


    def _prewarm_ocr_connection(self):
        """`openai` 패키지 임포트 + 클라이언트 캐시 워밍업(백그라운드 스레드에서 호출).

        실패해도 조용히 무시 — 실제 OCR 호출은 항상 정상 경로(`_start_ocr_worker`)로
        진행되고, 이건 그 경로가 커넥션 설정 비용 없이 시작하게 미리 데워둘 뿐이다.
        """
        try:
            from pasteflow.ocr_engine import warm_connection
            api_key, base_url, _model = self._resolve_gemini_cfg()
            warm_connection(api_key, base_url)
        except Exception:
            pass

    def _start_ocr_worker(self, png_bytes: bytes, translate: bool = False):
        """공용 OCR 워커 — PNG bytes를 받아 백그라운드에서 OCR 수행.

        영역 캡처 OCR과 이미지 항목 OCR이 공유. 결과/에러는
        `_bridge.ocr_done` / `_bridge.ocr_error` 시그널로 메인 스레드에 통지.
        translate=True면 같은 호출에서 프롬프트만 바꿔 번역문을 받아 `_bridge.ocr_translated`로 보낸다.
        """
        import io
        from PIL import Image
        from PyQt6.QtGui import QCursor

        # 진행 칩을 OCR 트리거 시점 커서가 있는 모니터 정중앙에 띄운다(영역 선택 직후 / 우클릭 위치).
        # 번역 창은 이 자리(끌기를 끝낸 곳) 옆에 띄운다.
        self._ocr_anchor = QCursor.pos()
        self._start_cursor_progress("번역 중…" if translate else "인식 중…", "🔤", self._ocr_anchor)

        def _run():
            # 호출마다 COM 초기화/해제 — asyncio/WinRT 상태 오염 방지
            ctypes.windll.ole32.CoInitializeEx(None, 0)
            try:
                pil_img = Image.open(io.BytesIO(png_bytes))

                from pasteflow.ocr_engine import OcrEngine
                # OCR은 별도 엔진 선택 없이 항상 AI(Gemini 공식 / Mindlogic 게이트웨이) API로 처리.
                # (WinRT 엔진 제거 — 설정에서 엔진/언어 선택 UI도 삭제됨. AI 답변과 동일 배관 재사용.)
                api_key, base_url, model = self._resolve_gemini_cfg()
                engine = OcrEngine(kind="gemini", api_key=api_key, base_url=base_url, model=model,
                                   translate=translate)
                text = engine.recognize(pil_img)
                if engine.last_fallback_from and engine.last_used_model:
                    self._bridge.ocr_fallback.emit(engine.last_fallback_from, engine.last_used_model)
                (self._bridge.ocr_translated if translate else self._bridge.ocr_done).emit(text)
            except Exception as e:
                self._bridge.ocr_error.emit(str(e))
            finally:
                ctypes.windll.ole32.CoUninitialize()

        threading.Thread(target=_run, daemon=True, name="ocr-worker").start()

    def _on_ocr_image_item(self, item: ClipboardItem, translate: bool = False):
        """이미지 우클릭 "텍스트 추출(OCR)"·"번역"(히스토리·미리보기·핀 공용) — 영역 OCR과 같은 워커로 처리.

        2026-08-02에 실사용 근거가 없어 지웠다가 실제로 필요해져 되살렸다. 옛 코드는
        `Image.open`으로 바로 열어 Alt+F2 캡처가 저장한 raw CF_DIB에서 실패할 수 있었으므로
        `_image_data_to_png_bytes`(DIB 대응)로 변환한다.
        """
        from pasteflow.ui.toast import ToastNotification

        if not item.image_data:
            ToastNotification("이미지 데이터를 찾을 수 없습니다", icon="🔤")
            return
        try:
            png_bytes = _image_data_to_png_bytes(item.image_data)
        except Exception as e:
            ToastNotification(f"이미지 변환 실패 — {e}", icon="🔤")
            return
        self._start_ocr_worker(png_bytes, translate=translate)

    def _on_ocr_item_by_id(self, item_id: int, translate: bool = False):
        """히스토리 우클릭 "텍스트 추출(OCR)"·"번역"(item_id 기반) → DB에서 풀 로드 후 공용 코어로 위임."""
        from pasteflow.ui.toast import ToastNotification
        item = self.db.get_item(item_id)
        if not item:
            ToastNotification("항목을 찾을 수 없습니다", icon="🔤")
            return
        self._on_ocr_image_item(item, translate=translate)

    def _on_translate_image_item(self, item: ClipboardItem):
        """미리보기·핀 우클릭 "번역" — 그 이미지의 글자를 읽어 번역(Shift+드래그 번역과 같은 경로)."""
        self._on_ocr_image_item(item, translate=True)

    def _on_translate_item_by_id(self, item_id: int):
        """히스토리 우클릭 "번역"."""
        self._on_ocr_item_by_id(item_id, translate=True)


    def _on_ocr_done(self, text: str, translated: bool = False):
        """메인 스레드: OCR 결과 → 클립보드 + DB + 큐 + 정중앙 결과 칩(✓ 앞부분…)

        translated=True(Shift+드래그 번역)면 text는 번역문이고, 읽으라고 텍스트 미리보기 창도 띄운다."""
        if not text.strip():
            # 진행 칩을 결과 메시지로 전환(칩이 없으면 일반 토스트로 폴백)
            if not self._finish_cursor_progress("인식된 텍스트 없음"):
                from pasteflow.ui.toast import ToastNotification
                ToastNotification("텍스트를 인식하지 못했습니다", icon="🔤")
            return

        item = ClipboardItem(
            content_type="text",
            text_content=text,
            preview_text=text[:200],
        )
        # 클립보드 먼저 입력 — _set_clipboard가 내부에서 monitor._self_triggered를 설정해
        # 클립보드 모니터가 동일 항목을 재감지(큐 중복 추가)하지 않도록 함
        self.interceptor._set_clipboard(item)
        # OCR은 아래에서 자체 칩을 띄우므로 복사 토스트 없는 경로 사용
        saved = self._persist_clipboard_item(item)
        if translated:
            # 끌기를 끝낸 자리 옆에 번역문을 띄운다(패널 미리보기와 같은 창 — 우클릭 복사·수정 그대로)
            from PyQt6.QtCore import QSize
            popup = TextPreviewPopup.open_new(saved, QRect(self._ocr_anchor, QSize(1, 1)))
            popup.copy_requested.connect(self._on_copy_item)
            popup.edit_requested.connect(self._open_memo)
            self._stop_cursor_progress()  # 번역 창이 곧 결과 — "✓ 앞부분…" 칩은 겹치는 정보라 띄우지 않는다
            return

        # 진행 칩을 "✓ 인식 앞부분…"으로 전환 — "내 OCR이 제대로 됐나" 시각 확인용.
        # 전체 텍스트는 클립보드·큐로 들어가므로 칩은 앞 24자 미리보기만 보여준다.
        flat = " ".join(text.split())  # 줄바꿈·연속 공백을 단일 공백으로
        preview = flat[:24]
        suffix = "…" if len(flat) > 24 else ""
        msg = f"✓ {preview}{suffix}"
        if not self._finish_cursor_progress(msg):
            from pasteflow.ui.toast import ToastNotification
            ToastNotification(msg, icon="🔤")

    def _on_ocr_translated(self, text: str):
        """메인 스레드: OCR 번역 결과(Shift+드래그) — OCR과 같은 처리에 번역 창만 더한다."""
        self._on_ocr_done(text, translated=True)

    def _on_ocr_fallback(self, failed_model: str, used_model: str):
        """모델 not_found로 폴백이 발동했을 때 사용자에게 알림.

        사용자가 다음 사용 시 모델 선택을 재고할 수 있게 한다 (DB 자체는 변경하지 않음 —
        잠깐의 게이트웨이 장애일 수도 있으므로 사용자 명시 선택을 유지).
        """
        from pasteflow.ui.toast import ToastNotification
        ToastNotification(
            f"{failed_model} → {used_model}로 폴백",
            icon="🔤",
        )

    def _on_ocr_error(self, msg: str):
        from pasteflow.ui.toast import ToastNotification

        self._stop_cursor_progress()  # 진행 칩 정리(지속형이라 수동 종료 필요)

        # API 키 미설정 → 토스트 후 설정 다이얼로그 자동 열기
        if "API 키" in msg:
            ToastNotification("API 키를 설정해 주세요", icon="🔤")
            QTimer.singleShot(300, self._open_settings)
            return

        if "미설치" in msg:
            # AI OCR 패키지 미설치 (google-generativeai, openai 등)
            from PyQt6.QtWidgets import QMessageBox
            import re
            # "xxx 패키지 미설치: pip install yyy" 패턴에서 pip 명령 추출
            m = re.search(r"(pip install \S+)", msg)
            pip_cmd = m.group(1) if m else ""
            body = "OCR에 필요한 패키지가 설치되지 않았습니다."
            if pip_cmd:
                body += f"\n\n터미널에서 실행하세요:\n  {pip_cmd}"
            dlg = QMessageBox(self.panel)
            dlg.setStyleSheet(_MSGBOX_DARK_STYLE)
            dlg.setWindowTitle("OCR 패키지 미설치")
            dlg.setIcon(QMessageBox.Icon.Information)
            dlg.setText(body)
            dlg.addButton(QMessageBox.StandardButton.Close)
            dlg.exec()
        else:
            from PyQt6.QtWidgets import QMessageBox
            dlg = QMessageBox(self.panel)
            dlg.setStyleSheet(_MSGBOX_DARK_STYLE)
            dlg.setWindowTitle("OCR 오류")
            dlg.setIcon(QMessageBox.Icon.Warning)
            dlg.setText(msg[:200])
            if len(msg) > 200:
                dlg.setDetailedText(msg)
            dlg.addButton(QMessageBox.StandardButton.Close)
            dlg.exec()

