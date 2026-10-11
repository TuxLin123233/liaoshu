"""把一本 txt 电子书解析成"群聊消息"序列。

两件事：切分旁白 / 对话，以及给每句对话找到说话人。

说话人判定分两遍：
  第一遍只用**紧贴引号、两端锚定**的"名字+言语动词"抽出一张演员表；
  第二遍拿演员表去解析模糊的（代词、上下文提到、纯轮替）。

为什么要锚定：中文里 道 / 想 / 念 / 应 这些单字动词满地都是
（知道、想到、应该），如果用"整段扫名字+动词"的松匹配，
会把"他说"、"知道他"这种统统当成角色名——实测第一版就是这么翻车的。
"""

from __future__ import annotations

import re
from collections import Counter, deque
from dataclasses import dataclass

# --------------------------------------------------------------------------
# 数据结构
# --------------------------------------------------------------------------

EXPLICIT, CONTEXT, GUESS = "explicit", "context", "guess"


@dataclass
class Message:
    kind: str                       # dialogue | narration | chapter
    text: str
    speaker: str | None = None
    confidence: str = EXPLICIT      # explicit / context / guess
    para: int = 0

    @property
    def inferred(self) -> bool:
        return self.confidence != EXPLICIT


# --------------------------------------------------------------------------
# 引号扫描（带偏移量，署名判定要靠它贴着引号找）
# --------------------------------------------------------------------------

QUOTE_PAIRS = {"「": "」", "『": "』", "“": "”", "‘": "’", "〈": "〉"}
CLOSERS = set(QUOTE_PAIRS.values())


def split_quotes(text: str):
    """返回 ([(是否对话, 文本, 起始偏移, 结束偏移)], 段末仍未闭合的引号)。

    支持跨段未闭合的引号：中文长对白常写成
        “前半句……
        “后半句。”
    第二段开头那个多余引号会被吃掉。
    """
    segs: list[tuple[bool, str, int, int]] = []
    buf: list[str] = []
    start = 0
    closer: str | None = None

    def flush(is_quote: bool, end: int):
        nonlocal start
        s = "".join(buf).strip()
        if s:
            segs.append((is_quote, s, start, end))
        buf.clear()

    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if closer is None:
            if ch in QUOTE_PAIRS or ch == '"':
                flush(False, i)
                closer = QUOTE_PAIRS.get(ch, '"')
                i += 1
                start = i
                continue
            if not buf:
                start = i
            buf.append(ch)
        else:
            if ch == closer:
                flush(True, i)
                closer = None
                i += 1
                continue
            if not buf:
                start = i
            buf.append(ch)
        i += 1

    flush(closer is not None, n)
    return segs, closer


# --------------------------------------------------------------------------
# 署名抽取
# --------------------------------------------------------------------------

SPEECH_VERBS = [
    "低声说道", "轻声说道", "冷冷地说道", "笑着说道", "解释道", "补充道", "反问道",
    "开口说", "接话道", "说道", "问道", "答道", "笑道", "喊道", "叫道", "应道",
    "回道", "叹道", "骂道", "吼道", "嘟囔", "嘀咕", "喃喃", "开口", "接话",
    "插话", "回答", "反问", "提醒", "宣布", "反驳", "承认", "否认", "纠正",
    "附和", "吐槽", "解释", "低声说", "轻声说", "笑着说", "冷冷地说",
    "继续说", "接着说", "说", "道", "问", "答", "喊", "叫", "念",
    "said", "asked", "replied", "answered", "whispered", "shouted", "muttered",
    "added", "continued", "cried", "called", "remarked", "exclaimed", "sighed",
]
_VERB_ALT = "|".join(sorted(map(re.escape, SPEECH_VERBS), key=len, reverse=True))

_ADV = (r"(?:也|都|又|才|就|便|则|却|还|终于|于是|然后|接着|随即|忽然|突然|"
        r"立刻|马上|忙|连忙|赶紧|笑着|冷笑|苦笑|微笑|叹气|叹|低声|轻声|小声|"
        r"大声|冷冷地|淡淡地|平静地|缓缓|慢慢|无奈地|认真地|点头|摇头|顿了顿|"
        r"顿了一下|想了想|沉默片刻|沉默|终于|只好|只得)")
_NAME = r"(?:[\u4e00-\u9fff]{2,4}|[A-Z][a-zA-Z]{1,14})"
# 名字和动词之间允许夹的东西：不能跨句末标点、不能跨引号，
# 否则 `他压低声音，“你之前说` 会被凑成一个人叫"他压"。
_MID = r"[^。！？\n“”「」『』\"]"

# 贴着引号后面：「随便。」他说 / 「随便。」阿岩说
_AFTER_RE = re.compile(rf"^(?P<name>{_NAME})(?:{_ADV})*(?P<v>{_VERB_ALT})")
_AFTER_LOOSE_RE = re.compile(rf"^(?P<name>{_NAME}){_MID}{{1,12}}?(?P<v>{_VERB_ALT})")
# 贴着引号前面：阿岩说：/ 阿岩把杯子放下说：
_BEFORE_RE = re.compile(rf"(?P<name>{_NAME})(?:{_ADV})*(?P<v>{_VERB_ALT})\s*[：:，,]?\s*$")
_BEFORE_LOOSE_RE = re.compile(
    rf"(?P<name>{_NAME}){_MID}{{1,12}}?(?P<v>{_VERB_ALT})\s*[：:，,]?\s*$")

_PRONOUNS = {"他", "她", "它", "我", "你", "他们", "她们", "咱们", "我们", "你们", "对方"}
_BAD_NAME_CHARS = set("的了着是不没把被在和与这那就都也很才又啊吧呢吗哦呀嗯得好让给向从对为")
_NOT_NAMES = {"知道", "说道", "想道", "应该", "回答", "开口", "他们", "她们", "众人",
              "所有", "有人", "大家", "什么", "怎么", "这么", "那么", "自己", "东西",
              "时候", "已经", "可以", "因为", "所以", "但是", "如果", "这个", "那个"}
# 纯署名的旁白片段（不该单独显示成一条旁白）
_PURE_ATTR_RE = re.compile(rf"^[^。！？\n]{{0,8}}?(?:{_VERB_ALT})\s*[，,。]?$")


def _plausible_name(s: str) -> bool:
    if not s:
        return False
    if s in _PRONOUNS:
        return True
    if not re.fullmatch(r"[\u4e00-\u9fff]{2,4}|[A-Z][a-zA-Z]{1,14}", s):
        return False
    if s in _NOT_NAMES:
        return False
    if any(c in _BAD_NAME_CHARS for c in s):
        return False
    return True


def _clean(pre: str) -> str | None:
    """把'名字+状语'洗成名字。"""
    pre = pre.strip()
    prev = None
    while prev != pre:
        prev = pre
        pre = re.sub(rf"(?:{_ADV})+$", "", pre)
    if not pre:
        return None
    if pre in _PRONOUNS:
        return pre
    return pre if _plausible_name(pre) else None


def name_after(window: str) -> str | None:
    """引号**紧后面**的署名。窗口必须从引号后面开始。"""
    if not window:
        return None
    m = _AFTER_RE.match(window) or _AFTER_LOOSE_RE.match(window)
    return _clean(m.group("name")) if m else None


def name_before(window: str) -> str | None:
    """引号**紧前面**的署名。窗口必须以引号前一个字符结束。"""
    if not window:
        return None
    m = _BEFORE_RE.search(window) or _BEFORE_LOOSE_RE.search(window)
    return _clean(m.group("name")) if m else None


def cast_in(text: str, cast: set[str], window: int | None = None,
            prefer_last: bool = True) -> str | None:
    """在一段文字里找演员表成员。"""
    if not text or not cast:
        return None
    if window:
        text = text[-window:] if prefer_last else text[:window]
    hits = sorted((text.rfind(n), n) for n in cast if n in text)
    hits = [h for h in hits if h[0] >= 0]
    return hits[-1][1] if hits else None


def label_before(window: str, cast: set[str]) -> str | None:
    """`名字：` 这种直接署名的形式（演员表已知时才好判）。"""
    if not window:
        return None
    for name in cast:
        idx = window.rfind(name)
        if idx >= 0 and len(window) - idx <= len(name) + 8 and window.rstrip()[-1:] in "：:":
            return name
    m = re.search(rf"(?P<name>{_NAME})\s*[：:]\s*$", window)
    if m and _plausible_name(m.group("name")):
        return m.group("name")
    return None


# --------------------------------------------------------------------------
# 版面处理
# --------------------------------------------------------------------------

_CHAPTER_RE = re.compile(
    r"^\s*(?:第\s*[0-9一二三四五六七八九十百千零〇两]+\s*[章节回卷部篇幕]"
    r"|Chapter\s+\d+|CHAPTER\s+\d+|序章|楔子|尾声|后记|番外)")
_SENT_END_CHARS = "。！？…"


def _looks_hard_wrapped(lines: list[str]) -> bool:
    """txt 电子书常见"按固定列宽硬换行"，要先把断行接回去。"""
    lens = [len(l) for l in lines if len(l) >= 10]
    if len(lens) < 12:
        return False
    common, count = Counter(lens).most_common(1)[0]
    if common < 24 or count / len(lens) < 0.22:
        return False
    tail = sum(1 for l in lines
               if len(l) in (common, common - 1, common + 1)
               and l and l[-1] not in "。！？…”』」")
    return tail / max(count, 1) > 0.6


def to_paragraphs(raw: str) -> list[str]:
    raw = raw.replace("\r\n", "\n").replace("\r", "\n").replace("\u3000", " ")
    lines = [l.rstrip() for l in raw.split("\n")]

    if _looks_hard_wrapped(lines):
        merged, buf = [], ""
        for l in lines:
            if not l.strip():
                if buf:
                    merged.append(buf)
                    buf = ""
                continue
            buf += l.strip()
            if buf and buf[-1] in "。！？…”』」":
                merged.append(buf)
                buf = ""
        if buf:
            merged.append(buf)
        return [p for p in merged if p.strip()]

    paras, buf = [], []
    for l in lines:
        if not l.strip():
            if buf:
                paras.append("".join(buf).strip())
                buf = []
            continue
        buf.append(l.strip())
        # 中文 txt 大多一行一段；只有上一行明显没写完才续行
        if buf[-1][-1] in "。！？…”』」：:；;":
            paras.append("".join(buf).strip())
            buf = []
    if buf:
        paras.append("".join(buf).strip())
    return [p for p in paras if p.strip()]


def split_sentences(text: str, max_len: int = 80) -> list[str]:
    """长旁白拆成几条短消息，免得在聊天流里糊成一堵墙。"""
    parts = [p.strip() for p in re.split(rf"(?<=[{_SENT_END_CHARS}])", text) if p.strip()]
    out: list[str] = []
    for p in parts:
        while len(p) > max_len:
            cut = max(p.rfind("，", 0, max_len), p.rfind("、", 0, max_len))
            cut = cut + 1 if cut > max_len * 0.4 else max_len
            out.append(p[:cut])
            p = p[cut:]
        if p:
            out.append(p)
    return out


# --------------------------------------------------------------------------
# 主解析
# --------------------------------------------------------------------------

def _windows(paras: list[str]):
    """产出每段里的 (段号, 段文本, [(是否对话, 文本, 前窗, 后窗)], 段末未闭合的引号)。

    前窗 = 开引号**之前**的文字，后窗 = 闭引号**之后**的文字。
    偏移量 s 指向引号内容的第一颗字，e 指向闭合引号本身，
    所以前窗切到 s-1（不含开引号），后窗从 e+1 开始（不含闭引号）。
    """
    pending: str | None = None
    for pi, p in enumerate(paras):
        segs, pending = split_quotes(p)
        items = []
        for is_quote, seg, s, e in segs:
            before = p[max(0, s - 23):s - 1] if s > 0 else ""
            after = p[e + 1:e + 21]
            items.append((is_quote, seg, before, after))
        yield pi, p, items, pending


_CHATLOG_RE = re.compile(r"^(?P<name>[\u4e00-\u9fffA-Za-z·]{1,6})\s*[：:]\s*(?P<text>\S.*)$")


def _chatlog_line(p: str) -> tuple[str, str] | None:
    """`名字：内容` 这种不带引号的聊天记录体。"""
    if any(c in p for c in QUOTE_PAIRS) or '"' in p:
        return None
    m = _CHATLOG_RE.match(p.strip())
    if m and _plausible_name(m.group("name")):
        return m.group("name"), m.group("text").strip()
    return None


def _resolve(seg, before, after, narr_all, cast, recent, gender):
    """按可信度从高到低找说话人。返回 (名字, 可信度)。"""
    pronoun = None

    # 1) 引号前紧贴的署名（阿岩说：/ 阿岩把杯子放下说：）
    cand = name_before(before)
    if cand in _PRONOUNS:
        pronoun = cand
    elif cand:
        return cand, EXPLICIT

    # 2) 引号后紧贴的署名（「随便。」阿岩说）
    cand = name_after(after)
    if cand in _PRONOUNS:
        pronoun = pronoun or cand
    elif cand:
        return cand, EXPLICIT

    # 3) 引号后 14 字内提到的人（「……」阿岩的声音从驾驶席传来）
    cand = cast_in(after, cast, window=14, prefer_last=False)
    if cand:
        return cand, CONTEXT

    # 4) 引号前 22 字内提到的人
    cand = cast_in(before, cast, window=22)
    if cand:
        return cand, CONTEXT

    # 5) 本段旁白里提到的人
    cand = cast_in(narr_all, cast)
    if cand:
        return cand, CONTEXT

    # 6) `名字：` 直接署名
    cand = label_before(before, cast)
    if cand:
        return cand, CONTEXT

    # 7) 代词 → 最近说过话的人（尽量同性）
    if pronoun:
        want = "女" if pronoun in ("她", "她们") else ("男" if pronoun in ("他", "他们") else None)
        pool = [x for x in recent if want is None or gender.get(x) == want]
        if pool:
            return pool[-1], GUESS
        if recent:
            return recent[-1], GUESS

    # 8) 纯轮替：群里两个人你一句我一句
    if len(recent) >= 2:
        return recent[0], GUESS
    if recent:
        return recent[-1], GUESS
    return None, GUESS


def _note_speaker(speaker, para, recent, gender):
    if not speaker:
        return
    if not recent or recent[-1] != speaker:
        recent.append(speaker)
    if speaker not in gender:
        # 从旁白里粗略推断性别，代词消解要用
        if re.search(rf"{re.escape(speaker)}[^。！？]{{0,6}}她", para):
            gender[speaker] = "女"
        elif re.search(rf"{re.escape(speaker)}[^。！？]{{0,6}}他", para):
            gender[speaker] = "男"


def parse(raw: str, keep_narration: bool = True, narration_max_len: int = 80) -> list[Message]:
    paras = to_paragraphs(raw)

    # 聊天记录体只在"整篇都是"的时候才启用，避免把 注意：/提示： 当成角色
    chatlog = [_chatlog_line(p) for p in paras]
    n_chat = sum(1 for c in chatlog if c)
    use_chatlog = n_chat >= 3 and n_chat / max(len(paras), 1) >= 0.5

    # ---- 第一遍：只用两端锚定的显式署名建演员表 ----
    counter: Counter[str] = Counter()
    for pi, _p, items, _pending in _windows(paras):
        if use_chatlog and chatlog[pi]:
            counter[chatlog[pi][0]] += 1
            continue
        for is_quote, _seg, before, after in items:
            if not is_quote:
                continue
            for cand in (name_after(after), name_before(before)):
                if cand and cand not in _PRONOUNS:
                    counter[cand] += 1
    cast = {n for n, c in counter.items() if c >= 2} or set(counter)

    # ---- 第二遍：逐句定说话人 ----
    messages: list[Message] = []
    recent: deque[str] = deque(maxlen=3)
    gender: dict[str, str] = {}
    open_idx: int | None = None       # 上一段没闭合、等着续写的对白

    for pi, p, items, pending in _windows(paras):
        if use_chatlog and chatlog[pi]:
            name, text = chatlog[pi]
            messages.append(Message("dialogue", text, name, EXPLICIT, pi))
            _note_speaker(name, p, recent, gender)
            open_idx = None
            continue

        items = list(items)
        # 续上一段没闭合的对白：把这一段的第一句接回去
        if open_idx is not None and items and items[0][0]:
            _q, seg, before, after = items[0]
            narr_here = " ".join(s for q, s, _b, _a in items if not q)
            messages[open_idx].text += seg
            if messages[open_idx].speaker is None or messages[open_idx].confidence != EXPLICIT:
                cand, conf = _resolve(seg, before, after, narr_here, cast, recent, gender)
                if cand:
                    messages[open_idx].speaker = cand
                    messages[open_idx].confidence = conf
            items = items[1:]

        narr_all = " ".join(seg for q, seg, _b, _a in items if not q)
        for is_quote, seg, before, after in items:
            if not is_quote:
                if not keep_narration:
                    continue
                seg = seg.strip()
                if not seg or _PURE_ATTR_RE.match(seg) or len(seg) <= 1:
                    continue           # 纯署名片段，别当成旁白刷屏
                seg = re.sub(r"[：:]\s*$", "", seg)
                for s in split_sentences(seg, narration_max_len):
                    messages.append(Message("narration", s, None, EXPLICIT, pi))
                continue

            speaker, conf = _resolve(seg, before, after, narr_all, cast, recent, gender)
            _note_speaker(speaker, p, recent, gender)
            messages.append(Message("dialogue", seg, speaker, conf, pi))

        # 段末仍未闭合 → 记住这一条，下一段接着写
        last = messages[-1] if messages else None
        if (pending and last is not None and last.kind == "dialogue"
                and last.para == pi and items and items[-1][0]):
            open_idx = len(messages) - 1
        else:
            open_idx = None

    # ---- 章节标题单独成条 ----
    out: list[Message] = []
    chapter_seen: set[int] = set()
    for pi, p in enumerate(paras):
        if _CHAPTER_RE.match(p) and len(p) <= 40:
            out.append(Message("chapter", p.strip(), None, EXPLICIT, pi))
            chapter_seen.add(pi)
    merged = sorted(out + [m for m in messages if not (m.kind == "narration" and m.para in chapter_seen and m.text.strip() in {q.text for q in out})],
                    key=lambda m: (m.para, 0 if m.kind == "chapter" else 1))
    return merged


def parse_file(path: str, encoding: str = "utf-8", **kw) -> list[Message]:
    for enc in (encoding, "utf-8-sig", "gb18030", "big5", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as f:
                return parse(f.read(), **kw)
        except (UnicodeDecodeError, LookupError):
            continue
    raise UnicodeDecodeError("liaoshu", b"", 0, 1, f"无法识别编码: {path}")


if __name__ == "__main__":
    import sys

    msgs = parse_file(sys.argv[1])
    speakers = Counter(m.speaker for m in msgs if m.kind == "dialogue")
    print(f"共 {len(msgs)} 条消息")
    print(f"  对话 {sum(1 for m in msgs if m.kind=='dialogue')}"
          f" | 旁白 {sum(1 for m in msgs if m.kind=='narration')}"
          f" | 章节 {sum(1 for m in msgs if m.kind=='chapter')}"
          f" | 猜的署名 {sum(1 for m in msgs if m.confidence==GUESS)}")
    print("说话人:", speakers.most_common(15))
    for m in msgs[:30]:
        tag = {"dialogue": f"[{m.speaker}{'?' if m.inferred else ''}]",
               "narration": "[旁白]", "chapter": "[章节]"}[m.kind]
        print(f"  {tag} {m.text[:46]}")
