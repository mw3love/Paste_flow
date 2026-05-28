"""DPAPI 기반 시크릿 보호 — 로컬 DB의 API 키 등을 현재 Windows 계정에 묶어 암호화.

전제: 같은 PC·같은 사용자만 복호화 가능 (CryptProtectData/CryptUnprotectData).
다중 PC 동기화는 settings.json 제거와 함께 폐기됐으므로 이 제약은 의도된 것.

저장 포맷: "enc:v1:<base64(blob)>"
빈값/이미 암호화된 값/평문(레거시)은 모두 안전하게 흘려보내 idempotent.
"""
from __future__ import annotations

import base64

_PREFIX = "enc:v1:"
_DESCRIPTION = "PasteFlow API Key"


def is_protected(value: str) -> bool:
    """문자열이 본 모듈로 암호화된 형태(enc:v1: prefix)인지."""
    return isinstance(value, str) and value.startswith(_PREFIX)


def protect(plain: str) -> str:
    """평문을 DPAPI로 암호화. 빈값·이미 암호화된 값은 그대로 반환(idempotent).

    실패 시 (예: pywin32 미설치) 평문 그대로 반환하고 경고를 출력 — 키를 잃는 것보다
    낫고, 다음 보호 호출 시 다시 시도된다.
    """
    if not plain:
        return ""
    if is_protected(plain):
        return plain
    try:
        import win32crypt  # pywin32 — 이미 의존성에 포함
        blob = win32crypt.CryptProtectData(
            plain.encode("utf-8"),
            _DESCRIPTION,
            None, None, None, 0,
        )
        return _PREFIX + base64.b64encode(blob).decode("ascii")
    except Exception as e:
        print(f"[crypto] protect 실패, 평문 유지: {e}")
        return plain


def unprotect(value: str) -> str:
    """암호화된 값을 평문으로. 평문(레거시)·빈값은 그대로 반환.

    복호화 실패(타 PC blob, 손상, pywin32 미설치)는 ""+경고로 흡수해 호출자가
    크래시하지 않게 한다 — 시크릿이 비어 보일 뿐이고, 사용자는 설정창에서 재입력 가능.
    """
    if not value:
        return ""
    if not is_protected(value):
        return value  # 평문 passthrough — 마이그레이션 전 코드 경로 안전
    try:
        import win32crypt
        b64 = value[len(_PREFIX):]
        blob = base64.b64decode(b64)
        _desc, plain_bytes = win32crypt.CryptUnprotectData(blob, None, None, None, 0)
        return plain_bytes.decode("utf-8")
    except Exception as e:
        print(f"[crypto] unprotect 실패 (재입력 필요): {e}")
        return ""
