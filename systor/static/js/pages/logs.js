/**
 * Systor System Logs Page Controller
 */
(function() {
  'use strict';

  async function loadLogs() {
    const lines = document.getElementById('log-lines')?.value || 200;
    const level = document.getElementById('log-level')?.value || 'all';
    const status = document.getElementById('log-status');
    const dot = document.getElementById('log-dot');
    try {
      const data = await window.fetchJSON(`/logs/raw?lines=${lines}&level=${level}`);
      const visible = (data.lines || []).slice().reverse();
      const text = visible.length ? visible.join('\n') : '(no matching log lines)';
      const logView = document.getElementById('log-view');
      if (logView) logView.textContent = text;
      const logPath = document.getElementById('log-path');
      if (logPath && data.path) logPath.textContent = data.path;

      if (status) {
        status.textContent = `${data.total || 0} lines · ${data.fresh_error_count || 0} fresh errors / 10m · ${new Date().toLocaleTimeString()}`;
      }
      if (dot) {
        dot.className = 'dot ' + ((data.fresh_error_count || 0) ? 'red' : 'green');
      }
    } catch (e) {
      if (status) status.textContent = 'failed: ' + e.message;
      if (dot) dot.className = 'dot red';
      const logView = document.getElementById('log-view');
      if (logView) logView.textContent = 'Could not load logs: ' + e.message;
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('refresh-logs')?.addEventListener('click', loadLogs);
    document.getElementById('log-lines')?.addEventListener('change', loadLogs);
    document.getElementById('log-level')?.addEventListener('change', loadLogs);

    document.getElementById('copy-logs')?.addEventListener('click', async () => {
      const text = document.getElementById('log-view')?.textContent || '';
      try {
        await navigator.clipboard.writeText(text);
        const status = document.getElementById('log-status');
        if (status) status.textContent = 'copied';
      } catch (e) {
        const status = document.getElementById('log-status');
        if (status) status.textContent = 'copy failed';
      }
    });

    document.getElementById('clear-logs')?.addEventListener('click', async () => {
      const ok = await window.systorConfirm('Clear the log file on disk now?', 'Clear Logs');
      if (!ok) return;

      const status = document.getElementById('log-status');
      if (status) status.textContent = 'clearing…';
      try {
        const j = await window.api('/api/logs/clear', { method: 'POST' });
        const logView = document.getElementById('log-view');
        if (logView) logView.textContent = '(log file cleared)';
        if (status) status.textContent = 'cleared · ' + new Date().toLocaleTimeString();
        const dot = document.getElementById('log-dot');
        if (dot) dot.className = 'dot green';
      } catch (e) {
        if (status) status.textContent = 'clear failed: ' + e.message;
        const dot = document.getElementById('log-dot');
        if (dot) dot.className = 'dot red';
      }
    });

    loadLogs();
    setInterval(loadLogs, 10000);
  });
})();
