"""最近打开的书。

选书每次都要在文件系统里翻一遍很烦。这里把打开过的书记下来，
下次启动直接把最近几本摊在选书窗口最上面，点一下就开。

存的位置和用户规则同一个目录（`~/.config/liaoshu/` 或 Windows 的
`%LOCALAPPDATA%\\liaoshu\\`），文件名 `recent.json`。
"""

from __future__ import annotations

import json
import os

from .rules import config_dir

MAX_KEEP = 20          # 文件里最多留多少条
MAX_SHOW = 12          # 选书窗口最多列几本


def recent_path() -> str:
    return os.path.join(config_dir(), "recent.json")


def list_recent(limit: int = MAX_SHOW) -> list[str]:
    """按"最近用过"排序，过滤掉已经不存在的文件。"""
    try:
        with open(recent_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    out, seen = [], set()
    for p in data:
        if not isinstance(p, str):
            continue
        ap = os.path.abspath(p)
        if ap in seen or not os.path.isfile(ap):
            continue
        seen.add(ap)
        out.append(ap)
        if len(out) >= limit:
            break
    return out


def add(path: str) -> None:
    """把一本书记到最前。按绝对路径去重，超过上限的丢掉。"""
    ap = os.path.abspath(path)
    items = [p for p in _raw() if os.path.abspath(p) != ap]
    items.insert(0, ap)
    items = items[:MAX_KEEP]
    try:
        os.makedirs(os.path.dirname(recent_path()), exist_ok=True)
        with open(recent_path(), "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def _raw() -> list[str]:
    try:
        with open(recent_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return [p for p in data if isinstance(p, str)] if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def clear() -> None:
    try:
        os.remove(recent_path())
    except OSError:
        pass
