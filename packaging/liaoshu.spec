# -*- mode: python ; coding: utf-8 -*-
"""聊书 liaoshu —— PyInstaller 打包配置。

Windows 打包（在 Windows 上跑，见 packaging/build_windows.bat）：

    pip install pywebview pyinstaller
    pyinstaller packaging/liaoshu.spec

Linux 打包只要去掉 console 相关即可（Qt/GTK 后端和 WebView2 无关）。

产物数据：
  - liaoshu/web/                       网页界面静态资源
  - tools/                             果蝇节奏源脚本（--mood fly 才用到）
生成后可执行文件：dist/liaoshu「.exe」
"""

from PyInstaller.utils.hooks import collect_data_files, collect_submodules
import os

datas = collect_data_files("liaoshu", include_py_files=False)
# tools/fly_mood.py 会被 --mood fly 当子进程脚本运行，得跟着走
datas += [("tools/fly_mood.py", "tools")]

hiddenimports = collect_submodules("webview")

# 有 .ico 就用它当图标（想换图标：把 packaging/liaoshu.ico 放进来即可）
_ICON = os.path.join("packaging", "liaoshu.ico")
icon = _ICON if os.path.exists(_ICON) else None

a = Analysis(
    ["launcher.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tests", "numba", "llvmlite", "scipy", "matplotlib", "notebook",
        "IPython", "pytest",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="liaoshu",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # 双击不弹黑框
    disable_windowed_traceback=False,
    icon=icon,
)