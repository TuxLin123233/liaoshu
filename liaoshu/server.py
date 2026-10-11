"""本地 HTTP + SSE 服务：Python 跑解析和节奏，浏览器只负责画。

为什么不是 WebSocket：SSE 是单向推流，标准库 `http.server` 就能做，
不需要任何第三方依赖。命令走 POST，事件走 SSE，一共两个端点。

    浏览器  ──GET /api/events──►  (SSE: init / state / char / sent / seek / status)
            ──POST /api/cmd────►  play / pause / rewind / skip / seek / speed / ...
"""

from __future__ import annotations

import json
import os
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from .mood import (BlendMood, MoodSource, MoodTrack, fly_available,
                   generate_fly_track, load_track_file)
from .parser import parse_file
from .personas import build_personas, persona_for
from .rhythm import Player, PlayerHooks, RhythmModel

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
TICK_S = 0.012              # 播放器心跳
STATUS_S = 0.4              # 状态栏刷新间隔
PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MIME = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
        ".js": "application/javascript; charset=utf-8", ".svg": "image/svg+xml",
        ".png": "image/png", ".ico": "image/x-icon"}


class Session:
    """一本书 + 一个播放器 + 若干 SSE 订阅者。"""

    def __init__(self, path: str, mood_mode: str = "local", speed: float = 1.0,
                 fly_seconds: int = 300, mood_cache: str | None = None):
        self.path = path
        self.lock = threading.RLock()
        self.clients: set[queue.Queue] = set()
        self.pending: list[dict] = []
        self.stopping = False
        self.mood_mode = mood_mode
        self.fly_seconds = fly_seconds
        self.mood_cache = mood_cache or os.path.join(PROJECT, "cache", "fly_mood.txt")
        self._fly_started = False

        self.messages = parse_file(path)
        self.personas = build_personas(self.messages)
        self.chapters = [{"idx": i, "text": m.text}
                         for i, m in enumerate(self.messages) if m.kind == "chapter"]
        self.mood_local = MoodSource(MoodTrack.local(seed=20241011), "本地 OU")
        self.mood_fly: MoodSource | None = None
        self.mood = self.mood_local
        self.mood_detail = ""

        self.model = RhythmModel(self.personas, self.mood, seed=12345)
        self.player = Player(self.messages, self.model, PlayerHooks(
            on_state=self._on_state, on_char=self._on_char, on_sent=self._on_sent,
            on_seek=self._on_seek, on_progress=self._on_progress))
        self.player.set_speed(speed)

        self._status_t0 = 0.0
        self._started = False       # 有没有被第一个客户端"开过播"
        # "用户主动按了暂停" 和 "没人看所以自动暂停" 必须分开记：
        # 混在一起的话，关一次页面回来就永远是暂停状态了。
        self.user_paused = False
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        if mood_mode in ("fly", "hybrid"):
            self.start_fly()

    # ---------------------------------------------------------------- 事件
    def _push(self, ev: dict) -> None:
        """只在持有锁、且处于"一拍"之内时调用。"""
        self.pending.append(ev)

    def _flush(self) -> None:
        if not self.pending:
            return
        batch = self.pending
        self.pending = []
        dead = []
        for q in self.clients:
            try:
                q.put_nowait(("batch", batch))
            except queue.Full:
                dead.append(q)
        for q in dead:
            self.clients.discard(q)

    def _on_state(self, state, msg):
        self._push({"t": "state", "state": state,
                    "idx": self.player.delivered,
                    "speaker": getattr(msg, "speaker", None),
                    "kind": getattr(msg, "kind", None)})

    def _on_char(self, idx, n):
        self._push({"t": "char", "idx": idx, "n": n})

    def _on_sent(self, idx):
        self._push({"t": "sent", "idx": idx})

    def _on_seek(self, delivered):
        self._push({"t": "seek", "delivered": delivered})

    def _on_progress(self, delivered, total):
        self._push({"t": "progress", "delivered": delivered, "total": total})

    # ---------------------------------------------------------------- 心跳
    def _loop(self) -> None:
        last = time.perf_counter()
        while not self.stopping:
            time.sleep(TICK_S)
            now = time.perf_counter()
            dt = (now - last) * 1000.0
            last = now
            try:
                with self.lock:
                    self.player.tick(dt)
                    if now - self._status_t0 > STATUS_S:
                        self._status_t0 = now
                        self._push(self.status_payload())
                    self._flush()
            except Exception as e:                      # noqa: BLE001
                self._push({"t": "error", "text": repr(e)})
                self._flush()

    def status_payload(self) -> dict:
        p = self.player
        msg = p.current_message
        return {
            "t": "status",
            "playing": p.playing,
            "delivered": p.delivered,
            "total": len(self.messages),
            "speed": p.speed,
            "mood": round(self.mood.at(p.model_time()), 3),
            "moodLabel": getattr(self.mood, "label", "?"),
            "moodDetail": self.mood_detail,
            "next": (None if msg is None else
                     {"speaker": msg.speaker, "kind": msg.kind}),
            "flyState": self.fly_state,
        }

    # ---------------------------------------------------------------- 心情
    fly_state = "off"

    def _compose_mood(self):
        if self.mood_mode == "local" or self.mood_fly is None:
            return self.mood_local
        if self.mood_mode == "fly":
            return BlendMood(self.mood_fly, label="果蝇连接组")
        return BlendMood(self.mood_fly, self.mood_local, label="果蝇×本地")

    def set_mood(self, mode: str) -> None:
        with self.lock:
            self.mood_mode = mode
            if mode == "local":
                self.mood_detail = ""
            self.mood = self._compose_mood()
            self.player.set_mood(self.mood)
            self._push({"t": "mood", "mode": mode,
                        "label": getattr(self.mood, "label", "?"),
                        "detail": self.mood_detail, "flyState": self.fly_state})
            self._flush()
        if mode in ("fly", "hybrid"):
            self.start_fly()

    def start_fly(self) -> None:
        if self._fly_started:
            return
        self._fly_started = True
        if not fly_available():
            self.fly_state = "unavailable"
            return
        cached = load_track_file(self.mood_cache) if os.path.exists(self.mood_cache) else None
        if cached is not None:
            self._install_fly(cached, "cache")
            return

        self.fly_state = "warming"
        threading.Thread(target=self._gen_fly, daemon=True).start()

    def _gen_fly(self) -> None:
        def log(s):
            with self.lock:
                self._push({"t": "log", "text": s})
                self._flush()

        track = generate_fly_track(seconds=self.fly_seconds, cache=self.mood_cache, log=log)
        if track is None:
            self.fly_state = "failed"
            with self.lock:
                self._push({"t": "log", "text": "果蝇轨道生成失败，继续用本地 OU"})
                self._flush()
            return
        self._install_fly(track, "generated")

    def _install_fly(self, track, how: str) -> None:
        note = getattr(track, "note", "") or ""
        detail = ""
        import re
        m = re.search(r"CV=([\d.]+)%.*?lag1_autocorr=([+-][\d.]+)", note)
        if m:
            detail = f"CV {m.group(1)}% · 自相关 {m.group(2)}"
        with self.lock:
            self.mood_fly = MoodSource(track, "果蝇连接组")
            self.mood_detail = detail
            self.fly_state = "ready"
            self.mood = self._compose_mood()
            self.player.set_mood(self.mood)
            self._push({"t": "mood", "mode": self.mood_mode,
                        "label": getattr(self.mood, "label", "?"),
                        "detail": detail, "flyState": "ready",
                        "samples": len(track.values), "how": how})
            self._flush()

    # ---------------------------------------------------------------- 命令
    def command(self, cmd: str, **kw) -> None:
        with self.lock:
            p = self.player
            if cmd == "play":
                self.user_paused = False
                p.play()
            elif cmd == "pause":
                self.user_paused = True
                p.pause()
            elif cmd == "toggle":
                if p.playing:
                    self.user_paused = True
                    p.pause()
                else:
                    self.user_paused = False
                    p.play()
            elif cmd == "rewind":
                p.rewind(int(kw.get("n", 1)))
            elif cmd == "skip":
                p.skip_current()
            elif cmd == "restart":
                p.seek(0, resume=True)
            elif cmd == "seek":
                p.seek(int(kw.get("delivered", 0)), resume=bool(kw.get("resume", p.playing)))
            elif cmd == "speed":
                p.set_speed(float(kw.get("value", 1.0)))
            elif cmd == "narration":
                on = bool(kw.get("on", True))
                p.skip_predicate = None if on else (lambda m: m.kind == "narration")
            elif cmd == "mood":
                self.lock.release()
                try:
                    self.set_mood(str(kw.get("mode", "local")))
                finally:
                    self.lock.acquire()
            self._push(self.status_payload())
            self._flush()

    def snapshot(self) -> dict:
        """新连上的客户端要的全量状态。"""
        with self.lock:
            p = self.player
            return {
                "t": "init",
                "book": os.path.basename(self.path),
                "total": len(self.messages),
                "messages": [{"k": m.kind, "x": m.text, "s": m.speaker,
                              "g": m.confidence == "guess"} for m in self.messages],
                "personas": {n: {"color": q.color, "avatar": q.avatar,
                                 "cpm": round(q.cpm)}
                             for n, q in self.personas.items()},
                "chapters": self.chapters,
                "cast": self._cast_list(),
                "delivered": p.delivered,
                "shown": p.shown,
                "playing": p.playing,
                "speed": p.speed,
                "narration": p.skip_predicate is None,
                "moodMode": self.mood_mode,
                "moodLabel": getattr(self.mood, "label", "?"),
                "moodDetail": self.mood_detail,
                "flyState": self.fly_state,
            }

    def _cast_list(self) -> list[dict]:
        n = {}
        for m in self.messages:
            if m.kind == "dialogue" and m.speaker:
                n[m.speaker] = n.get(m.speaker, 0) + 1
        out = []
        for name, cnt in sorted(n.items(), key=lambda kv: -kv[1]):
            q = self.personas.get(name)
            if q is None:
                continue
            out.append({"name": name, "count": cnt, "color": q.color,
                        "avatar": q.avatar, "cpm": round(q.cpm),
                        "msPerChar": q.ms_per_char})
        return out


def make_handler(session: Session):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):          # 别把访问日志刷到终端
            pass

        # -------------------------------------------------- 静态文件
        def _serve_file(self, rel: str) -> bool:
            safe = os.path.normpath(rel).lstrip("/")
            full = os.path.join(WEB_DIR, safe)
            try:
                inside = os.path.commonpath([os.path.abspath(full), WEB_DIR]) == WEB_DIR
            except ValueError:
                inside = False
            if not inside or not os.path.isfile(full):
                return False
            with open(full, "rb") as f:
                body = f.read()
            ctype = MIME.get(os.path.splitext(full)[1], "application/octet-stream")
            # 首页把初始状态直接嵌进去：首屏不用等异步请求，省掉一次白屏闪
            if safe == "index.html":
                boot = json.dumps(session.snapshot(), ensure_ascii=False).replace("</", "<\\/")
                body = body.replace(b"<!--BOOT-->",
                                    f"<script>window.__BOOT__={boot}</script>".encode())
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return True

        def _json(self, obj, code=200):
            body = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        # -------------------------------------------------- GET
        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/api/events":
                return self._events()
            if path in ("/", "/index.html"):
                return None if self._serve_file("index.html") else self._json({}, 404)
            if self._serve_file(path):
                return
            self._json({"error": "not found"}, 404)

        def _events(self):
            q: queue.Queue = queue.Queue(maxsize=256)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            session.clients.add(q)
            # 没人看的时候不该自己往前跑：有客户端在就播，最后一个走了就暂停。
            # 但"用户自己按的暂停"要尊重 —— 所以只看 user_paused，不看当前播放状态。
            if not session.user_paused:
                session._started = True
                session.command("play")
            try:
                self._write_event([session.snapshot()])
                while not session.stopping:
                    try:
                        _kind, batch = q.get(timeout=15)
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                        continue
                    self._write_event(batch)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                session.clients.discard(q)
                if not session.clients and session._started:
                    # 注意：这里绕开 command()，因为自动暂停不算"用户按了暂停"
                    with session.lock:
                        session.player.pause()
                        session._push(session.status_payload())
                        session._flush()

        def _write_event(self, batch):
            data = json.dumps(batch, ensure_ascii=False)
            self.wfile.write(f"data: {data}\n\n".encode())
            self.wfile.flush()

        # -------------------------------------------------- POST
        def do_POST(self):
            if urlparse(self.path).path != "/api/cmd":
                return self._json({"error": "not found"}, 404)
            n = int(self.headers.get("Content-Length") or 0)
            try:
                payload = json.loads(self.rfile.read(n) or b"{}")
            except json.JSONDecodeError:
                return self._json({"error": "bad json"}, 400)
            cmd = payload.pop("cmd", "")
            try:
                session.command(cmd, **payload)
            except Exception as e:                       # noqa: BLE001
                return self._json({"error": repr(e)}, 400)
            self._json({"ok": True, **session.status_payload()})

    return Handler


def serve(path: str, port: int = 8770, mood_mode: str = "local", speed: float = 1.0,
          fly_seconds: int = 300, mood_cache: str | None = None,
          open_browser: bool = True, block: bool = True):
    session = Session(path, mood_mode, speed, fly_seconds, mood_cache)

    httpd = None
    for p in range(port, port + 20):
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", p), make_handler(session))
            port = p
            break
        except OSError:
            continue
    if httpd is None:
        raise SystemExit(f"端口 {port}–{port+19} 都被占用了")

    url = f"http://127.0.0.1:{port}/"
    print(f"聊书 · {os.path.basename(path)}")
    print(f"  {len(session.messages)} 条消息 · {len(session._cast_list())} 个角色 "
          f"· 节奏源 {session.mood_mode}")
    print(f"  → {url}")
    print("  Ctrl-C 退出")

    if not block:
        # 内嵌模式：httpd 由调用方持有，退出循环交给桌面窗口管
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return url, httpd

    if open_browser:
        threading.Timer(0.6, lambda: _open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n再见")
    finally:
        session.stopping = True
        httpd.server_close()


def _open(url: str) -> None:
    import webbrowser
    try:
        webbrowser.open(url)
    except Exception:                                    # noqa: BLE001
        pass
