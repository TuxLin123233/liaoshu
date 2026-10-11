"""实测：flybrain 的放电信号能不能当"打字节奏的慢变量源"用。

要回答三个问题：
  1. 窗口化（>=500ms）之后的放电率，是不是一个有起伏的信号（有 CV），还是一条平线？
  2. 它有没有时间结构（lag-1 自相关），还是纯白噪声（窗口平均后会被抹平）？
  3. step() 跑的时候会不会占住 GIL（决定 UI 用线程还是子进程接它）。

输出 JSON 到 stdout，进度打到 stderr。
"""

import json
import os
import sys
import threading
import time

import numpy as np

DATA = os.environ.get("FLY_DATA", os.path.expanduser("~/fly-data"))
N_STEPS = int(os.environ.get("N_STEPS", "1500"))
WIN = 25                       # 25 步 * 20ms = 500ms 窗口


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def window_stats(x, win):
    k = len(x) // win
    w = x[: k * win].reshape(k, win).mean(axis=1)
    mean = float(w.mean())
    std = float(w.std())
    ac1 = float("nan")
    if std > 0 and len(w) > 2:
        a = w[:-1] - mean
        b = w[1:] - mean
        ac1 = float((a * b).sum() / np.sqrt((a * a).sum() * (b * b).sum()))
    return {
        "windows": int(k),
        "mean": round(mean, 2),
        "std": round(std, 2),
        "cv": round(std / mean, 4) if mean else None,
        "min": round(float(w.min()), 2),
        "max": round(float(w.max()), 2),
        "lag1_autocorr": round(ac1, 4),
        "series_head": [round(float(v), 1) for v in w[:24]],
    }


def raw_stats(x):
    mean = float(x.mean())
    std = float(x.std())
    return {"mean": round(mean, 2), "std": round(std, 2),
            "cv": round(std / mean, 4) if mean else None}


def gil_test(brain, n_probe_steps=40):
    """在一个小窗口里测：step() 期间纯 Python 线程能不能推进。"""
    stop = [False]
    counter = [0]

    def probe():
        while not stop[0]:
            counter[0] += 1

    th = threading.Thread(target=probe, daemon=True)
    th.start()

    t0 = time.time()
    time.sleep(0.5)
    base = counter[0] / (time.time() - t0)

    stop[0] = False
    c0 = counter[0]
    t0 = time.time()
    for _ in range(n_probe_steps):
        brain.step()
    dur = time.time() - t0
    during = (counter[0] - c0) / dur

    stop[0] = True
    th.join(timeout=1.0)
    ratio = during / base if base else 0.0
    return {
        "probe_per_s_baseline": int(base),
        "probe_per_s_during_step": int(during),
        "ratio": round(ratio, 3),
        "verdict": ("GIL 被占住 → 必须用子进程" if ratio < 0.15
                    else "GIL 有释放 → 可以用后台线程"),
    }


def main():
    log(f"加载大脑 data={DATA}")
    from flybrain import FlyBrain

    t0 = time.time()
    b = FlyBrain(data=DATA, device="cpu", sensory_input=False, seed=None)
    log(f"  加载完成 {time.time()-t0:.2f}s")
    b.reset(seed=None)

    dn_L = set(b.cells(["descending_neuron"], side="L").tolist())
    dn_R = set(b.cells(["descending_neuron"], side="R").tolist())
    steer_L = set(np.asarray(b.groups["steer_L"]).tolist())
    steer_R = set(np.asarray(b.groups["steer_R"]).tolist())

    out = {
        "data_dir": DATA, "n_steps": N_STEPS, "window_steps": WIN,
        "n_neurons": int(b.n),
        "sizes": {"descending_L": len(dn_L), "descending_R": len(dn_R),
                  "steer_L": len(steer_L), "steer_R": len(steer_R)},
    }

    log("第一次 step()（numba 编译）…")
    t0 = time.time()
    b.step()
    out["first_step_seconds"] = round(time.time() - t0, 2)
    log(f"  编译完成 {out['first_step_seconds']}s")

    log(f"跑 {N_STEPS} 步…")
    tot = np.zeros(N_STEPS)
    dnl = np.zeros(N_STEPS)
    dnr = np.zeros(N_STEPS)
    stl = np.zeros(N_STEPS)
    str_ = np.zeros(N_STEPS)
    t0 = time.time()
    for i in range(N_STEPS):
        s = set(np.asarray(b.step()).tolist())
        tot[i] = len(s)
        dnl[i] = len(s & dn_L)
        dnr[i] = len(s & dn_R)
        stl[i] = len(s & steer_L)
        str_[i] = len(s & steer_R)
        if i % 250 == 0 and i:
            log(f"  {i}/{N_STEPS}  {time.time()-t0:.1f}s")
    elapsed = time.time() - t0
    out["step_seconds"] = round(elapsed, 2)
    out["ms_per_step"] = round(elapsed / N_STEPS * 1000, 2)
    out["realtime_factor"] = round((N_STEPS * b.dt) / elapsed, 2)

    log("测 GIL…")
    out["gil"] = gil_test(b, 40)

    out["series"] = {
        "total_fired": {"raw": raw_stats(tot), "windowed": window_stats(tot, WIN)},
        "descending_L": {"raw": raw_stats(dnl), "windowed": window_stats(dnl, WIN)},
        "descending_R": {"raw": raw_stats(dnr), "windowed": window_stats(dnr, WIN)},
        "steer_L": {"raw": raw_stats(stl), "windowed": window_stats(stl, WIN)},
        "steer_R": {"raw": raw_stats(str_), "windowed": window_stats(str_, WIN)},
    }

    json.dump(out, sys.stdout, ensure_ascii=False, indent=2)
    print()
    log("完成")


if __name__ == "__main__":
    main()
