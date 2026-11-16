"""음성 입력(STT) 푸시투토크 — pasteflow/main.py에서 글자 그대로 옮김(2026-11-16 폴더 구조 2편)."""

import threading

from PyQt6.QtCore import QTimer

from pasteflow.models import ClipboardItem


class SttMixin:
    # ── 음성 입력(STT) — 푸시투토크: keydown=녹음 시작, keyup=녹음 종료+게이트웨이 전송 ──
    # 게이트웨이 오디오 입력은 Gemini 계열만 지원(2026-08-02 실측, stt_engine.py 참고).
    # api_key/base_url은 OCR과 같은 크리덴셜을 공유하고 모델만 별도 슬롯을 쓴다.

    def _resolve_stt_cfg(self) -> tuple[str, str, str]:
        """STT용 게이트웨이 설정 — (api_key, base_url, model). 크리덴셜은 OCR과 공유,
        모델은 `stt_model_gateway`(Gemini 계열 전용 슬롯) — OCR/AI 모델 분리와 동일 이유
        (한 모델을 공유하면 설정창에서 GPT/Claude를 골랐을 때 STT가 항상 실패한다).
        기본값은 `ocr_engine.STT_FALLBACK_DEFAULT`(gemini-3.1-flash-lite) — 실측상
        gemini-2.5-flash보다 일관되게 빠르다(2026-08-02, 근거는 그 상수 주석 참고)."""
        from pasteflow.ocr_engine import STT_FALLBACK_DEFAULT
        api_key, base_url, _ocr_model = self._resolve_gemini_cfg()
        model = self.db.get_setting("stt_model_gateway", "") or STT_FALLBACK_DEFAULT
        return (api_key, base_url, model)

    # 녹음 중 이퀄라이저 pill의 음량 폴링 간격 — 부드러워 보이면서도 CPU 부담 없는 수준.
    _STT_LEVEL_POLL_MS = 50

    def _on_stt_start(self):
        """음성 입력 단축키 keydown — 마이크 녹음 시작 + 시작 알림음 + 이퀄라이저 표시.

        Wispr Flow와 나란히 써본 사용자 피드백(2026-08-02)으로 추가 — 텍스트만 있던
        진행 칩 대신, 녹음 중에만 뜨는 음량 반응 pill(`ui/stt_indicator.py`)을 커서
        옆에 띄운다. pill을 클릭하면 단축키를 뗀 것과 동일하게 녹음이 끝난다.
        """
        from PyQt6.QtGui import QCursor
        from pasteflow.ui.toast import ToastNotification

        if self._stt_recorder is None:
            from pasteflow.stt_engine import Recorder
            self._stt_recorder = Recorder()
        mic_device = self.db.get_setting("stt_mic_device", "")
        try:
            self._stt_recorder.start(device=mic_device)
        except Exception as e:
            ToastNotification(f"마이크를 열 수 없습니다 — {e}", icon="🎤")
            return
        if mic_device and self._stt_recorder.used_device != mic_device:
            # 설정에 저장된 장치를 못 찾아(분리 등) 시스템 기본으로 폴백됨 — 조용히
            # 넘기지 않고 알린다(설정과 실제 동작이 어긋난 채 계속 쓰는 걸 막기 위함).
            ToastNotification(f"'{mic_device}' 마이크를 찾을 수 없어 기본 마이크로 녹음합니다", icon="🎤")

        # 시작 알림음 — winsound(stdlib, Windows 전용)로 새 의존성 없이 비동기 재생.
        try:
            import winsound
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
        except Exception:
            pass

        from pasteflow.ui.stt_indicator import SttIndicator
        indicator = SttIndicator(QCursor.pos())
        indicator.stop_clicked.connect(self._on_stt_stop)  # 클릭 = 단축키 뗀 것과 동일
        self._stt_indicator = indicator

        level_timer = QTimer()
        level_timer.setInterval(self._STT_LEVEL_POLL_MS)
        level_timer.timeout.connect(self._tick_stt_level)
        level_timer.start()
        self._stt_level_timer = level_timer

        # 30초 상한 — 무한 녹음 방지(토큰 비용·응답 지연 억제). keyup으로 정상 종료되면
        # _on_stt_stop이 이 타이머를 취소하므로, 여기까지 살아 있으면 자동 종료+전송.
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(self._on_stt_stop)
        timer.start(30_000)
        self._stt_auto_stop_timer = timer

    def _tick_stt_level(self):
        """`_STT_LEVEL_POLL_MS`마다 Recorder의 최근 RMS를 읽어 이퀄라이저 pill에 반영."""
        if self._stt_recorder is None or self._stt_indicator is None:
            return
        self._stt_indicator.set_level(self._stt_recorder.get_level())

    def _stop_stt_indicator(self):
        """이퀄라이저 pill·음량 폴링 타이머 정리(idempotent) — keyup·클릭·30초 상한 공통 경로."""
        timer = self._stt_level_timer
        if timer is not None:
            timer.stop()
            self._stt_level_timer = None
        indicator = self._stt_indicator
        if indicator is not None:
            indicator.dismiss()
            self._stt_indicator = None

    # keyup 즉시 녹음을 끊으면 발화 끝자락이 잘린다(오디오 장치 버퍼링 지연 + 마지막
    # 음절을 발음하는 동안 키를 떼는 사람의 반응 시간) — 2026-08-02 사용자 리포트:
    # "말한 뒤 바로 떼면 뒤가 짤림, 2초쯤 더 눌러야 함". keyup 시점에 곧장 멈추지 않고
    # 이 여유시간만큼 계속 녹음한 뒤에 실제로 멈춘다(수동 2초 홀드보다 훨씬 짧게).
    _STT_TAIL_PAD_MS = 400

    def _on_stt_stop(self):
        """음성 입력 단축키 keyup(또는 pill 클릭·30초 상한 도달) — 여유시간 뒤 녹음을 마무리한다."""
        timer = self._stt_auto_stop_timer
        if timer is not None:
            timer.stop()
            self._stt_auto_stop_timer = None
        self._stop_stt_indicator()  # 이퀄라이저는 keyup 즉시 사라짐(꼬리 여유는 백그라운드에서만 계속)

        if self._stt_recorder is None or not self._stt_recorder.is_recording():
            return
        QTimer.singleShot(self._STT_TAIL_PAD_MS, self._finish_stt_recording)

    def _finish_stt_recording(self):
        """`_on_stt_stop`의 여유시간이 끝난 뒤 실제로 녹음을 멈추고 워커에서 게이트웨이 호출."""
        from PyQt6.QtGui import QCursor

        if self._stt_recorder is None:
            return
        wav_bytes = self._stt_recorder.stop()
        if not wav_bytes:
            self._stop_cursor_progress()
            return

        # 무음/마이크 미연결 차단 — 침묵 오디오를 게이트웨이에 보내면 모델이 "00:01" 같은
        # 그럴듯한 텍스트를 환각해 정상 인식과 구분이 안 됐다(2026-08-26 사용자 리포트).
        # API를 부르기 전에 우리가 직접 측정한 피크 음량으로 차단 — 설정창 "마이크 테스트"
        # (_on_test_mic)와 같은 임계값을 공유해 판정 기준이 어긋나지 않게 한다.
        from pasteflow.stt_engine import SILENCE_RMS_THRESHOLD
        if self._stt_recorder.last_peak_rms < SILENCE_RMS_THRESHOLD:
            self._stop_cursor_progress()
            from pasteflow.ui.toast import ToastNotification
            ToastNotification("마이크에 소리가 감지되지 않았습니다 — 연결·음소거를 확인하세요", icon="🎤")
            return

        self._start_cursor_progress("인식 중…", "🎤", QCursor.pos())  # 칩을 "녹음 중" → "인식 중"으로 전환

        def _run():
            try:
                from pasteflow.stt_engine import transcribe
                api_key, base_url, model = self._resolve_stt_cfg()
                text = transcribe(wav_bytes, api_key, base_url, model)
                self._bridge.stt_done.emit(text)
            except Exception as e:
                self._bridge.stt_error.emit(str(e))

        threading.Thread(target=_run, daemon=True, name="stt-worker").start()

    def _on_stt_done(self, text: str):
        """메인 스레드: STT 결과 → 클립보드+DB+큐 저장 후 포커스된 입력창에 자동 Ctrl+V."""
        from pasteflow.paste_interceptor import VK_V

        if not text.strip():
            if not self._finish_cursor_progress("인식된 음성 없음"):
                from pasteflow.ui.toast import ToastNotification
                ToastNotification("음성을 인식하지 못했습니다", icon="🎤")
            return

        item = ClipboardItem(
            content_type="text",
            text_content=text,
            preview_text=text[:200],
        )
        # 클립보드 먼저 입력 — _set_clipboard가 monitor._self_triggered를 설정해
        # 클립보드 모니터의 재감지(큐 중복 추가)를 막는다.
        self.interceptor._set_clipboard(item)
        self._persist_clipboard_item(item)  # 히스토리 + 큐

        # 포커스된 입력창에 자동 붙여넣기 — keyup 시점에 사용자가 단축키 수정키를 여전히
        # 누르고 있을 수 있어 _send_clean_key로 해제→Ctrl+V→복원(이미지→경로 단축키와 동일 이유).
        QTimer.singleShot(50, lambda: self.interceptor._send_clean_key(VK_V))

        flat = " ".join(text.split())
        preview = flat[:24]
        suffix = "…" if len(flat) > 24 else ""
        msg = f"✓ {preview}{suffix}"
        if not self._finish_cursor_progress(msg):
            from pasteflow.ui.toast import ToastNotification
            ToastNotification(msg, icon="🎤")

    def _on_stt_error(self, msg: str):
        from pasteflow.ui.toast import ToastNotification

        self._stop_cursor_progress()  # 진행 칩 정리(지속형이라 수동 종료 필요)

        if "API 키" in msg or "Base URL" in msg:
            ToastNotification("API 키/Base URL을 설정해 주세요", icon="🎤")
            QTimer.singleShot(300, self._open_settings)
            return
        ToastNotification(f"음성 인식 실패 — {msg}", icon="🎤")

