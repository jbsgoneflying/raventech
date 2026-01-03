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

function initInfoTips() {
  // Click-to-open popover for legacy `.info` icons that only had a title attribute.
  // Uses the same `.taTipPop` styling as the TA panel so behavior is consistent on touch devices.
  if (window.__RavenInfoTipsInit) return;
  window.__RavenInfoTipsInit = true;

  let tipEl = null;
  let lastAnchor = null;

  const closeTip = () => {
    if (tipEl && tipEl.parentNode) tipEl.parentNode.removeChild(tipEl);
    tipEl = null;
    lastAnchor = null;
  };

  const openTip = (anchor, text) => {
    closeTip();
    const msg = String(text || "").trim();
    if (!msg) return;
    lastAnchor = anchor;
    tipEl = document.createElement("div");
    tipEl.className = "taTipPop taGlass";
    tipEl.setAttribute("role", "tooltip");
    const body = document.createElement("div");
    body.className = "taTipPopBody";
    body.style.whiteSpace = "pre-wrap";
    body.textContent = msg;
    tipEl.appendChild(body);
    document.body.appendChild(tipEl);

    const r = anchor.getBoundingClientRect();
    const pad = 10;
    const left = Math.max(pad, Math.min(window.innerWidth - pad, r.left + r.width / 2));
    const top = Math.max(pad, r.bottom + 8);
    tipEl.style.left = `${left}px`;
    tipEl.style.top = `${top}px`;
    tipEl.style.transform = "translateX(-50%)";
  };

  document.addEventListener("click", (ev) => {
    const t = ev.target;
    if (!(t && t.closest)) return;
    if (t.closest(".taTipPop")) return;
    const info = t.closest(".info");
    if (!info) return;
    const title = info.getAttribute("title") || info.getAttribute("data-tip") || "";
    if (!title) return;
    ev.preventDefault();
    ev.stopPropagation();
    if (lastAnchor === info && tipEl) closeTip();
    else openTip(info, title);
  });

  document.addEventListener("click", (ev) => {
    const t = ev.target;
    if (t && t.closest && (t.closest(".taTipPop") || t.closest(".info"))) return;
    closeTip();
  });

  document.addEventListener("scroll", closeTip, { passive: true });
  window.addEventListener("resize", closeTip);
  document.addEventListener("keydown", (ev) => { if (ev.key === "Escape") closeTip(); });
}

// Expose a single global namespace
window.RavenUI = {
  $,
  escapeHtml,
  clamp,
  fetchJson,
  copyToClipboard,
  initTooltips,
  initInfoTips,
  installGlobalApiLoading,
};

// ---------------------------------------------------------------------------
// Global API loading bar (desk usability): shows a subtle top progress bar for
// in-flight `/api/*` calls. Centralized via fetch wrapper so all pages benefit.
// ---------------------------------------------------------------------------

function installGlobalApiLoading({
  apiPrefix = "/api/",
  showDelayMs = 150,
} = {}) {
  // Idempotent install.
  if (window.__RavenApiLoadingInstalled) return;
  window.__RavenApiLoadingInstalled = true;

  const state = {
    inflight: 0,
    showTimer: null,
    lastClickEl: null,
  };

  const ensureBar = () => {
    if (document.getElementById("ravenTopLoader")) return;
    if (!document.body) return;
    const bar = document.createElement("div");
    bar.id = "ravenTopLoader";
    bar.setAttribute("aria-hidden", "true");
    document.body.appendChild(bar);
  };

  const isApiUrl = (input) => {
    try {
      if (!input) return false;
      // input can be Request | string | URL
      const raw = (typeof input === "string") ? input : (input?.url || String(input));
      const s = String(raw || "");
      if (s.startsWith(apiPrefix)) return true;
      if (s.startsWith("http://") || s.startsWith("https://")) {
        const u = new URL(s, window.location.href);
        // Only consider same-origin API calls.
        if (u.origin !== window.location.origin) return false;
        return u.pathname.startsWith(apiPrefix);
      }
      return false;
    } catch {
      return false;
    }
  };

  const setTempDisabled = (el, on) => {
    if (!el || !el.classList) return;
    const tag = String(el.tagName || "").toUpperCase();
    if (tag === "BUTTON") {
      if (on) {
        if (!el.hasAttribute("data-raven-prev-disabled")) {
          el.setAttribute("data-raven-prev-disabled", el.disabled ? "1" : "0");
        }
        el.disabled = true;
      } else {
        const prev = el.getAttribute("data-raven-prev-disabled");
        if (prev !== null) {
          el.disabled = prev === "1";
          el.removeAttribute("data-raven-prev-disabled");
        } else {
          // If we didn't record, leave as-is.
        }
      }
      return;
    }
    if (tag === "A") {
      if (on) {
        el.classList.add("ravenTempDisabled");
        el.setAttribute("aria-disabled", "true");
      } else {
        el.classList.remove("ravenTempDisabled");
        el.removeAttribute("aria-disabled");
      }
    }
  };

  const show = () => {
    ensureBar();
    document.documentElement.classList.add("isApiLoading");
    setTempDisabled(state.lastClickEl, true);
  };

  const hide = () => {
    document.documentElement.classList.remove("isApiLoading");
    setTempDisabled(state.lastClickEl, false);
  };

  const inc = () => {
    state.inflight += 1;
    if (state.inflight === 1) {
      // Avoid flicker for fast calls.
      if (state.showTimer) clearTimeout(state.showTimer);
      state.showTimer = setTimeout(() => {
        state.showTimer = null;
        if (state.inflight > 0) show();
      }, Math.max(0, Number(showDelayMs) || 0));
    }
  };

  const dec = () => {
    state.inflight = Math.max(0, state.inflight - 1);
    if (state.inflight === 0) {
      if (state.showTimer) {
        clearTimeout(state.showTimer);
        state.showTimer = null;
      }
      hide();
    }
  };

  // Track the most recently clicked button/link so we can temporarily disable it
  // while API calls are running (prevents spam-clicking).
  document.addEventListener("click", (ev) => {
    const t = ev.target;
    if (!(t && t.closest)) return;
    const el = t.closest("button, a");
    if (!el) return;
    state.lastClickEl = el;
  }, { capture: true });

  // Wrap fetch once and count `/api/*` in-flight calls.
  if (!window.__RavenFetchWrapped && typeof window.fetch === "function") {
    window.__RavenFetchWrapped = true;
    const origFetch = window.fetch.bind(window);
    window.fetch = async (input, init) => {
      const track = isApiUrl(input);
      if (track) inc();
      try {
        return await origFetch(input, init);
      } finally {
        if (track) dec();
      }
    };
  }

  // Ensure bar exists once body is available.
  if (document.body) ensureBar();
  else document.addEventListener("DOMContentLoaded", ensureBar, { once: true });
}

// Auto-install so all pages get consistent loading UX.
try { installGlobalApiLoading(); } catch { /* ignore */ }


