"""web_open의 URL 빌더 — 순수 함수라 테스트가 곧 규격이다."""

from urllib.parse import parse_qs, urlparse

from pasteflow import web_open


class TestGoogleAiUrl:
    def test_ai_mode_param(self):
        """udm=50이 빠지면 AI 답변이 아니라 평범한 검색 결과 목록이 뜬다."""
        qs = parse_qs(urlparse(web_open.google_ai_url("파이썬")).query)
        assert qs["udm"] == ["50"]

    def test_korean_and_spaces_encoded(self):
        url = web_open.google_ai_url("오늘 SK하이닉스 주가")
        assert " " not in url
        assert parse_qs(urlparse(url).query)["q"] == ["오늘 SK하이닉스 주가"]

    def test_special_chars_do_not_break_query(self):
        """&·=·#가 든 질문이 URL 파라미터를 깨뜨리지 않아야 한다."""
        q = "a&b=c#d 무엇?"
        assert parse_qs(urlparse(web_open.google_ai_url(q)).query)["q"] == [q]

    def test_strips_surrounding_whitespace(self):
        assert parse_qs(urlparse(web_open.google_ai_url("  주가  ")).query)["q"] == ["주가"]
