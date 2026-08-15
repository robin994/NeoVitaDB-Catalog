"use strict";

const FEEDS = {
  vita: "https://robin994.github.io/NeoVitaDB-Catalog/vita.json",
  psp: "https://robin994.github.io/NeoVitaDB-Catalog/psp.json",
};
const CAT_MAP = { 1: "Original Game", 2: "Game Port", 4: "Utility", 5: "Emulator" };

// Each category is tied to a real PlayStation face button — used as a small
// glyph + accent color instead of an arbitrary color swatch.
const CAT_GLYPH = {
  "Original Game": { shape: "triangle", color: "var(--triangle)" },
  "Game Port": { shape: "square", color: "var(--square)" },
  "Utility": { shape: "cross", color: "var(--cross)" },
  "Emulator": { shape: "circle", color: "var(--circle)" },
};

const FILTER_STORAGE_KEY = "neovita-filter-preferences";
const savedFilters = (() => {
  try {
    const parsed = JSON.parse(localStorage.getItem(FILTER_STORAGE_KEY) || "{}");
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch (_) { return {}; }
})();
const state = {
  all: [], platform: "all", category: "all", sort: "added", query: "", page: 1,
  pageSize: savedFilters.pageSize === "all" ? "all" : (parseInt(savedFilters.pageSize, 10) || 48),
  trustedOnly: savedFilters.trustedOnly === true,
  includeAiAssisted: savedFilters.includeAiAssisted !== false,
  includeVibecoded: savedFilters.includeVibecoded !== false,
};
const $ = (id) => document.getElementById(id);
const grid = $("grid");
const loadingEl = $("loading");
const errorEl = $("error");
const emptyEl = $("empty");
const resultCount = $("result-count");
const resetBtn = $("reset");
const trustedOnlyInput = $("trusted-only");
const includeAiAssistedInput = $("include-ai-assisted");
const includeVibecodedInput = $("include-vibecoded");
const pageSizeInput = $("page-size");
const paginationEl = $("pagination");
const pagePrevBtn = $("page-prev");
const pageNextBtn = $("page-next");
const pageIndicator = $("page-indicator");

function isEnabled(value) { return value === true || value === 1 || value === "1" || value === "true"; }
function saveFilterPreferences() {
  try { localStorage.setItem(FILTER_STORAGE_KEY, JSON.stringify({ trustedOnly: state.trustedOnly, includeAiAssisted: state.includeAiAssisted, includeVibecoded: state.includeVibecoded, pageSize: state.pageSize })); } catch (_) {}
}
function syncFilterInputs() {
  trustedOnlyInput.checked = state.trustedOnly;
  includeAiAssistedInput.checked = state.includeAiAssisted;
  includeVibecodedInput.checked = state.includeVibecoded;
  pageSizeInput.value = String(state.pageSize);
}
syncFilterInputs();

/* ---------------- boot sequence ---------------- */
(function boot() {
  const el = $("boot");
  if (!el) return;
  const dismiss = () => { el.style.animation = "boot-out 0.35s ease forwards"; };
  window.addEventListener("keydown", dismiss, { once: true });
  window.addEventListener("pointerdown", dismiss, { once: true });
  setTimeout(dismiss, 2200);
})();

/* ---------------- live clock, XMB-bar style ---------------- */
(function clock() {
  const el = $("clock");
  if (!el) return;
  const tick = () => {
    const now = new Date();
    const hh = String(now.getHours()).padStart(2, "0");
    const mm = String(now.getMinutes()).padStart(2, "0");
    el.textContent = `${hh}:${mm}`;
  };
  tick();
  setInterval(tick, 15000);
})();

/* ---------------- glyph helpers ---------------- */
function glyphSvg(category) {
  const spec = CAT_GLYPH[category];
  if (!spec) return "";
  const shapes = {
    triangle: `<path class="g-triangle" d="M12 3 L21 19 L3 19 Z" stroke-width="2.6" />`,
    square: `<rect class="g-square" x="4" y="4" width="16" height="16" rx="2" stroke-width="2.6" />`,
    cross: `<path class="g-cross" d="M6 6 L18 18 M18 6 L6 18" stroke-width="2.6" stroke-linecap="round" />`,
    circle: `<circle class="g-circle" cx="12" cy="12" r="8" stroke-width="2.6" />`,
  };
  return `<svg class="glyph-dot" viewBox="0 0 24 24" fill="none" aria-hidden="true">${shapes[spec.shape]}</svg>`;
}

function categoryOf(item) {
  let type = parseInt(item.type, 10);
  if (item.platform === "psp") type -= 10;
  return CAT_MAP[type] || "Other";
}

function iconUrl(item) {
  const iconDirectory = item.platform === "vita" ? "icons" : "icons_psp";
  return `${iconDirectory}/${item.icon}`;
}
function fmtSize(bytes) {
  const number = parseInt(bytes, 10);
  if (!number || Number.isNaN(number)) return "—";
  if (number >= 1024 * 1024) return `${(number / (1024 * 1024)).toFixed(1)} MB`;
  if (number >= 1024) return `${(number / 1024).toFixed(0)} KB`;
  return `${number} B`;
}
function fmtNum(value) { return (parseInt(value, 10) || 0).toLocaleString("en-US"); }
function fmtDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" });
}
function escapeHtml(value) {
  return String(value || "").replace(/[&<>"']/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[character]));
}
function richText(value) {
  if (!value) return "";
  const html = marked.parse(value, { breaks: true, gfm: true });
  return DOMPurify.sanitize(html);
}

/* ---------------- data ---------------- */
async function loadData() {
  loadingEl.hidden = false;
  errorEl.hidden = true;
  emptyEl.hidden = true;
  grid.innerHTML = "";
  try {
    const [vita, psp] = await Promise.all([
      fetch(FEEDS.vita).then((response) => { if (!response.ok) throw new Error(`vita ${response.status}`); return response.json(); }),
      fetch(FEEDS.psp).then((response) => { if (!response.ok) throw new Error(`psp ${response.status}`); return response.json(); }),
    ]);
    const tag = (items, platform) => items.map((item) => ({ ...item, platform, category: null }));
    state.all = [...tag(vita, "vita"), ...tag(psp, "psp")];
    state.all.forEach((item) => { item.category = categoryOf(item); });
    loadingEl.hidden = true;
    updateStats();
    render();
    renderChart();
  } catch (error) {
    loadingEl.hidden = true;
    errorEl.hidden = false;
    $("error-msg").textContent = error.message || "Network error";
  }
}

function updateStats() {
  const vita = state.all.filter((item) => item.platform === "vita").length;
  const psp = state.all.filter((item) => item.platform === "psp").length;
  $("stat-total").textContent = fmtNum(state.all.length);
  $("stat-vita").textContent = fmtNum(vita);
  $("stat-psp").textContent = fmtNum(psp);
}

/* ---------------- top-10-per-category charts ---------------- */
const CHART_SIZE = 10;
let activeChartCategory = "Original Game";
const chartListEl = $("chart-list");

function chartItemHtml(item, rank) {
  const initial = escapeHtml((item.name || "?").trim().charAt(0).toUpperCase());
  const accent = (CAT_GLYPH[item.category] || {}).color || "var(--muted)";
  return `<button type="button" class="chart-item" data-testid="chart-item-${rank}">
    <span class="chart-rank">${rank}</span>
    <span class="card-icon chart-icon" style="--accent:${accent}">
      <img src="${iconUrl(item)}" alt="" loading="lazy" onerror="this.parentElement.classList.add('fallback')" />
      <span class="icon-fallback" aria-hidden="true">${initial}</span>
    </span>
    <span class="chart-info">
      <span class="chart-name">${escapeHtml(item.name)}</span>
      <span class="chart-meta">${escapeHtml(item.author) || "Unknown"} · ${fmtNum(item.downloads)} downloads</span>
    </span>
  </button>`;
}

function renderChart() {
  const items = state.all
    .filter((item) => item.category === activeChartCategory)
    .slice()
    .sort((a, b) => (parseInt(b.downloads, 10) || 0) - (parseInt(a.downloads, 10) || 0))
    .slice(0, CHART_SIZE);
  chartListEl.innerHTML = items.map((item, i) => chartItemHtml(item, i + 1)).join("");
  Array.from(chartListEl.children).forEach((element, i) => {
    element.addEventListener("click", () => openModal(items[i]));
  });
}
function getFiltered() {
  let list = state.all.slice();
  if (state.platform !== "all") list = list.filter((item) => item.platform === state.platform);
  if (state.category !== "all") list = list.filter((item) => item.category === state.category);
  if (state.trustedOnly) list = list.filter((item) => isEnabled(item.trusted));
  if (!state.includeAiAssisted) list = list.filter((item) => !isEnabled(item.ai_assisted));
  if (!state.includeVibecoded) list = list.filter((item) => !isEnabled(item.ai));
  if (state.query) {
    const query = state.query.toLowerCase();
    list = list.filter((item) => (item.name || "").toLowerCase().includes(query) || (item.author || "").toLowerCase().includes(query));
  }
  if (state.sort === "added") list.sort((a, b) => new Date(b.added) - new Date(a.added));
  else if (state.sort === "updated") list.sort((a, b) => new Date(b.date) - new Date(a.date));
  else if (state.sort === "oldest") list.sort((a, b) => new Date(a.date) - new Date(b.date));
  else if (state.sort === "downloads") list.sort((a, b) => (parseInt(b.downloads, 10) || 0) - (parseInt(a.downloads, 10) || 0));
  else if (state.sort === "downloads_asc") list.sort((a, b) => (parseInt(a.downloads, 10) || 0) - (parseInt(b.downloads, 10) || 0));
  else if (state.sort === "name") list.sort((a, b) => (a.name || "").localeCompare(b.name || ""));
  else if (state.sort === "name_desc") list.sort((a, b) => (b.name || "").localeCompare(a.name || ""));
  else if (state.sort === "size_asc") list.sort((a, b) => (parseInt(a.size, 10) || 0) - (parseInt(b.size, 10) || 0));
  else if (state.sort === "size_desc") list.sort((a, b) => (parseInt(b.size, 10) || 0) - (parseInt(a.size, 10) || 0));
  else if (state.sort === "score_desc") list.sort((a, b) => (parseInt(b.likes, 10) || 0) - (parseInt(a.likes, 10) || 0));
  else if (state.sort === "score_asc") list.sort((a, b) => (parseInt(a.likes, 10) || 0) - (parseInt(b.likes, 10) || 0));
  return list;
}

/* ---------------- rendering ---------------- */
function cardHtml(item, index) {
  const initial = escapeHtml((item.name || "?").trim().charAt(0).toUpperCase());
  const accent = (CAT_GLYPH[item.category] || {}).color || "var(--muted)";
  const platformBadge = item.platform === "vita" ? '<span class="badge badge-vita">PS Vita</span>' : '<span class="badge badge-psp">PSP</span>';
  const trusted = isEnabled(item.trusted) ? '<span class="badge badge-trusted">Trusted</span>' : "";
  const vibecoded = isEnabled(item.ai) ? '<span class="badge badge-ai">Vibecoded</span>' : "";
  const aiAssisted = isEnabled(item.ai_assisted) ? '<span class="badge badge-ai-assisted">AI-assisted</span>' : "";
  const direct = item.direct && item.direct !== "0" ? '<span class="badge badge-direct">Direct</span>' : "";
  const description = escapeHtml(item.long_description || item.description || "No description available.");
  return `<article class="card" data-plat="${item.platform}" tabindex="0" style="animation-delay:${Math.min(index, 24) * 20}ms" data-testid="card-${item.platform}-${escapeHtml(item.id)}">
    <div class="card-top">
      <span class="card-icon" style="--accent:${accent}">
        <img src="${iconUrl(item)}" alt="" loading="lazy" onerror="this.parentElement.classList.add('fallback')" />
        <span class="icon-fallback" aria-hidden="true">${initial}</span>
      </span>
      <div class="card-head">
        <p class="card-name">${escapeHtml(item.name)}</p>
        <p class="card-author">${escapeHtml(item.author) || "Unknown"}</p>
      </div>
    </div>
    <div class="badges">${platformBadge}<span class="badge badge-cat">${glyphSvg(item.category)}${escapeHtml(item.category)}</span>${trusted}${vibecoded}${aiAssisted}${direct}</div>
    <p class="card-description">${description}</p>
    <div class="card-meta">
      <div class="counters">
        <span class="dl"><svg viewBox="0 0 24 24" fill="none"><path d="M12 3v12m0 0l-4-4m4 4l4-4M5 21h14" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>${fmtNum(item.downloads)} <span class="info-tooltip" title="Downloads are fetched directly from GitHub releases and may differ from legacy historical data." style="cursor:help;opacity:0.7;margin-left:4px;font-size:10px;">ⓘ</span></span>
        <span class="dl likes"><svg viewBox="0 0 24 24" fill="none"><path d="M12 17.3l-5.4 3 1.4-6-4.6-4 6-.5L12 4l2.6 5.8 6 .5-4.6 4 1.4 6z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>${fmtNum(item.likes)}</span>
      </div>
      <span class="ver">${escapeHtml(item.version) || "—"}</span>
    </div>
  </article>`;
}
// Rendered a page at a time instead of all ~3,000+ cards at once. Page
// size is user-selectable (24/48/96/all) and persisted alongside the
// other filter preferences.
let currentList = [];

function totalPages() {
  if (state.pageSize === "all") return 1;
  return Math.max(1, Math.ceil(currentList.length / state.pageSize));
}

function renderPage() {
  const pages = totalPages();
  state.page = Math.min(Math.max(1, state.page), pages);
  const items = state.pageSize === "all" ? currentList : currentList.slice((state.page - 1) * state.pageSize, state.page * state.pageSize);
  grid.innerHTML = items.map(cardHtml).join("");
  Array.from(grid.children).forEach((element, i) => {
    const item = items[i];
    element.addEventListener("click", () => openModal(item));
    element.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); openModal(item); } });
  });
  paginationEl.hidden = pages <= 1;
  pageIndicator.textContent = `Page ${state.page} / ${pages}`;
  pagePrevBtn.disabled = state.page <= 1;
  pageNextBtn.disabled = state.page >= pages;
}

function goToPage(page) {
  state.page = page;
  renderPage();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function render() {
  currentList = getFiltered();
  state.page = 1;
  const active = state.platform !== "all" || state.category !== "all" || state.query || state.trustedOnly || !state.includeAiAssisted || !state.includeVibecoded;
  resetBtn.hidden = !active;
  resultCount.innerHTML = `<b>${fmtNum(currentList.length)}</b> ${currentList.length === 1 ? "result" : "results"}`;
  if (!currentList.length) { grid.innerHTML = ""; emptyEl.hidden = false; paginationEl.hidden = true; return; }
  emptyEl.hidden = true;
  renderPage();
}

/* ---------------- modal ---------------- */
const modal = $("modal");
const modalBody = $("modal-body");
const modalPrompts = $("modal-prompts");
function spec(label, value, mono) { return `<div class="spec"><div class="spec-l">${label}</div><div class="spec-v ${mono ? "mono" : ""}">${value}</div></div>`; }

const ICONS = {
  cross: `<svg viewBox="0 0 24 24" fill="none"><path d="M6 6 L18 18 M18 6 L6 18" class="g-cross" stroke-width="2.6" stroke-linecap="round"/></svg>`,
  circle: `<svg viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="8" class="g-circle" stroke-width="2.6"/></svg>`,
  triangle: `<svg viewBox="0 0 24 24" fill="none"><path d="M12 3 L21 19 L3 19 Z" class="g-triangle" stroke-width="2.6"/></svg>`,
  square: `<svg viewBox="0 0 24 24" fill="none"><rect x="4" y="4" width="16" height="16" rx="2" class="g-square" stroke-width="2.6"/></svg>`,
};

function openModal(item) {
  const initial = escapeHtml((item.name || "?").trim().charAt(0).toUpperCase());
  const platformBadge = item.platform === "vita" ? '<span class="badge badge-vita">PS Vita</span>' : '<span class="badge badge-psp">PSP</span>';
  const trusted = isEnabled(item.trusted) ? '<span class="badge badge-trusted">Trusted</span>' : "";
  const vibecoded = isEnabled(item.ai) ? '<span class="badge badge-ai">Vibecoded</span>' : "";
  const aiAssisted = isEnabled(item.ai_assisted) ? '<span class="badge badge-ai-assisted">AI-assisted</span>' : "";
  const direct = item.direct && item.direct !== "0" ? '<span class="badge badge-direct">Direct Download</span>' : "";
  const specs = [
    spec("Version", escapeHtml(item.version) || "—"),
    spec("Downloads", `${fmtNum(item.downloads)} <span class="info-tooltip" title="Downloads are fetched directly from GitHub releases and may differ from legacy historical data." style="cursor:help;opacity:0.7;margin-left:4px;font-size:10px;">ⓘ</span>`),
    spec("Likes", fmtNum(item.likes)),
    spec("Released", fmtDate(item.date)),
    spec("Size", fmtSize(item.size)),
    item.titleid ? spec("Title ID", escapeHtml(item.titleid), true) : "",
    item.folder ? spec("Folder", escapeHtml(item.folder), true) : "",
    item.hash ? spec("MD5", escapeHtml(item.hash), true) : "",
    item.requirements ? spec("Requirements", escapeHtml(item.requirements)) : "",
  ].join("");
  const changelog = item.changelog ? `<div class="m-section-title">Changelog</div><div class="m-text m-changelog">${richText(item.changelog)}</div>` : "";

  const accent = (CAT_GLYPH[item.category] || {}).color || "var(--muted)";
  modalBody.innerHTML = `<div class="m-head">
      <span class="m-icon card-icon" style="--accent:${accent}">
        <img src="${iconUrl(item)}" alt="" onerror="this.parentElement.classList.add('fallback')" />
        <span class="icon-fallback" aria-hidden="true">${initial}</span>
      </span>
      <div><h2 class="m-title">${escapeHtml(item.name)}</h2><div class="m-author">by ${escapeHtml(item.author) || "Unknown"}</div></div>
    </div>
    <div class="m-badges">${platformBadge}<span class="badge badge-cat">${glyphSvg(item.category)}${escapeHtml(item.category)}</span>${trusted}${vibecoded}${aiAssisted}${direct}</div>
    <div class="m-specs">${specs}</div>
    ${item.long_description ? `<div class="m-section-title">About</div><div class="m-text">${richText(item.long_description)}</div>` : ""}
    ${changelog}`;

  const prompts = [];
  if (item.url) prompts.push(`<a class="prompt prompt-primary" href="${escapeHtml(item.url)}" target="_blank" rel="noopener" data-testid="modal-download">${ICONS.cross}Download</a>`);
  if (item.source) prompts.push(`<a class="prompt" href="${escapeHtml(item.source)}" target="_blank" rel="noopener">${ICONS.triangle}Source</a>`);
  if (item.release_page) prompts.push(`<a class="prompt" href="${escapeHtml(item.release_page)}" target="_blank" rel="noopener">${ICONS.square}Release notes</a>`);
  prompts.push(`<button class="prompt" id="modal-prompt-close">${ICONS.circle}Close</button>`);
  modalPrompts.innerHTML = prompts.join("");
  $("modal-prompt-close")?.addEventListener("click", closeModal);

  modal.hidden = false;
  document.body.style.overflow = "hidden";
}
function closeModal() { modal.hidden = true; document.body.style.overflow = ""; }

/* ---------------- events ---------------- */
let searchTimer;
$("search").addEventListener("input", (event) => { clearTimeout(searchTimer); searchTimer = setTimeout(() => { state.query = event.target.value.trim(); render(); }, 160); });
document.querySelectorAll(".xmb-tab").forEach((button) => button.addEventListener("click", () => {
  document.querySelectorAll(".xmb-tab").forEach((item) => { item.classList.remove("active"); item.setAttribute("aria-selected", "false"); });
  button.classList.add("active");
  button.setAttribute("aria-selected", "true");
  state.platform = button.dataset.platform;
  render();
}));
document.querySelectorAll(".chart-tab").forEach((button) => button.addEventListener("click", () => {
  document.querySelectorAll(".chart-tab").forEach((item) => { item.classList.remove("active"); item.setAttribute("aria-selected", "false"); });
  button.classList.add("active");
  button.setAttribute("aria-selected", "true");
  activeChartCategory = button.dataset.cat;
  renderChart();
}));
$("category").addEventListener("change", (event) => { state.category = event.target.value; render(); });
$("sort").addEventListener("change", (event) => { state.sort = event.target.value; render(); });
trustedOnlyInput.addEventListener("change", (event) => { state.trustedOnly = event.target.checked; saveFilterPreferences(); render(); });
includeAiAssistedInput.addEventListener("change", (event) => { state.includeAiAssisted = event.target.checked; saveFilterPreferences(); render(); });
includeVibecodedInput.addEventListener("change", (event) => { state.includeVibecoded = event.target.checked; saveFilterPreferences(); render(); });
pageSizeInput.addEventListener("change", (event) => {
  state.pageSize = event.target.value === "all" ? "all" : parseInt(event.target.value, 10);
  saveFilterPreferences();
  render();
});
pagePrevBtn.addEventListener("click", () => { if (state.page > 1) goToPage(state.page - 1); });
pageNextBtn.addEventListener("click", () => { if (state.page < totalPages()) goToPage(state.page + 1); });
resetBtn.addEventListener("click", () => {
  state.platform = "all"; state.category = "all"; state.query = ""; state.trustedOnly = false; state.includeAiAssisted = true; state.includeVibecoded = true;
  $("search").value = ""; $("category").value = "all";
  syncFilterInputs();
  saveFilterPreferences();
  document.querySelectorAll(".xmb-tab").forEach((button) => {
    const isAll = button.dataset.platform === "all";
    button.classList.toggle("active", isAll);
    button.setAttribute("aria-selected", String(isAll));
  });
  render();
});
$("retry").addEventListener("click", loadData);
$("modal-close").addEventListener("click", closeModal);
document.querySelector(".modal-backdrop").addEventListener("click", closeModal);
document.addEventListener("keydown", (event) => { if (event.key === "Escape" && !modal.hidden) closeModal(); });

loadData();
