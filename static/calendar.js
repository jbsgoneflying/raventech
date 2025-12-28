/* global window, document */

function $(id) { return document.getElementById(id); }

function escapeHtml(s) {
  const t = String(s ?? "");
  return t
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function isoDate(d) {
  const dt = new Date(d);
  if (Number.isNaN(dt.getTime())) return null;
  const y = dt.getFullYear();
  const m = String(dt.getMonth() + 1).padStart(2, "0");
  const day = String(dt.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function parseIsoDate(s) {
  const t = String(s || "").slice(0, 10);
  const dt = new Date(`${t}T00:00:00`);
  return Number.isNaN(dt.getTime()) ? null : dt;
}

function addDays(d, n) {
  const dt = new Date(d);
  dt.setDate(dt.getDate() + Number(n));
  return dt;
}

function startOfWeek(d) {
  // Monday start (US markets planning)
  const dt = new Date(d);
  const dow = (dt.getDay() + 6) % 7; // Mon=0..Sun=6
  return addDays(dt, -dow);
}

function fmtRangeTitle(view, start, end, anchor) {
  const opts = { month: "long", day: "numeric", year: "numeric" };
  const s = start.toLocaleDateString(undefined, opts);
  const e = end.toLocaleDateString(undefined, opts);
  if (view === "day") return s;
  if (view === "week") return `${s} – ${e}`;
  // month: show Month YYYY from anchor (not padded start)
  const ref = anchor || start;
  const m = ref.toLocaleDateString(undefined, { month: "long", year: "numeric" });
  return m;
}

async function fetchJson(url, { timeoutMs = 30000 } = {}) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), Number(timeoutMs));
  try {
    const r = await fetch(url, { signal: ctrl.signal });
    const txt = await r.text();
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}: ${txt.slice(0, 300)}`);
    return JSON.parse(txt);
  } finally {
    clearTimeout(t);
  }
}

const state = {
  view: "month",
  anchor: isoDate(new Date()),
  engine1Only: false,
  layers: { holiday: true, earlyClose: true, fed: true, econ: true, treasury: true, opex: true },
  lastPayload: null,
};

function setStatus(msg, isError = false) {
  const el = $("status");
  if (!el) return;
  el.textContent = msg || "";
  el.classList.toggle("isError", !!isError);
  el.classList.toggle("isOk", !isError && !!msg);
}

function setView(view) {
  state.view = view;
  ["month", "week", "day"].forEach((v) => {
    const b = $(`view${v[0].toUpperCase()}${v.slice(1)}`);
    if (b) b.classList.toggle("isActive", v === view);
  });
}

function openSettings(open) {
  const m = $("settingsModal");
  if (!m) return;
  m.classList.toggle("hidden", !open);
}

function openPopover(open) {
  const p = $("popover");
  if (!p) return;
  p.classList.toggle("hidden", !open);
}

function buildCalendarUrl() {
  const params = new URLSearchParams();
  params.set("view", state.view);
  params.set("anchor", String(state.anchor || ""));
  params.set("tz", "America/New_York");
  params.set("engine1Only", state.engine1Only ? "1" : "0");
  params.set("includeEvents", "1");
  return `/api/calendar?${params.toString()}`;
}

function badgeForEvent(ev) {
  const kind = String(ev?.kind || "").toUpperCase();
  if (kind === "HOLIDAY") return "pill pill--event holiday";
  if (kind === "EARLY_CLOSE") return "pill pill--event earlyClose";
  if (kind === "FED") return "pill pill--event fed";
  if (kind === "ECON") return "pill pill--event econ";
  if (kind === "TREASURY") return "pill pill--event treasury";
  if (kind === "OPEX") return "pill pill--event opex";
  return "pill pill--event neutral";
}

function shouldShowEvent(ev) {
  const kind = String(ev?.kind || "").toUpperCase();
  if (kind === "HOLIDAY") return !!state.layers.holiday;
  if (kind === "EARLY_CLOSE") return !!state.layers.earlyClose;
  if (kind === "FED") return !!state.layers.fed;
  if (kind === "ECON") return !!state.layers.econ;
  if (kind === "TREASURY") return !!state.layers.treasury;
  if (kind === "OPEX") return !!state.layers.opex;
  return true;
}

function render(payload) {
  state.lastPayload = payload;
  const days = Array.isArray(payload?.days) ? payload.days : [];
  const view = String(payload?.view || state.view);
  const start = parseIsoDate(payload?.range?.start) || new Date();
  const end = parseIsoDate(payload?.range?.end) || new Date();
  const anchorDt = parseIsoDate(state.anchor) || new Date();

  const title = $("rangeTitle");
  const sub = $("rangeSub");
  if (title) title.textContent = fmtRangeTitle(view, start, end, anchorDt);
  if (sub) sub.textContent = `asOf ${payload?.meta?.generatedAt || "—"} · ${payload?.meta?.engine1Only ? "Engine‑1 eligible" : "All names"}`;

  const grid = $("calGrid");
  if (!grid) return;
  grid.className = `calGrid calGrid--${view}`;

  // Quick diagnostic: show a friendly hint if we got zero earnings.
  const totalTickers = days.reduce((acc, d) => {
    const e = d?.earnings || {};
    const b = Array.isArray(e?.BMO) ? e.BMO.length : 0;
    const a = Array.isArray(e?.AMC) ? e.AMC.length : 0;
    const u = Array.isArray(e?.UNK) ? e.UNK.length : 0;
    return acc + b + a + u;
  }, 0);
  if (totalTickers === 0) {
    const notes = Array.isArray(payload?.meta?.notes) ? payload.meta.notes.filter(Boolean) : [];
    const c = payload?.meta?.counts || {};
    const cnt = `rowsFetched=${c.earningsRowsFetched ?? "—"} inRange=${c.earningsRowsInRange ?? "—"} tickersSeen=${c.tickersSeen ?? "—"} eligible=${c.tickersEligible ?? "—"}`;
    setStatus(`No earnings names returned for this range. Try toggling off Engine‑1 filter in Settings. Debug: ${cnt}. ${notes.length ? `Notes: ${notes.slice(0, 2).join(" ")}` : ""}`, false);
  } else {
    setStatus("");
  }

  const headerCells = () => {
    const names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
    if (view === "week") return names.slice(0, 5);
    if (view === "month") return names;
    return [];
  };

  const cells = [];
  const headers = headerCells();
  if (headers.length) {
    cells.push(...headers.map((h) => `<div class="calHeadCell">${escapeHtml(h)}</div>`));
  }

  days.forEach((d) => {
    const date = String(d?.date || "");
    const dt = parseIsoDate(date);
    const dow = dt ? dt.toLocaleDateString(undefined, { weekday: "short" }) : "";
    const dayNum = dt ? dt.getDate() : "";
    const isWeekend = dt ? (dt.getDay() === 0 || dt.getDay() === 6) : false;
    const isOutsideMonth = (view === "month" && dt)
      ? (dt.getMonth() !== anchorDt.getMonth() || dt.getFullYear() !== anchorDt.getFullYear())
      : false;

    const evs = (Array.isArray(d?.events) ? d.events : []).filter(shouldShowEvent);
    const earnings = d?.earnings || {};
    const bmo = Array.isArray(earnings?.BMO) ? earnings.BMO : [];
    const amc = Array.isArray(earnings?.AMC) ? earnings.AMC : [];
    const unk = Array.isArray(earnings?.UNK) ? earnings.UNK : [];

    const evShown = (view === "month" && evs.length > 2) ? evs.slice(0, 2) : evs;
    const evMore = (view === "month" && evs.length > 2) ? (evs.length - 2) : 0;
    const evHtml = evs.length
      ? `<div class="calEvents">
          ${evShown.map((ev) => `<div class="${badgeForEvent(ev)}" title="${escapeHtml(ev?.title || "")}">${escapeHtml(ev?.short || ev?.title || "")}</div>`).join("")}
          ${evMore > 0 ? `<div class="pill pill--event neutral" title="${escapeHtml(evs.map(e => e?.title).filter(Boolean).join(" · "))}">+${evMore}</div>` : ""}
        </div>`
      : "";

    const grp = (label, rows, cls) => {
      if (!rows.length) return "";
      const max = view === "month" ? 8 : 14;
      const shown = rows.slice(0, max);
      const rest = rows.length - shown.length;
      return `
        <div class="calEGroup ${cls}">
          <div class="calEGroupHead">${escapeHtml(label)}</div>
          <div class="calTiles">
            ${shown.map((r) => `<button class="calTile" type="button" data-ticker="${escapeHtml(r.ticker)}" data-date="${escapeHtml(date)}" title="${escapeHtml(r.ticker)}">${escapeHtml(r.ticker)}</button>`).join("")}
            ${rest > 0 ? `<div class="calTileMore">+${rest}</div>` : ""}
          </div>
        </div>
      `;
    };

    const cellCls = ["calCell", isWeekend ? "isWeekend" : "", isOutsideMonth ? "isOutside" : ""].filter(Boolean).join(" ");

    cells.push(`
      <div class="${cellCls}" data-date="${escapeHtml(date)}">
        <div class="calCellHead">
          <div class="calCellDate"><span class="calDow">${escapeHtml(dow)}</span><span class="calDayNum">${escapeHtml(dayNum)}</span></div>
        </div>
        ${evHtml}
        <div class="calEarnings">
          ${grp("Before Open", bmo, "bmo")}
          ${grp("After Close", amc, "amc")}
          ${grp("Other", unk, "unk")}
        </div>
      </div>
    `);
  });

  grid.innerHTML = cells.join("");

  // ticker click handler (delegated)
  grid.querySelectorAll(".calTile").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const ticker = String(btn.getAttribute("data-ticker") || "").toUpperCase();
      if (!ticker) return;
      await openTickerPopover(ticker);
    });
  });
}

async function openTickerPopover(ticker) {
  const popTitle = $("popTitle");
  const popBody = $("popBody");
  const popLink = $("popBreachLink");

  if (popTitle) popTitle.textContent = ticker;
  if (popBody) popBody.innerHTML = `<div class="muted">Rank disabled for now. Open Engine 1 for full details.</div>`;
  if (popLink) popLink.href = `/breach?ticker=${encodeURIComponent(ticker)}`;
  openPopover(true);
}

async function refresh() {
  setStatus("Loading…");
  try {
    const payload = await fetchJson(buildCalendarUrl(), { timeoutMs: 60000 });
    render(payload);
    setStatus("");
  } catch (e) {
    setStatus(String(e?.message || e), true);
  }
}

function shiftAnchor(dir) {
  const a = parseIsoDate(state.anchor) || new Date();
  if (state.view === "month") {
    const dt = new Date(a);
    dt.setMonth(dt.getMonth() + dir);
    state.anchor = isoDate(dt);
  } else if (state.view === "week") {
    state.anchor = isoDate(addDays(a, 7 * dir));
  } else {
    state.anchor = isoDate(addDays(a, dir));
  }
}

function init() {
  setView("month");

  $("viewMonth")?.addEventListener("click", () => { setView("month"); refresh(); });
  $("viewWeek")?.addEventListener("click", () => { setView("week"); refresh(); });
  $("viewDay")?.addEventListener("click", () => { setView("day"); refresh(); });

  $("prevBtn")?.addEventListener("click", () => { shiftAnchor(-1); refresh(); });
  $("nextBtn")?.addEventListener("click", () => { shiftAnchor(+1); refresh(); });
  $("todayBtn")?.addEventListener("click", () => { state.anchor = isoDate(new Date()); refresh(); });

  $("settingsBtn")?.addEventListener("click", () => openSettings(true));
  $("settingsCloseBtn")?.addEventListener("click", () => openSettings(false));
  $("settingsCloseX")?.addEventListener("click", () => openSettings(false));
  $("settingsModal")?.addEventListener("click", (ev) => {
    // close when clicking backdrop (not the card)
    if (ev.target && ev.target.id === "settingsModal") openSettings(false);
  });

  $("engine1OnlyToggle")?.addEventListener("change", (ev) => {
    state.engine1Only = !!ev.target.checked;
    refresh();
  });

  const bindLayer = (id, key) => {
    $(id)?.addEventListener("change", (ev) => {
      state.layers[key] = !!ev.target.checked;
      // no API refresh needed; purely client-side filtering
      if (state.lastPayload) render(state.lastPayload);
    });
  };
  bindLayer("evHoliday", "holiday");
  bindLayer("evEarlyClose", "earlyClose");
  bindLayer("evFed", "fed");
  bindLayer("evEcon", "econ");
  bindLayer("evTreasury", "treasury");
  bindLayer("evOpex", "opex");

  $("popClose")?.addEventListener("click", () => openPopover(false));
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") {
      openSettings(false);
      openPopover(false);
    }
  });
  document.addEventListener("click", (ev) => {
    const t = ev.target;
    if (t && t.closest && (t.closest("#popover") || t.closest(".calTile"))) return;
    openPopover(false);
  });

  refresh();
}

document.addEventListener("DOMContentLoaded", init);


