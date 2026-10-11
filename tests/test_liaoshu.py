"""不依赖界面的回归测试：解析、时序确定性、回退 / 跳转 / 跳过。

    python3 tests/test_liaoshu.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from liaoshu.mood import MoodTrack
from liaoshu.mood import MoodSource
from liaoshu.parser import (CONTEXT, EXPLICIT, GUESS, parse, parse_file)
from liaoshu.personas import build_personas, persona_for
from liaoshu.rhythm import Player, PlayerHooks, RhythmModel

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SAMPLE = os.path.join(ROOT, "samples", "夜航.txt")

FAILS: list[str] = []


def check(name: str, cond: bool, extra: str = ""):
    if cond:
        print(f"  ✓ {name}")
    else:
        print(f"  ✗ {name} {extra}")
        FAILS.append(name)


# --------------------------------------------------------------------------
print("\n[1] 解析：旁白 / 对话切分与说话人")
msgs = parse_file(SAMPLE)
dialogue = [m for m in msgs if m.kind == "dialogue"]
narration = [m for m in msgs if m.kind == "narration"]
cast = {m.speaker for m in dialogue}

check("解析出对话", len(dialogue) > 40, f"实际 {len(dialogue)}")
check("解析出旁白", len(narration) > 20, f"实际 {len(narration)}")
check("识别出 5 个角色", cast == {"沈薇", "阿岩", "老周", "小满", "陆离"}, str(sorted(cast)))
check("没有把'他说'当人名", not any(s and ("说" in s or "道" in s) for s in cast), str(cast))
check("大多数署名是显式或上下文",
      sum(1 for m in dialogue if m.confidence != GUESS) >= len(dialogue) * 0.55,
      f"猜的 {sum(1 for m in dialogue if m.confidence == GUESS)}/{len(dialogue)}")
check("第 1 条对话属于沈薇（旁白提到）",
      dialogue[0].speaker == "沈薇", f"实际 {dialogue[0].speaker}")
check("第 4 条对话属于阿岩（旁白提到）",
      dialogue[3].speaker == "阿岩", f"实际 {dialogue[3].speaker}")
check("纯署名片段不会变成旁白",
      not any(n.text.strip() in ("他说，", "她说，") for n in narration))

# `名字：` 直书体
label_msgs = parse("林晚：你终于来了。\n周野：路上堵车。\n林晚：借口。")
check("支持 `名字：` 直书体",
      [m.speaker for m in label_msgs] == ["林晚", "周野", "林晚"],
      str([m.speaker for m in label_msgs]))

# 硬换行（按固定列宽断行的 txt）
wrapped = "\n".join(["这是一段被硬换行的文字" + "字" * 25] * 14)
check("识别并合并硬换行", len(parse(wrapped)) < 14, f"实际 {len(parse(wrapped))} 段")

# 跨段未闭合的引号
multi = parse("“前半句没有结束。\n“后半句才收尾。”他说。")
check("跨段对白能合并", any("前半句没有结束" in m.text and "后半句" in m.text
                            for m in multi if m.kind == "dialogue"),
      str([(m.kind, m.text) for m in multi]))

# 单字代词署名（她说 / 他说），并被"旁白里刚点名的人"接走
pron = parse("沈薇说：“走吧。”\n沈薇关掉了星图。\n“那么，”她说，“就按静默航行来。”")
sp = [m.speaker for m in pron if m.kind == "dialogue"]
check("单字代词 `她说` 能认出来并落回沈薇", all(s == "沈薇" for s in sp), str(sp))

named = parse("老周问：“怎么了？”\n老周盯着星图看了很久。\n“不确定。”他说。")
sp = [m.speaker for m in named if m.kind == "dialogue"]
check("代词 `他` 落到旁白刚点名的人", sp[-1] == "老周", str(sp))

# `名字点头：` 这种身体动作署名不能被吃成"小满点头"这种假名
body = parse("阿岩问：“信号还在吗？”\n小满点头：“还在。”\n阿岩说：“一直？”")
sp = [m.speaker for m in body if m.kind == "dialogue"]
check("`小满点头：` 不会被当成名字", "小满点头" not in sp and "点头" not in sp, str(sp))

alt = parse("阿甲：先吃吧。\n阿乙：不了。\n阿甲：那我自己吃吧。")
sp = [m.speaker for m in alt if m.kind == "dialogue"]
check("轮替回话回给上一位", sp == ["阿甲", "阿乙", "阿甲"], str(sp))

# 名尾功能字：`名字+动作/状语` 不能被贪婪名字吃掉（《人生》实测的一大类）
junk = parse("“老四头该歇了。”德顺老汉唱完长长吐了口气说。\n“嗳。”他知。\n"
             "“那会儿是真来劲。”加林开玩笑道。\n“可不。”巧珍插嘴。\n"
             "两个人先后紧张地问她：“出了什么事？”")
sp = [m.speaker for m in junk if m.kind == "dialogue"]
bad = {"老汉唱完", "他知", "加林开玩", "巧珍插嘴", "张地问她", "问她",
       "处叫冤说", "眯眯地说", "亚萍转过", "他招招手"}
check("名尾功能字不会被吃成名字", not (bad & set(sp)), f"残留 {bad & set(sp)}")

# 长书实测：《人生》主力角色各归各位，动词尾假名不再进说话人栏
# （样本书有版权，不随仓库分发；本地没放这份本书就自动跳过）
rs_path = os.path.join(ROOT, "samples", "人生.txt")
if os.path.exists(rs_path):
    rs = parse(open(rs_path, encoding="utf-8").read())
    rs_sp = [m.speaker for m in rs if m.kind == "dialogue" and m.speaker]
    top = [s for s, _n in __import__("collections").Counter(rs_sp).most_common(6)]
    check("《人生》主力角色占前六", {"加林", "巧珍", "亚萍"} <= set(top), f"前六 {top}")
    check("《人生》说话人以功能字收尾的假名清零",
          not any(s[-1] in "手嘴轻想住告知惊玩叨断脸兄睛颤回硬摆母终怔般有舌先号父另溅解部一咽转怪吐"
                  for s in rs_sp),
          f"残留 {sorted({s for s in rs_sp if s[-1] in '手嘴轻想住告知惊玩叨断脸兄睛颤回硬摆母终怔般有舌先号父另溅解部一咽转怪吐'})[:6]}")
else:
    check("《人生》长书回归（样本书未入库，本地存在时启用）", True, "跳过")

# 内嵌模式：桌面窗口把本地网页服务交给调用方持有，得能起、能关
from liaoshu.server import serve
url, httpd = serve(os.path.join(ROOT, "samples", "夜航.txt"),
                   port=9961, open_browser=False, block=False)
try:
    import urllib.request
    with urllib.request.urlopen(url, timeout=5) as r:
        body = r.read(200)
    check("内嵌模式起服并能出首页", r.status == 200 and b"<" in body, str(r.status))
finally:
    httpd.shutdown()
    httpd.server_close()
check("内嵌模式 httpd 能干净关停", True, "shutdown()")

# --------------------------------------------------------------------------
print("\n[2] 时序：确定性")
personas = build_personas(msgs)
mood = MoodSource(MoodTrack.local(seed=7), "test")
m1 = RhythmModel(personas, mood, seed=12345)
m2 = RhythmModel(personas, mood, seed=12345)
p1 = m1.plan(12, msgs[12], msgs[11], 4000.0)
p2 = m2.plan(12, msgs[12], msgs[11], 4000.0)
check("同序号消息的时序完全一致", p1.delays == p2.delays and p1.think_ms == p2.think_ms)
check("每条消息的延迟数等于字数", len(p1.delays) == len(msgs[12].text))
check("延迟都在合理区间", all(8.0 <= d <= 2500.0 for d in p1.delays))

words = msgs[12].text
pos = persona_for(personas, msgs[12].speaker)
print(f"    示例：{pos.name} 平均 {sum(p1.delays)/len(p1.delays):.0f}ms/字 "
      f"(基准 {pos.ms_per_char:.0f}ms/字, {pos.cpm:.0f} 字/分)")

# --------------------------------------------------------------------------
print("\n[3] 播放器：停止 / 回退 / 跳转 / 跳过")
events: list[tuple] = []


def new_player(msgs=msgs, mood=mood, seed=12345):
    model = RhythmModel(build_personas(msgs), mood, seed=seed)
    return Player(msgs, model, PlayerHooks(
        on_state=lambda s, m: events.append(("state", s, m)),
        on_char=lambda i, n: events.append(("char", i, n)),
        on_sent=lambda i: events.append(("sent", i)),
        on_seek=lambda d: events.append(("seek", d)),
        on_progress=lambda d, t: None))


def run_for(player, ms, step=5.0):
    t = 0.0
    while t < ms and player.playing:
        player.tick(step)
        t += step
    return t


pl = new_player()
pl.play()
run_for(pl, 30000)
check("能连续送出多条消息", pl.delivered > 3, f"实际 {pl.delivered}")
sent_before = pl.delivered

# 暂停：位置必须冻住
pl.pause()
d0 = pl.delivered
run_for(pl, 5000)
check("暂停后不再推进", pl.delivered == d0 and not pl.playing)

# 继续：接着打，不重头
chars_before = pl.shown
pl.play()
run_for(pl, 60)
check("继续后接着打（不整条重来）", pl.shown >= chars_before, f"{chars_before} -> {pl.shown}")

# 回退：只有一条路径是对的 —— 退到上一条的开头
pl.pause()
pl.seek(10)
check("跳转后 delivered 正确", pl.delivered == 10)
pl.rewind(1)
check("空闲时回退一条", pl.delivered == 9, f"实际 {pl.delivered}")

pl.seek(10)
pl.play()
for _ in range(20000):                     # 精确推进到"第 10 条正在打字"
    pl.tick(5.0)
    if pl.delivered == 10 and pl.shown > 0:
        break
check("此时确实在打字", pl.delivered == 10 and pl.shown > 0,
      f"delivered={pl.delivered} shown={pl.shown}")
pl.rewind(1)
check("打字中回退 → 撤掉这条并回到它的开头", pl.delivered == 10 and pl.shown == 0,
      f"delivered={pl.delivered} shown={pl.shown}")
pl.rewind(1)
check("再按一次 → 退到上一条", pl.delivered == 9, f"实际 {pl.delivered}")

# 跳转：瞬间渲染，不重放
pl.seek(40)
check("跳转到 40", pl.delivered == 40 and pl.shown == 0)

# 跳过被隐藏的消息
pl2 = new_player()
pl2.skip_predicate = lambda m: m.kind == "narration"
pl2.seek(0, resume=True)
for _ in range(4000):
    pl2.tick(5.0)
    if pl2.delivered > 25:
        break
check("跳过谓词生效（旁白不占时间）", pl2.delivered > 5, f"实际 {pl2.delivered}")

# 速度：只影响将来，不改已显示内容
pl3 = new_player()
pl3.seek(5, resume=False)
before = pl3._ensure_plan(5).delays[:]
pl3.set_speed(4.0)
after = pl3._ensure_plan(5).delays[:]
check("改速度不重算已有时序", before == after)

# --------------------------------------------------------------------------
print("\n[4] 节奏模型：结构 vs 纯随机")
plans = [m1.plan(i, msgs[i], msgs[i - 1] if i else None, i * 3000.0)
         for i in range(1, 40)]
all_d = [d for p in plans for d in p.delays]
mean = sum(all_d) / len(all_d)
var = sum((d - mean) ** 2 for d in all_d) / len(all_d)
ac = 0.0
if len(all_d) > 2 and var > 0:
    a = [d - mean for d in all_d[:-1]]
    b = [d - mean for d in all_d[1:]]
    ac = sum(x * y for x, y in zip(a, b)) / (sum(x * x for x in a) ** 0.5 *
                                             sum(y * y for y in b) ** 0.5)
print(f"    逐字延迟：均值 {mean:.0f}ms  标准差 {var**0.5:.0f}ms  CV {var**0.5/mean:.2f}  lag1 {ac:+.3f}")
check("逐字延迟有结构（相邻延迟正相关）", ac > 0.05, f"lag1={ac:+.3f}")
check("延迟不是常数（有抖动）", var ** 0.5 > 8)

# --------------------------------------------------------------------------
print("\n[5] 心情轨道：量纲不能被两次 depth 搞偏")
from liaoshu.mood import MoodTrack, _ou_series

loc = MoodTrack.local(seed=20241011, n=20000)
lo = [loc.at(i * 500.0) for i in range(20000)]
check("本地轨道长期均值 ≈ 1.0", 0.96 < sum(lo) / len(lo) < 1.04,
      f"实际 {sum(lo)/len(lo):.4f}")
check("本地轨道有起伏但不出圈", min(lo) > 0.7 and max(lo) < 1.3,
      f"{min(lo):.3f}~{max(lo):.3f}")

# 外部轨道文件里存的是绝对倍率，不能再乘一次 depth
ext = MoodTrack.from_values([0.8, 0.9, 1.0, 1.1, 1.2])
vals = [ext.at(i * 500.0) for i in range(5)]
check("外部轨道按绝对值取样（不再乘 depth）", vals == [0.8, 0.9, 1.0, 1.1, 1.2], str(vals))

cache = os.path.join(ROOT, "cache", "fly_mood.txt")
if os.path.exists(cache):
    from liaoshu.mood import load_track_file
    fly = load_track_file(cache)
    fo = [fly.at(i * 500.0) for i in range(len(fly.values))]
    check("果蝇轨道均值 ≈ 1.0（没有被多乘一次 depth）",
          0.85 < sum(fo) / len(fo) < 1.15, f"实际 {sum(fo)/len(fo):.4f}")
    print(f"    果蝇轨道备注: {fly.note}")
else:
    print("  （跳过果蝇轨道检查：还没有 cache/fly_mood.txt）")

# --------------------------------------------------------------------------
print()
if FAILS:
    print(f"❌ {len(FAILS)} 项失败: {FAILS}")
    raise SystemExit(1)
print("✅ 全部通过")
