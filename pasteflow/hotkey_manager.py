"""단축키 문자열의 특수 키 이름 → Windows VK 코드 표 (`_SPECIAL_KEY_MAP`).

모든 전역 단축키는 `paste_interceptor`의 WH_KEYBOARD_LL 훅이 감지하고, 이 표를 import해
쓴다(CLAUDE.md 규칙: 단일 정의). 옛 RegisterHotKey 기반 `HotkeyManager`는 등록하는 단축키가
하나도 남지 않아 2026-10-03 제거했다.
"""

# 일반 ord() 매핑이 안 되는 특수 키 → Windows VK 코드
_SPECIAL_KEY_MAP = {
    "space": 0x20,   # VK_SPACE
    "return": 0x0D,  # VK_RETURN
    "enter": 0x0D,
    "tab": 0x09,     # VK_TAB
    "backspace": 0x08, # VK_BACK
    "delete": 0x2E,  # VK_DELETE
    "home": 0x24,    # VK_HOME
    "end": 0x23,     # VK_END
    "pageup": 0x21,  # VK_PRIOR
    "pagedown": 0x22, # VK_NEXT
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73,
    "f5": 0x74, "f6": 0x75, "f7": 0x76, "f8": 0x77,
    "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
    "`": 0xC0,   # VK_OEM_3  (backtick/tilde)
    "~": 0xC0,
    "-": 0xBD,   # VK_OEM_MINUS
    "=": 0xBB,   # VK_OEM_PLUS
    "[": 0xDB,   # VK_OEM_4
    "]": 0xDD,   # VK_OEM_6
    "\\": 0xDC,  # VK_OEM_5
    ";": 0xBA,   # VK_OEM_1
    "'": 0xDE,   # VK_OEM_7
    ",": 0xBC,   # VK_OEM_COMMA
    ".": 0xBE,   # VK_OEM_PERIOD
    "/": 0xBF,   # VK_OEM_2
}
