#!/usr/bin/env python
"""跑果蝇大脑，吐出一条"打字节奏的心情轨道"。

用法（必须用装了 flybrain 的 python 跑）：

    FLY_DATA=~/fly-data python tools/fly_mood.py --seconds 300

输出：每行一个浮点数，代表那 500ms 的节奏倍率（1.0 = 正常速度）。
第一行是 `#` 开头的注释，带着实测统计量——**这些数字很重要**：
如果 CV 很小、自相关接近 0，说明这个"心情"其实只是被放大过的白噪声，
那就没有必要为了它去跑一个 16 万神经元的网络（见 README 的结论）。
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys

import numpy as np

DT = 0.020  # flybrain 步长（秒），别改


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=300, help="生成多长的轨道（秒，按阅读时间算）")
    ap.add_argument("--window-steps", type=int, default=25, help="滑动窗口步数，25 步 = 500ms")
    ap.add_argument("--depth", type=float, default=None, help="起伏深度，默认按实测 CV 自适应")
    ap.add_argument("--cache", default=None, help="把结果写到这个文件")
    args = ap.parse_args()

    from flybrain import FlyBrain

    data = os.environ.get("FLY_DATA", os.path.expanduser("~/fly-data"))
    n_steps = int(args.seconds / DT)
    win = args.window_steps
    n_win = max(1, n_steps // win)

    brain = FlyBrain(data=data, device="cpu", sensory_input=False, seed=None)
    brain.reset(seed=None)

    counts = np.empty(n_win, dtype=np.float64)
    for w in range(n_win):
        acc = 0
        for _ in range(win):
            acc += len(brain.step())
        counts[w] = acc / win

    mean = float(counts.mean())
    std = float(counts.std())
    cv = std / mean if mean else 0.0
    if len(counts) > 2 and std > 0:
        a = counts[:-1] - mean
        b = counts[1:] - mean
        ac1 = float((a * b).sum() / np.sqrt((a * a).sum() * (b * b).sum()))
    else:
        ac1 = 0.0

    # 标准化成零均值单位方差，再压到 [-1,1]
    z = (counts - mean) / (std if std > 0 else 1.0)
    shaped = np.tanh(z / 2.0)

    # 深度按"真实起伏有多大"来定，避免拿噪声假装情绪
    depth = args.depth if args.depth is not None else max(0.05, min(0.32, cv * 6.0))

    note = (f"flybrain MaleCNS | window={win*DT*1000:.0f}ms | CV={cv*100:.2f}% "
            f"| lag1_autocorr={ac1:+.3f} | depth=±{depth*100:.0f}% "
            f"| samples={n_win} | mean_fired={mean:.0f}/step")

    lines = ["# " + note]
    vals = [1.0 + depth * float(v) for v in shaped]
    lines += [f"{v:.5f}" for v in vals]
    text = "\n".join(lines) + "\n"

    if args.cache:
        os.makedirs(os.path.dirname(os.path.abspath(args.cache)), exist_ok=True)
        with open(args.cache, "w") as f:
            f.write(text)
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
