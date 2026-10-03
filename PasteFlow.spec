# -*- mode: python ; coding: utf-8 -*-
import importlib.util, pathlib

def _get_version():
    spec = importlib.util.spec_from_file_location(
        "ver", pathlib.Path(SPECPATH) / "pasteflow" / "__version__.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.__version__

APP_VERSION = _get_version()


def _version_info(version_str):
    """'1.4.0' → 속성창에 표시할 VSVersionInfo. 4번째 자리는 0으로 패딩."""
    parts = [int(p) for p in version_str.split('.')][:4]
    while len(parts) < 4:
        parts.append(0)
    filevers = tuple(parts)
    from PyInstaller.utils.win32.versioninfo import (
        VSVersionInfo, FixedFileInfo, StringFileInfo, StringTable,
        StringStruct, VarFileInfo, VarStruct,
    )
    return VSVersionInfo(
        ffi=FixedFileInfo(filevers=filevers, prodvers=filevers),
        kids=[
            StringFileInfo([StringTable('040904B0', [
                StringStruct('CompanyName', 'PasteFlow'),
                StringStruct('FileDescription', 'PasteFlow — 순차 붙여넣기 클립보드 매니저'),
                StringStruct('FileVersion', version_str),
                StringStruct('InternalName', 'PasteFlow'),
                StringStruct('OriginalFilename', f'PasteFlow-{version_str}.exe'),
                StringStruct('ProductName', 'PasteFlow'),
                StringStruct('ProductVersion', version_str),
            ])]),
            VarFileInfo([VarStruct('Translation', [0x0409, 0x04B0])]),
        ],
    )


APP_ICON = str(pathlib.Path(SPECPATH) / 'pasteflow' / 'assets' / 'pasteflow.ico')

block_cipher = None

a = Analysis(
    ['run.pyw'],
    pathex=[],
    binaries=[],
    datas=[],
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
    icon=APP_ICON,
    version=_version_info(APP_VERSION),
)
