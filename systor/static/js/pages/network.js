/**
 * Systor Network Analytics & Diagnostics Page Controller
 */
(function() {
  'use strict';

  function getNetworkConfig() {
    const el = document.getElementById('network-page-config');
    return {
      hideVirtual: el ? el.dataset.hideVirtual === 'true' : false,
      barDays: el?.dataset.barDays || '90',
      tableDays: el?.dataset.tableDays || '90',
      defaultIface: el?.dataset.defaultIface || 'all',
    };
  }

  const NET_CFG = getNetworkConfig();
  const NCOL = {
    get rx() { return window.COLORS?.cyan || '#22d3ee'; },
    get tx() { return window.COLORS?.ram || '#ff6b8a'; },
    get grid() { return window.COLORS?.grid || 'rgba(255, 255, 255, 0.08)'; },
    get dim() { return window.COLORS?.dim || '#8b949e'; },
    get fillRx() { return 'rgba(34,211,238,.18)'; },
    get fillTx() { return 'rgba(255,107,138,.18)'; }
  };

  let netTip = null;
  let netTimer = null;
  let netLoading = false;
  let lastIfaceRows = [];
  const usageState = {
    daily: { rows: [], page: 1, pageSize: 10 },
    monthly: { rows: [], page: 1, pageSize: 10 },
    yearly: { rows: [], page: 1, pageSize: 10 }
  };

  function bytes(n) {
    n = Number(n || 0);
    const u = ['B', 'KB', 'MB', 'GB', 'TB'];
    let i = 0;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return n.toFixed(i ? 1 : 0) + ' ' + u[i];
  }

  function mbs(n) {
    return Number(n || 0).toFixed(3) + ' MB/s';
  }

  function niceStep(v) {
    if (!Number.isFinite(v) || v <= 0) return 1;
    const p = Math.pow(10, Math.floor(Math.log10(v)));
    const n = v / p;
    const m = n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10;
    return m * p;
  }

  function niceMax(v) {
    const step = niceStep((Number(v) || 0) / 4 || 1);
    return Math.max(step, Math.ceil((Number(v) || 0) / step) * step);
  }

  function gbVal(n) {
    return Number(n || 0).toFixed(3) + ' GB';
  }

  function fmtTs(ts) {
    return ts ? new Date(ts * 1000).toLocaleString() : '—';
  }

  function sum(arr, key) {
    return (arr || []).reduce((a, r) => a + Number(r[key] || 0), 0);
  }

  function avg(arr, key) {
    const vals = (arr || []).map(r => Number(r[key] || 0)).filter(v => Number.isFinite(v));
    return vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : 0;
  }

  function showTip(html, x, y) {
    if (!netTip) {
      netTip = document.createElement('div');
      netTip.className = 'canvas-tooltip hidden';
      document.body.appendChild(netTip);
    }
    netTip.innerHTML = html;
    netTip.classList.remove('hidden');
    netTip.style.left = (x + 14) + 'px';
    netTip.style.top = (y + 14) + 'px';
  }

  function hideTip() {
    if (netTip) netTip.classList.add('hidden');
  }

  function esc(s) {
    return String(s ?? '').replace(/[&<>\"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  }

  function isVirtualIface(name) {
    return /^(docker\d+|veth|br-|lo$)/.test(String(name || ''));
  }

  function periodsForDays(granularity, days) {
    if (days === 'all') return 5000;
    const d = Math.max(1, Number(days || 30));
    if (granularity === 'month') return Math.max(1, Math.ceil(d / 30));
    if (granularity === 'week') return Math.max(1, Math.ceil(d / 7));
    return d;
  }

  function bindLineTooltip(canvas) {
    if (canvas.__tooltipBound) return;
    canvas.__tooltipBound = true;
    canvas.addEventListener('mouseleave', hideTip);
    canvas.addEventListener('mousemove', ev => {
      const meta = canvas.__hoverMeta;
      if (!meta || !meta.groups || !meta.groups.length) return hideTip();
      const rect = canvas.getBoundingClientRect();
      const x = ev.clientX - rect.left;
      let best = null;
      for (const g of meta.groups) {
        const dx = Math.abs(g.x - x);
        if (!best || dx < best.dx) best = { dx, g };
      }
      if (!best || best.dx > 18) return hideTip();
      const lines = [`<div class="tt-time">${fmtTs(best.g.ts)}</div>`];
      for (const item of best.g.items) {
        lines.push(`<div><span style="color:${item.color};font-weight:700">${item.name}</span>: <strong>${item.value}</strong></div>`);
      }
      showTip(lines.join(''), ev.clientX, ev.clientY);
    });
  }

  function bindBarTooltip(canvas) {
    if (canvas.__barTooltipBound) return;
    canvas.__barTooltipBound = true;
    canvas.addEventListener('mouseleave', hideTip);
    canvas.addEventListener('mousemove', ev => {
      const meta = canvas.__barMeta;
      if (!meta || !meta.rects || !meta.rects.length) return hideTip();
      const rect = canvas.getBoundingClientRect();
      const x = ev.clientX - rect.left;
      const y = ev.clientY - rect.top;
      const hit = meta.rects.find(r => x >= r.x && x <= r.x + r.w && y >= r.y && y <= r.y + r.h);
      if (!hit) return hideTip();
      const row = hit.row;
      showTip(`<div class="tt-time">${row.label}</div><div><span class="dl-text"><strong>DL</strong></span>: ${gbVal(row.rx_gb)}</div><div><span class="ul-text"><strong>UL</strong></span>: ${gbVal(row.tx_gb)}</div><div><strong>Total</strong>: ${gbVal(row.total_gb)}</div><div>Avg rate: ${mbs(row.avg_rate_mbps || 0)}</div>`, ev.clientX, ev.clientY);
    });
  }

  function drawLine(canvas, points, color, name) {
    if (!canvas) return;
    bindLineTooltip(canvas);
    const dpr = window.devicePixelRatio || 1;
    const w = Math.max(10, canvas.clientWidth);
    const h = Math.max(90, canvas.clientHeight);
    canvas.width = Math.floor(w * dpr);
    canvas.height = Math.floor(h * dpr);
    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    if (!points.length) {
      ctx.fillStyle = NCOL.dim;
      ctx.font = '12px monospace';
      ctx.fillText('waiting for data', 12, h / 2);
      canvas.__hoverMeta = { groups: [] };
      return;
    }
    const vals = points.map(p => Number(p[1] || 0));
    const max = niceMax(Math.max(...vals, 0.01));
    const min = 0;
    const padL = 38, padR = 12, padT = 8, padB = 18;
    const gw = w - padL - padR, gh = h - padT - padB;
    ctx.strokeStyle = NCOL.grid;
    ctx.lineWidth = 1;
    ctx.fillStyle = NCOL.dim;
    ctx.font = '10px monospace';
    for (let i = 0; i <= 3; i++) {
      const y = padT + (gh / 3) * i;
      ctx.beginPath();
      ctx.moveTo(padL, y);
      ctx.lineTo(w - padR, y);
      ctx.stroke();
      ctx.fillText((max - ((max - min) / 3) * i).toFixed(2), 2, y + 3);
    }
    ctx.beginPath();
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.8;
    const groups = [];
    points.forEach((p, i) => {
      const v = Number(p[1] || 0);
      const x = padL + (points.length > 1 ? gw * i / (points.length - 1) : gw / 2);
      const y = padT + gh - ((v - min) / (max - min)) * gh;
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      groups.push({ x, ts: p[0], items: [{ name, value: mbs(v), color }] });
    });
    ctx.stroke();
    canvas.__hoverMeta = { groups };
  }

  function drawBars(canvas, rows, granularity) {
    if (!canvas) return;
    bindBarTooltip(canvas);
    const dpr = window.devicePixelRatio || 1;
    const w = Math.max(10, canvas.clientWidth);
    const h = Math.max(120, canvas.clientHeight);
    canvas.width = Math.floor(w * dpr);
    canvas.height = Math.floor(h * dpr);
    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    if (!rows.length) {
      ctx.fillStyle = NCOL.dim;
      ctx.font = '12px monospace';
      ctx.fillText('no usage buckets yet', 12, h / 2);
      canvas.__barMeta = { rects: [], granularity };
      return;
    }
    const max = niceMax(Math.max(...rows.map(r => Math.max(Number(r.rx_gb || 0), Number(r.tx_gb || 0))), 0.01));
    const padL = 38, padR = 12, padT = 12, padB = 34;
    const gw = w - padL - padR, gh = h - padT - padB;
    ctx.strokeStyle = NCOL.grid;
    ctx.lineWidth = 1;
    ctx.fillStyle = NCOL.dim;
    ctx.font = '10px monospace';
    for (let i = 0; i <= 3; i++) {
      const y = padT + (gh / 3) * i;
      ctx.beginPath();
      ctx.moveTo(padL, y);
      ctx.lineTo(w - padR, y);
      ctx.stroke();
      ctx.fillText((max - (max / 3) * i).toFixed(2), 2, y + 3);
    }
    const n = rows.length;
    const groupW = gw / Math.max(1, n);
    const barW = Math.max(4, Math.min(16, groupW * 0.32));
    const rects = [];
    rows.forEach((r, i) => {
      const xCenter = padL + groupW * i + groupW / 2;
      const rxh = gh * (Number(r.rx_gb || 0) / max);
      const txh = gh * (Number(r.tx_gb || 0) / max);
      const rxX = xCenter - barW - 1;
      const txX = xCenter + 1;
      const rxY = padT + gh - rxh;
      const txY = padT + gh - txh;
      ctx.fillStyle = NCOL.fillRx;
      ctx.fillRect(rxX, rxY, barW, rxh);
      ctx.strokeStyle = NCOL.rx;
      ctx.strokeRect(rxX, rxY, barW, rxh);
      ctx.fillStyle = NCOL.fillTx;
      ctx.fillRect(txX, txY, barW, txh);
      ctx.strokeStyle = NCOL.tx;
      ctx.strokeRect(txX, txY, barW, txh);
      rects.push({ x: rxX, y: rxY, w: barW, h: rxh, row: r });
      rects.push({ x: txX, y: txY, w: barW, h: txh, row: r });
      const label = String(r.label || '').replace(/^\d{4}-/, '');
      ctx.fillStyle = NCOL.dim;
      ctx.save();
      ctx.translate(xCenter, h - 6);
      ctx.rotate(-0.30);
      ctx.fillText(label, 0, 0);
      ctx.restore();
    });
    canvas.__barMeta = { rects, granularity };
  }

  function renderInterfaces(rows) {
    const hideVirtual = document.getElementById('hide-virtual-toggle')?.checked ?? false;
    const visible = hideVirtual ? rows.filter(r => !isVirtualIface(r.iface)) : rows.slice();
    const hiddenCount = rows.length - visible.length;
    const body = document.getElementById('iface-body');
    const titleMeta = document.getElementById('iface-title-meta');
    if (titleMeta) titleMeta.textContent = `${visible.length} visible · ${rows.length} total`;
    const periodNote = document.getElementById('iface-period-note');
    if (periodNote) periodNote.textContent = hideVirtual ? `(${hiddenCount} hidden virtual)` : `(${rows.length} total interfaces)`;
    if (!body) return;
    if (!visible.length) {
      body.innerHTML = '<tr><td colspan="5" class="empty-cell">No visible interfaces after filter</td></tr>';
      return;
    }
    const totalRx = visible.reduce((a, r) => a + Number(r.rx_bytes || 0), 0);
    const totalTx = visible.reduce((a, r) => a + Number(r.tx_bytes || 0), 0);
    const avgRx = visible.reduce((a, r) => a + Number(r.rx_mbps || 0), 0) / visible.length;
    const avgTx = visible.reduce((a, r) => a + Number(r.tx_mbps || 0), 0) / visible.length;
    const summary = `<tr class="summary-row"><td class="mono strongish">TOTAL</td><td class="mono text-right rx-text">${bytes(totalRx)}</td><td class="mono text-right tx-text">${bytes(totalTx)}</td><td class="mono text-right rx-text">${mbs(avgRx)}</td><td class="mono text-right tx-text">${mbs(avgTx)}</td></tr>`;
    body.innerHTML = summary + visible.map(r => `<tr><td class="mono">${r.iface}</td><td class="mono text-right rx-text">${bytes(r.rx_bytes)}</td><td class="mono text-right tx-text">${bytes(r.tx_bytes)}</td><td class="mono text-right rx-text">${mbs(r.rx_mbps)}</td><td class="mono text-right tx-text">${mbs(r.tx_mbps)}</td></tr>`).join('');
  }

  function renderUsageTable(kind, bodyId, pagerId, titleId, periodNoteId) {
    const stateBox = usageState[kind];
    const rows = stateBox.rows || [];
    const body = document.getElementById(bodyId);
    const pageSize = stateBox.pageSize;
    const totalPages = Math.max(1, Math.ceil(rows.length / pageSize));
    stateBox.page = Math.max(1, Math.min(totalPages, stateBox.page || 1));
    const pager = document.getElementById(pagerId);
    if (pager) {
      const prevSvg = '<svg viewBox="0 0 24 24" aria-hidden="true"><polyline points="15 18 9 12 15 6"></polyline></svg>';
      const nextSvg = '<svg viewBox="0 0 24 24" aria-hidden="true"><polyline points="9 18 15 12 9 6"></polyline></svg>';
      pager.innerHTML = `<button class="pager-btn" ${stateBox.page <= 1 ? 'disabled' : ''} data-usage-kind="${kind}" data-usage-dir="-1" aria-label="Previous page" type="button">${prevSvg}</button><span class="pager-text">${stateBox.page}/${totalPages}</span><button class="pager-btn" ${stateBox.page >= totalPages ? 'disabled' : ''} data-usage-kind="${kind}" data-usage-dir="1" aria-label="Next page" type="button">${nextSvg}</button>`;
    }
    const titleEl = document.getElementById(titleId);
    if (titleEl) titleEl.textContent = `${Math.min(pageSize, Math.max(0, rows.length - (stateBox.page - 1) * pageSize))}/${rows.length} ${kind}`;
    if (periodNoteId) {
      const noteEl = document.getElementById(periodNoteId);
      if (noteEl) noteEl.textContent = '(10 per page)';
    }
    if (!body) return;
    if (!rows.length) {
      body.innerHTML = '<tr><td colspan="5" class="empty-cell">No usage history yet</td></tr>';
      return;
    }
    const totalRx = sum(rows, 'rx_gb');
    const totalTx = sum(rows, 'tx_gb');
    const totalGb = sum(rows, 'total_gb');
    const avgRate = avg(rows, 'avg_rate_mbps');
    const summary = `<tr class="summary-row"><td class="mono strongish">TOTAL</td><td class="text-right mono rx-text">${gbVal(totalRx)}</td><td class="text-right mono tx-text">${gbVal(totalTx)}</td><td class="text-right mono strongish">${gbVal(totalGb)}</td><td class="text-right mono strongish">${mbs(avgRate)}</td></tr>`;
    const start = (stateBox.page - 1) * pageSize;
    const pageRows = rows.slice().reverse().slice(start, start + pageSize);
    body.innerHTML = summary + pageRows.map(r => `<tr><td class="mono">${r.label}</td><td class="text-right mono rx-text">${gbVal(r.rx_gb)}</td><td class="text-right mono tx-text">${gbVal(r.tx_gb)}</td><td class="text-right mono">${gbVal(r.total_gb)}</td><td class="text-right mono">${mbs(r.avg_rate_mbps || 0)}</td></tr>`).join('');
  }

  async function loadSeries() {
    const hours = document.getElementById('net-hours')?.value || 24;
    const j = await window.fetchJSON('/api/network-series?hours=' + hours);
    const data = j.data || [];
    const peakRx = data.reduce((a, b) => (!a || Number(b.rx_mbps || 0) > Number(a.rx_mbps || 0)) ? b : a, null);
    const peakTx = data.reduce((a, b) => (!a || Number(b.tx_mbps || 0) > Number(a.tx_mbps || 0)) ? b : a, null);
    const latestRx = data.length ? data[data.length - 1].rx_mbps : 0;
    const latestTx = data.length ? data[data.length - 1].tx_mbps : 0;

    const curRx = document.getElementById('cur-rx');
    if (curRx) curRx.textContent = bytes((j.current || {}).rx_bytes || 0);
    const curTx = document.getElementById('cur-tx');
    if (curTx) curTx.textContent = bytes((j.current || {}).tx_bytes || 0);
    const pkRx = document.getElementById('peak-rx');
    if (pkRx) pkRx.textContent = mbs(latestRx);
    const pkTx = document.getElementById('peak-tx');
    if (pkTx) pkTx.textContent = mbs(latestTx);

    const metaRx = document.getElementById('meta-rx-line');
    if (metaRx) metaRx.innerHTML = `<span class="dl-text"><strong>latest ${mbs(latestRx)}</strong></span> · avg ${mbs(avg(data, 'rx_mbps'))} · peak ${peakRx ? mbs(peakRx.rx_mbps) : '0.000 MB/s'}`;
    const metaTx = document.getElementById('meta-tx-line');
    if (metaTx) metaTx.innerHTML = `<span class="ul-text"><strong>latest ${mbs(latestTx)}</strong></span> · avg ${mbs(avg(data, 'tx_mbps'))} · peak ${peakTx ? mbs(peakTx.tx_mbps) : '0.000 MB/s'}`;

    drawLine(document.getElementById('net-rx-chart'), data.map(p => [p.ts, p.rx_mbps]), NCOL.rx, 'RX');
    drawLine(document.getElementById('net-tx-chart'), data.map(p => [p.ts, p.tx_mbps]), NCOL.tx, 'TX');
  }

  async function loadUsage() {
    const granularity = document.getElementById('net-granularity')?.value || 'day';
    const barDays = document.getElementById('net-bar-days')?.value || '90';
    const tableDays = document.getElementById('net-table-days')?.value || '90';
    const iface = document.getElementById('net-iface-filter')?.value || 'all';
    const barLimit = periodsForDays(granularity, barDays);
    const tableLimit = periodsForDays('day', tableDays);
    const monthLimit = periodsForDays('month', tableDays);
    const yearLimit = periodsForDays('year', tableDays);

    const [barRes, dailyRes, monthlyRes, yearlyRes] = await Promise.all([
      window.fetchJSON(`/api/network-usage?granularity=${granularity}&limit=${barLimit}&iface=${encodeURIComponent(iface)}`),
      window.fetchJSON(`/api/network-usage?granularity=day&limit=${tableLimit}&iface=${encodeURIComponent(iface)}`),
      window.fetchJSON(`/api/network-usage?granularity=month&limit=${monthLimit}&iface=${encodeURIComponent(iface)}`),
      window.fetchJSON(`/api/network-usage?granularity=year&limit=${yearLimit}&iface=${encodeURIComponent(iface)}`)
    ]);

    const barRows = barRes.data || [];
    usageState.daily.rows = dailyRes.data || [];
    usageState.monthly.rows = monthlyRes.data || [];
    usageState.yearly.rows = yearlyRes.data || [];

    const cov = document.getElementById('net-coverage');
    if (cov) cov.textContent = `${granularity} usage · iface ${iface} · bar ${barRows.length} · daily ${usageState.daily.rows.length}`;
    drawBars(document.getElementById('net-usage-chart'), barRows, granularity);

    const totalRx = sum(barRows, 'rx_gb');
    const totalTx = sum(barRows, 'tx_gb');
    const totalGb = sum(barRows, 'total_gb');
    const avgGb = avg(barRows, 'total_gb');

    const metaUsage = document.getElementById('meta-usage-bars');
    if (metaUsage) metaUsage.innerHTML = `<span class="rx-text"><strong>DL ${gbVal(totalRx)}</strong></span> · <span class="tx-text"><strong>UL ${gbVal(totalTx)}</strong></span> · avg ${gbVal(avgGb)} · total <strong>${gbVal(totalGb)}</strong>`;

    const today = (usageState.daily.rows || []).slice(-1)[0];
    const month = (usageState.monthly.rows || []).slice(-1)[0];
    const todayEl = document.getElementById('today-use');
    if (todayEl) todayEl.textContent = today ? gbVal(today.total_gb) : '0.000 GB';
    const monthEl = document.getElementById('month-use');
    if (monthEl) monthEl.textContent = month ? gbVal(month.total_gb) : '0.000 GB';

    renderUsageTable('daily', 'daily-usage-body', 'daily-pager', 'daily-title-meta', 'daily-period-note');
    renderUsageTable('monthly', 'monthly-usage-body', 'monthly-pager', 'monthly-title-meta', null);
    renderUsageTable('yearly', 'yearly-usage-body', 'yearly-pager', 'yearly-title-meta', null);
  }

  async function loadInterfaces() {
    const j = await window.fetchJSON('/api/network-interfaces');
    lastIfaceRows = j.interfaces || [];
    const sel = document.getElementById('net-iface-filter');
    if (!sel) return;
    const current = sel.value || 'all';
    const opts = ['<option value="all">all</option>'].concat((lastIfaceRows || []).map(r => `<option value="${esc(r.iface)}">${esc(r.iface)}</option>`));
    sel.innerHTML = opts.join('');
    if ([...sel.options].some(o => o.value === current)) sel.value = current;
    else if ([...sel.options].some(o => o.value === NET_CFG.defaultIface)) sel.value = NET_CFG.defaultIface;
    renderInterfaces(lastIfaceRows);
  }

  async function loadAppNet() {
    const j = await window.fetchJSON('/api/apps?scope=all&sort=net&limit=24');
    const rows = (j.rows || []).slice(0, 12);
    const body = document.getElementById('app-net-body');
    const active = rows.filter(r => Number(r.net_in_mb || 0) + Number(r.net_out_mb || 0) > 0).length;
    const meta = document.getElementById('app-net-meta');
    if (meta) meta.textContent = `${active} active rows · host net limited`;
    if (!body) return;
    if (!rows.length) {
      body.innerHTML = '<tr><td colspan="5" class="empty-cell">No per-app network rows available yet</td></tr>';
      return;
    }
    body.innerHTML = rows.map(r => `<tr><td><div class="app-inline-cell"><strong class="app-inline-name">${esc(r.name)}</strong>${r.kind === 'host' && r.pid ? `<span class="app-pid mono">pid ${r.pid}</span>` : ''}</div></td><td><span class="kind-pill ${String(r.kind || 'app').replace(/[^a-z0-9_-]/gi, '').toLowerCase()}">${esc(r.kind || 'app')}</span></td><td class="text-right mono">${bytes((Number(r.net_in_mb || 0) + Number(r.net_out_mb || 0)) * 1024 * 1024)}</td><td class="text-right mono rx-text">${bytes(Number(r.net_in_mb || 0) * 1024 * 1024)}</td><td class="text-right mono tx-text">${bytes(Number(r.net_out_mb || 0) * 1024 * 1024)}</td></tr>`).join('');
  }

  function resetRefreshTimer() {
    clearInterval(netTimer);
    const sec = Number(document.getElementById('net-refresh-sec')?.value || 15);
    netTimer = setInterval(() => { if (!netLoading) refreshAll(); }, sec * 1000);
  }

  async function refreshAll() {
    if (netLoading) return;
    netLoading = true;
    const upd = document.getElementById('net-updated');
    if (upd) upd.textContent = 'loading…';
    try {
      await Promise.all([loadSeries(), loadUsage(), loadInterfaces(), loadAppNet()]);
      if (upd) upd.textContent = 'updated ' + new Date().toLocaleTimeString();
    } catch (e) {
      if (upd) upd.textContent = 'error: ' + e.message;
    } finally {
      netLoading = false;
    }
  }

  // --- Feature 2: Service Uptime & Blackbox Health ---
  async function loadUptimeTargets() {
    try {
      const j = await window.fetchJSON('/api/uptime');
      const targets = j.targets || [];
      const meta = document.getElementById('uptime-meta');
      if (meta) meta.textContent = `${targets.filter(t => t.last_status === 'up').length}/${targets.length} online`;
      const body = document.getElementById('uptime-targets-body');
      if (!body) return;
      if (!targets.length) {
        body.innerHTML = '<tr><td colspan="6" class="empty-cell">No uptime targets configured yet. Click "Add Target" above.</td></tr>';
        return;
      }
      body.innerHTML = targets.map(t => {
        const stBadge = t.last_status === 'up'
          ? '<span class="status-chip ok">UP</span>'
          : (t.last_status === 'down' ? '<span class="status-chip critical">DOWN</span>' : '<span class="status-chip warn">CHECKING</span>');
        const latency = t.last_latency_ms != null ? Number(t.last_latency_ms).toFixed(1) + ' ms' : '—';
        const up24 = t.uptime_24h != null ? Number(t.uptime_24h).toFixed(1) + '%' : '100.0%';
        return `<tr>
          <td><strong>${esc(t.name)}</strong></td>
          <td class="mono" style="font-size:0.82rem;">${esc(t.target)}</td>
          <td>${stBadge}</td>
          <td class="mono">${latency}</td>
          <td class="text-right mono"><strong>${up24}</strong></td>
          <td class="text-right">
            <button type="button" class="btn secondary compact" style="color:var(--text-danger);" onclick="window.deleteUptimeTarget(${t.id})">Delete</button>
          </td>
        </tr>`;
      }).join('');
    } catch (e) {
      const body = document.getElementById('uptime-targets-body');
      if (body) body.innerHTML = `<tr><td colspan="6" class="empty-cell" style="color:var(--text-danger);">Error loading uptime targets: ${esc(e.message)}</td></tr>`;
    }
  }

  window.deleteUptimeTarget = async function(id) {
    const ok = await window.systorConfirm('Delete this monitored target?', 'Delete Target');
    if (!ok) return;
    try {
      await window.api(`/api/uptime/targets?id=${id}`, { method: 'DELETE' });
      await loadUptimeTargets();
    } catch (e) {
      await window.systorAlert('Failed to delete target: ' + e.message, 'Error');
    }
  };

  // --- Feature 4: SSL / TLS Expiry Tracker ---
  async function loadSslCerts(extraHost) {
    try {
      let url = '/api/ssl/certs';
      if (extraHost) url += '?host=' + encodeURIComponent(extraHost);
      const j = await window.fetchJSON(url);
      const certs = j.certs || [];
      const meta = document.getElementById('ssl-meta');
      if (meta) meta.textContent = `${certs.length} certificates monitored`;
      const body = document.getElementById('ssl-certs-body');
      if (!body) return;
      if (!certs.length) {
        body.innerHTML = '<tr><td colspan="5" class="empty-cell">No SSL/TLS certificates discovered yet. Enter a domain above to probe.</td></tr>';
        return;
      }
      body.innerHTML = certs.map(c => {
        let stBadge = '<span class="status-chip ok">VALID</span>';
        if (c.error) {
          stBadge = `<span class="status-chip critical" title="${esc(c.error)}">ERROR</span>`;
        } else if (c.is_expired) {
          stBadge = '<span class="status-chip critical">EXPIRED</span>';
        } else if (c.is_warning) {
          stBadge = '<span class="status-chip warn">EXPIRING SOON</span>';
        }
        const days = c.days_remaining != null ? `${c.days_remaining} d` : '—';
        const validUntil = c.valid_until ? new Date(c.valid_until).toLocaleDateString() : '—';
        return `<tr>
          <td><strong>${esc(c.domain)}</strong></td>
          <td style="font-size:0.82rem;color:var(--text-dim);">${esc(c.issuer || 'Local / Auto')}</td>
          <td class="mono">${validUntil}</td>
          <td class="mono"><strong>${days}</strong></td>
          <td class="text-right">${stBadge}</td>
        </tr>`;
      }).join('');
    } catch (e) {
      const body = document.getElementById('ssl-certs-body');
      if (body) body.innerHTML = `<tr><td colspan="5" class="empty-cell" style="color:var(--text-danger);">Error checking SSL certificates: ${esc(e.message)}</td></tr>`;
    }
  }

  // --- Security & Network Audit ---
  async function loadSecurityAudit(force) {
    try {
      const j = await window.fetchJSON('/api/network/security' + (force ? '?refresh=1' : ''));
      const fw = j.firewall || {};
      const f2b = j.fail2ban || {};
      const ports = j.listening_ports || [];

      const fwBadge = document.getElementById('sec-fw-badge');
      if (fwBadge) {
        if (fw.active) {
          fwBadge.className = 'status-chip ok';
          fwBadge.textContent = `UFW Active (${fw.rules_count} rules)`;
        } else {
          fwBadge.className = 'status-chip neutral';
          fwBadge.textContent = fw.available ? 'UFW Inactive' : 'UFW N/A';
        }
      }

      const f2bBadge = document.getElementById('sec-f2b-badge');
      if (f2bBadge) {
        if (f2b.active && f2b.jails_count > 0) {
          f2bBadge.className = 'status-chip ok';
          f2bBadge.textContent = `Fail2ban (${f2b.jails_count} jails, ${f2b.banned_total} banned)`;
        } else {
          f2bBadge.className = 'status-chip neutral';
          f2bBadge.textContent = f2b.available ? 'Fail2ban Inactive' : 'Fail2ban N/A';
        }
      }

      const body = document.getElementById('sec-ports-body');
      if (!body) return;
      if (!ports.length) {
        body.innerHTML = '<tr><td colspan="5" class="empty-cell">No open listening ports detected (or restricted permissions).</td></tr>';
        return;
      }

      body.innerHTML = ports.map(p => `<tr>
        <td class="mono"><strong>:${p.port}</strong></td>
        <td><span class="badge ${p.proto === 'TCP' ? 'ok' : 'info'}">${esc(p.proto)}</span></td>
        <td class="mono">${esc(p.address)}</td>
        <td><strong>${esc(p.process)}</strong></td>
        <td class="text-right mono text-muted">${p.pid ? p.pid : '—'}</td>
      </tr>`).join('');

      const meta = document.getElementById('sec-meta');
      if (meta) meta.textContent = `${ports.length} open ports detected`;
    } catch (e) {
      const body = document.getElementById('sec-ports-body');
      if (body) body.innerHTML = `<tr><td colspan="5" class="empty-cell" style="color:var(--text-danger);">Audit failed: ${esc(e.message)}</td></tr>`;
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    document.addEventListener('click', ev => {
      const btn = ev.target.closest('[data-usage-kind]');
      if (!btn) return;
      const kind = btn.getAttribute('data-usage-kind');
      const dir = Number(btn.getAttribute('data-usage-dir') || 0);
      const st = usageState[kind];
      st.page = Math.max(1, (st.page || 1) + dir);
      const map = {
        daily: ['daily-usage-body', 'daily-pager', 'daily-title-meta', 'daily-period-note'],
        monthly: ['monthly-usage-body', 'monthly-pager', 'monthly-title-meta', null],
        yearly: ['yearly-usage-body', 'yearly-pager', 'yearly-title-meta', null]
      };
      renderUsageTable(kind, ...map[kind]);
    });

    const hideVirt = document.getElementById('hide-virtual-toggle');
    if (hideVirt) {
      hideVirt.addEventListener('change', () => {
        try { localStorage.setItem('systor.network.hideVirtual', hideVirt.checked ? '1' : '0'); } catch (e) {}
        renderInterfaces(lastIfaceRows);
      });
      let storedHide = null;
      try { storedHide = localStorage.getItem('systor.network.hideVirtual'); } catch (e) {}
      if (storedHide !== null) hideVirt.checked = storedHide === '1';
      else hideVirt.checked = NET_CFG.hideVirtual;
    }

    const barDays = document.getElementById('net-bar-days');
    if (barDays) barDays.value = String(NET_CFG.barDays);
    const tableDays = document.getElementById('net-table-days');
    if (tableDays) tableDays.value = String(NET_CFG.tableDays);

    document.getElementById('net-refresh')?.addEventListener('click', refreshAll);
    document.getElementById('net-hours')?.addEventListener('change', refreshAll);
    document.getElementById('net-granularity')?.addEventListener('change', refreshAll);
    document.getElementById('net-bar-days')?.addEventListener('change', refreshAll);
    document.getElementById('net-table-days')?.addEventListener('change', () => {
      usageState.daily.page = 1; usageState.monthly.page = 1; usageState.yearly.page = 1;
      refreshAll();
    });
    document.getElementById('net-iface-filter')?.addEventListener('change', () => {
      usageState.daily.page = 1; usageState.monthly.page = 1; usageState.yearly.page = 1;
      refreshAll();
    });
    document.getElementById('net-refresh-sec')?.addEventListener('change', resetRefreshTimer);

    const upAddBtn = document.getElementById('uptime-add-btn');
    const upCard = document.getElementById('uptime-add-card') || document.getElementById('uptime-add-form');
    if (upAddBtn && upCard) {
      upAddBtn.addEventListener('click', () => {
        upCard.style.display = upCard.style.display === 'none' ? 'block' : 'none';
      });
    }
    const upCancelBtn = document.getElementById('up-cancel-btn');
    if (upCancelBtn && upCard) {
      upCancelBtn.addEventListener('click', () => { upCard.style.display = 'none'; });
    }
    const upSaveBtn = document.getElementById('up-save-btn');
    if (upSaveBtn) {
      upSaveBtn.addEventListener('click', async () => {
        const name = document.getElementById('up-name')?.value.trim();
        const target = (document.getElementById('up-target') || document.getElementById('up-url'))?.value.trim();
        const check_type = document.getElementById('up-type')?.value || 'http';
        const interval_sec = Number(document.getElementById('up-interval')?.value || 60);
        const expected_status = Number((document.getElementById('up-status') || document.getElementById('up-expected'))?.value || 200);
        if (!name || !target) {
          await window.systorAlert('Please provide both name and target URL/host', 'Input Required');
          return;
        }
        upSaveBtn.disabled = true;
        try {
          await window.api('/api/uptime/targets', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, target, check_type, interval_sec, expected_status })
          });
          const nameEl = document.getElementById('up-name');
          if (nameEl) nameEl.value = '';
          const targetEl = document.getElementById('up-target') || document.getElementById('up-url');
          if (targetEl) targetEl.value = '';
          if (upCard) upCard.style.display = 'none';
          await loadUptimeTargets();
        } catch (e) {
          await window.systorAlert(e.message, 'Error');
        } finally {
          upSaveBtn.disabled = false;
        }
      });
    }

    const upCheckBtn = document.getElementById('uptime-check-btn');
    if (upCheckBtn) {
      upCheckBtn.addEventListener('click', async () => {
        upCheckBtn.disabled = true;
        upCheckBtn.textContent = 'Checking…';
        try {
          await window.api('/api/uptime/check-now', { method: 'POST' });
          await loadUptimeTargets();
        } catch (e) {
          console.error(e);
        } finally {
          upCheckBtn.disabled = false;
          upCheckBtn.textContent = 'Check All Now';
        }
      });
    }

    const sslBtn = document.getElementById('ssl-check-btn');
    if (sslBtn) {
      sslBtn.addEventListener('click', async () => {
        const input = document.getElementById('ssl-host-input');
        const host = input ? input.value.trim() : '';
        if (!host) {
          await window.systorAlert('Please enter a host or domain name to check', 'Input Required');
          return;
        }
        sslBtn.disabled = true;
        sslBtn.textContent = 'Checking…';
        try {
          await loadSslCerts(host);
        } finally {
          sslBtn.disabled = false;
          sslBtn.textContent = 'Check Cert';
        }
      });
    }

    document.getElementById('sec-refresh-btn')?.addEventListener('click', () => loadSecurityAudit(true));

    refreshAll();
    loadUptimeTargets();
    loadSslCerts();
    loadSecurityAudit();
    resetRefreshTimer();
  });

  window.addEventListener('resize', () => {
    const nextDefault = window.innerWidth <= 760 ? '30' : '90';
    const barDaysEl = document.getElementById('net-bar-days');
    if (barDaysEl && !['30', '60', '90', '180', '365', 'all'].includes(barDaysEl.value)) {
      barDaysEl.value = nextDefault;
    }
  });
})();
