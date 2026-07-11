"""웹 검색 — 결과 포맷팅(순수 함수)과 실패 시 '예외 대신 문자열' 계약을 검증.

실패해도 예외를 올리지 않는 것이 이 모듈의 핵심 계약이다. 도구 실행이 예외로 터지면
대화가 끊겨 사용자는 답을 아예 못 받는다. 실패 사유를 문자열로 넘겨야 모델이 학습
지식으로라도 답하고 "검색이 안 됐다"고 밝힐 수 있다.
"""
import sys
import types
from unittest.mock import MagicMock

from pasteflow import web_search


def _inject_ddgs(text_fn):
    """sys.modules에 가짜 ddgs를 주입한다(네트워크 없이 검색 경로를 태우기 위해)."""
    fake = types.ModuleType("ddgs")
    ddgs_obj = MagicMock()
    ddgs_obj.text = text_fn
    fake.DDGS = MagicMock(return_value=ddgs_obj)
    sys.modules["ddgs"] = fake
    return fake


class TestFormatResults:
    def test_결과를_번호_제목_본문_출처로_정리한다(self):
        out = web_search.format_results("코스피", [
            {"title": "코스피 지수", "body": "7,475.94에 마감", "href": "https://ex.com/a"},
        ])
        assert "코스피 지수" in out
        assert "7,475.94에 마감" in out
        assert "https://ex.com/a" in out
        assert "[1]" in out

    def test_출처는_href_없으면_url_키도_본다(self):
        out = web_search.format_results("q", [{"title": "t", "body": "b", "url": "https://u"}])
        assert "https://u" in out

    def test_긴_본문은_잘라_프롬프트_비대를_막는다(self):
        out = web_search.format_results("q", [{"title": "t", "body": "가" * 900, "href": "u"}])
        assert "…" in out
        assert len(out) < 700

    def test_본문의_줄바꿈은_한_줄로_눕힌다(self):
        # 스니펫에 개행이 섞이면 "[2]" 같은 다음 항목 경계와 뒤섞여 읽기 어려워진다.
        out = web_search.format_results("q", [{"title": "t", "body": "a\nb\nc", "href": "u"}])
        assert "a b c" in out

    def test_결과가_없으면_없다고_알린다(self):
        out = web_search.format_results("없는질의", [])
        assert "없는질의" in out and "없습니다" in out


class TestSearch:
    """자격증명이 없을 때 = DuckDuckGo 안전망 경로."""

    def test_검색어가_비면_호출하지_않고_실패_문자열(self):
        out = web_search.search("   ")
        assert out.startswith("검색 실패")

    def test_정상_검색은_결과를_포맷해_돌려준다(self):
        _inject_ddgs(MagicMock(return_value=[
            {"title": "제목", "body": "본문", "href": "https://x"},
        ]))
        out = web_search.search("파이썬")
        assert "제목" in out and "https://x" in out

    def test_네트워크_예외는_삼키고_실패_문자열을_돌려준다(self):
        # 예외를 올리면 대화가 끊긴다 — 반드시 문자열로 내려와야 한다.
        _inject_ddgs(MagicMock(side_effect=RuntimeError("rate limit")))
        out = web_search.search("파이썬")
        assert out.startswith("검색 실패")
        assert "rate limit" in out

    def test_ddgs_미설치도_예외가_아니라_안내_문자열(self):
        sys.modules["ddgs"] = None  # import 시 ImportError를 유발
        try:
            out = web_search.search("파이썬")
            assert out.startswith("검색 실패")
            assert "ddgs" in out
        finally:
            sys.modules.pop("ddgs", None)


def _inject_openai(responses_create):
    """가짜 openai 모듈 주입 — Responses API(검색 심부름꾼) 경로용."""
    client = MagicMock()
    client.responses.create = responses_create
    fake = types.ModuleType("openai")
    fake.OpenAI = MagicMock(return_value=client)
    sys.modules["openai"] = fake
    return client


class TestGptSearchAgent:
    """자격증명이 있으면 GPT 검색 심부름꾼을 먼저 쓰고, 못 쓰면 DuckDuckGo로 내려간다.

    순서가 중요하다: DDG는 스니펫만 줘서 날씨·시세 같은 수치 질문에 답을 못 만든다
    (2026-07-11 실측: claude가 DDG 결과로 두 번 시도하고 포기). GPT 심부름꾼은 같은
    속도에 본문까지 읽어 온다.
    """

    def test_자격증명이_있으면_gpt로_검색하고_ddg를_안_쓴다(self):
        _inject_openai(MagicMock(return_value=MagicMock(output_text="서울 34도, 강수 20%")))
        ddg = MagicMock(return_value=[{"title": "t", "body": "b", "href": "u"}])
        _inject_ddgs(ddg)

        out = web_search.search("내일 서울 날씨", api_key="k", base_url="https://gw")

        assert out == "서울 34도, 강수 20%"
        ddg.assert_not_called()

    def test_가벼운_모델로_검색해_지연을_막는다(self):
        # gpt-5-mini(추론 모델)는 같은 검색에 51~83초가 걸렸다. 검색은 사고가 아니다.
        create = MagicMock(return_value=MagicMock(output_text="결과"))
        _inject_openai(create)

        web_search.search("q", api_key="k", base_url="https://gw")

        kwargs = create.call_args.kwargs
        assert kwargs["model"] == web_search.SEARCH_AGENT_MODEL == "gpt-5.4-nano"
        assert kwargs["tools"] == [{"type": "web_search"}]

    def test_gpt가_실패하면_ddg_안전망으로_내려간다(self):
        # 계정에 nano 권한이 없거나(403) Responses가 막힌 경우.
        _inject_openai(MagicMock(side_effect=RuntimeError("403 no access")))
        _inject_ddgs(MagicMock(return_value=[
            {"title": "폴백제목", "body": "b", "href": "https://f"},
        ]))

        out = web_search.search("q", api_key="k", base_url="https://gw")

        assert "폴백제목" in out

    def test_gpt가_빈_답을_주면_ddg로_내려간다(self):
        # 검색은 됐는데 아무 말도 안 한 것 = 쓸모없음. 스니펫이라도 있는 게 낫다.
        _inject_openai(MagicMock(return_value=MagicMock(output_text="   ")))
        _inject_ddgs(MagicMock(return_value=[
            {"title": "폴백제목", "body": "b", "href": "https://f"},
        ]))

        out = web_search.search("q", api_key="k", base_url="https://gw")

        assert "폴백제목" in out


class TestToolSpec:
    def test_도구_스펙은_openai_function_형식이고_query를_요구한다(self):
        spec = web_search.SEARCH_TOOL_SPEC
        assert spec["type"] == "function"
        assert spec["function"]["name"] == "web_search"
        assert spec["function"]["parameters"]["required"] == ["query"]
