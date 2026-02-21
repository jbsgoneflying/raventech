/* ── Engine 8: Post-Event Trade Extension ───────────────────────────────
   Frontend controller for the post-event evaluator page.
   ──────────────────────────────────────────────────────────────────────── */
(function () {
  "use strict";

  var form       = document.getElementById("e8Form");
  var runBtn     = document.getElementById("runBtn");
  var statusEl   = document.getElementById("status");
  var resultsEl  = document.getElementById("results");

  function qs(id) { return document.getElementById(id); }
  function fmt(v, d) { return v == null ? "—" : Number(v).toFixed(d == null ? 2 : d); }
  function pct(v) { return v == null ? "—" : (Number(v) * 100).toFixed(1) + "%"; }

  /* ── Render results ────────────────────────────────────────────────── */
  function render(data) {
    /* Decision */
    var decision = (data.decision || "PASS").toUpperCase();
    var badge = qs("decisionBadge");
    badge.textContent = decision;
    badge.className = "decisionBadge " + decision.toLowerCase();

    qs("decisionDirection").textContent = data.direction ? (data.direction === "long" ? "Long" : "Short") : "—";
    qs("decisionConfidence").textContent = data.confidence != null ? "Confidence: " + Math.round(data.confidence) + " / 100" : "";
    qs("decisionRationale").textContent = data.rationale || data.summary || "";

    /* Snapshot */
    var snap = data.snapshot || data;
    qs("snapActualMove").textContent = snap.actualMove != null ? fmt(snap.actualMove) + "%" : snap.actual_move != null ? fmt(snap.actual_move) + "%" : "—";
    qs("snapEmMultiple").textContent = fmt(snap.emMultiple || snap.em_multiple) + "x";
    qs("snapAtrMultiple").textContent = fmt(snap.atrMultiple || snap.atr_multiple) + "x";
    qs("snapGapStructure").textContent = snap.gapStructure || snap.gap_structure || "—";
    qs("snapIvCrush").textContent = snap.ivCrush != null ? fmt(snap.ivCrush) + "%" : snap.iv_crush != null ? fmt(snap.iv_crush) + "%" : "—";
    qs("snapSentiment").textContent = snap.sentiment || snap.eventSentiment || snap.event_sentiment || "—";

    /* Displacement */
    var disp = data.displacement || data.classification || data;
    qs("displaceMagnitude").textContent = disp.magnitude || "—";
    qs("displaceStructure").textContent = disp.structure || disp.gapStructure || disp.gap_structure || "—";
    qs("displaceContext").textContent = disp.contextAlignment || disp.context_alignment || disp.context || "—";

    /* Historical */
    var hist = data.historical || data.history || {};
    qs("histContinuation").textContent = hist.continuationProb != null ? pct(hist.continuationProb) : hist.continuation_prob != null ? pct(hist.continuation_prob) : "—";
    qs("histReversion").textContent = hist.reversionProb != null ? pct(hist.reversionProb) : hist.reversion_prob != null ? pct(hist.reversion_prob) : "—";
    qs("histMagnitude").textContent = hist.avgMagnitude != null ? fmt(hist.avgMagnitude) + "%" : hist.avg_magnitude != null ? fmt(hist.avg_magnitude) + "%" : "—";
    qs("histSample").textContent = hist.sampleSize || hist.sample_size || "—";

    /* Trade profile */
    if (decision !== "PASS") {
      qs("tradeDirection").textContent = data.direction ? (data.direction === "long" ? "Long" : "Short") : "—";
      qs("tradeRiskUnits").textContent = fmt(data.riskUnits || data.risk_units, 1);
      qs("tradeHolding").textContent = (data.holdingPeriod || data.holding_period || "1–5") + "d";
      qs("tradeEntry").textContent = data.entryPreference || data.entry_preference || "—";
      qs("tradeSection").style.display = "";
    } else {
      qs("tradeSection").style.display = "none";
    }
  }

  /* ── Submit handler ────────────────────────────────────────────────── */
  form.addEventListener("submit", function (e) {
    e.preventDefault();
    var ticker = qs("ticker").value.trim().toUpperCase();
    if (!ticker) { statusEl.textContent = "Please enter a ticker."; return; }

    var earningsDate = qs("earningsDate").value || "";

    var params = new URLSearchParams();
    params.set("ticker", ticker);
    if (earningsDate) params.set("earnings_date", earningsDate);

    runBtn.disabled = true;
    runBtn.querySelector(".btnSpinner").style.display = "inline-block";
    statusEl.textContent = "Evaluating " + ticker + " for post-event extension…";
    resultsEl.classList.add("hidden");

    if (window.RavenLoading) window.RavenLoading.show("Evaluating " + ticker + "…");

    fetch("/api/engine8/evaluate?" + params.toString())
      .then(function (r) {
        if (!r.ok) return r.json().then(function (d) { throw new Error(d.detail || r.statusText); });
        return r.json();
      })
      .then(function (data) {
        render(data);
        resultsEl.classList.remove("hidden");
        statusEl.textContent = "Evaluation complete for " + ticker + ".";
      })
      .catch(function (err) {
        statusEl.textContent = "Error: " + err.message;
      })
      .finally(function () {
        runBtn.disabled = false;
        runBtn.querySelector(".btnSpinner").style.display = "none";
        if (window.RavenLoading) window.RavenLoading.hide();
      });
  });

  /* ── Tooltip behaviour ─────────────────────────────────────────────── */
  document.addEventListener("click", function (e) {
    var btn = e.target.closest(".tipBtn");
    if (btn) {
      e.stopPropagation();
      var panel = btn.nextElementSibling;
      var open = panel.classList.contains("open");
      document.querySelectorAll(".tipPanel.open").forEach(function (p) { p.classList.remove("open"); });
      if (!open) panel.classList.add("open");
      return;
    }
    if (!e.target.closest(".tipPanel")) {
      document.querySelectorAll(".tipPanel.open").forEach(function (p) { p.classList.remove("open"); });
    }
  });
})();
