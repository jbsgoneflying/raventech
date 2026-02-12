/* ── Raven-Tech 2.0 – Research Lab JS ────────────────────────────────── */
(function () {
  "use strict";

  const $ = (sel) => document.querySelector(sel);

  function badgeClass(status) {
    const s = (status || "").toLowerCase();
    if (s === "proposed") return "rlBadge--proposed";
    if (s === "backtesting") return "rlBadge--backtesting";
    if (s === "rejected") return "rlBadge--rejected";
    if (s === "promoted_to_beta" || s === "beta") return "rlBadge--beta";
    if (s === "iterating") return "rlBadge--iterating";
    return "rlBadge--proposed";
  }

  async function loadFeatures() {
    try {
      const res = await fetch("/api/research-lab/features");
      if (!res.ok) return;
      const data = await res.json();
      const features = data.features || [];

      const list = $("#featureList");
      const empty = $("#featureEmpty");

      if (features.length === 0) {
        empty.style.display = "block";
        return;
      }
      empty.style.display = "none";
      list.innerHTML = "";

      features.forEach((f) => {
        const el = document.createElement("div");
        el.className = "rlFeature";
        el.innerHTML = `
          <div style="display:flex; align-items:center; gap:8px;">
            <span class="rlFeatureName">${f.name || "Unnamed"}</span>
            <span class="rlBadge ${badgeClass(f.status)}">${(f.status || "proposed").replace(/_/g, " ")}</span>
          </div>
          <div class="rlFeatureFormula">${f.formula || "—"}</div>
          <div class="rlFeatureHyp">${f.hypothesis || "—"}</div>
        `;
        list.appendChild(el);
      });
    } catch (e) {
      console.error("Load features failed:", e);
    }
  }

  window.suggestFeatures = async function () {
    const btn = $("#suggestBtn");
    const status = $("#suggestStatus");
    btn.disabled = true;
    status.textContent = "Requesting LLM suggestions...";

    try {
      const res = await fetch("/api/research-lab/suggest", { method: "POST" });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        status.textContent = err.detail || "Request failed";
        btn.disabled = false;
        return;
      }
      const data = await res.json();
      status.textContent = `${data.count || 0} feature(s) suggested`;
      await loadFeatures();
    } catch (e) {
      status.textContent = "Network error";
      console.error(e);
    }
    btn.disabled = false;
  };

  // Init
  loadFeatures();
})();
