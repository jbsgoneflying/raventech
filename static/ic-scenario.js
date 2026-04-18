/* ── Engine 14 — IC Scenario Simulator frontend ─────────────────────── */

(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };
  var mtmChart = null;
  var lastPayload = null;
  var lastRequestBody = null;
  var exitGrid = [];

  /* Default: entry = today, expiry = next Friday */
  function initDefaults() {
    var now = new Date();
    var entry = now.toISOString().slice(0, 10);
    var dow = now.getDay();                         // 0=Sun..6=Sat
    var daysToFri = (5 - dow + 7) % 7 || 7;
    var fri = new Date(now.getTime() + daysToFri * 86400000);
    var expiry = fri.toISOString().slice(0, 10);
    if (!$("entryDate").value) $("entryDate").value = entry;
    if (!$("expiry").value) $("expiry").value = expiry;
  }

  function setStatus(msg, kind) {
    var el = $("status");
    if (!el) return;
    el.textContent = msg || "";
    el.className = "status" + (kind ? " " + kind : "");
  }

  function showBanner(msg, kind) {
    var el = $("banner");
    if (!el) return;
    if (!msg) { el.style.display = "none"; return; }
    el.textContent = msg;
    el.className = "e14Banner" + (kind ? " " + kind : "");
    el.style.display = "block";
  }

  function fmtNum(x, digits) {
    if (x === null || x === undefined || isNaN(Number(x))) return "—";
    return Number(x).toFixed(digits === undefined ? 1 : digits);
  }

  function fmtPct(x, digits) {
    if (x === null || x === undefined || isNaN(Number(x))) return "—";
    var v = Number(x);
    return (v >= 0 ? "+" : "") + v.toFixed(digits === undefined ? 1 : digits) + "%";
  }

  function pnlColor(x) {
    if (x === null || x === undefined || isNaN(Number(x))) return "muted";
    var v = Number(x);
    if (v >= 50) return "green";
    if (v >= 0) return "blue";
    if (v >= -100) return "amber";
    return "red";
  }

  /* ── Outcome panel ─────────────────────────────────────────────── */

  var OUTCOME_META = {
    earlyTarget:  { label: "Early Target", color: "green" },
    fullCollect:  { label: "Full Collect", color: "blue" },
    whiteKnuckle: { label: "White Knuckle", color: "amber" },
    stopOut:      { label: "Stop Out",     color: "red" },
    breach:       { label: "Breach",       color: "red" },
  };

  function renderOutcomes(dist, targetId) {
    var panel = $(targetId || "outcomePanel");
    if (!panel) return;
    panel.innerHTML = "";
    var keys = ["earlyTarget", "fullCollect", "whiteKnuckle", "stopOut", "breach"];
    var best = null;
    keys.forEach(function (k) {
      var v = (dist && dist[k]) || { pct: 0, n: 0, avgPnlPct: 0, avgDays: 0, maxAdverseExcursionPct: 0 };
      if (!best || v.pct > best.pct) best = Object.assign({ key: k }, v);
    });

    keys.forEach(function (k) {
      var v = (dist && dist[k]) || { pct: 0, n: 0, avgPnlPct: 0, avgDays: 0, maxAdverseExcursionPct: 0 };
      var meta = OUTCOME_META[k];
      var card = document.createElement("div");
      card.className = "e14OutcomeCard" + (best && best.key === k ? " dominant" : "");
      card.innerHTML =
        '<div class="e14OutcomeName">' + meta.label + '</div>' +
        '<div class="e14OutcomePct ' + meta.color + '">' + fmtNum(v.pct) + '%</div>' +
        '<div class="e14OutcomeBar"><div class="e14OutcomeBarFill bg' + cap(meta.color) + '" style="width:' + Math.max(2, Math.min(100, v.pct)) + '%"></div></div>' +
        '<div class="e14OutcomeMeta">' +
          'n=' + (v.n || 0) + ' · avg ' + fmtPct(v.avgPnlPct) +
          (v.avgDays ? ' · ~' + fmtNum(v.avgDays, 1) + 'd' : '') +
        '</div>';
      panel.appendChild(card);
    });
  }

  /* ── Phase 2: Conditioning modifiers ─────────────────────────── */

  function renderModifiers(cond) {
    var panel = $("modifiersPanel");
    var label = $("modifiersDividerLabel");
    if (!panel || !label) return;
    if (!cond || Object.keys(cond).length === 0) {
      panel.style.display = "none"; label.style.display = "none";
      return;
    }
    var mods = [
      { key: "calendar",     title: "Macro Calendar" },
      { key: "dealerGamma",  title: "Dealer Gamma" },
      { key: "creditStress", title: "Cross-Asset Stress" },
      { key: "gapRegime",    title: "Gap Regime (E13)" },
    ];
    panel.innerHTML = "";
    mods.forEach(function (m) {
      var v = cond[m.key];
      if (!v) return;
      var colorMap = {
        extreme:  "red",
        elevated: "red",
        moderate: "amber",
        low:      "blue",
        none:     "muted",
      };
      var color = colorMap[v.severity] || "muted";
      var statusLine = v.status === "ok" ? "" : ' <span class="muted">[' + v.status + ']</span>';
      var tailBadge = v.tailMultiplier && v.tailMultiplier !== 1
        ? ' · tail ×' + Number(v.tailMultiplier).toFixed(2)
        : "";
      var wrBadge = v.winRateShiftPct && v.winRateShiftPct !== 0
        ? ' · WR ' + fmtPct(v.winRateShiftPct, 1)
        : "";
      var card = document.createElement("div");
      card.className = "e14Card";
      card.innerHTML =
        '<div class="e14CardLabel">' + m.title + statusLine + '</div>' +
        '<div class="e14CardValue ' + color + '" style="font-size:14px;">' +
          (v.severity || "none").toUpperCase() + tailBadge + wrBadge +
        '</div>' +
        '<div class="e14CardCaption">' + (v.note || "") + '</div>';
      panel.appendChild(card);
    });
    // Net summary card
    if ("netTailMultiplier" in cond || "netWinRateShiftPct" in cond) {
      var net = document.createElement("div");
      net.className = "e14Card";
      net.style.borderColor = "var(--blue)";
      net.innerHTML =
        '<div class="e14CardLabel">Net Adjustment</div>' +
        '<div class="e14CardValue blue" style="font-size:16px;">' +
          '×' + Number(cond.netTailMultiplier || 1).toFixed(2) +
          ' · ' + fmtPct(cond.netWinRateShiftPct || 0, 1) +
        '</div>' +
        '<div class="e14CardCaption">Combined tail multiplier × win-rate shift applied to the adjusted distribution.</div>';
      panel.appendChild(net);
    }
    panel.style.display = "grid";
    label.style.display = "";
  }

  function renderAdjusted(adj) {
    var panel = $("adjustedOutcomePanel");
    var label = $("adjustedDividerLabel");
    if (!panel || !label) return;
    if (!adj || Object.keys(adj).length === 0) {
      panel.style.display = "none"; label.style.display = "none";
      return;
    }
    renderOutcomes(adj, "adjustedOutcomePanel");
    panel.style.display = "grid";
    label.style.display = "";
  }

  function cap(s) { return s ? s[0].toUpperCase() + s.slice(1) : s; }

  /* ── Entry state cards ─────────────────────────────────────────── */

  function renderEntryCards(data) {
    var cards = $("entryCards");
    cards.innerHTML = "";
    var es = data.entryState;
    if (!es) {
      cards.innerHTML = '<div class="e14Card"><div class="e14CardLabel">Entry state</div><div class="e14CardValue">—</div><div class="e14CardCaption">Simulator returned no entry state.</div></div>';
      return;
    }
    var items = [
      { label: "Analogues Used",  value: data.analoguesUsed,              caption: "of " + (data.analoguesConsidered || 0) + " candidates" },
      { label: "Regime Bucket",   value: es.regimeBucket,                 caption: "proxy: RV20 percentile" },
      { label: "Spot (Entry)",    value: fmtNum(es.userSpot, 2) },
      { label: "1σ EM %",         value: fmtNum(es.userEmPct, 2) + "%" },
      { label: "Wing Width",      value: es.wingWidth,                    caption: "smaller of put/call wings" },
      { label: "Mean P&L",        value: fmtPct(data.expectedValue.meanPnlPct, 1), caption: "across all analogues" },
      { label: "Median P&L",      value: fmtPct(data.expectedValue.medianPnlPct, 1) },
      { label: "Sharpe (proxy)",  value: fmtNum(data.expectedValue.sharpeProxy, 2) },
    ];
    items.forEach(function (it) {
      var card = document.createElement("div");
      card.className = "e14Card";
      card.innerHTML =
        '<div class="e14CardLabel">' + it.label + '</div>' +
        '<div class="e14CardValue">' + (it.value === undefined || it.value === null ? "—" : it.value) + '</div>' +
        (it.caption ? '<div class="e14CardCaption">' + it.caption + '</div>' : '');
      cards.appendChild(card);
    });
  }

  /* ── MTM timeline chart ────────────────────────────────────────── */

  function renderMtmChart(timeline) {
    var canvas = $("mtmChart");
    if (!canvas || !window.Chart) return;
    var labels = timeline.map(function (r) { return "DTE " + r.dte; });
    var p10 = timeline.map(function (r) { return r.p10; });
    var p50 = timeline.map(function (r) { return r.p50; });
    var p90 = timeline.map(function (r) { return r.p90; });

    if (mtmChart) { mtmChart.destroy(); mtmChart = null; }

    mtmChart = new Chart(canvas.getContext("2d"), {
      type: "line",
      data: {
        labels: labels,
        datasets: [
          { label: "P90", data: p90, borderColor: "#34c759", backgroundColor: "rgba(52,199,89,0.12)", fill: "+1", tension: 0.25, pointRadius: 2 },
          { label: "P50", data: p50, borderColor: "#0a84ff", backgroundColor: "rgba(10,132,255,0.10)", fill: false, borderWidth: 2, tension: 0.25, pointRadius: 3 },
          { label: "P10", data: p10, borderColor: "#ff3b30", backgroundColor: "rgba(255,59,48,0.08)", fill: false, tension: 0.25, pointRadius: 2 },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        scales: {
          y: { title: { display: true, text: "P&L (% of credit)" }, grid: { color: "rgba(0,0,0,0.05)" } },
          x: { title: { display: true, text: "Days to Expiry" }, grid: { color: "rgba(0,0,0,0.03)" } },
        },
        plugins: {
          legend: { position: "bottom" },
          tooltip: { callbacks: {
            label: function (ctx) { return ctx.dataset.label + ": " + ctx.parsed.y.toFixed(1) + "%"; }
          }},
        },
      },
    });
  }

  /* ── Exit-rule panel ───────────────────────────────────────────── */

  function renderExitPanel(opt, req) {
    var el = $("exitPanel");
    if (!opt) { el.innerHTML = ""; return; }
    var changed = (opt.recommendedProfitTarget !== req.profitTargetPct)
                || (opt.recommendedStopLoss !== req.stopLossPct);
    var delta = opt.deltaFromDefault || {};
    el.innerHTML =
      '<div class="e14ExitCard">' +
        '<div class="e14ExitRow">' +
          '<div><div class="e14ExitLabel">Profit Target</div>' +
            '<div class="e14ExitValue ' + (changed ? "blue" : "") + '">' +
              fmtNum(opt.recommendedProfitTarget, 0) + '%' +
            '</div></div>' +
          '<div><div class="e14ExitLabel">Stop Loss</div>' +
            '<div class="e14ExitValue ' + (changed ? "blue" : "") + '">' +
              fmtNum(opt.recommendedStopLoss, 0) + '%' +
            '</div></div>' +
          '<div><div class="e14ExitLabel">Δ Win Rate</div>' +
            '<div class="e14ExitValue ' + (delta.winRatePct > 0 ? "green" : delta.winRatePct < 0 ? "red" : "") + '">' +
              fmtPct(delta.winRatePct, 1) +
            '</div></div>' +
          '<div><div class="e14ExitLabel">Δ Avg P&L</div>' +
            '<div class="e14ExitValue ' + (delta.avgPnlPct > 0 ? "green" : delta.avgPnlPct < 0 ? "red" : "") + '">' +
              fmtPct(delta.avgPnlPct, 1) +
            '</div></div>' +
        '</div>' +
        '<div class="e14ExitDelta" style="margin-top:10px">' +
          (changed
            ? "Historical grid search suggests these bands improve both win-rate and average P&L vs your defaults."
            : "Your current exit rules are already near-optimal on this analogue pool.") +
        '</div>' +
      '</div>';
  }

  /* ── Notes + analogue table ────────────────────────────────────── */

  function renderNotes(notes) {
    var el = $("notesList");
    el.innerHTML = "";
    (notes || []).forEach(function (n) {
      var d = document.createElement("div");
      d.className = "e14Note";
      d.textContent = n;
      el.appendChild(d);
    });
  }

  function renderAnaloguesTable(rows) {
    var el = $("analoguesTableWrap");
    if (!rows || !rows.length) { el.innerHTML = '<div class="muted">No analogues to display.</div>'; return; }
    var html = ['<table class="e14Table"><thead><tr>',
      '<th>Entry</th><th>Expiry</th><th>Outcome</th><th>Exit Day</th><th>P&L %</th><th>MAE %</th>',
      '<th>SP</th><th>LP</th><th>SC</th><th>LC</th><th>Breached</th>',
      '</tr></thead><tbody>'];
    rows.slice(0, 100).forEach(function (r) {
      var c = pnlColor(r.pnlPct);
      html.push('<tr>');
      html.push('<td>' + r.entryDate + '</td>');
      html.push('<td>' + r.expiryDate + '</td>');
      html.push('<td>' + (OUTCOME_META[r.outcome] ? OUTCOME_META[r.outcome].label : r.outcome) + '</td>');
      html.push('<td>' + r.exitDay + '</td>');
      html.push('<td class="' + c + '">' + fmtPct(r.pnlPct) + '</td>');
      html.push('<td class="amber">' + fmtPct(r.mae) + '</td>');
      html.push('<td>' + fmtNum(r.mappedStrikes.shortPut, 1) + '</td>');
      html.push('<td>' + fmtNum(r.mappedStrikes.longPut, 1) + '</td>');
      html.push('<td>' + fmtNum(r.mappedStrikes.shortCall, 1) + '</td>');
      html.push('<td>' + fmtNum(r.mappedStrikes.longCall, 1) + '</td>');
      html.push('<td>' + (r.breached ? '<span class="red">yes</span>' : '<span class="muted">no</span>') + '</td>');
      html.push('</tr>');
    });
    html.push('</tbody></table>');
    el.innerHTML = html.join("");
  }

  /* ── Submit handler ────────────────────────────────────────────── */

  function buildRequestBody() {
    return {
      underlying:       "SPX",
      entryDate:        $("entryDate").value,
      expiry:           $("expiry").value,
      shortPut:         Number($("shortPut").value),
      longPut:          Number($("longPut").value),
      shortCall:        Number($("shortCall").value),
      longCall:         Number($("longCall").value),
      creditReceived:   Number($("creditReceived").value),
      profitTargetPct:  Number($("profitTargetPct").value || 50),
      stopLossPct:      Number($("stopLossPct").value || 200),
      seasonMode:       $("seasonMode").value || "none",
    };
  }

  async function runScenario(evt) {
    if (evt) evt.preventDefault();
    showBanner("");
    setStatus("Running replay…");

    var body;
    try { body = buildRequestBody(); }
    catch (e) { setStatus("Invalid input: " + e.message, "error"); return; }

    // Front-end sanity
    if (!(body.longPut < body.shortPut && body.shortPut < body.shortCall && body.shortCall < body.longCall)) {
      setStatus("Strikes must satisfy: longPut < shortPut < shortCall < longCall", "error");
      return;
    }

    var t0 = Date.now();
    try {
      var resp = await fetch("/api/ic-scenario", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      var data = await resp.json();
      if (!resp.ok) {
        var msg = data && data.detail ? data.detail : ("HTTP " + resp.status);
        setStatus("Failed: " + msg, "error");
        showBanner(msg, "red");
        return;
      }
      render(data, body);
      var ms = Date.now() - t0;
      setStatus("Done in " + ms + "ms · " + (data.analoguesUsed || 0) + " analogues", "success");
    } catch (e) {
      setStatus("Network error: " + e.message, "error");
    }
  }

  function render(data, req) {
    lastPayload = data;
    lastRequestBody = req;
    $("results").classList.remove("hidden");
    if ((data.analoguesUsed || 0) === 0) {
      showBanner(
        (data.conditioningNotes && data.conditioningNotes[0])
          || "No analogues available. Run the backfill to populate the chain cache.",
        "red"
      );
    }
    renderEntryCards(data);
    renderOutcomes(data.outcomeDistribution);
    renderAdjusted(data.adjustedOutcomeDistribution);
    renderModifiers(data.conditioningModifiers);
    renderMtmChart(data.mtmTimeline || []);
    renderExitPanel(data.exitRulesOptimization, req);
    renderSlider(data.exitRulesOptimization, req);
    renderNotes(data.conditioningNotes || []);
    renderAnaloguesTable(data.matchedAnalogues || []);
    setActionStatus("", "");
  }

  /* ── Phase 3: Exit-rule sensitivity slider ───────────────────── */

  function renderSlider(opt, req) {
    var label = $("sliderDividerLabel");
    var panel = $("sliderPanel");
    if (!label || !panel) return;
    exitGrid = (opt && opt.grid) || [];
    if (!exitGrid.length) {
      label.style.display = "none"; panel.style.display = "none";
      return;
    }
    label.style.display = ""; panel.style.display = "";

    var pts = Array.from(new Set(exitGrid.map(function (c) { return c.profitTarget; }))).sort(function (a, b) { return a - b; });
    var sls = Array.from(new Set(exitGrid.map(function (c) { return c.stopLoss; }))).sort(function (a, b) { return a - b; });
    var ptInput = $("slidePt"), slInput = $("slideSl");
    if (!ptInput || !slInput) return;
    ptInput.min = pts[0]; ptInput.max = pts[pts.length - 1];
    slInput.min = sls[0]; slInput.max = sls[sls.length - 1];
    ptInput.value = (req && req.profitTargetPct) || 50;
    slInput.value = (req && req.stopLossPct) || 200;
    updateSlider();
    ptInput.oninput = updateSlider;
    slInput.oninput = updateSlider;
  }

  function nearestCell(pt, sl) {
    if (!exitGrid.length) return null;
    var best = null, bestD = Infinity;
    exitGrid.forEach(function (c) {
      var d = Math.pow(c.profitTarget - pt, 2) + Math.pow(c.stopLoss - sl, 2) / 25;
      if (d < bestD) { bestD = d; best = c; }
    });
    return best;
  }

  function updateSlider() {
    var pt = Number($("slidePt").value), sl = Number($("slideSl").value);
    $("slidePtLabel").textContent = pt + "%";
    $("slideSlLabel").textContent = sl + "%";
    var cell = nearestCell(pt, sl);
    if (!cell) return;
    $("slideWr").textContent = fmtNum(cell.winRatePct, 1) + "%";
    $("slideAvg").textContent = fmtPct(cell.avgPnlPct, 1);
    $("slideWr").className = "e14ExitValue " + (cell.winRatePct >= 65 ? "green" : cell.winRatePct >= 50 ? "blue" : "amber");
    $("slideAvg").className = "e14ExitValue " + pnlColor(cell.avgPnlPct);
    $("slideDelta").textContent =
      "Nearest grid cell: pt=" + cell.profitTarget + "%, sl=" + cell.stopLoss +
      "% · " + (cell.winRatePct).toFixed(1) + "% wins · avg " + fmtPct(cell.avgPnlPct);
  }

  /* ── Phase 3: Save to journal ─────────────────────────────────── */

  function setActionStatus(msg, kind) {
    var el = $("actionStatus");
    if (!el) return;
    el.textContent = msg || "";
    el.className = "e14ExitDelta" + (kind ? " " + kind : "");
  }

  async function saveToJournal() {
    if (!lastPayload || !lastRequestBody) {
      setActionStatus("Run a scenario first.", "red"); return;
    }
    setActionStatus("Saving trade…");
    try {
      var resp = await fetch("/api/ic-scenario/journal", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scenario: lastPayload, request: lastRequestBody }),
      });
      var data = await resp.json();
      if (!resp.ok) {
        setActionStatus("Failed: " + (data.detail || resp.status), "red");
        return;
      }
      var id = data.tradeId;
      setActionStatus("Saved as " + id + " — view at " + (data.viewUrl || "/spx"), "green");
    } catch (e) {
      setActionStatus("Network error: " + e.message, "red");
    }
  }

  /* ── Phase 3: Chat summary ───────────────────────────────────── */

  function copyChatSummary() {
    if (!lastPayload || !lastRequestBody) {
      setActionStatus("Run a scenario first.", "red"); return;
    }
    var d = lastPayload, r = lastRequestBody;
    var dist = d.outcomeDistribution || {};
    var adj = d.adjustedOutcomeDistribution || {};
    var mods = d.conditioningModifiers || {};
    var lines = [
      "Please review this iron condor scenario:",
      "- Underlying: SPX",
      "- Entry " + r.entryDate + " → Expiry " + r.expiry,
      "- Strikes: LP " + r.longPut + " / SP " + r.shortPut + " / SC " + r.shortCall + " / LC " + r.longCall,
      "- Credit " + r.creditReceived + " · Profit target " + r.profitTargetPct + "% · Stop " + r.stopLossPct + "%",
      "",
      "Simulator (" + (d.analoguesUsed || 0) + " analogues):",
      "- fullCollect " + fmtNum((dist.fullCollect || {}).pct) + "% · earlyTarget " + fmtNum((dist.earlyTarget || {}).pct) + "%",
      "- whiteKnuckle " + fmtNum((dist.whiteKnuckle || {}).pct) + "% · stopOut " + fmtNum((dist.stopOut || {}).pct) + "% · breach " + fmtNum((dist.breach || {}).pct) + "%",
      "- Mean P&L " + fmtPct((d.expectedValue || {}).meanPnlPct) + " · Median " + fmtPct((d.expectedValue || {}).medianPnlPct),
    ];
    if (Object.keys(adj).length) {
      lines.push(
        "",
        "Adjusted (after Phase 2 conditioning):",
        "- fullCollect " + fmtNum((adj.fullCollect || {}).pct) + "% · earlyTarget " + fmtNum((adj.earlyTarget || {}).pct) + "% · breach " + fmtNum((adj.breach || {}).pct) + "%"
      );
    }
    if (Object.keys(mods).length) {
      lines.push("", "Modifiers:");
      ["calendar", "dealerGamma", "creditStress", "gapRegime"].forEach(function (k) {
        if (mods[k] && mods[k].note) lines.push("- " + k + ": " + mods[k].note);
      });
    }
    lines.push("", "Thoughts?");
    var text = lines.join("\n");
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () {
        setActionStatus("Chat summary copied to clipboard. Paste it into Raven Chat.", "green");
      }, function () {
        setActionStatus("Clipboard write failed — falling back to alert.", "amber");
        window.prompt("Copy this summary:", text);
      });
    } else {
      window.prompt("Copy this summary:", text);
    }
  }

  /* ── Phase 3: Post-trade review ──────────────────────────────── */

  async function loadReview() {
    var tid = ($("reviewTradeId").value || "").trim();
    var wrap = $("reviewPanel");
    if (!tid) { wrap.innerHTML = '<div class="muted">Enter a trade ID.</div>'; return; }
    wrap.innerHTML = '<div class="muted">Loading…</div>';
    try {
      var resp = await fetch("/api/ic-scenario/review?tradeId=" + encodeURIComponent(tid));
      var data = await resp.json();
      if (!resp.ok) {
        wrap.innerHTML = '<div class="red">' + (data.detail || resp.status) + '</div>';
        return;
      }
      var pred = data.predicted || {};
      var actual = data.actual || {};
      var status = actual.status || "active";
      var pnl = actual.pnlPct;
      var rows = [
        ['Status', status],
        ['Closed At', actual.closedAt || '—'],
        ['Close Reason', actual.closeReason || '—'],
        ['Actual P&L', pnl === undefined || pnl === null ? '—' : fmtPct(pnl, 1)],
        ['Actual Days Held', actual.daysHeld !== undefined ? actual.daysHeld : '—'],
        ['Predicted Mean P&L', fmtPct(pred.meanPnlPct, 1)],
        ['Predicted Median P&L', fmtPct(pred.medianPnlPct, 1)],
        ['Predicted FullCollect %', fmtNum(pred.fullCollectPct) + '%'],
        ['Predicted Breach %', fmtNum(pred.breachPct) + '%'],
      ];
      var html = '<table class="e14Table"><tbody>';
      rows.forEach(function (r) {
        html += '<tr><th>' + r[0] + '</th><td>' + r[1] + '</td></tr>';
      });
      html += '</tbody></table>';
      if (data.verdict) {
        html += '<div class="e14Banner blue">' + data.verdict + '</div>';
      }
      wrap.innerHTML = html;
    } catch (e) {
      wrap.innerHTML = '<div class="red">Network error: ' + e.message + '</div>';
    }
  }

  /* ── Boot ─────────────────────────────────────────────────────── */

  function boot() {
    initDefaults();
    var form = $("icForm");
    if (form) form.addEventListener("submit", runScenario);
    var j = $("journalBtn"); if (j) j.addEventListener("click", saveToJournal);
    var c = $("chatBtn"); if (c) c.addEventListener("click", copyChatSummary);
    var rv = $("reviewBtn"); if (rv) rv.addEventListener("click", loadReview);

    // Health probe — if backend disabled or cache empty, surface a banner early.
    fetch("/api/ic-scenario/health")
      .then(function (r) { return r.json(); })
      .then(function (h) {
        if (!h || !h.enabled) {
          showBanner("Engine 14 is disabled. Set ENABLE_ENGINE14_IC_SCENARIO=1 to enable.", "amber");
          return;
        }
        if (h.chainCache && h.chainCache.daysCovered < 30) {
          showBanner(
            "Chain cache is sparse (" + (h.chainCache.daysCovered || 0) + " days cached). " +
            "Run scripts/engine14_backfill_chains.py on the droplet to populate 2 years of SPX weeklies.",
            "amber"
          );
        }
      })
      .catch(function () { /* ignore — endpoint gated in prod */ });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
