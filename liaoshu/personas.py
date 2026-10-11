"""从角色的台词里推断"这个人打字是什么手感"。

同一个人每次运行结果必须一样，所以随机性全部来自名字的哈希（crc32），
不用 Python 的 hash()（有盐，每次进程都不同）。
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass

from .parser import Message

# 深色主题下好看的头像底色
PALETTE = [
    "#5B8FF9", "#61DDAA", "#F6BD16", "#F08BB4", "#7262FD",
    "#78D3F8", "#9661BC", "#F6903D", "#008685", "#E8684A",
    "#6DC8EC", "#9270CA", "#FF9D4D", "#269A99", "#FF99C3",
]
NARRATOR_COLOR = "#8A8F98"
UNKNOWN_COLOR = "#7A7F88"


def _h(name: str) -> int:
    return zlib.crc32(name.encode("utf-8"))


def _unit(name: str, salt: str) -> float:
    """名字 + 盐 → [0,1) 的稳定伪随机数。"""
    return (_h(name + "\x00" + salt) % 100000) / 100000.0


@dataclass
class Persona:
    name: str
    color: str
    avatar: str
    ms_per_char: float = 105.0    # 打一个字的基准毫秒数
    jitter: float = 0.38          # 单字延迟的对数抖动
    think_base: float = 900.0     # 开口前的"思考"基准毫秒
    burstiness: float = 0.55      # 词组内越打越快的程度（越小越连珠炮）
    pause_scale: float = 1.0      # 标点停顿的倍率
    long_pause_rate: float = 0.012  # 中途卡壳的概率
    typo_rate: float = 0.0        # 打错字再改回来的概率（默认关，见 README）
    eagerness: float = 1.0        # 抢话程度，越大越快开口

    @property
    def cpm(self) -> float:
        return 60000.0 / self.ms_per_char


NARRATOR = Persona("旁白", NARRATOR_COLOR, "❖", ms_per_char=62.0, jitter=0.22,
                   think_base=420.0, burstiness=0.5, pause_scale=1.15,
                   long_pause_rate=0.0, eagerness=1.6)


def build_personas(messages: list[Message]) -> dict[str, Persona]:
    """统计每个角色的台词，生成他的打字手感。"""
    stats: dict[str, dict] = {}
    for m in messages:
        if m.kind != "dialogue" or not m.speaker:
            continue
        s = stats.setdefault(m.speaker, {"n": 0, "chars": 0, "excl": 0, "ellip": 0, "quest": 0})
        s["n"] += 1
        s["chars"] += len(m.text)
        s["excl"] += m.text.count("！") + m.text.count("!")
        s["ellip"] += m.text.count("…")
        s["quest"] += m.text.count("？") + m.text.count("?")

    personas: dict[str, Persona] = {}
    used_colors: set[str] = set()
    for i, (name, s) in enumerate(sorted(stats.items(), key=lambda kv: -kv[1]["n"])):
        avg_len = s["chars"] / max(s["n"], 1)
        excl_rate = s["excl"] / max(s["n"], 1)
        ellip_rate = s["ellip"] / max(s["n"], 1)

        # 颜色：按名字哈希取，撞了就顺延
        start = _h(name) % len(PALETTE)
        color = PALETTE[start]
        for k in range(len(PALETTE)):
            c = PALETTE[(start + k) % len(PALETTE)]
            if c not in used_colors:
                color = c
                break
        used_colors.add(color)

        # 基准速度：整体在 70~145 ms/字 之间，再按个人风格微调
        base = 78.0 + _unit(name, "speed") * 62.0
        # 话多且长句的人打字更快（不然一条消息要等太久）
        if avg_len > 18:
            base *= 0.86
        elif avg_len < 6:
            base *= 1.10
        # 感叹号多 → 手快；省略号多 → 犹犹豫豫
        base *= 1.0 - min(excl_rate, 1.0) * 0.16
        base *= 1.0 + min(ellip_rate, 1.0) * 0.20

        personas[name] = Persona(
            name=name,
            color=color,
            avatar=name[0] if name and name != "??" else "?",
            ms_per_char=round(base, 1),
            jitter=round(0.24 + _unit(name, "jit") * 0.26, 3),
            think_base=round(520.0 + _unit(name, "think") * 900.0, 1),
            burstiness=round(0.42 + _unit(name, "burst") * 0.30, 3),
            pause_scale=round(0.85 + _unit(name, "pause") * 0.4, 3),
            long_pause_rate=round(0.004 + _unit(name, "lp") * 0.018, 4),
            eagerness=round(0.7 + _unit(name, "eager") * 0.9, 3),
        )

    personas.setdefault("??", Persona("??", UNKNOWN_COLOR, "?", ms_per_char=120.0,
                                      jitter=0.4, think_base=1400.0, eagerness=0.6))
    return personas


def persona_for(personas: dict[str, Persona], name: str | None) -> Persona:
    if name is None:
        return NARRATOR
    return personas.get(name, NARRATOR)
