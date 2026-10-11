#!/usr/bin/env python3
"""把 liaoshu 装成 Linux 桌面应用（点图标就开）。

    python3 tools/install_desktop.py             # 安装
    python3 tools/install_desktop.py --uninstall # 卸载

装到用户目录（~/.local/share/），不需要 root：
  - ~/.local/share/applications/liaoshu.desktop
  - ~/.local/share/icons/hicolor/scalable/apps/liaoshu.svg

装完应用菜单里应该出现「聊书」；也可以右键任意 .txt → 打开方式 → 聊书。
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAUNCHER = os.path.join(ROOT, "launcher.py")
ICON_SRC = os.path.join(ROOT, "tools", "liaoshu.svg")

HOME_SHARE = os.path.join(os.path.expanduser("~"), ".local", "share")
APPLICATIONS = os.path.join(HOME_SHARE, "applications")
DESKTOP_FILE = os.path.join(APPLICATIONS, "liaoshu.desktop")
ICON_FILE = os.path.join(HOME_SHARE, "icons", "hicolor",
                         "scalable", "apps", "liaoshu.svg")

DESKTOP_TEMPLATE = """[Desktop Entry]
Version=1.0
Type=Application
Name=聊书
Name[en]=liaoshu
GenericName=Group-chat e-book reader
Comment=把 txt 电子书变成群聊界面来读
Exec=python3 {launcher} %f
TryExec=python3
Path={root}
Icon=liaoshu
Terminal=false
Categories=Utility;Viewer;Education;
MimeType=text/plain;
Keywords=txt;ebook;book;reader;chat;小说;电子书;
StartupNotify=true
StartupWMClass=liaoshu
"""


def _refresh_caches() -> None:
    """让应用菜单 / 图标缓存立刻认到（没有这些工具就静默跳过）。"""
    for cmd in (
        ["update-desktop-database", APPLICATIONS],
        ["gtk-update-icon-cache", "-f", "-t",
         os.path.join(HOME_SHARE, "icons", "hicolor")],
    ):
        try:
            subprocess.run(cmd, capture_output=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            continue


def install() -> int:
    if not os.path.isfile(LAUNCHER):
        print(f"找不到 {LAUNCHER}，请在仓库根目录跑本脚本")
        return 1
    os.makedirs(APPLICATIONS, exist_ok=True)
    os.makedirs(os.path.dirname(ICON_FILE), exist_ok=True)

    with open(DESKTOP_FILE, "w", encoding="utf-8") as f:
        f.write(DESKTOP_TEMPLATE.format(launcher=LAUNCHER, root=ROOT))
    shutil.copyfile(ICON_SRC, ICON_FILE)

    try:
        os.chmod(DESKTOP_FILE, 0o644)
    except OSError:
        pass
    _refresh_caches()
    print("已装好：")
    print(f"  {DESKTOP_FILE}")
    print(f"  {ICON_FILE}")
    print()
    print("打开方式：应用菜单搜「聊书」；或右键 .txt → 打开方式 → 聊书。")
    return 0


def uninstall() -> int:
    removed = False
    for path in (DESKTOP_FILE, ICON_FILE):
        if os.path.exists(path):
            os.remove(path)
            removed = True
            print(f"已删除 {path}")
    _refresh_caches()
    if not removed:
        print("本来就没装过")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="安装/卸载 liaoshu 桌面启动器")
    ap.add_argument("--uninstall", action="store_true")
    args = ap.parse_args()
    return uninstall() if args.uninstall else install()


if __name__ == "__main__":
    raise SystemExit(main())