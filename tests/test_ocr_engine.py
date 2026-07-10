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
        groups = group_models(["gpt-5", "gemini-2.5-flash", "claude-opus-4-8"],
                              usable=lambda _n: True)
        assert [label for label, _ in groups] == ["Gemini", "Claude", "GPT"]

    def test_unusable_sink_to_bottom_of_their_family(self):
        from pasteflow.ocr_engine import group_models
        groups = group_models(["gpt-5.2-codex", "gpt-5", "gpt-5-mini"],
                              usable=lambda n: n != "gpt-5.2-codex")
        _label, names = groups[0]
        assert names[-1] == "gpt-5.2-codex"

    def test_uppercase_ids_do_not_break_alphabetical_order(self):
        from pasteflow.ocr_engine import group_models
        groups = group_models(["gemini-z", "gemini-A"], usable=lambda _n: True)
        assert groups[0][1] == ["gemini-A", "gemini-z"]


class TestModelMatrix:
    """실제로 배포되는 model_matrix.json이 스윕 결과와 일치하는지 (회귀 방지)."""

    def test_matrix_loads_and_has_both_backends(self):
        from pasteflow.ocr_engine import load_model_matrix
        m = load_model_matrix()
        assert "gateway" in m and "official" in m

    def test_no_vision_models_are_blocked_for_ocr_only(self):
        """이미지 400을 내는 모델: 질의는 되고 OCR만 막혀야 한다."""
        from pasteflow.ocr_engine import chat_capable, ocr_capable
        for name in ("solar-pro2", "solar-pro3",
                     "accounts/fireworks/models/gpt-oss-120b",
                     "LGAI-EXAONE/K-EXAONE-236B-A23B"):
            assert chat_capable(name, "gateway"), name
            assert not ocr_capable(name, "gateway"), name

    def test_no_vision_models_stay_distinguishable_for_ai_combo(self):
        """AI 질의 콤보는 이들을 막지 않지만 '텍스트 전용'임을 알 수 있어야 한다.

        이미지 항목 우클릭 "AI에게 질문"이 AI 모델로 이미지를 멀티모달 전송하므로,
        비전 미지원 모델을 고르면 조용히 400이 난다(설정창이 📝로 고지).
        """
        from pasteflow.ocr_engine import chat_capable, model_status
        st = model_status("solar-pro2", "gateway")
        assert chat_capable("solar-pro2", "gateway")
        assert st["ocr"] == "fail"

    def test_endpoint_unsupported_models_blocked_everywhere(self):
        from pasteflow.ocr_engine import chat_capable, ocr_capable
        for name in ("gpt-5.1-codex-max", "gpt-5.2-codex", "gpt-5.3-codex"):
            assert not chat_capable(name, "gateway"), name
            assert not ocr_capable(name, "gateway"), name

    def test_weak_ocr_models_are_selectable_but_flagged(self):
        from pasteflow.ocr_engine import ocr_capable, ocr_is_weak
        assert ocr_capable("claude-haiku-4-5-20251001", "gateway")
        assert ocr_is_weak("claude-haiku-4-5-20251001", "gateway")
        assert not ocr_is_weak("gemini-2.5-flash", "gateway")

    def test_unknown_models_are_not_blocked(self):
        """스윕이 못 재본 모델(429)은 막지 않는다 — fail과 unknown은 다르다."""
        from pasteflow.ocr_engine import chat_capable, is_measured, ocr_capable
        name = "gemini-2.5-pro"  # official에서 429로 미측정
        assert chat_capable(name, "official")
        assert ocr_capable(name, "official")
        assert not is_measured(name, "official")

    def test_unlisted_model_defaults_to_unknown_and_usable(self):
        from pasteflow.ocr_engine import chat_capable, is_measured, ocr_capable
        assert chat_capable("brand-new-model-9", "gateway")
        assert ocr_capable("brand-new-model-9", "gateway")
        assert not is_measured("brand-new-model-9", "gateway")

    def test_invalid_backend_raises(self):
        from pasteflow.ocr_engine import model_status
        with pytest.raises(ValueError):
            model_status("gemini-2.5-flash", "???")


class TestSelectFallbackModel:
    def test_returns_default_safety_net(self):
        from pasteflow.ocr_engine import select_fallback_model
        assert select_fallback_model("gemini-anything", "gateway") == "gemini-2.5-flash"

    def test_skips_failed_model_when_it_is_default(self):
        from pasteflow.ocr_engine import select_fallback_model
        result = select_fallback_model("gemini-2.5-flash", "gateway")
        assert result is not None
        assert result != "gemini-2.5-flash"

    def test_invalid_backend_raises(self):
        from pasteflow.ocr_engine import select_fallback_model
        with pytest.raises(ValueError):
            select_fallback_model("gemini-anything", "???")

    def test_official_has_a_fallback_despite_unmeasured_matrix(self):
        """공식 백엔드는 스윕이 429로 막혀 실측 통과 모델이 0개다.

        '실측 통과'만 후보로 삼으면 폴백이 통째로 None이 되어 안전망이 사라진다.
        (회귀 방지 — 매트릭스 도입 때 실제로 None을 반환했다.)
        """
        from pasteflow.ocr_engine import select_fallback_model
        result = select_fallback_model("gemini-2.5-flash", "official")
        assert result is not None
        assert result != "gemini-2.5-flash"

    def test_fallback_never_picks_a_known_bad_model(self):
        from pasteflow.ocr_engine import select_fallback_model
        bad = {"gpt-5.1-codex-max", "gpt-5.2-codex", "gpt-5.3-codex",
               "solar-pro2", "solar-pro3"}
        for failed in ("gemini-2.5-flash", "claude-opus-4-8"):
            assert select_fallback_model(failed, "gateway") not in bad


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
            "gemini-2.5-flash", "gateway",
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

        result = engine._call_with_fallback("gemini-foo-preview", "gateway", call=_call)
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
            engine._call_with_fallback("gemini-2.5-flash", "gateway", call=_call)
        assert engine.last_fallback_from is None

