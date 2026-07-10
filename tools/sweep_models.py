"""전 모델 능력 전수 스윕 — `pasteflow/model_matrix.json` 생성기.

게이트웨이/공식 API가 광고하는 **모든** 모델에 대해 두 축을 실호출로 확인한다.

  ① chat  — 텍스트 질의가 되는가        (정답이 결정적인 계산 문제)
  ② ocr   — 이미지에서 글자를 읽는가    (난이도 L1 < L2 < L3, 랜덤 토큰 대조)

왜 필요한가
-----------
설정창 모델 콤보가 손으로 관리하던 상수 3개(`_VERIFIED_MODELS` / `_NO_VISION_MODELS` /
`_PHANTOM_MODELS`)를 대체한다. 사람이 고른 목록이라 "왜 이건 되고 저건 안 되나"에 답할
수 없었고, 게이트웨이가 라인업을 바꾸면 즉시 낡았다.

⚠ 폴백을 반드시 우회한다
------------------------
`OcrEngine._call_with_fallback`은 model_not_found를 만나면 **조용히**
`gemini-2.5-flash`로 재시도한다. 스윕이 그 래퍼를 타면 못 쓰는 모델이 "통과"로 기록된다.
그래서 openai/genai 클라이언트를 직접 호출하고, 응답의 `model` 필드가 요청 모델과 같은지
대조해 무단 대체(served_as)까지 잡는다. 모델 목록도 `list_gemini_models()`가 아니라
`client.models.list()` 원본을 쓴다.

채점 (여기서 두 번 데였다)
--------------------------
1. **CAPTCHA 오인** — 저대비 배경에 맥락 없는 무작위 영숫자를 뿌리면 안전 정렬된 모델이
   `finish_reason=content_filter`로 **거부**한다. OCR 능력이 아니라 거부 정책을 재게 된다.
   → 토큰을 자연스러운 로그 문장·표 안에 심는다.
2. **완전일치의 취약성** — "무작위 토큰을 전부 맞혔나"는 글자 하나가 판정을 뒤집는다.
   → 정답 전문 대비 **복원율(recall)**로 채점하고 임계값(기본 0.95)을 넘으면 통과.
   실측 분포에서 0.94와 0.96 사이에 자연스러운 빈틈이 있어 임계값이 자의적이지 않다.

한글 토큰에 낱글자 무작위 조합을 쓰지 말 것 — 자세한 이유는 `_KOR_WORDS` 주석 참고.

사용법
------
  python tools/sweep_models.py --dry-run                     # 호출 없이 이미지·프롬프트 확인
  python tools/sweep_models.py --backend gateway             # 실제 스윕 (비용 발생)
  python tools/sweep_models.py --backend gateway --limit 3   # 앞 3종만 (비용 시험)
  python tools/sweep_models.py --backend official --workers 1  # 공식 API는 분당 한도가 낮다

원시 덤프(`tools/sweep_raw/`)만 있으면 **API를 다시 호출하지 않고** 재채점·재분류할 수 있다.
채점 기준을 바꾸거나 `_classify` 버그를 고쳤을 때:

  python tools/sweep_models.py --from-raw tools/sweep_raw/A.json,tools/sweep_raw/B.json
      (뒤 파일이 우선 — 같은 backend를 담고 있으면 나중 것이 앞의 것을 덮는다)
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import random
import re
import sys
import threading
import time
import unicodedata
from datetime import datetime

from PIL import Image, ImageDraw, ImageFont

# 리포지토리 루트를 import 경로에 추가 (스크립트로 직접 실행되므로).
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from pasteflow.ocr_engine import _normalize_base_url, _ocr_prompt  # noqa: E402

# ── 설정 키 (settings_dialog.py와 동일. 그 모듈은 PyQt를 끌어오므로 import하지 않는다) ──
_K_KEY_GATEWAY = "ocr_gemini_api_key_gateway"
_K_KEY_OFFICIAL = "ocr_gemini_api_key_official"
_K_BASE_URL = "ocr_gemini_base_url"

_FONT_KO = r"C:\Windows\Fonts\malgun.ttf"
_FONT_MONO = r"C:\Windows\Fonts\consola.ttf"

# OCR이 원리적으로 혼동하는 글자(0/O, 1/I/l) 제외 — 부당한 실패 방지.
_TOK_CHARS = "ABCDEF23456789"

# ⚠ 낱글자 무작위 조합을 쓰지 말 것. 옛 알파벳
# "가나다라마바사아자차카타파하거너더러머버서어저처커터퍼허"는 자음마다 ㅏ/ㅓ
# 최소대립쌍(가/거, 라/러, 하/허 …)을 담고 있어 12px에서 사람도 구별하지 못한다.
# 2026-07-10 스윕에서 정답 `카러차`를 거의 모든 모델이 `카라차`로 읽었다 —
# OCR 능력이 아니라 획 하나의 변별을 재고 있었다. 실제 단어를 쓰면 모델이 어휘
# 문맥으로 보정할 수 있어 실사용 조건에 가깝고, 무작위 '선택'이라 예측도 불가능하다.
_KOR_WORDS = (
    "하늘빛", "바다물결", "겨울나무", "은행잎", "구름다리", "새벽별",
    "돌담길", "봄바람", "물안개", "숲속", "종이배", "달맞이",
)

_CHAT_PROMPT = "다음 계산의 답만 숫자로 출력하세요. 설명·단위·기호 없이 숫자만: 17*23"
_CHAT_EXPECT = "391"

_LEVELS = ("L1", "L2", "L3")

# 모델이 '못 한' 게 아니라 '안 한' 경우. OCR 능력 실패와 구분해 기록한다 —
# 이게 뜨면 십중팔구 테스트 이미지가 CAPTCHA처럼 보인 것이지 모델 결함이 아니다.
_REFUSAL_FINISH = frozenset({"content_filter", "refusal"})

_print_lock = threading.Lock()


def _force_utf8_console() -> None:
    """Windows 기본 콘솔은 cp949라 한글 프롬프트의 em-dash 등에서 죽는다."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def _log(msg: str) -> None:
    with _print_lock:
        print(msg, flush=True)


# ── 테스트 이미지 ─────────────────────────────────────────────────────────────


def _tok(rng: random.Random, n: int = 4) -> str:
    return "".join(rng.choice(_TOK_CHARS) for _ in range(n))


def _kor(rng: random.Random) -> str:
    return rng.choice(_KOR_WORDS)


def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    if not os.path.exists(path):
        raise RuntimeError(f"폰트를 찾을 수 없습니다: {path}")
    return ImageFont.truetype(path, size)


def make_l1(rng: random.Random) -> tuple[Image.Image, list[str], list[str]]:
    """L1 — 흰 배경 + 검정 글자 28px 3줄. 최소 OCR 능력. 여기서 실패하면 OCR 불가.

    3줄인 이유: 정답이 짧으면 recall 분모가 작아 글자 한 개 미끄러짐이 임계값을
    넘겨버린다(2줄 15자에서는 1자 오류가 0.93 → 오탈락).
    """
    code, kor = _tok(rng), _kor(rng)
    lines = [f"주문 번호: {code}", f"수령인: {kor}", "배송 상태: 준비중"]
    img = Image.new("RGB", (480, 150), "#ffffff")
    d = ImageDraw.Draw(img)
    f = _font(_FONT_KO, 28)
    for i, line in enumerate(lines):
        d.text((24, 16 + i * 42), line, fill="#000000", font=f)
    return img, lines, [code, kor]


def make_l2(rng: random.Random) -> tuple[Image.Image, list[str], list[str]]:
    """L2 — 앱 다크 테마(#121212) + 회색 글자 12px 로그 4줄. 실사용 캡처 조건 근사.

    PasteFlow가 실제로 OCR하는 대상은 대개 다크 UI의 작은 텍스트다. L1만 통과하고
    여기서 무너지는 모델은 "목록엔 있는데 실전에서 못 읽는" 모델이므로 걸러낸다.

    ⚠ 토큰을 맨 문자열로 흩뿌리면 안 된다 — 저대비 배경 위 무작위 영숫자는 CAPTCHA와
    구별되지 않아 안전 정렬된 모델(claude 계열 등)이 `finish_reason=content_filter`로
    **거부**한다(2026-07-10 스모크에서 claude-fable-5가 실제로 거부). OCR 능력이 아니라
    거부 정책을 재게 되므로, 토큰을 자연스러운 로그 문장 안에 심어 스크린샷으로 읽히게 한다.
    """
    c1, k1, c2 = _tok(rng), _kor(rng), _tok(rng)
    lines = [
        "[10:24:07] 빌드 완료 — 소요 12.4초",
        f"[10:24:12] 커밋 {c1} 배포 대기열에 추가됨",
        f"[10:25:03] 사용자 {k1} 님이 채널에 접속했습니다",
        f"[10:25:41] 경고: 작업 {c2} 재시도 3회 후 중단",
    ]
    img = Image.new("RGB", (480, 104), "#121212")
    d = ImageDraw.Draw(img)
    f = _font(_FONT_KO, 12)
    for i, line in enumerate(lines):
        d.text((20, 14 + i * 22), line, fill="#8a8a8a", font=f)
    return img, lines, [c1, k1, c2]


def make_l3(rng: random.Random) -> tuple[Image.Image, list[str], list[str]]:
    """L3 — 코드 블록 + 표. 줄바꿈·공백 구조 보존까지 요구하는 밀집 스크린샷.

    콤보 노출 기준에는 쓰지 않고(툴팁 등급 표시용) 최상위 모델을 가려내는 데만 쓴다.

    L2와 같은 이유로 토큰에 맥락을 준다 — 맥락 없는 `| AE56 | DF5C |` 표는 CAPTCHA로
    읽혀 claude-fable-5가 거부했다(2026-07-10). 컬럼 이름과 상태값을 넣어 문서로 읽히게 한다.
    """
    a, b, c, d4 = _tok(rng), _tok(rng), _tok(rng), _tok(rng)
    img = Image.new("RGB", (560, 172), "#ffffff")
    d = ImageDraw.Draw(img)
    f = _font(_FONT_MONO, 13)
    rows = [
        f"def retry(task):          # id={a}",
        f"    return task.run() * 3 # ref={b}",
        "",
        "| task    | status | code |",
        "|---------|--------|------|",
        f"| deploy  | done   | {c} |",
        f"| reindex | queued | {d4} |",
    ]
    for i, line in enumerate(rows):
        d.text((16, 12 + i * 21), line, fill="#1f2328", font=f)
    return img, [r for r in rows if r], [a, b, c, d4]


_BUILDERS = {"L1": make_l1, "L2": make_l2, "L3": make_l3}


def _png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ── 채점 ──────────────────────────────────────────────────────────────────────

_WS_RE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    """비교용 정규화 — NFC 결합, 공백 제거, 대문자화.

    모델이 `_ocr_prompt`의 "출력만" 지시를 어기고 서두를 붙이는 편차가 있으므로
    완전 일치가 아니라 '포함'으로 채점한다. 토큰이 랜덤이라 추측으로는 못 맞힌다.
    """
    return _WS_RE.sub("", unicodedata.normalize("NFC", text)).upper()


def _recall(answer: str, truth_lines: list[str]) -> float:
    """정답 전문 중 답변이 **순서대로 정확히 복원한 비율** (0.0~1.0).

    옛 방식(무작위 토큰 완전일치)의 구조적 한계를 대체한다: 토큰 하나의 글자 하나가
    미끄러지면 판정이 통째로 뒤집혀, "이 모델이 실사용 텍스트를 읽는가"가 아니라
    "최악의 글자 하나를 변별하는가"를 재고 있었다. recall은 OCR 업계 표준에 가깝고
    글자 1개 오류를 1/N만큼만 감점한다.

    SequenceMatcher의 matching block은 순서를 보존하므로, 모델이 앞에 "이미지의
    텍스트는:" 같은 서두를 붙여도 recall은 떨어지지 않는다(분모가 정답 길이라 여분의
    출력은 점수를 올리지도 내리지도 않는다).
    """
    from difflib import SequenceMatcher

    truth = _normalize("".join(truth_lines))
    if not truth:
        return 0.0
    got = _normalize(answer)
    matched = sum(b.size for b in SequenceMatcher(None, truth, got).get_matching_blocks())
    return min(1.0, matched / len(truth))


def _hit_tokens(answer: str, tokens: list[str]) -> tuple[int, int]:
    """진단용 보조 지표 — 어느 무작위 토큰을 맞혔나. 판정에는 쓰지 않는다."""
    norm = _normalize(answer)
    return sum(1 for t in tokens if _normalize(t) in norm), len(tokens)


def _ocr_level(passed: list[str], unknown: bool) -> str:
    """통과 레벨 목록 + 일시적 오류 여부 → 매트릭스의 ocr 값.

    L2까지 통과했으면 품질이 확정된 것이라 그 등급을 쓴다. L1만 통과한 채 서버 사정으로
    L2를 못 쟀다면 `unknown` — 여기서 "L1"을 박으면 **품질이 나쁘다는 경고(⚠)가 부당하게**
    붙는다("못 읽는다"와 "못 재봤다"는 다르다).
    """
    if "L2" in passed:
        return passed[-1]
    if unknown:
        return "unknown"
    return passed[-1] if passed else "fail"


def _is_alias(model: str, served: str) -> bool:
    """응답의 model 필드가 요청 모델의 정상 별칭인가.

    게이트웨이는 `gpt-5-mini` 요청에 `gpt-5-mini-2025-08-07`(날짜 스냅샷)로 답한다 —
    이건 정상이다. 반면 `gpt-5.2-codex` 요청에 `gemini-2.5-flash`가 오면 무단 대체이므로
    반드시 잡아야 한다(고른 모델이 아닌 답을 받게 됨).
    """
    return not served or served == model or served.startswith(model + "-")


# ── 오류 분류 ─────────────────────────────────────────────────────────────────


# 모델 결함이 아니라 그때의 서버 사정. 이걸 `fail`로 굳히면 좋은 모델이 UI에서
# 회색 처리된다(2026-07-10 1차 스윕: 공식 API 30종 중 21종이 429 → gemini-2.5-pro가
# "질의 불가"로 기록됨). 재시도해도 남으면 `unknown`(미측정)으로 남긴다.
_TRANSIENT = frozenset({"rate_limited", "timeout", "unavailable"})
# 모델이 그 엔드포인트를 영구히 못 쓰는 경우. 재시도해도 소용없다.
_HARD_FAIL = frozenset({"not_found", "unsupported_endpoint", "unsupported_modality"})

# ⚠ 순서가 중요하다. 옛 코드는 not_found를 맨 먼저, 그것도 맨 문자열 "404" 포함으로
# 판정해서 **429 할당량 초과를 유령 모델로 오분류**했다(2026-07-10:
# gemini-3.1-flash-image-preview가 429인데 phantom으로 기록됨 → UI에서 영구 회색될 뻔).
# 일시적 오류를 먼저 걸러내고, not_found는 아래 패턴으로 좁힌다.
_RE_TRANSIENT_RATE = re.compile(r"429|rate[ _-]?limit|quota|resource_exhausted")
_RE_TRANSIENT_UNAVAIL = re.compile(r"\b503\b|unavailable|overloaded|high demand")
_RE_TIMEOUT = re.compile(r"timeout|timed out")
# "v1/chat/completions 엔드포인트 미지원" — 모델은 존재하지만 우리 호출 경로로는 못 쓴다.
_RE_ENDPOINT = re.compile(r"not supported in the [\w./-]+ endpoint")
_RE_NOT_FOUND = re.compile(
    r"model_not_found|no longer available|error code: 404|'code':\s*404|\bnot found\b")
_RE_NO_VISION = re.compile(
    r"image input modality|does not support image|unsupported chatmessagecontent type: image")
# 텍스트 응답 자체를 못 내는 모델(TTS·이미지 생성 전용). 텍스트 프롬프트에 400을 낸다.
# 예: "The requested combination of response modalities (TEXT) is not supported by the model"
_RE_MODALITY = re.compile(r"response modalities|invalid.?argument")


def _classify(exc: Exception, *, vision: bool) -> tuple[str, str]:
    """(kind, why) — kind ∈ not_found | unsupported_endpoint | no_vision
    | rate_limited | timeout | unavailable | error
    """
    s = str(exc).lower()
    if _RE_TRANSIENT_RATE.search(s):
        return "rate_limited", "429 호출 한도 — 미측정"
    if _RE_TRANSIENT_UNAVAIL.search(s):
        return "unavailable", "503 일시 과부하 — 미측정"
    if _RE_TIMEOUT.search(s):
        return "timeout", "응답 시간 초과 — 미측정"
    if _RE_ENDPOINT.search(s):
        return "unsupported_endpoint", "chat/completions 엔드포인트 미지원 (v1/responses 전용)"
    if _RE_NOT_FOUND.search(s):
        return "not_found", "404 — API가 이 모델을 서빙하지 않음"
    if vision and (_RE_NO_VISION.search(s) or "400" in s or "invalid_request" in s):
        return "no_vision", "이미지 입력 미지원 (400) — 텍스트 전용 모델"
    if not vision and _RE_MODALITY.search(s):
        # 텍스트 프롬프트에 400. vision=False일 때만 봐야 한다 — 이미지 400은 위에서 잡힌다.
        return "unsupported_modality", "텍스트 응답 미지원 (TTS·이미지 생성 전용 모델)"
    return "error", str(exc)[:180]


# ── 백엔드별 원시 호출 (폴백 래퍼 우회) ───────────────────────────────────────


class Gateway:
    name = "gateway"

    def __init__(self, api_key: str, base_url: str, timeout: float):
        import openai
        # max_retries=0: 재시도는 이 스크립트가 통제한다. SDK가 404까지 재시도하면
        # 유령 모델 판정이 느려지고 비용만 늘어난다.
        self._client = openai.OpenAI(
            api_key=api_key, base_url=_normalize_base_url(base_url),
            timeout=timeout, max_retries=0,
        )

    def list_models(self) -> list[str]:
        # 원본 목록. OcrEngine.list_gemini_models()를 거치지 않는 이유는 그쪽이 앱의
        # 정규화·예외 래핑을 타기 때문 — 스윕은 게이트웨이가 광고하는 날것 그대로를 재야 한다.
        return sorted(m.id for m in self._client.models.list().data)

    def call(self, model: str, prompt: str, png: bytes | None) -> tuple[str, str, str]:
        if png is None:
            content: object = prompt
        else:
            b64 = base64.standard_b64encode(png).decode()
            content = [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                {"type": "text", "text": prompt},
            ]
        resp = self._client.chat.completions.create(
            model=model, max_tokens=16384,
            messages=[{"role": "user", "content": content}],
        )
        choice = resp.choices[0]
        return (
            (choice.message.content or "").strip(),
            choice.finish_reason or "",
            getattr(resp, "model", "") or "",
        )


class Official:
    name = "official"

    def __init__(self, api_key: str, base_url: str, timeout: float):
        from google import genai
        self._client = genai.Client(api_key=api_key)
        self._timeout = timeout

    def list_models(self) -> list[str]:
        # 공식 API는 태생적으로 gemini 전용. generateContent를 지원하는 것만.
        out: list[str] = []
        for m in self._client.models.list():
            name = (getattr(m, "name", "") or "").removeprefix("models/")
            actions = getattr(m, "supported_actions", None) or []
            if name.startswith("gemini-") and (not actions or "generateContent" in actions):
                out.append(name)
        return sorted(set(out))

    def call(self, model: str, prompt: str, png: bytes | None) -> tuple[str, str, str]:
        from google.genai import types
        parts = []
        if png is not None:
            parts.append(types.Part.from_bytes(data=png, mime_type="image/png"))
        parts.append(types.Part(text=prompt))
        # google_search 도구를 붙이지 않는다 — 여기서 재는 것은 '원시 능력'이고,
        # 검색 도구는 무료 할당량이 없는 모델에서 429를 유발해 판정을 오염시킨다.
        resp = self._client.models.generate_content(
            model=model,
            contents=[types.Content(role="user", parts=parts)],
            config=types.GenerateContentConfig(max_output_tokens=16384),
        )
        return (resp.text or "").strip(), "", model


# ── 단일 모델 프로브 ──────────────────────────────────────────────────────────


def _attempt(backend, model: str, prompt: str, png: bytes | None, retries: int):
    """호출 1회 + 일시적 오류(429/503/timeout)에 한해 백오프 재시도.

    공식 API 무료 티어는 분당 한도가 빡빡해 짧은 백오프로는 못 빠져나온다.
    5s → 10s → 20s → 40s (최대 60s).
    """
    for i in range(retries + 1):
        try:
            return backend.call(model, prompt, png)
        except Exception as exc:  # noqa: BLE001 — 분류는 _classify가 한다
            kind, _why = _classify(exc, vision=png is not None)
            if kind not in _TRANSIENT or i == retries:
                raise
            time.sleep(min(60, 5 * 2 ** i))
    raise RuntimeError("unreachable")  # pragma: no cover


def probe_model(backend, model: str, images: dict, pass_recall: float,
                retries: int) -> tuple[dict, dict]:
    """한 모델의 chat·ocr 능력을 잰다. (matrix_entry, raw_entry) 반환.

    상태는 셋이다 — `ok`/레벨 = 확인됨, `fail` = 모델이 실제로 못 함,
    `unknown` = 서버 사정(429/503/timeout)으로 **못 재봄**. fail과 unknown을 섞으면
    UI가 멀쩡한 모델을 회색 처리한다.

    비용 절약을 위해 짧게 끊는다:
      - chat이 404(유령)면 vision은 아예 재지 않는다.
      - 어느 레벨이든 실패하면 그 위 레벨은 재지 않는다(단조 등급).
    """
    entry: dict = {"chat": "fail", "ocr": "fail", "why": ""}
    raw: dict = {"levels": {}, "served_as": {}}

    # ① chat
    try:
        text, finish, served = _attempt(backend, model, _CHAT_PROMPT, None, retries)
        raw["chat_text"] = text[:400]
        raw["chat_finish"] = finish
        if served:
            raw["served_as"]["chat"] = served
        if not _is_alias(model, served):
            entry["served_as"] = served
        if not text:
            entry["why"] = f"본문 없음 (finish_reason={finish or '?'})"
        elif _CHAT_EXPECT not in _normalize(text):
            entry["why"] = f"오답 — {_CHAT_EXPECT} 미포함"
        else:
            entry["chat"] = "ok"
    except Exception as exc:  # noqa: BLE001
        kind, why = _classify(exc, vision=False)
        entry["why"] = why
        raw["chat_error"] = f"{kind}: {exc}"[:600]
        if kind in _HARD_FAIL:
            _log(f"  [{kind}] {model}")
            return entry, raw
        if kind in _TRANSIENT:
            entry["chat"] = "unknown"

    # ② ocr — L1 → L2 → L3, 실패 시 즉시 중단
    passed: list[str] = []
    unknown = False
    for lv in _LEVELS:
        img, truth_lines, tokens = images[lv]
        try:
            text, finish, served = _attempt(backend, model, _ocr_prompt("ko"), _png_bytes(img), retries)
            recall = _recall(text, truth_lines)
            hit, total = _hit_tokens(text, tokens)
            raw["levels"][lv] = {
                "recall": round(recall, 4), "tokens": f"{hit}/{total}",
                "truth": "\n".join(truth_lines), "text": text[:400], "finish": finish,
            }
            if served:
                raw["served_as"][lv] = served
            if not _is_alias(model, served):
                entry["served_as"] = served
            if not text and finish in _REFUSAL_FINISH:
                # 능력 부족이 아니라 거부. 이 값이 보이면 테스트 이미지를 의심할 것.
                entry["refused_at"] = lv
                entry["why"] = f"{lv} 거부 (finish_reason={finish}) — 테스트 이미지 재검토 필요"
                break
            if recall < pass_recall:
                if not entry["why"] or entry["chat"] == "unknown":
                    entry["why"] = (f"{lv} 복원율 {recall:.0%} (기준 {pass_recall:.0%})"
                                    if text else f"{lv} 본문 없음 (finish_reason={finish or '?'})")
                break
            passed.append(lv)
        except Exception as exc:  # noqa: BLE001
            kind, why = _classify(exc, vision=True)
            raw["levels"][lv] = {"error": f"{kind}: {exc}"[:300]}
            if kind in _TRANSIENT:
                unknown = True
            if not entry["why"] or kind == "no_vision":
                entry["why"] = why
            break

    entry["ocr"] = _ocr_level(passed, unknown)
    _log(f"  [{model}] chat={entry['chat']} ocr={entry['ocr']}"
         + (f"  ({entry['why']})" if entry["why"] else ""))
    return entry, raw


# ── 키 로드 ───────────────────────────────────────────────────────────────────


def entry_from_raw(rec: dict, pass_recall: float) -> dict:
    """원시 덤프 1건 → 매트릭스 항목. **API를 호출하지 않는다.**

    채점 기준(`--pass-recall`)을 바꾸거나 `_classify`의 버그를 고쳤을 때, 돈을 다시
    쓰지 않고 매트릭스를 다시 만들기 위한 경로다(1차 스윕에서 정답 텍스트를 저장하지
    않아 재채점이 불가능했던 실수의 교훈).

    옛 형식(레벨에 `recall`이 없는 덤프)은 채점을 신뢰할 수 없으므로 ocr=unknown으로
    남긴다 — 없는 데이터를 지어내지 않는다.
    """
    entry: dict = {"chat": "fail", "ocr": "fail", "why": ""}

    chat_err = rec.get("chat_error", "")
    if chat_err:
        msg = chat_err.split(":", 1)[1] if ":" in chat_err else chat_err
        kind, why = _classify(RuntimeError(msg), vision=False)
        entry["why"] = why
        if kind in _HARD_FAIL:
            return entry
        if kind in _TRANSIENT:
            entry["chat"] = "unknown"
    elif rec.get("chat_text") and _CHAT_EXPECT in _normalize(rec["chat_text"]):
        entry["chat"] = "ok"
    else:
        entry["why"] = f"오답 — {_CHAT_EXPECT} 미포함"

    levels = rec.get("levels", {})
    if "L1" not in levels:
        # 레벨을 한 번도 시도하지 못했다(429 등으로 chat에서 조기 종료).
        # 여기서 fail로 굳히면 "못 읽는다"가 아니라 "못 재봤다"가 실패로 둔갑한다.
        entry["ocr"] = "unknown"
        return entry

    passed: list[str] = []
    unknown = False
    for lv in _LEVELS:
        d = levels.get(lv)
        if not d:
            break
        if "error" in d:
            msg = d["error"].split(":", 1)[1] if ":" in d["error"] else d["error"]
            kind, why = _classify(RuntimeError(msg), vision=True)
            if kind in _TRANSIENT:
                unknown = True
            if not entry["why"] or kind == "no_vision":
                entry["why"] = why
            break
        if "recall" not in d:
            unknown = True  # 옛 형식 — 채점 근거가 없다
            break
        if d["recall"] < pass_recall:
            if not entry["why"]:
                entry["why"] = f"{lv} 복원율 {d['recall']:.0%} (기준 {pass_recall:.0%})"
            break
        passed.append(lv)

    entry["ocr"] = _ocr_level(passed, unknown)
    return entry


def run_from_raw(args) -> dict:
    """`--from-raw`로 넘긴 원시 덤프들을 합쳐 매트릭스를 만든다."""
    merged: dict = {}
    for path in args.from_raw.split(","):
        path = path.strip()
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        for backend, models in raw.items():
            dst = merged.setdefault(backend, {})
            for name, rec in models.items():
                dst[name] = entry_from_raw(rec, args.pass_recall)
        _log(f"[from-raw] {path}: " + ", ".join(f"{b} {len(m)}종" for b, m in raw.items()))
    return merged


def _db_path() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser(r"~\AppData\Local")
    return os.path.join(base, "PasteFlow", "pasteflow.db")


def load_credentials(backend: str) -> tuple[str, str]:
    """DB에서 (api_key, base_url). 키는 DPAPI 복호화. 절대 출력하지 않는다."""
    import sqlite3
    from pasteflow.crypto import unprotect

    path = _db_path()
    if not os.path.exists(path):
        raise RuntimeError(f"DB를 찾을 수 없습니다: {path}")
    conn = sqlite3.connect(path)
    try:
        rows = dict(conn.execute("SELECT key, value FROM settings").fetchall())
    finally:
        conn.close()

    key_name = _K_KEY_GATEWAY if backend == "gateway" else _K_KEY_OFFICIAL
    api_key = unprotect(rows.get(key_name, "") or "")
    base_url = (rows.get(_K_BASE_URL, "") or "") if backend == "gateway" else ""
    if not api_key:
        raise RuntimeError(f"{backend} API 키가 DB에 없습니다 (설정창에서 입력·저장하세요).")
    if backend == "gateway" and not base_url:
        raise RuntimeError("gateway Base URL이 DB에 없습니다.")
    return api_key, base_url


# ── 실행 ──────────────────────────────────────────────────────────────────────


def run_backend(backend_name: str, args, images: dict) -> tuple[dict, dict]:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    api_key, base_url = load_credentials(backend_name)
    _log(f"[{backend_name}] 키 로드됨 (길이 {len(api_key)}), base_url={base_url or '(공식 API)'}")

    cls = Gateway if backend_name == "gateway" else Official
    backend = cls(api_key, base_url, args.timeout)

    models = backend.list_models()
    if args.models:
        wanted = {m.strip() for m in args.models.split(",")}
        models = [m for m in models if m in wanted]
    if args.limit:
        models = models[: args.limit]
    _log(f"[{backend_name}] 대상 모델 {len(models)}종, 워커 {args.workers}")

    matrix: dict = {}
    raws: dict = {}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {pool.submit(probe_model, backend, m, images, args.pass_recall, args.retries): m
                for m in models}
        for fut in as_completed(futs):
            m = futs[fut]
            try:
                matrix[m], raws[m] = fut.result()
            except Exception as exc:  # noqa: BLE001
                matrix[m] = {"chat": "fail", "ocr": "fail", "why": f"스윕 오류: {exc}"[:180]}
                raws[m] = {"fatal": str(exc)[:300]}
                _log(f"  [{m}] 스윕 오류: {exc}")
    return matrix, raws


def _summarize(backend: str, entries: dict) -> None:
    chat_ok = sum(1 for e in entries.values() if e["chat"] == "ok")
    chat_fail = sum(1 for e in entries.values() if e["chat"] == "fail")
    ocr_ok = sum(1 for e in entries.values() if e["ocr"] in ("L2", "L3"))
    ocr_fail = sum(1 for e in entries.values() if e["ocr"] == "fail")
    weak = sum(1 for e in entries.values() if e["ocr"] == "L1")
    unk = sum(1 for e in entries.values() if "unknown" in (e["chat"], e["ocr"]))
    _log(f"[{backend}] 총 {len(entries)}종 / 질의 가능 {chat_ok}·불가 {chat_fail} "
         f"/ OCR 가능 {ocr_ok}·부정확 {weak}·불가 {ocr_fail} / 미측정 {unk}")


def main() -> int:
    _force_utf8_console()
    p = argparse.ArgumentParser(description="모델 능력 전수 스윕 → model_matrix.json")
    p.add_argument("--backend", choices=["gateway", "official", "both"], default="gateway")
    p.add_argument("--dry-run", action="store_true",
                   help="네트워크 호출 없이 테스트 이미지·프롬프트만 생성해 확인")
    p.add_argument("--from-raw", default="",
                   help="쉼표로 구분한 sweep_raw.json 경로들을 재분류해 매트릭스 생성 "
                        "(API 호출 없음. 채점 기준·분류 버그 수정 후 재빌드용)")
    p.add_argument("--models", default="", help="쉼표로 구분한 모델명만 검사")
    p.add_argument("--limit", type=int, default=0, help="앞 N종만 (비용 시험용)")
    p.add_argument("--workers", type=int, default=4,
                   help="공식 API는 무료 티어 분당 한도가 낮으니 1~2를 권장")
    p.add_argument("--retries", type=int, default=3, help="429/503/timeout 재시도 횟수")
    p.add_argument("--timeout", type=float, default=120.0)
    p.add_argument("--pass-recall", type=float, default=0.95,
                   help="해당 레벨 통과에 필요한 정답 전문 복원율 (기본 0.95)")
    p.add_argument("--seed", type=int, default=0, help="0이면 새 랜덤 시드를 뽑아 기록한다")
    p.add_argument("--out", default=os.path.join(_ROOT, "pasteflow", "model_matrix.json"))
    # 원시 덤프는 런타임 파일이 아니라 '증거'다. pasteflow/ 아래 두면 exe에 딸려 들어간다.
    p.add_argument("--raw-out", default="",
                   help="원시 응답 덤프 경로 (기본: tools/sweep_raw/sweep_raw.json)")
    p.add_argument("--image-dir", default="", help="--dry-run 시 테스트 이미지를 저장할 폴더")
    args = p.parse_args()

    # 시드를 기록해 두면 이상한 결과가 나왔을 때 같은 이미지를 그대로 재현할 수 있다.
    seed = args.seed or random.randrange(1, 2**31)
    rng = random.Random(seed)
    images = {lv: _BUILDERS[lv](rng) for lv in _LEVELS}

    if args.dry_run:
        out_dir = args.image_dir or os.path.join(_ROOT, "_sweep_preview")
        os.makedirs(out_dir, exist_ok=True)
        print(f"네트워크 호출 없음 (--dry-run), seed={seed}\n")
        print(f"[chat] 프롬프트: {_CHAT_PROMPT}")
        print(f"[chat] 정답 조건: 응답에 '{_CHAT_EXPECT}' 포함\n")
        print(f"[ocr] 프롬프트: {_ocr_prompt('ko')}\n")
        for lv in _LEVELS:
            img, truth_lines, tokens = images[lv]
            path = os.path.join(out_dir, f"{lv.lower()}.png")
            img.save(path)
            print(f"[{lv}] {img.width}x{img.height}  무작위 토큰: {tokens}")
            for line in truth_lines:
                print(f"      | {line}")
            print(f"      -> {path}")
        print(f"\n통과 기준: 각 레벨 정답 전문 복원율 >= {args.pass_recall:.0%}")
        print("콤보 노출: OCR = L1 AND L2 통과 / 질의 = chat 통과")
        return 0

    result: dict = {
        "_note": "tools/sweep_models.py가 생성. 손으로 고치지 말 것 — 스크립트를 다시 돌릴 것.",
        "_status": "chat: ok|fail|unknown / ocr: L3|L2|L1|fail|unknown (unknown=서버 사정으로 미측정)",
        "swept_at": datetime.now().isoformat(timespec="seconds"),
        "pass_recall": args.pass_recall,
        "seed": seed,
    }

    if args.from_raw:
        merged = run_from_raw(args)
        result.update(merged)
        result["_note"] += "  (--from-raw 재분류본 — 새 API 호출 없음)"
        result.pop("seed", None)
        os.makedirs(os.path.dirname(args.out), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2, sort_keys=False)
        for name, entries in merged.items():
            _summarize(name, entries)
        _log(f"\n매트릭스: {args.out}")
        return 0

    backends = ["gateway", "official"] if args.backend == "both" else [args.backend]
    raw_all: dict = {}
    for name in backends:
        try:
            matrix, raws = run_backend(name, args, images)
        except Exception as exc:  # noqa: BLE001
            _log(f"[{name}] 건너뜀: {exc}")
            continue
        result[name] = matrix
        raw_all[name] = raws

    if not raw_all:
        _log("스윕된 백엔드가 없습니다.")
        return 1

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, sort_keys=False)
    raw_out = args.raw_out or os.path.join(_ROOT, "tools", "sweep_raw", "sweep_raw.json")
    os.makedirs(os.path.dirname(raw_out), exist_ok=True)
    with open(raw_out, "w", encoding="utf-8") as f:
        json.dump(raw_all, f, ensure_ascii=False, indent=2)

    for name in raw_all:
        _summarize(name, result[name])
    _log(f"\n매트릭스: {args.out}\n원시 응답: {raw_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
