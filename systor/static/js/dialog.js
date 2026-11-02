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

  window.SystorDialog = DialogManager;
})();
