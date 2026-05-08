/* Raven Tech v2 — minimal client glue.
 * Phase 0 just animates the brain progress bars and pulls /api/v2/version
 * to populate the regime chip + section tag. No bundlers, no frameworks. */

(function () {
  "use strict";

  function $(sel, root) { return (root || document).querySelector(sel); }
  function $$(sel, root) { return Array.from((root || document).querySelectorAll(sel)); }

  // ── Animate brain progress bars ───────────────────────────
  function animateBrainBars() {
    $$(".v2BrainBarFill").forEach(function (el) {
      var pct = parseFloat(el.dataset.progress || "0");
      // Cap at 12 so phase-0 reads as "barely begun" — not 0, not 100.
      var clamped = Math.max(0, Math.min(100, pct * 8));
      requestAnimationFrame(function () {
        el.style.width = clamped + "%";
      });
    });
  }

  // ── Populate regime chip from /api/v2/version ─────────────
  async function loadVersion() {
    var chip = $("#v2RegimeChipText");
    var tag = $("#v2BrainTag");
    try {
      var res = await fetch("/api/v2/version", { credentials: "include" });
      if (!res.ok) throw new Error("HTTP " + res.status);
      var data = await res.json();
      var v = data.version || "";
      var enabled = Object.values(data.foundation || {}).filter(Boolean).length;
      var total = Object.keys(data.foundation || {}).length || 6;
      if (chip) chip.textContent = "v2." + v + " · brain " + enabled + "/" + total;
      if (tag) tag.textContent = "layer 1 · " + enabled + "/" + total + " modules online";
    } catch (err) {
      if (chip) chip.textContent = "v2 · standalone";
      if (tag)  tag.textContent  = "layer 1 · phase 0";
    }
  }

  // ── Streaming caret demo on the hero (subtle, ambient) ────
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
    loadVersion();
    ambientStream();
  });
})();
