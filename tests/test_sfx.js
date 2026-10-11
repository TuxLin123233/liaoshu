/* 音效的单元测试。
 *
 * 我听不到声音，但可以用一个假的 AudioContext 把 Sfx 的每个音效都跑一遍，
 * 确认：① 没有拼错的 Web Audio API；② 节点连接成了一条链；
 * ③ 打字限流真的生效（高速打字时不会一秒调度上百个振荡器）。
 *
 *     node tests/test_sfx.js
 */
const fs = require('fs');
const path = require('path');

const src = fs.readFileSync(
  path.join(__dirname, '..', 'liaoshu', 'web', 'app.js'), 'utf8');

// 把 Sfx 那段对象字面量抠出来（它是自包含的，只依赖 window.AudioContext）
const start = src.indexOf('const Sfx = {');
const end = src.indexOf('\n};', start);
if (start < 0 || end < 0) { console.error('没找到 Sfx 定义'); process.exit(1); }
const sfxSrc = src.slice(start, end + 3);

let created = {}, connected = 0, started = 0, paramSets = 0;

function gainParam() {
  return {
    value: 0,
    setValueAtTime() { paramSets++; return this; },
    exponentialRampToValueAtTime() { paramSets++; return this; },
  };
}
function node(kind) {
  created[kind] = (created[kind] || 0) + 1;
  return {
    kind,
    gain: gainParam(),
    frequency: Object.assign(gainParam(), {}),
    Q: { value: 0 },
    playbackRate: { value: 1 },
    type: '',
    buffer: null,
    connect(dst) { connected++; return dst; },
    start() { started++; },
    stop() {},
  };
}

class MockAudioContext {
  constructor() { this.sampleRate = 48000; this.currentTime = 0; this.destination = node('destination'); }
  createGain() { return node('gain'); }
  createBufferSource() { return node('bufferSource'); }
  createBiquadFilter() { return node('biquad'); }
  createOscillator() { return node('oscillator'); }
  createBuffer(ch, len) { return { length: len, getChannelData: () => new Float32Array(len) }; }
  resume() { return Promise.resolve(); }
}

const sandbox = {
  window: { AudioContext: MockAudioContext },
  performance,
  Math,
  Object,
  console,
};
const factory = new Function('window', 'performance', 'Math', 'Object', 'console',
  sfxSrc + '\nreturn Sfx;');

const FAILS = [];
function check(name, cond, extra = '') {
  console.log((cond ? '  ✓ ' : '  ✗ ') + name + (cond ? '' : '  ' + extra));
  if (!cond) FAILS.push(name);
}

const Sfx = factory(sandbox.window, sandbox.performance, sandbox.Math, sandbox.Object, sandbox.console);

console.log('\n[音效] Web Audio 合成（零音频文件）');

check('初始状态是关的', Sfx.on === false);
Sfx.click(); Sfx.pop(); Sfx.chime();
check('没开音效时什么都不做', started === 0 && Object.keys(created).length === 0);

(async () => {
  const ok = await Sfx.enable();
  check('能创建 AudioContext', ok === true && Sfx.ctx !== null);
  check('噪声 buffer 只建一次（约 5KB，不是一堆音频文件）',
    Sfx.noise && Sfx.noise.length === Math.floor(48000 * 0.06));

  created = {}; connected = 0; started = 0; paramSets = 0;
  Sfx.click();
  check('一次按键：白噪声源 + 带通 + 高通 + 低频闷响',
    created.bufferSource === 1 && created.biquad === 2 && created.oscillator === 1
    && created.gain === 2, JSON.stringify(created));
  check('节点被串成链', connected >= 6, `connected=${connected}`);
  check('源和振荡器都真的启动了', started === 2, `started=${started}`);

  // 限流：连续 200 次 click 只应该放出去很少几个
  // （都在同一个毫秒里，所以正常情况下一个都不该放出去）
  created = {}; started = 0;
  const before = Sfx.lastClick;
  for (let i = 0; i < 200; i++) Sfx.click();
  check('打字限流生效（200 次点击不会调度 200 个振荡器）',
    (created.oscillator || 0) <= 3, `实际 ${created.oscillator || 0}`);
  check('lastClick 有更新', Sfx.lastClick >= before);

  // 等过限流窗口，再确认还能发声
  await new Promise((r) => setTimeout(r, 30));
  created = {};
  Sfx.click();
  check('过了限流窗口后恢复发声', created.oscillator === 1);

  created = {};
  Sfx.pop();
  check('消息送达：两个音', created.oscillator === 2 && created.gain === 2,
    JSON.stringify(created));

  created = {};
  Sfx.chime();
  check('新章节：三个音的琶音', created.oscillator === 3 && created.gain === 3,
    JSON.stringify(created));

  check('关掉后立刻静音', (Sfx.disable(), Sfx.on === false));
  created = {};
  Sfx.click();
  check('关掉后不再产生任何节点', Object.keys(created).length === 0);

  console.log();
  if (FAILS.length) {
    console.log(`❌ ${FAILS.length} 项失败: ${FAILS}`);
    process.exit(1);
  }
  console.log('✅ 音效测试全部通过（整个音效系统 = 1 段 60ms 噪声 buffer，0 个音频文件）');
})();
