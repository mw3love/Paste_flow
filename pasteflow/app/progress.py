"""커서 옆 진행 칩(OCR·음성 입력 공용) — pasteflow/main.py에서 글자 그대로 옮김(2026-11-16 폴더 구조 2편)."""

from PyQt6.QtCore import QTimer


class CursorProgressMixin:
    def _start_cursor_progress(self, prefix: str, icon: str, anchor):
        """진행 칩 — 지속형 토스트(클릭 통과) + 0.5초 간격 경과시간 갱신. OCR 전용.

        anchor(QPoint)로 **커서가 있는 모니터**를 고른 뒤 그 모니터 정중앙에 표시한다
        (예측 가능·가장자리 잘림 없음). 예전엔 주 모니터 우하단 고정이라 보조 모니터
        작업 시 시선을 돌려야 했음.
        """
        import time
        from pasteflow.ui.toast import ToastNotification

        self._stop_cursor_progress()  # 중복 대비 이전 진행 칩 정리
        self._progress_prefix = prefix
        self._progress_toast = ToastNotification(
            f"{prefix} 0:00", icon=icon, duration_ms=0, anchor=anchor, center=True)
        self._progress_start = time.monotonic()
        self._progress_timer = QTimer()
        self._progress_timer.setInterval(500)
        self._progress_timer.timeout.connect(self._tick_cursor_progress)
        self._progress_timer.start()

    def _tick_cursor_progress(self):
        import time

        toast = getattr(self, "_progress_toast", None)
        if toast is None:
            return
        elapsed = int(time.monotonic() - self._progress_start)
        m, s = divmod(elapsed, 60)
        # 점(●··) 애니메이션은 제거 — ●(넓음)··(좁음) 폭 차이로 매 틱 칩 폭이 바뀌어
        # 앵커 재중심(_place_anchored)이 좌우로 흔들렸다. 경과시간만으로 "작업 중"이 충분히 보인다.
        toast.set_message(f"{self._progress_prefix} {m}:{s:02d}")

    def _stop_cursor_progress(self):
        """진행 칩·타이머 즉시 정리 (idempotent — 결과/에러 도착 시 호출)."""
        timer = getattr(self, "_progress_timer", None)
        if timer is not None:
            timer.stop()
            self._progress_timer = None
        toast = getattr(self, "_progress_toast", None)
        if toast is not None:
            toast.dismiss()
            self._progress_toast = None

    def _finish_cursor_progress(self, message: str, hold_ms: int = 1500) -> bool:
        """진행 칩을 결과 메시지(✓ 등)로 전환 후 잠시 뒤 fade. 같은 칩 재사용으로 부드럽게 전환.

        진행 칩이 없으면(이미 정리됨) False 반환 — 호출자가 일반 토스트로 폴백할 수 있다.
        """
        timer = getattr(self, "_progress_timer", None)
        if timer is not None:
            timer.stop()
            self._progress_timer = None
        toast = getattr(self, "_progress_toast", None)
        if toast is None:
            return False
        toast.set_message(message)
        self._progress_toast = None  # 더는 진행 칩으로 추적 안 함 (fade 예약됨)
        QTimer.singleShot(hold_ms, toast.dismiss)
        return True

