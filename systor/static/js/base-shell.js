/**
 * Systor base shell JavaScript.
 * Handles top navigation, global monitoring chips, mobile navigation drawer,
 * theme synchronizer, and emergency terminal drawer.
 */
(function() {
  'use strict';

  const IS_PRIVATE = document.body ? document.body.dataset.clientPrivate === 'true' : false;

  function fmt(n, unit) {
    if (n === null || n === undefined || Number.isNaN(Number(n))) return '—';
    const v = Number(n);
    const s = Math.abs(v) >= 100 ? v.toFixed(0) : Math.abs(v) >= 10 ? v.toFixed(1) : v.toFixed(2);
    return s + (unit ? ' ' + unit : '');
  }

  async function getJSON(url) {
    const r = await fetch(url, { cache: 'no-store' });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return r.json();
  }

  // --- Global Header Meta Polling ---
  async function updateHeaderMeta() {
    const el = document.getElementById('global-header-meta');
    if (!el) return;

    try {
      const snap = await getJSON('/api/snapshot');
      const runtime = IS_PRIVATE ? await getJSON('/api/runtime').catch(() => null) : null;
      const cpu = snap.cpu || {};
      const mem = snap.memory || {};
      const web = (runtime && runtime.web) || {};
      const collector = (runtime && runtime.collector) || {};
      const st = (runtime && runtime.storage) || {};
      const memPct = mem.total_mb ? Math.round(100 * mem.used_mb / mem.total_mb) : null;
      const upd = snap.system_updates || {};

      let updChips = '';
      if (upd.reboot_required) {
        updChips += ' <span class="header-chip reboot-required"><span class="chip-k">Reboot</span><strong>Required</strong></span>';
      }
      if (upd.updates_security > 0) {
        updChips += ` <span class="header-chip security-updates"><span class="chip-k">Security</span><strong>${upd.updates_security}</strong></span>`;
      } else if (upd.updates_total > 0) {
        updChips += ` <span class="header-chip"><span class="chip-k">Updates</span><strong>${upd.updates_total}</strong></span>`;
      }

      if (!IS_PRIVATE) {
        el.innerHTML = `<span class="header-chip"><span class="chip-k cpu-chip">CPU</span><strong>${fmt(cpu.percent, '%')}</strong></span><span class="header-chip"><span class="chip-k temp-chip">Temp</span><strong>${fmt(cpu.temp_c, '°C')}</strong></span><span class="header-chip"><span class="chip-k load-chip-mini">Load</span><strong>${fmt(cpu.load_1m)}</strong></span><span class="header-chip"><span class="chip-k ram-chip">RAM</span><strong>${memPct === null ? '—' : memPct + '%'}</strong></span>${updChips}`;
        return;
      }

      el.innerHTML = `<span class="header-chip"><span class="chip-k cpu-chip">CPU</span><strong>${fmt(cpu.percent, '%')}</strong></span><span class="header-chip"><span class="chip-k temp-chip">Temp</span><strong>${fmt(cpu.temp_c, '°C')}</strong></span><span class="header-chip"><span class="chip-k load-chip-mini">Load</span><strong>${fmt(cpu.load_1m)}</strong></span><span class="header-chip"><span class="chip-k ram-chip">RAM</span><strong>${memPct === null ? '—' : memPct + '%'}</strong></span><span class="header-sep">·</span><span class="header-chip" title="Web & Collector internal process CPU load"><span class="chip-k sys-chip">Internal CPU</span><strong>W ${fmt(web.cpu_percent, '%')} · C ${fmt(collector.cpu_percent, '%')}</strong></span><span class="header-chip" title="Web & Collector RSS memory footprint"><span class="chip-k sys-chip">Internal RAM</span><strong>W ${fmt(web.rss_mb, 'MB')} · C ${fmt(collector.rss_mb, 'MB')}</strong></span><span class="header-chip"><span class="chip-k disk-chip">Disk</span><strong>${fmt(st.total_mb, 'MB')}</strong></span>${updChips}`;
    } catch (e) {
      /* Keep fallback text */
    }
  }

  // --- Theme Controller & Synchronizer ---
  const THEME_ORDER = ['warm-dark', 'light', 'classic-dark'];
  const THEME_METAS = {
    'warm-dark':    { icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" width="16" height="16" aria-hidden="true"><path d="M20.5 14.2A8.5 8.5 0 0 1 9.8 3.5a8.5 8.5 0 1 0 10.7 10.7Z"/></svg>', label: 'Warm Dark', color: '#0b0b0b', scheme: 'dark' },
    'light':        { icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" width="16" height="16" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>', label: 'Paper Light', color: '#f9f9f7', scheme: 'light' },
    'classic-dark': { icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" width="16" height="16" aria-hidden="true"><rect x="2" y="4" width="20" height="14" rx="2"/><path d="M8 21h8M12 18v3"/></svg>', label: 'Classic Dark', color: '#0f1117', scheme: 'dark' }
  };

  function updateThemeUI(theme) {
    const meta = THEME_METAS[theme] || THEME_METAS['warm-dark'];
    const iconEl = document.getElementById('theme-toggle-icon');
    const labelEl = document.getElementById('theme-toggle-label');
    const btn = document.getElementById('theme-toggle-btn');
    const metaColor = document.querySelector('meta[name="theme-color"]');

    if (iconEl) iconEl.innerHTML = meta.icon;
    if (labelEl) labelEl.textContent = meta.label;
    if (btn) {
      btn.title = `Theme: ${meta.label} (click to switch)`;
      btn.setAttribute('aria-label', `Theme: ${meta.label} (click to switch)`);
    }
    if (metaColor) metaColor.setAttribute('content', meta.color);

    document.querySelectorAll('.theme-picker-card').forEach(card => {
      const cardTheme = card.getAttribute('data-theme-choice');
      const isSelected = cardTheme === theme;
      card.classList.toggle('active', isSelected);
      card.setAttribute('aria-checked', isSelected ? 'true' : 'false');
    });
  }

  window.setSystorTheme = function(newTheme) {
    if (!THEME_ORDER.includes(newTheme)) newTheme = 'warm-dark';
    document.documentElement.setAttribute('data-theme', newTheme);
    document.documentElement.style.colorScheme = THEME_METAS[newTheme].scheme;
    try { localStorage.setItem('systor_theme', newTheme); } catch (e) {}
    updateThemeUI(newTheme);
    window.dispatchEvent(new CustomEvent('systor:theme-change', { detail: { theme: newTheme } }));
  };

  window.addEventListener('systor:theme-change', function(e) {
    if (e && e.detail && e.detail.theme) {
      updateThemeUI(e.detail.theme);
    }
  });

  // --- Mobile Navigation Sheet ---
  function initMobileNav() {
    const moreBtn = document.getElementById('mobile-more-btn');
    const sheet = document.getElementById('mobile-more-sheet');
    const closeBtn = document.getElementById('mobile-more-close');
    const backdrop = document.getElementById('mobile-more-backdrop');

    if (!moreBtn || !sheet) return;

    function openSheet() {
      sheet.style.display = 'flex';
      sheet.classList.add('open');
      moreBtn.setAttribute('aria-expanded', 'true');
    }

    function closeSheet() {
      sheet.style.display = 'none';
      sheet.classList.remove('open');
      moreBtn.setAttribute('aria-expanded', 'false');
    }

    moreBtn.addEventListener('click', () => {
      if (sheet.style.display === 'flex') closeSheet();
      else openSheet();
    });

    closeBtn?.addEventListener('click', closeSheet);
    backdrop?.addEventListener('click', closeSheet);
  }

  // --- Emergency Terminal Drawer ---
  function initTerminalDrawer() {
    const toggle = document.getElementById('terminal-drawer-toggle');
    const drawer = document.getElementById('terminal-drawer');
    if (!toggle || !drawer) return;

    const unlockBtn = document.getElementById('term-unlock-btn');
    const passInput = document.getElementById('term-password-input');
    const authErr = document.getElementById('term-auth-error');
    const lockView = document.getElementById('term-lock-view');
    const consoleView = document.getElementById('term-console-view');
    const statusChip = document.getElementById('term-status-chip');
    const output = document.getElementById('term-output');
    const cmdInput = document.getElementById('term-command-input');
    const closeBtn = document.getElementById('term-close-btn');
    const clearBtn = document.getElementById('term-clear-btn');

    let termToken = sessionStorage.getItem('systor_term_token') || null;
    const history = [];
    let historyIdx = -1;

    function setUnlocked() {
      if (lockView) lockView.style.display = 'none';
      if (consoleView) consoleView.style.display = 'flex';
      if (statusChip) {
        statusChip.textContent = 'Unlocked';
        statusChip.className = 'status-chip ok';
      }
      cmdInput?.focus();
    }

    function setLocked() {
      if (lockView) lockView.style.display = 'flex';
      if (consoleView) consoleView.style.display = 'none';
      if (statusChip) {
        statusChip.textContent = 'Locked';
        statusChip.className = 'status-chip neutral';
      }
      sessionStorage.removeItem('systor_term_token');
      termToken = null;
    }

    function openDrawer() {
      drawer.style.display = 'flex';
      drawer.classList.add('open');
      if (termToken) {
        setUnlocked();
      } else {
        setLocked();
        setTimeout(() => passInput?.focus(), 50);
      }
    }

    function closeDrawer() {
      drawer.style.display = 'none';
      drawer.classList.remove('open');
      toggle?.focus();
    }

    toggle.addEventListener('click', () => {
      if (drawer.classList.contains('open') || drawer.style.display === 'flex') closeDrawer();
      else openDrawer();
    });

    closeBtn?.addEventListener('click', closeDrawer);
    clearBtn?.addEventListener('click', () => {
      if (output) output.textContent = '';
    });

    window.addEventListener('keydown', (ev) => {
      if (ev.key === 'Escape' && (drawer.classList.contains('open') || drawer.style.display === 'flex')) {
        closeDrawer();
      }
    });

    unlockBtn?.addEventListener('click', async () => {
      const pw = passInput ? passInput.value.trim() : '';
      if (!pw) return;
      if (authErr) authErr.style.display = 'none';
      unlockBtn.disabled = true;
      unlockBtn.textContent = 'Unlocking…';
      try {
        const res = await fetch('/api/terminal/auth', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ password: pw }),
        });
        const data = await res.json();
        if (!res.ok || !data.ok) {
          throw new Error(data.error || 'Authentication failed');
        }
        termToken = data.token;
        sessionStorage.setItem('systor_term_token', termToken);
        if (passInput) passInput.value = '';
        setUnlocked();
        if (output) output.textContent = '--- Emergency Terminal Console Initialized ---\nType commands below.\n\n';
      } catch (err) {
        if (authErr) {
          authErr.textContent = err.message;
          authErr.style.display = 'block';
        }
      } finally {
        unlockBtn.disabled = false;
        unlockBtn.textContent = 'Unlock';
      }
    });

    passInput?.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter') unlockBtn?.click();
    });

    cmdInput?.addEventListener('keydown', async (ev) => {
      if (ev.key === 'ArrowUp') {
        if (history.length && historyIdx < history.length - 1) {
          historyIdx++;
          cmdInput.value = history[history.length - 1 - historyIdx];
        }
        ev.preventDefault();
        return;
      }
      if (ev.key === 'ArrowDown') {
        if (historyIdx > 0) {
          historyIdx--;
          cmdInput.value = history[history.length - 1 - historyIdx];
        } else if (historyIdx === 0) {
          historyIdx = -1;
          cmdInput.value = '';
        }
        ev.preventDefault();
        return;
      }
      if (ev.key === 'Enter') {
        const cmd = cmdInput.value.trim();
        if (!cmd) return;
        history.push(cmd);
        historyIdx = -1;
        cmdInput.value = '';

        if (output) {
          output.textContent += `$ ${cmd}\n`;
          output.scrollTop = output.scrollHeight;
        }

        try {
          const res = await fetch('/api/terminal/exec', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ token: termToken, command: cmd }),
          });
          const data = await res.json();
          if (res.status === 401 || (data.error && data.error.includes('expired'))) {
            setLocked();
            if (authErr) {
              authErr.textContent = 'Session expired. Please re-authenticate.';
              authErr.style.display = 'block';
            }
            return;
          }
          if (output) output.textContent += (data.output || data.error || '') + '\n\n';
        } catch (err) {
          if (output) output.textContent += `[Error: ${err.message}]\n\n`;
        }
        if (output) output.scrollTop = output.scrollHeight;
      }
    });
  }

  // --- Initializer ---
  document.addEventListener('DOMContentLoaded', function() {
    const curTheme = document.documentElement.getAttribute('data-theme') || 'warm-dark';
    updateThemeUI(curTheme);

    const themeBtn = document.getElementById('theme-toggle-btn');
    if (themeBtn) {
      themeBtn.addEventListener('click', function() {
        const current = document.documentElement.getAttribute('data-theme') || 'warm-dark';
        const idx = THEME_ORDER.indexOf(current);
        const nextTheme = THEME_ORDER[(idx + 1) % THEME_ORDER.length];
        window.setSystorTheme(nextTheme);
      });
    }

    updateHeaderMeta();
    setInterval(updateHeaderMeta, 15000);

    initMobileNav();
    initTerminalDrawer();

    if ('serviceWorker' in navigator && location.protocol.startsWith('http')) {
      navigator.serviceWorker.register('/sw.js').catch(function() {});
    }
  });
})();
