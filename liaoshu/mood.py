"""节奏的"慢变量"来源 —— 也就是"这个人现在是什么状态"。

打字节奏可以拆成两个时间尺度：

  微尺度（几十~几百毫秒，逐字）  用本地随机 + 结构化模型（见 rhythm.py）
  慢尺度（几秒~几分钟，情绪）    用这里的 MoodTrack

MoodTrack 只是一条时间序列：每 500ms 一个数，1.0 表示"正常速度"，
>1 表示这个人打得更快（兴奋/着急），<1 表示更慢（走神/犹豫）。

它有两种来源，接口完全一样，可以热插拔：
  - local：自己生成的平稳 OU 过程（瞬间可用，离线）
  - fly  ：flybrain 果蝇连接组的窗口化放电率（真实生物神经噪声，需要预热）
"""

from __future__ import annotations

import math
import os
import random
import subprocess
import sys
import threading
import time

SAMPLE_MS = 500.0          # 一个采样点代表 500ms
TAU_S = 45.0               # OU 过程的相关时间：约 45 秒换一次"心情"
DEPTH = 0.22               # 本地轨道的起伏深度（±22%）


def _ou_series(n: int, seed: int, dt_ms: float = SAMPLE_MS, tau_s: float = TAU_S) -> list[float]:
    rng = random.Random(seed)
    rho = math.exp(-(dt_ms / 1000.0) / tau_s)
    sigma = math.sqrt(1.0 - rho * rho)
    x = 0.0
    out = []
    for i in range(n):
        x = x * rho + sigma * rng.gauss(0.0, 1.0)
        # 再叠一层很慢的"呼吸"，让情绪有几个来回
        breathe = 0.35 * math.sin(2 * math.pi * (i * dt_ms / 1000.0) / 180.0)
        out.append(math.tanh(x + breathe))
    return out


class MoodTrack:
    """一条可无限延伸的心情轨道。

    两种存法，别混：
      absolute=False（本地 OU）：values 是 [-1,1] 的归一化值，取样时再乘 depth
      absolute=True （fly 轨道）：values 本身就是倍率（0.84 ~ 1.08），直接用
    混了的话轨道会整体偏移（第一版就踩了：fly 轨道被多乘了一次 depth，
    结果所有角色都莫名快 32%）。
    """

    def __init__(self, values: list[float], dt_ms: float = SAMPLE_MS,
                 source: str = "local", depth: float = DEPTH, seed: int = 0,
                 looping: bool = False, absolute: bool = False, note: str = ""):
        self.values = list(values) if values else [1.0]
        self.dt_ms = dt_ms
        self.source = source
        self.depth = depth
        self.seed = seed
        self.looping = looping
        self.absolute = absolute
        self.note = note
        self._gen_lock = threading.Lock()

    # ---- 取样 ----
    def at(self, t_ms: float) -> float:
        if not self.values:
            return 1.0
        idx = int(t_ms // self.dt_ms)
        if idx < 0:
            idx = 0
        if idx >= len(self.values):
            if self.looping:
                idx %= len(self.values)
            else:
                self.extend(idx + 1)
        v = self.values[idx]
        return v if self.absolute else 1.0 + self.depth * v

    def extend(self, n: int) -> None:
        """轨道不够长时就地延长（只有本地轨道能延长）。"""
        if self.source != "local":
            return
        with self._gen_lock:
            if n <= len(self.values):
                return
            self.values = _ou_series(n, self.seed, self.dt_ms)

    @property
    def stats(self) -> dict:
        v = self.values
        if len(v) < 2:
            return {"n": len(v), "cv": 0.0, "lo": 1.0, "hi": 1.0}
        mean = sum(v) / len(v)
        var = sum((x - mean) ** 2 for x in v) / len(v)
        lo, hi = (min(v), max(v)) if self.absolute else \
            (1.0 + self.depth * min(v), 1.0 + self.depth * max(v))
        return {
            "n": len(v),
            "cv": (math.sqrt(var) / abs(mean)) if mean else 0.0,
            "lo": lo,
            "hi": hi,
        }

    # ---- 构造 ----
    @classmethod
    def local(cls, seed: int = 0, n: int = 4096) -> "MoodTrack":
        return cls(_ou_series(n, seed), SAMPLE_MS, "local", DEPTH, seed)

    @classmethod
    def from_values(cls, values: list[float], source: str = "file",
                    dt_ms: float = SAMPLE_MS, looping: bool = True,
                    note: str = "") -> "MoodTrack":
        """载入外部轨道 —— 文件里的值已经是绝对倍率。"""
        return cls(values, dt_ms, source, DEPTH, 0, looping=looping,
                   absolute=True, note=note)


class MoodSource:
    """当前生效的心情轨道，支持运行中被替换（flybrain 预热完成后热切换）。"""

    def __init__(self, track: MoodTrack, label: str):
        self._lock = threading.Lock()
        self._track = track
        self._label = label
        self.rev = 0

    @property
    def track(self) -> MoodTrack:
        with self._lock:
            return self._track

    @property
    def label(self) -> str:
        with self._lock:
            return self._label

    def swap(self, track: MoodTrack, label: str) -> None:
        with self._lock:
            self._track = track
            self._label = label
            self.rev += 1

    def at(self, t_ms: float) -> float:
        return self.track.at(t_ms)


class BlendMood:
    """把几条慢变量相乘。比如 果蝇轨道 × 本地 OU，起伏更丰富。"""

    def __init__(self, *sources, label: str = "blend"):
        self.sources = sources
        self._label = label

    @property
    def label(self) -> str:
        return self._label

    def at(self, t_ms: float) -> float:
        v = 1.0
        for s in self.sources:
            v *= s.at(t_ms)
        return v


# --------------------------------------------------------------------------
# flybrain 轨道的生成（子进程，避免 numba 占住 GIL 卡住界面）
# --------------------------------------------------------------------------

FLY_SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "tools", "fly_mood.py")


def fly_python() -> str:
    """能找到的、装了 flybrain 的 python。"""
    cand = os.environ.get("FLYBRAIN_PYTHON")
    if cand and os.path.exists(cand):
        return cand
    guess = os.path.expanduser("~/.local/share/pipx/venvs/flybrain/bin/python")
    if os.path.exists(guess):
        return guess
    return sys.executable


def fly_available() -> bool:
    data = os.environ.get("FLY_DATA", os.path.expanduser("~/fly-data"))
    return (os.path.exists(os.path.join(data, "brain.npz"))
            and os.path.exists(FLY_SCRIPT))


def generate_fly_track(seconds: int = 300, cache: str | None = None,
                       log=print) -> MoodTrack | None:
    """跑一段果蝇大脑，把窗口化放电率变成心情轨道。"""
    if not fly_available():
        log("flybrain 不可用（缺数据或缺 tools/fly_mood.py）")
        return None
    py = fly_python()
    cmd = [py, FLY_SCRIPT, "--seconds", str(seconds)]
    if cache:
        cmd += ["--cache", cache]
    log(f"唤醒果蝇大脑：{os.path.basename(py)} {FLY_SCRIPT} --seconds {seconds}")
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=seconds * 2 + 600)
    except subprocess.TimeoutExpired:
        log("flybrain 生成超时")
        return None
    if proc.returncode != 0:
        log("flybrain 生成失败：\n" + (proc.stderr or "")[-800:])
        return None
    values: list[float] = []
    note = ""
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line.startswith("#"):
            note = line.lstrip("# ").strip()
            continue
        if not line:
            continue
        try:
            values.append(float(line))
        except ValueError:
            continue
    if len(values) < 4:
        log("flybrain 返回的轨道太短，丢弃")
        return None
    log(f"果蝇轨道就绪：{len(values)} 个采样点（{len(values)*SAMPLE_MS/1000:.0f} 秒），"
        f"耗时 {time.time()-t0:.1f}s")
    return MoodTrack.from_values(values, source="fly", looping=True, note=note)


def load_track_file(path: str) -> MoodTrack | None:
    try:
        note = ""
        vals = []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                if line.startswith("#"):
                    note = line.lstrip("# ").strip()
                    continue
                vals.append(float(line))
        if len(vals) >= 4:
            return MoodTrack.from_values(vals, source=os.path.basename(path), note=note)
    except (OSError, ValueError):
        pass
    return None
