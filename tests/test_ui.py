"""界面层的回归测试（需要一个可用的 X/Wayland 显示）。

    DISPLAY=:0 python3 tests/test_ui.py

窗口是隐藏的（withdraw），只验证 widget 状态，不会弹出来打扰人。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
    print("没有显示环境，跳过 UI 测试")
    raise SystemExit(0)

import tkinter as tk

from liaoshu.rhythm import THINKING, TYPING
from liaoshu.ui import ChatBookApp

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SAMPLE = os.path.join(ROOT, "samples", "夜航.txt")

FAILS: list[str] = []


def check(name, cond, extra=""):
    print(("  ✓ " if cond else "  ✗ ") + name + ("" if cond else f"  {extra}"))
    if not cond:
        FAILS.append(name)


root = tk.Tk()
root.withdraw()
app = ChatBookApp(root, SAMPLE, mood_mode="local", speed=6.0)

print("\n[UI] 消息流 / 正在输入 / 回退")
check("载入后开始播放", app.player.playing)

# 交给播放器跑一会儿，让它真的吐几条消息出来（不依赖 after 的偶然触发）
for _ in range(3000):
    app.player.tick(16.0)
    if len(app._rows) >= 3:
        break
check("播放后消息流里渲染出内容", len(app._rows) >= 3, f"rows={len(app._rows)}")

# 后面的断言都是同步的 widget 操作，先把播放器按住，免得后台 tick 插进来改状态
app.player.pause()

# 手动走一遍 思考 -> 打字，找一个对话消息
idx = next(i for i, m in enumerate(app.messages) if m.kind == "dialogue")
msg = app.messages[idx]

app.on_state(THINKING, msg)
check("思考阶段出现「正在输入」指示器", app._typing_row is not None)
check("指示器里带说话人名字",
      msg.speaker in (app._typing_label.cget("text") if app._typing_label else ""))

app.on_char(idx, 3)
check("开始打字后指示器消失", app._typing_row is None)
check("气泡里是前 3 个字", app._rows[idx]._bubble.cget("text") == msg.text[:3],
      app._rows[idx]._bubble.cget("text"))

app.on_char(idx, len(msg.text))
check("整条打完后文字完整", app._rows[idx]._bubble.cget("text") == msg.text)

# 回退：这条已经发完（delivered 指向下一条、shown 归零），按回退应该撤掉它
app.player.delivered = idx + 1
app.player.shown = 0
app.player.state = "idle"
app.player.playing = True
app.do_rewind()
check("回退后这条气泡被删除", idx not in app._rows, f"rows={sorted(app._rows)[:6]}")
check("回退后重新出现「正在输入」指示器", app._typing_row is not None)
check("回退把位置退回到这条", app.player.delivered == idx, f"delivered={app.player.delivered}")
check("回退后是重打而不是定格", app.player.shown == 0 and app.player.playing)

# 跳转：瞬间渲染历史
app.player.pause()
app.player.seek(6, resume=False)
check("跳转后渲染出历史消息", len(app._rows) > 0, f"rows={len(app._rows)}")
check("跳转后没有残留的指示器", app._typing_row is None)

# 旁白开关：下标不变，位置不丢
before_delivered = app.player.delivered
app.narr_var.set(False)
app.on_toggle_narration()
check("关旁白后阅读位置不变", app.player.delivered == before_delivered)
narr_rows = [i for i in app._rows if app.messages[i].kind == "narration"]
check("关旁白后消息流里没有旁白", not narr_rows, str(narr_rows[:5]))
check("跳过谓词已生效", app.player.skip_predicate is not None)

# 长列表裁剪
app.player.seek(min(400, len(app.messages) - 1), resume=False)
check("长列表会裁剪到上限以内", len(app._rows) <= 240, f"rows={len(app._rows)}")

root.destroy()

print()
if FAILS:
    print(f"❌ {len(FAILS)} 项失败: {FAILS}")
    raise SystemExit(1)
print("✅ UI 测试全部通过")
