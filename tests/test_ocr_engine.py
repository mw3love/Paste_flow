"""OcrEngine 단위 테스트 — winocr를 mock으로 대체해 순수 로직만 검증."""
import sys
import types
from unittest.mock import MagicMock, patch, call
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


class TestRecognizeAiApi:
    """AI API OCR 경로 테스트 — anthropic 패키지를 mock으로 대체."""

    @pytest.fixture(autouse=True)
    def cleanup_anthropic(self):
        """각 테스트 전후로 sys.modules["anthropic"] 상태를 복원."""
        had_it = "anthropic" in sys.modules
        orig = sys.modules.get("anthropic")
        yield
        if had_it:
            sys.modules["anthropic"] = orig
        else:
            sys.modules.pop("anthropic", None)

    def _inject_anthropic_mock(self, response_text: str = "인식 결과"):
        """sys.modules에 anthropic 가짜 모듈을 주입하고 (fake_module, mock_client)를 반환."""
        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text=response_text)]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_msg
        fake_anthropic = types.ModuleType("anthropic")
        fake_anthropic.Anthropic = MagicMock(return_value=mock_client)
        sys.modules["anthropic"] = fake_anthropic
        return fake_anthropic, mock_client

    def test_no_api_key_raises_runtime_error(self):
        """api_key=""이고 관련 환경 변수도 없으면 RuntimeError('API 키')."""
        self._inject_anthropic_mock()
        from pasteflow.ocr_engine import OcrEngine
        engine = OcrEngine(kind="ai_api", api_key="")
        img = Image.new("RGB", (50, 50), "white")

        with patch.dict("os.environ", {"ANTHROPIC_AUTH_TOKEN": "", "ANTHROPIC_API_KEY": ""}):
            with pytest.raises(RuntimeError) as exc_info:
                engine.recognize(img)

        assert "API 키" in str(exc_info.value)

    def test_anthropic_not_installed_raises_runtime_error(self):
        """sys.modules["anthropic"]=None으로 미설치 시뮬레이션 → RuntimeError('anthropic 패키지 미설치')."""
        sys.modules["anthropic"] = None  # import 시 ImportError 발생
        from pasteflow.ocr_engine import OcrEngine
        engine = OcrEngine(kind="ai_api", api_key="sk-test")
        img = Image.new("RGB", (50, 50), "white")

        with pytest.raises(RuntimeError) as exc_info:
            engine.recognize(img)

        assert "anthropic 패키지 미설치" in str(exc_info.value)

    def test_correct_model_and_prompt_passed(self):
        """messages.create에 haiku 모델, base64 PNG 이미지, 텍스트 추출 프롬프트가 전달된다."""
        _, mock_client = self._inject_anthropic_mock("결과")
        from pasteflow.ocr_engine import OcrEngine
        engine = OcrEngine(kind="ai_api", api_key="sk-test")
        img = Image.new("RGB", (50, 50), "white")
        engine.recognize(img)

        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert call_kwargs["model"] == "claude-haiku-4-5-20251001"
        content = call_kwargs["messages"][0]["content"]
        assert content[0]["type"] == "image"
        assert content[0]["source"]["media_type"] == "image/png"
        assert content[0]["source"]["data"]  # base64 비어 있지 않음
        assert "텍스트만" in content[1]["text"]

    def test_strip_applied_to_result(self):
        """응답 텍스트 앞뒤 공백·개행이 제거된다."""
        self._inject_anthropic_mock("  결과 텍스트  \n")
        from pasteflow.ocr_engine import OcrEngine
        engine = OcrEngine(kind="ai_api", api_key="sk-test")
        img = Image.new("RGB", (50, 50), "white")

        result = engine.recognize(img)

        assert result == "결과 텍스트"

    def test_env_var_auth_token_used_as_fallback(self):
        """api_key="" + ANTHROPIC_AUTH_TOKEN 환경 변수 → 토큰이 Anthropic() api_key로 전달된다."""
        fake_anthropic, _ = self._inject_anthropic_mock()
        from pasteflow.ocr_engine import OcrEngine
        engine = OcrEngine(kind="ai_api", api_key="")
        img = Image.new("RGB", (50, 50), "white")

        with patch.dict("os.environ", {"ANTHROPIC_AUTH_TOKEN": "env-token", "ANTHROPIC_API_KEY": ""}):
            engine.recognize(img)

        init_kwargs = fake_anthropic.Anthropic.call_args.kwargs
        assert init_kwargs.get("api_key") == "env-token"
