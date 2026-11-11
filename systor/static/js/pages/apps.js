/**
 * Systor Applications & Stacks Page Controller
 */
(function() {
  'use strict';

  let appsTimer = null;
  let appsLoading = false;
  const state = { rawRows: [], pages: { cpu: 1, mem: 1, disk: 1, net: 1, all: 1 } };

  function bytesMB(n) {
    n = Number(n || 0);
    if (n >= 1024) return (n / 1024).toFixed(2) + ' GB';
    return n.toFixed(2) + ' MB';
  }

  function esc(s) {
    return String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  }

  function metricVal(row, metric) {
    if (metric === 'cpu') return Number(row.cpu_percent || 0).toFixed(2) + '%';
    if (metric === 'mem') return bytesMB(row.mem_mb || 0);
    if (metric === 'disk') return bytesMB((row.disk_in_mb || 0) + (row.disk_out_mb || 0));
    return bytesMB((row.net_in_mb || 0) + (row.net_out_mb || 0));
  }

  function usageCell(row, metric) {
    const cpu = Math.min(100, Number(row.cpu_percent || 0));
    const mem = Math.min(100, Number(row.mem_percent || 0) || Math.min(100, Number(row.mem_mb || 0) / 50));
    const diskMb = (Number(row.disk_in_mb || 0) + Number(row.disk_out_mb || 0));
    const disk = Math.min(100, diskMb * 2);
    const netMb = (Number(row.net_in_mb || 0) + Number(row.net_out_mb || 0));
    const net = Math.min(100, netMb * 2);
    const val = metric === 'cpu' ? `${Number(row.cpu_percent || 0).toFixed(1)}%` : metric === 'mem' ? bytesMB(row.mem_mb || 0) : metric === 'disk' ? bytesMB(diskMb) : bytesMB(netMb);
    const cls = metric === 'cpu' ? 'cpu' : metric === 'mem' ? 'mem' : metric === 'disk' ? 'disk' : 'net';
    const width = metric === 'cpu' ? cpu : metric === 'mem' ? mem : metric === 'disk' ? disk : net;
    const label = metric === 'cpu' ? 'CPU' : metric === 'mem' ? 'RAM' : metric === 'disk' ? 'DSK' : 'NET';
    return `<div class="single-app-bar"><span class="${cls === 'cpu' ? 'rx-text' : cls === 'mem' ? 'tx-text' : cls === 'disk' ? 'disk-text' : 'net-text'}">${label}</span><strong>${val}</strong><div class="app-bar"><i class="${cls}" style="width:${width}%"></i></div></div>`;
  }

  function rowName(r) {
    const pid = r.kind === 'host' && r.pid ? `<span class="app-pid mono">pid ${r.pid}</span>` : '';
    return `<div class="app-inline-cell"><strong class="app-inline-name">${esc(r.name)}</strong>${pid}</div>`;
  }

  function kindPill(kind) {
    const text = esc(kind || 'app');
    const cls = String(kind || 'app').replace(/[^a-z0-9_-]/gi, '').toLowerCase() || 'app';
    return `<span class="kind-pill ${cls}">${text}</span>`;
  }

  function metricSort(metric) {
    return (a, b) => {
      const va = metric === 'cpu' ? Number(a.cpu_percent || 0) : metric === 'mem' ? Number(a.mem_mb || 0) : metric === 'disk' ? Number(a.disk_in_mb || 0) + Number(a.disk_out_mb || 0) : Number(a.net_in_mb || 0) + Number(a.net_out_mb || 0);
      const vb = metric === 'cpu' ? Number(b.cpu_percent || 0) : metric === 'mem' ? Number(b.mem_mb || 0) : metric === 'disk' ? Number(b.disk_in_mb || 0) + Number(b.disk_out_mb || 0) : Number(b.net_in_mb || 0) + Number(b.net_out_mb || 0);
      return vb - va;
    };
  }

  function pageSlice(rows, key, pageSize) {
    const totalPages = Math.max(1, Math.ceil(rows.length / pageSize));
    state.pages[key] = Math.min(totalPages, Math.max(1, state.pages[key] || 1));
    const start = (state.pages[key] - 1) * pageSize;
    return { totalPages, pageRows: rows.slice(start, start + pageSize) };
  }

  function renderPager(key, totalRows, pageSize, mountId) {
    const mount = document.getElementById(mountId);
    if (!mount) return;
    const totalPages = Math.max(1, Math.ceil(totalRows / pageSize));
    const page = Math.min(totalPages, Math.max(1, state.pages[key] || 1));
    const prevSvg = '<svg viewBox="0 0 24 24" aria-hidden="true"><polyline points="15 18 9 12 15 6"></polyline></svg>';
    const nextSvg = '<svg viewBox="0 0 24 24" aria-hidden="true"><polyline points="9 18 15 12 9 6"></polyline></svg>';
    mount.innerHTML = `<button class="pager-btn" ${page <= 1 ? 'disabled' : ''} data-page-key="${key}" data-page-dir="-1" aria-label="Previous page" type="button">${prevSvg}</button><span class="pager-text">${page}/${totalPages}</span><button class="pager-btn" ${page >= totalPages ? 'disabled' : ''} data-page-key="${key}" data-page-dir="1" aria-label="Next page" type="button">${nextSvg}</button>`;
  }

  function renderRows(metric, rows, bodyId, key, pagerId, titleId, pageSize, scope, note = '') {
    const body = document.getElementById(bodyId);
    if (!body) return;
    if (!(rows || []).length) {
      body.innerHTML = '<tr><td colspan="4" class="empty-cell">No apps found</td></tr>';
      const titleEl = document.getElementById(titleId);
      if (titleEl) titleEl.textContent = `0 rows · ${scope}${note}`;
      renderPager(key, 0, pageSize, pagerId);
      return;
    }
    const { totalPages, pageRows } = pageSlice(rows, key, pageSize);
    body.innerHTML = pageRows.map(r => `<tr><td>${rowName(r)}</td><td>${kindPill(r.kind)}</td><td class="text-right mono">${metricVal(r, metric)}</td><td>${usageCell(r, metric)}</td></tr>`).join('');
    const titleEl = document.getElementById(titleId);
    if (titleEl) titleEl.textContent = `${pageRows.length}/${rows.length} · ${scope} · ${state.pages[key]}/${totalPages}${note}`;
    renderPager(key, rows.length, pageSize, pagerId);
  }

  function renderAllRows(rows, scope) {
    const pageSize = Number(document.getElementById('apps-all-limit')?.value || 25);
    const body = document.getElementById('apps-all-body');
    if (!body) return;
    if (!rows.length) {
      body.innerHTML = '<tr><td colspan="5" class="empty-cell">No apps found</td></tr>';
      const meta = document.getElementById('apps-all-meta');
      if (meta) meta.textContent = '0 rows';
      renderPager('all', 0, pageSize, 'all-pager');
      return;
    }
    const { totalPages, pageRows } = pageSlice(rows, 'all', pageSize);
    body.innerHTML = pageRows.map(r => `<tr><td><div class="app-full-name">${rowName(r)}${kindPill(r.kind)}</div></td><td class="text-right mono">${metricVal(r, 'cpu')}</td><td class="text-right mono">${metricVal(r, 'mem')}</td><td class="text-right mono">${metricVal(r, 'disk')}</td><td class="text-right mono">${metricVal(r, 'net')}</td></tr>`).join('');
    const meta = document.getElementById('apps-all-meta');
    if (meta) meta.textContent = `${rows.length} rows · ${scope} · page ${state.pages.all}/${totalPages}`;
    renderPager('all', rows.length, pageSize, 'all-pager');
  }

  function repaintFromRows(scope, summary) {
    const rows = state.rawRows || [];
    const pageSize = Number(document.getElementById('apps-limit')?.value || 10);
    const cpuRows = [...rows].sort(metricSort('cpu'));
    const memRows = [...rows].sort(metricSort('mem'));
    const diskRows = [...rows].sort(metricSort('disk'));
    const netRows = [...rows].sort(metricSort('net'));
    renderRows('cpu', cpuRows, 'apps-cpu-body', 'cpu', 'cpu-pager', 'cpu-title-meta', pageSize, scope);
    renderRows('mem', memRows, 'apps-mem-body', 'mem', 'mem-pager', 'mem-title-meta', pageSize, scope);
    renderRows('disk', diskRows, 'apps-disk-body', 'disk', 'disk-pager', 'disk-title-meta', pageSize, scope);
    renderRows('net', netRows, 'apps-net-body', 'net', 'net-pager', 'net-title-meta', pageSize, scope, ' · host net limited');
    renderAllRows(rows, scope);

    const hCpu = document.getElementById('host-cpu');
    if (hCpu) hCpu.textContent = (summary.host_cpu || 0).toFixed(2) + '%';
    const hMem = document.getElementById('host-mem');
    if (hMem) hMem.textContent = bytesMB(summary.host_mem_mb || 0);
    const dCpu = document.getElementById('docker-cpu');
    if (dCpu) dCpu.textContent = (summary.docker_cpu || 0).toFixed(2) + '%';
    const dMem = document.getElementById('docker-mem');
    if (dMem) dMem.textContent = bytesMB(summary.docker_mem_mb || 0);
    const hDisk = document.getElementById('host-disk');
    if (hDisk) hDisk.textContent = bytesMB(summary.host_disk_mb || 0);
    const dDisk = document.getElementById('docker-disk');
    if (dDisk) dDisk.textContent = bytesMB(summary.docker_disk_mb || 0);
    const hSub = document.getElementById('host-sub');
    if (hSub) hSub.textContent = `${summary.host_count || 0} apps`;
    const dSub = document.getElementById('docker-sub');
    if (dSub) dSub.textContent = `${summary.docker_count || 0} containers`;
  }

  async function loadApps() {
    const scope = document.getElementById('apps-scope')?.value || 'all';
    const j = await window.fetchJSON(`/api/apps?scope=${scope}&sort=raw&limit=1000`);
    state.rawRows = j.rows || [];
    state.summary = j.summary || {};
    const meta = document.getElementById('apps-meta');
    if (meta) meta.textContent = `host ${j.host_count} · docker ${j.docker_count} · full table below`;
    repaintFromRows(scope, state.summary);
  }

  function resetAppsTimer() {
    clearInterval(appsTimer);
    const sec = Number(document.getElementById('apps-refresh-sec')?.value || 15);
    appsTimer = setInterval(() => { if (!appsLoading) refreshApps(); }, sec * 1000);
  }

  async function refreshApps() {
    if (appsLoading) return;
    appsLoading = true;
    const upd = document.getElementById('apps-updated');
    if (upd) upd.textContent = 'loading…';
    try {
      await loadApps();
      if (upd) upd.textContent = 'updated ' + new Date().toLocaleTimeString();
    } catch (e) {
      if (upd) upd.textContent = 'error: ' + e.message;
    } finally {
      appsLoading = false;
    }
  }

  // --- Systemd Services ---
  let systemdUnits = [];
  async function loadSystemdServices() {
    const body = document.getElementById('systemd-units-body');
    if (!body) return;
    body.innerHTML = '<tr><td colspan="5" class="empty-cell">Loading system services…</td></tr>';
    try {
      const j = await window.fetchJSON('/api/apps/systemd');
      if (!j.available) {
        body.innerHTML = `<tr><td colspan="5" class="empty-cell">${esc(j.error || 'systemd unavailable')}</td></tr>`;
        const meta = document.getElementById('systemd-meta');
        if (meta) meta.textContent = 'systemd not active';
        return;
      }
      systemdUnits = j.services || [];
      renderSystemdRows();
    } catch (e) {
      body.innerHTML = `<tr><td colspan="5" class="empty-cell">${esc(e.message)}</td></tr>`;
    }
  }

  function renderSystemdRows() {
    const body = document.getElementById('systemd-units-body');
    if (!body) return;
    const q = (document.getElementById('systemd-search')?.value || '').trim().toLowerCase();
    const filterState = document.getElementById('systemd-state-filter')?.value || 'active';
    let rows = systemdUnits;
    if (filterState !== 'all') {
      rows = rows.filter(r => (r.active || '').toLowerCase() === filterState);
    }
    if (q) {
      rows = rows.filter(r => (r.unit || '').toLowerCase().includes(q) || (r.description || '').toLowerCase().includes(q));
    }
    const meta = document.getElementById('systemd-meta');
    if (meta) meta.textContent = `${rows.length}/${systemdUnits.length} units`;
    if (!rows.length) {
      body.innerHTML = '<tr><td colspan="5" class="empty-cell">No matching system services found</td></tr>';
      return;
    }
    body.innerHTML = rows.slice(0, 50).map(u => {
      const stateBadge = u.active === 'active' ? '<span class="status-chip ok">active</span>' : (u.active === 'failed' ? '<span class="status-chip critical">failed</span>' : `<span class="badge default">${esc(u.active)}</span>`);
      return `<tr>
        <td class="mono"><strong>${esc(u.unit)}</strong></td>
        <td>${stateBadge}</td>
        <td class="mono">${esc(u.sub || '—')}</td>
        <td class="network-mini">${esc(u.description || '—')}</td>
        <td class="text-right" style="white-space:nowrap;">
          <button type="button" class="btn secondary compact" style="margin-right:0.35rem;" onclick="window.openLogStreamer('unit','${esc(u.unit)}')">Logs</button>
          <button type="button" class="btn secondary compact" onclick="window.restartService('${esc(u.unit)}')">Restart</button>
        </td>
      </tr>`;
    }).join('');
  }

  async function restartService(unit) {
    const ok = await window.systorConfirm(`Are you sure you want to safely restart '${unit}'?`, 'Restart Service');
    if (!ok) return;
    try {
      const j = await window.fetchJSON(`/api/apps/systemd/${encodeURIComponent(unit)}/restart`, { method: 'POST' });
      await window.systorAlert(j.message || 'Service restarted', 'Restart Service');
      await loadSystemdServices();
    } catch (e) {
      await window.systorAlert('Failed to restart: ' + e.message, 'Error');
    }
  }
  window.restartService = restartService;

  // --- Docker Compose Stacks ---
  let composeStacks = [];
  async function loadComposeStacks() {
    const body = document.getElementById('compose-stacks-body');
    if (!body) return;
    body.innerHTML = '<tr><td colspan="5" class="empty-cell">Loading Docker Compose projects…</td></tr>';
    try {
      const j = await window.fetchJSON('/api/apps/compose');
      composeStacks = j.stacks || [];
      const meta = document.getElementById('compose-meta');
      if (meta) meta.textContent = `${composeStacks.length} project stack(s)`;
      if (!composeStacks.length) {
        body.innerHTML = '<tr><td colspan="5" class="empty-cell">No Docker Compose stacks running</td></tr>';
        return;
      }
      body.innerHTML = composeStacks.map(st => {
        const svcs = (st.containers || []).map(c => `<span class="badge default" title="${esc(c.status)}" style="margin:2px;">${esc(c.service || c.name)}</span>`).join('');
        const isCompose = st.is_compose;
        const actBtn = isCompose ? `<button type="button" class="btn secondary compact" onclick="window.restartCompose('${esc(st.project)}')">Restart Stack</button>` : '—';
        return `<tr>
          <td class="mono"><strong>${esc(st.project)}</strong></td>
          <td><div style="display:flex;flex-wrap:wrap;gap:2px;">${svcs}</div></td>
          <td class="text-right mono">${Number(st.total_cpu_pct || 0).toFixed(1)}%</td>
          <td class="text-right mono">${bytesMB(st.total_mem_mb || 0)}</td>
          <td class="text-right">${actBtn}</td>
        </tr>`;
      }).join('');
    } catch (e) {
      body.innerHTML = `<tr><td colspan="5" class="empty-cell">${esc(e.message)}</td></tr>`;
    }
  }

  async function restartCompose(project) {
    const ok = await window.systorConfirm(`Restart all containers in stack '${project}'?`, 'Restart Stack');
    if (!ok) return;
    try {
      const j = await window.fetchJSON(`/api/apps/compose/${encodeURIComponent(project)}/restart`, { method: 'POST' });
      await window.systorAlert(j.message || 'Stack restarted', 'Restart Stack');
      await loadComposeStacks();
    } catch (e) {
      await window.systorAlert('Failed to restart stack: ' + e.message, 'Error');
    }
  }
  window.restartCompose = restartCompose;

  // --- Live Log Streamer (SSE) ---
  let activeLogEventSource = null;
  function openLogStreamer(kind, target) {
    const modal = document.getElementById('log-modal');
    const title = document.getElementById('log-target-title');
    const badge = document.getElementById('log-status-badge');
    const out = document.getElementById('log-terminal-output');
    if (!modal || !out) return;

    if (activeLogEventSource) activeLogEventSource.close();
    out.textContent = '';
    modal.style.display = 'flex';
    modal.classList.add('open');
    title.textContent = `${kind === 'unit' ? 'Service' : 'Container'}: ${target}`;
    badge.textContent = 'Connecting…';
    badge.className = 'network-mini';

    const query = kind === 'unit' ? `unit=${encodeURIComponent(target)}` : `container=${encodeURIComponent(target)}`;
    const es = new EventSource(`/api/apps/logs/stream?${query}&lines=60`);
    activeLogEventSource = es;

    es.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data);
        if (data.status === 'connected') {
          badge.textContent = 'Live Streaming';
          badge.className = 'status-chip ok';
        } else if (data.log) {
          const lineEl = document.createElement('div');
          lineEl.textContent = data.log;
          out.appendChild(lineEl);
          if (document.getElementById('log-autoscroll')?.checked) {
            out.scrollTop = out.scrollHeight;
          }
        } else if (data.error) {
          badge.textContent = 'Error: ' + data.error;
          badge.className = 'status-chip critical';
        }
      } catch (err) {}
    };

    es.onerror = () => {
      badge.textContent = 'Disconnected';
      badge.className = 'network-mini';
      es.close();
    };
  }
  window.openLogStreamer = openLogStreamer;

  function closeLogStreamer() {
    if (activeLogEventSource) {
      activeLogEventSource.close();
      activeLogEventSource = null;
    }
    const modal = document.getElementById('log-modal');
    if (modal) {
      modal.style.display = 'none';
      modal.classList.remove('open');
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    document.addEventListener('click', (ev) => {
      const btn = ev.target.closest('[data-page-key]');
      if (!btn) return;
      const key = btn.getAttribute('data-page-key');
      const dir = Number(btn.getAttribute('data-page-dir') || 0);
      state.pages[key] = Math.max(1, (state.pages[key] || 1) + dir);
      repaintFromRows(document.getElementById('apps-scope')?.value || 'all', state.summary || {});
    });

    document.getElementById('all-pager')?.addEventListener('click', ev => ev.stopPropagation());
    document.getElementById('apps-all-limit')?.addEventListener('click', ev => ev.stopPropagation());
    document.getElementById('apps-refresh')?.addEventListener('click', refreshApps);
    document.getElementById('apps-scope')?.addEventListener('change', () => {
      state.pages = { cpu: 1, mem: 1, disk: 1, net: 1, all: 1 };
      refreshApps();
    });
    document.getElementById('apps-limit')?.addEventListener('change', () => {
      state.pages.cpu = 1; state.pages.mem = 1; state.pages.disk = 1; state.pages.net = 1;
      repaintFromRows(document.getElementById('apps-scope')?.value || 'all', state.summary || {});
    });
    document.getElementById('apps-all-limit')?.addEventListener('change', () => {
      state.pages.all = 1;
      repaintFromRows(document.getElementById('apps-scope')?.value || 'all', state.summary || {});
    });
    document.getElementById('apps-refresh-sec')?.addEventListener('change', resetAppsTimer);

    document.getElementById('systemd-refresh-btn')?.addEventListener('click', loadSystemdServices);
    document.getElementById('systemd-search')?.addEventListener('input', renderSystemdRows);
    document.getElementById('systemd-state-filter')?.addEventListener('change', renderSystemdRows);

    document.getElementById('compose-refresh-btn')?.addEventListener('click', loadComposeStacks);
    document.getElementById('log-close-btn')?.addEventListener('click', closeLogStreamer);
    document.getElementById('log-clear-btn')?.addEventListener('click', () => {
      const out = document.getElementById('log-terminal-output');
      if (out) out.textContent = '';
    });

    refreshApps();
    resetAppsTimer();
    loadSystemdServices();
    loadComposeStacks();
  });
})();
