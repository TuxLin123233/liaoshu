#!/usr/bin/env python3
"""聊书 · liaoshu 一键启动器。

给桌面图标 / 文件管理器（`用聊书打开`）用的入口：

    python3 launcher.py 某本.txt          # 直接开这本
    python3 launcher.py                    # 没给书 → 弹系统选书窗口
    python3 launcher.py 某本.txt --ui tk   # 其余参数和 run.py 完全一样
    python3 launcher.py --ui native        # 弹选书 → pywebview 原生窗口打开

读取参数的行为和 run.py 保持一致，唯一差别是：**没给书时不报错**，
而是先弹一个选书对话框，选中了才开始。这是"桌面双击就能用"的前提。
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def _pick_file() -> str | None:
    """优先系统原生对话框（zenity / kdialog），没有就退回 tkinter。"""
    choosers = [
        ["zenity", "--file-selection", "--title=选一本 txt 电子书",
         "--file-filter", "txt 电子书 (*.txt)", "*.txt"],
        ["kdialog", "--getopenfilename", os.path.expanduser("~"),
         "*.txt|TXT 电子书"],
    ]
    for cmd in choosers:
        try:
            proc = subprocess.run(cmd, capture_output=True,
                                  text=True, timeout=60)
            path = (proc.stdout or "").strip()
            if path and os.path.isfile(path):
                return path
        except (OSError, subprocess.TimeoutExpired):
            continue
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        path = filedialog.askopenfilename(
            title="选一本 txt", filetypes=[("文本", "*.txt"), ("全部", "*.*")])
        root.destroy()
        return path or None
    except Exception:                            # noqa: BLE001
        return None


def main() -> int:
    ap = argparse.ArgumentParser(prog="launcher.py", description="群聊式 txt 电子书阅读器（选书启动）")
    ap.add_argument("book", nargs="?", help="txt 文件路径（不给就弹选书窗口）")
    # 打成 exe/AppImage 的"软件"默认直接开原生窗口；源码里跑还按老习惯走浏览器
    default_ui = "native" if getattr(sys, "frozen", False) else "web"
    ap.add_argument("--ui", choices=["web", "tk", "native"], default=default_ui,
                    help="web=网页界面；tk=零依赖 tkinter 界面；"
                         "native=pywebview 原生窗口（需 pip install pywebview）")
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--no-open", action="store_true")
    ap.add_argument("--mood", choices=["local", "fly", "hybrid"], default="local")
    ap.add_argument("--mood-cache", default=None)
    ap.add_argument("--fly-seconds", type=int, default=300)
    ap.add_argument("--speed", type=float, default=1.0)
    args = ap.parse_args()

    book = args.book
    if not book:
        book = _pick_file()
        if not book:
            print("没选到书。也可以直接命令行给：python3 launcher.py 某本.txt")
            return 1
    if not os.path.isfile(book):
        print(f"找不到文件：{book}")
        return 1

    if args.ui == "tk":
        from liaoshu import ui
        ui.run(book, mood_mode=args.mood, mood_cache=args.mood_cache,
               fly_seconds=args.fly_seconds, speed=args.speed)
        return 0

    if args.ui == "native":
        from liaoshu.desktop import run_native
        run_native(book, port=args.port, mood_mode=args.mood, speed=args.speed,
                   fly_seconds=args.fly_seconds, mood_cache=args.mood_cache)
        return 0

    from liaoshu.server import serve
    serve(book, port=args.port, mood_mode=args.mood, speed=args.speed,
          fly_seconds=args.fly_seconds, mood_cache=args.mood_cache,
          open_browser=not args.no_open)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())