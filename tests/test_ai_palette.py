"""ai_palette의 사이트 목록 로드/저장·URL 빌더·키워드 매칭 — 순수 함수라 테스트가 곧 규격이다."""

from urllib.parse import parse_qs, urlparse

from pasteflow import ai_palette


class TestLoadSites:
    def test_empty_falls_back_to_default(self):
        sites = ai_palette.load_sites("")
        assert sites == ai_palette.DEFAULT_SITES

    def test_broken_json_falls_back_to_default(self):
        sites = ai_palette.load_sites("{not valid json")
        assert sites == ai_palette.DEFAULT_SITES

    def test_empty_list_falls_back_to_default(self):
        sites = ai_palette.load_sites("[]")
        assert sites == ai_palette.DEFAULT_SITES

    def test_parses_saved_list(self):
        raw = ai_palette.dump_sites([{"label": "테스트", "keyword": "t",
                                      "kind": "url", "url": "https://x.com?q={q}"}])
        sites = ai_palette.load_sites(raw)
        assert sites == [{"label": "테스트", "keyword": "t",
                           "kind": "url", "url": "https://x.com?q={q}"}]

    def test_default_is_not_mutated_by_caller(self):
        """load_sites()가 돌려준 리스트를 호출자가 수정해도 DEFAULT_SITES 원본은 안 바뀐다."""
        sites = ai_palette.load_sites("")
        sites[0]["label"] = "훼손됨"
        assert ai_palette.DEFAULT_SITES[0]["label"] != "훼손됨"


class TestBuildUrl:
    def test_placeholder_replaced(self):
        url = ai_palette.build_url("https://search.danawa.com/dsearch.php?query={q}", "그래픽카드")
        assert parse_qs(urlparse(url).query)["query"] == ["그래픽카드"]

    def test_strips_surrounding_whitespace(self):
        url = ai_palette.build_url("https://x.com?q={q}", "  hello  ")
        assert parse_qs(urlparse(url).query)["q"] == ["hello"]

    def test_special_chars_do_not_break_query(self):
        q = "a&b=c#d 무엇?"
        url = ai_palette.build_url("https://x.com?q={q}", q)
        assert parse_qs(urlparse(url).query)["q"] == [q]

    def test_no_placeholder_appends_query_param(self):
        url = ai_palette.build_url("https://x.com/search", "hello")
        assert parse_qs(urlparse(url).query)["q"] == ["hello"]

    def test_no_placeholder_with_existing_query_uses_ampersand(self):
        url = ai_palette.build_url("https://x.com/search?lang=ko", "hello")
        qs = parse_qs(urlparse(url).query)
        assert qs["lang"] == ["ko"]
        assert qs["q"] == ["hello"]


class TestMatchKeyword:
    def setup_method(self):
        self.sites = [
            {"label": "유튜브", "keyword": "yt", "kind": "url", "url": ""},
            {"label": "다나와", "keyword": "dw", "kind": "url", "url": ""},
        ]

    def test_matches_keyword_prefix(self):
        result = ai_palette.match_keyword(self.sites, "yt 고양이")
        assert result == (0, "고양이")

    def test_second_site_matches(self):
        result = ai_palette.match_keyword(self.sites, "dw 그래픽카드")
        assert result == (1, "그래픽카드")

    def test_no_match_without_trailing_space(self):
        """keyword 뒤에 공백이 없으면(예: "ytb") 매치하지 않는다 — 우연한 접두어 오검출 방지."""
        assert ai_palette.match_keyword(self.sites, "ytb 고양이") is None

    def test_no_match_returns_none(self):
        assert ai_palette.match_keyword(self.sites, "그냥 질문") is None

    def test_empty_keyword_never_matches(self):
        sites = [{"label": "x", "keyword": "", "kind": "url", "url": ""}]
        assert ai_palette.match_keyword(sites, " 아무거나") is None
