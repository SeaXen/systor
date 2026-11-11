/* systor dashboard JS — vanilla, no frameworks */
const _CACHED_COLORS = {};

function _readStyleProp(prop, fallback) {
  if (typeof document === 'undefined' || !document.documentElement) return fallback;
  return getComputedStyle(document.documentElement).getPropertyValue(prop).trim() || fallback;
}

function updateChartColors(theme) {
  _CACHED_COLORS.green = _readStyleProp('--chart-net', '#1d9e75');
  _CACHED_COLORS.yellow = _readStyleProp('--chart-disk', '#db9300');
  _CACHED_COLORS.red = _readStyleProp('--chart-warn', '#ec7e7e');
  _CACHED_COLORS.blue = _readStyleProp('--chart-load', '#1d9e75');
  _CACHED_COLORS.purple = _readStyleProp('--chart-alt', '#a78bfa');
  _CACHED_COLORS.cyan = _readStyleProp('--chart-cpu', '#22d3ee');
  _CACHED_COLORS.cpu = _CACHED_COLORS.cyan;
  _CACHED_COLORS.ram = _readStyleProp('--chart-ram', '#ff6b8a');
  _CACHED_COLORS.disk = _readStyleProp('--chart-disk', '#f6c453');
  _CACHED_COLORS.disk2 = _readStyleProp('--chart-disk2', '#ffb347');
  _CACHED_COLORS.tx = _CACHED_COLORS.ram;
  _CACHED_COLORS.dim = _readStyleProp('--chart-label', '#898781');
  _CACHED_COLORS.grid = _readStyleProp('--chart-grid', 'rgba(255, 255, 255, 0.08)');
  _CACHED_COLORS.text = _readStyleProp('--text-muted', '#c9d1d9');
}

updateChartColors();
const COLORS = _CACHED_COLORS;
window.COLORS = COLORS;
window.updateChartColors = updateChartColors;

window.addEventListener('systor:theme-change', function(e) {
  updateChartColors(e && e.detail ? e.detail.theme : undefined);
});

function fmt(n, unit) {
  if (n === null || n === undefined) return '—';
  if (typeof n === 'number') {
    if (Math.abs(n) >= 100) return n.toFixed(0) + (unit ? ' ' + unit : '');
    if (Math.abs(n) >= 10) return n.toFixed(1) + (unit ? ' ' + unit : '');
    return n.toFixed(2) + (unit ? ' ' + unit : '');
  }
  return n;
}

function bytes(n) {
  if (!n) return '0 B';
  const u = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return n.toFixed(1) + ' ' + u[i];
}

const _timeFmt = new Intl.DateTimeFormat(undefined, {hour:'2-digit', minute:'2-digit', second:'2-digit'});
function timeShort(ts) {
  return _timeFmt.format(new Date(ts * 1000));
}

function timeAgo(ts) {
  const s = Math.floor((Date.now() / 1000) - ts);
  if (s < 60) return s + 's ago';
  if (s < 3600) return Math.floor(s / 60) + 'm ago';
  if (s < 86400) return Math.floor(s / 3600) + 'h ago';
  return Math.floor(s / 86400) + 'd ago';
}

/* Tiny line chart (no Chart.js) */
function drawLineChart(canvas, data, color, ymin, ymax) {
  if (!canvas) return;
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  canvas.width = w * dpr; canvas.height = h * dpr;
  const ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, w, h);
  if (!data || data.length < 2) {
    ctx.fillStyle = COLORS.dim; ctx.font = '12px monospace';
    ctx.fillText('No data', 8, h / 2);
    return;
  }
  const vals = data.map(p => p[1]).filter(v => v !== null);
  if (vals.length === 0) return;
  const lo = ymin !== undefined ? ymin : Math.min(...vals);
  const hi = ymax !== undefined ? ymax : Math.max(...vals);
  const range = (hi - lo) || 1;
  const pad = 8;
  const w0 = w - pad * 2, h0 = h - pad * 2;
  // grid
  ctx.strokeStyle = COLORS.grid; ctx.lineWidth = 1;
  for (let i = 0; i <= 3; i++) {
    const y = pad + (h0 / 3) * i;
    ctx.beginPath(); ctx.moveTo(pad, y); ctx.lineTo(w - pad, y); ctx.stroke();
  }
  // line
  ctx.strokeStyle = color; ctx.lineWidth = 1.5;
  ctx.beginPath();
  let started = false;
  for (let i = 0; i < data.length; i++) {
    const v = data[i][1];
    if (v === null || v === undefined) continue;
    const x = pad + (data.length > 1 ? (w0 * i) / (data.length - 1) : w0 / 2);
    const y = pad + h0 - ((v - lo) / range) * h0;
    if (!started) { ctx.moveTo(x, y); started = true; } else { ctx.lineTo(x, y); }
  }
  ctx.stroke();
  // fill under
  ctx.lineTo(pad + w0, pad + h0); ctx.lineTo(pad, pad + h0); ctx.closePath();
  ctx.fillStyle = color + '20'; ctx.fill();
  // min/max labels
  ctx.fillStyle = COLORS.dim; ctx.font = '10px monospace';
  ctx.fillText(hi.toFixed(1), 2, pad + 6);
  ctx.fillText(lo.toFixed(1), 2, h - 4);
  // last value
  const last = data[data.length - 1][1];
  if (last !== null) {
    ctx.fillStyle = color;
    ctx.fillText(last.toFixed(1), w - 30, pad + 6);
  }
}

function getCsrfToken() {
  const meta = document.querySelector('meta[name="csrf-token"]');
  return meta ? meta.getAttribute('content') : '';
}

async function api(url, opts) {
  const options = Object.assign({ cache: 'no-store' }, opts || {});
  options.headers = Object.assign({}, options.headers || {});
  const method = (options.method || 'GET').toUpperCase();
  if (['POST', 'PUT', 'PATCH', 'DELETE'].includes(method)) {
    const token = getCsrfToken();
    if (token && !options.headers['X-CSRF-Token']) {
      options.headers['X-CSRF-Token'] = token;
    }
  }
  const r = await fetch(url, options);
  if (r.status === 401) {
    window.location.href = '/login?next=' + encodeURIComponent(window.location.pathname);
    return;
  }
  const j = await r.json().catch(() => ({ ok: false, error: 'HTTP ' + r.status }));
  if (!r.ok || j.ok === false) {
    const err = new Error(j.error || j.message || ('HTTP ' + r.status));
    err.response = j;
    err.status = r.status;
    throw err;
  }
  return j;
}

window.api = api;

async function fetchJSON(url, opts) {
  if (opts && opts.method && opts.method.toUpperCase() !== 'GET') {
    return api(url, opts);
  }
  const r = await fetch(url, Object.assign({ cache: 'no-store' }, opts || {}));
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

function statusClass(value, thresholds) {
  // returns 'green' | 'yellow' | 'red'
  if (value === null || value === undefined) return 'gray';
  if (thresholds === undefined) return 'green';
  if (value > thresholds.red) return 'red';
  if (value > thresholds.yellow) return 'yellow';
  return 'green';
}
