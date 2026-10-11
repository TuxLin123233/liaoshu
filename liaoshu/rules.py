"""用户自己的过滤/干涉规则。

解析器把 `名尾黑名单`（_BAD_NAME_TAILS）等写成内置表，总有个头——真人对书里的
坑才最有感知。这个文件给普通用户一条不加代码就能干预解析的路：

    rules.json  （~/.config/liaoshu/ 或 Windows 的 %LOCALAPPDATA%\\liaoshu\\ 下）

    {
      "ban": ["身子往儿", "父亲紧张"],   // 这些词一辈子不许当说话人
      "letters": ["儿", "珠", "声"],     // 追加"名尾黑名单"：末字是这个的都别当说话人
      "map": { "高加林": "加林" }         // 说话人改名 / 多别名合并成一个人
    }

窗口/命令行每次启动都会自动加载这个文件；改完重开一次就生效。
也可以用 `--rules 路径.json` 指到别的文件。
"""

from __future__ import annotations

import json
import os
import sys

SAMPLE = {
    # 这些词一辈子不许当说话人（整词匹配）
    "ban": [],
    # 追加"名尾黑名单"：末字落在这些字里的都别当说话人。
    # 例：《人生》这类书里 儿 常是语尾（身子往儿＝身子往儿子…），别的书可能是真名
    "letters": [],
    # 说话人改名 / 多别名合并成一个人
    "map": {},
}


def config_dir() -> str:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        d = os.path.join(base, "liaoshu")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
        d = os.path.join(base, "liaoshu")
    return d


def rules_path() -> str:
    return os.path.join(config_dir(), "rules.json")


def default_rules() -> dict:
    """尽量宽松：文件不存在/坏了都当没规则，不打断阅读。"""
    path = rules_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        return data
    except (OSError, ValueError):
        return {}


def load_rules(path: str | None = None) -> dict:
    if path:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            print(f"规则文件读不了：{path}（文件坏了？）")
            return {}
    return default_rules()


def save_rules(data: dict, path: str | None = None) -> str:
    path = path or rules_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def init_rules_file(path: str | None = None) -> str:
    """第一个用户：把带注释的例子写出来，省得看文档。"""
    path = path or rules_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        return path
    save_rules(SAMPLE, path)
    return path


if __name__ == "__main__":
    p = init_rules_file()
    print(f"已生成示例规则：{p}")
    print("格式：ban=整词禁当说话人；letters=名尾禁字；map=改名/合并")