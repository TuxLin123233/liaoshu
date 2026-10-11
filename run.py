#!/usr/bin/env python3
"""聊书 · liaoshu 启动入口。

    python3 run.py samples/夜航.txt              # 网页界面（默认），自动开浏览器
    python3 run.py samples/夜航.txt --port 8770  # 指定端口
    python3 run.py samples/夜航.txt --no-open    # 不自动开浏览器，只打印地址
    python3 run.py samples/夜航.txt --mood fly   # 用果蝇连接组当节奏的慢变量
    python3 run.py samples/夜航.txt --ui tk      # 退回零依赖的 tkinter 界面
    python3 run.py --parse-only 某本书.txt        # 只看解析结果，不开界面
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    ap = argparse.ArgumentParser(description="群聊式 txt 电子书阅读器")
    ap.add_argument("book", nargs="?", help="txt 文件路径")
    ap.add_argument("--ui", choices=["web", "tk"], default="web",
                    help="web=网页界面（默认，套用 DeepSeek 设计令牌）；tk=零依赖 tkinter 界面")
    ap.add_argument("--port", type=int, default=8770, help="网页界面的端口（被占用会自动往后找）")
    ap.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    ap.add_argument("--mood", choices=["local", "fly", "hybrid"], default="local",
                    help="节奏的慢变量来源：local=本地 OU，fly=果蝇连接组，hybrid=两者相乘")
    ap.add_argument("--mood-cache", default=None, help="果蝇轨道缓存文件路径")
    ap.add_argument("--fly-seconds", type=int, default=300,
                    help="果蝇轨道生成多长（秒，按阅读时间算）")
    ap.add_argument("--speed", type=float, default=1.0, help="初始速度倍率")
    ap.add_argument("--parse-only", action="store_true", help="只打印解析结果，不开界面")
    args = ap.parse_args()

    if args.parse_only:
        from liaoshu.parser import parse_file
        if not args.book:
            ap.error("--parse-only 需要给一个 txt 文件")
        msgs = parse_file(args.book)
        from collections import Counter
        c = Counter(m.speaker for m in msgs if m.kind == "dialogue")
        print(f"{len(msgs)} 条消息 | 对话 {sum(1 for m in msgs if m.kind=='dialogue')} "
              f"| 旁白 {sum(1 for m in msgs if m.kind=='narration')} "
              f"| 章节 {sum(1 for m in msgs if m.kind=='chapter')} "
              f"| 说话人推断 {sum(1 for m in msgs if m.inferred)}")
        print("角色:", ", ".join(f"{k}×{v}" for k, v in c.most_common(20)))
        return 0

    if not args.book:
        if args.ui == "tk":
            args.book = None
        else:
            ap.error("请给一个 txt 文件，例如：python3 run.py samples/夜航.txt")

    if args.ui == "tk":
        from liaoshu import ui
        ui.run(args.book, mood_mode=args.mood, mood_cache=args.mood_cache,
               fly_seconds=args.fly_seconds, speed=args.speed)
        return 0

    if not os.path.isfile(args.book):
        ap.error(f"找不到文件：{args.book}")
    from liaoshu.server import serve
    serve(args.book, port=args.port, mood_mode=args.mood, speed=args.speed,
          fly_seconds=args.fly_seconds, mood_cache=args.mood_cache,
          open_browser=not args.no_open)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
