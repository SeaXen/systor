/* systor dashboard JS — vanilla, no frameworks */
const COLORS = {
  green: '#2ecc71', yellow: '#f1c40f', red: '#e74c3c',
  blue: '#3498db', purple: '#9b59b6', dim: '#6f7888', grid: '#232a36'
};

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

function timeShort(ts) {
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString();
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

async function fetchJSON(url) {
  const r = await fetch(url);
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
