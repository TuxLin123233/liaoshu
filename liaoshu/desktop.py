"""原生窗口：用 pywebview 把本地网页界面包成一个"软件窗口"。

和 `--ui web` 的区别只有一个：不开系统浏览器，直接弹一个原生窗口
（Linux 走 WebKitGTK，Windows 走系统自带的 Edge WebView2），
窗口右上角一关，服务也就停了，没有后台残留。

不是真正的"零依赖"——需要 `pip install pywebview`。没装时给出提示，
退回系统浏览器（相当于 --ui web）。
"""

from __future__ import annotations

import os


def run_native(path: str, port: int = 8770, speed: float = 1.0,
               mood_mode: str = "local", fly_seconds: int = 300,
               mood_cache: str | None = None, rules: dict | None = None) -> None:
    try:
        import webview
    except ImportError:
        print("原生窗口需要 pywebview：")
        print("    pip install pywebview")
        print("没装，这次退回系统浏览器打开。")
        from .server import serve
        serve(path, port=port, mood_mode=mood_mode, speed=speed,
              fly_seconds=fly_seconds, mood_cache=mood_cache,
              open_browser=True, rules=rules)
        return

    from .server import serve

    url, httpd = serve(path, port=port, mood_mode=mood_mode, speed=speed,
                       fly_seconds=fly_seconds, mood_cache=mood_cache,
                       open_browser=False, block=False, rules=rules)

    title = f"聊书 · {os.path.splitext(os.path.basename(path))[0]}"
    gx, gy, gw, gh = _load_geom()
    window = webview.create_window(
        title, url, x=gx, y=gy, width=gw, height=gh, min_size=(720, 480))
    window.events.closed += lambda: (_save_geom(window), _shutdown(httpd))

    try:
        webview.start()
    finally:
        _shutdown(httpd)


def _geom_path() -> str:
    from .rules import config_dir
    return os.path.join(config_dir(), "window.json")


def _load_geom() -> tuple[int | None, int | None, int, int]:
    """上次窗口的大小/位置；坏了或没有就用默认。"""
    import json
    try:
        with open(_geom_path(), "r", encoding="utf-8") as f:
            d = json.load(f)
        w = max(720, int(d["w"]))
        h = max(480, int(d["h"]))
        x = d.get("x")
        y = d.get("y")
        return (int(x) if x is not None else None,
                int(y) if y is not None else None, w, h)
    except (OSError, ValueError, KeyError, TypeError):
        return (None, None, 1000, 720)


def _save_geom(window) -> None:
    """关窗前把窗口几何记下来，下次原样开。"""
    import json
    try:
        d = {"x": int(window.x), "y": int(window.y),
             "w": int(window.width), "h": int(window.height)}
        os.makedirs(os.path.dirname(_geom_path()), exist_ok=True)
        with open(_geom_path(), "w", encoding="utf-8") as f:
            json.dump(d, f)
    except (OSError, ValueError, TypeError, AttributeError):
        pass


def _shutdown(httpd) -> None:
    try:
        httpd.shutdown()
    except Exception:                                    # noqa: BLE001
        pass
    try:
        httpd.server_close()
    except Exception:                                    # noqa: BLE001
        pass