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


