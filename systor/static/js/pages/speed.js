/**
 * Systor Speed Test Page Controller
 */
(function() {
  'use strict';

  function getSpeedConfig() {
    const el = document.getElementById('speed-cfg-data');
    if (el && el.textContent) {
      try { return JSON.parse(el.textContent); } catch (e) {}
    }
    return {};
  }

  const SPEED_CFG = getSpeedConfig();
  const SPEED_COL = {
    get dl() { return window.COLORS?.cyan || '#22d3ee'; },
    get ul() { return window.COLORS?.ram || '#ff6b8a'; },
    get grid() { return window.COLORS?.grid || 'rgba(255, 255, 255, 0.08)'; },
    get dim() { return window.COLORS?.dim || '#8b949e'; }
  };

  let speedTimer = null;
  let speedBusy = false;
  let speedRunning = false;
  let speedLivePoll = null;
  let speedOptions = [];
  const historyState = {
    page: 1,
    limit: Number(SPEED_CFG.history_page_size || 25),
    provider: 'all',
    run_type: 'all',
    total: 0,
  };

  function esc(s) {
    return String(s ?? '').replace(/[&<>\"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  }

  function fmtMbps(v) {
    return (v === null || v === undefined || Number.isNaN(Number(v))) ? '—' : Number(v).toFixed(Number(v) >= 100 ? 0 : Number(v) >= 10 ? 1 : 2) + ' Mbps';
  }

  function fmtBare(v) {
    return (v === null || v === undefined || Number.isNaN(Number(v))) ? '—' : Number(v).toFixed(Number(v) >= 100 ? 0 : Number(v) >= 10 ? 1 : 2);
  }

  function fmtMs(v) {
    return (v === null || v === undefined || Number.isNaN(Number(v))) ? '—' : Number(v).toFixed(1) + ' ms';
  }

  function fmtPct(v) {
    return (v === null || v === undefined || Number.isNaN(Number(v))) ? '—' : Number(v).toFixed(1) + '%';
  }

  function providerBadge(p) {
    const cls = p === 'ookla' ? 'ookla' : (p === 'speedtest' ? 'legacy' : 'other');
    return `<span class="provider-badge ${cls}">${esc(p)}</span>`;
  }

  function runTypeBadge(t) {
    const cls = t === 'auto' ? 'auto' : 'manual';
    return `<span class="provider-badge ${cls}">${esc(t || 'manual')}</span>`;
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

  function drawLine(canvas, dl, ul) {
    if (!canvas) return;
    const dpr = window.devicePixelRatio || 1;
    const w = Math.max(10, canvas.clientWidth);
    const h = Math.max(150, canvas.clientHeight);
    canvas.width = Math.floor(w * dpr);
    canvas.height = Math.floor(h * dpr);
    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    const all = [...dl.map(x => Number(x[1] || 0)), ...ul.map(x => Number(x[1] || 0))];
    if (!all.length) {
      ctx.fillStyle = SPEED_COL.dim;
      ctx.font = '12px monospace';
      ctx.fillText('waiting for speed history', 12, h / 2);
      return;
    }
    const max = niceMax(Math.max(...all, 0.01));
    const padL = 40, padR = 10, padT = 8, padB = 20;
    const gw = w - padL - padR, gh = h - padT - padB;
    ctx.strokeStyle = SPEED_COL.grid;
    ctx.fillStyle = SPEED_COL.dim;
    ctx.font = '10px monospace';
    for (let i = 0; i <= 3; i++) {
      const y = padT + (gh / 3) * i;
      ctx.beginPath();
      ctx.moveTo(padL, y);
      ctx.lineTo(w - padR, y);
      ctx.stroke();
      ctx.fillText((max - (max / 3) * i).toFixed(max > 100 ? 0 : 1), 2, y + 3);
    }
    const line = (points, color) => {
      if (!points.length) return;
      ctx.beginPath();
      ctx.strokeStyle = color;
      ctx.lineWidth = 1.8;
      points.forEach((p, i) => {
        const x = padL + (points.length > 1 ? gw * i / (points.length - 1) : gw / 2);
        const y = padT + gh - ((Number(p[1] || 0) / max) * gh);
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      });
      ctx.stroke();
    };
    line(dl, SPEED_COL.dl);
    line(ul, SPEED_COL.ul);
  }

  function pickLatest(rows) {
    for (const name of ['ookla', 'speedtest']) {
      const hit = (rows || []).find(r => r.provider === name);
      if (hit) return hit;
    }
    return null;
  }

  function fillCurrent(row) {
    const curDl = document.getElementById('cur-dl');
    if (curDl) curDl.textContent = row ? fmtBare(row.dl_mbps) : '—';
    const curUl = document.getElementById('cur-ul');
    if (curUl) curUl.textContent = row ? fmtBare(row.ul_mbps) : '—';
    const curPing = document.getElementById('cur-ping');
    if (curPing) curPing.textContent = row ? fmtMs(row.ping_ms) : '—';
    const curJitter = document.getElementById('cur-jitter');
    if (curJitter) curJitter.textContent = row ? fmtMs(row.jitter_ms) : '—';
    const curLoss = document.getElementById('cur-loss');
    if (curLoss) curLoss.textContent = row ? fmtPct(row.packet_loss) : '—';
    const curServer = document.getElementById('cur-server');
    if (curServer) curServer.textContent = row ? (row.target || row.server_id || 'auto') : '—';
    if (!speedRunning) updateSelectedTargetDisplay();
  }

  const SPEED_PRESETS = [
    { id: '33496', label: 'MS Online · Dhaka, Bangladesh' },
    { id: '73779', label: 'VOLO NET · Dhaka, Bangladesh' },
    { id: '70157', label: 'Carnival Internet · Dhaka, Bangladesh' },
    { id: '13623', label: 'Singtel · Singapore, Singapore' },
    { id: '1755',  label: 'Vodafone Albania · Tirana, Albania' },
    { id: '4227',  label: 'Telecom Personal · Santa Fe, Argentina' },
  ];

  function ensureServerOption(id, label) {
    if (!id) return;
    const sel = document.getElementById('ookla-server-select');
    if (!sel) return;
    if ([...sel.options].some(o => o.value === String(id))) return;
    const opt = document.createElement('option');
    opt.value = String(id);
    opt.textContent = label || ('server ' + id);
    sel.appendChild(opt);
  }

  function updateSelectedTargetDisplay() {
    const sel = document.getElementById('ookla-server-select');
    const txt = sel?.selectedOptions?.[0]?.textContent?.trim() || 'auto';
    const targetEl = document.getElementById('speed-live-target');
    if (targetEl) targetEl.textContent = txt;
    const labelTopEl = document.getElementById('speed-go-label-top');
    if (labelTopEl) labelTopEl.textContent = txt.split('·')[0].trim() || txt;
  }

  function renderScheduleSummary(sc) {
    const box = document.getElementById('speed-schedule-current');
    if (!box) return;
    const hasSaved = !!(sc.auto_enabled || sc.notify_enabled || sc.ookla_server_id);
    const server = sc.ookla_server_id || 'auto';
    const active = !!sc.auto_enabled;
    if (!hasSaved) {
      box.innerHTML = '<div class="speed-saved-empty"><strong>No saved schedule</strong><span>Enable autorun and save when you want a stored schedule.</span></div>';
      return;
    }
    box.innerHTML = `
      <div class="speed-saved-head"><span>Saved schedule</span><span class="badge ${active ? 'ok' : 'warn'}">${active ? 'enabled' : 'disabled'}</span></div>
      <div class="speed-saved-grid">
        <div><span>Every</span><strong>${sc.auto_interval_min || 180} min</strong></div>
        <div><span>Server</span><strong>${esc(server)}</strong></div>
        <div><span>Min DL</span><strong>${Number(sc.min_download_mbps ?? 0).toFixed(1)} Mbps</strong></div>
        <div><span>Min UL</span><strong>${Number(sc.min_upload_mbps ?? 0).toFixed(1)} Mbps</strong></div>
        <div><span>Notify</span><strong>${sc.notify_enabled ? 'on' : 'off'}</strong></div>
        <div><span>Status poll</span><strong>${sc.page_refresh_sec || 0}s</strong></div>
      </div>
      <div class="speed-saved-actions">
        <button type="button" class="btn secondary compact" id="speed-disable-schedule">Disable</button>
        <button type="button" class="btn secondary compact" id="speed-clear-schedule">Delete</button>
      </div>`;
    document.getElementById('speed-disable-schedule')?.addEventListener('click', async () => {
      const autoEn = document.getElementById('auto-enabled-input');
      if (autoEn) autoEn.checked = false;
      await saveSettings();
    });
    document.getElementById('speed-clear-schedule')?.addEventListener('click', async () => {
      const autoEn = document.getElementById('auto-enabled-input');
      if (autoEn) autoEn.checked = false;
      const notifyEn = document.getElementById('auto-notify-input');
      if (notifyEn) notifyEn.checked = false;
      const intInput = document.getElementById('auto-interval-input');
      if (intInput) intInput.value = '180';
      const minDl = document.getElementById('auto-min-dl-input');
      if (minDl) minDl.value = '50';
      const minUl = document.getElementById('auto-min-ul-input');
      if (minUl) minUl.value = '20';
      const ooklaId = document.getElementById('ookla-server-id-input');
      if (ooklaId) ooklaId.value = '';
      const ooklaSel = document.getElementById('ookla-server-select');
      if (ooklaSel) ooklaSel.value = '';
      const resEl = document.getElementById('speed-save-result');
      if (resEl) resEl.textContent = 'schedule deleted';
      updateSelectedTargetDisplay();
      renderScheduleSummary({
        auto_enabled: false, notify_enabled: false, ookla_server_id: '',
        auto_interval_min: 180, min_download_mbps: 50, min_upload_mbps: 20,
        page_refresh_sec: document.getElementById('speed-page-refresh-input')?.value || 0
      });
      await saveSettings();
    });
  }

  async function pollLiveStatus(loop = false) {
    const tick = async () => {
      const j = await window.fetchJSON('/api/speed/live/status');
      applyLiveState(j.live || {});
      if (!(j.live || {}).running && speedLivePoll) {
        clearInterval(speedLivePoll);
        speedLivePoll = null;
        await refreshAll();
      }
    };
    await tick();
    if (loop) {
      clearInterval(speedLivePoll);
      speedLivePoll = setInterval(() => { tick().catch(() => {}); }, 700);
    }
  }

  function applyLiveState(live) {
    const map = { idle: 6, starting: 6, ping: 25, jitter: 50, download: 75, upload: 75, complete: 100, stopped: 0, failed: 100 };
    if (!live || !Object.keys(live).length) return;
    speedRunning = !!live.running;
    document.getElementById('speed-live-runner')?.classList.toggle('is-running', !!live.running);
    const goBtn = document.getElementById('speed-go-btn');
    if (goBtn) goBtn.disabled = !!live.running;
    const runBtn = document.getElementById('speed-run-btn');
    if (runBtn) {
      runBtn.disabled = !!live.running;
      runBtn.textContent = live.running ? 'Running…' : 'Run speedtest';
    }
    const stopBtn = document.getElementById('speed-stop-btn');
    if (stopBtn) stopBtn.disabled = !live.running;

    if (live.target) {
      const targetEl = document.getElementById('speed-live-target');
      if (targetEl) targetEl.textContent = live.target;
      const labelTopEl = document.getElementById('speed-go-label-top');
      if (labelTopEl) labelTopEl.textContent = String(live.target).split('·')[0].trim();
      const curServer = document.getElementById('cur-server');
      if (curServer) curServer.textContent = live.target;
    }
    if (live.ping_ms !== null && live.ping_ms !== undefined) {
      const el = document.getElementById('cur-ping');
      if (el) el.textContent = fmtMs(live.ping_ms);
    }
    if (live.jitter_ms !== null && live.jitter_ms !== undefined) {
      const el = document.getElementById('cur-jitter');
      if (el) el.textContent = fmtMs(live.jitter_ms);
    }
    if (live.packet_loss !== null && live.packet_loss !== undefined) {
      const el = document.getElementById('cur-loss');
      if (el) el.textContent = fmtPct(live.packet_loss);
    }
    if (live.dl_mbps !== null && live.dl_mbps !== undefined) {
      const el = document.getElementById('cur-dl');
      if (el) el.textContent = fmtBare(live.dl_mbps);
    }
    if (live.ul_mbps !== null && live.ul_mbps !== undefined) {
      const el = document.getElementById('cur-ul');
      if (el) el.textContent = fmtBare(live.ul_mbps);
    }
    const core = document.getElementById('speed-live-core');
    if (core) core.textContent = 'GO';
    const phaseEl = document.getElementById('speed-live-phase');
    if (phaseEl) phaseEl.textContent = live.phase || 'idle';
    const statEl = document.getElementById('speed-run-status');
    if (statEl) statEl.textContent = live.status || 'ready';

    const phase = live.phase || 'idle';
    const phases = ['ping', 'jitter', 'download', 'upload'];
    document.querySelectorAll('.speed-phase-chip').forEach(el => el.classList.remove('active', 'done', 'fail'));
    if (phase === 'complete') document.querySelectorAll('.speed-phase-chip').forEach(el => el.classList.add('done'));
    else if (phase === 'failed' || phase === 'stopped') document.querySelectorAll('.speed-phase-chip').forEach(el => el.classList.add('fail'));
    else if (phases.includes(phase)) {
      phases.forEach((p, idx) => {
        const el = document.getElementById('phase-' + p);
        if (p === phase) el?.classList.add('active');
        if (phases.indexOf(phase) > idx) el?.classList.add('done');
      });
    }
    const fill = document.getElementById('speed-stage-fill');
    if (fill) fill.style.width = `${map[phase] ?? 6}%`;
  }

  async function loadServers() {
    const j = await window.fetchJSON('/api/speed/options');
    speedOptions = j.ookla_servers || [];
    const sel = document.getElementById('ookla-server-select');
    if (!sel) return;
    const current = String(SPEED_CFG.ookla_server_id || '');
    sel.innerHTML = '<option value="">auto</option>' + speedOptions.map(s => `<option value="${esc(s.id)}">${esc(s.label)}</option>`).join('');
    SPEED_PRESETS.forEach(p => ensureServerOption(p.id, p.label));
    if (current) ensureServerOption(current, current);
    if ([...sel.options].some(o => o.value === current)) sel.value = current;
  }

  async function loadStatus() {
    const j = await window.fetchJSON('/api/speed/status');
    const latest = pickLatest(j.latest || []);
    fillCurrent(latest);
    const sc = (j.config || {}).speed || {};
    const poll = String(sc.page_refresh_sec ?? 0);
    const pollSel = document.getElementById('speed-poll-select');
    if (pollSel) pollSel.value = poll;
    const pageRef = document.getElementById('speed-page-refresh-input');
    if (pageRef) pageRef.value = poll;
    const autoEn = document.getElementById('auto-enabled-input');
    if (autoEn) autoEn.checked = !!sc.auto_enabled;
    const notifyEn = document.getElementById('auto-notify-input');
    if (notifyEn) notifyEn.checked = !!sc.notify_enabled;
    const autoInt = document.getElementById('auto-interval-input');
    if (autoInt) autoInt.value = sc.auto_interval_min || 180;
    const minDl = document.getElementById('auto-min-dl-input');
    if (minDl) minDl.value = sc.min_download_mbps ?? 50;
    const minUl = document.getElementById('auto-min-ul-input');
    if (minUl) minUl.value = sc.min_upload_mbps ?? 20;
    const ooklaId = document.getElementById('ookla-server-id-input');
    if (ooklaId) ooklaId.value = sc.ookla_server_id || '';
    const histSize = document.getElementById('history-page-size-input');
    if (histSize) histSize.value = String(sc.history_page_size || 25);
    const schedMeta = document.getElementById('speed-schedule-meta');
    if (schedMeta) schedMeta.textContent = `${sc.auto_enabled ? 'on' : 'off'} · every ${sc.auto_interval_min || 180}m`;
    renderScheduleSummary(sc);
    if ((j.live || {}).running) applyLiveState(j.live); else setRunnerIdle();
  }

  async function loadHistory() {
    const q = new URLSearchParams({
      limit: String(historyState.limit),
      offset: String((historyState.page - 1) * historyState.limit),
      provider: historyState.provider,
      run_type: historyState.run_type,
    });
    const j = await window.fetchJSON('/api/speedtests?' + q.toString());
    const rows = j.rows || [];
    historyState.total = Number(j.total || rows.length);
    const totalPages = Math.max(1, Math.ceil(historyState.total / historyState.limit));
    const meta = document.getElementById('speed-history-meta');
    if (meta) meta.textContent = `${historyState.total} rows · page ${historyState.page}/${totalPages}`;
    const prevBtn = document.getElementById('speed-history-prev');
    if (prevBtn) prevBtn.disabled = historyState.page <= 1;
    const nextBtn = document.getElementById('speed-history-next');
    if (nextBtn) nextBtn.disabled = historyState.page >= totalPages;
    const body = document.getElementById('speed-history-body');
    if (!body) return;
    if (!rows.length) {
      body.innerHTML = '<tr><td colspan="11" class="empty-cell">No history for this filter</td></tr>';
      drawLine(document.getElementById('speed-history-chart'), [], []);
      const chartMeta = document.getElementById('speed-chart-meta');
      if (chartMeta) chartMeta.innerHTML = '<span class="dl-text"><strong>DL —</strong></span> · <span class="ul-text"><strong>UL —</strong></span>';
      return;
    }
    body.innerHTML = rows.map(r => `
      <tr>
        <td class="mono">${new Date((r.ts || 0) * 1000).toLocaleString()}</td>
        <td>${providerBadge(r.provider)}</td>
        <td>${runTypeBadge(r.run_type || 'manual')}</td>
        <td title="${esc(r.note || '')}">${esc(r.target || '—')}</td>
        <td class="mono">${esc(r.server_id || 'auto')}</td>
        <td class="text-right mono dl-text">${fmtMbps(r.dl_mbps)}</td>
        <td class="text-right mono ul-text">${fmtMbps(r.ul_mbps)}</td>
        <td class="text-right mono">${fmtMs(r.ping_ms)}</td>
        <td class="text-right mono">${fmtMs(r.jitter_ms)}</td>
        <td class="text-right mono">${fmtPct(r.packet_loss)}</td>
        <td><span class="badge ${r.ok ? 'ok' : 'fail'}">${r.ok ? 'ok' : 'fail'}</span></td>
      </tr>
    `).join('');
    const chartRows = [...rows].reverse();
    const dl = chartRows.filter(r => r.dl_mbps !== null && r.dl_mbps !== undefined).map(r => [r.ts, Number(r.dl_mbps)]);
    const ul = chartRows.filter(r => r.ul_mbps !== null && r.ul_mbps !== undefined).map(r => [r.ts, Number(r.ul_mbps)]);
    drawLine(document.getElementById('speed-history-chart'), dl, ul);
    const lastDl = dl.length ? dl[dl.length - 1][1] : null;
    const lastUl = ul.length ? ul[ul.length - 1][1] : null;
    const chartMeta = document.getElementById('speed-chart-meta');
    if (chartMeta) chartMeta.innerHTML = `<span class="dl-text"><strong>DL ${fmtMbps(lastDl)}</strong></span> · <span class="ul-text"><strong>UL ${fmtMbps(lastUl)}</strong></span>`;
  }

  async function refreshAll() {
    if (speedBusy) return;
    speedBusy = true;
    const upd = document.getElementById('speed-updated');
    if (upd) upd.textContent = 'loading…';
    try {
      await Promise.all([loadStatus(), loadHistory()]);
      if (upd) upd.textContent = 'updated ' + new Date().toLocaleTimeString();
    } catch (e) {
      if (upd) upd.textContent = 'error: ' + e.message;
    } finally {
      speedBusy = false;
    }
  }

  function resetTimer(sec) {
    clearInterval(speedTimer);
    const raw = (sec ?? document.getElementById('speed-poll-select')?.value ?? 0);
    const val = Number(raw || 0);
    if (val <= 0) return;
    speedTimer = setInterval(refreshAll, val * 1000);
  }

  function setRunnerIdle() {
    speedRunning = false;
    clearInterval(speedLivePoll);
    speedLivePoll = null;
    document.getElementById('speed-live-runner')?.classList.remove('is-running');
    const goBtn = document.getElementById('speed-go-btn');
    if (goBtn) goBtn.disabled = false;
    const stopBtn = document.getElementById('speed-stop-btn');
    if (stopBtn) stopBtn.disabled = true;
    const runBtn = document.getElementById('speed-run-btn');
    if (runBtn) {
      runBtn.disabled = false;
      runBtn.textContent = 'Run speedtest';
    }
    const core = document.getElementById('speed-live-core');
    if (core) core.textContent = 'GO';
    const phaseEl = document.getElementById('speed-live-phase');
    if (phaseEl) phaseEl.textContent = 'idle';
    const statEl = document.getElementById('speed-run-status');
    if (statEl) statEl.textContent = 'ready';
    document.querySelectorAll('.speed-phase-chip').forEach(el => el.classList.remove('active', 'done', 'fail'));
    const fill = document.getElementById('speed-stage-fill');
    if (fill) fill.style.width = '6%';
    updateSelectedTargetDisplay();
  }

  async function stopLiveRun() {
    try { await window.api('/api/speed/live/stop', { method: 'POST' }); }
    catch (e) {}
    await pollLiveStatus(false);
  }

  function applyServerPreset(id, label) {
    ensureServerOption(id, label);
    const sel = document.getElementById('ookla-server-select');
    if (sel) sel.value = String(id);
    const idInput = document.getElementById('ookla-server-id-input');
    if (idInput) idInput.value = String(id);
    document.querySelectorAll('.speed-preset-btn').forEach(btn => btn.classList.toggle('active', btn.dataset.serverId === String(id)));
    const targetEl = document.getElementById('speed-live-target');
    if (targetEl) targetEl.textContent = label || ('server ' + id);
    const labelTopEl = document.getElementById('speed-go-label-top');
    if (labelTopEl) labelTopEl.textContent = String(label || ('server ' + id)).split('·')[0].trim();
  }

  async function runOokla() {
    if (speedRunning) return;
    const sel = document.getElementById('ookla-server-select');
    if (!sel) return;
    const serverId = sel.value;
    const target = sel.selectedOptions[0]?.textContent?.trim() || 'auto';
    const stat = document.getElementById('speed-run-status');
    if (stat) stat.textContent = 'starting…';
    try {
      await window.api('/api/speed/live/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ provider: 'ookla', server_id: serverId, target, run_type: 'manual' })
      });
      await pollLiveStatus(true);
    } catch (e) {
      if (stat) stat.textContent = 'error: ' + e.message;
      setRunnerIdle();
    }
  }

  async function saveSettings() {
    const payload = {
      speed_default_provider: 'ookla',
      speed_auto_provider: 'ookla',
      speed_auto_enabled: document.getElementById('auto-enabled-input')?.checked ? 'true' : 'false',
      speed_notify_enabled: document.getElementById('auto-notify-input')?.checked ? 'true' : 'false',
      speed_auto_interval_min: document.getElementById('auto-interval-input')?.value,
      speed_min_download_mbps: document.getElementById('auto-min-dl-input')?.value,
      speed_min_upload_mbps: document.getElementById('auto-min-ul-input')?.value,
      speed_ookla_server_id: document.getElementById('ookla-server-id-input')?.value,
      speed_page_refresh_sec: document.getElementById('speed-page-refresh-input')?.value,
      speed_history_page_size: document.getElementById('history-page-size-input')?.value,
    };
    const result = document.getElementById('speed-save-result');
    if (result) result.textContent = 'saving…';
    try {
      const j = await window.api('/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const j2 = await window.api('/api/apply', { method: 'POST' });
      historyState.limit = Number(document.getElementById('history-page-size-input')?.value || 25);
      const pollSel = document.getElementById('speed-poll-select');
      if (pollSel) pollSel.value = document.getElementById('speed-page-refresh-input')?.value || '0';
      resetTimer(document.getElementById('speed-page-refresh-input')?.value);
      if (result) {
        result.textContent = 'saved · applied';
        result.title = (j.message || 'saved') + ' · ' + (j2.message || 'applied');
      }
      await refreshAll();
    } catch (e) {
      if (result) result.textContent = 'save failed: ' + e.message;
    }
  }

  document.addEventListener('DOMContentLoaded', async () => {
    document.getElementById('speed-refresh-btn')?.addEventListener('click', refreshAll);
    document.querySelectorAll('.speed-preset-btn').forEach(btn => {
      btn.addEventListener('click', () => applyServerPreset(btn.dataset.serverId, btn.dataset.serverLabel));
    });
    document.getElementById('ookla-server-select')?.addEventListener('change', () => {
      const sel = document.getElementById('ookla-server-select');
      const idInput = document.getElementById('ookla-server-id-input');
      if (idInput && sel) idInput.value = sel.value;
      document.querySelectorAll('.speed-preset-btn').forEach(btn => btn.classList.toggle('active', btn.dataset.serverId === sel?.value));
      updateSelectedTargetDisplay();
    });
    document.getElementById('speed-run-btn')?.addEventListener('click', runOokla);
    document.getElementById('speed-go-btn')?.addEventListener('click', runOokla);
    document.getElementById('speed-stop-btn')?.addEventListener('click', stopLiveRun);
    document.getElementById('speed-poll-select')?.addEventListener('change', (e) => resetTimer(e.target.value));
    document.getElementById('speed-save-settings')?.addEventListener('click', saveSettings);
    document.getElementById('speed-history-provider')?.addEventListener('change', () => {
      historyState.provider = document.getElementById('speed-history-provider')?.value || 'all';
      historyState.page = 1;
      loadHistory();
    });
    document.getElementById('speed-history-type')?.addEventListener('change', () => {
      historyState.run_type = document.getElementById('speed-history-type')?.value || 'all';
      historyState.page = 1;
      loadHistory();
    });
    document.getElementById('speed-history-prev')?.addEventListener('click', () => {
      historyState.page = Math.max(1, historyState.page - 1);
      loadHistory();
    });
    document.getElementById('speed-history-next')?.addEventListener('click', () => {
      historyState.page += 1;
      loadHistory();
    });

    await loadServers();
    historyState.limit = Number(SPEED_CFG.history_page_size || 25);
    await refreshAll();
    resetTimer(Number(SPEED_CFG.page_refresh_sec || 0));
    setRunnerIdle();
    updateSelectedTargetDisplay();
  });

  window.addEventListener('resize', () => {
    clearTimeout(window.__speedResize);
    window.__speedResize = setTimeout(loadHistory, 150);
  });
})();
