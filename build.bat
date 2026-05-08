@echo off
chcp 65001 >nul 2>nul
cd /d "%~dp0"
python -m PyInstaller PasteFlow.spec
pause
