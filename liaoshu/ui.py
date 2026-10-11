"""群聊式电子书阅读器 —— tkinter 界面。

界面只有三块：工具条 / 消息流 / 状态栏。
消息流是一个 Canvas 套 Frame 的可滚动区域，每条消息一个 widget 行：
  - 对话：彩色头像 + 名字 + 气泡（左对齐，跟真实群聊一样）
  - 旁白：居中的灰色小字，像系统提示
  - 章节：居中带横线的分隔
"""

from __future__ import annotations

import os
import queue
import re
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog

from .mood import (BlendMood, MoodSource, MoodTrack, fly_available,
                   generate_fly_track, load_track_file)
from .parser import Message, parse_file
from .personas import NARRATOR, build_personas, persona_for
from .rhythm import Player, PlayerHooks, RhythmModel, THINKING, TYPING

BG = "#1E1F22"
BG_RAISED = "#2B2D31"
BG_HOVER = "#35373C"
FG = "#E6E6E6"
FG_DIM = "#8A8F98"
ACCENT = "#5B8FF9"
BORDER = "#3A3D42"

MAX_ROWS = 220          # 消息流里最多保留多少个 widget 行
TICK_MS = 16


def pick_font() -> str:
    fams = set(tkfont.families())
    for f in ("Noto Sans CJK SC", "Source Han Sans SC", "Noto Sans SC",
              "WenQuanYi Micro Hei", "WenQuanYi Zen Hei", "Microsoft YaHei",
              "SimHei", "DejaVu Sans"):
        if f in fams:
            return f
    return "TkDefaultFont"


def _short_note(note: str) -> str:
    """把轨道文件头里的实测统计量压成一小段，显示在状态栏上。

    这不是装饰：它让"果蝇轨道到底有多少信息"一直摆在眼前，
    省得自己骗自己（实测 CV 2.75%、自相关 −0.05 ≈ 白噪声）。
    """
    m = re.search(r"CV=([\d.]+)%.*?lag1_autocorr=([+-][\d.]+)", note or "")
    return f"· CV {m.group(1)}% 自相关 {m.group(2)}" if m else ""


class ChatBookApp:
    def __init__(self, root: tk.Tk, path: str | None = None,
                 mood_mode: str = "local", mood_cache: str | None = None,
                 fly_seconds: int = 300, speed: float = 1.0, rules=None):
        self.root = root
        self.font = pick_font()
        self.init_speed = speed
        self.mood_mode = mood_mode
        self._rules = rules
        self.mood_cache = mood_cache or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "cache", "fly_mood.txt")
        self.fly_seconds = fly_seconds

        self.messages: list[Message] = []
        self.personas = {}
        self.show_narration = True
        self._rows: dict[int, tk.Frame] = {}      # 消息下标 -> widget 行
        self._bubble_labels: list[tk.Label] = []
        self._typing_row: tk.Frame | None = None
        self._typing_label: tk.Label | None = None
        self._typing_dots = 0
        self._typing_t0 = 0.0
        self._autoscroll = True
        self._updating_scale = False
        self._scrubbing = False
        self._last_tick = time.perf_counter()
        self._last_mood_label = ""
        self._fly_thread: threading.Thread | None = None
        # Tk 不是线程安全的：后台线程只能往这个队列里丢活，由主线程的 _tick 执行
        self._ui_queue: queue.Queue = queue.Queue()
        self._sticky_status = False
        self.mood_detail = ""

        # 心情轨道：先用本地 OU，保证秒开；果蝇轨道后台预热完再热切换
        self.mood_local = MoodSource(MoodTrack.local(seed=20241011), "本地 OU")
        self.mood_fly: MoodSource | None = None
        self.mood = self.mood_local

        self._build_ui()
        if path:
            self.load(path)
        else:
            self._set_status("打开一个 txt 文件开始（左上角「打开 txt」）")
        # 启动参数要求果蝇轨道 → 这里就去预热（有缓存的话是瞬间完成）
        if self.mood_mode in ("fly", "hybrid"):
            self._start_fly_prewarm()
        self.root.after(TICK_MS, self._tick)

    # ------------------------------------------------------------------
    # 界面骨架
    # ------------------------------------------------------------------
    def _btn(self, parent, text, cmd, width=None, fg=FG, bg=BG_RAISED):
        b = tk.Button(parent, text=text, command=cmd, bg=bg, fg=fg,
                      activebackground=BG_HOVER, activeforeground=FG,
                      relief="flat", bd=0, padx=10, pady=4,
                      font=(self.font, 10), cursor="hand2",
                      highlightthickness=0, takefocus=0)
        if width:
            b.configure(width=width)
        b.pack(side="left", padx=5, pady=6)
        return b

    def _build_ui(self):
        self.root.title("liaoshu · 群聊式电子书")
        self.root.configure(bg=BG)
        self.root.geometry("980x720")
        self.root.minsize(640, 420)

        # ---- 工具条 ----
        bar = tk.Frame(self.root, bg=BG)
        bar.pack(fill="x", side="top")
        self._btn(bar, "打开 txt", self.open_dialog)
        self.btn_play = self._btn(bar, "▶  播放", self.toggle_play, width=9, bg=ACCENT, fg="#0B0B0C")
        self._btn(bar, "⏪ 回退", self.do_rewind)
        self._btn(bar, "⏭ 跳过", self.do_skip)
        self._btn(bar, "⏮ 重来", self.do_restart)

        tk.Label(bar, text="速度", bg=BG, fg=FG_DIM,
                 font=(self.font, 10)).pack(side="left", padx=(14, 4))
        self.speed_var = tk.DoubleVar(value=self.init_speed)
        # 下限给到 0.1×：默认速度对真人来说是压缩过的（不然读一本书要等到天荒地老），
        # 0.1× 时 102ms/字 → 1020ms/字 ≈ 59 字/分，正好是真人打中文的速度。
        sp = tk.Scale(bar, from_=0.1, to=6.0, resolution=0.05, orient="horizontal",
                      variable=self.speed_var, command=self.on_speed,
                      bg=BG, fg=FG, troughcolor="#3A3D42", highlightthickness=0,
                      bd=0, length=132, showvalue=False, font=(self.font, 9),
                      activebackground=ACCENT, takefocus=0, sliderrelief="flat",
                      sliderlength=18, width=12)
        sp.pack(side="left")
        self.speed_label = tk.Label(bar, text=f"{self.init_speed:g}×", bg=BG, fg=FG,
                                    font=(self.font, 9), width=6, anchor="w")
        self.speed_label.pack(side="left", padx=(4, 0))

        self.narr_var = tk.BooleanVar(value=True)
        tk.Checkbutton(bar, text="旁白", variable=self.narr_var, command=self.on_toggle_narration,
                       bg=BG, fg=FG, selectcolor=BG_RAISED, activebackground=BG,
                       activeforeground=FG, font=(self.font, 10),
                       highlightthickness=0, bd=0).pack(side="left", padx=(16, 2))

        tk.Label(bar, text="节奏源", bg=BG, fg=FG_DIM,
                 font=(self.font, 10)).pack(side="left", padx=(16, 4))
        self.mood_var = tk.StringVar(value={"local": "本地 OU", "fly": "果蝇",
                                            "hybrid": "果蝇×本地"}[self.mood_mode])
        om = tk.OptionMenu(bar, self.mood_var, "本地 OU", "果蝇", "果蝇×本地",
                           command=self.on_mood_change)
        om.configure(bg=BG_RAISED, fg=FG, activebackground=BG_HOVER, relief="flat",
                     highlightthickness=0, bd=0, font=(self.font, 10), width=10)
        om["menu"].configure(bg=BG_RAISED, fg=FG, font=(self.font, 10))
        om.pack(side="left", padx=3)

        tk.Frame(self.root, bg=BORDER, height=1).pack(fill="x")

        # ---- 消息流 ----
        mid = tk.Frame(self.root, bg=BG)
        mid.pack(fill="both", expand=True)

        self.canvas = tk.Canvas(mid, bg=BG, highlightthickness=0, bd=0)
        self.vsb = tk.Scrollbar(mid, orient="vertical", command=self.canvas.yview,
                                bg=BG_RAISED, troughcolor=BG, bd=0,
                                activebackground=BG_HOVER, highlightthickness=0)
        self.canvas.configure(yscrollcommand=self.vsb.set)
        self.vsb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        self.inner = tk.Frame(self.canvas, bg=BG)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>",
                        lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", self._on_canvas_resize)

        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.canvas.bind_all(seq, self._on_wheel)

        # ---- 底部 ----
        tk.Frame(self.root, bg=BORDER, height=1).pack(fill="x")
        bottom = tk.Frame(self.root, bg=BG)
        bottom.pack(fill="x", side="bottom")
        self.status = tk.Label(bottom, text="", bg=BG, fg=FG_DIM, anchor="w",
                               font=(self.font, 9))
        self.status.pack(side="left", padx=10, pady=4)

        self.scale_var = tk.DoubleVar(value=0.0)
        tk.Label(bottom, text="进度", bg=BG, fg=FG_DIM,
                 font=(self.font, 9)).pack(side="right", padx=(6, 2))
        self.scale = tk.Scale(bottom, from_=0, to=1, orient="horizontal",
                              variable=self.scale_var, showvalue=False,
                              command=self._on_scrub, bg=BG, fg=FG,
                              troughcolor="#3A3D42", highlightthickness=0, bd=0,
                              activebackground=ACCENT, length=360, takefocus=0,
                              sliderrelief="flat", sliderlength=20, width=12)
        self.scale.pack(side="right", padx=4, pady=2)
        self.scale.bind("<ButtonPress-1>", lambda e: setattr(self, "_scrubbing", True))
        self.scale.bind("<ButtonRelease-1>", self._on_scrub_release)

        for key, fn in (("<space>", lambda e: self.toggle_play()),
                        ("<Left>", lambda e: self.do_rewind()),
                        ("<Right>", lambda e: self.do_skip()),
                        ("<r>", lambda e: self.do_restart())):
            self.root.bind(key, lambda e, f=fn: (f(), "break")[1])

    # ------------------------------------------------------------------
    # 载入
    # ------------------------------------------------------------------
    def open_dialog(self):
        p = filedialog.askopenfilename(
            title="选一本 txt", filetypes=[("文本", "*.txt"), ("全部", "*.*")])
        if p:
            self.load(p)

    def load(self, path: str):
        try:
            msgs = parse_file(path, rules=self._rules)
        except Exception as e:                       # noqa: BLE001
            self._set_status(f"读取失败：{e}")
            return
        if not msgs:
            self._set_status("这个文件里没解析出内容")
            return

        self.path = path
        self.messages = msgs
        self.personas = build_personas(msgs)
        self.root.title(f"liaoshu · {os.path.basename(path)}")

        if self.mood_fly is not None or self.mood_mode != "local":
            self.mood = self._compose_mood()
        self.model = RhythmModel(self.personas, self.mood, seed=12345)
        self.player = Player(msgs, self.model, PlayerHooks(
            on_state=self.on_state, on_char=self.on_char, on_sent=self.on_sent,
            on_seek=self.on_seek, on_progress=self.on_progress))
        self.player.skip_predicate = (
            None if self.show_narration else (lambda m: m.kind == "narration"))
        self.player.set_speed(float(self.speed_var.get()))

        self._reset_view()
        n_dlg = sum(1 for m in msgs if m.kind == "dialogue")
        cast = len([k for k in self.personas if k != "??"])
        self._set_status(
            f"{os.path.basename(path)} · {len(msgs)} 条消息（对话 {n_dlg}）· "
            f"{cast} 个角色 · 空格暂停/继续，← 回退，→ 跳过")
        self.player.play()

    def _reset_view(self):
        for w in self.inner.winfo_children():
            w.destroy()
        self._rows.clear()
        self._bubble_labels.clear()
        self._typing_row = None
        self._typing_label = None

    # ------------------------------------------------------------------
    # 气泡渲染
    # ------------------------------------------------------------------
    def _wrap(self) -> int:
        w = self.canvas.winfo_width()
        return max(240, int(w * 0.62))

    def _fake_clock(self, t_ms: float) -> str:
        total = int(20 * 3600 + t_ms / 1000.0)       # 从 20:00 开始读
        return f"{total // 3600 % 24:02d}:{total // 60 % 60:02d}"

    def _make_row(self, idx: int, msg: Message) -> tk.Frame:
        row = tk.Frame(self.inner, bg=BG)
        row.pack(fill="x", padx=14, pady=2, anchor="w")

        if msg.kind in ("narration", "chapter"):
            txt = msg.text
            lbl = tk.Label(row, text=txt, bg=BG,
                           fg=FG_DIM if msg.kind == "narration" else "#B9BEC7",
                           font=(self.font, 10, "italic" if msg.kind == "narration" else "bold"),
                           justify="center", wraplength=self._wrap(), pady=4)
            lbl.pack(anchor="center", fill="x")
            self._bubble_labels.append(lbl)
            if msg.kind == "chapter":
                tk.Frame(row, bg=BORDER, height=1).pack(fill="x", pady=(0, 4))
            self._rows[idx] = row
            return row

        p = persona_for(self.personas, msg.speaker)
        tk.Label(row, text=p.avatar, bg=p.color, fg="#101114", width=2, height=1,
                 font=(self.font, 13, "bold")).pack(side="left", anchor="n", padx=(0, 8))

        box = tk.Frame(row, bg=BG)
        box.pack(side="left", fill="x", expand=True, anchor="w")

        head = tk.Frame(box, bg=BG)
        head.pack(anchor="w", fill="x")
        tk.Label(head, text=str(msg.speaker) + ("?" if msg.confidence == "guess" else ""),
                 bg=BG, fg=p.color, font=(self.font, 9)).pack(side="left")
        try:
            tstr = self._fake_clock(self.player.message_time(idx) if idx <= self.player.delivered else 0)
        except Exception:                            # noqa: BLE001
            tstr = ""
        tk.Label(head, text="  " + tstr, bg=BG, fg="#5C6169",
                 font=(self.font, 8)).pack(side="left")

        bubble = tk.Label(box, text="", bg=BG_RAISED, fg=FG, font=(self.font, 11),
                          justify="left", anchor="w", wraplength=self._wrap(),
                          padx=10, pady=6)
        bubble.pack(anchor="w", pady=(1, 0))
        self._bubble_labels.append(bubble)
        self._rows[idx] = row
        row._bubble = bubble                          # type: ignore[attr-defined]
        return row

    def _ensure_row(self, idx: int) -> tk.Frame:
        if idx in self._rows:
            return self._rows[idx]
        row = self._make_row(idx, self.messages[idx])
        self._trim()
        return row

    def _trim(self):
        """消息流只保留最近 MAX_ROWS 行，不然长篇小说会把内存吃光。"""
        keys = sorted(self._rows)
        if len(keys) <= MAX_ROWS:
            return
        for k in keys[: len(keys) - MAX_ROWS]:
            self._rows.pop(k).destroy()

    def _remove_rows_from(self, idx: int):
        for k in [k for k in self._rows if k >= idx]:
            self._rows.pop(k).destroy()
        # 被销毁的 label 还在 _bubble_labels 里，顺手清掉失效的
        self._bubble_labels = [l for l in self._bubble_labels if l.winfo_exists()]

    # ------------------------------------------------------------------
    # Player 回调
    # ------------------------------------------------------------------
    def on_state(self, state, msg):
        if state == THINKING and msg is not None and msg.kind == "dialogue":
            self._show_typing(msg)
        else:
            self._hide_typing()
        if state == "done":
            self._set_status("读完了 · 按 ← 可以回退重看")
            self.btn_play.configure(text="▶  播放")
        elif state == "paused":
            self._set_status("已暂停")

    def on_char(self, idx: int, n_shown: int):
        self._hide_typing()
        row = self._ensure_row(idx)
        bubble = getattr(row, "_bubble", None)
        if bubble is not None:
            bubble.configure(text=self.messages[idx].text[:n_shown])
        self._autoscroll_maybe()

    def on_sent(self, idx: int):
        self._hide_typing()
        self._autoscroll_maybe()

    def on_seek(self, delivered: int):
        self._remove_rows_from(delivered)
        self._hide_typing()
        # 瞬间把前面的内容补出来（回退/跳转后要能看到历史）
        start = max(0, delivered - MAX_ROWS)
        for i in range(start, delivered):
            row = self._ensure_row(i)
            b = getattr(row, "_bubble", None)
            if b is not None:
                b.configure(text=self.messages[i].text)
        self._autoscroll_maybe(force=True)

    def on_progress(self, delivered: int, total: int):
        self._updating_scale = True
        try:
            self.scale.configure(to=max(total, 1))
            self.scale_var.set(delivered)
        finally:
            self._updating_scale = False
        self._refresh_status()

    # ------------------------------------------------------------------
    # 正在输入
    # ------------------------------------------------------------------
    def _show_typing(self, msg: Message):
        if self._typing_row is not None:
            return
        p = persona_for(self.personas, msg.speaker)
        row = tk.Frame(self.inner, bg=BG)
        row.pack(fill="x", padx=14, pady=2, anchor="w")
        tk.Label(row, text=p.avatar, bg=p.color, fg="#101114", width=2, height=1,
                 font=(self.font, 13, "bold")).pack(side="left", anchor="n", padx=(0, 8))
        lbl = tk.Label(row, text=f"　{msg.speaker} 正在输入", bg=BG, fg=FG_DIM,
                       font=(self.font, 10, "italic"))
        lbl.pack(side="left", anchor="w", pady=2)
        self._typing_row = row
        self._typing_label = lbl
        self._typing_dots = 0
        self._typing_t0 = time.perf_counter()
        self._autoscroll_maybe()

    def _hide_typing(self):
        if self._typing_row is not None:
            self._typing_row.destroy()
            self._typing_row = None
            self._typing_label = None

    # ------------------------------------------------------------------
    # 滚动
    # ------------------------------------------------------------------
    def _on_wheel(self, event):
        num = getattr(event, "num", None)
        delta = getattr(event, "delta", 0)
        down = (delta < 0) if delta else (num == 5)
        self.canvas.yview_scroll(3 if down else -3, "units")
        self.canvas.update_idletasks()
        self._autoscroll = self.canvas.yview()[1] >= 0.999
        return "break"

    def _autoscroll_maybe(self, force: bool = False):
        if not (self._autoscroll or force):
            return
        self.inner.update_idletasks()
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self.canvas.yview_moveto(1.0)

    def _on_canvas_resize(self, event):
        self.canvas.itemconfigure(self._win, width=event.width)
        wrap = self._wrap()
        for l in self._bubble_labels:
            try:
                l.configure(wraplength=wrap)
            except tk.TclError:
                pass

    # ------------------------------------------------------------------
    # 控制
    # ------------------------------------------------------------------
    def toggle_play(self):
        if not self.messages:
            self.open_dialog()
            return
        if self.player.playing:
            self.player.pause()
            self.btn_play.configure(text="▶  播放")
        else:
            self.player.play()
            self.btn_play.configure(text="⏸  暂停")
            self._autoscroll = True
            self._autoscroll_maybe(force=True)
        self._refresh_status()

    def do_rewind(self):
        if not self.messages:
            return
        self.player.rewind(1)
        self._autoscroll = True
        self._refresh_status()

    def do_skip(self):
        if not self.messages:
            return
        self.player.skip_current()

    def do_restart(self):
        if not self.messages:
            return
        self.player.seek(0, resume=True)
        self.btn_play.configure(text="⏸  暂停")
        self._autoscroll = True

    def on_speed(self, _=None):
        if not self.messages:
            return
        s = float(self.speed_var.get())
        self.speed_label.configure(text=f"{s:g}×")
        self.player.set_speed(s)
        self._refresh_status()

    def on_toggle_narration(self):
        self.show_narration = bool(self.narr_var.get())
        if not self.messages:
            return
        # 只是加一个"跳过谓词"，消息下标不变 → 阅读位置不受影响
        self.player.skip_predicate = (
            None if self.show_narration else (lambda m: m.kind == "narration"))
        for k in [k for k in self._rows
                  if not self.show_narration and self.messages[k].kind == "narration"]:
            self._rows.pop(k).destroy()
        self._bubble_labels = [l for l in self._bubble_labels if l.winfo_exists()]

    def on_mood_change(self, choice: str):
        if choice == "本地 OU":
            self.mood_mode = "local"
            self.mood = self._compose_mood()
            self._apply_mood("本地 OU（OU 过程，相关时间 45s）")
        elif choice == "果蝇":
            self.mood_mode = "fly"
            if self.mood_fly is None:
                self._start_fly_prewarm()
            else:
                self._apply_mood("果蝇连接组慢变量")
        else:
            self.mood_mode = "hybrid"
            if self.mood_fly is None:
                self._start_fly_prewarm()
            else:
                self._apply_mood("果蝇 × 本地 OU")

    def _compose_mood(self):
        if self.mood_mode == "local" or self.mood_fly is None:
            return self.mood_local
        if self.mood_mode == "fly":
            return BlendMood(self.mood_fly, label="果蝇")
        return BlendMood(self.mood_fly, self.mood_local, label="果蝇×本地")

    def _apply_mood(self, label: str):
        self._sticky_status = False
        self.mood = self._compose_mood()
        if self.messages:
            self.player.set_mood(self.mood)
        self._last_mood_label = label
        self._refresh_status()

    # ------------------------------------------------------------------
    # 果蝇轨道预热
    # ------------------------------------------------------------------
    def _start_fly_prewarm(self):
        if self._fly_thread is not None and self._fly_thread.is_alive():
            return
        if not fly_available():
            self._set_status("flybrain 不可用（先按 README 装好 flybrain 和数据）", sticky=True)
            return

        cached = load_track_file(self.mood_cache) if os.path.exists(self.mood_cache) else None
        if cached is not None:
            self.mood_fly = MoodSource(cached, "果蝇连接组")
            self.mood_detail = _short_note(cached.note)
            self._apply_mood(f"果蝇连接组（缓存 {os.path.basename(self.mood_cache)}，"
                             f"{len(cached.values)} 个采样点）")
            return

        self._set_status("果蝇大脑预热中（numba 首次编译约 2s，之后 ~8ms/步）…", sticky=True)

        def work():
            # 只把结果丢回主线程，绝不在工作线程里碰 Tk
            track = generate_fly_track(
                seconds=self.fly_seconds, cache=self.mood_cache,
                log=lambda s: self._ui_queue.put(("status", s)))
            self._ui_queue.put(("fly_ready", track))

        self._fly_thread = threading.Thread(target=work, daemon=True)
        self._fly_thread.start()

    def _on_fly_ready(self, track):
        self._sticky_status = False
        if track is None:
            self._set_status("果蝇轨道生成失败，继续用本地 OU")
            self.mood_mode = "local"
            self.mood_var.set("本地 OU")
            self._refresh_status()
            return
        self.mood_fly = MoodSource(track, "果蝇连接组")
        self.mood_detail = _short_note(track.note)
        self._apply_mood("果蝇连接组（刚生成）")

    # ------------------------------------------------------------------
    # 状态栏 / 心跳
    # ------------------------------------------------------------------
    def _set_status(self, text: str, sticky: bool = False):
        self._sticky_status = sticky
        self.status.configure(text=text)

    def _refresh_status(self):
        if not self.messages or self._sticky_status:
            return
        p = self.player
        msg = p.current_message
        who = ""
        if msg is not None:
            who = ("旁白" if msg.kind != "dialogue" else str(msg.speaker))
        mood_now = self.mood.at(p.model_time())
        speed = p.speed
        if self.mood_mode == "local":
            self.mood_detail = ""
        self._set_status(
            f"{p.delivered}/{len(self.messages)} · 节奏源 {getattr(self.mood, 'label', '?')} "
            f"{self.mood_detail} · 心情 ×{mood_now:.2f} · 速度 {speed:g}× · 下一条「{who}」"
        )

    def _on_scrub(self, value):
        if self._updating_scale or not self.messages:
            return
        try:
            n = int(float(value))
        except (TypeError, ValueError):
            return
        self._set_status(f"拖到 {n}/{len(self.messages)}…（松手跳转）")

    def _on_scrub_release(self, _event):
        self._scrubbing = False
        if not self.messages:
            return
        n = int(float(self.scale_var.get()))
        self.player.seek(n, resume=self.player.playing)
        self._autoscroll = True
        self._refresh_status()

    def _tick(self):
        # 先把后台线程丢过来的活干完（Tk 只能在主线程碰）
        while True:
            try:
                kind, payload = self._ui_queue.get_nowait()
            except queue.Empty:
                break
            if kind == "status":
                self._set_status(payload, sticky=True)
            elif kind == "fly_ready":
                self._on_fly_ready(payload)

        now = time.perf_counter()
        dt = (now - self._last_tick) * 1000.0
        self._last_tick = now
        if getattr(self, "player", None) is not None:
            self.player.tick(dt)
            # 正在输入的省略号动画
            if self._typing_label is not None and now - self._typing_t0 > 0.35:
                self._typing_t0 = now
                self._typing_dots = (self._typing_dots + 1) % 4
                name = self.player.current_message
                who = name.speaker if name is not None else ""
                self._typing_label.configure(
                    text=f"　{who} 正在输入" + "·" * self._typing_dots)
            if int(now * 2) % 2 == 0 and self.player.playing:
                self._refresh_status()
        self.root.after(TICK_MS, self._tick)


def run(path: str | None = None, mood_mode: str = "local", mood_cache: str | None = None,
        fly_seconds: int = 300, speed: float = 1.0, rules=None):
    root = tk.Tk()
    ChatBookApp(root, path, mood_mode, mood_cache, fly_seconds, speed, rules)
    root.mainloop()
