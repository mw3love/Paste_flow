"""질문을 **브라우저에서 직접** 열어 답을 보게 한다 — API로는 못 가져오는 것을 위해.

왜 이 경로가 필요한가 (2026-07-14 실측)
--------------------------------------
"오늘 SK하이닉스 주가"를 API 검색 경로(`web_search.py`의 nano 심부름꾼)에 세 번 물었더니
현재가를 **한 번도 못 맞혔다**(205만·208만·175만 — 실제 191.3만). 심부름꾼을 mini로
격상해도 마찬가지였다. 전일 종가 같은 '정적 사실'은 다 맞히는데 현재가만 틀린다.

이유는 모델이 약해서가 아니라 **수단이 못 닿아서**다. 실시간 시세는 페이지 본문 텍스트에
없고 JS가 그리는 값이라, 크롤링된 텍스트를 읽는 웹 검색 도구는 영영 볼 수 없다. 검색이
긁어올 수 있는 숫자는 뉴스 기사에 박제된 과거 시점 값이나 낡은 캐시뿐이고, 그게 오답의
정체다. 반면 구글 검색 AI 모드(`udm=50`)를 **브라우저에서 열면** 구글이 금융 피드를 직접
물고 있어 정확한 현재가가 차트째로 나온다(같은 질문에 1,913,000원 — 정답).

그래서 이 모듈은 답을 '가져오지' 않는다. 브라우저를 열어 **사용자가 직접 보게** 한다.
받아적기(API)에서 지던 싸움을 그만두고 전광판 앞에 데려다 놓는 것이다.

한계 — 답은 크롬에 뜨고 끝난다
------------------------------
프로그램이 그 답 텍스트를 읽지 못하므로 PasteFlow의 답변창 렌더·여러 모델 비교·"이미지로
복사"는 이 경로에 적용되지 않는다. 되가져오려면 크롬 확장이 필요하다(별개 작업).

URL 프리필 실측
---------------
- 구글 AI 모드: `?q=…&udm=50` → **질문이 자동 전송되고 답이 렌더된다**(로그인 불필요).
- 구글 드라이브: `/drive/search?q=…` → 검색이 바로 실행된다(크롬에 로그인돼 있으면 그대로).
- ⚠ 제미나이 앱(`gemini.google.com/app?q=…`)은 **`q`를 무시한다**(빈 입력칸만 뜬다).
  제미나이를 쓰려면 창을 띄운 뒤 클립보드+키 주입이 필요해 타이밍에 취약하므로,
  같은 일을 URL 한 줄로 해내는 위 두 경로를 먼저 쓴다.
"""
from __future__ import annotations

import webbrowser
from urllib.parse import quote_plus

# 구글 검색의 AI 모드 식별자. 검색 결과 목록 대신 AI 답변 화면으로 바로 들어간다.
_AI_MODE_PARAM = "udm=50"


def google_ai_url(query: str) -> str:
    """구글 검색 AI 모드 URL. 열면 질문이 자동 전송돼 AI 답변이 렌더된다."""
    return f"https://www.google.com/search?q={quote_plus(query.strip())}&{_AI_MODE_PARAM}"


def google_ai_home_url() -> str:
    """질문 없이 AI 모드를 연다 — 이미지를 Ctrl+V로 첨부해야 할 때의 시작점.

    이미지는 URL에 실을 수 없어 클립보드+키 주입이 필요하다. 다행히 이 화면은 **로드 직후
    입력칸(TEXTAREA)에 포커스가 자동으로 간다**(2026-07-14 실측) — 그래서 창을 띄운 뒤
    바로 Ctrl+V를 쏘면 그 칸에 꽂힌다.
    """
    return f"https://www.google.com/search?{_AI_MODE_PARAM}"


def is_browser_foreground() -> bool:
    """지금 포그라운드 창이 브라우저인가 — 키 주입 전 안전 검사(Windows).

    ⚠ 이 검사가 없으면 사고가 난다: 페이지 로드가 늦거나 사용자가 그 사이 다른 창을
    클릭하면, 우리가 쏜 Ctrl+V와 Enter가 **엉뚱한 앱**(메모장·채팅창·터미널)에 그대로
    들어간다. 주입은 URL 방식과 달리 "지금 앞에 누가 있는가"에 전적으로 의존하므로,
    확인되지 않으면 아무것도 하지 않는 편이 낫다.

    클래스명으로 판별한다 — 크롬 계열은 `Chrome_WidgetWin_*`(엣지·웨일 등 Chromium
    파생도 같다), 파이어폭스는 `MozillaWindowClass`.
    """
    try:
        import ctypes
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return False
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, buf, 256)
        cls = buf.value or ""
    except Exception:
        return False
    return cls.startswith("Chrome_WidgetWin") or cls == "MozillaWindowClass"


def drive_search_url(query: str) -> str:
    """내 구글 드라이브 검색 URL. 크롬 로그인 세션을 그대로 타므로 별도 인증이 없다."""
    return f"https://drive.google.com/drive/search?q={quote_plus(query.strip())}"


def open_url(url: str) -> bool:
    """기본 브라우저로 URL을 연다. 실패해도 예외를 올리지 않는다(호출자가 토스트로 안내).

    기본 브라우저를 쓰는 이유: 사용자가 이미 로그인해 둔 프로필이 그대로 물려야
    드라이브·제미나이가 재인증 없이 열린다. 브라우저를 새로 띄우는 자동화 도구
    (Playwright 등)를 쓰면 그 세션이 없어 로그인 화면부터 만난다.
    """
    try:
        return webbrowser.open(url)
    except Exception:
        return False
