"""OcrEngine 단위 테스트 — winocr를 mock으로 대체해 순수 로직만 검증."""
import sys
import types
from unittest.mock import MagicMock
import pytest
from PIL import Image


# winocr가 설치되지 않은 환경에서도 테스트 실행 가능하도록 가짜 모듈 주입
def _inject_winocr_mock(mock_fn=None):
    """sys.modules에 winocr 가짜 모듈을 주입하고, 기존 캐시를 초기화한다."""
    fake = types.ModuleType("winocr")
    fake.recognize_pil_sync = mock_fn or MagicMock(return_value={"text": "hello"})
    sys.modules["winocr"] = fake
    # _check_winocr 캐시 초기화
    import pasteflow.ocr_engine as _m
    _m._winocr_checked = False
    _m._winocr_error = None
    return fake


@pytest.fixture(autouse=True)
def reset_winocr_cache():
    """각 테스트 전후로 winocr 캐시를 초기화해 테스트 간 간섭 방지."""
    import pasteflow.ocr_engine as _m
    _m._winocr_checked = False
    _m._winocr_error = None
    yield
    _m._winocr_checked = False
    _m._winocr_error = None
    sys.modules.pop("winocr", None)


class TestRecognizeWinrt:
    def test_passes_correct_lang(self):
        """recognize()가 recognize_pil_sync에 올바른 lang 인자를 넘긴다."""
        mock_fn = MagicMock(return_value={"text": "인식된 텍스트"})
        _inject_winocr_mock(mock_fn)

        from pasteflow.ocr_engine import OcrEngine
        engine = OcrEngine(language="ko")
        img = Image.new("RGB", (100, 100), "white")
        engine.recognize(img)

        mock_fn.assert_called_once()
        _, kwargs = mock_fn.call_args
        # positional 또는 keyword 모두 허용
        assert kwargs.get("lang") == "ko" or mock_fn.call_args.args[1] == "ko"

    def test_downscale_when_exceeds_4096(self):
        """4096px 초과 이미지는 recognize_pil_sync에 4096 이내 크기로 전달된다."""
        received_sizes = []

        def _fake_recognize(img, lang="ko"):
            received_sizes.append(img.size)
            return {"text": "ok"}

        _inject_winocr_mock(_fake_recognize)

        from pasteflow.ocr_engine import OcrEngine
        engine = OcrEngine(language="ko")
        img = Image.new("RGB", (5000, 3000), "white")
        engine.recognize(img)

        assert received_sizes, "recognize_pil_sync가 호출되지 않음"
        w, h = received_sizes[0]
        assert max(w, h) <= 4096, f"downscale 미적용: {w}×{h}"

    def test_rgba_converted_to_rgb_or_rgba(self):
        """RGBA 모드 이미지도 정상 처리된다 (RGBA는 허용된 모드)."""
        received_modes = []

        def _fake_recognize(img, lang="ko"):
            received_modes.append(img.mode)
            return {"text": "ok"}

        _inject_winocr_mock(_fake_recognize)

        from pasteflow.ocr_engine import OcrEngine
        engine = OcrEngine(language="ko")
        img = Image.new("RGBA", (100, 100), (255, 255, 255, 128))
        engine.recognize(img)

        assert received_modes, "recognize_pil_sync가 호출되지 않음"
        assert received_modes[0] in ("RGB", "RGBA"), f"예상치 못한 모드: {received_modes[0]}"

    def test_grayscale_converted_to_rgb(self):
        """L(그레이스케일) 모드 이미지는 RGB로 변환되어 전달된다."""
        received_modes = []

        def _fake_recognize(img, lang="ko"):
            received_modes.append(img.mode)
            return {"text": "ok"}

        _inject_winocr_mock(_fake_recognize)

        from pasteflow.ocr_engine import OcrEngine
        engine = OcrEngine(language="ko")
        img = Image.new("L", (100, 100), 200)
        engine.recognize(img)

        assert received_modes, "recognize_pil_sync가 호출되지 않음"
        assert received_modes[0] == "RGB", f"L→RGB 변환 미적용: {received_modes[0]}"

    def test_assertion_error_becomes_runtime_error_with_language_hint(self):
        """winocr AssertionError(언어팩 미지원) → RuntimeError, 메시지에 '언어팩' 포함."""

        def _fake_recognize(img, lang="ko"):
            raise AssertionError("Language not supported: ko")

        _inject_winocr_mock(_fake_recognize)

        from pasteflow.ocr_engine import OcrEngine
        engine = OcrEngine(language="ko")
        img = Image.new("RGB", (100, 100), "white")

        with pytest.raises(RuntimeError) as exc_info:
            engine.recognize(img)

        assert "언어팩" in str(exc_info.value), \
            f"오류 메시지에 '언어팩' 없음: {exc_info.value}"


class TestFamilyOf:
    def test_plain_prefixes(self):
        from pasteflow.ocr_engine import family_of
        assert family_of("gemini-2.5-flash") == "Gemini"
        assert family_of("claude-opus-4-8") == "Claude"
        assert family_of("gpt-5-mini") == "GPT"
        assert family_of("grok-4") == "Grok"

    def test_uses_basename_after_slash(self):
        """게이트웨이는 'accounts/fireworks/models/gpt-oss-120b' 같은 경로형 ID를 준다."""
        from pasteflow.ocr_engine import family_of
        assert family_of("accounts/fireworks/models/gpt-oss-120b") == "GPT"
        assert family_of("google/gemma-3-27b-it") == "Gemma"
        assert family_of("meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8") == "Llama"

    def test_case_insensitive(self):
        from pasteflow.ocr_engine import family_of
        assert family_of("LGAI-EXAONE/K-EXAONE-236B-A23B") == "EXAONE"

    def test_unknown_goes_to_other(self):
        from pasteflow.ocr_engine import family_of
        assert family_of("some-brand-new-model") == "기타"


class TestGroupModels:
    def test_families_in_fixed_order_and_empty_ones_dropped(self):
        from pasteflow.ocr_engine import group_models
        groups = group_models(["gpt-5", "gemini-2.5-flash", "claude-opus-4-8"])
        assert [label for label, _ in groups] == ["Gemini", "Claude", "GPT"]

    def test_uppercase_ids_do_not_break_alphabetical_order(self):
        from pasteflow.ocr_engine import group_models
        groups = group_models(["gemini-z", "gemini-A"])
        assert groups[0][1] == ["gemini-A", "gemini-z"]

    def test_no_model_is_dropped(self):
        """콤보가 모든 모델을 보여야 한다 — 되는지 여부는 연결 테스트가 판정한다."""
        from pasteflow.ocr_engine import group_models
        names = ["solar-pro2", "gpt-5.2-codex", "gemini-2.5-flash", "brand-new-9"]
        flat = [n for _label, group in group_models(names) for n in group]
        assert sorted(flat) == sorted(names)


class TestSelectFallbackModel:
    def test_returns_default_safety_net(self):
        from pasteflow.ocr_engine import select_fallback_model
        assert select_fallback_model("gemini-anything") == "gemini-2.5-flash"

    def test_skips_failed_model_when_it_is_default(self):
        from pasteflow.ocr_engine import select_fallback_model
        result = select_fallback_model("gemini-2.5-flash")
        assert result is not None
        assert result != "gemini-2.5-flash"

    def test_never_returns_the_failed_model(self):
        from pasteflow.ocr_engine import _FALLBACK_CHAIN, select_fallback_model
        for failed in _FALLBACK_CHAIN + ("some-other-model",):
            assert select_fallback_model(failed) != failed


class TestProbeErrorClassification:
    """`retry`(서버 사정)와 `fail`(모델이 못 함)을 섞지 않는지 — 섞으면 멀쩡한 모델을
    나쁜 모델로 표시하게 된다(옛 매트릭스 1차 스윕에서 실제로 벌어진 사고)."""

    def test_quota_is_retry_not_fail(self):
        from pasteflow.ocr_engine import _classify_probe_error
        assert _classify_probe_error(Exception("429 RESOURCE_EXHAUSTED")).status == "retry"

    def test_server_busy_is_retry(self):
        from pasteflow.ocr_engine import _classify_probe_error
        assert _classify_probe_error(Exception("503 Service Unavailable")).status == "retry"
        assert _classify_probe_error(Exception("model is overloaded")).status == "retry"

    def test_model_not_found_is_fail(self):
        from pasteflow.ocr_engine import _classify_probe_error
        r = _classify_probe_error(Exception("Error code: 404 - Model 'x' not found"))
        assert r.status == "fail"
        assert "404" in r.detail

    def test_image_rejection_only_classified_when_image_was_sent(self):
        from pasteflow.ocr_engine import _classify_probe_error
        exc = Exception("Error code: 400 - image input is not supported")
        assert "이미지" in _classify_probe_error(exc, with_image=True).detail
        # 텍스트 프로브에서는 같은 400이라도 이미지 탓으로 단정하지 않는다.
        assert "이미지" not in _classify_probe_error(exc, with_image=False).detail

    def test_unknown_error_is_fail_with_truncated_detail(self):
        from pasteflow.ocr_engine import _classify_probe_error
        r = _classify_probe_error(Exception("x" * 400))
        assert r.status == "fail"
        assert len(r.detail) <= 140


class TestProbeImage:
    def test_probe_image_is_a_readable_png(self):
        import io
        from PIL import Image
        from pasteflow.ocr_engine import _probe_image_png
        img = Image.open(io.BytesIO(_probe_image_png()))
        assert img.format == "PNG"
        assert img.size == (160, 80)

    def test_probe_image_actually_has_dark_glyphs(self):
        """흰 캔버스만 보내면 OCR 프로브가 항상 weak로 뜬다 — 글자가 실제로 그려져야."""
        import io
        from PIL import Image
        from pasteflow.ocr_engine import _probe_image_png
        img = Image.open(io.BytesIO(_probe_image_png())).convert("L")
        assert sum(img.histogram()[:100]) > 50  # 어두운 픽셀 = 그려진 글자


class TestProbeChatModel:
    """AI 질의 프로브는 **이미지 첨부까지** 본다 — 이미지 항목 우클릭 'AI에게 질문'이
    OCR 모델이 아니라 이 모델로 이미지를 멀티모달 전송하기 때문이다.

    판별은 에러 메시지 문구가 아니라 '텍스트만으로 재시도'로 한다(게이트웨이가 뭐라고
    답하든 흔들리지 않게). 이 분기가 깨지면 텍스트 전용 모델이 조용히 ✓로 통과한다.
    """

    def _patch(self, monkeypatch, behavior):
        """(_chat_probe_call 대체) behavior(image_png) → 반환값 or raise."""
        import pasteflow.ocr_engine as oe
        calls = []

        def fake(api_key, base_url, model, image_png):
            calls.append(image_png is not None)
            return behavior(image_png)

        monkeypatch.setattr(oe, "_chat_probe_call", fake)
        return calls

    def test_ok_when_image_call_succeeds(self, monkeypatch):
        from pasteflow.ocr_engine import probe_chat_model
        calls = self._patch(monkeypatch, lambda img: "42")
        r = probe_chat_model("k", "", "gemini-2.5-flash")
        assert r.status == "ok"
        assert calls == [True], "이미지를 실은 호출 한 번으로 끝나야(비용)"

    def test_weak_when_only_the_image_call_fails(self, monkeypatch):
        """텍스트 전용 모델(solar-pro2 등) — 옛 📝 배지가 잡던 케이스."""
        from pasteflow.ocr_engine import probe_chat_model

        def behavior(img):
            if img is not None:
                raise RuntimeError("Error code: 400 - 뭐라고 하든 상관없음")
            return "2"

        calls = self._patch(monkeypatch, behavior)
        r = probe_chat_model("k", "https://gw", "solar-pro2")
        assert r.status == "weak"
        assert "이미지 첨부" in r.detail
        assert calls == [True, False], "이미지 실패 후 텍스트만으로 재시도해야"

    def test_fail_when_text_call_also_fails(self, monkeypatch):
        from pasteflow.ocr_engine import probe_chat_model

        def behavior(img):
            raise RuntimeError("Error code: 404 - Model 'x' not found")

        self._patch(monkeypatch, behavior)
        r = probe_chat_model("k", "", "ghost-model")
        assert r.status == "fail"
        assert "404" in r.detail

    def test_quota_on_image_call_is_retry_not_weak(self, monkeypatch):
        """429는 '못 재본 것'이지 '못 하는 것'이 아니다 — weak/fail로 굳히면 안 된다."""
        from pasteflow.ocr_engine import probe_chat_model

        def behavior(img):
            if img is not None:
                raise RuntimeError("429 RESOURCE_EXHAUSTED")
            return "2"

        self._patch(monkeypatch, behavior)
        r = probe_chat_model("k", "", "gemini-3.1-flash-lite")
        assert r.status == "retry"

    def test_empty_model_is_fail_without_calling(self, monkeypatch):
        from pasteflow.ocr_engine import probe_chat_model
        calls = self._patch(monkeypatch, lambda img: "42")
        assert probe_chat_model("k", "", "").status == "fail"
        assert calls == []


class TestModelNotFoundDetection:
    def test_detects_gateway_404_message(self):
        """사용자가 본 실제 게이트웨이 응답 형태."""
        from pasteflow.ocr_engine import _is_model_not_found
        msg = (
            "Error code: 404 - {'detail': {'code': 404, 'message': "
            "\"invalid_request_error - Model 'gemini-3.1-flash-lite-preview' not found\"}}"
        )
        assert _is_model_not_found(Exception(msg))

    def test_detects_model_not_found_token(self):
        from pasteflow.ocr_engine import _is_model_not_found
        assert _is_model_not_found(Exception("model_not_found"))

    def test_does_not_match_rate_limit(self):
        from pasteflow.ocr_engine import _is_model_not_found
        assert not _is_model_not_found(Exception("rate limit exceeded"))

    def test_does_not_match_network_timeout(self):
        from pasteflow.ocr_engine import _is_model_not_found
        assert not _is_model_not_found(Exception("connection timed out"))


class TestCallWithFallback:
    def test_no_fallback_on_success(self):
        from pasteflow.ocr_engine import OcrEngine
        engine = OcrEngine(kind="gemini")
        result = engine._call_with_fallback(
            "gemini-2.5-flash",
            call=lambda m: f"text-from-{m}",
        )
        assert result == "text-from-gemini-2.5-flash"
        assert engine.last_used_model == "gemini-2.5-flash"
        assert engine.last_fallback_from is None

    def test_fallback_on_model_not_found(self):
        from pasteflow.ocr_engine import OcrEngine
        engine = OcrEngine(kind="gemini")
        calls = []

        def _call(m):
            calls.append(m)
            if m == "gemini-foo-preview":
                raise RuntimeError(
                    "Error code: 404 - Model 'gemini-foo-preview' not found"
                )
            return f"text-from-{m}"

        result = engine._call_with_fallback("gemini-foo-preview", call=_call)
        assert result == "text-from-gemini-2.5-flash"
        assert engine.last_fallback_from == "gemini-foo-preview"
        assert engine.last_used_model == "gemini-2.5-flash"
        assert calls == ["gemini-foo-preview", "gemini-2.5-flash"]

    def test_no_fallback_on_other_error(self):
        from pasteflow.ocr_engine import OcrEngine
        engine = OcrEngine(kind="gemini")

        def _call(m):
            raise RuntimeError("rate limit exceeded")

        with pytest.raises(RuntimeError, match="rate limit"):
            engine._call_with_fallback("gemini-2.5-flash", call=_call)
        assert engine.last_fallback_from is None


# ── 웹 검색 도구 왕복 (게이트웨이 chat.completions 경로) ────────────────────
# 게이트웨이는 제공사 내장 검색을 chat.completions로 실어 주지 않으므로, 모델이 요청하면
# 우리가 직접 검색해 결과를 되돌려준다. 여기서 지키는 계약:
#   1) tool_calls가 오면 검색을 실행하고 재호출해 최종 답을 얻는다
#   2) gemini-3의 thought_signature를 에코백한다 (없으면 게이트웨이가 400)
#   3) 모델이 검색만 반복해도 _MAX_TOOL_ROUNDS에서 끊고 답을 낸다

def _fake_message(content=None, tool_calls=None):
    """openai SDK의 message 객체 흉내 — .content와 .model_dump()만 쓴다."""
    msg = MagicMock()
    msg.content = content
    msg.model_dump.return_value = {"content": content, "tool_calls": tool_calls or []}
    return msg


def _fake_response(message):
    resp = MagicMock()
    resp.choices = [MagicMock(message=message)]
    return resp


def _tool_call(call_id="c1", query="내일 서울 날씨", extra_content=None):
    call = {"id": call_id, "type": "function",
            "function": {"name": "web_search",
                         "arguments": '{"query": "%s"}' % query}}
    if extra_content:
        call["extra_content"] = extra_content
    return call


def _install_fake_openai(monkeypatch, responses):
    """가짜 openai 모듈을 심고, chat.completions.create가 responses를 차례로 뱉게 한다.
    호출 시 받은 kwargs를 기록해 반환한다(무엇을 보냈는지 검증용).
    """
    sent = []

    def _create(**kwargs):
        sent.append(kwargs)
        return responses[len(sent) - 1]

    client = MagicMock()
    client.chat.completions.create = _create
    fake = types.ModuleType("openai")
    fake.OpenAI = MagicMock(return_value=client)
    monkeypatch.setitem(sys.modules, "openai", fake)
    return sent


class TestWebSearchToolLoop:
    def test_tool_call이_오면_검색후_재호출해_최종답을_낸다(self, monkeypatch):
        from pasteflow.ocr_engine import OcrEngine
        from pasteflow import web_search

        monkeypatch.setattr(web_search, "search",
                            lambda q, **kw: f"[검색결과] {q}: 비, 29도")
        sent = _install_fake_openai(monkeypatch, [
            _fake_response(_fake_message(tool_calls=[_tool_call()])),
            _fake_response(_fake_message(content="내일 서울은 비, 최고 29도입니다.")),
        ])

        engine = OcrEngine(kind="gemini", api_key="k", base_url="https://gw/v1/gateway")
        out = engine._ask_openai_compat(
            [{"role": "user", "content": "내일 서울 날씨?"}], "k", "https://gw/v1/gateway", "gemini-3.1-flash-lite")

        assert out == "내일 서울은 비, 최고 29도입니다."
        assert len(sent) == 2, "검색 후 재호출이 일어나야 한다"
        # 2번째 호출에 검색 결과가 tool 메시지로 실려야 모델이 그걸 읽고 답할 수 있다
        tool_msgs = [m for m in sent[1]["messages"] if m.get("role") == "tool"]
        assert len(tool_msgs) == 1
        assert "비, 29도" in tool_msgs[0]["content"]

    def test_gemini3의_thought_signature를_에코백한다(self, monkeypatch):
        # 빠뜨리면 게이트웨이가 400 "missing a thought_signature"로 대화를 끊는다.
        from pasteflow.ocr_engine import OcrEngine
        from pasteflow import web_search

        monkeypatch.setattr(web_search, "search", lambda q, **kw: "결과")
        sig = {"google": {"thought_signature": "SIG-ABC"}}
        sent = _install_fake_openai(monkeypatch, [
            _fake_response(_fake_message(tool_calls=[_tool_call(extra_content=sig)])),
            _fake_response(_fake_message(content="답변")),
        ])

        engine = OcrEngine(kind="gemini", api_key="k", base_url="https://gw")
        engine._ask_openai_compat([{"role": "user", "content": "q"}], "k", "https://gw", "gemini-3.1-flash-lite")

        assistant = [m for m in sent[1]["messages"] if m.get("role") == "assistant"][0]
        assert assistant["tool_calls"][0]["extra_content"] == sig

    def test_서명이_없는_모델은_그대로_통과한다(self, monkeypatch):
        # gpt·claude·gemini-2.5는 서명을 안 보낸다 — 없는 키를 만들어 넣으면 안 된다.
        from pasteflow.ocr_engine import OcrEngine
        from pasteflow import web_search

        monkeypatch.setattr(web_search, "search", lambda q, **kw: "결과")
        sent = _install_fake_openai(monkeypatch, [
            _fake_response(_fake_message(tool_calls=[_tool_call()])),
            _fake_response(_fake_message(content="답변")),
        ])

        engine = OcrEngine(kind="gemini", api_key="k", base_url="https://gw")
        engine._ask_openai_compat([{"role": "user", "content": "q"}], "k", "https://gw", "claude-sonnet-5")

        assistant = [m for m in sent[1]["messages"] if m.get("role") == "assistant"][0]
        assert "extra_content" not in assistant["tool_calls"][0]

    def test_검색을_안_하면_한_번만_호출한다(self, monkeypatch):
        # 평범한 질문(코드·번역 등)에 검색이 돌면 지연·비용만 늘어난다.
        from pasteflow.ocr_engine import OcrEngine
        from pasteflow import web_search

        called = []
        monkeypatch.setattr(web_search, "search", lambda q, **kw: called.append(q) or "x")
        sent = _install_fake_openai(monkeypatch, [
            _fake_response(_fake_message(content="1+1은 2입니다.")),
        ])

        engine = OcrEngine(kind="gemini", api_key="k", base_url="https://gw")
        out = engine._ask_openai_compat([{"role": "user", "content": "1+1?"}], "k", "https://gw", "gpt-5-mini")

        assert out == "1+1은 2입니다."
        assert len(sent) == 1
        assert called == [], "모델이 요청하지 않으면 검색하지 않는다"

    def test_계속_검색만_요청해도_최대_라운드에서_끊고_답을_낸다(self, monkeypatch):
        # 무한 검색 루프 방지. 마지막 호출은 도구를 떼고 "지금까지 찾은 걸로 답하라"고 강제.
        from pasteflow.ocr_engine import OcrEngine
        from pasteflow import web_search
        from pasteflow.ocr_engine import _MAX_TOOL_ROUNDS

        monkeypatch.setattr(web_search, "search", lambda q, **kw: "결과")
        responses = [_fake_response(_fake_message(tool_calls=[_tool_call(call_id=f"c{i}")]))
                     for i in range(_MAX_TOOL_ROUNDS)]
        responses.append(_fake_response(_fake_message(content="찾은 정보로 답합니다.")))
        sent = _install_fake_openai(monkeypatch, responses)

        engine = OcrEngine(kind="gemini", api_key="k", base_url="https://gw")
        out = engine._ask_openai_compat([{"role": "user", "content": "q"}], "k", "https://gw", "gemini-3.1-flash-lite")

        assert out == "찾은 정보로 답합니다."
        assert len(sent) == _MAX_TOOL_ROUNDS + 1
        assert "tools" not in sent[-1], "마지막 호출은 도구를 떼야 또 검색을 요청하지 않는다"

    def test_검색_진행_콜백이_시작과_종료를_알린다(self, monkeypatch):
        from pasteflow.ocr_engine import OcrEngine
        from pasteflow import web_search

        monkeypatch.setattr(web_search, "search", lambda q, **kw: "결과")
        _install_fake_openai(monkeypatch, [
            _fake_response(_fake_message(tool_calls=[_tool_call(query="코스피")])),
            _fake_response(_fake_message(content="답변")),
        ])

        seen = []
        engine = OcrEngine(kind="gemini", api_key="k", base_url="https://gw")
        engine.on_tool_progress = seen.append
        engine._ask_openai_compat([{"role": "user", "content": "q"}], "k", "https://gw", "gemini-3.1-flash-lite")

        assert seen == ["코스피", ""], "검색 시작(검색어) → 종료(빈 문자열)"

    def test_도구를_못_받는_모델은_도구를_떼고_다시_물어_답을_낸다(self, monkeypatch):
        # 실측 회귀: meta-llama/Llama-4-Maverick은 tools를 붙이면 405로 죽는다. 도구 없이는
        # 멀쩡히 답하던 모델이므로, 도구를 무조건 붙이면 예전 동작이 통째로 깨진다.
        from pasteflow.ocr_engine import OcrEngine

        sent = []

        def _create(**kwargs):
            sent.append(kwargs)
            if "tools" in kwargs:
                raise RuntimeError("Error code: 405 - tools not supported")
            return _fake_response(_fake_message(content="2"))

        client = MagicMock()
        client.chat.completions.create = _create
        fake = types.ModuleType("openai")
        fake.OpenAI = MagicMock(return_value=client)
        monkeypatch.setitem(sys.modules, "openai", fake)

        engine = OcrEngine(kind="gemini", api_key="k", base_url="https://gw")
        out = engine._ask_openai_compat(
            [{"role": "user", "content": "1+1?"}], "k", "https://gw",
            "meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8")

        assert out == "2", "도구를 떼고 재시도해 답을 내야 한다"
        assert "tools" in sent[0] and "tools" not in sent[1]

    def test_진짜_오류는_도구를_떼도_그대로_올라간다(self, monkeypatch):
        # 도구 재시도가 진짜 오류(키 불량 등)를 삼켜 버리면 사용자가 원인을 못 본다.
        from pasteflow.ocr_engine import OcrEngine

        def _create(**kwargs):
            raise RuntimeError("401 invalid api key")

        client = MagicMock()
        client.chat.completions.create = _create
        fake = types.ModuleType("openai")
        fake.OpenAI = MagicMock(return_value=client)
        monkeypatch.setitem(sys.modules, "openai", fake)

        engine = OcrEngine(kind="gemini", api_key="k", base_url="https://gw")
        with pytest.raises(RuntimeError, match="invalid api key"):
            engine._ask_openai_compat([{"role": "user", "content": "q"}], "k", "https://gw", "claude-sonnet-5")

    def test_시스템_프롬프트에_오늘_날짜가_박힌다(self, monkeypatch):
        # 날짜가 없으면 모델이 검색 결과를 받고도 "'내일'이 언제인지 모르겠다"고 답한다.
        import datetime as dt
        from pasteflow.ocr_engine import OcrEngine

        sent = _install_fake_openai(monkeypatch, [_fake_response(_fake_message(content="답"))])
        engine = OcrEngine(kind="gemini", api_key="k", base_url="https://gw")
        engine._ask_openai_compat([{"role": "user", "content": "q"}], "k", "https://gw", "gpt-5-mini")

        system = sent[0]["messages"][0]
        today = dt.date.today()
        assert system["role"] == "system"
        assert f"{today.year}년 {today.month}월 {today.day}일" in system["content"]


class TestResponsesApiRouting:
    """GPT는 Responses API(내장 웹 검색)로, 나머지는 chat.completions(DDG)로 간다."""

    def test_gpt_계열만_responses를_탄다(self):
        from pasteflow.ocr_engine import supports_responses_api
        assert supports_responses_api("gpt-5-mini")
        assert supports_responses_api("gpt-5.5")
        assert not supports_responses_api("claude-sonnet-5")
        assert not supports_responses_api("gemini-3.1-flash-lite")

    def test_이름만_gpt인_서드파티_모델은_제외한다(self):
        # accounts/fireworks/models/gpt-oss-120b 는 OpenAI 모델이 아니라 Responses를 못 탄다.
        from pasteflow.ocr_engine import supports_responses_api
        assert not supports_responses_api("accounts/fireworks/models/gpt-oss-120b")

