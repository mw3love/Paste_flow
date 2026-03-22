"""PasteFlow 실행 스크립트 — 에러 표시용"""
import traceback
import os
import sys

os.chdir(os.path.dirname(os.path.abspath(__file__)))

try:
    from pasteflow.main import main
    main()
except Exception as e:
    traceback.print_exc()
    input("\n오류 발생. Enter 키를 누르면 종료됩니다...")
