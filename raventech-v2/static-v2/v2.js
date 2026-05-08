/* Raven Tech v2 — minimal client glue.
 *
 * Phase 0:
 *   - Animate brain progress bars + sync the pct chip
 *   - Pull /api/v2/version for the regime chip + section tag
 *   - Wire the mobile drawer (hamburger + overlay)
 *   - Pull /api/v2/counterfactual/recent for the live ticker
 *
 * No bundlers, no frameworks. Stays this lean until we genuinely need more. */

(function () {
  "use strict";

  function $(sel, root)  { return (root || document).querySelector(sel); }
  function $$(sel, root) { return Array.from((root || document).querySelectorAll(sel)); }

  // ── Animate brain bars + populate the % chip ──────────────
  function animateBrainBars() {
    $$(".v2BrainBarFill").forEach(function (el) {
      var pct = parseFloat(el.dataset.progress || "0");
      // Phase 0 reads as "barely begun" but always visible (>=14% fill).
      var clamped = Math.max(14, Math.min(100, pct));
      requestAnimationFrame(function () { el.style.width = clamped + "%"; });
    });
    $$(".v2BrainBarPct").forEach(function (el) {
      var n = parseFloat(el.dataset.pctOf || "0");
      el.textContent = (Math.round(n)) + "%";
    });
  }

  // ── Mobile drawer ─────────────────────────────────────────
  function wireDrawer() {
    var btn  = $("#v2MenuToggle");
    var ovl  = $("#v2NavOverlay");
    var side = $("#v2Sidebar");
    if (!btn) return;

    function open() {
      document.body.classList.add("v2NavOpen");
      btn.setAttribute("aria-expanded", "true");
      if (ovl) ovl.setAttribute("aria-hidden", "false");
    }
    function close() {
      document.body.classList.remove("v2NavOpen");
      btn.setAttribute("aria-expanded", "false");
      if (ovl) ovl.setAttribute("aria-hidden", "true");
    }
    function toggle() {
      if (document.body.classList.contains("v2NavOpen")) close(); else open();
    }

    btn.addEventListener("click", toggle);
    if (ovl) ovl.addEventListener("click", close);

    // Tapping a nav link inside the drawer should close it on mobile.
    if (side) {
      side.addEventListener("click", function (ev) {
        var link = ev.target.closest("a");
        if (link) close();
      });
    }

    // Esc closes the drawer.
    document.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape" && document.body.classList.contains("v2NavOpen")) close();
    });

    // If the viewport grows back past mobile, ensure we close the drawer
    // so the persistent sidebar layout doesn't end up with stale state.
    var mq = window.matchMedia("(min-width: 901px)");
    if (mq.addEventListener) mq.addEventListener("change", function (e) { if (e.matches) close(); });
  }

  // ── /api/v2/version → regime chip + section tag ───────────
  async function loadVersion() {
    var chip = $("#v2RegimeChipText");
    var tag  = $("#v2BrainTag");
    try {
      var res = await fetch("/api/v2/version", { credentials: "include" });
      if (!res.ok) throw new Error("HTTP " + res.status);
      var data = await res.json();
      var v = data.version || "";
      var enabled = Object.values(data.foundation || {}).filter(Boolean).length;
      var total   = Object.keys(data.foundation || {}).length || 6;
      if (chip) chip.textContent = "v2." + v + " · brain " + enabled + "/" + total;
      if (tag)  tag.textContent  = "layer 1 · " + enabled + "/" + total + " modules online";
    } catch (err) {
      if (chip) chip.textContent = "v2 · standalone";
      if (tag)  tag.textContent  = "layer 1 · phase 0";
    }
  }

  // ── /api/v2/counterfactual/recent → live ticker ───────────
  async function loadTicker() {
    var inner = $(".v2TickerInner");
    if (!inner) return;
    try {
      var res = await fetch("/api/v2/counterfactual/recent?n=24", { credentials: "include" });
      if (!res.ok) return;
      var data = await res.json();
      var entries = (data && data.entries) || [];
      if (!entries.length) return;

      // Replace the placeholder content with real entries; duplicate the
      // list so the marquee animation loops seamlessly.
      var rendered = entries.map(renderEntry).join("");
      inner.innerHTML = rendered + rendered;
    } catch (err) { /* keep placeholder */ }
  }

  function renderEntry(e) {
    var ts = (e.ts || "").slice(11, 16) || "--:--";
    var engine = (e.engine || "?").toUpperCase();
    var agree = !!e.agree;
    var kindCls = agree ? "v2TickerKind v2TickerKind--em" : "v2TickerKind v2TickerKind--mag";
    var verdict = agree ? "agree" : "DISAGREE";
    var note = e.delta_summary
      ? ' <span class="v2TickerNote">"' + escapeHtml(e.delta_summary).slice(0, 64) + '"</span>'
      : "";
    return ''
      + '<span class="v2TickerItem">'
      +   '<span class="' + kindCls + '">[' + ts + ']</span> '
      +   '<span class="v2TickerEng">' + escapeHtml(engine) + '</span> '
      +   verdict
      +   note
      + '</span>';
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  // ── Streaming caret on the hero (subtle, ambient) ─────────
  function ambientStream() {
    var title = $(".v2HeroTitle em");
    if (!title || title.dataset.streamed === "1") return;
    title.dataset.streamed = "1";
    var caret = document.createElement("span");
    caret.className = "v2StreamCaret";
    caret.setAttribute("aria-hidden", "true");
    title.parentNode.appendChild(caret);
  }

  document.addEventListener("DOMContentLoaded", function () {
    animateBrainBars();
    wireDrawer();
    loadVersion();
    loadTicker();
    ambientStream();
  });
})();
