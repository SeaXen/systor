/**
 * Systor Live Dashboard Controller
 */
(function() {
  'use strict';

  function getDashConfig() {
    const el = document.getElementById('dash-config');
    let defaults = {};
    const cfgScript = document.getElementById('dash-cfg-data');
    if (cfgScript) {
      try {
        defaults = JSON.parse(cfgScript.textContent);
      } catch (e) {
        defaults = {};
      }
    }
    const isPriv = el
      ? el.dataset.clientPrivate === 'true'
      : (document.body.dataset.clientPrivate === 'true');

    return {
      isPrivate: isPriv,
      refreshMs: Number(defaults.refreshMs || el?.dataset.refreshMs || 5000),
      chartMs: Number(defaults.chartMs || el?.dataset.chartMs || 5000),
      rangeHours: Number(defaults.rangeHours || el?.dataset.rangeHours || 6)
    };
  }

  const CONFIG = getDashConfig();
  const IS_PRIVATE = CONFIG.isPrivate;

  const tooltip = document.createElement('div');
  tooltip.className = 'canvas-tooltip hidden';
  document.body.appendChild(tooltip);

  function fmt(n, unit) {
    if (n === null || n === undefined || Number.isNaN(n)) return '—';
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
    return new Date(ts * 1000).toLocaleTimeString();
  }

  function timeAgo(ts) {
    const s = Math.floor(Date.now() / 1000 - ts);
    if (s < 60) return s + 's ago';
    if (s < 3600) return Math.floor(s / 60) + 'm ago';
    if (s < 86400) return Math.floor(s / 3600) + 'h ago';
    return Math.floor(s / 86400) + 'd ago';
  }

  function statusClass(value, y, r) {
    if (value === null || value === undefined || Number.isNaN(value)) return 'gray';
    if (r !== undefined && value > r) return 'red';
    if (y !== undefined && value > y) return 'yellow';
    return 'green';
  }

  function niceStep(v) {
    if (!Number.isFinite(v) || v <= 0) return 1;
    const p = Math.pow(10, Math.floor(Math.log10(v)));
    const n = v / p;
    const m = n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10;
    return m * p;
  }

  function niceBounds(values, ymin, ymax) {
    const clean = values.filter(v => v !== null && v !== undefined && Number.isFinite(v));
    if (!clean.length) return null;
    let lo = ymin !== null && ymin !== undefined ? ymin : Math.min(...clean);
    let hi = ymax !== null && ymax !== undefined ? ymax : Math.max(...clean);
    if (hi === lo) {
      hi = hi + 1;
      lo = ymin !== null && ymin !== undefined ? ymin : lo - 1;
    }
    const raw = (hi - lo) || 1;
    const pad = raw * 0.08;
    if (ymin === null || ymin === undefined) lo = Math.max(0, lo - pad);
    if (ymax === null || ymax === undefined) hi = hi + pad;
    const step = niceStep((hi - lo) / 4);
    if (ymin === null || ymin === undefined) lo = Math.max(0, Math.floor(lo / step) * step);
    if (ymax === null || ymax === undefined) hi = Math.ceil(hi / step) * step;
    if (hi <= lo) hi = lo + step;
    return [lo, hi];
  }

  function showTooltip(html, x, y) {
    tooltip.innerHTML = html;
    tooltip.classList.remove('hidden');
    tooltip.style.left = (x + 14) + 'px';
    tooltip.style.top = (y + 14) + 'px';
  }

  function hideTooltip() {
    tooltip.classList.add('hidden');
  }

  function bindLineTooltip(canvas) {
    if (canvas.__tooltipBound) return;
    canvas.__tooltipBound = true;
    canvas.addEventListener('mouseleave', hideTooltip);
    canvas.addEventListener('mousemove', ev => {
      const meta = canvas.__hoverMeta;
      if (!meta || !meta.groups || !meta.groups.length) return hideTooltip();
      const rect = canvas.getBoundingClientRect();
      const x = ev.clientX - rect.left;
      let best = null;
      for (const g of meta.groups) {
        const dx = Math.abs(g.x - x);
        if (!best || dx < best.dx) best = { dx, g };
      }
      if (!best || best.dx > 18) return hideTooltip();
      const lines = [`<div class="tt-time">${new Date(best.g.ts * 1000).toLocaleString()}</div>`];
      for (const item of best.g.items) {
        lines.push(`<div><span style="color:${item.color};font-weight:700">${item.name}</span>: <strong>${item.value}</strong></div>`);
      }
      showTooltip(lines.join(''), ev.clientX, ev.clientY);
    });
  }

  // D13: Draw chart with nice Y ticks, 4-6 X time labels, and clear zero/empty state
  function drawChart(canvas, series, opts = {}) {
    if (!canvas) return;
    bindLineTooltip(canvas);
    const dpr = window.devicePixelRatio || 1;
    const w = Math.max(10, canvas.clientWidth);
    const h = Math.max(104, canvas.clientHeight);
    canvas.width = Math.floor(w * dpr);
    canvas.height = Math.floor(h * dpr);
    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);

    const colors = window.COLORS || {
      grid: 'rgba(255, 255, 255, 0.08)',
      dim: '#8b949e',
      text: '#e6edf3'
    };

    const allPoints = series.flatMap(s => (s.data || []));
    const allVals = allPoints.map(p => p[1]).filter(v => v !== null && v !== undefined && Number.isFinite(v));

    const padL = 46, padR = 14, padT = 10, padB = 22;
    const gw = Math.max(10, w - padL - padR);
    const gh = Math.max(10, h - padT - padB);

    if (!allVals.length) {
      ctx.fillStyle = colors.dim;
      ctx.font = '11px var(--font-mono, monospace)';
      ctx.textAlign = 'center';
      ctx.fillText('waiting for data…', padL + gw / 2, padT + gh / 2);
      canvas.__hoverMeta = { groups: [] };
      return;
    }

    const bounds = niceBounds(allVals, opts.ymin, opts.ymax);
    if (!bounds) {
      ctx.fillStyle = colors.dim;
      ctx.font = '11px var(--font-mono, monospace)';
      ctx.textAlign = 'center';
      ctx.fillText('waiting for data…', padL + gw / 2, padT + gh / 2);
      canvas.__hoverMeta = { groups: [] };
      return;
    }

    const [lo, hi] = bounds;

    // Grid & Y Ticks (D13: nice ticks)
    ctx.strokeStyle = colors.grid;
    ctx.lineWidth = 1;
    ctx.fillStyle = colors.dim;
    ctx.font = '10px var(--font-mono, monospace)';
    ctx.textAlign = 'right';
    ctx.textBaseline = 'middle';

    const numTicks = 3;
    for (let i = 0; i <= numTicks; i++) {
      const y = padT + (gh / numTicks) * i;
      ctx.beginPath();
      ctx.moveTo(padL, y);
      ctx.lineTo(w - padR, y);
      ctx.stroke();

      const val = hi - ((hi - lo) / numTicks) * i;
      let label = val >= 100 ? val.toFixed(0) : (val >= 10 ? val.toFixed(1) : val.toFixed(2));
      if (val === 0) label = '0';
      ctx.fillText(label, padL - 6, y);
    }

    // Check if flat/zero activity
    const isAllZero = allVals.every(v => v === 0);
    if (isAllZero && opts.showZeroState) {
      ctx.fillStyle = colors.dim;
      ctx.font = '11px var(--font-sans, sans-serif)';
      ctx.textAlign = 'center';
      ctx.fillText('No activity in window', padL + gw / 2, padT + gh / 2);
    }

    // Render Series Lines & Fills
    const groups = [];
    for (const s of series) {
      const data = s.data || [];
      if (!data.length) continue;

      if (s.fill) {
        const grad = ctx.createLinearGradient(0, padT, 0, padT + gh);
        grad.addColorStop(0, (s.fillColor || s.color) + '33');
        grad.addColorStop(1, (s.fillColor || s.color) + '05');
        ctx.beginPath();
        let started = false;
        for (let i = 0; i < data.length; i++) {
          const v = data[i][1];
          if (v === null || v === undefined || !Number.isFinite(v)) continue;
          const x = padL + (data.length > 1 ? (gw * i) / (data.length - 1) : gw / 2);
          const y = padT + gh - ((v - lo) / (hi - lo)) * gh;
          if (!started) {
            ctx.moveTo(x, y);
            started = true;
          } else {
            ctx.lineTo(x, y);
          }
        }
        if (started) {
          ctx.lineTo(padL + gw, padT + gh);
          ctx.lineTo(padL, padT + gh);
          ctx.closePath();
          ctx.fillStyle = grad;
          ctx.fill();
        }
      }

      ctx.beginPath();
      ctx.strokeStyle = s.color;
      ctx.lineWidth = s.width || 1.8;
      let started = false;
      for (let i = 0; i < data.length; i++) {
        const ts = data[i][0];
        const v = data[i][1];
        if (v === null || v === undefined || !Number.isFinite(v)) continue;
        const x = padL + (data.length > 1 ? (gw * i) / (data.length - 1) : gw / 2);
        const y = padT + gh - ((v - lo) / (hi - lo)) * gh;
        if (!started) {
          ctx.moveTo(x, y);
          started = true;
        } else {
          ctx.lineTo(x, y);
        }
        let g = groups.find(p => Math.abs(p.x - x) < 0.5);
        if (!g) {
          g = { x, ts, items: [] };
          groups.push(g);
        }
        g.items.push({ name: s.name, value: fmt(v, s.unit), color: s.color });
      }
      if (started) ctx.stroke();
    }

    // D13: 4-6 time labels on X-axis
    const timestamps = allPoints.map(p => p[0]).filter(ts => ts && Number.isFinite(ts));
    if (timestamps.length >= 2) {
      const minTs = timestamps[0];
      const maxTs = timestamps[timestamps.length - 1];
      const spanTs = maxTs - minTs;
      const xLabels = 4;
      ctx.fillStyle = colors.dim;
      ctx.font = '10px var(--font-mono, monospace)';
      ctx.textBaseline = 'top';

      for (let i = 0; i <= xLabels; i++) {
        const x = padL + (gw / xLabels) * i;
        const curTs = minTs + (spanTs / xLabels) * i;
        const d = new Date(curTs * 1000);
        const label = spanTs > 86400 * 2
          ? d.toLocaleDateString([], { month: 'numeric', day: 'numeric' })
          : d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

        ctx.textAlign = (i === 0) ? 'left' : (i === xLabels ? 'right' : 'center');
        ctx.fillText(label, x, padT + gh + 6);
      }
    }

    canvas.__hoverMeta = { groups };
  }

  async function fetchJSON(url, opts) {
    const r = await fetch(url, opts || { cache: 'no-store' });
    if (!r.ok) {
      let msg = 'HTTP ' + r.status;
      try {
        const j = await r.json();
        msg = j.error || j.message || msg;
      } catch (e) {}
      throw new Error(msg);
    }
    return r.json();
  }

  let settings = {
    refreshMs: Math.max(1000, CONFIG.refreshMs),
    chartMs: Math.max(1000, CONFIG.chartMs),
    rangeHours: CONFIG.rangeHours,
    paused: false
  };

  function setLoadState(text) {
    const el = document.getElementById('load-state');
    if (el) el.textContent = text;
  }

  function setupControls() {
    const refreshSel = document.getElementById('refresh-sec');
    const chartSel = document.getElementById('chart-sec');
    const rangeSel = document.getElementById('range-hours');
    const pauseBtn = document.getElementById('pause-btn');

    if (refreshSel) refreshSel.value = String(settings.refreshMs);
    if (chartSel) chartSel.value = String(settings.chartMs);
    if (rangeSel) rangeSel.value = String(settings.rangeHours);
    if (pauseBtn) pauseBtn.textContent = settings.paused ? 'Resume' : 'Pause';

    refreshSel?.addEventListener('change', () => {
      settings.refreshMs = Math.max(1000, parseInt(refreshSel.value, 10));
      restartTimers();
      refreshAll();
    });
    chartSel?.addEventListener('change', () => {
      settings.chartMs = Math.max(1000, parseInt(chartSel.value, 10));
      restartTimers();
      refreshCharts();
    });
    rangeSel?.addEventListener('change', () => {
      settings.rangeHours = parseFloat(rangeSel.value);
      refreshCharts();
    });
    pauseBtn?.addEventListener('click', () => {
      settings.paused = !settings.paused;
      pauseBtn.textContent = settings.paused ? 'Resume' : 'Pause';
      restartTimers();
    });

    document.getElementById('manual-refresh-btn')?.addEventListener('click', refreshAll);

    const rc = document.getElementById('restart-collector-btn');
    const rw = document.getElementById('restart-web-btn');
    if (rc) {
      rc.addEventListener('click', async () => {
        const ok = await window.SystorDialog.confirm('Restart collector service now?', 'Restart Collector');
        if (!ok) return;
        const j = await fetchJSON('/api/restart-collector', { method: 'POST' });
        setLoadState('collector restarted');
        await window.SystorDialog.alert(j.message || 'Collector restarting', 'Restart Status');
      });
    }
    if (rw) {
      rw.addEventListener('click', async () => {
        const ok = await window.SystorDialog.confirm('Restart dashboard web now?', 'Restart Web');
        if (!ok) return;
        const j = await fetchJSON('/api/restart-web', { method: 'POST' });
        setLoadState('web restarting');
        await window.SystorDialog.alert(j.message || 'Web restarting', 'Restart Status');
      });
    }
  }

  let snapTimer, chartTimer, alertTimer, procTimer;
  const inflight = { snap: false, charts: false, alerts: false, procs: false };

  function restartTimers() {
    clearInterval(snapTimer);
    clearInterval(chartTimer);
    clearInterval(alertTimer);
    clearInterval(procTimer);
    if (settings.paused) return;

    snapTimer = setInterval(refresh, settings.refreshMs);
    if (IS_PRIVATE) {
      chartTimer = setInterval(refreshCharts, settings.chartMs);
      alertTimer = setInterval(refreshAlerts, Math.max(settings.refreshMs, 10000));
      procTimer = setInterval(refreshProcs, 30000);
    }
  }

  function refreshAll() {
    if (IS_PRIVATE) {
      refresh();
      refreshCharts();
      refreshAlerts();
      refreshProcs();
    } else {
      refresh();
    }
  }

  function setGlobalHeader(snap, runtime) {
    const cpu = snap.cpu || {};
    const mem = snap.memory || {};
    const web = (runtime && runtime.web) || {};
    const collector = (runtime && runtime.collector) || {};
    const s = (runtime && runtime.storage) || {};
    const memPct = mem.total_mb ? Math.round(100 * mem.used_mb / mem.total_mb) : null;
    const el = document.getElementById('global-header-meta');
    if (!el) return;

    if (!IS_PRIVATE) {
      el.innerHTML = `<span class="header-chip"><span class="chip-k cpu-chip">CPU</span><strong>${fmt(cpu.percent, '%')}</strong></span><span class="header-chip"><span class="chip-k temp-chip">Temp</span><strong>${fmt(cpu.temp_c, '°C')}</strong></span><span class="header-chip"><span class="chip-k load-chip-mini">Load</span><strong>${fmt(cpu.load_1m)}</strong></span><span class="header-chip"><span class="chip-k ram-chip">RAM</span><strong>${memPct === null ? '—' : memPct + '%'}</strong></span>`;
      return;
    }
    el.innerHTML = `<span class="header-chip"><span class="chip-k cpu-chip">CPU</span><strong>${fmt(cpu.percent, '%')}</strong></span><span class="header-chip"><span class="chip-k temp-chip">Temp</span><strong>${fmt(cpu.temp_c, '°C')}</strong></span><span class="header-chip"><span class="chip-k load-chip-mini">Load</span><strong>${fmt(cpu.load_1m)}</strong></span><span class="header-chip"><span class="chip-k ram-chip">RAM</span><strong>${memPct === null ? '—' : memPct + '%'}</strong></span><span class="header-sep">·</span><span class="header-chip"><span class="chip-k sys-chip">CPU</span><strong>W ${fmt(web.cpu_percent, '%')} · C ${fmt(collector.cpu_percent, '%')}</strong></span><span class="header-chip"><span class="chip-k sys-chip">RAM</span><strong>W ${fmt(web.rss_mb, 'MB')} · C ${fmt(collector.rss_mb, 'MB')}</strong></span><span class="header-chip"><span class="chip-k disk-chip">Disk</span><strong>${fmt(s.total_mb, 'MB')}</strong></span>`;
  }

  function lastVal(data) {
    const p = [...(data || [])].reverse().find(x => x[1] !== null && x[1] !== undefined);
    return p ? p[1] : null;
  }

  function maxVal(data) {
    const vals = (data || []).map(x => x[1]).filter(v => v !== null && v !== undefined);
    return vals.length ? Math.max(...vals) : null;
  }

  function avgVal(data) {
    const vals = (data || []).map(x => Number(x[1])).filter(v => Number.isFinite(v));
    return vals.length ? (vals.reduce((a, b) => a + b, 0) / vals.length) : null;
  }

  async function refresh() {
    if (settings.paused || inflight.snap) return;
    inflight.snap = true;
    setLoadState('loading snapshot…');
    try {
      if (!IS_PRIVATE) {
        const pub = await fetchJSON('/api/public-dashboard?hours=' + settings.rangeHours);
        const snap = pub.snapshot || {};
        const runtime = null;
        renderSnapshotData(snap, pub.metrics || {}, pub.network || []);
        setGlobalHeader(snap, runtime);
        setLoadState('public ' + new Date().toLocaleTimeString());
        return;
      }
      const snap = await fetchJSON('/api/snapshot');
      const runtime = IS_PRIVATE ? await fetchJSON('/api/runtime') : null;
      renderSnapshotData(snap);
      setGlobalHeader(snap, runtime);
      setLoadState('snapshot ' + new Date().toLocaleTimeString());
    } catch (e) {
      console.error(e);
      setLoadState('snapshot error');
    } finally {
      inflight.snap = false;
    }
  }

  function renderSnapshotData(snap, pubMetrics = null, pubNet = null) {
    const updEl = document.getElementById('updated');
    if (updEl) updEl.textContent = 'updated ' + new Date().toLocaleTimeString();
    const hostEl = document.getElementById('hostname');
    if (hostEl) hostEl.textContent = snap.hostname || 'systor';
    const up = snap.uptime_sec || 0;
    const upEl = document.getElementById('uptime');
    if (upEl) upEl.textContent = Math.floor(up / 3600) + 'h ' + Math.floor((up % 3600) / 60) + 'm';
    const dbEl = document.getElementById('dbmeta');
    if (dbEl) dbEl.textContent = (snap.db_stats?.samples || 0) + ' samples';

    const cpu = snap.cpu || {};
    const cpuPctEl = document.getElementById('cpu-pct');
    if (cpuPctEl) cpuPctEl.textContent = fmt(cpu.percent);
    const l1 = document.getElementById('load1');
    if (l1) l1.textContent = fmt(cpu.load_1m);
    const l5 = document.getElementById('load5');
    if (l5) l5.textContent = fmt(cpu.load_5m);
    const l15 = document.getElementById('load15');
    if (l15) l15.textContent = fmt(cpu.load_15m);

    const kpiCpu = document.getElementById('kpi-cpu-pct');
    if (kpiCpu) kpiCpu.className = 'stat-card has-meter ' + statusClass(cpu.percent, 50, 85);
    const barCpu = document.getElementById('bar-cpu');
    if (barCpu) barCpu.style.width = Math.max(0, Math.min(100, Number(cpu.percent || 0))) + '%';

    const kpiLoad = document.getElementById('kpi-load');
    if (kpiLoad) kpiLoad.className = 'stat-card has-meter ' + statusClass(Math.max(cpu.load_1m || 0, cpu.load_5m || 0), 3, 6);
    const barLoad = document.getElementById('bar-load');
    if (barLoad) barLoad.style.width = Math.max(0, Math.min(100, ((Number(cpu.load_1m || 0) / 8) * 100))) + '%';

    const tempEl = document.getElementById('temp');
    if (tempEl) tempEl.textContent = fmt(cpu.temp_c);
    const tempSub = document.getElementById('temp-sub');
    if (tempSub) tempSub.textContent = (cpu.temp_c === null || cpu.temp_c === undefined) ? 'sensor n/a' : 'CPU sensor';
    const kpiTemp = document.getElementById('kpi-temp');
    if (kpiTemp) kpiTemp.className = 'stat-card has-meter ' + statusClass(cpu.temp_c, 70, 85);
    const barTemp = document.getElementById('bar-temp');
    if (barTemp) barTemp.style.width = Math.max(0, Math.min(100, Number(cpu.temp_c || 0))) + '%';

    const mem = snap.memory || {};
    const memPct = mem.total_mb ? Math.round(100 * mem.used_mb / mem.total_mb) : null;
    const memPctEl = document.getElementById('mem-pct');
    if (memPctEl) memPctEl.textContent = memPct !== null ? memPct : '—';
    const memUsed = document.getElementById('mem-used');
    if (memUsed) memUsed.textContent = bytes((mem.used_mb || 0) * 1024 * 1024);
    const memTotal = document.getElementById('mem-total');
    if (memTotal) memTotal.textContent = bytes((mem.total_mb || 0) * 1024 * 1024);
    const kpiMem = document.getElementById('kpi-mem');
    if (kpiMem) kpiMem.className = 'stat-card has-meter ' + statusClass(memPct, 75, 90);
    const barMem = document.getElementById('bar-mem');
    if (barMem) barMem.style.width = Math.max(0, Math.min(100, Number(memPct || 0))) + '%';

    const swapUsed = document.getElementById('swap-used');
    if (swapUsed) swapUsed.textContent = mem.swap_used_mb || 0;
    const swapTotal = document.getElementById('swap-total');
    if (swapTotal) swapTotal.textContent = mem.swap_total_mb || 0;
    const swapPct = (mem.swap_total_mb ? ((Number(mem.swap_used_mb || 0) / Number(mem.swap_total_mb || 1)) * 100) : 0);
    const kpiSwap = document.getElementById('kpi-swap');
    if (kpiSwap) kpiSwap.className = 'stat-card has-meter ' + statusClass(mem.swap_used_mb, 2048, 4096);
    const barSwap = document.getElementById('bar-swap');
    if (barSwap) barSwap.style.width = Math.max(0, Math.min(100, swapPct)) + '%';

    const dio = snap.disk_io || {};
    const diskR = document.getElementById('disk-r-mbps');
    if (diskR) diskR.textContent = fmt(dio.read_mbps);
    const diskW = document.getElementById('disk-w-mbps');
    if (diskW) diskW.textContent = fmt(dio.write_mbps);
    const kpiDisk = document.getElementById('kpi-disk-io');
    if (kpiDisk) kpiDisk.className = 'stat-card has-meter ' + statusClass(Math.max(dio.read_mbps || 0, dio.write_mbps || 0), 20, 80);
    const barDisk = document.getElementById('bar-disk');
    if (barDisk) barDisk.style.width = Math.max(0, Math.min(100, (Math.max(Number(dio.read_mbps || 0), Number(dio.write_mbps || 0)) / 100) * 100)) + '%';

    updateSensors(snap);

    if (pubMetrics) {
      renderPublicCharts(pubMetrics, pubNet);
    }
  }

  function renderPublicCharts(metrics, netData) {
    const C = window.COLORS || {};
    const cpuSeries = metrics.cpu || [];
    const tempSeries = metrics.temp || [];
    const memSeries = metrics.mem || [];
    const dRead = metrics.disk_read || [];
    const dWrite = metrics.disk_write || [];
    const load1 = metrics.load1 || [];
    const load5 = metrics.load5 || [];
    const load15 = metrics.load15 || [];
    const netRx = (netData || []).map(p => [p.ts, p.rx_mbps]);
    const netTx = (netData || []).map(p => [p.ts, p.tx_mbps]);

    drawChart(document.getElementById('chart-cpu-pct'), [{ name: 'CPU', unit: '%', data: cpuSeries, color: C.cpu, fill: true }], { ymin: 0, ymax: 100 });
    drawChart(document.getElementById('chart-temp'), [{ name: 'Temp', unit: '°C', data: tempSeries, color: C.red, fill: true }], { ymin: null, ymax: null });
    drawChart(document.getElementById('chart-mem'), [{ name: 'RAM', unit: 'MB', data: memSeries, color: C.ram, fill: true }], { ymin: 0, ymax: null });
    drawChart(document.getElementById('chart-disk-io'), [{ name: 'Read', unit: 'MB/s', data: dRead, color: C.disk, fill: true }, { name: 'Write', unit: 'MB/s', data: dWrite, color: C.disk2 }], { ymin: 0, ymax: null, showZeroState: true });
    drawChart(document.getElementById('chart-load'), [{ name: '1m', unit: '', data: load1, color: C.green, fill: true }, { name: '5m', unit: '', data: load5, color: C.yellow }, { name: '15m', unit: '', data: load15, color: C.blue }], { ymin: 0, ymax: null });
    drawChart(document.getElementById('chart-net'), [{ name: 'DL', unit: 'MB/s', data: netRx, color: C.cyan, fill: true }, { name: 'UL', unit: 'MB/s', data: netTx, color: C.tx }], { ymin: 0, ymax: null, showZeroState: true });

    document.getElementById('meta-cpu-pct').innerHTML = `<span class="cpu-text"><strong>latest ${fmt(lastVal(cpuSeries), '%')}</strong></span> · avg ${fmt(avgVal(cpuSeries), '%')} · peak ${fmt(maxVal(cpuSeries), '%')}`;
    document.getElementById('meta-temp').innerHTML = `<strong>latest ${fmt(lastVal(tempSeries), '°C')}</strong> · avg ${fmt(avgVal(tempSeries), '°C')} · peak ${fmt(maxVal(tempSeries), '°C')}`;
    document.getElementById('meta-mem').innerHTML = `<span class="ram-text"><strong>latest ${fmt(lastVal(memSeries), 'MB')}</strong></span> · avg ${fmt(avgVal(memSeries), 'MB')} · peak ${fmt(maxVal(memSeries), 'MB')}`;
    document.getElementById('meta-disk-io').innerHTML = `<span class="disk-text"><strong>read ${fmt(lastVal(dRead), 'MB/s')}</strong></span> · avg ${fmt(avgVal(dRead), 'MB/s')} · <span class="disk2-text"><strong>write ${fmt(lastVal(dWrite), 'MB/s')}</strong></span> · avg ${fmt(avgVal(dWrite), 'MB/s')}`;
    document.getElementById('meta-load').innerHTML = `1m ${fmt(lastVal(load1))} avg ${fmt(avgVal(load1))} · 5m ${fmt(lastVal(load5))} avg ${fmt(avgVal(load5))} · 15m ${fmt(lastVal(load15))} avg ${fmt(avgVal(load15))}`;

    const lastDl = lastVal(netRx), lastUl = lastVal(netTx), maxDl = maxVal(netRx), maxUl = maxVal(netTx);
    document.getElementById('net-rx').textContent = fmt(lastDl, 'MB/s');
    document.getElementById('net-tx').textContent = fmt(lastUl, 'MB/s');
    document.getElementById('kpi-net').className = 'stat-card ' + statusClass(Math.max(lastDl || 0, lastUl || 0), 10, 40);
    document.getElementById('meta-net-chart').innerHTML = `<span class="dl-text"><strong>DL ${fmt(lastDl, 'MB/s')}</strong></span> · avg ${fmt(avgVal(netRx), 'MB/s')} · <span class="ul-text"><strong>UL ${fmt(lastUl, 'MB/s')}</strong></span> · avg ${fmt(avgVal(netTx), 'MB/s')}`;
    document.getElementById('bar-net').style.width = ((Math.max(lastDl || 0, lastUl || 0) / Math.max(1, Math.max(maxDl || 0, maxUl || 0))) * 100).toFixed(1) + '%';
  }

  function updateSensors(snap) {
    const sensors = snap.sensors || {};
    const gpuCard = document.getElementById('kpi-gpu');
    if (gpuCard) {
      if (sensors.gpu && sensors.gpu.length > 0) {
        const g = sensors.gpu[0];
        gpuCard.style.display = '';
        document.getElementById('gpu-pct').textContent = fmt(g.gpu_util_pct);
        document.getElementById('gpu-vram').textContent = Math.round(g.mem_used_mb) + '/' + Math.round(g.mem_total_mb) + ' MB';
        document.getElementById('gpu-temp').textContent = fmt(g.temp_c);
        document.getElementById('bar-gpu').style.width = Math.max(0, Math.min(100, Number(g.gpu_util_pct || 0))) + '%';
        gpuCard.className = 'stat-card has-meter ' + statusClass(g.gpu_util_pct, 60, 85);
      } else {
        gpuCard.style.display = 'none';
      }
    }
    const upsCard = document.getElementById('kpi-ups');
    if (upsCard) {
      if (sensors.ups && sensors.ups.present) {
        upsCard.style.display = '';
        document.getElementById('ups-pct').textContent = sensors.ups.battery_pct !== null ? sensors.ups.battery_pct : '—';
        document.getElementById('ups-status').textContent = sensors.ups.status || 'Online';
        document.getElementById('ups-load').textContent = sensors.ups.load_pct !== null ? sensors.ups.load_pct : '0';
        document.getElementById('bar-ups').style.width = Math.max(0, Math.min(100, Number(sensors.ups.battery_pct || 0))) + '%';
      } else {
        upsCard.style.display = 'none';
      }
    }
    const rpiBanner = document.getElementById('rpi-warning-banner');
    if (rpiBanner) {
      if (sensors.rpi && (sensors.rpi.undervoltage || sensors.rpi.throttled)) {
        rpiBanner.style.display = 'flex';
        document.getElementById('rpi-warning-text').textContent = 'Raspberry Pi alert: ' + (sensors.rpi.undervoltage ? 'Under-voltage detected! ' : '') + (sensors.rpi.throttled ? 'CPU frequency throttling active!' : '');
      } else {
        rpiBanner.style.display = 'none';
      }
    }
  }

  async function getSeries(metric) {
    const d = await fetchJSON('/api/series?metric=' + metric + '&hours=' + settings.rangeHours);
    return d.data || [];
  }

  async function refreshCharts() {
    if (!IS_PRIVATE) return;
    if (settings.paused || inflight.charts) return;
    inflight.charts = true;
    setLoadState('loading charts…');
    try {
      const C = window.COLORS || {};
      const [cpu, temp, mem, dRead, dWrite, load1, load5, load15, netSeries] = await Promise.all([
        getSeries('cpu_pct'), getSeries('cpu_temp'), getSeries('mem_used_mb'),
        getSeries('disk_read_mbps'), getSeries('disk_write_mbps'),
        getSeries('load_1m'), getSeries('load_5m'), getSeries('load_15m'),
        fetchJSON('/api/network-series?hours=' + settings.rangeHours)
      ]);
      const netRx = (netSeries.data || []).map(p => [p.ts, p.rx_mbps]);
      const netTx = (netSeries.data || []).map(p => [p.ts, p.tx_mbps]);

      drawChart(document.getElementById('chart-cpu-pct'), [{ name: 'CPU', unit: '%', data: cpu, color: C.cpu, fill: true }], { ymin: 0, ymax: 100 });
      drawChart(document.getElementById('chart-temp'), [{ name: 'Temp', unit: '°C', data: temp, color: C.red, fill: true }], { ymin: null, ymax: null });
      drawChart(document.getElementById('chart-mem'), [{ name: 'RAM', unit: 'MB', data: mem, color: C.ram, fill: true }], { ymin: 0, ymax: null });
      drawChart(document.getElementById('chart-disk-io'), [{ name: 'Read', unit: 'MB/s', data: dRead, color: C.disk, fill: true }, { name: 'Write', unit: 'MB/s', data: dWrite, color: C.disk2 }], { ymin: 0, ymax: null, showZeroState: true });
      drawChart(document.getElementById('chart-load'), [{ name: '1m', unit: '', data: load1, color: C.green, fill: true }, { name: '5m', unit: '', data: load5, color: C.yellow }, { name: '15m', unit: '', data: load15, color: C.blue }], { ymin: 0, ymax: null });
      drawChart(document.getElementById('chart-net'), [{ name: 'DL', unit: 'MB/s', data: netRx, color: C.cyan, fill: true }, { name: 'UL', unit: 'MB/s', data: netTx, color: C.tx }], { ymin: 0, ymax: null, showZeroState: true });

      document.getElementById('meta-cpu-pct').innerHTML = `<span class="cpu-text"><strong>latest ${fmt(lastVal(cpu), '%')}</strong></span> · avg ${fmt(avgVal(cpu), '%')} · peak ${fmt(maxVal(cpu), '%')}`;
      document.getElementById('meta-temp').innerHTML = `<strong>latest ${fmt(lastVal(temp), '°C')}</strong> · avg ${fmt(avgVal(temp), '°C')} · peak ${fmt(maxVal(temp), '°C')}`;
      document.getElementById('meta-mem').innerHTML = `<span class="ram-text"><strong>latest ${fmt(lastVal(mem), 'MB')}</strong></span> · avg ${fmt(avgVal(mem), 'MB')} · peak ${fmt(maxVal(mem), 'MB')}`;
      document.getElementById('meta-disk-io').innerHTML = `<span class="disk-text"><strong>read ${fmt(lastVal(dRead), 'MB/s')}</strong></span> · avg ${fmt(avgVal(dRead), 'MB/s')} · <span class="disk2-text"><strong>write ${fmt(lastVal(dWrite), 'MB/s')}</strong></span> · avg ${fmt(avgVal(dWrite), 'MB/s')}`;
      document.getElementById('meta-load').innerHTML = `1m ${fmt(lastVal(load1))} avg ${fmt(avgVal(load1))} · 5m ${fmt(lastVal(load5))} avg ${fmt(avgVal(load5))} · 15m ${fmt(lastVal(load15))} avg ${fmt(avgVal(load15))}`;

      const lastDl = lastVal(netRx), lastUl = lastVal(netTx), maxDl = maxVal(netRx), maxUl = maxVal(netTx);
      document.getElementById('net-rx').textContent = fmt(lastDl, 'MB/s');
      document.getElementById('net-tx').textContent = fmt(lastUl, 'MB/s');
      document.getElementById('kpi-net').className = 'stat-card ' + statusClass(Math.max(lastDl || 0, lastUl || 0), 10, 40);
      document.getElementById('meta-net-chart').innerHTML = `<span class="dl-text"><strong>DL ${fmt(lastDl, 'MB/s')}</strong></span> · avg ${fmt(avgVal(netRx), 'MB/s')} · <span class="ul-text"><strong>UL ${fmt(lastUl, 'MB/s')}</strong></span> · avg ${fmt(avgVal(netTx), 'MB/s')}`;
      document.getElementById('bar-net').style.width = ((Math.max(lastDl || 0, lastUl || 0) / Math.max(1, Math.max(maxDl || 0, maxUl || 0))) * 100).toFixed(1) + '%';
      setLoadState('charts ' + new Date().toLocaleTimeString());
    } catch (e) {
      console.error(e);
      setLoadState('charts error');
    } finally {
      inflight.charts = false;
    }
  }

  async function refreshAlerts() {
    if (!IS_PRIVATE) return;
    if (settings.paused || inflight.alerts) return;
    inflight.alerts = true;
    try {
      const alerts = await fetchJSON('/api/alerts?limit=20');
      const body = document.getElementById('dash-alerts-body');
      if (!alerts.length) {
        body.innerHTML = '<tr><td colspan="4" class="empty-cell">No alerts — system healthy</td></tr>';
        return;
      }
      body.innerHTML = alerts.map(a => `
        <tr>
          <td><span class="mono">${timeShort(a.ts)}</span> <span class="text-muted">${timeAgo(a.ts)}</span></td>
          <td><span class="badge ${a.severity}">${a.severity}</span></td>
          <td>${esc(a.metric)}</td>
          <td>${esc(a.message)}</td>
        </tr>
      `).join('');
    } catch (e) {
      console.error(e);
    } finally {
      inflight.alerts = false;
    }
  }

  function procColor(v, warn, danger) {
    const C = window.COLORS || {};
    return v > danger ? (C.red || '#f85149') : (v > warn ? (C.yellow || '#d29922') : (C.text || '#e6edf3'));
  }

  function esc(s) {
    return String(s || '').replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
  }

  async function refreshProcs() {
    if (!IS_PRIVATE) return;
    if (settings.paused || inflight.procs) return;
    inflight.procs = true;
    try {
      const [cpuTop, memTop] = await Promise.all([
        fetchJSON('/api/top-processes?by=cpu&n=10'),
        fetchJSON('/api/top-processes?by=mem&n=10')
      ]);
      const cpuBody = document.getElementById('top-cpu-body');
      if (cpuBody) {
        cpuBody.innerHTML = cpuTop.processes?.length ? cpuTop.processes.map(p => `
          <tr>
            <td class="mono text-muted">${p.pid}</td>
            <td title="${esc(p.cmdline)}">${esc(p.name)}</td>
            <td class="text-muted">${esc(p.user)}</td>
            <td class="text-right mono" style="color:${procColor(p.cpu_percent, 20, 50)}">${p.cpu_percent}</td>
            <td class="text-right mono">${p.mem_mb}</td>
          </tr>
        `).join('') : '<tr><td colspan="5" class="empty-cell">No process data available</td></tr>';
      }
      const memBody = document.getElementById('top-mem-body');
      if (memBody) {
        memBody.innerHTML = memTop.processes?.length ? memTop.processes.map(p => `
          <tr>
            <td class="mono text-muted">${p.pid}</td>
            <td title="${esc(p.cmdline)}">${esc(p.name)}</td>
            <td class="text-muted">${esc(p.user)}</td>
            <td class="text-right mono">${p.mem_mb}</td>
            <td class="text-right mono" style="color:${procColor(p.mem_percent, 5, 10)}">${p.mem_percent}</td>
          </tr>
        `).join('') : '<tr><td colspan="5" class="empty-cell">No process data available</td></tr>';
      }
    } catch (e) {
      console.error(e);
    } finally {
      inflight.procs = false;
    }
  }

  setupControls();
  refreshAll();
  restartTimers();

  window.addEventListener('resize', () => {
    clearTimeout(window.__chartResize);
    window.__chartResize = setTimeout(() => {
      refreshCharts();
    }, 150);
  });
})();
