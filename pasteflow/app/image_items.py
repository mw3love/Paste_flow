"""이미지 항목 — AI 질문·경로 복사·파일 위치·순차 경로 붙여넣기 — pasteflow/main.py에서 글자 그대로 옮김(2026-11-16 폴더 구조 2편)."""

import os

from PyQt6.QtCore import QTimer

from pasteflow.models import ClipboardItem
from pasteflow.winapi.image_io import (
    _default_capture_folder, _save_image_to_drop_temp, _save_image_to_folder,
)


class ImageItemMixin:
    def _on_ask_ai_for_image(self, item: ClipboardItem):
        """이미지 우클릭 "Gemini에게 질문"(핀·미리보기·히스토리 공용) — 이미지를 미리 첨부한 채
        AI 질문창을 연다(2026-08-02 사용자 요청). image_data(DIB/PNG)를 PNG bytes로 변환해 첨부한다."""
        import io
        from PIL import Image
        from pasteflow.ui.toast import ToastNotification

        if not item.image_data:
            ToastNotification("이미지 데이터를 찾을 수 없습니다", icon="✨")
            return
        try:
            img = Image.open(io.BytesIO(item.image_data))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            png_bytes = buf.getvalue()
        except Exception as e:
            ToastNotification(f"이미지 변환 실패 — {e}", icon="✨")
            return
        self._open_ai_dialog(initial_image_png=png_bytes)

    def _on_copy_image_as_path(self, item_id: int):
        """우클릭 "파일로 저장 후 경로 복사"(item_id 기반) → DB 로드 후 항목 기반 코어로 위임."""
        item = self.db.get_item(item_id)
        if item:
            self._copy_image_as_path_for_item(item)

    def _on_open_file_location(self, item_id: int):
        """우클릭 "파일 위치 열기" — 항목이 가리키는 파일을 탐색기에서 선택 표시.
        텍스트 항목은 경로 텍스트(녹화 GIF/영상·"파일로 저장 후 경로 복사"·이미지→경로
        단축키 등으로 생긴 것), 이미지 항목은 영역 캡처(Alt+F2)가 저장한 원본 PNG
        (`saved_image_path` — 캡처 시점에 함께 기록됨, 위 `_on_capture_region` 참고)를
        쓴다(panel._on_item_context_menu가 파일이 실제 존재할 때만 이 항목을 활성화함).
        `explorer /select,` 는 해당 파일이 든 폴더를 열고 그 파일을 하이라이트한다
        (shell=True 없이 인자 리스트로 호출해 경로에 특수문자가 섞여도 안전).

        ⚠ `os.path.normpath()`로 슬래시를 백슬래시로 통일한 뒤 넘겨야 한다 — 실측 확인:
        `capture_save_folder` 설정에 슬래시(`/`)가 섞여 있으면(사용자가 직접 입력해
        저장한 경우) `os.path.join`이 뒤에 붙이는 파일명과 합쳐져 `C:/Users/...\\file.mp4`
        같은 혼합 경로가 되는데, `explorer.exe`가 `/select,` 뒤 경로 안의 `/`를 새 스위치
        구분자로 오인해 대상 폴더 대신 그냥 "내 PC"를 연다(`os.path.isfile`은 슬래시
        혼용도 정상 인식하므로 위 존재 확인은 통과 — 이 버그를 못 잡음).
        """
        from pasteflow.ui.toast import ToastNotification
        import subprocess
        item = self.db.get_item(item_id)
        if item and item.content_type == "image":
            path = (item.saved_image_path or "").strip()
        else:
            path = (item.text_content or "").strip() if item else ""
        if not path or not os.path.isfile(path):
            ToastNotification("파일을 찾을 수 없습니다", icon="📁")
            return
        try:
            subprocess.Popen(["explorer", f"/select,{os.path.normpath(path)}"])
        except Exception as e:
            ToastNotification(f"탐색기 열기 실패 — {e}", icon="📁")

    def _on_ask_ai_item_by_id(self, item_id: int):
        """히스토리 우클릭 "Gemini에게 질문"(item_id 기반, 2026-08-03) → DB에서 풀 로드 후
        핀·미리보기와 공유하는 코어(_on_ask_ai_for_image)로 위임."""
        from pasteflow.ui.toast import ToastNotification
        item = self.db.get_item(item_id)
        if not item:
            ToastNotification("항목을 찾을 수 없습니다", icon="✨")
            return
        self._on_ask_ai_for_image(item)

    def _copy_image_as_path_for_item(self, item: ClipboardItem):
        """캡처 저장 폴더(Alt+F2와 동일 — 설정 `capture_save_folder`, 기본 <사진>\\PasteFlow)에
        PNG로 저장 후 절대경로를 클립보드에 텍스트로 복사.
        Claude CLI 등 "경로 텍스트"를 첨부로 받는 앱에 사용자가 직접 Ctrl+V로 붙여넣기 위한 경로.
        DB id가 없는 임시 항목(화면 핀 등)도 받을 수 있도록 ClipboardItem을 직접 받는다.

        %TEMP%가 아니라 캡처 폴더를 쓰는 이유: 이 메뉴는 "파일로 저장"이라 사용자가
        결과물을 나중에 다시 찾을 수 있어야 하는데, 임시 폴더는 관리 폴더가 두 개로
        나뉘는 데다 시스템이 언제든 비울 수 있어 부적합하다(2026-08-02 사용자 판단).
        이미지→경로 단축키(Ctrl+Shift+P·[)·Alt+드래그는 반복 실행되는 일회성 브리지
        파일이라 여기와 무관하게 계속 `_save_image_to_drop_temp`(temp)를 쓴다.
        """
        from pasteflow.ui.toast import ToastNotification
        if not item or not item.image_data:
            ToastNotification("이미지 데이터를 찾을 수 없습니다", icon="🔤")
            return
        folder = self.db.get_setting("capture_save_folder", "") or _default_capture_folder()
        try:
            os.makedirs(folder, exist_ok=True)
            saved_path = _save_image_to_folder(item.image_data, folder)
        except Exception as e:
            ToastNotification(f"파일 저장 실패 — {e}", icon="🔤")
            return
        path_item = ClipboardItem(
            content_type="text",
            text_content=saved_path,
            preview_text=saved_path[:200],
        )
        # _set_clipboard가 monitor._self_triggered를 설정해 히스토리 자동 추가 방지
        self.interceptor._set_clipboard(path_item)
        # 썸네일을 함께 띄워 "복사한 게 의도한 이미지가 맞는지" 그 자리에서 시각 확인
        ToastNotification(
            f"경로 복사됨: {os.path.basename(saved_path)}",
            icon="", image_path=saved_path)

    def _on_seq_image_to_path_hotkey(self):
        """순차 경로 붙여넣기 단축키(기본 Ctrl+Shift+P) — 큐에서 다음 항목을 꺼내되
        이미지면 임시 PNG 경로 텍스트로 렌더해 붙여넣는다.

        Ctrl+Shift+V(순차)와 **같은 큐·포인터를 공유하는 '경로 버전'**. 캡처(Alt+F2)가
        이미 큐에 이미지로 쌓이므로(_on_capture_region → _persist_clipboard_item), 캡처
        여러 장을 이 키로 순서대로 경로 텍스트로 붙일 수 있다(예: 캡처1·2 → 이 키 두 번 →
        경로1·2). 이미지가 아닌 항목은 원본 그대로 붙여(Ctrl+Shift+V와 동일). 큐가 소진되면
        토스트만 표시하고 아무것도 하지 않는다(옛 한 번짜리 경로 붙여넣기는 2026-09-25 제거 —
        같은 경로를 여러 번 붙일 일이 드물고, 필요하면 패널에서 따로 복사한다).

        클립보드 교체는 _set_clipboard(모니터 재감지 방지)로, 주입은 _send_clean_key(VK_V)로
        수정키(Ctrl+Shift+Alt) 해제·복원 + 입력기 전환 마스킹을 거친다(일반 경로 붙여넣기와 동일).
        """
        from pasteflow.ui.toast import ToastNotification
        from pasteflow.paste_interceptor import VK_V

        next_item = self.queue.get_next()
        if next_item is None:
            ToastNotification("순차 큐가 비었습니다", icon="🔤")
            return

        # summary 항목이면 전체 로드 (이미지 항목은 image_data가 인라인이라 대개 불필요)
        if not next_item.image_data and not next_item.extra_formats and next_item.id:
            full = self.db.get_item(next_item.id)
            if full:
                next_item = full

        if next_item.content_type == "image" and next_item.image_data:
            try:
                saved_path = _save_image_to_drop_temp(next_item.image_data)
            except Exception as e:
                ToastNotification(f"임시 파일 저장 실패 — {e}", icon="🔤")
                return
            # Alt+F3 핀이 "방금 붙여넣은 경로"를 원본 이미지로 되살리기 위해 경로를 기억
            self._last_pasted_image_path = saved_path
            path_item = ClipboardItem(
                content_type="text",
                text_content=saved_path,
                preview_text=saved_path[:200],
            )
            self.interceptor._set_clipboard(path_item)
            QTimer.singleShot(50, lambda: self.interceptor._send_clean_key(VK_V))
            # 단발 경로 붙여넣기와 동일하게, 대상 앱이 경로를 다 읽은 뒤 클립보드를
            # 원본 이미지로 복원 — 이후 일반 Ctrl+V가 경로 재붙임이 아닌 이미지 붙여넣기가 되게 함.
            QTimer.singleShot(250, lambda: self.interceptor._set_clipboard(next_item))
            ToastNotification(
                f"경로 붙여넣음: {os.path.basename(saved_path)}",
                icon="", image_path=saved_path)
        else:
            # 이미지가 아니면 원본 그대로 붙여넣기(Ctrl+Shift+V와 동일 처리)
            self.interceptor._set_clipboard(next_item)
            QTimer.singleShot(50, lambda: self.interceptor._send_clean_key(VK_V))

        # 진행 HUD 갱신 + 큐 소진 시 정리 (Ctrl+Shift+V 경로와 동일 표시)
        self._update_paste_ui()
        pointer, total = self.queue.get_status()
        if pointer >= total and total > 0:
            # Ctrl+Shift+V 소진과 동일하게 큐 클리어 + HUD 페이드 (찌꺼기 방지)
            self._on_paste_queue_done()

