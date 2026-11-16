"""GIF·영상 녹화(Ctrl+Shift+G) — pasteflow/main.py에서 글자 그대로 옮김(2026-11-16 폴더 구조 2편)."""

import os

from PyQt6.QtCore import QRect, QTimer

from pasteflow.models import ClipboardItem
from pasteflow.winapi.image_io import _default_capture_folder


class RecordingMixin:
    # ── GIF 녹화 (Ctrl+Shift+G) ──

    def _recording_in_progress(self) -> bool:
        return (
            (self._gif_recorder is not None and self._gif_recorder.is_active())
            or (self._video_recorder is not None and self._video_recorder.is_active())
        )

    def _on_record_hotkey(self):
        """메인 스레드: 녹화 단축키(기본 Ctrl+Shift+R) — 이미 녹화 중이 아니면 선택 오버레이
        (select_only)를 띄운다. GIF/영상은 영역을 고른 뒤 뜨는 선택 바에서 정한다.

        2026-09-25에 옛 GIF(Ctrl+Shift+G)·영상(Ctrl+Shift+R) 두 단축키를 하나로 합쳤다
        — 영역 지정까지는 완전히 같은 흐름이라(사용자 요청). capture_overlay는 '얼린'
        스냅이라 녹화엔 부적합 — 여기선 사각형만 받고(region_selected →
        _on_record_region_selected) 그 자리를 라이브로 연속 캡처한다.
        """
        from pasteflow.ui.toast import ToastNotification
        if self._recording_in_progress():
            ToastNotification("이미 녹화 중입니다 (■ 정지 또는 ESC)", icon="🎬")
            return
        if self._record_chooser is not None:  # 선택 바가 떠 있으면 새로 시작하지 않는다
            return
        self._capture_overlay.start(select_only=True)

    def _on_record_region_selected(self, rect):
        """메인 스레드: 영역이 정해지면 그 옆에 [GIF] [영상] 선택 바를 띄운다.

        Enter로 고를 기본값은 지난번 선택(DB `record_last_mode`) — 한 방식만 계속 쓰는
        사람은 Enter 한 번으로 끝난다.
        """
        from pasteflow.ui.record_chooser import RecordModeChooser

        last = self.db.get_setting("record_last_mode", "gif")
        chooser = RecordModeChooser(QRect(rect), default_mode=last)
        chooser.chosen.connect(lambda mode, r=QRect(rect): self._on_record_mode_chosen(mode, r))
        chooser.cancelled.connect(self._on_record_chooser_cancelled)
        self._record_chooser = chooser
        chooser.show_chooser()

    def _on_record_mode_chosen(self, mode: str, rect):
        """선택 바에서 방식을 고름 → 기억해 두고 녹화 시작(오버레이·선택 바 잔상 방지 150ms 지연은
        각 _start_*가 이미 둔다)."""
        self._record_chooser = None
        self.db.set_setting("record_last_mode", mode)
        if mode == "video":
            self._start_video_recording(rect)
        else:
            self._start_gif_recording(rect)

    def _on_record_chooser_cancelled(self):
        self._record_chooser = None
        self._on_record_cancelled()

    def _start_gif_recording(self, rect):
        from pasteflow.gif_recorder import GifRecorder

        try:
            fps = int(self.db.get_setting("gif_fps", "12") or 12)
        except ValueError:
            fps = 12
        try:
            max_sec = int(self.db.get_setting("gif_max_seconds", "15") or 15)
        except ValueError:
            max_sec = 15
        show_cursor = self.db.get_setting("gif_show_cursor", "1") != "0"

        self._gif_recorder = GifRecorder(fps=fps, max_seconds=max_sec, show_cursor=show_cursor)
        self._gif_recorder.finished.connect(self._on_record_finished)
        self._gif_recorder.cancelled.connect(self._on_record_cancelled)
        QTimer.singleShot(150, lambda: self._gif_recorder.start(QRect(rect)))

    def _start_video_recording(self, rect):
        """영상(MP4) 녹화 시작 — GIF와 달리 프레임을 즉시 파일에 쓰므로 별도 인코딩 워커가 없다."""
        from pasteflow.video_recorder import VideoRecorder
        from datetime import datetime

        from pasteflow.ui.toast import ToastNotification

        show_cursor = self.db.get_setting("gif_show_cursor", "1") != "0"
        try:
            fps = int(self.db.get_setting("video_fps", "15") or 15)
        except ValueError:
            fps = 15
        try:
            max_sec = int(self.db.get_setting("video_max_seconds", "600") or 600)
        except ValueError:
            max_sec = 600
        folder = self.db.get_setting("capture_save_folder", "") or _default_capture_folder()
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as e:
            ToastNotification(f"영상 저장 폴더를 만들 수 없습니다 — {e}", icon="🎬")
            return
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(folder, f"vid_{ts}.mp4")

        self._video_recorder = VideoRecorder(fps=fps, max_seconds=max_sec, show_cursor=show_cursor)
        self._video_recorder.finished.connect(self._on_video_saved)
        self._video_recorder.cancelled.connect(self._on_record_cancelled)
        QTimer.singleShot(150, lambda: self._start_video_recorder_now(rect, path))

    def _start_video_recorder_now(self, rect, path):
        """cv2.VideoWriter가 코덱을 못 열면 예외 없이 조용히 무효화되므로(video_recorder.py의
        isOpened() 체크가 그걸 RuntimeError로 바꿔줌) 여기서 잡아 토스트로 알린다."""
        from pasteflow.ui.toast import ToastNotification
        try:
            self._video_recorder.start(QRect(rect), path)
        except Exception as e:
            ToastNotification(f"영상 녹화 시작 실패 — {e}", icon="🎬")

    def _on_video_saved(self, path):
        """메인 스레드: 영상 저장 완료 — GIF(_on_gif_saved)와 동일 패턴(경로 복사+히스토리·큐 저장+토스트).

        mp4는 프레임을 이미 즉시 파일에 써 왔으므로(video_recorder.py) 별도 인코딩 단계가 없다.
        """
        from pasteflow.ui.toast import ToastNotification
        path_item = ClipboardItem(
            content_type="text",
            text_content=path,
            preview_text=path[:200],
        )
        self.interceptor._set_clipboard(path_item)
        self._persist_clipboard_item(path_item)
        from pasteflow.video_recorder import extract_first_frame_png
        try:
            thumb = extract_first_frame_png(path)
        except Exception:
            thumb = None
        ToastNotification(
            f"영상 저장·경로 복사됨: {os.path.basename(path)}",
            icon="" if thumb else "🎬", image_bytes=thumb)

    def _on_record_finished(self, frames, interval_ms):
        """메인 스레드: 녹화 종료 → 인코딩을 워커에 넘긴다(프레임 수가 많으면 UI 블로킹)."""
        from pasteflow.ui.toast import ToastNotification
        ToastNotification(f"GIF 인코딩 중… ({len(frames)}프레임)", icon="🎬")
        self._gif_executor.submit(self._encode_gif_worker, frames, interval_ms)

    def _on_record_cancelled(self):
        from pasteflow.ui.toast import ToastNotification
        ToastNotification("녹화 취소됨", icon="🎬")

    def _encode_gif_worker(self, frames, interval_ms):
        """워커 스레드: 프레임 → GIF 파일. 결과/에러를 시그널로 메인에 되돌린다."""
        from datetime import datetime
        from pasteflow import gif_recorder
        try:
            folder = self.db.get_setting("capture_save_folder", "") or _default_capture_folder()
            os.makedirs(folder, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = os.path.join(folder, f"rec_{ts}.gif")
            gif_recorder.encode_gif(frames, path, interval_ms)
            self._bridge.gif_saved.emit(path)
        except Exception as e:
            self._bridge.gif_error.emit(str(e))

    def _on_gif_saved(self, path):
        """메인 스레드: GIF 저장 완료 → 경로를 클립보드 텍스트로 복사 + 히스토리·큐 저장 + 썸네일 토스트.

        GIF는 클립보드에 애니메이션으로 못 올라가므로(이미지→경로 패턴과 동일) 파일 경로를
        클립보드에 넣어 노션·슬랙 등엔 경로/파일로 넘긴다. 단 이미지→경로(Ctrl+Shift+P)와
        달리 이 경로에는 히스토리에 남아 있는 '원본'이 따로 없다(녹화 자체가 새 콘텐츠) —
        캡처(Alt+F2)와 같은 성격이라 여기서도 _persist_clipboard_item으로 저장한다.
        """
        from pasteflow.ui.toast import ToastNotification
        path_item = ClipboardItem(
            content_type="text",
            text_content=path,
            preview_text=path[:200],
        )
        self.interceptor._set_clipboard(path_item)
        self._persist_clipboard_item(path_item)
        ToastNotification(
            f"GIF 저장·경로 복사됨: {os.path.basename(path)}",
            icon="", image_path=path)

    def _on_gif_error(self, msg):
        from pasteflow.ui.toast import ToastNotification
        ToastNotification(f"GIF 인코딩 실패 — {msg}", icon="🎬")

