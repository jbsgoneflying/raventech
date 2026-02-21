/* ── Engine 8: Post-Event Trade Extension (Lifecycle) ───────────────────
   Frontend controller — handles Phase A (pre-earnings) and Phase B
   (post-earnings) rendering based on the API response.
   ──────────────────────────────────────────────────────────────────────── */
(function () {
  "use strict";

  var form       = document.getElementById("e8Form");
  var runBtn     = document.getElementById("runBtn");
  var statusEl   = document.getElementById("status");
  var resultsEl  = document.getElementById("results");
  var phaseAEl   = document.getElementById("phaseAResults");

  var _dummyEl = document.createElement("span");
  function qs(id) { return document.getElementById(id) || _dummyEl; }
  var _lastPhaseAData = null;
  var _deskNotesCache = {};
  var _rowPlaybookCache = {};
  var _rowPlaybookAbort = null;
  function fmt(v, d) { return v == null ? "—" : Number(v).toFixed(d == null ? 2 : d); }
  function pct(v) { return v == null ? "—" : (Number(v) * 100).toFixed(1) + "%"; }

  /* ── Phase A: Pre-Earnings ───────────────────────────────────────── */
  function renderPhaseA(data) {
    phaseAEl.classList.remove("hidden");
    resultsEl.classList.add("hidden");

    var timing = data.timing || "UNK";
    var timingLabel = timing === "AMC" ? "After Market Close" : timing === "BMO" ? "Before Market Open" : "Timing TBD";
    qs("phaseATiming").textContent = data.earnings_date + " · " + timingLabel;
    qs("phaseACountdown").textContent = data.countdown_days != null ? data.countdown_days + " day" + (data.countdown_days !== 1 ? "s" : "") + " away" : "";

    var e1 = data.engine1 || {};
    var sum = e1.summary || {};
    var cur = e1.current || {};
    var em = e1.expectedMove || {};
    var st = e1.strikeTargets || {};
    var bl = e1.baseline || {};

    /* Core metrics row */
    qs("paBreachRate").textContent = sum.breach_rate_pct != null ? fmt(sum.breach_rate_pct) + "%" : "—";

    var avgOs = sum.avg_above_breach_pct;
    qs("paAvgOvershoot").textContent = avgOs != null ? fmt(avgOs) + "%" : "—";

    qs("paAvgRealizedImplied").textContent = bl.avg_ratio_realized_to_implied != null ? fmt(bl.avg_ratio_realized_to_implied) + "×" : "—";

    var evUsed = sum.events_used;
    var evFound = sum.events_found;
    qs("paEventsUsed").textContent = evUsed != null ? evUsed + (evFound != null ? " / " + evFound : "") : "—";

    var regime = e1.regime || {};
    qs("paRegimeLabel").textContent = regime.label || "—";

    /* ORATS EM (EOD + delayed) */
    var eodEmPct = cur.impliedMovePct;
    var delayedEmPct = cur.delayedImpliedMovePct;
    qs("paOratsEm").textContent = eodEmPct != null ? fmt(eodEmPct) + "%" : "—";
    qs("paOratsEmCaption").textContent = cur.asOfDate ? "As of: " + cur.asOfDate + " · EOD (used for breach history)" : "EOD (used for breach history)";
    qs("paDelayedEm").textContent = delayedEmPct != null ? fmt(delayedEmPct) + "%" : "—";
    var delayedNote = cur.delayedUpdatedAt ? "Updated: " + cur.delayedUpdatedAt : cur.delayedTradeDate ? "As of: " + cur.delayedTradeDate : "";
    qs("paDelayedEmCaption").textContent = (delayedNote ? delayedNote + " · " : "") + "15-min delayed" + (delayedEmPct != null ? " · Used for strike targets" : "");

    /* Straddle EM */
    var stEmPct = em.expectedMovePct;
    var stEmDollars = em.expectedMoveDollars;
    var stEmExpiry = em.expiry ? String(em.expiry).slice(0, 10) : "";
    var stEmSource = em.source || "";
    qs("paStraddleEm").textContent = stEmPct != null ? fmt(stEmPct) + "%" : "—";
    var stCaption = [];
    if (stEmDollars != null) stCaption.push("$" + fmt(stEmDollars) + " pts");
    if (stEmExpiry) stCaption.push("Exp: " + stEmExpiry);
    if (stEmSource) stCaption.push(stEmSource === "live" ? "Live" : stEmSource === "eod" ? "EOD" : stEmSource);
    qs("paStraddleEmCaption").textContent = stCaption.length ? stCaption.join(" · ") : "ATM-forward straddle method";

    /* Strike Targets */
    qs("paStWhite").textContent = st && st.whitePct != null ? fmt(st.whitePct) + "%" : "—";
    qs("paStBlue").textContent = st && st.bluePct != null ? fmt(st.bluePct) + "%" : "—";
    qs("paStRed").textContent = st && st.redPct != null ? fmt(st.redPct) + "%" : "—";
    var stSource = st && st.emSource === "delayed" ? "15-min delayed EM" : "ORATS EOD EM";
    qs("paStrikeCaption").textContent = "Wing distance as % of spot (" + stSource + ").";

    /* Breach detail */
    qs("paUpBreach").textContent = sum.upBreachRatePct != null ? fmt(sum.upBreachRatePct) + "%" : "—";
    qs("paDownBreach").textContent = sum.downBreachRatePct != null ? fmt(sum.downBreachRatePct) + "%" : "—";
    qs("paUpOvershoot").textContent = sum.avgUpOvershootPct != null ? fmt(sum.avgUpOvershootPct) + "%" : "—";
    qs("paDownOvershoot").textContent = sum.avgDownOvershootPct != null ? fmt(sum.avgDownOvershootPct) + "%" : "—";
    qs("paTailBias").textContent = sum.tailBias || "—";
    qs("paStockPrice").textContent = data.stock_price != null ? "$" + fmt(data.stock_price) : "—";

    /* IC structure */
    var tb = e1.tradeBuilder;
    var icSection = qs("phaseAIcSection");
    var icGrid = qs("phaseAIcGrid");
    if (tb && tb.totalCredit != null) {
      icSection.style.display = "";
      var putLeg = tb.put || {};
      var callLeg = tb.call || {};
      icGrid.innerHTML =
        '<div class="evalCard"><div class="evalCardLabel">Short Put</div><div class="evalCardValue">' + fmt(putLeg.shortStrike) + '</div><div class="evalCardCaption">strike</div></div>' +
        '<div class="evalCard"><div class="evalCardLabel">Short Call</div><div class="evalCardValue">' + fmt(callLeg.shortStrike) + '</div><div class="evalCardCaption">strike</div></div>' +
        '<div class="evalCard"><div class="evalCardLabel">Total Credit</div><div class="evalCardValue">$' + fmt(tb.totalCredit) + '</div><div class="evalCardCaption">IC premium collected</div></div>' +
        '<div class="evalCard"><div class="evalCardLabel">Expiration</div><div class="evalCardValue" style="font-size:14px;">' + (tb.expiration || "—") + '</div><div class="evalCardCaption">options expiry</div></div>';
      if (putLeg.longStrike != null || callLeg.longStrike != null) {
        icGrid.innerHTML +=
          '<div class="evalCard"><div class="evalCardLabel">Long Put</div><div class="evalCardValue">' + fmt(putLeg.longStrike) + '</div><div class="evalCardCaption">wing</div></div>' +
          '<div class="evalCard"><div class="evalCardLabel">Long Call</div><div class="evalCardValue">' + fmt(callLeg.longStrike) + '</div><div class="evalCardCaption">wing</div></div>';
      }
    } else {
      icSection.style.display = "none";
    }

    /* Playbook */
    _lastPhaseAData = data;
    renderPlaybook(data.playbook);
  }

  /* ── Playbook Renderer ────────────────────────────────────────────── */
  function renderPlaybook(pb) {
    var section = qs("playbookSection");
    var deskWrap = qs("pbDeskNotesWrap");
    var deskPanel = qs("pbDeskNotesPanel");
    var deskBtn = qs("pbDeskNotesBtn");
    var deskBtnText = qs("pbDeskNotesBtnText");
    deskPanel.style.display = "none";
    deskPanel.innerHTML = "";
    deskBtn.disabled = false;
    deskBtnText.textContent = "Get Full Playbook Brief";
    _rowPlaybookCache = {};

    if (!pb) {
      section.style.display = "";
      qs("pbQuickRefList").innerHTML = '<div style="color:var(--muted); font-style:italic;">Playbook unavailable — historical bar data could not be loaded. Try again when markets are open.</div>';
      qs("pbThresholds").style.display = "none";
      qs("pbScenarioBody").innerHTML = "";
      qs("pbMeta").textContent = "";
      deskWrap.style.display = "none";
      return;
    }
    section.style.display = "";

    if (!pb.scenarios || !pb.scenarios.length) {
      qs("pbQuickRefList").innerHTML = '<div style="color:var(--muted); font-style:italic;">Not enough historical data to build scenarios. Default: PASS on all outcomes.</div>';
      qs("pbThresholds").style.display = "none";
      qs("pbScenarioBody").innerHTML = "";
      var meta = pb.meta || {};
      qs("pbMeta").textContent = (meta.total_historical_events || 0) + " historical events analyzed — insufficient per-scenario data.";
      deskWrap.style.display = "none";
      return;
    }
    deskWrap.style.display = "";

    /* Quick reference */
    var qrList = qs("pbQuickRefList");
    var refs = pb.quick_reference || [];
    qrList.innerHTML = refs.map(function (line) {
      return '<div style="padding:2px 0;">' + escHtml(line) + '</div>';
    }).join("");

    /* Threshold prices */
    var thrEl = qs("pbThresholds");
    var thrGrid = qs("pbThresholdGrid");
    if (pb.thresholds && pb.thresholds.levels) {
      thrEl.style.display = "";
      var lvls = pb.thresholds.levels;
      var thrHtml = "";
      var multLabels = {"1.0x": "1.0× EM", "1.5x": "1.5× EM", "2.0x": "2.0× EM"};
      var multKeys = ["1.0x", "1.5x", "2.0x"];
      for (var mi = 0; mi < multKeys.length; mi++) {
        var mk = multKeys[mi];
        var lv = lvls[mk];
        if (!lv) continue;
        thrHtml +=
          '<div class="evalCard pbThresholdCard">' +
            '<div class="evalCardLabel">' + multLabels[mk] + ' (' + fmt(lv.gap_pct) + '%)</div>' +
            '<div style="display:flex; justify-content:center; gap:16px; margin-top:4px;">' +
              '<div><span class="pbThresholdUp">&#9650; $' + fmt(lv.up_price) + '</span></div>' +
              '<div><span class="pbThresholdDown">&#9660; $' + fmt(lv.down_price) + '</span></div>' +
            '</div>' +
          '</div>';
      }
      thrGrid.innerHTML = thrHtml;
    } else {
      thrEl.style.display = "none";
    }

    /* Scenario table */
    var tbody = qs("pbScenarioBody");
    var rows = "";
    var magLabels = {"contained": "< 1× EM", "extended": "1–1.5× EM", "extreme": "> 1.5× EM", "all": "Any size"};
    for (var si = 0; si < pb.scenarios.length; si++) {
      var s = pb.scenarios[si];
      var magClass = s.magnitude === "contained" ? "contained" : s.magnitude === "extended" ? "extended" : s.magnitude === "extreme" ? "extreme" : "all";
      var actClass = (s.action || "pass").toLowerCase();
      var confClass = (s.confidence || "low").toLowerCase();
      var cont1d = s.continuation_rate_1d != null ? Math.round(s.continuation_rate_1d * 100) + "%" : "—";
      var cont3d = s.continuation_rate_3d != null ? Math.round(s.continuation_rate_3d * 100) + "%" : "—";
      var cont5d = s.continuation_rate_5d != null ? Math.round(s.continuation_rate_5d * 100) + "%" : "—";
      var driftVal = s.avg_continuation_5d;
      var avgDrift = driftVal != null ? (driftVal > 0 ? "+" : "") + fmt(driftVal) + "%" : "—";
      var dirArrow = s.direction === "UP" ? "&#9650;" : "&#9660;";
      var dirColor = s.direction === "UP" ? "color:rgba(52,199,89,0.9)" : "color:rgba(255,59,48,0.85)";

      /* Volume confirmation badge */
      var volHtml = "—";
      if (s.high_vol_pct != null) {
        var vp = Math.round(s.high_vol_pct * 100);
        var volColor = vp >= 60 ? "color:rgba(52,199,89,0.9)" : vp >= 40 ? "color:rgba(255,149,0,0.9)" : "color:rgba(11,11,15,0.4)";
        volHtml = '<span style="' + volColor + '; font-weight:700;">' + vp + '%</span>';
        if (s.avg_rel_volume != null) volHtml += '<br><span style="font-size:10px; color:var(--muted);">' + fmt(s.avg_rel_volume) + '×</span>';
      }

      /* Optimal hold period */
      var holdHtml = s.optimal_hold_days != null ? s.optimal_hold_days + "d" : "—";

      rows +=
        '<tr class="pbScenarioRow" data-scenario-idx="' + si + '" style="cursor:pointer;">' +
          '<td><span class="pbMagLabel ' + magClass + '">' + (magLabels[s.magnitude] || escHtml(s.magnitude || "")) + '</span></td>' +
          '<td style="font-weight:700;' + dirColor + '">' + dirArrow + ' ' + escHtml(s.direction || "") + '</td>' +
          '<td>' + escHtml(s.structure || "") + '</td>' +
          '<td style="font-family:monospace;">' + (s.count || 0) + '</td>' +
          '<td style="font-family:monospace;">' + cont1d + '</td>' +
          '<td style="font-family:monospace;">' + cont3d + '</td>' +
          '<td style="font-family:monospace; font-weight:700;">' + cont5d + '</td>' +
          '<td style="font-family:monospace;">' + avgDrift + '</td>' +
          '<td style="font-family:monospace; text-align:center;">' + volHtml + '</td>' +
          '<td style="font-family:monospace; text-align:center; font-weight:700;">' + holdHtml + '</td>' +
          '<td><span class="pbActionBadge ' + actClass + '">' + escHtml(s.action || "PASS") + '</span></td>' +
          '<td><span class="pbConfBadge ' + confClass + '">' + escHtml(s.confidence || "") + '</span></td>' +
        '</tr>';
    }
    tbody.innerHTML = rows;

    /* Meta */
    var meta = pb.meta || {};
    qs("pbMeta").textContent =
      meta.total_historical_events + " historical events analyzed · " +
      meta.scenarios_computed + " scenarios computed · " +
      meta.actionable_scenarios + " actionable · " +
      "min " + meta.min_events_per_scenario + " events/scenario";
  }

  /* ── Row Playbook (per-scenario GPT-5.2 trade ticket) ────────────── */

  function buildRowPlaybookPayload(scenario) {
    var e1 = (_lastPhaseAData || {}).engine1 || {};
    var sum = e1.summary || {};
    var cur = e1.current || {};
    var bl = e1.baseline || {};
    var pb = (_lastPhaseAData || {}).playbook || {};
    return {
      scenario: scenario,
      context: {
        ticker: (_lastPhaseAData || {}).ticker || "",
        stock_price: cur.stockPrice || (_lastPhaseAData || {}).stock_price,
        em_pct: cur.impliedMovePct || cur.delayedImpliedMovePct,
        breach_stats: {
          breach_rate_pct: sum.breach_rate_pct,
          avg_above_breach_pct: sum.avg_above_breach_pct,
          events_used: sum.events_used,
          avg_ratio_realized_to_implied: bl.avg_ratio_realized_to_implied,
        },
        thresholds: pb.thresholds || {},
        strike_targets: e1.strikeTargets || {},
      },
    };
  }

  function renderRowPlaybook(data, detailTd) {
    var sections = [
      { key: "one_liner", title: null, cls: "rpOneLiner" },
      { key: "entry_plan", title: "Entry Plan", cls: "rpEntry", nested: true },
      { key: "exit_plan", title: "Exit Plan", cls: "rpExit", nested: true },
      { key: "risk_notes", title: "Risk Notes", cls: "" },
      { key: "historical_anchor", title: "Historical Anchor", cls: "" },
      { key: "what_if_wrong", title: "What If Wrong", cls: "" },
      { key: "gamma_read", title: "Gamma Read", cls: "" },
      { key: "desk_voice", title: "Desk Voice", cls: "rpDeskVoice" },
    ];

    var verdict = (data.verdict || "PASS").toUpperCase();
    var conviction = (data.conviction || "LOW").toUpperCase();
    var verdictClass = verdict === "CONTINUE" ? "continue" : verdict === "FADE" ? "fade" : "pass";
    var convClass = conviction === "HIGH" ? "high" : conviction === "MEDIUM" ? "medium" : "low";

    var html = '<div class="rpCard">';
    html += '<div class="rpHeader">';
    html += '<span class="pbActionBadge ' + verdictClass + '" style="font-size:13px; padding:5px 14px;">' + escHtml(verdict) + '</span>';
    html += '<span class="pbConfBadge ' + convClass + '" style="font-size:12px; margin-left:8px;">' + escHtml(conviction) + '</span>';
    if (data._source) html += '<span class="rpSource">GPT-5.2 Trade Ticket</span>';
    html += '</div>';

    for (var i = 0; i < sections.length; i++) {
      var sec = sections[i];
      var val = data[sec.key];
      if (!val) continue;

      if (sec.nested && typeof val === "object") {
        html += '<div class="rpSection">';
        if (sec.title) html += '<div class="rpSectionTitle">' + escHtml(sec.title) + '</div>';
        html += '<div class="rpNestedGrid">';
        var nestedKeys = Object.keys(val);
        for (var nk = 0; nk < nestedKeys.length; nk++) {
          var nLabel = nestedKeys[nk].replace(/_/g, " ");
          nLabel = nLabel.charAt(0).toUpperCase() + nLabel.slice(1);
          html += '<div class="rpNestedItem">';
          html += '<div class="rpNestedLabel">' + escHtml(nLabel) + '</div>';
          html += '<div class="rpNestedValue">' + escHtml(val[nestedKeys[nk]]) + '</div>';
          html += '</div>';
        }
        html += '</div></div>';
      } else {
        html += '<div class="rpSection ' + (sec.cls || "") + '">';
        if (sec.title) html += '<div class="rpSectionTitle">' + escHtml(sec.title) + '</div>';
        html += '<div class="rpText">' + escHtml(typeof val === "string" ? val : JSON.stringify(val)) + '</div>';
        html += '</div>';
      }
    }

    html += '</div>';
    detailTd.innerHTML = html;
  }

  function onScenarioRowClick(e) {
    var row = e.target.closest(".pbScenarioRow");
    if (!row) return;
    var idx = parseInt(row.getAttribute("data-scenario-idx"), 10);
    var pb = ((_lastPhaseAData || {}).playbook || {}).scenarios;
    if (!pb || !pb[idx]) return;

    var existing = row.nextElementSibling;
    if (existing && existing.classList.contains("pbRowDetail")) {
      existing.remove();
      row.classList.remove("pbRowActive");
      return;
    }

    document.querySelectorAll(".pbRowDetail").forEach(function (r) { r.remove(); });
    document.querySelectorAll(".pbRowActive").forEach(function (r) { r.classList.remove("pbRowActive"); });

    var scenario = pb[idx];
    var cacheKey = scenario.key || (scenario.magnitude + "_" + scenario.direction + "_" + scenario.structure);

    var detailRow = document.createElement("tr");
    detailRow.className = "pbRowDetail";
    var detailTd = document.createElement("td");
    detailTd.colSpan = 12;
    detailTd.className = "rpDetailCell";
    detailRow.appendChild(detailTd);

    row.classList.add("pbRowActive");
    row.parentNode.insertBefore(detailRow, row.nextSibling);

    if (_rowPlaybookCache[cacheKey]) {
      renderRowPlaybook(_rowPlaybookCache[cacheKey], detailTd);
      return;
    }

    detailTd.innerHTML = '<div class="rpLoading"><span class="rpDot"></span> Generating trade ticket with GPT-5.2\u2026</div>';

    if (_rowPlaybookAbort) _rowPlaybookAbort.abort();
    _rowPlaybookAbort = new AbortController();

    var payload = buildRowPlaybookPayload(scenario);

    fetch("/api/engine8/row-playbook", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: _rowPlaybookAbort.signal,
    })
      .then(function (r) {
        if (!r.ok) return r.json().then(function (d) { throw new Error(d.detail || r.statusText); });
        return r.json();
      })
      .then(function (data) {
        _rowPlaybookCache[cacheKey] = data;
        renderRowPlaybook(data, detailTd);
      })
      .catch(function (err) {
        if (err.name === "AbortError") return;
        detailTd.innerHTML = '<div class="rpError">Error: ' + escHtml(err.message) + '</div>';
      });
  }

  qs("pbScenarioBody").addEventListener("click", onScenarioRowClick);

  function escHtml(s) {
    var d = document.createElement("div");
    d.textContent = s;
    return d.innerHTML;
  }

  /* ── Phase B: Post-Earnings ──────────────────────────────────────── */
  function renderPhaseB(data) {
    phaseAEl.classList.add("hidden");
    resultsEl.classList.remove("hidden");

    /* Engine 1 outcome card */
    var e1s = data.engine1_summary || {};
    var outcomeSection = qs("e1OutcomeSection");
    var outcomeContent = qs("e1OutcomeContent");
    if (e1s.had_phase_a) {
      outcomeSection.style.display = "";
      var outcomeLabel = (e1s.trade_outcome || "unknown").replace(/_/g, " ");
      var outcomeColor = e1s.trade_outcome === "profitable" ? "rgba(52,199,89,0.9)" :
                         e1s.trade_outcome === "controlled_loss" ? "rgba(255,149,0,0.9)" :
                         e1s.trade_outcome === "breakdown" ? "rgba(255,59,48,0.9)" : "var(--muted)";
      var html = '<div style="display:flex; align-items:center; gap:12px; flex-wrap:wrap;">';
      html += '<span class="decisionBadge" style="background:' + outcomeColor.replace("0.9", "0.12") + '; border:1px solid ' + outcomeColor.replace("0.9", "0.30") + '; color:' + outcomeColor + ';">' + outcomeLabel.toUpperCase() + '</span>';
      if (e1s.expected_move_pct != null) html += '<span style="font-size:12px; color:var(--muted);">Expected move: ' + fmt(e1s.expected_move_pct) + '%</span>';
      if (e1s.breach_rate_pct != null) html += '<span style="font-size:12px; color:var(--muted);">Breach rate: ' + fmt(e1s.breach_rate_pct) + '%</span>';
      html += '</div>';
      outcomeContent.innerHTML = html;
    } else {
      outcomeSection.style.display = "";
      outcomeContent.innerHTML = '<div style="color:var(--muted); font-style:italic;">' + (e1s.message || "No pre-earnings setup found. Run Engine 8 before earnings to set up the lifecycle.") + '</div>';
    }

    /* Decision */
    var dec = data.decision || {};
    var decisionStr = (typeof dec === "string" ? dec : dec.decision || "PASS").toUpperCase();
    var badge = qs("decisionBadge");
    badge.textContent = decisionStr;
    badge.className = "decisionBadge " + decisionStr.toLowerCase();

    var dir = dec.direction || data.direction;
    qs("decisionDirection").textContent = dir ? (dir.toLowerCase() === "long" ? "Long" : "Short") : "—";
    var conf = dec.confidence_score != null ? dec.confidence_score : data.confidence;
    qs("decisionConfidence").textContent = conf != null ? "Confidence: " + Math.round(conf) + " / 100" : "";

    var rationale = "";
    if (dec.pass_reason) {
      var reasonMap = {
        "activation_failed": "Activation failed — earnings date or post-event data unavailable.",
        "insufficient_historical_sample": "Insufficient historical data for this ticker under similar conditions.",
        "regime_blocked": "Regime overlay blocked — volatility regime is too stressed for new directional trades.",
        "below_threshold": "Confidence below threshold — edge is not clear enough. PASS is correct here.",
        "tied_candidates": "CONTINUE and FADE scored equally — ambiguous signal, PASS is safest.",
      };
      rationale = reasonMap[dec.pass_reason] || dec.pass_reason.replace(/_/g, " ");
    }
    qs("decisionRationale").textContent = rationale;

    /* Snapshot */
    var snap = data.snapshot || {};
    qs("snapActualMove").textContent = snap.actual_move_pct != null ? fmt(snap.actual_move_pct) + "%" : "—";
    qs("snapEmMultiple").textContent = fmt(snap.move_vs_em) + "x";
    qs("snapAtrMultiple").textContent = fmt(snap.atr_multiple) + "x";
    qs("snapGapStructure").textContent = snap.gap_structure || "—";
    qs("snapIvCrush").textContent = snap.iv_crush_pct != null ? fmt(snap.iv_crush_pct) + "%" : "—";
    qs("snapSentiment").textContent = snap.sentiment || "—";

    /* Displacement */
    var prof = data.profile || {};
    qs("displaceMagnitude").textContent = prof.magnitude_em_label || "—";
    qs("displaceStructure").textContent = prof.structure_label || "—";
    qs("displaceContext").textContent = prof.context_label || "—";

    /* Historical */
    var hist = data.historical || {};
    var contProb = hist.continuation_prob_5d != null ? hist.continuation_prob_5d : hist.continuation_prob_3d;
    var revProb  = hist.reversion_prob_5d != null ? hist.reversion_prob_5d : hist.reversion_prob_3d;
    qs("histContinuation").textContent = contProb != null ? pct(contProb) : "—";
    qs("histReversion").textContent = revProb != null ? pct(revProb) : "—";
    qs("histMagnitude").textContent = hist.avg_continuation_magnitude != null ? fmt(hist.avg_continuation_magnitude) + "%" : "—";
    qs("histSample").textContent = hist.sample_size || "—";

    /* Trade profile */
    if (decisionStr !== "PASS") {
      qs("tradeDirection").textContent = dir ? (dir.toLowerCase() === "long" ? "Long" : "Short") : "—";
      qs("tradeRiskUnits").textContent = fmt(dec.risk_units, 1);
      qs("tradeHolding").textContent = (dec.holding_period_days || "1–5") + "d";
      qs("tradeEntry").textContent = dec.entry_preference || "—";
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

    var earningsDate = qs("earningsDate").value;
    if (!earningsDate) { statusEl.textContent = "Earnings date is required."; return; }

    var timingRadio = document.querySelector('input[name="timing"]:checked');
    if (!timingRadio) { statusEl.textContent = "Please select BMO or AMC."; return; }
    var timing = timingRadio.value;

    var params = new URLSearchParams();
    params.set("ticker", ticker);
    params.set("earnings_date", earningsDate);
    params.set("timing", timing);

    runBtn.disabled = true;
    runBtn.querySelector(".btnSpinner").style.display = "inline-block";
    statusEl.textContent = "Evaluating " + ticker + " (" + earningsDate + " " + timing + ")…";
    resultsEl.classList.add("hidden");
    phaseAEl.classList.add("hidden");

    if (window.RavenLoading) window.RavenLoading.show("Evaluating " + ticker + "…");

    fetch("/api/engine8/evaluate?" + params.toString())
      .then(function (r) {
        if (!r.ok) return r.json().then(function (d) { throw new Error(d.detail || r.statusText); });
        return r.json();
      })
      .then(function (data) {
        if (data.phase === "pre_earnings") {
          renderPhaseA(data);
          statusEl.textContent = "Pre-earnings analysis for " + ticker + " — earnings " + earningsDate + " (" + timing + ").";
        } else {
          renderPhaseB(data);
          statusEl.textContent = "Post-earnings evaluation for " + ticker + " — " + earningsDate + " (" + timing + ").";
        }
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

  /* ── Desk Notes (GPT-5.2 LLM) ─────────────────────────────────────── */
  var _deskNotesSections = [
    { key: "overall_thesis",   title: "Overall Thesis",      icon: "&#128202;" },
    { key: "iron_condor_view", title: "Iron Condor View",    icon: "&#9878;" },
    { key: "scenario_playbook",title: "Scenario Playbook",   icon: "&#128214;" },
    { key: "entry_timing",     title: "Entry Timing",        icon: "&#9654;" },
    { key: "risk_management",  title: "Risk Management",     icon: "&#128737;" },
    { key: "what_breaks_it",   title: "What Breaks It",      icon: "&#9888;" },
    { key: "desk_takeaway",    title: "Desk Takeaway",       icon: "&#128161;" },
  ];

  var _deskNotesAbort = null;

  function buildDeskNotesPayload(data) {
    var e1 = data.engine1 || {};
    var sum = e1.summary || {};
    var cur = e1.current || {};
    var bl = e1.baseline || {};
    var pb = data.playbook || {};
    return {
      ticker: data.ticker || "",
      earnings_date: data.earnings_date || "",
      timing: data.timing || "",
      breach_stats: {
        breach_rate_pct: sum.breach_rate_pct,
        avg_above_breach_pct: sum.avg_above_breach_pct,
        events_used: sum.events_used,
        events_found: sum.events_found,
        avg_ratio_realized_to_implied: bl.avg_ratio_realized_to_implied,
      },
      expected_move: {
        orats_em_eod_pct: cur.impliedMovePct,
        orats_em_delayed_pct: cur.delayedImpliedMovePct,
        straddle_em_pct: cur.straddleImpliedMovePct,
        stock_price: cur.stockPrice,
        strike_targets: e1.strikeTargets,
      },
      playbook: {
        scenarios: (pb.scenarios || []).slice(0, 16),
        thresholds: pb.thresholds,
        quick_reference: pb.quick_reference,
        meta: pb.meta,
      },
    };
  }

  function renderDeskNotes(data) {
    var panel = qs("pbDeskNotesPanel");
    var html = "";
    _deskNotesSections.forEach(function (sec) {
      var val = data[sec.key];
      if (!val) return;
      html += '<div class="pbDeskNoteSection">';
      html += '<div class="pbDeskNoteTitle">' + sec.icon + " " + sec.title + '</div>';
      html += '<div class="pbDeskNoteText">' + escHtml(val) + '</div>';
      html += '</div>';
    });
    if (data._source) {
      html += '<div class="pbDeskNoteSource">Generated by ' + escHtml(data._source) + '</div>';
    }
    panel.innerHTML = html;
    panel.style.display = "";
  }

  qs("pbDeskNotesBtn").addEventListener("click", function () {
    if (!_lastPhaseAData) return;

    var ticker = (_lastPhaseAData.ticker || "").toUpperCase();
    var cacheKey = ticker + "_" + (_lastPhaseAData.earnings_date || "");

    if (_deskNotesCache[cacheKey]) {
      renderDeskNotes(_deskNotesCache[cacheKey]);
      return;
    }

    var btn = qs("pbDeskNotesBtn");
    var btnText = qs("pbDeskNotesBtnText");
    btn.disabled = true;
    btnText.textContent = "Generating full playbook brief\u2026";

    var dotCount = 0;
    var dotInterval = setInterval(function () {
      dotCount = (dotCount + 1) % 4;
      btnText.textContent = "Generating full playbook brief" + ".".repeat(dotCount);
    }, 400);

    if (_deskNotesAbort) _deskNotesAbort.abort();
    _deskNotesAbort = new AbortController();

    var payload = buildDeskNotesPayload(_lastPhaseAData);

    fetch("/api/engine8/desk-notes", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ payload: payload }),
      signal: _deskNotesAbort.signal,
    })
      .then(function (r) {
        if (!r.ok) return r.json().then(function (d) { throw new Error(d.detail || r.statusText); });
        return r.json();
      })
      .then(function (data) {
        _deskNotesCache[cacheKey] = data;
        renderDeskNotes(data);
        btn.disabled = false;
        btnText.textContent = "Refresh Playbook Brief";
      })
      .catch(function (err) {
        if (err.name === "AbortError") return;
        qs("pbDeskNotesPanel").innerHTML = '<div style="color:rgba(255,59,48,0.8); font-size:13px;">Error: ' + escHtml(err.message) + '</div>';
        qs("pbDeskNotesPanel").style.display = "";
        btn.disabled = false;
        btnText.textContent = "Retry Playbook Brief";
      })
      .finally(function () {
        clearInterval(dotInterval);
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
