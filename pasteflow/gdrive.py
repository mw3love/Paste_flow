"""구글 드라이브 OAuth — 커넥터 도구에 실을 액세스 토큰을 조달·갱신한다.

왜 이 모듈이 필요한가
--------------------
게이트웨이의 Responses API는 OpenAI 커넥터(`connector_googledrive`)를 중계한다(2026-07-13
실호출 검증: 도구 목록 노출 + 실제 파일명 반환). 커넥터에 넘겨야 하는 `authorization`은
**구글 액세스 토큰**인데, 이 토큰은 1시간이면 만료된다. 사용자가 매시간 OAuth Playground를
여는 건 제품이 아니므로, 앱이 refresh token을 쥐고 조용히 갱신해 주는 이 배관이 필요하다.

토큰 3종의 역할 (헷갈리기 쉬움)
------------------------------
- `client_id`/`client_secret` — "이 앱이 누구인가". Cloud Console에서 1회 발급, 계속 씀.
- `refresh_token`             — "사용자가 이 앱에 권한을 줬다"는 영구 증서. 1회 동의로 받아 보관.
- `access_token`             — 실제 호출에 쓰는 1시간짜리 입장권. refresh_token으로 계속 재발급.

**비유:** client_id/secret은 사원증, refresh_token은 출입 계약서, access_token은 매번 받는
1시간짜리 방문 스티커다. 우리가 DPAPI로 지켜야 하는 건 앞의 둘이다.

⚠ 동의 화면이 "테스트" 상태면 구글이 refresh_token을 **7일 만에 만료**시킨다. Cloud Console에서
앱을 "프로덕션"으로 게시해야 오래 산다(미검증 앱 경고는 1회 넘기면 됨 — 개인용이므로 무해).

의존성 없음 — 루프백 OAuth는 표준 라이브러리(http.server·webbrowser·urllib)로 충분하다.
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import json
import secrets
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from typing import Optional

# 읽기 전용 스코프. 커넥터가 제공하는 도구(search·fetch·recent_documents·list_drives)가
# 전부 읽기라 이보다 넓은 권한은 필요 없다 — 좁게 요청하는 것이 사고 시 피해를 줄인다.
SCOPE = "https://www.googleapis.com/auth/drive.readonly"

_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"

# 커넥터 식별자 — OpenAI가 관리하는 드라이브 MCP 래퍼.
CONNECTOR_ID = "connector_googledrive"

# 동의 대기 상한(초). 사용자가 브라우저를 닫고 잊어버려도 스레드가 영원히 매달리지 않게.
_CONSENT_TIMEOUT_SEC = 300

# 만료 몇 초 전부터 미리 갱신할지. 0으로 두면 "아직 유효" 판정 직후 만료되는 경합이 난다.
_REFRESH_MARGIN_SEC = 120


class GDriveError(RuntimeError):
    """OAuth 흐름 실패 — 호출자가 사용자에게 보여줄 메시지를 담는다."""


def _post_form(url: str, fields: dict) -> dict:
    """토큰 엔드포인트에 form-urlencoded POST. 실패는 GDriveError로 올린다.

    requests를 쓰지 않는 이유: 표준 라이브러리로 충분하고, 새 의존성은 그 자체로 비용이다.
    """
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:300]
        raise GDriveError(f"구글 토큰 요청 실패 ({e.code}): {body}") from e
    except Exception as e:
        raise GDriveError(f"구글 토큰 요청 실패: {type(e).__name__}: {e}") from e


class _CodeCatcher(http.server.BaseHTTPRequestHandler):
    """구글이 리다이렉트로 돌려주는 `?code=...`를 한 번 받아내는 최소 서버."""

    code: Optional[str] = None
    error: Optional[str] = None
    expected_state: str = ""

    def do_GET(self):  # noqa: N802 (BaseHTTPRequestHandler 규약)
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        state = (params.get("state") or [""])[0]
        if state != _CodeCatcher.expected_state:
            # state 불일치 = 우리가 시작한 흐름이 아니다(CSRF). 코드를 받지 않는다.
            _CodeCatcher.error = "state 불일치 — 인증을 다시 시도하세요."
        elif "code" in params:
            _CodeCatcher.code = params["code"][0]
        else:
            _CodeCatcher.error = (params.get("error") or ["동의가 취소되었습니다."])[0]

        ok = _CodeCatcher.code is not None
        msg = ("PasteFlow에 구글 드라이브를 연결했습니다. 이 창을 닫아도 됩니다."
               if ok else f"연결 실패: {_CodeCatcher.error}")
        body = (
            "<html><head><meta charset='utf-8'><title>PasteFlow</title></head>"
            "<body style='background:#121212;color:#e8e8e8;font-family:sans-serif;"
            "display:flex;align-items:center;justify-content:center;height:100vh;margin:0'>"
            f"<div style='text-align:center'><h2 style='color:#ff9e7d'>{msg}</h2></div>"
            "</body></html>"
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass  # 콘솔 오염 방지


def authorize(client_id: str, client_secret: str) -> str:
    """브라우저로 구글 동의를 받아 **refresh token**을 반환한다(1회, 설정창에서 호출).

    루프백 흐름: 임의 포트에 로컬 서버를 띄우고 그 주소를 redirect_uri로 준다. 데스크톱 앱
    클라이언트는 `http://127.0.0.1:<임의포트>`를 별도 등록 없이 허용하므로 포트를 고정할
    필요가 없다(고정하면 그 포트가 이미 쓰이는 PC에서 실패한다).

    PKCE를 함께 쓴다 — 데스크톱 앱의 client_secret은 바이너리에서 추출 가능해 진짜 비밀이
    아니므로, 코드 가로채기를 막는 실질 방어는 PKCE 쪽이다.

    UI를 블로킹하므로 **워커 스레드에서 호출**해야 한다(사용자 동의에 수십 초).
    """
    if not client_id or not client_secret:
        raise GDriveError("클라이언트 ID와 보안 비밀번호를 먼저 입력하세요.")

    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    state = secrets.token_urlsafe(16)

    _CodeCatcher.code = None
    _CodeCatcher.error = None
    _CodeCatcher.expected_state = state

    # 포트 0 = OS가 빈 포트를 골라 준다.
    server = http.server.HTTPServer(("127.0.0.1", 0), _CodeCatcher)
    redirect_uri = f"http://127.0.0.1:{server.server_port}/"

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        # access_type=offline + prompt=consent — 이 둘이 있어야 refresh_token이 확실히 온다.
        # (이미 동의한 계정은 prompt 없이는 refresh_token을 다시 주지 않아, 재연결 시
        #  토큰이 비어 오는 함정이 있다.)
        "access_type": "offline",
        "prompt": "consent",
    }
    webbrowser.open(f"{_AUTH_ENDPOINT}?{urllib.parse.urlencode(params)}")

    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    thread.join(timeout=_CONSENT_TIMEOUT_SEC)
    server.server_close()

    if _CodeCatcher.code is None:
        raise GDriveError(_CodeCatcher.error or "동의 대기 시간이 초과되었습니다.")

    payload = _post_form(_TOKEN_ENDPOINT, {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": _CodeCatcher.code,
        "code_verifier": verifier,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    })
    refresh = (payload.get("refresh_token") or "").strip()
    if not refresh:
        # prompt=consent를 줬는데도 안 왔다면 구글 쪽 정책 변화 — 조용히 넘기면 다음 호출에서
        # "연결됐다는데 안 된다"가 되므로 여기서 끊는다.
        raise GDriveError("구글이 refresh token을 주지 않았습니다. 동의 화면을 다시 시도하세요.")
    return refresh


def refresh_access_token(client_id: str, client_secret: str, refresh_token: str) -> tuple[str, int]:
    """refresh token으로 액세스 토큰을 재발급 → (access_token, 유효기간 초)."""
    payload = _post_form(_TOKEN_ENDPOINT, {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    })
    token = (payload.get("access_token") or "").strip()
    if not token:
        raise GDriveError("액세스 토큰을 받지 못했습니다. 드라이브를 다시 연결하세요.")
    return token, int(payload.get("expires_in") or 3600)


class TokenCache:
    """액세스 토큰을 물고 있다가 만료 전에 조용히 갱신한다(스레드 안전).

    AI 워커 여러 개가 동시에 토큰을 물어볼 수 있으므로(여러 모델 비교 = 병렬 3워커) 락으로
    갱신을 직렬화한다 — 안 그러면 세 워커가 각자 갱신 요청을 날린다.

    `creds()`는 호출 시점의 (client_id, client_secret, refresh_token)을 돌려주는 콜러블이다.
    설정이 바뀌면 다음 호출에 자동 반영되게 하려고 값이 아니라 함수를 받는다.
    """

    def __init__(self, creds):
        self._creds = creds
        self._lock = threading.Lock()
        self._token = ""
        self._expires_at = 0.0
        self._fingerprint: tuple = ()

    def access_token(self) -> str:
        """유효한 액세스 토큰. 연결 안 됐거나 갱신 실패면 **""**(호출자는 도구를 안 붙인다).

        예외를 올리지 않는 이유: 드라이브가 안 붙는다고 AI 질의 자체를 깨뜨리면 안 된다.
        토큰이 없으면 커넥터 도구만 빠지고 웹 검색은 그대로 동작한다(우아한 열화).
        """
        client_id, client_secret, refresh_token = self._creds()
        if not (client_id and client_secret and refresh_token):
            return ""

        # 설정이 바뀌면(계정 재연결 등) 캐시된 토큰은 남의 것이다 — 지문으로 감지해 버린다.
        fingerprint = (client_id, refresh_token)
        with self._lock:
            if fingerprint != self._fingerprint:
                self._token, self._expires_at, self._fingerprint = "", 0.0, fingerprint
            if self._token and time.monotonic() < self._expires_at:
                return self._token
            try:
                token, ttl = refresh_access_token(client_id, client_secret, refresh_token)
            except GDriveError:
                return ""
            self._token = token
            self._expires_at = time.monotonic() + max(60, ttl - _REFRESH_MARGIN_SEC)
            return self._token


def drive_tool(access_token: str) -> dict:
    """Responses API `tools` 배열에 넣을 드라이브 커넥터 도구 스펙.

    `require_approval="never"` — 승인 프롬프트는 대화형 UI가 있는 챗봇 전제라, 백그라운드
    워커에서는 응답이 영영 오지 않는다(사용자가 이미 설정창에서 연결에 동의했다).
    """
    return {
        "type": "mcp",
        "server_label": "gdrive",
        "connector_id": CONNECTOR_ID,
        "authorization": access_token,
        "require_approval": "never",
    }
