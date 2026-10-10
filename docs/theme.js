/* Shared theme preference. Loaded before CSS to avoid a flash of the wrong mode. */
"use strict";
(() => {
  const KEY = "radar-theme";
  const THEMES = new Set(["dark", "light"]);
  const root = document.documentElement;
  const ICONS = {
    dark: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M20.7 13.3A9 9 0 0 1 10.7 3.3 9 9 0 1 0 20.7 13.3Z"/></svg>',
    light: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.4 1.4m11.2 11.2L19 19M5 19l1.4-1.4M17.6 6.4L19 5"/></svg>',
  };

  function syncControls() {
    const light = root.dataset.theme === "light";
    document.querySelectorAll("[data-theme-toggle]").forEach(button => {
      button.setAttribute("aria-pressed", String(light));
      button.setAttribute("aria-label", "الوضع الفاتح");
      button.title = light ? "التبديل إلى الوضع الداكن" : "التبديل إلى الوضع الفاتح";
      const label = button.querySelector(".theme-label");
      const icon = button.querySelector(".theme-icon");
      if (label) label.textContent = light ? "فاتح" : "داكن";
      if (icon) icon.innerHTML = ICONS[light ? "light" : "dark"];
    });
  }

  function setTheme(theme, persist = true) {
    if (!THEMES.has(theme)) return false;
    const changed = root.dataset.theme !== theme;
    root.dataset.theme = theme;
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.content = theme === "light" ? "#f3f6fb" : "#0a0e17";
    syncControls();
    if (persist) {
      try { localStorage.setItem(KEY, theme); } catch (_) { /* Storage may be disabled. The switch still works. */ }
    }
    if (changed) window.dispatchEvent(new CustomEvent("radar:themechange", {detail: {theme}}));
    return true;
  }

  let saved = "dark";
  try { const value = localStorage.getItem(KEY); if (THEMES.has(value)) saved = value; } catch (_) {}
  setTheme(saved, false);
  window.RadarTheme = Object.freeze({
    get current() { return root.dataset.theme; },
    setTheme,
    toggle() { return setTheme(root.dataset.theme === "light" ? "dark" : "light"); },
    syncControls,
  });
  document.addEventListener("DOMContentLoaded", syncControls, {once: true});
  document.addEventListener("click", event => {
    const button = event.target.closest?.("[data-theme-toggle]");
    if (button && !button.disabled) window.RadarTheme.toggle();
  });
  // Keep another open dashboard/settings tab aligned without writing back a loop.
  window.addEventListener("storage", event => {
    if (event.key === KEY || event.key === null) setTheme(THEMES.has(event.newValue) ? event.newValue : "dark", false);
  });
})();
