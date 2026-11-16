"""tools/check_file_length.py 시험 — 임시 git 저장소에서 새 파일·기존 파일·HEAD 없음."""
import importlib.util
import os
import subprocess

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "check_file_length",
    os.path.join(os.path.dirname(__file__), "..", "tools", "check_file_length.py"))
cfl = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cfl)


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _write(path, n):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("x = 1\n" * n)


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    return tmp_path


def _commit_all(repo):
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "c")


def test_new_file_over_limit_fails(repo):
    _write(str(repo / "a.py"), 10)
    _commit_all(repo)
    _write(str(repo / "pkg" / "big.py"), 301)
    assert cfl.main([str(repo)]) == 1


def test_new_file_at_limit_passes(repo):
    _write(str(repo / "a.py"), 10)
    _commit_all(repo)
    _write(str(repo / "ok.py"), 300)
    assert cfl.main([str(repo)]) == 0


def test_existing_big_file_is_only_reported(repo, capsys):
    _write(str(repo / "old.py"), 3000)
    _commit_all(repo)
    _write(str(repo / "old.py"), 3010)  # 기존 파일이 커져도 막지 않는다
    assert cfl.main([str(repo)]) == 0
    out = capsys.readouterr().out
    assert "큰 파일 1개" in out and "old.py" in out


def test_no_head_treats_all_as_new(repo):
    _write(str(repo / "first.py"), 301)
    assert cfl.main([str(repo)]) == 1


def test_ignored_and_non_python_files_skipped(repo):
    _write(str(repo / "a.py"), 10)
    (repo / ".gitignore").write_text("build/\n", encoding="utf-8")
    _commit_all(repo)
    _write(str(repo / "build" / "gen.py"), 999)
    _write(str(repo / "notes.txt"), 999)
    assert cfl.main([str(repo)]) == 0
