/* 聊书 · liaoshu — 前端
 *
 * 这里只负责"画"和"响"。解析、说话人判定、打字节奏全在 Python 那边，
 * 通过 SSE 推过来（init / state / char / sent / seek / progress / status）。
 */

const $ = (id) => document.getElementById(id);

/* ==========================================================================
 * 音效：全部用 Web Audio 现场合成，零音频文件
 *
 * 打字声不能用"一个按键一个文件"的思路做——那样既占地方又单调。
 * 正确做法是：一段白噪声 + 随机带通频率 + 极快包络 = 一次按键声，
 * 每次随机化滤波频率、增益、衰减和音高，就得到无穷多种"不同的敲击"。
 * 底层再叠一个 170Hz 往下扫的三角波当"闷响"，听起来才像机械键盘而不是电子音。
 *
 * 整个音效系统的内存占用 = 一段 60ms 的白噪声 buffer（约 5KB）。
 * ========================================================================== */
const Sfx = {
  ctx: null, master: null, noise: null, on: false, vol: 0.5, lastClick: 0,

  ensure() {
    if (this.ctx) return true;
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return false;
    this.ctx = new AC();
    this.master = this.ctx.createGain();
    this.master.gain.value = this.vol;
    this.master.connect(this.ctx.destination);
    const n = Math.floor(this.ctx.sampleRate * 0.06);
    const buf = this.ctx.createBuffer(1, n, this.ctx.sampleRate);
    const d = buf.getChannelData(0);
    for (let i = 0; i < n; i++) d[i] = Math.random() * 2 - 1;
    this.noise = buf;
    return true;
  },

  async enable() {
    if (!this.ensure()) return false;
    try { await this.ctx.resume(); } catch (e) { /* 忽略 */ }
    this.on = true;
    return true;
  },
  disable() { this.on = false; },
  toggle() { return this.on ? (this.disable(), false) : (this.enable(), true); },

  // 一次按键：白噪声过带通 + 低频闷响
  click() {
    if (!this.on || !this.ctx) return;
    const now = performance.now();
    if (now - this.lastClick < 20) return;      // 限流：高速打字时别变成机枪
    this.lastClick = now;
    const t = this.ctx.currentTime;

    const src = this.ctx.createBufferSource();
    src.buffer = this.noise;
    src.playbackRate.value = 0.9 + Math.random() * 0.35;
    const bp = this.ctx.createBiquadFilter();
    bp.type = 'bandpass'; bp.Q.value = 1.0;
    bp.frequency.value = 1500 + Math.random() * 2200;
    const hp = this.ctx.createBiquadFilter();
    hp.type = 'highpass'; hp.frequency.value = 700;
    const g = this.ctx.createGain();
    const peak = 0.20 + Math.random() * 0.14;
    const dur = 0.012 + Math.random() * 0.014;
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(peak, t + 0.0015);
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    src.connect(bp); bp.connect(hp); hp.connect(g); g.connect(this.master);
    src.start(t); src.stop(t + dur + 0.02);

    const o = this.ctx.createOscillator();
    o.type = 'triangle';
    o.frequency.setValueAtTime(170 + Math.random() * 60, t);
    o.frequency.exponentialRampToValueAtTime(70, t + 0.03);
    const og = this.ctx.createGain();
    og.gain.setValueAtTime(0.085 + Math.random() * 0.05, t);
    og.gain.exponentialRampToValueAtTime(0.0001, t + 0.035);
    o.connect(og); og.connect(this.master);
    o.start(t); o.stop(t + 0.05);
  },

  // 一条消息送达：两音小提示音
  pop() {
    if (!this.on || !this.ctx) return;
    const t = this.ctx.currentTime;
    [[880, 0, 0.055], [1320, 0.045, 0.07]].forEach(([f, at, len]) => {
      const o = this.ctx.createOscillator();
      o.type = 'sine'; o.frequency.value = f;
      const g = this.ctx.createGain();
      g.gain.setValueAtTime(0.0001, t + at);
      g.gain.exponentialRampToValueAtTime(0.10, t + at + 0.008);
      g.gain.exponentialRampToValueAtTime(0.0001, t + at + len);
      o.connect(g); g.connect(this.master);
      o.start(t + at); o.stop(t + at + len + 0.02);
    });
  },

  // 新章节：三个音的小琶音
  chime() {
    if (!this.on || !this.ctx) return;
    const t = this.ctx.currentTime;
    [523.25, 659.25, 783.99].forEach((f, i) => {
      const at = t + i * 0.075;
      const o = this.ctx.createOscillator();
      o.type = 'sine'; o.frequency.value = f;
      const g = this.ctx.createGain();
      g.gain.setValueAtTime(0.0001, at);
      g.gain.exponentialRampToValueAtTime(0.085, at + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, at + 0.34);
      o.connect(g); g.connect(this.master);
      o.start(at); o.stop(at + 0.4);
    });
  },
};

/* ========================================================================== */
const S = {
  messages: [], personas: {}, cast: [], chapters: [],
  rows: new Map(), inner: null, stream: null,
  delivered: 0, shown: 0, total: 0, playing: false, narration: true,
  auto: true, prefix: [], typingEl: null, scrubbing: false, partial: null,
  bookId: null, _restored: false, _savedAt: 0,
};

/* 小窗模式：只显示打字流。开着时视口永远贴底，绝不跟丢正在打的那句。 */
let MINI = false;

const MAX_ROWS = 260;
const PAGE = 200;

/* ---------------------------------------------------------------- 图标
 * 用内联 SVG 而不是 emoji：emoji 会被系统渲染成彩色图形，
 * 和这套克制的线性风格不搭，而且在各个平台上长得都不一样。
 */
const ICONS = {
  play: '<polygon points="5.5,3.5 12.5,8 5.5,12.5" fill="currentColor" stroke="none"/>',
  pause: '<rect x="4.6" y="3.5" width="2.4" height="9" rx="1" fill="currentColor" stroke="none"/>'
       + '<rect x="9" y="3.5" width="2.4" height="9" rx="1" fill="currentColor" stroke="none"/>',
  rewind: '<path d="M7.5 3.2 3.2 7.5l4.3 4.3"/><path d="M3.6 7.5h6.2a3.9 3.9 0 0 1 0 7.8H6.4"/>',
  skip: '<path d="M4.2 3.4 10 8l-5.8 4.6z" fill="currentColor" stroke="none"/>'
      + '<path d="M11.8 3.6v8.8"/>',
  restart: '<path d="M3.4 3.4v9.2"/><path d="M12.6 3.4 6.4 8l6.2 4.6z" fill="currentColor" stroke="none"/>',
  sound: '<path d="M3.6 6.2v3.6h2.3L9 12.4V3.6L5.9 6.2z"/>'
       + '<path d="M11.4 6.4a2.4 2.4 0 0 1 0 3.2"/><path d="M13.2 4.8a4.6 4.6 0 0 1 0 6.4"/>',
  mute: '<path d="M3.6 6.2v3.6h2.3L9 12.4V3.6L5.9 6.2z"/>'
      + '<path d="M11.6 6.6l3 2.8M14.6 6.6l-3 2.8"/>',
  theme: '<circle cx="8" cy="8" r="5.4"/><path d="M8 2.6a5.4 5.4 0 0 0 0 10.8z" fill="currentColor" stroke="none"/>',
  gear: '<circle cx="8" cy="8" r="2.3"/>'
      + '<path d="M8 1.7v1.5M8 12.8v1.5M1.7 8h1.5M12.8 8h1.5'
      + 'M3.5 3.5l1.1 1.1M11.4 11.4l1.1 1.1M12.5 3.5l-1.1 1.1M4.6 11.4l-1.1 1.1"/>',
  mini: '<rect x="2.4" y="3" width="11.2" height="10" rx="1.8"/>'
      + '<path d="M2.4 5.7h11.2"/>',
};

function svg(name, size) {
  size = size || 14;
  return `<svg viewBox="0 0 16 16" width="${size}" height="${size}" fill="none"`
    + ` stroke="currentColor" stroke-width="1.5" stroke-linecap="round"`
    + ` stroke-linejoin="round" style="vertical-align:-2px">${ICONS[name]}</svg>`;
}

function setPlayBtn(playing) {
  $('play').innerHTML = playing ? svg('pause') + ' 暂停' : svg('play') + ' 播放';
}

/* ---------------------------------------------------------------- 工具 */
function personaOf(m) {
  if (m.k === 'dialogue' && m.s && S.personas[m.s]) return S.personas[m.s];
  return { color: 'var(--dsw-alias-label-secondary)', avatar: '❖', cpm: 600 };
}

function pad(n) { return String(n).padStart(2, '0'); }

// 时间戳是客户端按"字数和角色语速"累加估出来的，纯装饰（和真实节奏无关）
function buildClock() {
  S.prefix = new Array(S.messages.length);
  let acc = 0;
  for (let i = 0; i < S.messages.length; i++) {
    const m = S.messages[i];
    S.prefix[i] = acc;
    const msPerChar = m.k === 'dialogue' ? (60000 / (personaOf(m).cpm || 600)) : 62;
    acc += m.x.length * msPerChar + (m.k === 'dialogue' ? 900 : 450);
  }
}
function clockOf(idx) {
  const sec = (S.prefix[idx] || 0) / 1000 + 20 * 3600;
  return `${pad(Math.floor(sec / 3600) % 24)}:${pad(Math.floor(sec / 60) % 60)}`;
}

/* ---------------------------------------------------------------- 渲染 */
function makeRow(idx) {
  const m = S.messages[idx];
  const row = document.createElement('div');
  let bubble = null;

  if (m.k === 'chapter') {
    row.className = 'row chap';
    const d = document.createElement('div');
    d.className = 'chap-in'; d.textContent = m.x;
    row.appendChild(d);
  } else if (m.k === 'narration') {
    row.className = 'row narr';
    bubble = document.createElement('div');
    bubble.className = 'bubble'; bubble.textContent = '';
    row.appendChild(bubble);
  } else {
    row.className = 'row';
    const p = personaOf(m);
    const av = document.createElement('div');
    av.className = 'avatar';
    av.style.background = p.color;
    av.textContent = p.avatar;
    row.appendChild(av);

    const body = document.createElement('div');
    body.className = 'body';
    const head = document.createElement('div');
    head.className = 'head';
    const name = document.createElement('b');
    name.textContent = m.s || '??';
    name.style.color = p.color;
    head.appendChild(name);
    if (m.g) {
      const tag = document.createElement('span');
      tag.className = 'guess'; tag.textContent = '推断';
      head.appendChild(tag);
    }
    const time = document.createElement('time');
    time.textContent = clockOf(idx);
    head.appendChild(time);
    body.appendChild(head);

    bubble = document.createElement('div');
    bubble.className = 'bubble'; bubble.textContent = '';
    body.appendChild(bubble);
    row.appendChild(body);
  }

  row._bubble = bubble;
  S.rows.set(idx, row);
  S.inner.appendChild(row);
  return row;
}

function ensureRow(idx) {
  const el = S.rows.get(idx);
  if (!el) {
    const row = makeRow(idx);
    trim();
    return row;
  }
  return el;
}

function trim() {
  if (S.rows.size <= MAX_ROWS) return;
  const keys = [...S.rows.keys()].sort((a, b) => a - b);
  for (const k of keys.slice(0, S.rows.size - MAX_ROWS)) {
    S.rows.get(k).remove();
    S.rows.delete(k);
  }
}

function renderRange(from, to, full) {
  for (let i = from; i < to; i++) {
    if (!S.narration && S.messages[i].k === 'narration') continue;
    const row = ensureRow(i);
    if (full && row._bubble) row._bubble.textContent = S.messages[i].x;
  }
}

function dropFrom(idx) {
  for (const k of [...S.rows.keys()]) {
    if (k >= idx) { S.rows.get(k).remove(); S.rows.delete(k); }
  }
}

function nearBottom() {
  const s = S.stream;
  return s.scrollHeight - s.scrollTop - s.clientHeight < 90;
}
function maybeScroll(force) {
  if (!force && !S.auto && !MINI) return;
  S.auto = true;
  // 等这一帧的布局落定再滚，否则新加的一行还没量高度，会差一截
  requestAnimationFrame(() => { S.stream.scrollTop = S.stream.scrollHeight; });
}

/* ---------------------------------------------------------------- 小窗模式
 * 独立的一个小窗口，只留打字流：侧栏、进度条收起，视口永远贴着正在打的那句。
 * 点顶栏的小窗按钮开一个弹窗；弹窗被拦（或已在浏览器标签里）时就地折叠。
 */
function applyMini(on) {
  MINI = on;
  const app = document.querySelector('.app');
  if (app) app.classList.toggle('mini', on);
  const b = $('mini');
  if (b) b.title = on ? '退出小窗' : '小窗模式（只显示打字）';
  if (b) b.classList.toggle('on', on);
  maybeScroll(true);
}

function openMini() {
  if (MINI) {
    window.close();                          // 自己开的窗口能关；关不掉就退化成还原
    setTimeout(() => applyMini(false), 60);
    return;
  }
  const w = window.open(location.pathname + '?mini=1', 'liaoshu-mini',
                        'popup=yes,width=380,height=620');
  if (!w) applyMini(true);                   // 弹窗被拦截 → 就地折叠
}

/* ---------------------------------------------------------------- 正在输入 */
function showTyping(speaker) {
  hideTyping();
  const p = S.personas[speaker] || { color: 'var(--dsw-alias-label-secondary)', avatar: '?' };
  const row = document.createElement('div');
  row.className = 'row typing';
  const av = document.createElement('div');
  av.className = 'avatar sm';
  av.style.background = p.color;
  av.textContent = p.avatar;
  const body = document.createElement('div');
  body.className = 'body';
  const b = document.createElement('div');
  b.className = 'bubble';
  b.innerHTML = `${escapeHtml(speaker || '')} 正在输入<span class="dots"></span>`;
  body.appendChild(b);
  row.appendChild(av); row.appendChild(body);
  S.inner.appendChild(row);
  S.typingEl = row;
  maybeScroll();
}
function hideTyping() {
  if (S.typingEl) { S.typingEl.remove(); S.typingEl = null; }
}
function escapeHtml(s) {
  return String(s).replace(/[&<>"]/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

/* ---------------------------------------------------------------- 事件处理 */
function handle(ev) {
  switch (ev.t) {
    case 'init': return onInit(ev);
    case 'state':
      if (ev.state === 'thinking' && ev.kind === 'dialogue') showTyping(ev.speaker);
      else hideTyping();
      if (ev.state === 'done') setPlayBtn(false);
      return;
    case 'char': return onChar(ev.idx, ev.n);
    case 'sent': return onSent(ev.idx);
    case 'seek': return onSeek(ev.delivered);
    case 'progress': return onProgress(ev.delivered, ev.total);
    case 'status': return onStatus(ev);
    case 'mood': return onMood(ev);
    case 'log': return;
    case 'error': return console.warn('server', ev.text);
  }
}

/* 续读：按书 id 把"读到第几条"存在本地，下次打开自动接上 */
function posKey(id) { return 'liaoshu.pos.' + id; }

function saveProgress(force) {
  if (!S.bookId || !S._restored) return;   // 恢复动作完成前不落盘，免得把手头存档抹了
  const now = Date.now();
  if (!force && now - S._savedAt < 1000) return;   // 自动落盘最多每秒一次
  S._savedAt = now;
  try {
    if (S.delivered <= 0 || (S.total && S.delivered >= S.total)) {
      localStorage.removeItem(posKey(S.bookId));   // 开头或读完 → 不记
    } else {
      localStorage.setItem(posKey(S.bookId), String(S.delivered));
    }
  } catch (e) { /* 无痕模式 */ }
}

function restoreProgress(d) {
  if (!d.bookId) return;
  let saved = 0;
  try { saved = parseInt(localStorage.getItem(posKey(d.bookId)), 10) || 0; } catch (e) {}
  // 只在"全新会话还停在开头"时恢复；重连/进行中的会话不打断
  if (saved > 0 && saved < d.total && d.delivered === 0) {
    S._resumeTo = saved;
    send('seek', { delivered: saved, resume: false });
    $('book').textContent = `${d.book} · 续读到 ${saved}/${d.total}`;
  }
}

function onInit(d) {
  S.messages = d.messages; S.personas = d.personas;
  S.cast = d.cast; S.chapters = d.chapters; S.total = d.total;
  S.delivered = d.delivered; S.shown = d.shown; S.playing = d.playing;
  S.narration = d.narration;
  S.bookId = d.bookId;
  buildClock();

  $('book').textContent = `${d.book} · ${d.total} 条`;
  setPlayBtn(d.playing);
  $('speed').value = d.speed; $('speedVal').textContent = `${d.speed}×`;
  $('narr').classList.toggle('on', d.narration);
  $('mood').value = d.moodMode;
  $('progress').max = Math.max(d.total, 1);

  buildSidebar();
  buildJump();
  S.rows.forEach((el) => el.remove());
  S.rows.clear();
  hideTyping();
  renderRange(Math.max(0, d.delivered - PAGE), d.delivered, true);
  if (d.shown > 0 && d.delivered < d.total) {
    const row = ensureRow(d.delivered);
    if (row._bubble) row._bubble.textContent = S.messages[d.delivered].x.slice(0, d.shown);
  }
  onProgress(d.delivered, d.total);
  if (!S._restored) { S._restored = true; restoreProgress(d); }
  setTimeout(() => maybeScroll(true), 30);
}

/* 章节快速跳转：顶栏常驻下拉，窄屏侧栏隐藏时也能用 */
function buildJump() {
  const sel = $('jump');
  sel.innerHTML = '';
  const head = document.createElement('option');
  head.value = '';
  head.textContent = S.chapters.length ? '章节…' : '无章节';
  sel.appendChild(head);
  for (const c of S.chapters) {
    const o = document.createElement('option');
    o.value = String(c.idx);
    o.textContent = c.text.replace(/\s+/g, ' ').trim().slice(0, 20);
    sel.appendChild(o);
  }
  sel.disabled = !S.chapters.length;
}

function syncJump(idx) {
  if (!S.chapters.length) return;
  let cur = '';
  for (const c of S.chapters) { if (c.idx <= idx) cur = String(c.idx); else break; }
  $('jump').value = cur;
}

function buildSidebar() {
  const ch = $('chapters');
  ch.innerHTML = '';
  if (!S.chapters.length) ch.innerHTML = '<div class="side-head">（无章节标记）</div>';
  for (const c of S.chapters) {
    const b = document.createElement('button');
    b.className = 'chapter'; b.textContent = c.text;
    b.onclick = () => send('seek', { delivered: c.idx, resume: true });
    ch.appendChild(b);
  }
  const cs = $('cast');
  cs.innerHTML = '';
  for (const c of S.cast) {
    const item = document.createElement('div');
    item.className = 'cast-item';
    const av = document.createElement('div');
    av.className = 'avatar'; av.style.background = c.color; av.textContent = c.avatar;
    const who = document.createElement('div');
    who.className = 'who';
    who.innerHTML = `<b></b><i></i>`;
    who.querySelector('b').textContent = c.name;
    who.querySelector('b').style.color = c.color;
    who.querySelector('i').textContent = `${c.count} 句 · ${c.cpm} 字/分`;
    item.appendChild(av); item.appendChild(who);
    cs.appendChild(item);
  }
}

function onChar(idx, n) {
  hideTyping();
  if (!S.narration && S.messages[idx] && S.messages[idx].k === 'narration') return;
  const row = ensureRow(idx);
  const full = S.messages[idx].x;
  if (row._bubble) {
    row._bubble.textContent = full.slice(0, n);
    row._bubble.classList.toggle('live', n < full.length);   // 还在打 → 显示光标
  }
  S.shown = n; S.delivered = idx + (n >= full.length ? 1 : 0);
  Sfx.click();
  maybeScroll();
}

function onSent(idx) {
  hideTyping();
  const row = S.rows.get(idx);
  if (row && row._bubble) row._bubble.classList.remove('live');
  if (S.messages[idx] && S.messages[idx].k === 'chapter') Sfx.chime();
  else Sfx.pop();
  maybeScroll();
}

function onSeek(delivered) {
  hideTyping();
  S.delivered = delivered; S.shown = 0;
  dropFrom(delivered);
  renderRange(Math.max(0, delivered - PAGE), delivered, true);
  maybeScroll(true);
  syncJump(delivered);
  saveProgress(true);
}

function onProgress(delivered, total) {
  if (!S.scrubbing) {
    $('progress').max = Math.max(total, 1);
    $('progress').value = delivered;
  }
  S.delivered = delivered;
  if (total) S.total = total;
  syncJump(delivered);
  saveProgress();
}

function onStatus(s) {
  S.playing = s.playing;
  setPlayBtn(s.playing);
  const next = s.next
    ? (s.next.kind === 'dialogue' ? `下一条「${s.next.speaker || '??'}」` : '下一条「旁白」')
    : '读完了';
  const detail = s.moodDetail ? ` · ${s.moodDetail}` : '';
  const fly = s.flyState === 'warming' ? ' · 果蝇预热中…'
    : s.flyState === 'failed' ? ' · 果蝇失败' : '';
  $('status').innerHTML =
    `<b>${s.delivered}</b>/${s.total} · 节奏源 ${s.moodLabel}${detail}`
    + ` · 心情 ×${s.mood.toFixed(2)} · ${s.speed}× · ${next}${fly}`;
  if (!S.scrubbing) $('progress').value = s.delivered;
}

function onMood(ev) {
  if (ev.mode) $('mood').value = ev.mode;
}

/* ---------------------------------------------------------------- 通信 */
function send(cmd, args) {
  fetch('/api/cmd', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(Object.assign({ cmd }, args || {})),
  }).catch((e) => console.warn('命令失败', cmd, e));
}

function connect() {
  const es = new EventSource('/api/events');
  es.onmessage = (e) => {
    try {
      const batch = JSON.parse(e.data);
      for (const ev of (Array.isArray(batch) ? batch : [batch])) handle(ev);
    } catch (err) { console.warn('坏事件', err); }
  };
  es.onerror = () => { /* EventSource 自己会重连，重连后会收到新的 init */ };
}

/* ---------------------------------------------------------------- 控制 */
const FONT_MIN = 12, FONT_MAX = 26, FONT_DEF = 15;

function curFont() {
  const v = parseFloat(getComputedStyle(document.documentElement)
    .getPropertyValue('--msg-size'));
  return isNaN(v) ? FONT_DEF : v;
}

function applyFont(px) {
  px = Math.max(FONT_MIN, Math.min(FONT_MAX, Math.round(px)));
  document.documentElement.style.setProperty('--msg-size', px + 'px');
  try { localStorage.setItem('liaoshu.font', String(px)); } catch (e) {}
  return px;
}

function bindControls() {
  $('rewind').innerHTML = svg('rewind') + ' 回退';
  $('skip').innerHTML = svg('skip') + ' 跳过';
  $('restart').innerHTML = svg('restart') + ' 重来';
  $('theme').innerHTML = svg('theme');
  $('sound').innerHTML = svg('mute');
  $('settings').innerHTML = svg('gear');
  $('mini').innerHTML = svg('mini');
  setPlayBtn(false);

  $('mini').onclick = (e) => { e.stopPropagation(); openMini(); };

  $('settings').onclick = (e) => {
    e.stopPropagation();
    $('pop').hidden = !$('pop').hidden;
  };
  $('pop').onclick = (e) => e.stopPropagation();
  document.addEventListener('click', () => { if (!$('pop').hidden) $('pop').hidden = true; });

  $('jump').onchange = (e) => {
    const idx = parseInt(e.target.value, 10);
    if (!isNaN(idx)) send('seek', { delivered: idx, resume: true });
  };

  $('play').onclick = () => send('toggle');
  $('rewind').onclick = () => send('rewind', { n: 1 });
  $('skip').onclick = () => send('skip');
  $('restart').onclick = () => send('restart');

  $('speed').oninput = (e) => {
    const v = parseFloat(e.target.value);
    $('speedVal').textContent = `${v}×`;
    clearTimeout(S._spd);
    S._spd = setTimeout(() => send('speed', { value: v }), 60);
  };

  $('narr').onclick = () => {
    S.narration = !S.narration;
    $('narr').classList.toggle('on', S.narration);
    send('narration', { on: S.narration });
    // 本地立刻重画可见部分，不用等服务端把旁白跳过一遍
    dropFrom(0);
    renderRange(Math.max(0, S.delivered - PAGE), S.delivered, true);
    maybeScroll(true);
  };

  $('mood').onchange = (e) => send('mood', { mode: e.target.value });

  $('fontDown').onclick = () => applyFont(curFont() - 1);
  $('fontUp').onclick = () => applyFont(curFont() + 1);

  $('sound').onclick = async () => {
    const on = await Sfx.toggle();
    $('sound').innerHTML = svg(on ? 'sound' : 'mute');
    $('sound').classList.toggle('on', on);
    try { localStorage.setItem('liaoshu.sound', on ? '1' : '0'); } catch (e) {}
    if (on) Sfx.click();
  };

  $('theme').onclick = () => {
    const cur = document.documentElement.dataset.theme;
    const next = cur === 'dark' ? 'light' : cur === 'light' ? '' : 'dark';
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem('liaoshu.theme', next); } catch (e) {}
  };

  const bar = $('progress');
  bar.oninput = () => { S.scrubbing = true; };
  bar.onchange = () => {
    S.scrubbing = false;
    send('seek', { delivered: parseInt(bar.value, 10), resume: S.playing });
  };

  S.stream.onscroll = () => { S.auto = nearBottom(); };

  document.addEventListener('keydown', (e) => {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;
    if (e.code === 'Space') { e.preventDefault(); send('toggle'); }
    else if (e.code === 'ArrowLeft') send('rewind', { n: 1 });
    else if (e.code === 'ArrowRight') send('skip');
    else if (e.code === 'KeyR') send('restart');
  });
}

/* ---------------------------------------------------------------- 启动 */
(function main() {
  S.inner = $('streamInner');
  S.stream = $('stream');
  S.inner.innerHTML = '';
  bindControls();

  // ?mini=1 → 独立小窗：只显示打字流（由顶栏小窗按钮 popup 打开）
  if (new URLSearchParams(location.search).get('mini') === '1') applyMini(true);

  try {
    // ?theme=dark|light 可以直接指定主题（也方便截图和分享链接）
    const q = new URLSearchParams(location.search).get('theme');
    const th = q !== null ? q : localStorage.getItem('liaoshu.theme');
    if (th !== null && th !== '') document.documentElement.dataset.theme = th;
    else if (th === '') document.documentElement.dataset.theme = '';
    const fs = parseInt(localStorage.getItem('liaoshu.font'), 10);
    if (fs) applyFont(fs);
    if (localStorage.getItem('liaoshu.sound') === '1') {
      // 浏览器要求先有用户手势才能出声，所以这里只做"点亮"，真正 resume 在第一次点击时
      $('sound').innerHTML = svg('sound');
      const kick = () => {
        Sfx.enable().then(() => {
          $('sound').classList.add('on');
          document.removeEventListener('pointerdown', kick);
        });
      };
      document.addEventListener('pointerdown', kick);
    }
  } catch (e) { /* 无痕模式下 localStorage 可能不可用 */ }

  // 服务端把初始快照嵌在页面里了，先照着画一遍，再连 SSE 接后续增量
  if (window.__BOOT__) {
    handle(window.__BOOT__);
    window.__BOOT__ = null;
  }
  connect();
})();
