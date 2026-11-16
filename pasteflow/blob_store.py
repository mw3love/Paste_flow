"""이미지 원본 파일 저장소 — DB 밖 `blobs` 폴더에 `<sha256>.bin`으로 둔다.

DB(`database.py`)에는 해시만 남기고 원본 바이트는 여기 둔다. 같은 그림은 한 번만 저장된다.
이미지 원본을 DB BLOB에 넣었을 때 DB가 수백 MB로 부풀고 목록 조회가 느려졌던 문제
(database.py의 mmap·자동 압축 주석)를 뿌리에서 없애려는 것이다.
계획: docs/plan/done/20261116-이미지-파일-저장.md
"""
import hashlib
import os
import tempfile
from typing import Iterable, Optional

_SUFFIX = ".bin"
_TMP_PREFIX = ".tmp-"


def digest(data: bytes) -> str:
    """원본 바이트의 sha256 해시(16진 64자)."""
    return hashlib.sha256(data).hexdigest()


class BlobStore:
    def __init__(self, folder: str):
        self.folder = folder
        os.makedirs(folder, exist_ok=True)

    def path(self, h: str) -> str:
        return os.path.join(self.folder, h + _SUFFIX)

    def put(self, data: bytes) -> str:
        """저장하고 해시를 돌려준다. 같은 파일이 이미 있으면 다시 쓰지 않는다.
        임시 파일에 쓴 뒤 이름을 바꿔, 쓰다 끊겨도 반쪽 파일이 남지 않게 한다."""
        h = digest(data)
        final = self.path(h)
        if os.path.exists(final):
            return h
        fd, tmp = tempfile.mkstemp(prefix=_TMP_PREFIX, dir=self.folder)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, final)
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
        return h

    def get(self, h: str) -> Optional[bytes]:
        """해시의 원본 바이트. 파일이 없거나 못 읽으면 None(앱이 죽지 않게)."""
        try:
            with open(self.path(h), "rb") as f:
                return f.read()
        except OSError as e:
            print(f"[Blob] 원본 파일 없음/읽기 실패 — {h[:12]}…: {e}")
            return None

    def remove(self, h: str) -> None:
        try:
            os.remove(self.path(h))
        except FileNotFoundError:
            pass
        except OSError as e:
            print(f"[Blob] 지우기 실패 — {h[:12]}…: {e}")

    def sweep(self, keep: Iterable[str]) -> int:
        """keep에 없는 원본 파일과 임시 파일 찌꺼기를 지우고 지운 개수를 돌려준다."""
        keep_set = set(keep)
        removed = 0
        try:
            names = os.listdir(self.folder)
        except OSError:
            return 0
        for name in names:
            full = os.path.join(self.folder, name)
            if not os.path.isfile(full):
                continue
            stale_tmp = name.startswith(_TMP_PREFIX)
            orphan = name.endswith(_SUFFIX) and name[: -len(_SUFFIX)] not in keep_set
            if stale_tmp or orphan:
                try:
                    os.remove(full)
                    removed += 1
                except OSError as e:
                    print(f"[Blob] 청소 실패 — {name}: {e}")
        return removed
