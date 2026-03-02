/* ── Engine 12 — VIX Spike Fade ────────────────────────────────────── */
(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };
  var esc = typeof escapeHtml === "function" ? escapeHtml : function (s) { return String(s); };
  var fmt2 = function (v) { return v == null ? "—" : Number(v).toFixed(2); };
  var fmt1 = function (v) { return v == null ? "—" : Number(v).toFixed(1); };
  var fmt0 = function (v) { return v == null ? "—" : Math.round(Number(v)).toString(); };
  var fmtPct = function (v) { return v == null ? "—" : (Number(v) * 100).toFixed(1) + "%"; };

  var lastPayload = null;

  function scoreClass(score) {
    if (score >= 70) return "e12Score--high";
    if (score >= 50) return "e12Score--mid";
    if (score >= 30) return "e12Score--low";
    return "e12Score--neutral";
  }

  function card(label, value, caption, scoreVal) {
    var sc = scoreVal != null ? ' <span class="e12Score ' + scoreClass(scoreVal) + '">' + fmt0(scoreVal) + "</span>" : "";
    return '<div class="e12Card"><div class="e12CardLabel">' + esc(label) + "</div>" +
      '<div class="e12CardValue">' + value + sc + "</div>" +
      '<div class="e12CardCaption">' + esc(caption || "") + "</div></div>";
  }

  function barHtml(pct, color) {
    return '<div class="e12Bar"><div class="e12BarFill" style="width:' +
      Math.max(0, Math.min(100, pct)) + "%;background:" + (color || "#4466ff") + '"></div></div>';
  }

  function gammaColor(sign) {
    if (sign === "negative") return "#ff6060";
    if (sign === "positive") return "#3cc878";
    return "#9999cc";
  }

  /* ── Render regime dashboard ── */
  function renderRegime(d) {
    var sp = d.spike || {};
    var sev = d.severity || {};
    var dg = d.dealerGamma || {};
    var stress = d.crossAssetStress || {};

    var html = "";
    html += card("VIX Current", fmt2(sp.vixCurrent), "20d MA: " + fmt2(sp.vix20dMA) + " | z: " + fmt2(sp.zScore));
    html += card("Spike Detected", sp.detected ? "YES" : "NO",
      "+" + fmt1(sp.spikePctAboveMA) + "% above MA | Pre-regime: " + (sp.preEventRegime || "—"));
    html += card("Severity Score", fmt0(sev.score), "VIX " + fmt1(sev.vixSpikePct) + "% | SPX " + fmt2(sev.spxGapPct) + "% | Oil " + fmt2(sev.oilGapPct) + "%", sev.score);
    html += card("Dealer Gamma",
      '<span style="color:' + gammaColor(dg.netGammaSign) + '">' + (dg.netGammaSign || "unknown").toUpperCase() + "</span>",
      "Magnitude: " + (dg.magnitudeBucket || "—"));
    html += card("Cross-Asset Stress", fmt0(stress.score), stress.label || "", stress.score);

    $("regimeGrid").innerHTML = html;
  }

  /* ── Render edge decomposition ── */
  function renderEdges(d) {
    var ec = d.edgeComposite || {};
    var edges = ec.edges || [];
    var html = "";

    for (var i = 0; i < edges.length; i++) {
      var e = edges[i];
      html += card(e.label, fmt1(e.score) + " / 100", e.interpretation, e.score);
    }
    html += card("Composite Edge", fmt1(ec.score) + " / 100", ec.label || "", ec.score);
    $("edgeGrid").innerHTML = html;
  }

  /* ── Render OU model ── */
  function renderOU(d) {
    var ou = d.ouModel || {};
    var ts = d.termStructure || {};
    var html = "";

    html += card("Modeled Half-Life", fmt1(ou.modeledHalfLifeDays) + "d",
      "kappa: " + fmt2(ou.kappa) + " | theta: " + fmt2(ou.theta));
    html += card("Long-Run Mean (θ)", fmt2(ou.theta), "sigma: " + fmt2(ou.sigma) + " | R²: " + fmt2(ou.rSquared));
    html += card("IV 30d / 60d / 90d",
      fmt1(ts.iv_30d) + " / " + fmt1(ts.iv_60d) + " / " + fmt1(ts.iv_90d), "SPX ATM implied vol by DTE");

    $("ouGrid").innerHTML = html;
    renderForwardCurve(d.forwardCurve || []);
  }

  /* ── SVG Forward Curve ── */
  function renderForwardCurve(curve) {
    var wrap = $("forwardCurveChart");
    if (!curve.length) { wrap.innerHTML = '<div class="e12CardCaption">No forward curve data.</div>'; return; }

    var W = 600, H = 160, pad = { l: 50, r: 20, t: 10, b: 30 };
    var xMin = 0, xMax = curve[curve.length - 1].horizon_days;
    var yVals = curve.map(function (p) { return p.expected_vix; });
    var yMin = Math.floor(Math.min.apply(null, yVals) - 1);
    var yMax = Math.ceil(Math.max.apply(null, yVals) + 1);

    function sx(d) { return pad.l + (d - xMin) / (xMax - xMin) * (W - pad.l - pad.r); }
    function sy(v) { return pad.t + (1 - (v - yMin) / (yMax - yMin)) * (H - pad.t - pad.b); }

    var pts = curve.map(function (p) { return sx(p.horizon_days) + "," + sy(p.expected_vix); }).join(" ");

    var svg = '<svg viewBox="0 0 ' + W + " " + H + '" xmlns="http://www.w3.org/2000/svg">';
    svg += '<line x1="' + pad.l + '" y1="' + (H - pad.b) + '" x2="' + (W - pad.r) + '" y2="' + (H - pad.b) + '" stroke="#2a2a4a" />';
    svg += '<line x1="' + pad.l + '" y1="' + pad.t + '" x2="' + pad.l + '" y2="' + (H - pad.b) + '" stroke="#2a2a4a" />';

    for (var yy = yMin; yy <= yMax; yy += Math.max(1, Math.round((yMax - yMin) / 4))) {
      svg += '<text x="' + (pad.l - 6) + '" y="' + (sy(yy) + 4) + '" fill="#8888aa" font-size="10" text-anchor="end">' + yy + "</text>";
      svg += '<line x1="' + pad.l + '" y1="' + sy(yy) + '" x2="' + (W - pad.r) + '" y2="' + sy(yy) + '" stroke="#1a1a3a" />';
    }
    for (var i = 0; i < curve.length; i++) {
      var d = curve[i].horizon_days;
      svg += '<text x="' + sx(d) + '" y="' + (H - 8) + '" fill="#8888aa" font-size="10" text-anchor="middle">' + d + "d</text>";
    }

    svg += '<polyline points="' + pts + '" fill="none" stroke="#4466ff" stroke-width="2" />';
    for (var j = 0; j < curve.length; j++) {
      var p = curve[j];
      svg += '<circle cx="' + sx(p.horizon_days) + '" cy="' + sy(p.expected_vix) + '" r="3" fill="#4466ff" />';
      svg += '<text x="' + sx(p.horizon_days) + '" y="' + (sy(p.expected_vix) - 8) + '" fill="#ccc" font-size="9" text-anchor="middle">' + fmt1(p.expected_vix) + "</text>";
    }
    svg += "</svg>";
    wrap.innerHTML = svg;
  }

  /* ── Render scenarios ── */
  function renderScenarios(d) {
    var sc = d.scenarios || {};
    var html = "";
    html += card("Contained", fmtPct(sc.pContained), "Spike fades within days", null);
    html += card("Disruption", fmtPct(sc.pDisruption), "Secondary expansion, then decay", null);
    html += card("Escalation", fmtPct(sc.pEscalation), "Multi-day vol expansion", null);

    html += barHtml((sc.pContained || 0) * 100, "#3cc878");
    html += barHtml((sc.pDisruption || 0) * 100, "#ffb43c");
    html += barHtml((sc.pEscalation || 0) * 100, "#ff6060");

    if (sc.adjustments && sc.adjustments.length) {
      for (var i = 0; i < sc.adjustments.length; i++) {
        html += '<div class="e12CardCaption" style="margin-top:4px;">• ' + esc(sc.adjustments[i]) + "</div>";
      }
    }
    $("scenarioGrid").innerHTML = html;

    $("slContained").value = Math.round((sc.pContained || 0.55) * 100);
    $("slDisruption").value = Math.round((sc.pDisruption || 0.28) * 100);
    $("slEscalation").value = Math.round((sc.pEscalation || 0.17) * 100);
    updateSliderLabels();
  }

  /* ── Render recommendation ── */
  function renderRecommendation(d) {
    var rec = d.recommendation;
    if (!rec) { $("recContent").innerHTML = '<div class="e12CardCaption">No recommendation available.</div>'; return; }

    var html = '<div class="e12Rec">';
    html += '<div class="e12RecTitle">' + esc(rec.primary) + "</div>";
    html += '<div class="e12RecBody">' + esc(rec.primaryRationale) + "</div>";
    html += "</div>";

    if (rec.guardrails && rec.guardrails.length) {
      for (var i = 0; i < rec.guardrails.length; i++) {
        html += '<div class="e12Guardrail">' + esc(rec.guardrails[i]) + "</div>";
      }
    }

    if (rec.positionSize) {
      var ps = rec.positionSize;
      html += '<div class="e12Grid" style="margin-top:12px;">';
      html += card("Contracts", String(ps.contracts || 0), ps.note || "");
      html += card("Risk Budget", "$" + fmt0(ps.adjustedBudget), "Severity " + fmtPct(ps.severityScale) + " | Esc " + fmtPct(ps.escalationScale));
      html += card("Total Max Loss", "$" + fmt0(ps.totalMaxLoss), "Per contract: $" + fmt0(ps.maxLossPerContract));
      html += "</div>";
    }

    $("recContent").innerHTML = html;
  }

  /* ── Render MC table ── */
  function renderMC(d) {
    var mc = d.monteCarlo;
    if (!mc || !mc.structures) { $("mcBody").innerHTML = ""; return; }

    var html = "";
    var structs = mc.structures.slice().sort(function (a, b) { return b.sharpe - a.sharpe; });
    for (var i = 0; i < structs.length; i++) {
      var s = structs[i];
      html += "<tr>";
      html += "<td><strong>" + esc(s.name) + "</strong></td>";
      html += '<td style="color:' + (s.expectedPnL >= 0 ? "#3cc878" : "#ff6060") + '">$' + fmt2(s.expectedPnL) + "</td>";
      html += "<td>" + fmtPct(s.pProfit) + "</td>";
      html += "<td>" + fmt2(s.sharpe) + "</td>";
      html += '<td style="color:#ff6060">$' + fmt2(s.cvar95) + "</td>";
      html += '<td style="color:#ff6060">$' + fmt2(s.maxLoss) + "</td>";
      html += '<td style="color:#3cc878">$' + fmt2(s.maxGain) + "</td>";
      html += "</tr>";
    }
    $("mcBody").innerHTML = html;
  }

  /* ── Render historical table ── */
  function renderHistorical(d) {
    var events = d.historicalComparisons || [];
    var html = "";
    for (var i = 0; i < events.length; i++) {
      var e = events[i];
      var pre = e.vix_pre_close || 0;
      var open = e.vix_event_open || 0;
      var peak = e.peak_vix || open;
      var jumpR = open > 0 ? (peak / open).toFixed(2) : "—";
      var decay5d = open > 0 && e.vix_5d_after ? (((e.vix_5d_after - open) / open) * 100).toFixed(1) + "%" : "—";

      var outcomeColor = e.outcome_class === "contained" ? "#3cc878" : e.outcome_class === "disruption" ? "#ffb43c" : "#ff6060";

      html += "<tr>";
      html += "<td>" + esc(e.description || e.event_id) + "</td>";
      html += "<td>" + esc(e.event_date || "") + "</td>";
      html += "<td>" + fmt1(pre) + "</td>";
      html += "<td>" + fmt1(open) + "</td>";
      html += "<td>" + fmt1(peak) + "</td>";
      html += "<td>" + jumpR + "x</td>";
      html += "<td>" + fmt2(e.spx_gap_pct) + "%</td>";
      html += "<td>" + fmt2(e.oil_gap_pct) + "%</td>";
      html += '<td style="color:' + outcomeColor + '">' + esc(e.outcome_class || "") + "</td>";
      html += "<td>" + decay5d + "</td>";
      html += "</tr>";
    }
    $("histBody").innerHTML = html;
  }

  /* ── Slider helpers ── */
  function updateSliderLabels() {
    $("valContained").textContent = $("slContained").value + "%";
    $("valDisruption").textContent = $("slDisruption").value + "%";
    $("valEscalation").textContent = $("slEscalation").value + "%";
  }

  $("slContained").addEventListener("input", updateSliderLabels);
  $("slDisruption").addEventListener("input", updateSliderLabels);
  $("slEscalation").addEventListener("input", updateSliderLabels);

  /* ── Re-simulate ── */
  $("reSimBtn").addEventListener("click", function () {
    var c = parseInt($("slContained").value, 10);
    var d = parseInt($("slDisruption").value, 10);
    var e = parseInt($("slEscalation").value, 10);
    var total = c + d + e;
    if (total <= 0) return;

    var url = "/api/engine12/simulate?p_contained=" + (c / total).toFixed(3) +
      "&p_disruption=" + (d / total).toFixed(3) +
      "&p_escalation=" + (e / total).toFixed(3);

    if (lastPayload && lastPayload.spike && lastPayload.spike.vixCurrent) {
      url += "&vix_current=" + lastPayload.spike.vixCurrent;
    }

    $("status").textContent = "Re-simulating…";
    if (typeof RavenLoading !== "undefined") RavenLoading.show();

    fetch(url)
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.monteCarlo) {
          renderMC({ monteCarlo: data.monteCarlo });
        }
        if (data.recommendation) {
          renderRecommendation({ recommendation: data.recommendation });
        }
        $("status").textContent = "Re-simulation complete";
      })
      .catch(function (err) {
        $("status").textContent = "Re-simulation failed: " + err.message;
      })
      .finally(function () {
        if (typeof RavenLoading !== "undefined") RavenLoading.hide();
      });
  });

  /* ── Main scan ── */
  function runScan() {
    var dateVal = $("dateInput").value || "";
    var url = "/api/engine12/scan";
    if (dateVal) url += "?date=" + dateVal;

    $("status").textContent = "Running Engine 12 analysis…";
    $("results").classList.add("hidden");
    if (typeof RavenLoading !== "undefined") RavenLoading.show();

    fetch(url)
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.status === "error") {
          $("status").textContent = "Error: " + (data.message || "Unknown");
          return;
        }
        lastPayload = data;
        renderRegime(data);
        renderEdges(data);
        renderOU(data);
        renderScenarios(data);
        renderRecommendation(data);
        renderMC(data);
        renderHistorical(data);
        $("results").classList.remove("hidden");
        $("status").textContent = "Analysis complete — " + (data.asOfDate || "");
      })
      .catch(function (err) {
        $("status").textContent = "Failed: " + err.message;
      })
      .finally(function () {
        if (typeof RavenLoading !== "undefined") RavenLoading.hide();
      });
  }

  $("scanForm").addEventListener("submit", function (e) { e.preventDefault(); runScan(); });
  $("runBtn").addEventListener("click", function (e) { e.preventDefault(); runScan(); });
})();
