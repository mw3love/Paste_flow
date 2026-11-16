"""pasteflow/blob_store.py 시험 — 해시 이름 저장·중복·읽기·지우기·청소."""
import os

from pasteflow.blob_store import BlobStore, digest


def test_put_get_roundtrip(tmp_path):
    s = BlobStore(str(tmp_path / "blobs"))
    h = s.put(b"\x89PNG abc")
    assert h == digest(b"\x89PNG abc")
    assert os.path.isfile(s.path(h))
    assert s.get(h) == b"\x89PNG abc"


def test_same_bytes_stored_once(tmp_path):
    s = BlobStore(str(tmp_path))
    assert s.put(b"same") == s.put(b"same")
    assert len([n for n in os.listdir(tmp_path) if n.endswith(".bin")]) == 1


def test_missing_file_returns_none(tmp_path):
    s = BlobStore(str(tmp_path))
    h = s.put(b"x")
    os.remove(s.path(h))
    assert s.get(h) is None


def test_remove_is_quiet_when_absent(tmp_path):
    s = BlobStore(str(tmp_path))
    s.remove("0" * 64)  # 없는 것도 예외 없이


def test_sweep_keeps_used_removes_orphans_and_tmp(tmp_path):
    s = BlobStore(str(tmp_path))
    used = s.put(b"used")
    orphan = s.put(b"orphan")
    (tmp_path / ".tmp-leftover").write_bytes(b"partial")
    removed = s.sweep([used])
    assert removed == 2
    assert os.path.isfile(s.path(used))
    assert not os.path.exists(s.path(orphan))
    assert not (tmp_path / ".tmp-leftover").exists()
