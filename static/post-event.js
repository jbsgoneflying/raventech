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

    var guidance = regime.guidance || {};
    var gateRaw = guidance.tradeGate || "";
    var gateLabel = gateRaw === "NO_TRADE" ? "No Trade" : gateRaw === "CAUTION" ? "Caution" : gateRaw === "OK" ? "OK" : "—";
    qs("paGoNoGo").textContent = gateLabel;

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
