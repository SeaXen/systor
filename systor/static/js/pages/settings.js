/**
 * Systor Settings Page Controller
 */
(function() {
  'use strict';

  function getSettingsData() {
    const el = document.getElementById('settings-defaults-data');
    if (!el) return { defaults: {}, saved_at: '' };
    try {
      const data = JSON.parse(el.textContent);
      return {
        defaults: data.defaults || {},
        saved_at: data.saved_at || ''
      };
    } catch (e) {
      return { defaults: {}, saved_at: '' };
    }
  }

  const { defaults: SETTINGS_DEFAULTS, saved_at: INITIAL_SAVED_AT } = getSettingsData();
  let LAST_SAVED_AT = INITIAL_SAVED_AT;
  let pendingImportFile = null;

  function showFlash(type, msg, ms = 7000) {
    const el = document.getElementById('flash');
    if (!el) return;
    el.className = 'flash ' + type;
    el.textContent = msg;
    if (ms) setTimeout(() => { el.textContent = ''; el.className = ''; }, ms);
  }

  function esc(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
  }

  function fmtUptime(sec) {
    if (sec === null || sec === undefined) return '—';
    sec = Number(sec) || 0;
    const d = Math.floor(sec / 86400), h = Math.floor((sec % 86400) / 3600), m = Math.floor((sec % 3600) / 60);
    if (d) return `${d}d ${h}h`;
    if (h) return `${h}h ${m}m`;
    return `${m}m`;
  }

  function setSettingsTab(name) {
    document.querySelectorAll('[data-settings-tab]').forEach(btn => btn.classList.toggle('active', btn.dataset.settingsTab === name));
    document.querySelectorAll('[data-settings-panel]').forEach(panel => panel.classList.toggle('active', panel.dataset.settingsPanel === name));
    try { localStorage.setItem('systor.settings.tab', name); } catch (e) {}
  }

  function initSettingsTabs() {
    const fallback = 'general';
    let wanted = fallback;
    try { wanted = localStorage.getItem('systor.settings.tab') || fallback; } catch (e) {}
    if (wanted === 'alerts') wanted = 'general';
    if (!document.querySelector(`[data-settings-panel="${wanted}"]`)) wanted = fallback;
    setSettingsTab(wanted);
    document.querySelectorAll('[data-settings-tab]').forEach(btn => btn.addEventListener('click', () => setSettingsTab(btn.dataset.settingsTab)));
  }

  function formToJSON(form, scopeEl = null) {
    if (!form) return {};
    const fd = new FormData(form);
    const data = Object.fromEntries(fd.entries());
    const boolKeys = ['telegram_enabled', 'discord_enabled', 'network_hide_virtual_default', 'speed_auto_enabled', 'speed_notify_enabled', 'auth_enabled', 'auth_clear_password', 'public_dashboard', 'public_alerts'];
    boolKeys.forEach(k => {
      if (!scopeEl || scopeEl.querySelector(`[name="${k}"]`)) data[k] = fd.has(k) ? 'true' : 'false';
    });
    ['cpu_load_1m', 'cpu_temp_c', 'mem_free_mb', 'swap_used_mb', 'disk_used_pct'].forEach(k => {
      const name = `threshold_${k}_enabled`;
      if (!scopeEl || scopeEl.querySelector(`[name="${name}"]`)) data[name] = fd.has(name) ? 'true' : 'false';
    });
    if (!scopeEl) return data;
    const filtered = {};
    scopeEl.querySelectorAll('[name]').forEach(el => {
      filtered[el.name] = Object.prototype.hasOwnProperty.call(data, el.name) ? data[el.name] : (el.type === 'checkbox' ? 'false' : '');
    });
    return filtered;
  }

  const INITIAL_FORM_DATA = {};

  function activePanel() {
    return document.querySelector('[data-settings-panel].active');
  }

  function captureInitialState() {
    Object.assign(INITIAL_FORM_DATA, formToJSON(document.getElementById('settings-form')));
  }

  function setFieldValue(el, value) {
    if (el.type === 'checkbox') el.checked = String(value) === 'true';
    else el.value = value ?? '';
  }

  function updateDirtyState() {
    const form = document.getElementById('settings-form');
    if (!form) return;
    const now = formToJSON(form);
    let dirty = 0;
    form.querySelectorAll('[name]').forEach(el => {
      const changed = String(now[el.name] ?? '') !== String(INITIAL_FORM_DATA[el.name] ?? '');
      el.classList.toggle('is-dirty', changed);
      if (changed) dirty += 1;
    });
    const chip = document.getElementById('dirty-summary');
    if (!chip) return;
    chip.textContent = dirty ? `${dirty} changed` : 'clean';
    chip.className = 'status-chip settings-meta-chip ' + (dirty ? 'warn' : 'ok');
  }

  function relativeTimeFromIso(iso) {
    if (!iso) return 'saved —';
    const t = new Date(iso);
    if (Number.isNaN(t.getTime())) return `saved ${iso}`;
    const diffSec = Math.max(0, Math.round((Date.now() - t.getTime()) / 1000));
    if (diffSec < 15) return 'saved just now';
    if (diffSec < 60) return `saved ${diffSec}s ago`;
    const diffMin = Math.floor(diffSec / 60);
    if (diffMin < 60) return `saved ${diffMin}m ago`;
    const diffHr = Math.floor(diffMin / 60);
    if (diffHr < 24) return `saved ${diffHr}h ago`;
    const diffDay = Math.floor(diffHr / 24);
    return `saved ${diffDay}d ago`;
  }

  function updateLastSavedChip(value = LAST_SAVED_AT) {
    LAST_SAVED_AT = value || '';
    const chip = document.getElementById('last-saved-summary');
    if (!chip) return;
    chip.textContent = relativeTimeFromIso(LAST_SAVED_AT);
    chip.title = LAST_SAVED_AT || '';
  }

  function tabDefaultValues(name) {
    const d = SETTINGS_DEFAULTS || {};
    if (name === 'general') return {
      threshold_cpu_load_1m_enabled: String((((d.thresholds||{}).cpu_load_1m||{}).enabled ?? true)),
      threshold_cpu_load_1m_value: String((((d.thresholds||{}).cpu_load_1m||{}).value ?? 4.0)),
      threshold_cpu_load_1m_duration: String((((d.thresholds||{}).cpu_load_1m||{}).duration_min ?? 2)),
      threshold_cpu_temp_c_enabled: String((((d.thresholds||{}).cpu_temp_c||{}).enabled ?? true)),
      threshold_cpu_temp_c_value: String((((d.thresholds||{}).cpu_temp_c||{}).value ?? 85.0)),
      threshold_cpu_temp_c_duration: String((((d.thresholds||{}).cpu_temp_c||{}).duration_min ?? 2)),
      threshold_mem_free_mb_enabled: String((((d.thresholds||{}).mem_free_mb||{}).enabled ?? true)),
      threshold_mem_free_mb_value: String((((d.thresholds||{}).mem_free_mb||{}).value ?? 500)),
      threshold_mem_free_mb_duration: String((((d.thresholds||{}).mem_free_mb||{}).duration_min ?? 2)),
      threshold_swap_used_mb_enabled: String((((d.thresholds||{}).swap_used_mb||{}).enabled ?? true)),
      threshold_swap_used_mb_value: String((((d.thresholds||{}).swap_used_mb||{}).value ?? 1024)),
      threshold_swap_used_mb_duration: String((((d.thresholds||{}).swap_used_mb||{}).duration_min ?? 2)),
      threshold_disk_used_pct_enabled: String((((d.thresholds||{}).disk_used_pct||{}).enabled ?? true)),
      threshold_disk_used_pct_value: String((((d.thresholds||{}).disk_used_pct||{}).value ?? 90)),
      threshold_disk_used_pct_duration: String((((d.thresholds||{}).disk_used_pct||{}).duration_min ?? 2)),
      cooldown_sec: String((d.thresholds||{}).cooldown_sec ?? 0),
      dashboard_default_hours: String((d.dashboard||{}).default_hours ?? 6),
      dashboard_refresh_sec: String((d.dashboard||{}).refresh_sec ?? 5),
      dashboard_chart_refresh_sec: String((d.dashboard||{}).chart_refresh_sec ?? 5),
      poll_interval_sec: String((d.collector||{}).poll_interval_sec ?? 5),
      apps_default_scope: String((d.apps||{}).default_scope ?? 'all'),
      apps_default_sort: String((d.apps||{}).default_sort ?? 'cpu'),
      apps_auto_refresh_sec: String((d.apps||{}).auto_refresh_sec ?? 15),
      apps_default_limit: String((d.apps||{}).default_limit ?? 10),
      network_default_hours: String((d.network||{}).default_hours ?? 1),
      network_default_granularity: String((d.network||{}).default_granularity ?? 'day'),
      network_auto_refresh_sec: String((d.network||{}).auto_refresh_sec ?? 5),
      network_default_bar_days: String((d.network||{}).default_bar_days ?? 90),
      network_default_table_days: String((d.network||{}).default_table_days ?? 90),
      network_default_iface: String((d.network||{}).default_iface ?? 'all'),
      network_hide_virtual_default: String((d.network||{}).hide_virtual_default ?? true),
      speed_page_refresh_sec: String((d.speed||{}).page_refresh_sec ?? 0),
      speed_default_provider: String((d.speed||{}).default_provider ?? 'ookla'),
      speed_auto_enabled: String((d.speed||{}).auto_enabled ?? false),
      speed_auto_provider: String((d.speed||{}).auto_provider ?? 'ookla'),
      speed_auto_interval_min: String((d.speed||{}).auto_interval_min ?? 60),
      speed_notify_enabled: String((d.speed||{}).notify_enabled ?? false),
      speed_min_download_mbps: String((d.speed||{}).min_download_mbps ?? 0),
      speed_min_upload_mbps: String((d.speed||{}).min_upload_mbps ?? 0),
      speed_librespeed_server_id: String((d.speed||{}).librespeed_server_id ?? ''),
      speed_ookla_server_id: String((d.speed||{}).ookla_server_id ?? ''),
      speed_local_librespeed_port: String((d.speed||{}).local_librespeed_port ?? 8077),
      speed_history_page_size: String((d.speed||{}).history_page_size ?? 25),
      speed_iperf_port: String((d.speed||{}).iperf_port ?? 5201),
    };
    if (name === 'notifications') return {
      telegram_enabled: String((d.telegram||{}).enabled ?? false),
      telegram_bot_token: '',
      telegram_chat_id: String((d.telegram||{}).chat_id ?? ''),
      discord_enabled: String((d.discord||{}).enabled ?? false),
      discord_webhook_url: '',
    };
    if (name === 'auth') return {
      auth_enabled: String((d.auth||{}).enabled ?? false),
      auth_mode: String((d.auth||{}).mode ?? 'admin_only'),
      auth_username: String((d.auth||{}).username ?? 'admin'),
      auth_password: '',
      auth_password_confirm: '',
      auth_clear_password: 'false',
      auth_idle_timeout_min: String((d.auth||{}).idle_timeout_min ?? 0),
      auth_max_fails: String((d.auth||{}).max_fails ?? 5),
      auth_cooldown_sec: String((d.auth||{}).cooldown_sec ?? 60),
    };
    if (name === 'access') return {
      public_dashboard: String((d.public||{}).dashboard ?? true),
      public_alerts: String((d.public||{}).alerts ?? false),
      web_host: String((d.web||{}).host ?? '0.0.0.0'),
      web_port: String((d.web||{}).port ?? 6677),
    };
    return {};
  }

  function restoreActiveTabDefaults(tabName = null) {
    const panel = tabName ? document.querySelector(`[data-settings-panel="${tabName}"]`) : activePanel();
    if (!panel) return;
    if (tabName) setSettingsTab(tabName);
    const effectiveTab = panel.dataset.settingsPanel;
    const vals = tabDefaultValues(effectiveTab);
    Object.entries(vals).forEach(([name, val]) => {
      const el = panel.querySelector(`[name="${name}"]`);
      if (el) setFieldValue(el, val);
    });
    syncPresetSliders();
    updateAuthSummaryChip();
    updateDirtyState();
    setSaveState('unsaved', 'warn');
    showFlash('ok', 'Built-in defaults loaded for active tab');
  }

  function openImportModal(file) {
    pendingImportFile = file;
    document.getElementById('import-modal-file').textContent = file ? file.name : 'No file selected';
    document.getElementById('import-modal-backdrop')?.classList.add('open');
  }

  function closeImportModal() {
    pendingImportFile = null;
    document.getElementById('import-modal-backdrop')?.classList.remove('open');
  }

  function resetActiveTab() {
    const panel = activePanel();
    if (!panel) return;
    panel.querySelectorAll('[name]').forEach(el => setFieldValue(el, INITIAL_FORM_DATA[el.name]));
    syncPresetSliders();
    updateAuthSummaryChip();
    updateDirtyState();
    setSaveState('ready');
    showFlash('ok', 'Active tab reset to saved values');
  }

  function setSaveState(text, cls = '') {
    const el = document.getElementById('save-state');
    if (!el) return;
    el.textContent = text;
    el.className = 'status-chip ' + cls;
  }

  function explainNotifyError(err) {
    const s = String(err || 'failed');
    if (s.includes('404') && s.includes('Not Found')) return s + ' · likely wrong Telegram bot token';
    if (s.includes('403') && s.includes('1010')) return s + ' · blocked by Cloudflare/WAF';
    return s;
  }

  async function fetchJSON(url, opts) {
    const r = await fetch(url, opts || { cache: 'no-store' });
    const j = await r.json();
    if (!r.ok) throw new Error(j.error || j.message || ('HTTP ' + r.status));
    return j;
  }

  function buildTestPayload(channel) {
    const data = formToJSON(document.getElementById('settings-form'));
    if (channel === 'telegram') {
      return {
        telegram_enabled: data.telegram_enabled,
        telegram_bot_token: data.telegram_bot_token || '',
        telegram_chat_id: data.telegram_chat_id || '',
        message: 'auto-generated by /api/test-telegram if omitted'
      };
    }
    return {
      discord_enabled: data.discord_enabled,
      discord_webhook_url: data.discord_webhook_url || '',
      message: 'auto-generated by /api/test-discord if omitted'
    };
  }

  async function copyPayload(channel) {
    const payload = JSON.stringify(buildTestPayload(channel), null, 2);
    await navigator.clipboard.writeText(payload);
    showFlash('ok', channel + ' payload copied');
  }

  async function saveSettings(thenApply) {
    setSaveState('saving…');
    const data = formToJSON(document.getElementById('settings-form'));
    const j = await fetchJSON('/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data)
    });
    if (thenApply) {
      const j2 = await fetchJSON('/api/apply', { method: 'POST' });
      showFlash('ok', (j.message || 'Saved.') + ' · ' + (j2.message || 'Applied.'));
    } else {
      showFlash('ok', j.message || 'Saved.');
    }
    Object.assign(INITIAL_FORM_DATA, formToJSON(document.getElementById('settings-form')));
    setSaveState('saved', 'ok');
    updateDirtyState();
    updateLastSavedChip(j.saved_at);
    await Promise.all([refreshStatus(), loadRuntime()]);
    updateAuthSummaryChip();
  }

  async function saveActiveTab(thenApply = false) {
    const panel = activePanel();
    if (!panel) return;
    setSaveState('saving…');
    const data = formToJSON(document.getElementById('settings-form'), panel);
    const j = await fetchJSON('/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data)
    });
    if (thenApply) {
      const j2 = await fetchJSON('/api/apply', { method: 'POST' });
      showFlash('ok', (j.message || 'Saved tab.') + ' · ' + (j2.message || 'Applied.'));
    } else {
      showFlash('ok', j.message || 'Saved active tab.');
    }
    Object.assign(INITIAL_FORM_DATA, formToJSON(document.getElementById('settings-form')));
    setSaveState('saved', 'ok');
    updateDirtyState();
    updateLastSavedChip(j.saved_at);
    await Promise.all([refreshStatus(), loadRuntime()]);
    updateAuthSummaryChip();
  }

  async function exportSettings() {
    window.location.href = '/api/settings/export';
    showFlash('ok', 'Export started');
  }

  function chooseImportFile() {
    document.getElementById('import-settings-file')?.click();
  }

  async function importSettingsFile(file) {
    if (!file) return;
    const fd = new FormData();
    fd.append('file', file);
    setSaveState('importing…');
    const r = await fetch('/api/settings/import', { method: 'POST', body: fd });
    const j = await r.json();
    if (!r.ok || !j.ok) throw new Error(j.message || j.error || ('HTTP ' + r.status));
    updateLastSavedChip(j.saved_at);
    showFlash('ok', j.message || 'Settings imported. Reloading…', 9000);
    setTimeout(() => location.reload(), 800);
  }

  async function testChannel(channel) {
    const data = formToJSON(document.getElementById('settings-form'));
    const resultEl = document.getElementById(channel === 'telegram' ? 'test-tg-result' : 'test-dc-result');
    const chip = document.getElementById(channel === 'telegram' ? 'tg-last-status' : 'dc-last-status');
    if (resultEl) resultEl.textContent = 'testing…';
    if (chip) {
      chip.textContent = 'testing…';
      chip.className = 'status-chip';
    }
    try {
      const j = await fetchJSON('/api/test-' + channel, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
      });
      if (j.ok) {
        if (resultEl) {
          resultEl.textContent = '✓ sent';
          resultEl.style.color = 'var(--green)';
        }
        if (chip) {
          chip.textContent = 'sent';
          chip.className = 'status-chip ok';
        }
        showFlash('ok', channel + ' test sent');
      } else {
        const msg = explainNotifyError(j.error);
        if (resultEl) {
          resultEl.textContent = '✗ ' + msg;
          resultEl.style.color = 'var(--red)';
        }
        if (chip) {
          chip.textContent = 'failed';
          chip.className = 'status-chip err';
        }
        showFlash('err', channel + ' failed: ' + msg);
      }
    } catch (e) {
      const msg = explainNotifyError(e.message);
      if (resultEl) {
        resultEl.textContent = '✗ ' + msg;
        resultEl.style.color = 'var(--red)';
      }
      if (chip) {
        chip.textContent = 'failed';
        chip.className = 'status-chip err';
      }
      showFlash('err', channel + ' failed: ' + msg);
    }
    await refreshStatus();
  }

  async function saveThenTest(channel) {
    try {
      await saveSettings(true);
      await testChannel(channel);
    } catch (e) {
      showFlash('err', e.message || 'save failed');
    }
  }

  function updateAuthSummaryChip() {
    const chip = document.getElementById('auth-summary');
    if (!chip) return;
    const enabled = !!document.querySelector('[name="auth_enabled"]')?.checked;
    chip.textContent = enabled ? 'auth on' : 'auth off';
    chip.className = 'status-chip settings-meta-chip ' + (enabled ? 'ok' : '');
  }

  async function loadAccess() {
    try {
      const j = await fetchJSON('/api/access');
      const list = [];
      for (const u of j.localhost_urls || []) list.push(`<div class="access-item"><span class="kind">localhost</span><span class="value">${esc(u)}</span><button type="button" class="btn secondary compact copy-url" data-url="${esc(u)}">Copy</button></div>`);
      for (const u of j.lan_urls || []) list.push(`<div class="access-item"><span class="kind">LAN</span><span class="value">${esc(u)}</span><button type="button" class="btn secondary compact copy-url" data-url="${esc(u)}">Copy</button></div>`);
      for (const u of j.tailscale_urls || []) list.push(`<div class="access-item"><span class="kind">tailscale</span><span class="value">${esc(u)}</span><button type="button" class="btn secondary compact copy-url" data-url="${esc(u)}">Copy</button></div>`);
      list.push(`<div class="access-item"><span class="kind">cloudflare</span><span class="value">${j.cloudflare && j.cloudflare.running ? 'tunnel process detected' : 'not detected'}</span><button type="button" class="btn secondary compact" disabled>Copy</button></div>`);
      document.getElementById('access-list').innerHTML = list.join('');
      document.querySelectorAll('.copy-url').forEach(btn => btn.addEventListener('click', async () => {
        await navigator.clipboard.writeText(btn.dataset.url || '');
        showFlash('ok', 'URL copied');
      }));
      const accessSummary = document.getElementById('access-summary');
      if (accessSummary) {
        accessSummary.textContent = `${(j.lan_urls||[]).length} LAN · ${(j.tailscale_urls||[]).length} TS · ${j.cloudflare && j.cloudflare.running ? 'CF on' : 'CF off'}`;
        accessSummary.className = 'status-chip settings-meta-chip ok';
      }
    } catch (e) {
      document.getElementById('access-list').innerHTML = `<div class="access-item"><span class="kind">error</span><span class="value">${esc(e.message)}</span></div>`;
      const accessSummary = document.getElementById('access-summary');
      if (accessSummary) {
        accessSummary.textContent = 'net · failed';
        accessSummary.className = 'status-chip settings-meta-chip err';
      }
    }
  }

  async function loadRuntime() {
    try {
      const j = await fetchJSON('/api/runtime');
      const svc = [
        `<span class="status-chip ${j.web.running ? 'ok' : 'err'}">web ${j.web.running ? 'up' : 'down'}</span>`,
        `<span class="status-chip ${j.collector.running ? 'ok' : 'err'}">collector ${j.collector.running ? 'up' : 'down'}</span>`,
        `<span class="status-chip ${j.cloudflared.running ? 'ok' : 'warn'}">cloudflared ${j.cloudflared.running ? 'on' : 'off'}</span>`,
        `<span class="status-chip ${j.tailscaled.running ? 'ok' : 'warn'}">tailscaled ${j.tailscaled.running ? 'on' : 'off'}</span>`
      ];
      document.getElementById('service-row').innerHTML = svc.join('');
      document.getElementById('svc-summary').textContent = `${j.web.running ? 'web up' : 'web down'} · ${j.collector.running ? 'collector up' : 'collector down'}`;
      document.getElementById('svc-summary').className = 'status-chip settings-meta-chip ' + (j.web.running && j.collector.running ? 'ok' : 'err');
      document.getElementById('runtime-chip-grid').innerHTML = [
        `<span class="status-chip ok">web pid ${j.web.pid ?? '—'}</span>`,
        `<span class="status-chip ok">collector pid ${j.collector.pid ?? '—'}</span>`,
        `<span class="status-chip">web up ${fmtUptime(j.web.uptime_sec)}</span>`,
        `<span class="status-chip">collector up ${fmtUptime(j.collector.uptime_sec)}</span>`
      ].join('');
    } catch (e) {
      document.getElementById('service-row').innerHTML = `<span class="status-chip err">runtime failed: ${esc(e.message)}</span>`;
      document.getElementById('svc-summary').textContent = 'svc · failed';
      document.getElementById('svc-summary').className = 'status-chip settings-meta-chip err';
    }
  }

  function renderHistory(rows) {
    const box = document.getElementById('notify-history');
    if (!box) return;
    if (!rows.length) {
      box.innerHTML = '<div class="history-item"><span class="text-muted">No notification history yet.</span></div>';
      return;
    }
    box.innerHTML = rows.slice(0, 8).map(r => `
      <div class="history-item">
        <span class="history-head"><span class="badge ${r.success ? 'ok' : 'fail'}">${r.channel}</span><span class="text-muted">${new Date((r.ts||0)*1000).toLocaleTimeString()}</span></span>
        <span class="history-body ${r.success ? 'ok' : 'err'}">${r.success ? 'sent' : esc(explainNotifyError(r.error || 'failed'))}</span>
      </div>`).join('');
  }

  async function refreshStatus() {
    try {
      const rows = await fetchJSON('/api/notifications?limit=20');
      const tg = rows.find(x => x.channel === 'telegram');
      const dc = rows.find(x => x.channel === 'discord');
      if (tg) {
        const chip = document.getElementById('tg-last-status');
        if (chip) {
          chip.textContent = tg.success ? 'last sent' : 'last failed';
          chip.className = 'status-chip ' + (tg.success ? 'ok' : 'err');
        }
        const errEl = document.getElementById('tg-last-error');
        if (errEl) errEl.textContent = tg.success ? '—' : explainNotifyError(tg.error);
        if (!tg.success) {
          const resEl = document.getElementById('test-tg-result');
          if (resEl) resEl.textContent = explainNotifyError(tg.error);
        }
      }
      if (dc) {
        const chip = document.getElementById('dc-last-status');
        if (chip) {
          chip.textContent = dc.success ? 'last sent' : 'last failed';
          chip.className = 'status-chip ' + (dc.success ? 'ok' : 'err');
        }
        const errEl = document.getElementById('dc-last-error');
        if (errEl) errEl.textContent = dc.success ? '—' : explainNotifyError(dc.error);
        if (!dc.success) {
          const resEl = document.getElementById('test-dc-result');
          if (resEl) resEl.textContent = explainNotifyError(dc.error);
        }
      }
      renderHistory(rows);
    } catch (e) {}
  }

  function syncPresetSliders() {
    const cpuInput = document.querySelector('[name="threshold_cpu_load_1m_value"]');
    const memInput = document.querySelector('[name="threshold_mem_free_mb_value"]');
    const tempInput = document.querySelector('[name="threshold_cpu_temp_c_value"]');
    const cpuSlider = document.getElementById('cpu-load-slider');
    const memSlider = document.getElementById('mem-free-slider');
    const tempSlider = document.getElementById('cpu-temp-slider');
    if (!cpuInput || !memInput || !tempInput || !cpuSlider || !memSlider || !tempSlider) return;

    const repaint = () => {
      document.getElementById('cpu-load-slider-val').textContent = Number(cpuInput.value || 0).toFixed(1);
      document.getElementById('mem-free-slider-val').textContent = `${parseInt(memInput.value || 0, 10)} MB`;
      document.getElementById('cpu-temp-slider-val').textContent = `${parseInt(tempInput.value || 0, 10)} °C`;
      cpuSlider.value = cpuInput.value || 4.0;
      memSlider.value = memInput.value || 500;
      tempSlider.value = tempInput.value || 85;
    };
    cpuSlider.addEventListener('input', () => { cpuInput.value = cpuSlider.value; repaint(); setSaveState('unsaved', 'warn'); });
    memSlider.addEventListener('input', () => { memInput.value = memSlider.value; repaint(); setSaveState('unsaved', 'warn'); });
    tempSlider.addEventListener('input', () => { tempInput.value = tempSlider.value; repaint(); setSaveState('unsaved', 'warn'); });
    [cpuInput, memInput, tempInput].forEach(el => el.addEventListener('input', repaint));
    document.querySelectorAll('.threshold-preset').forEach(btn => btn.addEventListener('click', () => {
      const p = btn.dataset.preset;
      if (p === 'safe') {
        cpuInput.value = 6.0; tempInput.value = 90; memInput.value = 384;
      } else if (p === 'aggressive') {
        cpuInput.value = 3.0; tempInput.value = 80; memInput.value = 1024;
      } else {
        cpuInput.value = 4.0; tempInput.value = 85; memInput.value = 500;
      }
      repaint();
      setSaveState('unsaved', 'warn');
    }));
    repaint();
  }

  document.getElementById('settings-form')?.addEventListener('submit', e => { e.preventDefault(); saveSettings(false); });
  document.getElementById('apply-now')?.addEventListener('click', () => saveSettings(true));
  document.getElementById('restore-defaults-inline')?.addEventListener('click', () => restoreActiveTabDefaults());
  document.getElementById('save-tab')?.addEventListener('click', () => saveActiveTab(false));
  document.getElementById('reset-tab')?.addEventListener('click', resetActiveTab);
  document.getElementById('export-settings')?.addEventListener('click', exportSettings);
  document.getElementById('import-settings-btn')?.addEventListener('click', chooseImportFile);
  document.getElementById('import-modal-cancel')?.addEventListener('click', closeImportModal);
  document.getElementById('import-modal-backdrop')?.addEventListener('click', (e) => { if (e.target.id === 'import-modal-backdrop') closeImportModal(); });
  document.getElementById('import-modal-confirm')?.addEventListener('click', async () => {
    const file = pendingImportFile;
    closeImportModal();
    try {
      await importSettingsFile(file);
    } catch (err) {
      setSaveState('ready');
      showFlash('err', err.message || 'Import failed');
    }
  });

  document.getElementById('import-settings-file')?.addEventListener('change', async (e) => {
    const file = e.target.files && e.target.files[0];
    document.getElementById('import-file-name').textContent = file ? file.name : 'No import file selected.';
    if (file) openImportModal(file);
    e.target.value = '';
  });

  document.getElementById('restart-collector')?.addEventListener('click', async () => {
    const ok = await window.SystorDialog.confirm('Restart the collector now? It will be down briefly.', 'Restart Collector');
    if (!ok) return;
    try {
      const j = await fetchJSON('/api/restart-collector', { method: 'POST' });
      showFlash('ok', j.message || 'collector restarting');
      setTimeout(loadRuntime, 1500);
    } catch (e) {
      showFlash('err', e.message);
    }
  });

  document.getElementById('restart-web')?.addEventListener('click', async () => {
    const ok = await window.SystorDialog.confirm('Restart the web dashboard now? The page will need a refresh.', 'Restart Web');
    if (!ok) return;
    try {
      const j = await fetchJSON('/api/restart-web', { method: 'POST' });
      showFlash('ok', j.message || 'web restarting', 9000);
    } catch (e) {
      showFlash('err', e.message);
    }
  });

  document.getElementById('reload-page')?.addEventListener('click', () => location.reload());
  document.getElementById('test-tg')?.addEventListener('click', () => testChannel('telegram'));
  document.getElementById('test-dc')?.addEventListener('click', () => testChannel('discord'));
  document.getElementById('save-test-tg')?.addEventListener('click', () => saveThenTest('telegram'));
  document.getElementById('save-test-dc')?.addEventListener('click', () => saveThenTest('discord'));
  document.getElementById('copy-tg-payload')?.addEventListener('click', () => copyPayload('telegram'));
  document.getElementById('copy-dc-payload')?.addEventListener('click', () => copyPayload('discord'));

  Array.from(document.querySelectorAll('#settings-form input, #settings-form select')).forEach(el => {
    el.addEventListener('change', () => { setSaveState('unsaved', 'warn'); updateAuthSummaryChip(); updateDirtyState(); });
    el.addEventListener('input', () => { setSaveState('unsaved', 'warn'); updateAuthSummaryChip(); updateDirtyState(); });
  });

  const restoreFileEl = document.getElementById('restore-archive-file');
  const restoreNameEl = document.getElementById('restore-file-name');
  const restoreBtnEl = document.getElementById('restore-archive-btn');
  if (restoreFileEl && restoreBtnEl) {
    restoreFileEl.addEventListener('change', () => {
      const f = restoreFileEl.files && restoreFileEl.files[0];
      if (f) {
        if (restoreNameEl) restoreNameEl.textContent = `${f.name} (${(f.size / 1024).toFixed(1)} KB)`;
        restoreBtnEl.style.display = 'inline-flex';
      } else {
        if (restoreNameEl) restoreNameEl.textContent = 'No archive selected.';
        restoreBtnEl.style.display = 'none';
      }
    });

    restoreBtnEl.addEventListener('click', async () => {
      const f = restoreFileEl.files && restoreFileEl.files[0];
      if (!f) return;
      const ok = await window.SystorDialog.confirm(`Restore system from archive "${f.name}"?\n\nThis will restore your SQLite database and configuration settings. A safe pre-restore rollback snapshot will be taken first.`, 'Disaster Recovery');
      if (!ok) return;
      restoreBtnEl.disabled = true;
      restoreBtnEl.textContent = 'Restoring…';
      try {
        const fd = new FormData();
        fd.append('backup', f);
        const res = await fetch('/api/backup/restore', {
          method: 'POST',
          body: fd
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'Restore failed');
        showFlash('ok', data.message || 'System restored successfully! Reloading in 2s…', 10000);
        setTimeout(() => location.reload(), 2000);
      } catch (e) {
        showFlash('err', 'Restore failed: ' + e.message);
        restoreBtnEl.disabled = false;
        restoreBtnEl.textContent = 'Restore archive';
      }
    });
  }

  initSettingsTabs();
  captureInitialState();
  syncPresetSliders();
  loadAccess();
  loadRuntime();
  refreshStatus();
  updateAuthSummaryChip();
  updateLastSavedChip();
  updateDirtyState();
  setInterval(() => updateLastSavedChip(), 30000);
})();
