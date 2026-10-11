"""打字节奏模型 + 播放器。

设计上把节奏拆成三层，这是"真实感"的来源，而不是"随机数够不够随机"：

  1. 结构化基线：每个角色有自己的 ms/字（由他的台词统计推出来）
  2. 局部结构：词内连打会变快、换词会顿一下、标点后必然停顿、偶尔卡壳
  3. 两层噪声：快变量（~1 秒尺度的词组呼吸）+ 慢变量（~45 秒尺度的心情，见 mood.py）

为什么不能只用"每字一个随机数"：独立同分布的随机数在时间上是白的，
打字看起来会像均匀的雨点；真人打字是**成串**的（一个字接一个字地冒出来，
然后停一下）。所以噪声必须带自相关，B 层和 C 层的 AR(1) 就是干这个的。

播放器是**确定性重放**的：每条消息的时序由 `(消息序号, 角色, 文本)` 决定，
和"当前播到哪儿"无关。所以回退 = 把缓存丢掉重算，得到一模一样的结果，
不需要为撤销操作写任何补偿逻辑。
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from .mood import MoodSource
from .parser import Message
from .personas import NARRATOR, Persona, persona_for

PUNCT = set("，。！？；：、…—,.!?;:~～")
PAUSE_AFTER = {
    "、": 70, "，": 130, ",": 130, "；": 150, ";": 150, "：": 120, ":": 120,
    "。": 270, "！": 280, "!": 280, "？": 300, "?": 300, "…": 220, "—": 170,
    "~": 90, "～": 90,
}


@dataclass
class Planned:
    think_ms: float
    delays: list[float]
    end_t: float
    start_t: float = 0.0


class RhythmModel:
    def __init__(self, personas: dict[str, Persona], mood: MoodSource,
                 speed: float = 1.0, seed: int = 12345):
        self.personas = personas
        self.mood = mood
        self.speed = speed
        self.seed = seed

    # -- 快变量：~1.2 秒尺度的"词组呼吸"，让同一个人一阵快一阵慢 --
    @staticmethod
    def _fast_noise_series(n: int, rng: random.Random, tau_chars: float = 6.0) -> list[float]:
        rho = math.exp(-1.0 / tau_chars)
        sigma = math.sqrt(1.0 - rho * rho)
        x = 0.0
        out = []
        for _ in range(n):
            x = x * rho + sigma * rng.gauss(0.0, 1.0)
            out.append(math.exp(0.14 * x))
        return out

    def think_delay(self, msg: Message, prev: Message | None, rng: random.Random,
                    t_ms: float) -> float:
        p = persona_for(self.personas, msg.speaker)
        if msg.kind == "narration":
            base = 300.0
        elif msg.kind == "chapter":
            base = 900.0
        else:
            base = p.think_base / max(p.eagerness, 0.2)

        if prev is not None and msg.speaker is not None:
            if prev.speaker == msg.speaker:
                base *= 1.7                      # 刚发完又发，会慢一点
            if msg.speaker and msg.speaker in (prev.text or ""):
                base *= 0.30                     # 被点名了，秒回
        # 长消息要多打一会儿，开口前也更容易多酝酿一下
        base += min(len(msg.text) * 9.0, 800.0)
        base *= rng.uniform(0.55, 1.55)
        # 心情好/急的时候开口更快
        base /= max(self.mood.at(t_ms), 0.5)
        return max(60.0, min(base, 12000.0))

    def plan(self, idx: int, msg: Message, prev: Message | None, t_ms: float) -> Planned:
        p = persona_for(self.personas, msg.speaker)
        rng = random.Random((self.seed * 2654435761 + idx * 40503) % (2 ** 31))
        text = msg.text
        n = len(text)
        fast = self._fast_noise_series(n, rng)

        think = self.think_delay(msg, prev, rng, t_ms)
        t = t_ms + think

        delays: list[float] = []
        run_pos = 0
        word_len = rng.choice((2, 3, 3, 4))
        for i, ch in enumerate(text):
            d = p.ms_per_char

            if ch in PUNCT:
                d *= 1.45                        # 打标点本身要腾一下手
                run_pos = 0
                word_len = rng.choice((2, 3, 3, 4))
            else:
                if run_pos == 0:
                    d *= 1.22                    # 起一个新词，慢
                elif run_pos % word_len == 0:
                    d *= 1.12                    # 换词，略慢
                else:
                    d *= p.burstiness            # 词中间连打，快
                run_pos += 1

            if i > 0 and text[i - 1] in PAUSE_AFTER:
                d += PAUSE_AFTER[text[i - 1]] * p.pause_scale
            if ch == "\n":
                d += 380.0

            d *= math.exp(rng.gauss(0.0, p.jitter))     # 逐字抖动
            d *= fast[i]                                 # 词组呼吸
            d /= max(self.mood.at(t), 0.5)               # 心情（慢变量）

            if i > 0 and rng.random() < p.long_pause_rate:
                d += rng.uniform(180.0, 900.0)           # 偶尔卡壳

            d = max(8.0, min(d, 2500.0))
            delays.append(d)
            t += d

        t += rng.uniform(70.0, 260.0)                    # 敲回车
        return Planned(think, delays, t)


# --------------------------------------------------------------------------
# 播放器
# --------------------------------------------------------------------------

IDLE, THINKING, TYPING = "idle", "thinking", "typing"


@dataclass
class PlayerHooks:
    on_state: callable = lambda state, msg: None
    on_char: callable = lambda idx, n_shown: None
    on_sent: callable = lambda idx: None
    on_seek: callable = lambda delivered: None
    on_progress: callable = lambda delivered, total: None


class Player:
    """虚拟时钟驱动的播放器。UI 每 ~16ms 调一次 tick()。"""

    def __init__(self, messages: list[Message], model: RhythmModel, hooks: PlayerHooks):
        self.messages = messages
        self.model = model
        self.hooks = hooks
        self.speed = 1.0
        self.playing = False
        self.delivered = 0          # 已经完整送出的消息条数
        self.state = IDLE
        self.shown = 0              # 当前消息已显示的字数
        self._plans: dict[int, Planned] = {}
        self._pending = 0.0
        self._phase = "think"            # think -> type -> send
        # 返回 True 的消息会被"瞬间跳过"（不渲染、不占时间），
        # 但仍然占一个下标 —— 所以开关旁白不会打乱阅读位置。
        self.skip_predicate = None

    # ---- 时序缓存 ----
    def _ensure_plan(self, idx: int) -> Planned:
        """时序是稠密地从 0 号消息往前推的，所以缓存长度天然就是"下一条的序号"。"""
        i = len(self._plans)
        while i <= idx:
            start = self._plans[i - 1].end_t if i > 0 else 0.0
            prev = self.messages[i - 1] if i > 0 else None
            pl = self.model.plan(i, self.messages[i], prev, start)
            pl.start_t = start
            self._plans[i] = pl
            i += 1
        return self._plans[idx]

    def invalidate_from(self, idx: int) -> None:
        """把 idx 及以后的时序丢掉重算（过去的保持原样）。"""
        for k in [k for k in self._plans if k >= idx]:
            del self._plans[k]

    # ---- 传输控制 ----
    def play(self) -> None:
        if self.delivered >= len(self.messages):
            return
        self.playing = True
        if self.state in (THINKING, TYPING):
            # 暂停时停在哪就接着打哪，不要整条重来
            self.hooks.on_state(self.state, self.current_message)
            return
        self._enter_next()

    def model_time(self) -> float:
        """当前这一条开始的"模型时间"（毫秒），用来取心情。"""
        if not self._plans:
            return 0.0
        i = min(self.delivered, len(self._plans) - 1)
        return self._plans[i].start_t

    def message_time(self, idx: int) -> float:
        return self._ensure_plan(idx).start_t

    def pause(self) -> None:
        self.playing = False
        self.hooks.on_state("paused", self.current_message)

    @property
    def current_message(self) -> Message | None:
        return self.messages[self.delivered] if self.delivered < len(self.messages) else None

    def _enter_next(self) -> None:
        # 被隐藏的消息（比如关掉旁白）直接跳过去，不占时间也不渲染
        if self.skip_predicate is not None:
            moved = False
            while (self.delivered < len(self.messages)
                   and self.skip_predicate(self.messages[self.delivered])):
                self.delivered += 1
                moved = True
            if moved:
                self.hooks.on_progress(self.delivered, len(self.messages))
        msg = self.current_message
        if msg is None:
            self.playing = False
            self.state = IDLE
            self.hooks.on_state("done", None)
            return
        pl = self._ensure_plan(self.delivered)
        self.state = THINKING
        self._phase = "think"
        self.shown = 0
        self._pending = pl.think_ms / self.speed
        self.hooks.on_state(THINKING, msg)

    def skip_current(self) -> None:
        """把当前这条直接显示完。"""
        msg = self.current_message
        if msg is None:
            return
        self.shown = len(msg.text)
        self.hooks.on_char(self.delivered, self.shown)
        self._finish_current()

    def _finish_current(self) -> None:
        idx = self.delivered
        self.delivered += 1
        # 这条已经收尾了，进度要归零 —— 否则"暂停时跳过当前条"之后
        # 再按回退，会因为 shown>0 而误判成"这条正在打"，退错位置。
        self.shown = 0
        self._phase = "think"
        self.hooks.on_sent(idx)
        self.hooks.on_progress(self.delivered, len(self.messages))
        if self.playing:
            self._enter_next()
        else:
            self.state = IDLE

    def tick(self, dt_ms: float) -> None:
        if not self.playing:
            return
        if self.current_message is None:
            self.playing = False
            self.state = IDLE
            self.hooks.on_state("done", None)
            return

        self._pending -= dt_ms
        guard = 0
        while self._pending <= 0 and self.playing and guard < 4000:
            guard += 1
            msg = self.current_message
            if msg is None:
                self.playing = False
                self.state = IDLE
                self.hooks.on_state("done", None)
                return
            pl = self._ensure_plan(self.delivered)

            if self._phase == "think":
                self.state = TYPING
                self._phase = "type"
                self.shown = 0
                self.hooks.on_state(TYPING, msg)
                # 第一颗字在"想完"的那一刻出现，所以这里不额外等
                if not msg.text:
                    self._finish_current()
                continue

            if self._phase == "type":
                if self.shown < len(msg.text):
                    k = self.shown
                    self.shown += 1
                    self.hooks.on_char(self.delivered, self.shown)
                    if self.shown >= len(msg.text):
                        self._phase = "send"
                        self._pending += 90.0 / self.speed      # 敲回车
                    else:
                        self._pending += max(pl.delays[k], 1.0) / self.speed
                else:
                    self._phase = "send"
                continue

            # send：把这条正式发出去，进入下一条
            self._finish_current()
            if not self.playing:
                break

    # ---- 回退 / 跳转 ----
    def rewind(self, n: int = 1) -> None:
        """回退。

        正在打一条（已经显示了一部分）→ 撤掉这条，从它的开头重打；
        否则 → 退回到上一条的开头。
        这样连按可以一直往回退，符合直觉。
        """
        was_playing = self.playing
        self.playing = False
        if self.shown > 0:
            target = self.delivered
        else:
            target = max(0, self.delivered - n)
        self.delivered = target
        self.shown = 0
        self.state = IDLE
        self._phase = "think"
        self.hooks.on_seek(self.delivered)
        self.hooks.on_progress(self.delivered, len(self.messages))
        if was_playing:
            self.playing = True
            self._enter_next()

    def seek(self, delivered: int, resume: bool | None = None) -> None:
        """跳到"已送出 delivered 条"的位置，瞬间渲染，不重放动画。"""
        if resume is None:
            resume = self.playing
        self.playing = False
        self.delivered = max(0, min(delivered, len(self.messages)))
        self.shown = 0
        self.state = IDLE
        self._phase = "think"
        self.hooks.on_seek(self.delivered)
        self.hooks.on_progress(self.delivered, len(self.messages))
        if resume:
            self.playing = True
            self._enter_next()

    def set_speed(self, speed: float) -> None:
        """改速度只影响还没打的部分，已经显示的不动。

        时序缓存是用"模型时间"算的（不含速度），所以改速度不用重算缓存，
        只需要把当前这一拍剩余的时间按比例缩放，避免卡顿或跳字。
        """
        old = self.speed
        self.speed = max(0.1, min(speed, 20.0))
        if old > 0 and self._pending > 0:
            self._pending *= old / self.speed

    def set_mood(self, mood: MoodSource) -> None:
        self.model.mood = mood
        # 心情换了 → 将来的节奏重算；已经打出来的保持原样（刻意的，不然会跳字）
        self.invalidate_from(self.delivered)
        if self.playing:
            pl = self._ensure_plan(self.delivered)
            self._pending = max(pl.think_ms, 60.0) / self.speed
            self.state = THINKING
            self._phase = "think"
            self.shown = 0
            self.hooks.on_state(THINKING, self.current_message)
