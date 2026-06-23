"""OCR 엔진 추상화 — Windows WinRT 기본, Gemini 옵션.

설계
----
- recognize(PIL.Image) → str (동기). UI 블로킹 방지를 위해 호출자가 워커 스레드에서 실행.
- winocr 패키지가 winrt-* 계열을 래핑해 recognize_pil_sync() 동기 API를 제공.
  winsdk는 Python 3.14 미지원으로 채택하지 않음.
- 언어 지원 확인은 winrt.windows.media.ocr.OcrEngine.is_language_supported()로.
"""
from __future__ import annotations

from typing import Literal, Optional

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

# ── Gemini 모델 화이트리스트 ────────────────────────────────────────────────
# 코드 작성자가 명시적으로 검증한 모델만 등재한다. ↻ 새로고침으로 받은 게이트웨이
# 라인업 정렬과 OCR 실패 시 폴백 후보 선정에 사용. 게이트웨이가 모델 라인업을
# 바꾸면 이 목록을 갱신하면 된다. 알 수 없는 모델은 콤보에 "(미검증)" 태그로
# 노출되어 사용자가 명시적으로 선택할 수 있다.
#
# 필드: (name, tier_rank, on_official, on_gateway)
#   - tier_rank   가격 티어. 0=flash-lite, 1=flash, 2=pro. 낮을수록 저렴.
#   - on_official 공식 Google AI Studio API에서 사용 가능 (확인된 것만 True)
#   - on_gateway  factchat-cloud 게이트웨이에서 사용 가능 (확인된 것만 True)
_VERIFIED_MODELS: tuple[tuple[str, int, bool, bool], ...] = (
    ("gemini-3.1-flash-lite", 0, False, True),   # 게이트웨이 확인 2026-05-27
    ("gemini-2.5-flash",      1, True,  True),   # 공식+게이트웨이 모두 검증
    ("gemini-3.5-flash",      1, False, True),   # 게이트웨이 확인 2026-05-27
    ("gemini-2.5-pro",        2, True,  True),   # 공식+게이트웨이 모두 검증
)

# 어느 backend에서든 호출되리라 신뢰하는 최종 안전망 폴백 모델
_FALLBACK_DEFAULT = "gemini-2.5-flash"


def _backend_compat(entry: tuple, backend: str) -> bool:
    _name, _tier, on_official, on_gateway = entry
    if backend == "official":
        return on_official
    if backend == "gateway":
        return on_gateway
    raise ValueError(f"Unknown backend: {backend!r}")


def sort_models_with_whitelist(
    candidates: list[str], backend: str
) -> tuple[list[str], list[str]]:
    """↻ 새로고침 결과를 화이트리스트와 머지해 분류한다.

    Returns
    -------
    (verified, unverified)
        verified   — 화이트리스트 ∩ candidates, tier_rank 오름차순 (저렴한 것 먼저).
        unverified — candidates − 화이트리스트, 알파벳순.
    """
    wl = {e[0]: e[1] for e in _VERIFIED_MODELS if _backend_compat(e, backend)}
    verified = sorted((n for n in candidates if n in wl), key=lambda n: (wl[n], n))
    unverified = sorted(n for n in candidates if n not in wl)
    return verified, unverified


def whitelist_model_names(backend: str) -> list[str]:
    """backend 호환 화이트리스트 모델 이름 목록 (tier_rank 오름차순).

    캐시가 비어 있는 첫 실행에서 콤보 초기값으로 사용.
    """
    return [
        e[0] for e in sorted(
            (e for e in _VERIFIED_MODELS if _backend_compat(e, backend)),
            key=lambda e: (e[1], e[0]),
        )
    ]


def _is_model_not_found(exc: Exception) -> bool:
    """OCR 호출 예외가 '존재하지 않는 모델'을 의미하는지 휴리스틱 판정.

    OpenAI 호환 게이트웨이는 404 본문에 'model ... not found'를 담아 보내고,
    공식 google.generativeai는 NotFound·INVALID_ARGUMENT 형태로 던진다. SDK·게이트웨이
    버전마다 예외 타입이 달라 메시지 내용 기반 판정이 가장 안정적.
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


def select_fallback_model(failed_model: str, backend: str) -> Optional[str]:
    """OCR 호출이 실패한 모델에 대해 backend 호환 폴백 후보 1개.

    선정 규칙: backend 호환 화이트리스트 모델 중 _FALLBACK_DEFAULT 우선,
    실패 모델이 마침 _FALLBACK_DEFAULT면 같은 backend의 다른 화이트리스트 모델 1개.
    적합한 후보가 없으면 None.
    """
    compatible = [
        e[0] for e in _VERIFIED_MODELS
        if _backend_compat(e, backend) and e[0] != failed_model
    ]
    if not compatible:
        return None
    if _FALLBACK_DEFAULT in compatible:
        return _FALLBACK_DEFAULT
    return compatible[0]


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


def _ask_prompt(question: str, context_text: str = "") -> str:
    """클립보드 항목을 컨텍스트로 끼운 AI 질의 프롬프트.

    컨텍스트가 비어 있으면 질문만 그대로 보낸다(자유 질문).
    """
    if context_text.strip():
        return (
            "다음은 사용자가 복사해 둔 클립보드 내용입니다.\n"
            "----\n"
            f"{context_text}\n"
            "----\n\n"
            f"위 내용을 참고하여 질문에 답하세요. 질문: {question}"
        )
    return question


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
        """API에서 사용 가능한 Gemini 모델 ID 목록을 조회한다.

        - base_url 있음: OpenAI 호환 게이트웨이의 `/v1/models` 엔드포인트 사용
          (학교/사내 프록시 등). 응답 중 'gemini'가 포함된 모델 ID만 추출.
        - base_url 없음: `google.generativeai.list_models()` 사용. generateContent를
          지원하는 gemini-* 모델만 추출.

        실패 시 RuntimeError. UI 블로킹 방지를 위해 호출자가 워커 스레드에서 실행해야 한다.
        """
        if not api_key:
            raise RuntimeError("API 키가 비어 있습니다.")

        if base_url:
            try:
                import openai
            except ImportError as e:
                raise RuntimeError("openai 패키지 미설치: pip install openai") from e
            try:
                client = openai.OpenAI(api_key=api_key, base_url=_normalize_base_url(base_url))
                resp = client.models.list()
                models = [m.id for m in resp.data if "gemini" in m.id.lower()]
            except Exception as e:
                raise RuntimeError(f"게이트웨이 모델 조회 실패: {e}") from e
        else:
            try:
                import google.generativeai as genai
            except ImportError as e:
                raise RuntimeError("google-generativeai 패키지 미설치") from e
            try:
                genai.configure(api_key=api_key)
                models = []
                for m in genai.list_models():
                    if "generateContent" not in getattr(m, "supported_generation_methods", []):
                        continue
                    name = m.name.split("/", 1)[-1] if "/" in m.name else m.name
                    if "gemini" in name.lower():
                        models.append(name)
            except Exception as e:
                raise RuntimeError(f"Google API 모델 조회 실패: {e}") from e

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
            raise RuntimeError("API 키가 설정되지 않았습니다. 설정에서 Gemini API 키를 입력하세요.")

        # 매 호출마다 폴백 상태 리셋 (이번 호출이 폴백 없이 끝났는지 main이 구별)
        self.last_fallback_from = None

        if self.base_url:
            # 게이트웨이 폴백 기본은 가장 저렴한 flash-lite
            model_name = self.model or "gemini-3.1-flash-lite"
            return self._call_with_fallback(
                model_name,
                backend="gateway",
                call=lambda m: self._openai_compat_call(pil_image, api_key, self.base_url, m),
            )

        try:
            import google.generativeai as genai
        except ImportError:
            raise RuntimeError("google-generativeai 패키지 미설치: pip install google-generativeai")

        genai.configure(api_key=api_key)
        model_name = self.model or "gemini-2.5-flash"
        return self._call_with_fallback(
            model_name,
            backend="official",
            call=lambda m: self._google_genai_call(genai, pil_image, m),
        )

    # ── 폴백 공통 래퍼 ──

    def _call_with_fallback(self, model: str, backend: str, call) -> str:
        """1차 호출 실패가 model_not_found 류면 화이트리스트의 다음 안전 모델로 1회 재시도."""
        self.last_used_model = model
        try:
            return call(model)
        except Exception as exc:
            if not _is_model_not_found(exc):
                raise
            fallback = select_fallback_model(model, backend)
            if not fallback or fallback == model:
                raise
            # 폴백 진행 — main이 토스트로 표시
            self.last_fallback_from = model
            self.last_used_model = fallback
            return call(fallback)

    # ── 공식 Google API 단일 호출 ──

    def _google_genai_call(self, genai_module, pil_image: Image.Image, model_name: str) -> str:
        model = genai_module.GenerativeModel(model_name)
        response = model.generate_content([pil_image, _ocr_prompt(self.language)])
        return (response.text or "").strip()

    # ── OpenAI 호환 게이트웨이 ──

    def _recognize_openai_compat(self, pil_image: Image.Image, api_key: str, base_url: str, model: str) -> str:
        """OpenAI 호환 게이트웨이/프록시를 통한 OCR (폴백 포함).

        학교 게이트웨이처럼 base_url + Bearer 토큰 방식의 프록시에 사용.
        base_url은 '/chat/completions' 앞까지만 입력 (예: https://host/v1/gateway).

        직접 호출되는 일은 거의 없고 _recognize_gemini에서 사용되지만, 외부에서
        직접 호출하는 경로(테스트 등)를 위해 폴백 래퍼를 거치도록 통일.
        """
        self.last_fallback_from = None
        return self._call_with_fallback(
            model,
            backend="gateway",
            call=lambda m: self._openai_compat_call(pil_image, api_key, base_url, m),
        )

    def _openai_compat_call(self, pil_image: Image.Image, api_key: str, base_url: str, model: str) -> str:
        """OpenAI 호환 게이트웨이 단일 호출 (폴백 없음)."""
        import io, base64
        try:
            import openai
        except ImportError:
            raise RuntimeError("openai 패키지 미설치: pip install openai")

        buf = io.BytesIO()
        pil_image.save(buf, format="PNG")
        b64 = base64.standard_b64encode(buf.getvalue()).decode()

        client = openai.OpenAI(api_key=api_key, base_url=_normalize_base_url(base_url))
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

    # ── 텍스트 AI 질의 (OCR과 동일 배관 재사용) ──

    def ask(self, question: str, context_text: str = "", image_png: bytes | None = None) -> str:
        """AI 질의 — 클립보드 항목(텍스트 또는 이미지)을 컨텍스트로 질문에 답한다.

        OCR과 동일한 Gemini 배관(게이트웨이/공식 분기·자동 폴백·_normalize_base_url)을
        재사용한다. `image_png`가 주어지면 질문과 함께 이미지를 멀티모달로 전송(시각 질의),
        없으면 텍스트 컨텍스트+질문만 보낸다. 게이트웨이는 OpenAI 호환 chat.completions,
        공식 API는 google.generativeai로 갈린다(_recognize_gemini와 동일 구조).
        동기 호출이므로 호출자가 워커 스레드에서 실행해야 UI 블로킹이 없다.
        """
        import os
        api_key = self.api_key or os.environ.get("GOOGLE_API_KEY", "")
        if not api_key:
            raise RuntimeError("API 키가 설정되지 않았습니다. 설정에서 Gemini API 키를 입력하세요.")

        self.last_fallback_from = None
        prompt = _ask_prompt(question, context_text)

        if self.base_url:
            model_name = self.model or "gemini-3.1-flash-lite"
            return self._call_with_fallback(
                model_name,
                backend="gateway",
                call=lambda m: self._ask_openai_compat(prompt, api_key, self.base_url, m, image_png),
            )

        try:
            import google.generativeai as genai
        except ImportError:
            raise RuntimeError("google-generativeai 패키지 미설치: pip install google-generativeai")

        genai.configure(api_key=api_key)
        model_name = self.model or "gemini-2.5-flash"
        return self._call_with_fallback(
            model_name,
            backend="official",
            call=lambda m: self._ask_google_genai(genai, prompt, m, image_png),
        )

    def _ask_openai_compat(
        self, prompt: str, api_key: str, base_url: str, model: str, image_png: bytes | None = None
    ) -> str:
        """OpenAI 호환 게이트웨이 질의 단일 호출 (폴백 없음). image_png가 있으면 멀티모달."""
        import base64
        try:
            import openai
        except ImportError:
            raise RuntimeError("openai 패키지 미설치: pip install openai")
        client = openai.OpenAI(api_key=api_key, base_url=_normalize_base_url(base_url))
        # 이미지가 있으면 OCR(_openai_compat_call)과 동일한 image_url+text 멀티모달 content,
        # 없으면 텍스트만. max_tokens=16384는 OCR과 동일(thinking 토큰 잘림 방지).
        if image_png:
            b64 = base64.standard_b64encode(image_png).decode()
            content = [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                {"type": "text", "text": prompt},
            ]
        else:
            content = prompt
        resp = client.chat.completions.create(
            model=model,
            max_tokens=16384,
            messages=[{"role": "user", "content": content}],
        )
        return (resp.choices[0].message.content or "").strip()

    def _ask_google_genai(
        self, genai_module, prompt: str, model_name: str, image_png: bytes | None = None
    ) -> str:
        """공식 Google API 질의 단일 호출 (폴백 없음). image_png가 있으면 멀티모달."""
        model = genai_module.GenerativeModel(model_name)
        if image_png:
            parts = [{"mime_type": "image/png", "data": image_png}, prompt]
            response = model.generate_content(parts)
        else:
            response = model.generate_content(prompt)
        return (response.text or "").strip()


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
