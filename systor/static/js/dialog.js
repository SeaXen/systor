/**
 * Systor accessible dialog focus manager.
 * Handles focus trap, Escape to close, and focus restoration for modal dialogs.
 */
(function() {
  'use strict';

  const FOCUSABLE_SELECTOR = 'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

  class DialogManager {
    constructor(dialogEl, options = {}) {
      this.dialog = typeof dialogEl === 'string' ? document.querySelector(dialogEl) : dialogEl;
      this.options = Object.assign({
        onClose: null,
        initialFocus: null,
      }, options);
      this.invoker = null;
      this._boundKeydown = this._handleKeydown.bind(this);
    }

    open(invokerEl) {
      if (!this.dialog) return;
      this.invoker = invokerEl || document.activeElement;
      this.dialog.setAttribute('role', 'dialog');
      this.dialog.setAttribute('aria-modal', 'true');
      this.dialog.style.display = 'flex';
      this.dialog.classList.add('open');
      document.addEventListener('keydown', this._boundKeydown);

      const focusTarget = this.options.initialFocus
        ? this.dialog.querySelector(this.options.initialFocus)
        : this.dialog.querySelector(FOCUSABLE_SELECTOR);

      if (focusTarget) {
        setTimeout(() => focusTarget.focus(), 50);
      }
    }

    close() {
      if (!this.dialog) return;
      this.dialog.style.display = 'none';
      this.dialog.classList.remove('open');
      document.removeEventListener('keydown', this._boundKeydown);
      if (this.options.onClose) {
        this.options.onClose();
      }
      if (this.invoker && typeof this.invoker.focus === 'function') {
        this.invoker.focus();
      }
    }

    _handleKeydown(e) {
      if (e.key === 'Escape') {
        e.preventDefault();
        this.close();
        return;
      }

      if (e.key === 'Tab') {
        const focusables = Array.from(this.dialog.querySelectorAll(FOCUSABLE_SELECTOR))
          .filter(el => el.offsetParent !== null);
        if (!focusables.length) return;

        const first = focusables[0];
        const last = focusables[focusables.length - 1];

        if (e.shiftKey) {
          if (document.activeElement === first || !this.dialog.contains(document.activeElement)) {
            e.preventDefault();
            last.focus();
          }
        } else {
          if (document.activeElement === last || !this.dialog.contains(document.activeElement)) {
            e.preventDefault();
            first.focus();
          }
        }
      }
    }
  }

  function _createModalContainer() {
    let root = document.getElementById('systor-modal-root');
    if (!root) {
      root = document.createElement('div');
      root.id = 'systor-modal-root';
      document.body.appendChild(root);
    }
    return root;
  }

  function _escapeHtml(s) {
    return String(s || '').replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
  }

  DialogManager.alert = function(message, title = 'Notice') {
    return new Promise(resolve => {
      const root = _createModalContainer();
      const modal = document.createElement('div');
      modal.className = 'modal-backdrop';
      modal.setAttribute('role', 'dialog');
      modal.setAttribute('aria-modal', 'true');
      modal.setAttribute('aria-label', title);
      modal.innerHTML = `
        <div class="modal-card">
          <div class="modal-head">
            <h3>${_escapeHtml(title)}</h3>
          </div>
          <div class="modal-body">
            <p style="margin: 0; line-height: 1.5; color: var(--text);">${_escapeHtml(message)}</p>
          </div>
          <div class="modal-actions">
            <button type="button" class="btn primary compact ok-btn">OK</button>
          </div>
        </div>
      `;
      root.appendChild(modal);

      const mgr = new DialogManager(modal, {
        onClose: () => {
          modal.remove();
          resolve();
        }
      });
      mgr.open();

      const okBtn = modal.querySelector('.ok-btn');
      okBtn?.addEventListener('click', () => mgr.close());
    });
  };

  DialogManager.confirm = function(message, title = 'Confirm Action') {
    return new Promise(resolve => {
      const root = _createModalContainer();
      const modal = document.createElement('div');
      modal.className = 'modal-backdrop';
      modal.setAttribute('role', 'dialog');
      modal.setAttribute('aria-modal', 'true');
      modal.setAttribute('aria-label', title);
      modal.innerHTML = `
        <div class="modal-card">
          <div class="modal-head">
            <h3>${_escapeHtml(title)}</h3>
          </div>
          <div class="modal-body">
            <p style="margin: 0; line-height: 1.5; color: var(--text);">${_escapeHtml(message).replace(/\\n/g, '<br>')}</p>
          </div>
          <div class="modal-actions">
            <button type="button" class="btn secondary compact cancel-btn">Cancel</button>
            <button type="button" class="btn primary compact confirm-btn">Confirm</button>
          </div>
        </div>
      `;
      root.appendChild(modal);

      let confirmed = false;
      const mgr = new DialogManager(modal, {
        onClose: () => {
          modal.remove();
          resolve(confirmed);
        }
      });
      mgr.open();

      modal.querySelector('.confirm-btn')?.addEventListener('click', () => {
        confirmed = true;
        mgr.close();
      });
      modal.querySelector('.cancel-btn')?.addEventListener('click', () => {
        confirmed = false;
        mgr.close();
      });
    });
  };

  DialogManager.prompt = function(message, defaultValue = '', options = {}) {
    return new Promise(resolve => {
      const isPassword = typeof options === 'boolean' ? options : !!options.isPassword;
      const title = options.title || (isPassword ? 'Password Required' : 'Input Required');
      const inputType = isPassword ? 'password' : 'text';

      const root = _createModalContainer();
      const modal = document.createElement('div');
      modal.className = 'modal-backdrop';
      modal.setAttribute('role', 'dialog');
      modal.setAttribute('aria-modal', 'true');
      modal.setAttribute('aria-label', title);
      modal.innerHTML = `
        <div class="modal-card">
          <div class="modal-head">
            <h3>${_escapeHtml(title)}</h3>
          </div>
          <div class="modal-body">
            <label style="display:block; margin-bottom: 8px; color: var(--text);">${_escapeHtml(message)}</label>
            <input type="${inputType}" class="control-input modal-prompt-input" value="${_escapeHtml(defaultValue)}" style="width: 100%; box-sizing: border-box;" autocomplete="off">
          </div>
          <div class="modal-actions">
            <button type="button" class="btn secondary compact cancel-btn">Cancel</button>
            <button type="button" class="btn primary compact submit-btn">Submit</button>
          </div>
        </div>
      `;
      root.appendChild(modal);

      const input = modal.querySelector('.modal-prompt-input');
      let resultVal = null;
      const mgr = new DialogManager(modal, {
        initialFocus: '.modal-prompt-input',
        onClose: () => {
          modal.remove();
          resolve(resultVal);
        }
      });
      mgr.open();

      function submit() {
        resultVal = input ? input.value : '';
        mgr.close();
      }

      modal.querySelector('.submit-btn')?.addEventListener('click', submit);
      modal.querySelector('.cancel-btn')?.addEventListener('click', () => {
        resultVal = null;
        mgr.close();
      });
      input?.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          submit();
        }
      });
    });
  };

  window.SystorDialog = DialogManager;
  window.systorAlert = DialogManager.alert;
  window.systorConfirm = DialogManager.confirm;
  window.systorPrompt = DialogManager.prompt;
})();
