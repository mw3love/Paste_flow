"""OCR 엔진 추상화 — Windows WinRT 기본, Gemini 옵션.

설계
----
- recognize(PIL.Image) → str (동기). UI 블로킹 방지를 위해 호출자가 워커 스레드에서 실행.
- winocr 패키지가 winrt-* 계열을 래핑해 recognize_pil_sync() 동기 API를 제공.
  winsdk는 Python 3.14 미지원으로 채택하지 않음.
- 언어 지원 확인은 winrt.windows.media.ocr.OcrEngine.is_language_supported()로.
"""
from __future__ import annotations

import threading
from typing import Literal, NamedTuple, Optional

from PIL import Image

EngineKind = Literal["winrt", "gemini"]


def _normalize_base_url(base_url: str) -> str:
    """OpenAI 호환 게이트웨이 base_url 정규화.

    사용자가 실수로 endpoint 전체 경로를 붙여넣어도 SDK 표준 형식으로 보정.
    예: '.../v1/gateway/chat/completions' → '.../v1/gateway'
    """
    if not base_url:
        return base_url
    url = base_url.strip().rstrip("/")
    for suffix in ("/chat/completions", "/completions", "/models", "/embeddings"):
        if url.endswith(suffix):
            url = url[: -len(suffix)]
            break
    return url

# ── 폴백 안전망 ──────────────────────────────────────────────────────────────
# 호출이 `model_not_found`로 깨졌을 때 조용히 갈아탈 모델 사슬. 앞에서부터 실패
# 모델이 아닌 첫 항목을 쓴다.
#
# 옛 `model_matrix.json`(전 모델 능력표)은 폐기했다. 스윕 시점 스냅샷이라 게이트웨이가
# 라인업을 바꾸면 즉시 낡는데, 그 낡은 판정으로 설정창이 모델 **선택을 차단**했다 —
# 빌드에 번들되는 데이터라 사용자가 고칠 수도 없었다. 모델이 되는지는 이제 설정창의
# `연결 테스트`가 **그 자리에서 실호출**해 확인한다(`probe_*` 함수들).
#
# 이 사슬만 상수로 남기는 이유: 폴백은 사용자가 볼 수 없는 자리에서 일어나므로 후보를
# 실호출로 정할 기회가 없다.
_FALLBACK_DEFAULT = "gemini-2.5-flash"
_FALLBACK_CHAIN = (_FALLBACK_DEFAULT, "gemini-2.0-flash")

# 계열 표시 순서. 각 항목은 (표시명, 모델 ID 접두사들).
# 매칭은 `/`로 구분된 경로의 **마지막 조각**을 소문자화해 접두사 비교한다
# (예: "accounts/fireworks/models/gpt-oss-120b" → "gpt-oss-120b" → GPT).
_FAMILIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Gemini", ("gemini",)),
    ("Claude", ("claude",)),
    ("GPT", ("gpt", "o1-", "o3-")),
    ("Grok", ("grok",)),
    ("Gemma", ("gemma",)),
    ("Llama", ("llama", "meta-llama")),
    ("Sonar", ("sonar",)),
    ("Solar", ("solar",)),
    ("EXAONE", ("exaone", "k-exaone", "lgai")),
)
_FAMILY_OTHER = "기타"


def family_of(name: str) -> str:
    """모델 ID에서 계열 표시명. 어디에도 안 걸리면 '기타'."""
    base = name.rsplit("/", 1)[-1].lower()
    full = name.lower()
    for label, prefixes in _FAMILIES:
        if any(base.startswith(p) or full.startswith(p) for p in prefixes):
            return label
    return _FAMILY_OTHER


def group_models(candidates: list[str]) -> list[tuple[str, list[str]]]:
    """계열별로 묶어 `[(계열명, [모델…]), …]` 반환. 빈 계열은 생략.

    계열 순서는 `_FAMILIES` 고정 순서(마지막이 '기타'). 계열 안에서는 대소문자 무시
    알파벳순 — `LGAI-EXAONE/...` 같은 대문자 ID가 ASCII 순서로 소문자 앞에 튀어나오는
    것을 막는다.

    (옛 `usable` 인자는 매트릭스와 함께 제거됐다. 어떤 모델이 되는지는 이 목록을
    정렬할 때가 아니라 설정창 `연결 테스트`가 실호출로 판정한다.)
    """
    buckets: dict[str, list[str]] = {}
    for name in candidates:
        buckets.setdefault(family_of(name), []).append(name)

    out: list[tuple[str, list[str]]] = []
    for label in [lbl for lbl, _ in _FAMILIES] + [_FAMILY_OTHER]:
        names = buckets.get(label)
        if not names:
            continue
        names.sort(key=str.lower)
        out.append((label, names))
    return out


def _is_model_not_found(exc: Exception) -> bool:
    """OCR 호출 예외가 '존재하지 않는 모델'을 의미하는지 휴리스틱 판정.

    OpenAI 호환 게이트웨이는 404 본문에 'model ... not found'를 담아 보낸다. 게이트웨이·
    SDK 버전마다 예외 타입이 달라 메시지 내용 기반 판정이 가장 안정적.
    """
    msg = str(exc).lower()
    if "not found" in msg:
        return True
    if "model_not_found" in msg:
        return True
    # 게이트웨이는 본문 자체에 "Error code: 404"를 포함시킴
    if "404" in msg and "model" in msg:
        return True
    return False


def _is_quota_error(exc: Exception) -> bool:
    """호출 예외가 할당량 초과(429 RESOURCE_EXHAUSTED)를 의미하는지 휴리스틱 판정.

    프로브(`_classify_probe_error`)가 이걸로 `retry`(서버 사정으로 **판정 불가**)와
    `fail`(이 모델·키로는 안 됨)을 가른다 — 섞으면 멀쩡한 모델을 나쁜 모델로 표시한다.
    게이트웨이·SDK 버전마다 예외 타입이 달라 메시지 기반 판정이 가장 안정적.
    """
    msg = str(exc).lower()
    return "429" in msg or "resource_exhausted" in msg or "quota" in msg


def select_fallback_model(failed_model: str) -> Optional[str]:
    """호출이 실패한 모델을 대신할 폴백 후보 1개. 남은 후보가 없으면 None.

    `_FALLBACK_CHAIN`에서 실패 모델이 아닌 첫 항목.
    """
    for candidate in _FALLBACK_CHAIN:
        if candidate != failed_model:
            return candidate
    return None


# WinRT OcrEngine.MaxImageDimension (4096px 초과 이미지는 에러)
_WINRT_MAX_DIM = 4096
# OCR 전 이미지 4방향 여백 — 한 줄짜리 좁은 이미지에서 WinRT 인식률 향상
_OCR_PAD = 16

# ── winocr/winrt lazy load ──────────────────────────────────────────────────
_winocr_checked = False
_winocr_error: Optional[str] = None


def _check_winocr() -> bool:
    """winocr 패키지 import 가능 여부를 한 번 확인하고 캐시."""
    global _winocr_checked, _winocr_error
    if _winocr_checked:
        return _winocr_error is None
    try:
        import winocr  # noqa: F401
        _winocr_error = None
    except ImportError as e:
        _winocr_error = f"winocr 미설치: {e}. pip install winocr"
    _winocr_checked = True
    return _winocr_error is None


# ── OpenAI 호환 클라이언트 캐시 ──────────────────────────────────────────────
# 옛 코드는 OCR 호출마다 openai.OpenAI()를 새로 만들었다 — 그러면 매 호출이 TCP+TLS
# 핸드셰이크를 새로 치른다(연결 재사용 0). (api_key, base_url)별로 클라이언트를 캐싱해
# 같은 크리덴셜의 호출들이 커넥션 풀(keep-alive)을 공유하게 한다 — 앱을 오래 켜둔
# 채로 여러 번 OCR을 써도 매번 새 핸드셰이크를 안 치른다.
_client_cache: dict[tuple[str, str], object] = {}
_client_cache_lock = threading.Lock()


def _get_client(api_key: str, base_url: str):
    """(api_key, base_url)별로 openai.OpenAI 클라이언트를 캐싱해 커넥션 풀을 재사용한다."""
    import openai

    norm_url = _normalize_base_url(base_url)
    cache_key = (api_key, norm_url)
    with _client_cache_lock:
        client = _client_cache.get(cache_key)
        if client is None:
            client = openai.OpenAI(api_key=api_key, base_url=norm_url)
            _client_cache[cache_key] = client
        return client


def warm_connection(api_key: str, base_url: str) -> None:
    """앱 시작 시 백그라운드 스레드에서 호출 — `openai` 패키지 임포트 + 클라이언트 생성 +
    가벼운 `/models` 조회로 DNS 조회·TCP/TLS 핸드셰이크를 앱 시작 시점에 미리 끝내 둔다.

    실제 첫 OCR 호출이 `_get_client`로 같은 캐시된 클라이언트를 재사용하므로, 이 워밍업이
    끝난 뒤라면 첫 OCR도 커넥션 설정 비용 없이 모델 응답 시간만 기다리면 된다.
    크리덴셜이 비어 있거나 워밍업 자체가 실패해도 조용히 무시한다 — 최선 노력형이며,
    실패해도 실제 OCR 호출은 평소 경로(에러 시 사용자에게 표시)로 그대로 진행된다.
    """
    if not api_key or not base_url:
        return
    try:
        client = _get_client(api_key, base_url)
        client.models.list()
    except Exception:
        pass


# ── AI OCR 프롬프트 ──────────────────────────────────────────────────────────

def _ocr_prompt(language: str) -> str:
    if language.startswith("ko"):
        return (
            "Extract all text from this image with high accuracy. "
            "This image contains Korean text — carefully distinguish similar-looking "
            "Korean characters (e.g. ㅂ/ㅍ, ㄱ/ㄴ/ㄷ/ㄹ, ㅅ/ㅈ/ㅊ, ㅗ/ㅛ/ㅜ/ㅠ, 이/의/에). "
            "Preserve line breaks. Output only the extracted text with no explanation or commentary."
        )
    return (
        "Extract all text from this image exactly as it appears. "
        "Preserve line breaks. Output only the extracted text with no explanation or commentary."
    )


# ── 엔진 ────────────────────────────────────────────────────────────────────


class OcrEngine:
    """OCR 추상화 — kind에 따라 WinRT/AI API 분기."""

    def __init__(
        self,
        kind: EngineKind = "winrt",
        api_key: str = "",
        base_url: str = "",
        language: str = "ko",
        model: str = "",
    ):
        self.kind: EngineKind = kind
        self.api_key = api_key
        self.base_url = base_url
        self.language = language
        self.model = model
        # OCR 호출이 끝났을 때 main이 토스트로 안내할 수 있도록 남기는 상태:
        # last_used_model    — 실제 응답을 만든 모델 (폴백 발생 시 폴백 모델)
        # last_fallback_from — 원래 시도했다가 실패한 모델 (폴백 없으면 None)
        self.last_used_model: str = ""
        self.last_fallback_from: Optional[str] = None

    def recognize(self, pil_image: Image.Image) -> str:
        """동기 OCR — 호출자가 워커 스레드에서 실행해야 UI 블로킹이 없다."""
        if self.kind == "winrt":
            return self._recognize_winrt(pil_image)
        if self.kind == "gemini":
            return self._recognize_gemini(pil_image)
        raise ValueError(f"Unknown OCR engine kind: {self.kind!r}")

    # ── 진단용 정적 메서드 ──

    @staticmethod
    def is_winrt_available() -> bool:
        """winocr 패키지가 설치되어 있는지."""
        return _check_winocr()

    @staticmethod
    def is_winrt_language_supported(lang_code: str = "ko") -> bool:
        """Windows에 해당 언어팩이 있어 OCR 가능한지.

        winrt.windows.media.ocr.OcrEngine.is_language_supported() 사용.
        winocr가 같은 winrt-* 패키지를 의존하므로 winocr 설치 시 사용 가능.
        """
        try:
            from winrt.windows.media.ocr import OcrEngine as WinOcrEngine
            from winrt.windows.globalization import Language
            return bool(WinOcrEngine.is_language_supported(Language(lang_code)))
        except Exception:
            return False

    @staticmethod
    def list_gemini_models(api_key: str, base_url: str = "") -> list[str]:
        """게이트웨이의 `/v1/models`에서 사용 가능한 모델 ID 목록을 조회한다.

        **모델 계열 필터 없이 전부 반환** — 게이트웨이 호출 경로
        (`_recognize_openai_compat`/`_ask_openai_compat`)는 평범한 OpenAI 호환
        chat.completions라 claude·gpt·grok 등 gemini가 아닌 모델도 그대로 동작한다.
        옛 `"gemini" in id` 필터는 게이트웨이가 제공하는 45종 중 6종만 노출시켜
        사용자가 다른 모델을 고를 수 없게 만들던 제약이라 제거했다.

        **여기서 아무것도 걸러내지 않는다.** 못 쓰는 모델(유령 404·이미지 400·TTS)을
        목록에서 조용히 지우면 사용자는 "왜 이 모델이 없지?"를 알 수 없다. 어떤 모델이
        실제로 되는지는 설정창 `연결 테스트`가 그 모델을 실호출해(`probe_*`) 알려준다.
        메서드 이름은 하위 호환을 위해 유지(gemini 전용이 아니다).

        실패 시 RuntimeError. UI 블로킹 방지를 위해 호출자가 워커 스레드에서 실행해야 한다.
        """
        if not api_key:
            raise RuntimeError("API 키가 비어 있습니다.")
        if not base_url:
            raise RuntimeError("Base URL이 비어 있습니다. 설정에서 게이트웨이 주소를 입력하세요.")

        try:
            client = _get_client(api_key, base_url)
        except ImportError as e:
            raise RuntimeError("openai 패키지 미설치: pip install openai") from e
        try:
            resp = client.models.list()
            # 필터 없음 — 게이트웨이가 광고하는 모든 모델을 그대로 넘긴다.
            # (옛날엔 여기서 조용히 지워 "왜 없지?"가 됐다.)
            models = [m.id for m in resp.data]
        except Exception as e:
            raise RuntimeError(f"게이트웨이 모델 조회 실패: {e}") from e

        return sorted(set(models))

    @staticmethod
    def winrt_supported_languages() -> list[str]:
        """OCR 가능한 언어 BCP-47 태그 목록 (주요 언어 탐색 방식).

        winrt-* 3.x에서 get_available_recognizer_languages() 미지원이므로
        is_language_supported()로 알려진 언어 코드를 개별 탐색한다.
        """
        _PROBE = ["ko", "ko-KR", "en-US", "en-GB", "ja", "zh-Hans", "zh-Hant",
                  "fr-FR", "de-DE", "es-ES", "ru", "ar", "pt-BR"]
        try:
            from winrt.windows.media.ocr import OcrEngine as WinOcrEngine
            from winrt.windows.globalization import Language
            return [code for code in _PROBE
                    if WinOcrEngine.is_language_supported(Language(code))]
        except Exception:
            return []

    # ── WinRT 구현 ──

    def _recognize_winrt(self, pil_image: Image.Image) -> str:
        if not _check_winocr():
            raise RuntimeError(_winocr_error or "winocr가 설치되지 않았습니다. pip install winocr")

        w, h = pil_image.size
        if w == 0 or h == 0:
            return ""

        # WinRT OcrEngine 최대 처리 크기 제한 — 패딩 추가분(_OCR_PAD*2) 포함해 4096 이내 유지
        max_before_pad = _WINRT_MAX_DIM - _OCR_PAD * 2
        if max(w, h) > max_before_pad:
            scale = max_before_pad / max(w, h)
            pil_image = pil_image.resize(
                (max(1, int(w * scale)), max(1, int(h * scale))),
                Image.LANCZOS,
            )

        # RGBA/RGB 이외 모드는 변환 (OCR은 투명도 불필요)
        if pil_image.mode not in ("RGB", "RGBA"):
            pil_image = pil_image.convert("RGB")

        # 한 줄짜리 좁은 이미지에서 WinRT OCR이 인식 실패하는 문제 방지
        fill = (255, 255, 255, 255) if pil_image.mode == "RGBA" else (255, 255, 255)
        padded = Image.new(
            pil_image.mode,
            (pil_image.width + _OCR_PAD * 2, pil_image.height + _OCR_PAD * 2),
            fill,
        )
        padded.paste(pil_image, (_OCR_PAD, _OCR_PAD))
        pil_image = padded

        try:
            from winocr import recognize_pil_sync
            result = recognize_pil_sync(pil_image, lang=self.language)
            return (result.get("text") or "").strip()
        except AssertionError as e:
            # winocr가 언어팩 미지원 시 AssertionError + 설치 안내 메시지를 던짐
            msg = str(e)
            raise RuntimeError(
                f"Windows OCR이 '{self.language}' 언어를 지원하지 않습니다. "
                f"언어팩 설치: {msg}\n"
                "Windows 설정 → 시간 및 언어 → 언어 → 한국어 → 언어 옵션 → OCR 다운로드"
            ) from e

    # ── Gemini ──

    def _recognize_gemini(self, pil_image: Image.Image) -> str:
        import os
        api_key = self.api_key or os.environ.get("GOOGLE_API_KEY", "")
        if not api_key:
            raise RuntimeError("API 키가 설정되지 않았습니다. 설정에서 API 키를 입력하세요.")
        if not self.base_url:
            raise RuntimeError("Base URL이 설정되지 않았습니다. 설정에서 게이트웨이 주소를 입력하세요.")

        # 매 호출마다 폴백 상태 리셋 (이번 호출이 폴백 없이 끝났는지 main이 구별)
        self.last_fallback_from = None

        # 폴백 기본은 가장 저렴한 flash-lite
        model_name = self.model or "gemini-3.1-flash-lite"
        return self._call_with_fallback(
            model_name,
            call=lambda m: self._openai_compat_call(pil_image, api_key, self.base_url, m),
        )

    # ── 폴백 공통 래퍼 ──

    def _call_with_fallback(self, model: str, call) -> str:
        """1차 호출 실패가 model_not_found 류면 `_FALLBACK_CHAIN`의 안전 모델로 1회 재시도."""
        self.last_used_model = model
        try:
            return call(model)
        except Exception as exc:
            if not _is_model_not_found(exc):
                raise
            fallback = select_fallback_model(model)
            if not fallback or fallback == model:
                raise
            # 폴백 진행 — main이 토스트로 표시
            self.last_fallback_from = model
            self.last_used_model = fallback
            return call(fallback)

    # ── OpenAI 호환 게이트웨이 ──

    def _recognize_openai_compat(self, pil_image: Image.Image, api_key: str, base_url: str, model: str) -> str:
        """OpenAI 호환 게이트웨이/프록시를 통한 OCR (폴백 포함).

        Mindlogic Gateway처럼 base_url + Bearer 토큰 방식의 프록시에 사용.
        base_url은 '/chat/completions' 앞까지만 입력 (예: https://host/v1/gateway).

        직접 호출되는 일은 거의 없고 _recognize_gemini에서 사용되지만, 외부에서
        직접 호출하는 경로(테스트 등)를 위해 폴백 래퍼를 거치도록 통일.
        """
        self.last_fallback_from = None
        return self._call_with_fallback(
            model,
            call=lambda m: self._openai_compat_call(pil_image, api_key, base_url, m),
        )

    def _openai_compat_call(self, pil_image: Image.Image, api_key: str, base_url: str, model: str) -> str:
        """OpenAI 호환 게이트웨이 단일 호출 (폴백 없음)."""
        import io, base64
        try:
            client = _get_client(api_key, base_url)
        except ImportError:
            raise RuntimeError("openai 패키지 미설치: pip install openai")

        buf = io.BytesIO()
        pil_image.save(buf, format="PNG")
        b64 = base64.standard_b64encode(buf.getvalue()).decode()

        # max_tokens=16384: 게이트웨이가 reasoning(thinking) 토큰을 같은 max_tokens 예산에서
        # 차감하므로 작게 잡으면 thinking 모델(pro·preview 계열)에서 본문이 0~200자로 잘림.
        # 2048에서는 gemini-2.5-pro가 본문 0자, 3-flash-preview/3.5-flash/3.1-pro-preview가
        # finish_reason=length로 잘렸음. 16384면 6종 모델 전부 finish_reason=stop으로 정상 종료.
        # 청구는 실제 사용 토큰 기준이라 비용 영향은 미세하다.
        resp = client.chat.completions.create(
            model=model,
            max_tokens=16384,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                    {"type": "text", "text": _ocr_prompt(self.language)},
                ],
            }],
        )
        return (resp.choices[0].message.content or "").strip()


# ── 연결·모델 라이브 프로브 ──────────────────────────────────────────────────
# 설정창 `연결 테스트` 버튼이 쓰는 온디맨드 검사. 옛 `model_matrix.json`(빌드타임 전수
# 스윕)을 대체한다 — 같은 일을 하되 **지금 선택된 두 모델에 대해서만, 누른 순간에** 한다.
# 그래서 게이트웨이가 라인업을 바꿔도 결과가 낡지 않는다.
#
# ⚠ 프로브는 `_call_with_fallback`을 **거치지 않는다**. 폴백이 끼면 망가진 모델이 조용히
# gemini-2.5-flash로 갈아타 ✓로 보고되어, 테스트가 존재 이유를 잃는다.

ProbeStatus = Literal["ok", "weak", "fail", "retry"]

_PROBE_TEXT = "42"


class ProbeResult(NamedTuple):
    """`status`는 UI 색을, `detail`은 사용자에게 보여줄 한 줄 설명을 정한다.

    - ok    : 호출 성공.
    - weak  : 호출은 됐지만 결과가 미덥지 않다(OCR이 이미지는 받았으나 글자를 못 읽음).
    - fail  : 이 모델·키로는 안 된다(404 / 이미지 미지원 400 / 인증 실패).
    - retry : 서버 사정(429·503)으로 **판정 불가**. `fail`과 절대 섞지 말 것 —
              멀쩡한 모델을 나쁜 모델로 오해하게 만든다.
    """
    status: ProbeStatus
    detail: str


def _short_error(exc: Exception, limit: int = 140) -> str:
    msg = " ".join(str(exc).split())
    return msg if len(msg) <= limit else msg[: limit - 1] + "…"


def _is_server_busy(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "503" in msg or "unavailable" in msg or "overloaded" in msg


def _is_image_rejected(exc: Exception) -> bool:
    """이미지 입력을 못 받는 모델의 400 응답인지 (텍스트 전용 모델)."""
    msg = str(exc).lower()
    if "400" not in msg and "invalid_request" not in msg:
        return False
    return any(k in msg for k in ("image", "vision", "multimodal", "image_url"))


def _classify_probe_error(exc: Exception, *, with_image: bool = False) -> ProbeResult:
    if _is_quota_error(exc):
        return ProbeResult("retry", "일시적 한도 초과(429) — 잠시 후 다시 시도하세요.")
    if _is_server_busy(exc):
        return ProbeResult("retry", "서버 일시 장애(503) — 잠시 후 다시 시도하세요.")
    if _is_model_not_found(exc):
        return ProbeResult("fail", "이 이름의 모델이 없습니다(404).")
    if with_image and _is_image_rejected(exc):
        return ProbeResult("fail", "이미지 입력을 지원하지 않는 모델입니다(400) — OCR에 쓸 수 없습니다.")
    return ProbeResult("fail", _short_error(exc))


def _probe_image_png() -> bytes:
    """OCR 프로브용 이미지 — 흰 바탕에 큰 검은 글씨로 `_PROBE_TEXT`."""
    import io
    from PIL import ImageDraw, ImageFont

    img = Image.new("RGB", (160, 80), "white")
    draw = ImageDraw.Draw(img)
    font = None
    for name in ("arial.ttf", "malgun.ttf"):
        try:
            font = ImageFont.truetype(name, 48)
            break
        except OSError:
            continue
    # 트루타입이 없으면 PIL 기본 비트맵 폰트(작음). 읽히면 좋고, 못 읽어도 'weak'일 뿐
    # 이미지 수락 여부(진짜 알고 싶은 것)는 그대로 판정된다.
    draw.text((30, 12), _PROBE_TEXT, fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def probe_connection(api_key: str, base_url: str = "") -> ProbeResult:
    """키·엔드포인트가 살아 있는지. 모델 목록 조회로 확인(어떤 모델도 호출하지 않음)."""
    try:
        models = OcrEngine.list_gemini_models(api_key, base_url)
    except Exception as exc:
        return _classify_probe_error(exc)
    if not models:
        return ProbeResult("fail", "응답은 왔으나 모델 목록이 비어 있습니다.")
    return ProbeResult("ok", f"키 유효 — 모델 {len(models)}종 조회됨")


def probe_ocr_model(api_key: str, base_url: str, model: str, language: str = "ko") -> ProbeResult:
    """이 모델이 **이미지를 받아** 글자를 읽는지 실호출로 확인.

    실제 OCR과 같은 경로(`_openai_compat_call`)를 폴백 없이 탄다.
    `_PROBE_TEXT`가 응답에 있으면 ok, 이미지는 받았는데 못 읽으면 weak(고를 수는 있으나
    작은 글씨에서 무너질 신호), 이미지 자체를 거부하면 fail.
    """
    if not model:
        return ProbeResult("fail", "모델이 비어 있습니다.")
    import io
    try:
        engine = OcrEngine(kind="gemini", api_key=api_key, base_url=base_url,
                           language=language, model=model)
        pil = Image.open(io.BytesIO(_probe_image_png()))
        text = engine._openai_compat_call(pil, api_key, base_url, model)
    except Exception as exc:
        # 이미지 생성 실패도 여기서 잡힌다 — OCR 줄에 표시돼야 원인을 짚을 수 있다.
        return _classify_probe_error(exc, with_image=True)

    if _PROBE_TEXT in text:
        return ProbeResult("ok", f'이미지 인식 확인 — "{_PROBE_TEXT}" 읽음')
    if not text.strip():
        return ProbeResult("ok", "이미지 수락 — 응답 본문은 비어 있음")
    return ProbeResult("weak", "이미지는 받지만 글자를 못 읽었습니다 — OCR 품질이 낮을 수 있습니다.")


# ── 단독 실행 검증 ────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    import threading

    print(f"[OCR] winocr 사용 가능: {OcrEngine.is_winrt_available()}")
    if OcrEngine.is_winrt_available():
        print(f"[OCR] 한국어 지원: {OcrEngine.is_winrt_language_supported('ko')}")
        print(f"[OCR] 사용 가능한 언어: {OcrEngine.winrt_supported_languages()}")

    if len(sys.argv) > 1:
        import argparse
        parser = argparse.ArgumentParser(description="OCR 단독 실행 검증")
        parser.add_argument("path", help="이미지 파일 경로")
        parser.add_argument("lang", nargs="?", default="ko", help="언어 코드 (기본: ko)")
        parser.add_argument("--engine", default="winrt", choices=["winrt", "gemini"])
        parser.add_argument("--key", default="", help="AI API 키 (--engine gemini 시 필요)")
        args = parser.parse_args()

        path = args.path
        lang = args.lang
        engine = OcrEngine(kind=args.engine, api_key=args.key, language=lang)

        # 워커 스레드에서 호출 (실제 사용 환경 재현)
        result_holder: list[str] = []
        error_holder: list[Exception] = []

        def _run():
            try:
                result_holder.append(engine.recognize(Image.open(path)))
            except Exception as e:
                error_holder.append(e)

        t = threading.Thread(target=_run)
        t.start()
        t.join()

        if error_holder:
            print(f"[OCR] 오류: {error_holder[0]}", file=sys.stderr)
            sys.exit(1)
        print(f"\n[OCR] {path!r} ({lang}):\n{result_holder[0]}")
    else:
        print("\n사용법: python -m pasteflow.ocr_engine <이미지경로> [언어=ko] [--engine winrt|gemini] [--key <API키>]")
