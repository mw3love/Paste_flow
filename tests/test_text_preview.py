"""AI 답변창의 마크다운 전처리(순수 함수) — 렌더 전에 문법을 고치는 부분만 검증.

UI 자체는 수동 확인 대상이지만, 이 전처리는 텍스트→텍스트 순수 함수라 테스트로 고정한다.
"""
from pasteflow.ui.text_preview import _fix_markdown_emphasis


class TestAutolinkCodeUrls:
    """모델이 URL을 백틱으로 감싸 보내면 Qt가 링크가 아니라 '코드'로 렌더한다.

    코드색과 링크색이 둘 다 파랑+밑줄이라 **링크처럼 보이는데 눌리지 않는** 상태가 된다.
    백틱을 벗겨 맨 URL로 두면 Qt가 자동으로 앵커를 만들어 클릭이 살아난다.
    """

    def test_백틱_URL은_맨_URL로_풀어_링크가_되게_한다(self):
        out = _fix_markdown_emphasis("출처: `https://weather.com/seoul`")
        assert out == "출처: https://weather.com/seoul"

    def test_꺾쇠_autolink_형태도_받는다(self):
        out = _fix_markdown_emphasis("- `<https://drive.google.com/file/d/abc>`")
        assert out == "- https://drive.google.com/file/d/abc"

    def test_URL이_아닌_코드는_백틱을_유지한다(self):
        # 파일명·식별자는 코드로 보여야 한다(파랑+밑줄+볼드). 링크로 바꿀 대상이 아니다.
        out = _fix_markdown_emphasis("`main.py`와 `1MKknENR-891qWdXbR5L0`")
        assert out == "`main.py`와 `1MKknENR-891qWdXbR5L0`"

    def test_코드블록_안의_URL은_건드리지_않는다(self):
        # 코드블록은 '코드를 코드로 보여주는' 자리 — 링크로 바꾸면 복붙용 코드가 오염된다.
        src = "```\ncurl `https://api.example.com/v1`\n```"
        assert _fix_markdown_emphasis(src) == src

    def test_이미_마크다운_링크면_그대로_둔다(self):
        src = "[PRD.md](https://drive.google.com/file/d/xyz)"
        assert _fix_markdown_emphasis(src) == src

    def test_기존_전처리와_함께_동작한다(self):
        # 볼드+백틱 중첩 해소(_BOLD_CODE_RE)는 그대로 유지되어야 한다.
        out = _fix_markdown_emphasis("**`main.py`** 참고, 출처 `https://ex.com`")
        assert out == "`main.py` 참고, 출처 https://ex.com"
