"""crypto.py 라운드트립·idempotent·안전성 테스트."""
import pytest

from pasteflow.crypto import protect, unprotect, is_protected, _PREFIX


def test_empty_passthrough():
    assert protect("") == ""
    assert unprotect("") == ""
    assert not is_protected("")


def test_roundtrip_ascii():
    plain = "dummy-secret-not-a-real-key"
    enc = protect(plain)
    assert is_protected(enc)
    assert enc != plain
    assert unprotect(enc) == plain


def test_roundtrip_unicode():
    plain = "한글-토큰-🔑-mixed-123"
    enc = protect(plain)
    assert is_protected(enc)
    assert unprotect(enc) == plain


def test_double_protect_idempotent():
    """이미 암호화된 값을 다시 protect해도 같은 값을 반환해야 한다(prefix·blob 불변)."""
    enc = protect("secret")
    assert protect(enc) == enc


def test_unprotect_plain_passthrough():
    """평문(레거시 DB 데이터)을 unprotect하면 그대로 돌려준다 — 마이그레이션 전 안전."""
    assert unprotect("plain-legacy-value") == "plain-legacy-value"


def test_unprotect_corrupt_blob_returns_empty():
    """깨진 enc:v1: 값은 예외 대신 ""를 반환 — 호출자 크래시 방지."""
    assert unprotect(_PREFIX + "!!!not-base64!!!") == ""
    assert unprotect(_PREFIX + "YWJj") == ""  # 유효 base64지만 DPAPI blob 아님


def test_is_protected_prefix_only():
    assert is_protected(_PREFIX + "x")
    assert not is_protected("plain")
    assert not is_protected("enc:v0:x")  # 다른 버전
