"""새 파이썬 파일 길이 검사 — 커밋 직전 검사(.claude/commit-gate.json)가 부른다.

- 기준 커밋(HEAD)에 없던 **새** .py 파일이 300줄을 넘으면 실패(종료 코드 1).
- 기존 파일 중 300줄 넘는 것은 「참고」로 목록만 찍는다(막지 않는다 — 버그 수정 커밋이 막히지 않게).
- HEAD가 없는 저장소(첫 커밋 전)면 모든 파일을 새 파일로 친다.
표준 라이브러리만 쓴다. 계획: docs/plan/done/20261116-시험과-검사.md
"""
import os
import subprocess
import sys

LIMIT = 300


def git(args, cwd):
    return subprocess.run(["git", "-c", "core.quotePath=false"] + args, cwd=cwd,
                          capture_output=True, text=True, encoding="utf-8")


def repo_root(start):
    r = git(["rev-parse", "--show-toplevel"], start)
    if r.returncode != 0:
        raise SystemExit("git 저장소가 아님: " + start)
    return r.stdout.strip()


def python_files(root):
    """추적 중 + 스테이징 + 추적 안 되는 새 파일 중 .py (.gitignore가 빼는 것은 제외)."""
    r = git(["ls-files", "--cached", "--others", "--exclude-standard"], root)
    files = sorted({f for f in r.stdout.splitlines() if f.endswith(".py")})
    return [f for f in files if os.path.isfile(os.path.join(root, f))]  # 지워진 파일 제외


def has_head(root):
    return git(["rev-parse", "--verify", "-q", "HEAD"], root).returncode == 0


def in_head(root, path):
    return git(["cat-file", "-e", "HEAD:" + path], root).returncode == 0


def count_lines(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return sum(1 for _ in f)


def check(root):
    """(새 파일 위반 목록, 기존 큰 파일 목록) — 각 항목은 (경로, 줄 수)."""
    head = has_head(root)
    new_over, old_big = [], []
    for rel in python_files(root):
        n = count_lines(os.path.join(root, rel))
        if n <= LIMIT:
            continue
        if head and in_head(root, rel):
            old_big.append((rel, n))
        else:
            new_over.append((rel, n))
    old_big.sort(key=lambda x: -x[1])
    return new_over, old_big


def main(argv=None):
    # 콘솔·훅 파이프가 cp949여도 한글·기호 출력으로 죽지 않게(죽으면 커밋이 엉뚱하게 막힌다)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    argv = sys.argv[1:] if argv is None else argv
    root = repo_root(argv[0] if argv else os.getcwd())
    new_over, old_big = check(root)
    if old_big:
        print(f"참고 — 큰 파일 {len(old_big)}개({LIMIT}줄 넘음, 막지 않음):")
        for rel, n in old_big:
            print(f"  {n:>5}  {rel}")
    if new_over:
        for rel, n in new_over:
            print(f"새 파일 {rel} {n}줄 — {LIMIT}줄 넘음")
        return 1
    print("길이 검사 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
