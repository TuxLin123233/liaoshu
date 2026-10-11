#!/usr/bin/env python3
"""聊书 · liaoshu 一键启动器。

给桌面图标 / 文件管理器（`用聊书打开`）用的入口：

    python3 launcher.py 某本.txt          # 直接开这本
    python3 launcher.py                    # 没给书 → 弹内置选书窗口
    python3 launcher.py 某本.txt --ui tk   # 其余参数和 run.py 完全一样
    python3 launcher.py --ui native        # 弹选书 → pywebview 原生窗口打开
    python3 launcher.py --picker native    # 选书优先用系统原生框（zenity/kdialog）

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


def _pick_builtin(recents: list[str]) -> str | None:
    """内置选择器：有最近打开就先摊出来，没有就直接文件对话框。"""
    if not recents:
        return _browse()
    try:
        import tkinter as tk
        from tkinter import ttk
    except Exception:                            # noqa: BLE001
        return _browse()

    root = tk.Tk()
    root.title("聊书 · 选一本")
    root.geometry("460x360")
    root.minsize(360, 260)
    chosen: dict[str, str | None] = {"path": None}

    tk.Label(root, text="最近打开", anchor="w",
             font=("", 10, "bold")).pack(fill="x", padx=12, pady=(12, 4))

    box = tk.Listbox(root, activestyle="none", exportselection=False)
    for p in recents:
        box.insert("end", f"{os.path.basename(p)}    —    {os.path.dirname(p)}")
    box.pack(fill="both", expand=True, padx=12)
    if recents:
        box.selection_set(0)

    def open_selected(_e=None):
        sel = box.curselection()
        if sel:
            chosen["path"] = recents[sel[0]]
            root.destroy()

    def browse():
        root.destroy()
        chosen["path"] = _browse()

    box.bind("<Double-Button-1>", open_selected)
    box.bind("<Return>", open_selected)

    bar = tk.Frame(root)
    bar.pack(fill="x", padx=12, pady=12)
    ttk.Button(bar, text="打开", command=open_selected).pack(side="right")
    ttk.Button(bar, text="浏览其他文件…", command=browse).pack(side="right", padx=(0, 8))
    ttk.Button(bar, text="取消", command=root.destroy).pack(side="right", padx=(0, 8))
    root.mainloop()
    return chosen["path"]


def _browse() -> str | None:
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


def _pick_file(style: str = "builtin") -> str | None:
    """内置选择器（tkinter）是默认；`native` 才先试系统原生框（zenity/kdialog）。"""
    from liaoshu.recent import list_recent
    recents = list_recent()
    if style == "native":
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
    return _pick_builtin(recents)


def _default_ui() -> str:
    """没指定 --ui 时的默认：装了 pywebview 且有显示 → 原生窗口；否则网页。"""
    if getattr(sys, "frozen", False):
        return "native"
    try:
        import webview  # noqa: F401
    except ImportError:
        return "web"
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return "web"
    return "native"


def main() -> int:
    ap = argparse.ArgumentParser(prog="launcher.py", description="群聊式 txt 电子书阅读器（选书启动）")
    ap.add_argument("book", nargs="?", help="txt 文件路径（不给就弹选书窗口）")
    # 装了原生窗口依赖就直接用它（点桌面图标的体验）；没有就退回浏览器
    default_ui = _default_ui()
    ap.add_argument("--ui", choices=["web", "tk", "native"], default=default_ui,
                    help="web=网页界面；tk=零依赖 tkinter 界面；"
                         "native=pywebview 原生窗口（需 pip install pywebview）")
    ap.add_argument("--picker", choices=["builtin", "native"], default="builtin",
                    help="选书用哪种文件选择器：builtin=内置（默认，跨平台一致）；"
                         "native=先试系统原生框（zenity/kdialog）再退回内置")
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--no-open", action="store_true")
    ap.add_argument("--mood", choices=["local", "fly", "hybrid"], default="local")
    ap.add_argument("--mood-cache", default=None)
    ap.add_argument("--fly-seconds", type=int, default=300)
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--rules", default=None,
                    help="用户过滤规则文件（默认自动读 ~/.config/liaoshu/rules.json，"
                         "Windows 为 %LOCALAPPDATA%\\liaoshu\\rules.json）")
    ap.add_argument("--rules-init", action="store_true",
                    help="生成一份示例规则文件并退出（想自己动过滤时用它）")
    args = ap.parse_args()

    if args.rules_init:
        from liaoshu.rules import init_rules_file
        print("已生成示例规则：", init_rules_file(args.rules))
        return 0

    rules = _load(args.rules)
    book = args.book
    if not book:
        book = _pick_file(args.picker)
        if not book:
            print("没选到书。也可以直接命令行给：python3 launcher.py 某本.txt")
            return 1
    if not os.path.isfile(book):
        print(f"找不到文件：{book}")
        return 1

    from liaoshu.recent import add as _remember
    _remember(book)

    if args.ui == "tk":
        from liaoshu import ui
        ui.run(book, mood_mode=args.mood, mood_cache=args.mood_cache,
               fly_seconds=args.fly_seconds, speed=args.speed, rules=rules)
        return 0

    if args.ui == "native":
        from liaoshu.desktop import run_native
        run_native(book, port=args.port, mood_mode=args.mood, speed=args.speed,
                   fly_seconds=args.fly_seconds, mood_cache=args.mood_cache,
                   rules=rules)
        return 0

    from liaoshu.server import serve
    serve(book, port=args.port, mood_mode=args.mood, speed=args.speed,
          fly_seconds=args.fly_seconds, mood_cache=args.mood_cache,
          open_browser=not args.no_open, rules=rules)
    return 0


def _load(rules_arg: str | None) -> dict:
    from liaoshu.rules import load_rules
    return load_rules(rules_arg)


if __name__ == "__main__":
    raise SystemExit(main())