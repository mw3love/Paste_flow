"""훅·워커 스레드 → 메인 스레드 시그널 다리 — pasteflow/main.py에서 글자 그대로 옮김(2026-11-16 폴더 구조 2편)."""

from PyQt6.QtCore import QObject, pyqtSignal


class _SignalBridge(QObject):
    """훅 스레드 → 메인 스레드 시그널 전달"""
    paste_happened     = pyqtSignal()
    new_item_saved     = pyqtSignal(object)  # 모든 복사 경로: DB + 큐 추가
    copy_toast         = pyqtSignal(object, int)  # 복사 알림 토스트 (item, 큐 개수)
    panel_toggle       = pyqtSignal()        # 패널 토글 단축키 (훅 스레드 → 메인)
    paste_queue_done   = pyqtSignal()        # 큐 소진 (붙여넣기 HUD fade-out용)
    plain_paste        = pyqtSignal()        # 일반 Ctrl+V 감지 (훅 스레드 → 메인: 큐 clear + UI 갱신)
    ocr_requested      = pyqtSignal()        # 훅 스레드 → 메인: OCR 오버레이 띄우기
    ocr_done           = pyqtSignal(str)     # 워커 스레드 → 메인: OCR 결과 텍스트
    ocr_translated     = pyqtSignal(str)     # 워커 스레드 → 메인: OCR 번역 결과(Shift+드래그)
    ocr_error          = pyqtSignal(str)     # 워커 스레드 → 메인: 에러 메시지
    ocr_fallback       = pyqtSignal(str, str)  # 워커 스레드 → 메인: (실패 모델, 폴백 모델) 자동 폴백 알림
    seq_image_to_path  = pyqtSignal()        # 훅 스레드 → 메인: 큐에서 다음 항목을 꺼내 이미지면 경로 텍스트로 순차 붙여넣기
    bulk_paste         = pyqtSignal()        # 훅 스레드 → 메인: 큐 전체를 간격 두고 순차 자동주입(Ctrl+Shift+V 벌크 버전)
    bulk_path_paste    = pyqtSignal()        # 훅 스레드 → 메인: 큐 전체를 경로 텍스트로 간격 두고 순차 자동주입(Ctrl+Shift+[ 벌크 버전)
    pin_image          = pyqtSignal()        # 훅 스레드 → 메인: 클립보드 이미지를 화면에 핀(떠 있는 창)으로 띄우기
    new_memo           = pyqtSignal()        # 훅 스레드 → 메인: 메모장에 새 메모를 만들고 메모창 열기
    live_draw          = pyqtSignal()        # 훅 스레드 → 메인: 라이브 화면 판서 켜기 / 그리기↔앱 쓰기 전환
    capture_requested  = pyqtSignal()        # 훅 스레드 → 메인: 영역 캡처 오버레이 띄우기
    record             = pyqtSignal()        # 훅 스레드 → 메인: 녹화 영역 선택 오버레이 띄우기(GIF/영상은 선택 뒤 고름)
    gif_saved          = pyqtSignal(str)     # 인코딩 워커 → 메인: 저장된 GIF 경로
    gif_error          = pyqtSignal(str)     # 인코딩 워커 → 메인: 에러 메시지
    annotation_copied  = pyqtSignal(bytes)   # 주석 복사 워커 → 메인: 클립보드+DB 저장 완료(썸네일 토스트용)
    panel_paste_done   = pyqtSignal()        # 패널 붙여넣기 워커 → 메인: _paste_in_progress 해제
    stt_start          = pyqtSignal()        # 훅 스레드 → 메인: 음성 입력 단축키 keydown(녹음 시작)
    stt_stop           = pyqtSignal()        # 훅 스레드 → 메인: 음성 입력 단축키 keyup(녹음 종료+전송)
    stt_done           = pyqtSignal(str)     # 워커 스레드 → 메인: STT 결과 텍스트
    stt_error          = pyqtSignal(str)     # 워커 스레드 → 메인: 에러 메시지
