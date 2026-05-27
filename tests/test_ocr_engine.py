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


class TestWhitelistSorting:
    def test_separates_verified_and_unverified(self):
        from pasteflow.ocr_engine import sort_models_with_whitelist
        verified, unverified = sort_models_with_whitelist(
            ["gemini-2.5-flash", "gemini-foo-x", "gemini-2.5-pro"],
            backend="gateway",
        )
        assert "gemini-2.5-flash" in verified
        assert "gemini-2.5-pro" in verified
        assert unverified == ["gemini-foo-x"]

    def test_verified_sorted_by_tier_ascending(self):
        from pasteflow.ocr_engine import sort_models_with_whitelist
        verified, _ = sort_models_with_whitelist(
            ["gemini-2.5-pro", "gemini-3.1-flash-lite", "gemini-2.5-flash"],
            backend="gateway",
        )
        # tier 0(flash-lite) → 1(flash) → 2(pro)
        assert verified == ["gemini-3.1-flash-lite", "gemini-2.5-flash", "gemini-2.5-pro"]

    def test_filters_by_backend_official_vs_gateway(self):
        """gemini-3.1-flash-lite는 게이트웨이 검증, official 검증은 아님."""
        from pasteflow.ocr_engine import sort_models_with_whitelist
        v, u = sort_models_with_whitelist(
            ["gemini-3.1-flash-lite", "gemini-2.5-flash"],
            backend="official",
        )
        assert "gemini-2.5-flash" in v
        assert "gemini-3.1-flash-lite" in u

    def test_unverified_sorted_alphabetically(self):
        from pasteflow.ocr_engine import sort_models_with_whitelist
        _, u = sort_models_with_whitelist(
            ["gemini-z-mystery", "gemini-a-mystery"],
            backend="gateway",
        )
        assert u == ["gemini-a-mystery", "gemini-z-mystery"]

    def test_invalid_backend_raises(self):
        from pasteflow.ocr_engine import sort_models_with_whitelist
        with pytest.raises(ValueError):
            sort_models_with_whitelist(["gemini-2.5-flash"], backend="???")


class TestWhitelistNames:
    def test_returns_gateway_compatible_models(self):
        from pasteflow.ocr_engine import whitelist_model_names
        names = whitelist_model_names("gateway")
        assert "gemini-2.5-flash" in names
        assert "gemini-3.1-flash-lite" in names

    def test_excludes_gateway_only_for_official_backend(self):
        from pasteflow.ocr_engine import whitelist_model_names
        names = whitelist_model_names("official")
        assert "gemini-3.1-flash-lite" not in names
        assert "gemini-2.5-flash" in names

    def test_sorted_by_tier_cheapest_first(self):
        from pasteflow.ocr_engine import whitelist_model_names
        names = whitelist_model_names("gateway")
        # 첫 항목은 가장 저렴한 티어
        assert "flash-lite" in names[0]


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

