# -*- mode: python ; coding: utf-8 -*-
import importlib.util, pathlib
from PyInstaller.utils.hooks import collect_all, collect_submodules

def _get_version():
    spec = importlib.util.spec_from_file_location(
        "ver", pathlib.Path(SPECPATH) / "pasteflow" / "__version__.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.__version__

APP_VERSION = _get_version()

block_cipher = None

# winocr/winrt 의존성 수집 — 빌드 환경에 설치되지 않은 경우 빈 리스트
try:
    winocr_datas, winocr_binaries, winocr_hiddenimports = collect_all('winocr')
except Exception:
    winocr_datas, winocr_binaries, winocr_hiddenimports = [], [], []

try:
    winrt_hiddenimports = collect_submodules('winrt')
except Exception:
    winrt_hiddenimports = []

a = Analysis(
    ['run.pyw'],
    pathex=[],
    binaries=winocr_binaries,
    datas=winocr_datas,
    hiddenimports=[
        # pywin32
        'win32api',
        'win32con',
        'win32clipboard',
        'win32gui',
        'win32process',
        'win32event',
        'pywintypes',
        # PyQt6 플러그인
        'PyQt6.QtCore',
        'PyQt6.QtGui',
        'PyQt6.QtWidgets',
        # PIL
        'PIL.Image',
        'PIL.ImageQt',
        # 표준 라이브러리
        'sqlite3',
        'ctypes',
        'ctypes.wintypes',
        # winocr / winrt (OCR 기능)
        *winocr_hiddenimports,
        *winrt_hiddenimports,
        'winrt.windows.media.ocr',
        'winrt.windows.globalization',
        'winrt.windows.graphics.imaging',
        'winrt.windows.storage.streams',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'matplotlib',
        'numpy',
        'scipy',
        'pandas',
        'IPython',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name=f'PasteFlow-{APP_VERSION}',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,       # 콘솔 창 숨김
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
