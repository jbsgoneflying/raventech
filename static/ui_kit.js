/* global window, document, navigator */

// Raven UI Kit: shared helpers for the whole app (no dependencies).
// This is a plain script (not an ES module) so it works with existing static pages.

function $(id) {
  return document.getElementById(id);
}

function escapeHtml(s) {
  const t = String(s ?? "");
  return t
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function clamp(x, lo, hi) {
  const n = Number(x);
  if (!Number.isFinite(n)) return lo;
  return Math.max(Number(lo), Math.min(Number(hi), n));
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

async function copyToClipboard(text) {
  const t = String(text ?? "");
  if (!t) return false;
  if (navigator?.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(t);
      return true;
    } catch {
      // fall through
    }
  }
  // Fallback
  try {
    const ta = document.createElement("textarea");
    ta.value = t;
    ta.setAttribute("readonly", "true");
    ta.style.position = "fixed";
    ta.style.left = "-9999px";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    return !!ok;
  } catch {
    return false;
  }
}

function initTooltips() {
  // If a page already defines initTooltips (legacy), don't override it.
  // This kit provides a stable delegated tooltip implementation for `.tipWrap`.
  if (typeof window.initTooltips === "function") {
    try { window.initTooltips(); } catch { /* ignore */ }
    return;
  }

  function closeAllTooltips() {
    document.querySelectorAll(".tipWrap.isOpen").forEach((w) => {
      w.classList.remove("isOpen");
      const b = w.querySelector(".tipBtn");
      if (b) b.setAttribute("aria-expanded", "false");
      const p = w.querySelector(".tipPanel");
      if (p) {
        p.style.display = "none";
        p.style.visibility = "";
      }
    });
  }

  function placeFixedTooltip(wrap) {
    if (!wrap?.classList?.contains("tipWrap--fixed")) return;
    const btn = wrap.querySelector(".tipBtn");
    const panel = wrap.querySelector(".tipPanel");
    if (!btn || !panel) return;

    panel.style.visibility = "hidden";
    panel.style.display = "block";

    const br = btn.getBoundingClientRect();
    const pr = panel.getBoundingClientRect();
    const pad = 12;
    const vw = window.innerWidth;
    const vh = window.innerHeight;

    let top = br.bottom + 10;
    if (top + pr.height + pad > vh) top = br.top - pr.height - 10;
    top = Math.max(pad, Math.min(top, vh - pr.height - pad));

    let left = br.left + br.width / 2 - pr.width / 2;
    left = Math.max(pad, Math.min(left, vw - pr.width - pad));

    panel.style.top = `${Math.round(top)}px`;
    panel.style.left = `${Math.round(left)}px`;
    panel.style.visibility = "visible";
    panel.style.display = wrap.classList.contains("isOpen") ? "block" : "none";
  }

  document.addEventListener("click", (ev) => {
    const t = ev.target;
    if (!(t && t.closest)) return;
    const btn = t.closest(".tipBtn");
    if (!btn) return;
    const wrap = btn.closest(".tipWrap");
    if (!wrap) return;
    ev.preventDefault();
    ev.stopPropagation();
    const isOpen = wrap.classList.contains("isOpen");
    closeAllTooltips();
    if (!isOpen) {
      wrap.classList.add("isOpen");
      btn.setAttribute("aria-expanded", "true");
      placeFixedTooltip(wrap);
    }
  });

  document.addEventListener("click", (ev) => {
    const t = ev.target;
    if (t && t.closest && t.closest(".tipWrap")) return;
    closeAllTooltips();
  });

  window.addEventListener("resize", () => {
    document.querySelectorAll(".tipWrap--fixed.isOpen").forEach(placeFixedTooltip);
  });

  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") closeAllTooltips();
  });

  // expose for other scripts (idempotent)
  window.initTooltips = () => {};
}

// Expose a single global namespace
window.RavenUI = {
  $,
  escapeHtml,
  clamp,
  fetchJson,
  copyToClipboard,
  initTooltips,
};


