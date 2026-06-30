(function () {
  'use strict';

  var STORAGE_KEY = 'theme_mode';
  var VALID = ['light', 'dark', 'system'];

  function getStoredMode() {
    try {
      var m = localStorage.getItem(STORAGE_KEY);
      if (VALID.indexOf(m) !== -1) return m;
    } catch (_) {}
    return 'system';
  }

  function resolveTheme(mode) {
    if (mode === 'system') {
      return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    }
    return mode;
  }

  function updateMetaThemeColor(resolved) {
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute('content', resolved === 'dark' ? '#1a1a2e' : '#c8102e');
  }

  function applyTheme(mode) {
    var resolved = resolveTheme(mode);
    document.documentElement.setAttribute('data-bs-theme', resolved);
    updateMetaThemeColor(resolved);
    updateToggleUI(mode);
  }

  function updateToggleUI(mode) {
    document.querySelectorAll('[data-theme-mode]').forEach(function (btn) {
      var active = btn.getAttribute('data-theme-mode') === mode;
      btn.classList.toggle('active', active);
      btn.setAttribute('aria-checked', active ? 'true' : 'false');
    });
    var icon = document.getElementById('themeToggleIcon');
    if (icon) {
      icon.className = mode === 'dark'
        ? 'bi bi-moon-stars-fill'
        : mode === 'light'
          ? 'bi bi-sun-fill'
          : 'bi bi-circle-half';
    }
  }

  function setThemeMode(mode, persistServer) {
    if (VALID.indexOf(mode) === -1) return;
    try { localStorage.setItem(STORAGE_KEY, mode); } catch (_) {}
    applyTheme(mode);
    if (persistServer) {
      fetch('/api/theme', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: mode }),
      }).catch(function () {});
    }
  }

  window.__setThemeMode = setThemeMode;
  window.__getThemeMode = getStoredMode;

  document.addEventListener('DOMContentLoaded', function () {
    var initial = getStoredMode();
    if (window.__SERVER_THEME_MODE && VALID.indexOf(window.__SERVER_THEME_MODE) !== -1) {
      initial = window.__SERVER_THEME_MODE;
      try { localStorage.setItem(STORAGE_KEY, initial); } catch (_) {}
    }
    applyTheme(initial);

    document.querySelectorAll('[data-theme-mode]').forEach(function (btn) {
      btn.addEventListener('click', function (e) {
        e.preventDefault();
        setThemeMode(btn.getAttribute('data-theme-mode'), !!window.__SERVER_THEME_MODE);
      });
    });

    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', function () {
      if (getStoredMode() === 'system') applyTheme('system');
    });
  });
})();
