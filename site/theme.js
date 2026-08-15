"use strict";
/* Shared day/night toggle for index.html and developers.html. Runs
   synchronously in <head>, before first paint, so the page never
   flashes the wrong theme. */
(function () {
  const STORAGE_KEY = "neovita-theme";
  const root = document.documentElement;

  function storedTheme() {
    try { return localStorage.getItem(STORAGE_KEY); } catch (_) { return null; }
  }
  function systemTheme() {
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  function apply(theme) {
    // Every hover/focus transition in the stylesheet (card borders, buttons,
    // tabs...) also fires on a plain CSS-variable change, so a theme swap
    // would otherwise animate dozens of elements at once and read as
    // sluggish. Kill transitions for two frames around the swap only.
    root.classList.add("theme-switching");
    root.setAttribute("data-theme", theme);
    requestAnimationFrame(() => requestAnimationFrame(() => root.classList.remove("theme-switching")));
  }

  let theme = storedTheme() || systemTheme();
  apply(theme);

  document.addEventListener("DOMContentLoaded", () => {
    const btn = document.getElementById("theme-toggle");
    if (!btn) return;
    const sync = () => btn.setAttribute("aria-pressed", String(theme === "dark"));
    sync();
    btn.addEventListener("click", () => {
      theme = theme === "dark" ? "light" : "dark";
      apply(theme);
      sync();
      try { localStorage.setItem(STORAGE_KEY, theme); } catch (_) {}
    });
  });
})();
