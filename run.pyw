"""PasteFlow 실행 스크립트 — CMD 창 없이 실행 (pythonw.exe)
에러 발생 시 logs/error.log 에 기록.
"""
import traceback
import os
import sys

os.chdir(os.path.dirname(os.path.abspath(__file__)))

try:
    from pasteflow.main import main
    main()
except Exception:
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "error.log")
    with open(log_path, "a", encoding="utf-8") as f:
        import datetime
        f.write(f"\n{'='*60}\n{datetime.datetime.now()}\n")
        f.write(traceback.format_exc())
