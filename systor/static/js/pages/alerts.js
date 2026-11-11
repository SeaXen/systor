/**
 * Systor Alerts & Deliveries Page Controller
 */
(function() {
  'use strict';

  const IS_PRIVATE = document.body ? document.body.dataset.clientPrivate === 'true' : false;

  function esc(s) {
    return String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  }

  function flash(type, msg) {
    const el = document.getElementById('alert-flash');
    if (!el) return;
    el.className = 'flash ' + type;
    el.textContent = msg;
    setTimeout(() => { el.className = ''; el.textContent = ''; }, 7000);
  }

  async function loadAlerts() {
    const dot = document.getElementById('alert-dot');
    try {
      const alerts = await window.fetchJSON('/api/alerts?limit=200');
      let notifs = [];
      if (IS_PRIVATE) {
        notifs = await window.fetchJSON('/api/notifications?limit=100').catch(() => []);
      }
      const alertsBody = document.getElementById('alerts-body');
      if (alertsBody) {
        alertsBody.innerHTML = alerts.length ? alerts.map(x => `
          <tr>
            <td><span class="mono">${window.timeShort(x.ts)}</span> <span class="text-muted">${window.timeAgo(x.ts)}</span></td>
            <td><span class="badge ${esc(x.severity)}">${esc(x.severity)}</span></td>
            <td>${esc(x.metric)}</td>
            <td class="mono">${x.value ?? '—'}</td>
            <td class="mono">${x.threshold ?? '—'}</td>
            <td>${esc(x.message)}</td>
          </tr>`).join('') : '<tr><td colspan="6" class="empty-cell">No alerts</td></tr>';
      }

      const notifBody = document.getElementById('notif-body');
      if (IS_PRIVATE && notifBody) {
        notifBody.innerHTML = notifs.length ? notifs.map(x => `
          <tr>
            <td><span class="mono">${window.timeShort(x.ts)}</span> <span class="text-muted">${window.timeAgo(x.ts)}</span></td>
            <td>${esc(x.channel)}</td>
            <td><span class="badge ${x.success ? 'ok' : 'fail'}">${x.success ? 'sent' : 'failed'}</span></td>
            <td class="mono text-muted">${esc(x.error || '')}</td>
          </tr>`).join('') : '<tr><td colspan="4" class="empty-cell">No notifications sent yet</td></tr>';
      }

      const failures = notifs.filter(x => !x.success).length;
      const statusEl = document.getElementById('alert-status');
      if (statusEl) {
        statusEl.textContent = IS_PRIVATE
          ? `${alerts.length} alerts · ${notifs.length} deliveries · ${failures} failed · ${new Date().toLocaleTimeString()}`
          : `${alerts.length} alerts · ${new Date().toLocaleTimeString()}`;
      }
      if (dot) dot.className = 'dot ' + (IS_PRIVATE && failures ? 'yellow' : 'green');
    } catch (e) {
      if (dot) dot.className = 'dot red';
      const statusEl = document.getElementById('alert-status');
      if (statusEl) statusEl.textContent = 'failed: ' + e.message;
    }
  }

  async function testChannel(channel) {
    try {
      const j = await window.fetchJSON('/api/test-' + channel, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: `🧪 systor ${channel} test from Alerts page` })
      });
      flash(j.ok ? 'ok' : 'err', channel + ': ' + (j.ok ? 'sent' : (j.error || 'failed')));
    } catch (e) {
      flash('err', channel + ': ' + e.message);
    }
    await loadAlerts();
  }

  document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('refresh-alerts')?.addEventListener('click', loadAlerts);
    if (IS_PRIVATE) {
      document.getElementById('test-telegram')?.addEventListener('click', () => testChannel('telegram'));
      document.getElementById('test-discord')?.addEventListener('click', () => testChannel('discord'));
    }
    loadAlerts();
    setInterval(loadAlerts, 10000);
  });
})();
