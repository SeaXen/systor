/**
 * Systor Storage Manager Page Controller
 */
(function() {
  'use strict';

  const st = {
    path: '',
    roots: [],
    canWrite: false,
    clipboard: null,
    tab: 'overview',
    contextPath: '',
    allRows: [],
    page: 1,
    pageSize: 25,
    filter: '',
    actionPw: '',
    actionPwRequired: false,
    hasActionPw: false
  };

  const $ = s => document.querySelector(s);
  const $$ = s => [...document.querySelectorAll(s)];

  function esc(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
  }

  function fmtBytes(n) {
    if (n === null || n === undefined || n === '') return '—';
    n = Number(n || 0);
    const u = ['B', 'KB', 'MB', 'GB', 'TB'];
    let i = 0;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return (i ? n.toFixed(n >= 10 ? 1 : 2) : n.toFixed(0)) + ' ' + u[i];
  }

  function short(p) {
    p = String(p || '');
    return p.length > 54 ? '…' + p.slice(-53) : p;
  }

  function renderBreadcrumbs() {
    const box = $('#storage-breadcrumbs');
    if (!box) return;
    const p = String(st.path || '/');
    const parts = p.split('/').filter(Boolean);
    let acc = '';
    const chips = [`<button type="button" data-crumb="/">/</button>`];
    parts.forEach(part => {
      acc += '/' + part;
      chips.push(`<button type="button" data-crumb="${esc(acc)}">${esc(part)}</button>`);
    });
    box.innerHTML = chips.join('<span>/</span>');
  }

  async function api(url, opt) {
    const r = await fetch(url, Object.assign({ cache: 'no-store' }, opt || {}));
    const j = await r.json().catch(() => ({ ok: false, error: 'bad json' }));
    if (!r.ok || j.ok === false) {
      throw new Error(j.error || ('HTTP ' + r.status));
    }
    return j;
  }

  function setTab(t) {
    st.tab = t;
    $$('.storage-tab').forEach(b => {
      const act = b.dataset.tab === t;
      b.classList.toggle('active', act);
      b.setAttribute('aria-selected', act ? 'true' : 'false');
    });
    $$('.storage-panel').forEach(p => p.classList.toggle('active', p.dataset.panel === t));
    if (t === 'ops') {
      loadOps();
      loadTrash();
    }
    if (t === 'smart') {
      loadSmartHealth();
    }
  }

  async function loadSmartHealth(force = false) {
    const body = $('#smart-drives-body');
    if (!body) return;
    body.innerHTML = '<tr><td colspan="8" class="empty-cell">Scanning physical drives…</td></tr>';
    try {
      const j = await api('/api/storage/smart' + (force ? '?refresh=1' : ''));
      const drives = j.drives || [];
      if (!drives.length) {
        body.innerHTML = '<tr><td colspan="8" class="empty-cell">No physical drives detected or smartctl unavailable.</td></tr>';
        return;
      }
      body.innerHTML = drives.map(d => {
        const statusBadge = d.passed
          ? '<span class="status-chip ok">PASSED</span>'
          : '<span class="status-chip critical">FAILED</span>';
        const temp = d.temperature_c !== null && d.temperature_c !== undefined ? d.temperature_c + ' °C' : '—';
        const pOn = d.power_on_hours !== null && d.power_on_hours !== undefined ? d.power_on_hours + 'h' : '—';
        const wear = d.nvme_wear_pct !== null && d.nvme_wear_pct !== undefined
          ? `${d.nvme_wear_pct}% wear`
          : (d.reallocated_sectors !== null && d.reallocated_sectors !== undefined ? `${d.reallocated_sectors} bad sectors` : '0 bad sectors');
        const cap = d.capacity_gb ? `${d.capacity_gb} GB` : '—';
        return `<tr>
          <td class="mono"><strong>${esc(d.device)}</strong></td>
          <td><div>${esc(d.model)}</div><div class="network-mini">${esc(d.serial)}</div></td>
          <td><span class="badge ${d.protocol === 'NVMe' ? 'cyan' : 'default'}">${esc(d.protocol)}</span></td>
          <td>${statusBadge}</td>
          <td class="mono">${esc(temp)}</td>
          <td class="mono">${esc(pOn)}</td>
          <td class="mono">${esc(wear)}</td>
          <td class="text-right mono">${esc(cap)}</td>
        </tr>`;
      }).join('');
    } catch (e) {
      body.innerHTML = `<tr><td colspan="8" class="empty-cell">${esc(e.message)}</td></tr>`;
    }
  }

  function selected() {
    return $$('.storage-row-check:checked').map(x => x.value);
  }

  function fileTone(r) {
    if (r.type === 'dir') return 'dir';
    const ext = String(r.name || '').split('.').pop().toLowerCase();
    if (['mp4', 'mkv', 'webm', 'avi', 'mov'].includes(ext)) return 'video';
    if (['jpg', 'jpeg', 'png', 'webp', 'gif', 'svg'].includes(ext)) return 'image';
    if (['pdf', 'doc', 'docx', 'md', 'txt'].includes(ext)) return 'doc';
    if (['zip', 'rar', '7z', 'tar', 'gz'].includes(ext)) return 'archive';
    return 'file';
  }

  function rowName(r) {
    const tone = fileTone(r);
    const icon = r.type === 'dir' ? '📁' : r.type === 'symlink' ? '🔗' : tone === 'video' ? '🎞️' : tone === 'image' ? '🖼️' : tone === 'doc' ? '📘' : tone === 'archive' ? '🗜️' : '📄';
    return `<button type="button" class="storage-name-btn storage-tone-${tone}" data-open="${esc(r.path)}" data-type="${esc(r.type)}">${icon} <span>${esc(r.name)}</span></button>`;
  }

  function renderRootButtons() {
    const box = $('#storage-root-buttons');
    if (!box) return;
    box.innerHTML = (st.roots || []).map(r =>
      `<button type="button" class="btn secondary compact storage-root-btn ${st.path === r.path ? 'active' : ''}" data-root="${esc(r.path)}">${esc(r.label || r.path)}</button>`
    ).join('');
  }

  async function loadSettings() {
    const j = await api('/api/storage/settings');
    st.roots = j.roots || [];
    st.canWrite = !!j.can_write;

    // D12: Synchronize status wording
    const accessEl = $('#storage-access');
    if (accessEl) {
      accessEl.textContent = st.canWrite ? 'LAN/Tailscale · actions enabled' : 'Read-only · LAN/Tailscale required for write actions';
    }
    const modeEl = $('#st-mode');
    if (modeEl) {
      modeEl.textContent = st.canWrite ? 'Manage' : 'Read-only';
    }
    const noteEl = $('#file-mode-note');
    if (noteEl) noteEl.textContent = '';

    const allowedEl = $('#storage-allowed-roots');
    if (allowedEl) allowedEl.value = (j.config.allowed_roots || []).join('\n');
    const pubRoEl = $('#storage-public-readonly');
    if (pubRoEl) pubRoEl.checked = !!j.config.public_readonly;
    const trashEl = $('#storage-trash-enabled');
    if (trashEl) trashEl.checked = !!j.config.trash_enabled;
    const noRcEl = $('#storage-no-right-click');
    if (noRcEl) noRcEl.checked = !!j.config.no_right_click;
    const pwReqEl = $('#storage-action-password-required');
    if (pwReqEl) pwReqEl.checked = !!j.config.action_password_required;

    st.actionPwRequired = !!j.config.action_password_required;
    st.hasActionPw = !!j.config.has_action_password;

    const pwStateEl = $('#storage-password-state');
    if (pwStateEl) {
      pwStateEl.textContent = st.hasActionPw ? 'Password set' : 'Password not set';
    }

    document.body.classList.toggle('storage-no-context', !!j.config.no_right_click);
    const maxScanEl = $('#storage-max-scan');
    if (maxScanEl) maxScanEl.value = j.config.max_scan_files || 20000;

    if (!st.path && st.roots[0]) {
      st.path = st.roots[0].path;
    }
    renderRootButtons();
  }

  async function loadBrowse() {
    await loadSettings();
    const pathInput = $('#storage-path');
    if (pathInput) pathInput.value = st.path;

    const j = await api('/api/storage/browse?path=' + encodeURIComponent(st.path));
    st.path = j.path;
    st.canWrite = !!j.can_write;

    // D12: Synchronize status wording
    const modeEl = $('#st-mode');
    if (modeEl) modeEl.textContent = st.canWrite ? 'Manage' : 'Read-only';
    const noteEl = $('#file-mode-note');
    if (noteEl) noteEl.textContent = '';
    if (pathInput) pathInput.value = j.path;
    renderRootButtons();

    const usedEl = $('#st-used');
    if (usedEl) usedEl.textContent = j.mount ? j.mount.used_pct + '%' : '—';
    const freeEl = $('#st-free');
    if (freeEl) freeEl.textContent = j.mount ? fmtBytes(j.mount.free) : '—';
    const mountEl = $('#st-mount');
    if (mountEl) mountEl.textContent = j.mount ? j.mount.mount : '—';

    const sm = j.summary || {};
    const sizeEl = $('#st-folder-size');
    if (sizeEl) sizeEl.textContent = fmtBytes(sm.total_size || 0);
    const countEl = $('#st-folder-count');
    if (countEl) countEl.textContent = `${sm.dirs || 0} dirs · ${sm.files || 0} files`;

    st.allRows = j.entries || [];
    st.page = 1;
    renderBreadcrumbs();
    renderFiles();
    renderLargest(j.entries || []);
    renderTypes(j.types || []);
    loadMounts();
  }

  function filteredRows() {
    const q = st.filter.trim().toLowerCase();
    let rows = st.allRows || [];
    if (q) {
      rows = rows.filter(r =>
        (r.name || '').toLowerCase().includes(q) ||
        (r.path || '').toLowerCase().includes(q) ||
        (r.type || '').toLowerCase().includes(q)
      );
    }
    return rows;
  }

  function renderFiles() {
    const rows = filteredRows();
    const body = $('#storage-files-body');
    st.pageSize = Number($('#storage-page-size')?.value || 25);
    const pages = Math.max(1, Math.ceil(rows.length / st.pageSize));
    st.page = Math.min(Math.max(1, st.page), pages);
    const start = (st.page - 1) * st.pageSize;
    const view = rows.slice(start, start + st.pageSize);

    const infoEl = $('#storage-page-info');
    if (infoEl) infoEl.textContent = `${rows.length} items · page ${st.page}/${pages}`;

    if (!view.length) {
      // D12: Empty state with call to action
      body.innerHTML = '<tr><td colspan="5" class="empty-cell">Empty folder. Upload files or select another root.</td></tr>';
      return;
    }

    body.innerHTML = view.map(r => `
      <tr data-row-path="${esc(r.path)}" data-row-type="${esc(r.type)}">
        <td><input class="storage-row-check" type="checkbox" value="${esc(r.path)}" aria-label="Select ${esc(r.name)}"></td>
        <td>${rowName(r)}</td>
        <td><span class="storage-type-badge storage-tone-${fileTone(r)}">${esc(r.type)}</span></td>
        <td class="text-right mono">${fmtBytes(r.size)}</td>
        <td class="mono">${esc(r.mtime_text)}</td>
      </tr>
    `).join('');
  }

  function renderLargest(rows) {
    const body = $('#storage-largest-body');
    if (!body) return;
    const top = [...rows].sort((a, b) => (b.size || 0) - (a.size || 0)).slice(0, 10);
    body.innerHTML = top.length ? top.map(r => `
      <tr>
        <td>${rowName(r)}</td>
        <td><span class="storage-type-badge storage-tone-${fileTone(r)}">${esc(r.type)}</span></td>
        <td class="text-right mono">${fmtBytes(r.size)}</td>
      </tr>
    `).join('') : '<tr><td colspan="3" class="empty-cell">No files found</td></tr>';
  }

  function renderTypes(rows) {
    const body = $('#storage-types-body');
    if (!body) return;
    body.innerHTML = rows.length ? rows.map(r => `
      <tr>
        <td>${esc(r.ext)}</td>
        <td class="text-right mono">${r.count}</td>
        <td class="text-right mono">${fmtBytes(r.size)}</td>
      </tr>
    `).join('') : '<tr><td colspan="3" class="empty-cell">No file type data</td></tr>';
  }

  async function loadMounts() {
    const body = $('#storage-mounts-body');
    if (!body) return;
    try {
      const j = await api('/api/storage/mounts');
      const rows = j.mounts || [];
      body.innerHTML = rows.length ? rows.map(r => `
        <tr>
          <td><strong>${esc(r.label)}</strong><div class="network-mini">${esc(r.path)}</div></td>
          <td class="text-right mono">${r.used_pct}%</td>
          <td class="text-right mono">${fmtBytes(r.free)}</td>
        </tr>
      `).join('') : '<tr><td colspan="3" class="empty-cell">No active storage mounts found</td></tr>';
    } catch (e) {
      body.innerHTML = `<tr><td colspan="3" class="empty-cell">${esc(e.message)}</td></tr>`;
    }
  }

  async function loadAnalysis(force = true) {
    if (!force && $('#analysis-files')?.dataset.loaded) return;
    const limit = Number($('#analysis-limit')?.value || 25);
    const grid = $('#analysis-grid');
    if (grid) grid.hidden = false;

    ['#analysis-folders', '#analysis-files', '#analysis-old', '#analysis-dupes'].forEach(s => {
      const el = $(s);
      if (el) el.innerHTML = '<tr><td colspan="3" class="empty-cell">Analyzing…</td></tr>';
    });

    const sumEl = $('#analysis-summary');
    if (sumEl) sumEl.textContent = 'Analyzing selected root…';

    const j = await api(`/api/storage/analysis?mode=quick&path=${encodeURIComponent(st.path)}&limit=${limit}`);
    const sm = j.summary || {};
    const filesEl = $('#analysis-files');
    if (filesEl) filesEl.dataset.loaded = '1';

    if (sumEl) {
      sumEl.innerHTML = `<strong>${j.scanned || 0}</strong> scanned · <strong>${sm.files || 0}</strong> files · <strong>${sm.folders || 0}</strong> folders · <strong>${fmtBytes(sm.total_file_size || 0)}</strong> files · <strong>${sm.old_large || 0}</strong> old large · <strong>${sm.duplicates || 0}</strong> duplicate candidates`;
    }

    const foldersEl = $('#analysis-folders');
    if (foldersEl) {
      foldersEl.innerHTML = (j.folders || []).map(r => `<tr><td title="${esc(r.path)}">${esc(short(r.path))}</td><td class="text-right mono">${fmtBytes(r.size)}</td></tr>`).join('') || '<tr><td colspan="2" class="empty-cell">No folders</td></tr>';
    }
    if (filesEl) {
      filesEl.innerHTML = (j.files || []).map(r => `<tr><td title="${esc(r.path)}">${esc(short(r.path))}</td><td class="text-right mono">${fmtBytes(r.size)}</td></tr>`).join('') || '<tr><td colspan="2" class="empty-cell">No files</td></tr>';
    }
    const oldEl = $('#analysis-old');
    if (oldEl) {
      oldEl.innerHTML = (j.old_files || []).map(r => `<tr><td title="${esc(r.path)}">${esc(short(r.path))}</td><td>${r.age_days}d</td><td class="text-right mono">${fmtBytes(r.size)}</td></tr>`).join('') || '<tr><td colspan="3" class="empty-cell">No old large files</td></tr>';
    }
    const dupesEl = $('#analysis-dupes');
    if (dupesEl) {
      dupesEl.innerHTML = (j.duplicates || []).map(r => `<tr><td title="${esc((r.paths || []).join('\n'))}">${esc(short(r.name))}</td><td>${r.count}</td><td class="text-right mono">${fmtBytes(r.size)}</td></tr>`).join('') || '<tr><td colspan="3" class="empty-cell">No duplicate candidates</td></tr>';
    }
  }

  async function loadOps() {
    const j = await api('/api/storage/ops');
    const body = $('#storage-ops-body');
    if (!body) return;
    body.innerHTML = (j.rows || []).map(r => `
      <tr>
        <td class="mono">${esc(r.time)}</td>
        <td>${esc(r.action)}</td>
        <td title="${esc(r.path)}">${esc(short(r.path))}</td>
        <td>${r.ok ? '✅' : '❌'} ${esc(r.error || 'ok')}</td>
      </tr>
    `).join('') || '<tr><td colspan="4" class="empty-cell">No operations recorded yet</td></tr>';
  }

  async function loadTrash() {
    const j = await api('/api/storage/trash');
    const body = $('#storage-trash-body');
    if (!body) return;
    body.innerHTML = (j.rows || []).map(r => `
      <tr>
        <td title="${esc(r.path)}">${esc(short(r.name))}</td>
        <td>${esc(r.type)}</td>
        <td class="text-right mono">${fmtBytes(r.size)}</td>
        <td><button type="button" class="btn secondary compact" data-restore="${esc(r.path)}">Restore</button></td>
      </tr>
    `).join('') || '<tr><td colspan="4" class="empty-cell">Trash is empty</td></tr>';
  }

  async function getActionPassword() {
    if (!st.actionPwRequired) return '';
    if (st.actionPw) return st.actionPw;
    const pw = await window.SystorDialog.prompt(
      st.hasActionPw ? 'Storage action password required:' : 'Please enter storage action password:',
      '',
      { isPassword: true, title: 'Password Authentication' }
    );
    if (!pw) throw new Error('Action password required');
    st.actionPw = pw;
    return pw;
  }

  async function restoreTrash(path) {
    let pw = '';
    try {
      pw = await getActionPassword();
    } catch (e) {
      await window.SystorDialog.alert(e.message, 'Authorization Required');
      return;
    }
    const destination = await window.SystorDialog.prompt('Restore to folder path:', st.path, { title: 'Restore Location' });
    if (!destination) return;

    try {
      const j = await api('/api/storage/restore', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ trash_path: path, destination, action_password: pw })
      });
      await window.SystorDialog.alert(j.message || 'Restored', 'Restore Complete');
      await loadTrash();
      await loadBrowse();
    } catch (e) {
      await window.SystorDialog.alert(e.message, 'Restore Failed');
    }
  }

  function hideContext() {
    const m = $('#storage-context-menu');
    if (m) m.hidden = true;
  }

  function showContext(e, row) {
    const m = $('#storage-context-menu');
    if (!m) return;
    st.contextPath = row.dataset.rowPath || '';
    $$('.storage-row-check').forEach(x => { x.checked = false; });
    const cb = row.querySelector('.storage-row-check');
    if (cb) cb.checked = true;
    m.hidden = false;
    m.style.left = Math.min(e.clientX, window.innerWidth - 150) + 'px';
    m.style.top = Math.min(e.clientY, window.innerHeight - 190) + 'px';
  }

  async function openPath(p, type) {
    if (type === 'dir') {
      st.path = p;
      await loadBrowse();
    }
  }

  async function downloadSelected() {
    const items = selected();
    if (!items.length) {
      await window.SystorDialog.alert('Select one file or folder first.', 'Download');
      return;
    }
    try {
      await getActionPassword();
    } catch (e) {
      await window.SystorDialog.alert(e.message, 'Authorization Required');
      return;
    }
    window.location.href = '/api/storage/download?path=' + encodeURIComponent(items[0]);
  }

  async function previewSelected(path) {
    const box = $('#storage-preview');
    if (!box) return;
    try {
      const j = await api('/api/storage/preview?path=' + encodeURIComponent(path));
      box.hidden = false;
      if (j.kind === 'text') {
        box.innerHTML = `<div><strong>${esc(j.name)}</strong> · ${fmtBytes(j.size)}</div><pre>${esc(j.text)}</pre>`;
      } else if (j.kind === 'image') {
        box.innerHTML = `<div><strong>${esc(j.name)}</strong> · ${fmtBytes(j.size)}</div><img src="${esc(j.download_url)}" alt="File preview" width="600" height="340" loading="lazy" style="max-width:100%;height:auto;object-fit:contain;">`;
      } else {
        box.innerHTML = `<div><strong>${esc(j.name)}</strong> · ${fmtBytes(j.size)}</div><p>${esc(j.message || 'No preview available')}</p>`;
      }
    } catch (e) {
      box.hidden = false;
      box.innerHTML = `<p class="empty-cell">Preview: ${esc(e.message)}</p>`;
    }
  }

  async function startDeepScan() {
    const limit = Number($('#storage-max-scan')?.value || 20000);
    const sumEl = $('#analysis-summary');
    if (sumEl) sumEl.textContent = 'Deep scan running in background…';
    let pw = '';
    try {
      pw = await getActionPassword();
    } catch (e) {
      await window.SystorDialog.alert(e.message, 'Authorization Required');
      return;
    }

    await api('/api/storage/deep-scan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: st.path, limit, action_password: pw })
    });

    const poll = setInterval(async () => {
      try {
        const j = await api('/api/storage/deep-scan');
        const s = j.state || {};
        if (s.running) {
          if (sumEl) {
            sumEl.textContent = `Deep scan running · ${s.scanned || 0} scanned · ${s.files_found || 0} files · ${Math.round((Date.now() / 1000) - (s.started || 0))}s · ${short(s.current || '')}`;
          }
        } else {
          clearInterval(poll);
          if (s.error) {
            if (sumEl) sumEl.textContent = 'Deep scan error: ' + s.error;
          } else {
            const sm = s.result?.summary || {};
            if (sumEl) {
              sumEl.innerHTML = `Deep scan done · <strong>${s.result?.scanned || 0}</strong> scanned · <strong>${sm.files || 0}</strong> files · <strong>${sm.folders || 0}</strong> folders · <strong>${fmtBytes(sm.total_file_size || 0)}</strong>`;
            }
          }
        }
      } catch (e) {
        clearInterval(poll);
        if (sumEl) sumEl.textContent = 'Deep scan status error: ' + e.message;
      }
    }, 1500);
  }

  async function checksumDupes() {
    const limit = Number($('#storage-max-scan')?.value || 3000);
    const sumEl = $('#analysis-summary');
    if (sumEl) sumEl.textContent = 'Checksum duplicate scan running…';
    let pw = '';
    try {
      pw = await getActionPassword();
    } catch (e) {
      await window.SystorDialog.alert(e.message, 'Authorization Required');
      return;
    }

    const j = await api(`/api/storage/checksum-duplicates?path=${encodeURIComponent(st.path)}&limit=${limit}`, {
      headers: pw ? { 'X-Storage-Password': pw } : {}
    });

    const grid = $('#analysis-grid');
    if (grid) grid.hidden = false;
    const dupesEl = $('#analysis-dupes');
    if (dupesEl) {
      dupesEl.innerHTML = (j.groups || []).map(g => `
        <tr>
          <td title="${esc((g.paths || []).join('\n'))}">${esc(short(g.sha256))}</td>
          <td>${g.count}</td>
          <td class="text-right mono">${fmtBytes(g.size)}</td>
        </tr>
      `).join('') || '<tr><td colspan="3" class="empty-cell">No checksum-confirmed duplicates</td></tr>';
    }

    if (sumEl) {
      sumEl.innerHTML = `Checksum scan · <strong>${j.scanned}</strong> files · <strong>${(j.groups || []).length}</strong> confirmed duplicate groups`;
    }
  }

  async function uploadLargeFile(f, pw) {
    const chunk = 8 * 1024 * 1024;
    const id = Date.now() + '-' + Math.random().toString(36).slice(2);
    let offset = 0;
    while (offset < f.size) {
      const blob = f.slice(offset, offset + chunk);
      const url = `/api/storage/upload-chunk?path=${encodeURIComponent(st.path)}&filename=${encodeURIComponent(f.name)}&upload_id=${encodeURIComponent(id)}&offset=${offset}&total=${f.size}`;
      const r = await fetch(url, {
        method: 'POST',
        headers: pw ? { 'X-Storage-Password': pw } : {},
        body: blob
      });
      const j = await r.json().catch(() => ({ ok: false, error: 'bad json' }));
      if (!r.ok || j.ok === false) throw new Error(j.error || ('HTTP ' + r.status));
      offset = j.written || offset + blob.size;
      const infoEl = $('#storage-page-info');
      if (infoEl) {
        infoEl.textContent = `Uploading ${f.name} · ${Math.round(offset / f.size * 100)}%`;
      }
    }
  }

  async function uploadFiles(files) {
    if (!st.canWrite) {
      await window.SystorDialog.alert('Upload is only allowed inside managed roots like /mnt/tb', 'Read-Only Notice');
      return;
    }
    if (!files || !files.length) return;
    let pw = '';
    try {
      pw = await getActionPassword();
    } catch (e) {
      await window.SystorDialog.alert(e.message, 'Authorization Required');
      return;
    }

    const small = [...files].filter(f => f.size <= 64 * 1024 * 1024);
    const large = [...files].filter(f => f.size > 64 * 1024 * 1024);

    if (small.length) {
      const fd = new FormData();
      small.forEach(f => fd.append('files', f));
      const r = await fetch('/api/storage/upload?path=' + encodeURIComponent(st.path), {
        method: 'POST',
        headers: pw ? { 'X-Storage-Password': pw } : {},
        body: fd
      });
      const j = await r.json().catch(() => ({ ok: false, error: 'bad json' }));
      if (!r.ok || j.ok === false) {
        await window.SystorDialog.alert(j.error || ('HTTP ' + r.status), 'Upload Failed');
        return;
      }
    }

    try {
      for (const f of large) {
        await uploadLargeFile(f, pw);
      }
    } catch (e) {
      await window.SystorDialog.alert('Upload error: ' + e.message, 'Upload Failed');
      return;
    }

    await loadBrowse();
    await loadOps();
    await window.SystorDialog.alert(`Uploaded ${files.length} file(s) successfully.`, 'Upload Complete');
  }

  async function doAction(action) {
    if (!st.canWrite) {
      await window.SystorDialog.alert('File actions are disabled on public/tunnel access. Use LAN/Tailscale/local.', 'Read-Only Mode');
      return;
    }
    const items = selected();
    let pw = '';
    try {
      pw = await getActionPassword();
    } catch (e) {
      await window.SystorDialog.alert(e.message, 'Authorization Required');
      return;
    }

    let body = { action, path: st.path, items, action_password: pw };

    if (action === 'mkdir') {
      const name = await window.SystorDialog.prompt('Enter new folder name:', '', { title: 'New Folder' });
      if (!name) return;
      body.name = name;
    } else if (action === 'rename') {
      if (items.length !== 1) {
        await window.SystorDialog.alert('Please select exactly one item to rename.', 'Rename');
        return;
      }
      const name = await window.SystorDialog.prompt('Enter new name:', '', { title: 'Rename Item' });
      if (!name) return;
      body.name = name;
    } else if (action === 'copy' || action === 'move') {
      if (!items.length) {
        await window.SystorDialog.alert('Please select item(s) first.', action.toUpperCase());
        return;
      }
      const dst = await window.SystorDialog.prompt(`Destination folder path for ${action}:`, st.path, { title: `${action.toUpperCase()} Destination` });
      if (!dst) return;
      body.destination = dst;
    } else if (action === 'delete') {
      if (!items.length) {
        await window.SystorDialog.alert('Please select item(s) first.', 'Trash');
        return;
      }
      const confirmed = await window.SystorDialog.confirm(`Move ${items.length} item(s) to trash?`, 'Confirm Trash');
      if (!confirmed) return;
    }

    try {
      const j = await api('/api/storage/action', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      });
      await loadBrowse();
      await loadOps();
      await window.SystorDialog.alert(j.message || 'Action completed', 'Storage Action');
    } catch (e) {
      await window.SystorDialog.alert(e.message, 'Action Failed');
    }
  }

  // Bind click handlers
  document.addEventListener('click', async e => {
    const restore = e.target.closest('[data-restore]');
    if (restore) {
      await restoreTrash(restore.dataset.restore);
      return;
    }
    const crumb = e.target.closest('[data-crumb]');
    if (crumb) {
      st.path = crumb.dataset.crumb;
      loadBrowse();
      return;
    }
    const cm = e.target.closest('[data-context-action]');
    if (cm) {
      hideContext();
      const a = cm.dataset.contextAction;
      if (a === 'open') {
        const row = document.querySelector(`[data-row-path="${CSS.escape(st.contextPath)}"]`);
        openPath(st.contextPath, row?.dataset.rowType || '');
      } else if (a === 'download') {
        downloadSelected();
      } else if (a === 'preview') {
        previewSelected(st.contextPath);
      } else {
        doAction(a);
      }
      return;
    }
    if (!e.target.closest('#storage-context-menu')) {
      hideContext();
    }
    const jump = e.target.closest('[data-jump]');
    if (jump) {
      const v = jump.dataset.jump;
      if (v === 'up') {
        $('#storage-up')?.click();
      } else {
        st.path = v;
        loadBrowse();
      }
      return;
    }
    const rb = e.target.closest('[data-root]');
    if (rb) {
      st.path = rb.dataset.root;
      loadBrowse();
      return;
    }
    const tab = e.target.closest('.storage-tab');
    if (tab) setTab(tab.dataset.tab);
    const open = e.target.closest('[data-open]');
    if (open) {
      openPath(open.dataset.open, open.dataset.type);
    }
    const act = e.target.closest('[data-action]');
    if (act) {
      doAction(act.dataset.action);
    }
  });

  $('#storage-refresh')?.addEventListener('click', () => loadBrowse());
  $('#smart-refresh-btn')?.addEventListener('click', () => loadSmartHealth(true));
  $('#storage-go')?.addEventListener('click', () => {
    st.path = $('#storage-path').value.trim();
    loadBrowse();
  });
  $('#storage-path')?.addEventListener('keydown', e => {
    if (e.key === 'Enter') $('#storage-go')?.click();
  });
  $('#storage-up')?.addEventListener('click', () => {
    const p = st.path.replace(/\/+$/, '');
    st.path = p === '/' ? '/' : (p.split('/').slice(0, -1).join('/') || '/');
    loadBrowse();
  });

  $('#analysis-run')?.addEventListener('click', () => loadAnalysis(true));
  $('#analysis-deep')?.addEventListener('click', startDeepScan);
  $('#analysis-checksum')?.addEventListener('click', checksumDupes);
  $('#ops-refresh')?.addEventListener('click', loadOps);
  $('#trash-refresh')?.addEventListener('click', loadTrash);
  $('#ops-export')?.addEventListener('click', () => {
    window.location.href = '/api/storage/ops/export';
  });

  $('#ops-clear')?.addEventListener('click', async () => {
    const confirmed = await window.SystorDialog.confirm('Clear Storage operation log history?', 'Confirm Clear');
    if (!confirmed) return;
    let pw = '';
    try {
      pw = await getActionPassword();
    } catch (e) {
      await window.SystorDialog.alert(e.message, 'Authorization Required');
      return;
    }
    try {
      await api('/api/storage/ops/clear', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action_password: pw })
      });
      await loadOps();
    } catch (e) {
      await window.SystorDialog.alert(e.message, 'Clear Failed');
    }
  });

  $('#storage-check-all')?.addEventListener('change', e => {
    $$('.storage-row-check').forEach(x => { x.checked = e.target.checked; });
  });

  $('#storage-search')?.addEventListener('input', e => {
    st.filter = e.target.value;
    st.page = 1;
    renderFiles();
  });
  $('#storage-page-size')?.addEventListener('change', () => {
    st.page = 1;
    renderFiles();
  });
  $('#storage-prev-page')?.addEventListener('click', () => {
    st.page--;
    renderFiles();
  });
  $('#storage-next-page')?.addEventListener('click', () => {
    st.page++;
    renderFiles();
  });

  $('#storage-upload-btn')?.addEventListener('click', () => {
    $('#storage-upload-input')?.click();
  });
  $('#storage-upload-input')?.addEventListener('change', e => {
    uploadFiles(e.target.files);
  });
  $('#storage-download-btn')?.addEventListener('click', downloadSelected);

  $('#storage-no-right-click')?.addEventListener('change', e => {
    document.body.classList.toggle('storage-no-context', e.target.checked);
  });

  $('#storage-save-settings')?.addEventListener('click', async () => {
    if (!st.canWrite) {
      await window.SystorDialog.alert('Settings save is local/LAN only.', 'Read-Only Mode');
      return;
    }
    const body = {
      allowed_roots: $('#storage-allowed-roots').value.split('\n').map(x => x.trim()).filter(Boolean),
      public_readonly: $('#storage-public-readonly').checked,
      trash_enabled: $('#storage-trash-enabled').checked,
      no_right_click: $('#storage-no-right-click').checked,
      action_password_required: $('#storage-action-password-required').checked,
      action_password: $('#storage-action-password').value,
      max_scan_files: Number($('#storage-max-scan').value || 20000)
    };
    try {
      const j = await api('/api/storage/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      });
      $('#storage-save-result').textContent = j.message || 'saved';
      $('#storage-action-password').value = '';
      st.actionPw = '';
      await loadBrowse();
    } catch (e) {
      $('#storage-save-result').textContent = 'error: ' + e.message;
    }
  });

  document.addEventListener('contextmenu', e => {
    const row = e.target.closest('tr[data-row-path]');
    if (row) {
      e.preventDefault();
      showContext(e, row);
      return;
    }
    if (document.body.classList.contains('storage-no-context') && e.target.closest('.storage-page')) {
      e.preventDefault();
    }
  });

  loadBrowse().catch(e => {
    const meta = $('#storage-meta');
    if (meta) meta.textContent = 'error: ' + e.message;
  });
})();
