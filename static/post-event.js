/* ── Engine 8: Post-Event Trade Extension (Lifecycle) ───────────────────
   Frontend controller — handles Phase A (pre-earnings) and Phase B
   (post-earnings) rendering based on the API response.
   ──────────────────────────────────────────────────────────────────────── */
(function () {
  "use strict";

  const form       = document.getElementById("e8Form");
  const runBtn     = document.getElementById("runBtn");
  const statusEl   = document.getElementById("status");
  const resultsEl  = document.getElementById("results");
  const phaseAEl   = document.getElementById("phaseAResults");

  const _dummyEl = document.createElement("span");
  function qs(id) { return document.getElementById(id) || _dummyEl; }

  function setE8TickerLogo(ticker) {
    const img = document.getElementById("e8TickerLogo");
    if (!img) return;
    const t = String(ticker || "").trim().toUpperCase();
    if (!t) { img.classList.add("hidden"); img.removeAttribute("src"); return; }
    const src = "https://financialmodelingprep.com/image-stock/" + encodeURIComponent(t) + ".png";
    img.src = src;
    img.alt = t + " logo";
    img.classList.remove("hidden");
    img.onerror = function () { img.classList.add("hidden"); };
  }

  const tickerInput = document.getElementById("ticker");
  if (tickerInput) {
    setE8TickerLogo(tickerInput.value);
    tickerInput.addEventListener("input", function () { setE8TickerLogo(tickerInput.value); });
  }

  let _lastPhaseAData = null;
  const _deskNotesCache = {};
  let _rowPlaybookCache = {};
  let _rowPlaybookAbort = null;
  let _activationAbort = null;
  let _activationCache = null;
  function fmt(v, d) { return v == null ? "—" : Number(v).toFixed(d == null ? 2 : d); }
  function pct(v) { return v == null ? "—" : (Number(v) * 100).toFixed(1) + "%"; }

  /* ── Phase A: Pre-Earnings ───────────────────────────────────────── */
  function renderPhaseA(data) {
    phaseAEl.classList.remove("hidden");
    resultsEl.classList.add("hidden");

    const timing = data.timing || "UNK";
    const timingLabel = timing === "AMC" ? "After Market Close" : timing === "BMO" ? "Before Market Open" : "Timing TBD";
    qs("phaseATiming").textContent = data.earnings_date + " · " + timingLabel;
    qs("phaseACountdown").textContent = data.countdown_days != null ? data.countdown_days + " day" + (data.countdown_days !== 1 ? "s" : "") + " away" : "";

    const e1 = data.engine1 || {};
    const sum = e1.summary || {};
    const cur = e1.current || {};
    const em = e1.expectedMove || {};
    const st = e1.strikeTargets || {};
    const bl = e1.baseline || {};

    /* Core metrics row */
    const regime = e1.regime || {};
    qs("paRegimeLabel").textContent = regime.label || "—";

    /* ORATS EM (EOD + delayed) */
    const eodEmPct = cur.impliedMovePct;
    const delayedEmPct = cur.delayedImpliedMovePct;
    const avgImpliedPct = sum.avg_implied_all_pct;
    if (eodEmPct != null) {
      qs("paOratsEm").textContent = fmt(eodEmPct) + "%";
      qs("paOratsEmCaption").textContent = cur.asOfDate ? "As of: " + cur.asOfDate + " · EOD (used for breach history)" : "EOD (used for breach history)";
    } else if (avgImpliedPct != null) {
      qs("paOratsEm").textContent = fmt(avgImpliedPct) + "%";
      qs("paOratsEmCaption").textContent = "Avg implied across " + (sum.events_used || "—") + " events (EOD unavailable)";
    } else {
      qs("paOratsEm").textContent = "—";
      qs("paOratsEmCaption").textContent = "EOD (used for breach history)";
    }
    qs("paDelayedEm").textContent = delayedEmPct != null ? fmt(delayedEmPct) + "%" : "—";
    const delayedNote = cur.delayedUpdatedAt ? "Updated: " + cur.delayedUpdatedAt : cur.delayedTradeDate ? "As of: " + cur.delayedTradeDate : "";
    qs("paDelayedEmCaption").textContent = (delayedNote ? delayedNote + " · " : "") + "15-min delayed" + (delayedEmPct != null ? " · Used for strike targets" : "");

    /* Straddle EM */
    const stEmPct = em.expectedMovePct;
    const stEmDollars = em.expectedMoveDollars;
    const stEmExpiry = em.expiry ? String(em.expiry).slice(0, 10) : "";
    const stEmSource = em.source || "";
    qs("paStraddleEm").textContent = stEmPct != null ? fmt(stEmPct) + "%" : "—";
    const stCaption = [];
    if (stEmDollars != null) stCaption.push("$" + fmt(stEmDollars) + " pts");
    if (stEmExpiry) stCaption.push("Exp: " + stEmExpiry);
    if (stEmSource) stCaption.push(stEmSource === "live" ? "Live" : stEmSource === "eod" ? "EOD" : stEmSource);
    qs("paStraddleEmCaption").textContent = stCaption.length ? stCaption.join(" · ") : "ATM-forward straddle method";

    /* Strike Targets */
    qs("paStWhite").textContent = st && st.whitePct != null ? fmt(st.whitePct) + "%" : "—";
    qs("paStBlue").textContent = st && st.bluePct != null ? fmt(st.bluePct) + "%" : "—";
    qs("paStRed").textContent = st && st.redPct != null ? fmt(st.redPct) + "%" : "—";
    const stSource = st && st.emSource === "delayed" ? "15-min delayed EM" : "ORATS EOD EM";
    qs("paStrikeCaption").textContent = "Wing distance as % of spot (" + stSource + ").";

    /* Breach detail */
    qs("paBreach1x").textContent = sum.breach_rate_pct != null ? fmt(sum.breach_rate_pct) + "%" : "—";
    const hr = e1.holdRisk || {};
    qs("paBreach15x").textContent = hr.breach_1_5x != null ? fmt(hr.breach_1_5x * 100) + "%" : "—";
    qs("paBreach2x").textContent = hr.breach_2_0x != null ? fmt(hr.breach_2_0x * 100) + "%" : "—";
    qs("paStockPrice").textContent = data.stock_price != null ? "$" + fmt(data.stock_price) : "—";
    qs("paUpBreach").textContent = sum.upBreachRatePct != null ? fmt(sum.upBreachRatePct) + "%" : "—";
    qs("paDownBreach").textContent = sum.downBreachRatePct != null ? fmt(sum.downBreachRatePct) + "%" : "—";
    qs("paUpOvershoot").textContent = sum.avgUpOvershootPct != null ? fmt(sum.avgUpOvershootPct) + "%" : "—";
    qs("paDownOvershoot").textContent = sum.avgDownOvershootPct != null ? fmt(sum.avgDownOvershootPct) + "%" : "—";
    qs("paTailBias").textContent = sum.tailBias || "—";

    /* Breach detail row 3 */
    qs("paAvgRealizedImplied").textContent = bl.avg_ratio_realized_to_implied != null ? fmt(bl.avg_ratio_realized_to_implied) + "×" : "—";
    const evUsed = sum.events_used;
    const evFound = sum.events_found;
    qs("paEventsUsed").textContent = evUsed != null ? evUsed + (evFound != null ? " / " + evFound : "") : "—";
    const goNoGo = e1.goNoGo || {};
    const gateVal = (goNoGo.guidance || {}).tradeGate || goNoGo.tradeGate || "";
    const gateTxt = gateVal === "NO_TRADE" ? "No Trade" : gateVal === "CAUTION" ? "Caution" : gateVal === "OK" ? "OK" : "—";
    qs("paTradeGate").textContent = gateTxt;

    /* IC structure */
    const tb = e1.tradeBuilder;
    const icSection = qs("phaseAIcSection");
    const icGrid = qs("phaseAIcGrid");
    if (tb && tb.totalCredit != null) {
      icSection.style.display = "";
      const putLeg = tb.put || {};
      const callLeg = tb.call || {};
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
    _activationCache = null;
    renderPlaybook(data.playbook);

    /* Show Activation Scanner button if playbook has scenarios */
    const actWrap = qs("activationScanWrap");
    if (data.playbook && data.playbook.scenarios && data.playbook.scenarios.length > 0) {
      actWrap.style.display = "";
      qs("activationScanBtn").disabled = false;
      qs("activationScanBtnText").textContent = "Run Activation Scanner";
    } else {
      actWrap.style.display = "none";
    }
  }

  /* ── Playbook Renderer ────────────────────────────────────────────── */
  function renderPlaybook(pb) {
    const section = qs("playbookSection");
    const deskWrap = qs("pbDeskNotesWrap");
    const deskPanel = qs("pbDeskNotesPanel");
    const deskBtn = qs("pbDeskNotesBtn");
    const deskBtnText = qs("pbDeskNotesBtnText");
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
      const meta = pb.meta || {};
      qs("pbMeta").textContent = (meta.total_historical_events || 0) + " historical events analyzed — insufficient per-scenario data.";
      deskWrap.style.display = "none";
      return;
    }
    deskWrap.style.display = "";

    /* Quick reference */
    const qrList = qs("pbQuickRefList");
    const refs = pb.quick_reference || [];
    qrList.innerHTML = refs.map(function (line) {
      return '<div style="padding:2px 0;">' + escHtml(line) + '</div>';
    }).join("");

    /* Threshold prices */
    const thrEl = qs("pbThresholds");
    const thrGrid = qs("pbThresholdGrid");
    if (pb.thresholds && pb.thresholds.levels) {
      thrEl.style.display = "";
      const lvls = pb.thresholds.levels;
      let thrHtml = "";
      const multLabels = {"1.0x": "1.0× EM", "1.5x": "1.5× EM", "2.0x": "2.0× EM"};
      const multKeys = ["1.0x", "1.5x", "2.0x"];
      for (let mi = 0; mi < multKeys.length; mi++) {
        const mk = multKeys[mi];
        const lv = lvls[mk];
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
    const tbody = qs("pbScenarioBody");
    let rows = "";
    const magLabels = {"contained": "< 1× EM", "extended": "1–1.5× EM", "extreme": "> 1.5× EM", "all": "Any size"};
    for (let si = 0; si < pb.scenarios.length; si++) {
      const s = pb.scenarios[si];
      const magClass = s.magnitude === "contained" ? "contained" : s.magnitude === "extended" ? "extended" : s.magnitude === "extreme" ? "extreme" : "all";
      const actClass = (s.action || "pass").toLowerCase();
      const confClass = (s.confidence || "low").toLowerCase();
      const cont1d = s.continuation_rate_1d != null ? Math.round(s.continuation_rate_1d * 100) + "%" : "—";
      const cont3d = s.continuation_rate_3d != null ? Math.round(s.continuation_rate_3d * 100) + "%" : "—";
      const cont5d = s.continuation_rate_5d != null ? Math.round(s.continuation_rate_5d * 100) + "%" : "—";
      const driftVal = s.avg_continuation_5d;
      const avgDrift = driftVal != null ? (driftVal > 0 ? "+" : "") + fmt(driftVal) + "%" : "—";
      const dirArrow = s.direction === "UP" ? "&#9650;" : "&#9660;";
      const dirColor = s.direction === "UP" ? "color:rgba(52,199,89,0.9)" : "color:rgba(255,59,48,0.85)";

      /* Volume confirmation badge */
      let volHtml = "—";
      if (s.high_vol_pct != null) {
        const vp = Math.round(s.high_vol_pct * 100);
        const volColor = vp >= 60 ? "color:rgba(52,199,89,0.9)" : vp >= 40 ? "color:rgba(255,149,0,0.9)" : "color:rgba(11,11,15,0.4)";
        volHtml = '<span style="' + volColor + '; font-weight:700;">' + vp + '%</span>';
        if (s.avg_rel_volume != null) volHtml += '<br><span style="font-size:10px; color:var(--muted);">' + fmt(s.avg_rel_volume) + '×</span>';
      }

      /* Optimal hold period */
      const holdHtml = s.optimal_hold_days != null ? s.optimal_hold_days + "d" : "—";

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
    const meta = pb.meta || {};
    qs("pbMeta").textContent =
      meta.total_historical_events + " historical events analyzed · " +
      meta.scenarios_computed + " scenarios computed · " +
      meta.actionable_scenarios + " actionable · " +
      "min " + meta.min_events_per_scenario + " events/scenario";
  }

  /* ── Row Playbook (per-scenario GPT-5.2 trade ticket) ────────────── */

  function buildRowPlaybookPayload(scenario) {
    const e1 = (_lastPhaseAData || {}).engine1 || {};
    const sum = e1.summary || {};
    const cur = e1.current || {};
    const bl = e1.baseline || {};
    const pb = (_lastPhaseAData || {}).playbook || {};
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
    const sections = [
      { key: "one_liner", title: null, cls: "rpOneLiner" },
      { key: "entry_plan", title: "Entry Plan", cls: "rpEntry", nested: true },
      { key: "exit_plan", title: "Exit Plan", cls: "rpExit", nested: true },
      { key: "risk_notes", title: "Risk Notes", cls: "" },
      { key: "historical_anchor", title: "Historical Anchor", cls: "" },
      { key: "what_if_wrong", title: "What If Wrong", cls: "" },
      { key: "gamma_read", title: "Gamma Read", cls: "" },
      { key: "desk_voice", title: "Desk Voice", cls: "rpDeskVoice" },
    ];

    const verdict = (data.verdict || "PASS").toUpperCase();
    const conviction = (data.conviction || "LOW").toUpperCase();
    const verdictClass = verdict === "CONTINUE" ? "continue" : verdict === "FADE" ? "fade" : "pass";
    const convClass = conviction === "HIGH" ? "high" : conviction === "MEDIUM" ? "medium" : "low";

    let html = '<div class="rpCard">';
    html += '<div class="rpHeader">';
    html += '<span class="pbActionBadge ' + verdictClass + '" style="font-size:13px; padding:5px 14px;">' + escHtml(verdict) + '</span>';
    html += '<span class="pbConfBadge ' + convClass + '" style="font-size:12px; margin-left:8px;">' + escHtml(conviction) + '</span>';
    if (data._source) html += '<span class="rpSource">GPT-5.2 Trade Ticket</span>';
    html += '</div>';

    for (let i = 0; i < sections.length; i++) {
      const sec = sections[i];
      const val = data[sec.key];
      if (!val) continue;

      if (sec.nested && typeof val === "object") {
        html += '<div class="rpSection">';
        if (sec.title) html += '<div class="rpSectionTitle">' + escHtml(sec.title) + '</div>';
        html += '<div class="rpNestedGrid">';
        const nestedKeys = Object.keys(val);
        for (let nk = 0; nk < nestedKeys.length; nk++) {
          let nLabel = nestedKeys[nk].replace(/_/g, " ");
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
    const row = e.target.closest(".pbScenarioRow");
    if (!row) return;
    const idx = parseInt(row.getAttribute("data-scenario-idx"), 10);
    const pb = ((_lastPhaseAData || {}).playbook || {}).scenarios;
    if (!pb || !pb[idx]) return;

    const existing = row.nextElementSibling;
    if (existing && existing.classList.contains("pbRowDetail")) {
      existing.remove();
      row.classList.remove("pbRowActive");
      return;
    }

    document.querySelectorAll(".pbRowDetail").forEach(function (r) { r.remove(); });
    document.querySelectorAll(".pbRowActive").forEach(function (r) { r.classList.remove("pbRowActive"); });

    const scenario = pb[idx];
    const cacheKey = scenario.key || (scenario.magnitude + "_" + scenario.direction + "_" + scenario.structure);

    const detailRow = document.createElement("tr");
    detailRow.className = "pbRowDetail";
    const detailTd = document.createElement("td");
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

    const payload = buildRowPlaybookPayload(scenario);

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
    const d = document.createElement("div");
    d.textContent = s;
    return d.innerHTML;
  }

  /* ── Phase B: Post-Earnings ──────────────────────────────────────── */
  function renderPhaseB(data) {
    phaseAEl.classList.add("hidden");
    resultsEl.classList.remove("hidden");

    /* Engine 1 outcome card */
    const e1s = data.engine1_summary || {};
    const outcomeSection = qs("e1OutcomeSection");
    const outcomeContent = qs("e1OutcomeContent");
    if (e1s.had_phase_a) {
      outcomeSection.style.display = "";
      const outcomeLabel = (e1s.trade_outcome || "unknown").replace(/_/g, " ");
      const outcomeColor = e1s.trade_outcome === "profitable" ? "rgba(52,199,89,0.9)" :
                         e1s.trade_outcome === "controlled_loss" ? "rgba(255,149,0,0.9)" :
                         e1s.trade_outcome === "breakdown" ? "rgba(255,59,48,0.9)" : "var(--muted)";
      let html = '<div style="display:flex; align-items:center; gap:12px; flex-wrap:wrap;">';
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
    const dec = data.decision || {};
    const decisionStr = (typeof dec === "string" ? dec : dec.decision || "PASS").toUpperCase();
    const badge = qs("decisionBadge");
    badge.textContent = decisionStr;
    badge.className = "decisionBadge " + decisionStr.toLowerCase();

    const dir = dec.direction || data.direction;
    qs("decisionDirection").textContent = dir ? (dir.toLowerCase() === "long" ? "Long" : "Short") : "—";
    const conf = dec.confidence_score != null ? dec.confidence_score : data.confidence;
    qs("decisionConfidence").textContent = conf != null ? "Confidence: " + Math.round(conf) + " / 100" : "";

    let rationale = "";
    if (dec.pass_reason) {
      const reasonMap = {
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
    const snap = data.snapshot || {};
    qs("snapActualMove").textContent = snap.actual_move_pct != null ? fmt(snap.actual_move_pct) + "%" : "—";
    qs("snapEmMultiple").textContent = fmt(snap.move_vs_em) + "x";
    qs("snapAtrMultiple").textContent = fmt(snap.atr_multiple) + "x";
    qs("snapGapStructure").textContent = snap.gap_structure || "—";
    qs("snapIvCrush").textContent = snap.iv_crush_pct != null ? fmt(snap.iv_crush_pct) + "%" : "—";
    qs("snapSentiment").textContent = snap.sentiment || "—";

    /* Displacement */
    const prof = data.profile || {};
    qs("displaceMagnitude").textContent = prof.magnitude_em_label || "—";
    qs("displaceStructure").textContent = prof.structure_label || "—";
    qs("displaceContext").textContent = prof.context_label || "—";

    /* Historical */
    const hist = data.historical || {};
    const contProb = hist.continuation_prob_5d != null ? hist.continuation_prob_5d : hist.continuation_prob_3d;
    const revProb  = hist.reversion_prob_5d != null ? hist.reversion_prob_5d : hist.reversion_prob_3d;
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
    const ticker = qs("ticker").value.trim().toUpperCase();
    if (!ticker) { statusEl.textContent = "Please enter a ticker."; return; }

    const earningsDate = qs("earningsDate").value;
    if (!earningsDate) { statusEl.textContent = "Earnings date is required."; return; }

    const timingRadio = document.querySelector('input[name="timing"]:checked');
    if (!timingRadio) { statusEl.textContent = "Please select BMO or AMC."; return; }
    const timing = timingRadio.value;

    const params = new URLSearchParams();
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
  const _deskNotesSections = [
    { key: "overall_thesis",   title: "Overall Thesis",      icon: "&#128202;" },
    { key: "iron_condor_view", title: "Iron Condor View",    icon: "&#9878;" },
    { key: "scenario_playbook",title: "Scenario Playbook",   icon: "&#128214;" },
    { key: "entry_timing",     title: "Entry Timing",        icon: "&#9654;" },
    { key: "risk_management",  title: "Risk Management",     icon: "&#128737;" },
    { key: "what_breaks_it",   title: "What Breaks It",      icon: "&#9888;" },
    { key: "desk_takeaway",    title: "Desk Takeaway",       icon: "&#128161;" },
  ];

  let _deskNotesAbort = null;

  function buildDeskNotesPayload(data) {
    const e1 = data.engine1 || {};
    const sum = e1.summary || {};
    const cur = e1.current || {};
    const bl = e1.baseline || {};
    const pb = data.playbook || {};
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
    const panel = qs("pbDeskNotesPanel");
    let html = "";
    _deskNotesSections.forEach(function (sec) {
      const val = data[sec.key];
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

    const ticker = (_lastPhaseAData.ticker || "").toUpperCase();
    const cacheKey = ticker + "_" + (_lastPhaseAData.earnings_date || "");

    if (_deskNotesCache[cacheKey]) {
      renderDeskNotes(_deskNotesCache[cacheKey]);
      return;
    }

    const btn = qs("pbDeskNotesBtn");
    const btnText = qs("pbDeskNotesBtnText");
    btn.disabled = true;
    btnText.textContent = "Generating full playbook brief\u2026";

    let dotCount = 0;
    const dotInterval = setInterval(function () {
      dotCount = (dotCount + 1) % 4;
      btnText.textContent = "Generating full playbook brief" + ".".repeat(dotCount);
    }, 400);

    if (_deskNotesAbort) _deskNotesAbort.abort();
    _deskNotesAbort = new AbortController();

    const payload = buildDeskNotesPayload(_lastPhaseAData);

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

  /* ── Activation Scanner (Engine 8.5) ──────────────────────────────── */

  function renderActivationPopup(data) {
    const popup = qs("e8InsightPopup");
    const body = qs("e8InsightBody");

    const activation = (data.activation || "NO-GO").toUpperCase();
    const action = (data.action || "PASS").toUpperCase();
    const conviction = (data.conviction || "LOW").toUpperCase();
    const m = data._metrics || {};

    const actClass = activation === "GO" ? "go" : activation === "WAIT" ? "wait" : "no-go";
    const actionClass = action === "BUY" ? "buy" : action === "SHORT" ? "short" : "pass";
    const convClass = conviction === "HIGH" ? "high" : conviction === "MEDIUM" ? "medium" : "low";

    let html = '';

    html += '<div style="display:flex;align-items:center;gap:8px;margin-bottom:14px;">';
    html += '<span class="actBadge ' + actClass + '">' + escHtml(activation) + '</span>';
    html += '<span class="actBadge ' + actionClass + '" style="font-size:12px;padding:4px 12px;">' + escHtml(action) + '</span>';
    html += '<span class="actConvBadge ' + convClass + '">' + escHtml(conviction) + ' conviction</span>';
    html += '</div>';

    html += '<div class="actMetricBar">';
    html += '<div class="actMetricItem"><div class="actMetricLabel">Gap</div><div class="actMetricVal">' + (m.live_gap_pct != null ? (m.live_gap_pct > 0 ? "+" : "") + fmt(m.live_gap_pct) + "%" : "—") + '</div><div class="actMetricSub">' + escHtml((m.gap_vs_em != null ? fmt(m.gap_vs_em) + "x EM" : "") + " " + (m.gap_direction || "")) + '</div></div>';
    html += '<div class="actMetricItem"><div class="actMetricLabel">Structure</div><div class="actMetricVal">' + escHtml(m.structure_read || "—") + '</div><div class="actMetricSub">' + (m.retracement_pct != null ? fmt(m.retracement_pct, 0) + "% retrace" : "") + '</div></div>';
    html += '<div class="actMetricItem"><div class="actMetricLabel">Volume</div><div class="actMetricVal">' + escHtml(m.volume_read || "—") + '</div><div class="actMetricSub">' + (m.volume_ratio != null ? fmt(m.volume_ratio) + "x avg" : "") + '</div></div>';
    html += '<div class="actMetricItem"><div class="actMetricLabel">Price</div><div class="actMetricVal">$' + fmt(m.last_price) + '</div><div class="actMetricSub">open $' + fmt(m.session_open) + '</div></div>';
    html += '</div>';

    const lr = data.live_read || {};
    const lrKeys = ["gap", "structure", "volume", "iv_crush", "gamma"];
    let lrHas = false;
    for (let k = 0; k < lrKeys.length; k++) { if (lr[lrKeys[k]]) { lrHas = true; break; } }
    if (lrHas) {
      html += '<div class="e8InsightSection">';
      html += '<div class="e8InsightSectionTitle">Live Read</div>';
      for (let k = 0; k < lrKeys.length; k++) {
        let lbl = lrKeys[k].replace(/_/g, " ");
        lbl = lbl.charAt(0).toUpperCase() + lbl.slice(1);
        if (lr[lrKeys[k]]) {
          html += '<div style="margin-bottom:6px;"><span style="font-size:10px;font-weight:700;color:rgba(255,255,255,.45);text-transform:uppercase;">' + escHtml(lbl) + '</span><div class="e8InsightText">' + escHtml(lr[lrKeys[k]]) + '</div></div>';
        }
      }
      html += '</div>';
    }

    const tt = data.trade_ticket || {};
    const ttKeys = Object.keys(tt);
    if (ttKeys.length > 0) {
      html += '<div class="e8InsightSection">';
      html += '<div class="e8InsightSectionTitle">Trade Ticket</div>';
      html += '<div class="actTicketGrid">';
      for (let t = 0; t < ttKeys.length; t++) {
        let ttLabel = ttKeys[t].replace(/_/g, " ");
        ttLabel = ttLabel.charAt(0).toUpperCase() + ttLabel.slice(1);
        html += '<div class="actTicketItem"><div class="actTicketLabel">' + escHtml(ttLabel) + '</div><div class="actTicketVal">' + escHtml(tt[ttKeys[t]]) + '</div></div>';
      }
      html += '</div></div>';
    }

    if (data.desk_note) {
      html += '<div class="e8InsightSection">';
      html += '<div class="e8InsightSectionTitle">Desk Note</div>';
      html += '<div class="e8InsightText" style="font-style:italic;color:rgba(255,255,255,.75);">' + escHtml(data.desk_note) + '</div>';
      html += '</div>';
    }

    if (data._source) {
      html += '<div style="margin-top:12px;font-size:9px;color:rgba(255,255,255,.3);text-align:right;">Generated by ' + escHtml(data._source) + ' · Engine 8.5 Activation Scanner</div>';
    }

    body.innerHTML = html;
    qs("e8InsightTitle").textContent = "Activation Scanner — " + ((_lastPhaseAData || {}).ticker || "").toUpperCase();

    popup.style.display = "block";
    popup.style.top = "80px";
    popup.style.right = "24px";
    popup.style.left = "auto";
  }

  function initPopupDrag() {
    const popup = qs("e8InsightPopup");
    const header = qs("e8InsightHeader");
    let dragging = false, startX = 0, startY = 0, origX = 0, origY = 0;

    header.addEventListener("mousedown", function (e) {
      if (e.target.closest(".e8InsightClose")) return;
      dragging = true;
      popup.classList.add("isDragging");
      const rect = popup.getBoundingClientRect();
      startX = e.clientX; startY = e.clientY;
      origX = rect.left; origY = rect.top;
      e.preventDefault();
    });
    document.addEventListener("mousemove", function (e) {
      if (!dragging) return;
      const dx = e.clientX - startX, dy = e.clientY - startY;
      popup.style.left = (origX + dx) + "px";
      popup.style.top = (origY + dy) + "px";
      popup.style.right = "auto";
    });
    document.addEventListener("mouseup", function () {
      if (dragging) { dragging = false; popup.classList.remove("isDragging"); }
    });

    qs("e8InsightClose").addEventListener("click", function () {
      popup.style.display = "none";
    });
  }
  initPopupDrag();

  qs("activationScanBtn").addEventListener("click", function () {
    if (!_lastPhaseAData) return;

    const ticker = (_lastPhaseAData.ticker || "").toUpperCase();
    if (!ticker) return;

    _activationCache = null;

    const btn = qs("activationScanBtn");
    const btnText = qs("activationScanBtnText");
    btn.disabled = true;
    btnText.textContent = "Scanning live market data\u2026";

    let dotCount = 0;
    const dotInterval = setInterval(function () {
      dotCount = (dotCount + 1) % 4;
      btnText.textContent = "Scanning live market data" + ".".repeat(dotCount);
    }, 400);

    if (_activationAbort) _activationAbort.abort();
    _activationAbort = new AbortController();

    const payload = {
      ticker: ticker,
      earnings_date: _lastPhaseAData.earnings_date || "",
      timing: _lastPhaseAData.timing || "",
      phase_a: _lastPhaseAData,
    };

    fetch("/api/engine8/activation-scan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: _activationAbort.signal,
    })
      .then(function (r) {
        if (!r.ok) return r.json().then(function (d) { throw new Error(d.detail || r.statusText); });
        return r.json();
      })
      .then(function (data) {
        _activationCache = data;
        renderActivationPopup(data);
        btn.disabled = false;
        btnText.textContent = "Re-scan (refresh live data)";
      })
      .catch(function (err) {
        if (err.name === "AbortError") return;
        const popup = qs("e8InsightPopup");
        qs("e8InsightTitle").textContent = "Activation Scanner — Error";
        qs("e8InsightBody").innerHTML = '<div style="color:rgba(255,59,48,0.8);font-size:13px;padding:16px;">Error: ' + escHtml(err.message) + '</div>';
        popup.style.display = "block";
        popup.style.top = "80px";
        popup.style.right = "24px";
        popup.style.left = "auto";
        btn.disabled = false;
        btnText.textContent = "Retry Activation Scanner";
      })
      .finally(function () {
        clearInterval(dotInterval);
      });
  });

  /* ── Tooltip behaviour ─────────────────────────────────────────────── */
  document.addEventListener("click", function (e) {
    const btn = e.target.closest(".tipBtn");
    if (btn) {
      e.stopPropagation();
      const panel = btn.nextElementSibling;
      const open = panel.classList.contains("open");
      document.querySelectorAll(".tipPanel.open").forEach(function (p) { p.classList.remove("open"); });
      if (!open) panel.classList.add("open");
      return;
    }
    if (!e.target.closest(".tipPanel")) {
      document.querySelectorAll(".tipPanel.open").forEach(function (p) { p.classList.remove("open"); });
    }
  });
})();
