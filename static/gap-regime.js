/* ── Engine 13 — Gap Regime Scanner ─────────────────────────────────────── */
"use strict";

var lastPayload = null;

(function () {
  var $ = function (id) { return document.getElementById(id); };

  // ── Helpers ────────────────────────────────────────────────────────────

  function fetchJson(url, opts) {
    return fetch(url, opts).then(function (r) {
      if (r.redirected && r.url.indexOf("/login") !== -1) throw new Error("Session expired — please refresh.");
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    });
  }

  function pctColor(v) {
    if (v == null) return "var(--muted)";
    return v > 0 ? "var(--green)" : v < 0 ? "var(--red)" : "var(--muted)";
  }

  function fmtPct(v, decimals) {
    if (v == null) return "\u2014";
    var d = decimals != null ? decimals : 2;
    var prefix = v > 0 ? "+" : "";
    return prefix + v.toFixed(d) + "%";
  }

  function card(label, value, caption) {
    return '<div class="e13Card">' +
      '<div class="e13CardLabel">' + label + '</div>' +
      '<div class="e13CardValue">' + value + '</div>' +
      (caption ? '<div class="e13CardCaption">' + caption + '</div>' : '') +
      '</div>';
  }

  function badge(text, cls) {
    return '<span class="e13Badge e13Badge--' + cls + '">' + text + '</span>';
  }


  // ── Scan ───────────────────────────────────────────────────────────────

  function runScan() {
    $("status").textContent = "Running Engine 13 gap scan\u2026";
    $("status").className = "status isRunning";
    $("results").classList.add("hidden");
    $("runBtn").disabled = true;
    if (typeof RavenLoading !== "undefined") RavenLoading.show();

    var threshold = parseFloat($("gapThreshold").value) || 1.5;
    var url = "/api/engine13/scan?gap_threshold=" + threshold;

    fetchJson(url)
      .then(function (data) {
        lastPayload = data;
        renderAll(data);
        $("results").classList.remove("hidden");
        $("status").textContent = "Scan complete";
        $("status").className = "status isOk";
      })
      .catch(function (err) {
        $("status").textContent = "Failed: " + err.message;
        $("status").className = "status isError";
      })
      .finally(function () {
        $("runBtn").disabled = false;
        if (typeof RavenLoading !== "undefined") RavenLoading.hide();
      });
  }


  // ── Render orchestrator ────────────────────────────────────────────────

  function renderAll(data) {
    renderGapCards(data.gap || {});
    renderFragility(data.catalystFragility || {});
    renderScenarios(data.scenarios || {});
    renderHistorical(data.historicalAnalogues || {}, data.geopoliticalAnalogues);
    renderOptions(data.optionsMicrostructure || {});
    renderTechVix(data.technicals || {}, data.vixBehaviour || {});
    $("advisorPanel").innerHTML = "";
  }


  // ── Gap header cards ───────────────────────────────────────────────────

  function renderGapCards(gap) {
    var el = $("gapCards");
    if (!gap.enabled) {
      el.innerHTML = card("Gap", "No gap detected", "SPX opened near prior close");
      return;
    }

    var dirBadge = gap.direction === "up"
      ? badge("UP", "green")
      : badge("DOWN", "red");

    var pctBadge = gap.percentileRank > 95 ? badge("P" + gap.percentileRank, "red")
      : gap.percentileRank > 80 ? badge("P" + gap.percentileRank, "amber")
      : badge("P" + gap.percentileRank, "muted");

    el.innerHTML = [
      card("SPX Gap",
        fmtPct(gap.gapPct, 2) + " " + dirBadge,
        "Prev close: " + (gap.prevClose || "\u2014") + " \u00b7 Open: " + (gap.todayOpen || "\u2014")),
      card("Gap Percentile",
        gap.percentileRank + "th " + pctBadge,
        "Rank vs 5-year daily gaps"),
      card("Live Price",
        gap.livePrice ? gap.livePrice.toLocaleString() : "\u2014",
        gap.gapFillPct != null ? "Gap fill: " + gap.gapFillPct + "% intraday" : "Intraday gap fill: pending"),
      card("Catalyst",
        gap.catalystTag ? '<span style="font-size:16px;font-weight:800">' + gap.catalystTag + '</span>' : "\u2014",
        "From Daily Market State themes"),
    ].join("");
  }


  // ── Catalyst Fragility ──────────────────────────────────────────────────

  function renderFragility(frag) {
    var compEl = $("fragilityComposite");
    var gridEl = $("fragilitySubGrid");
    var factEl = $("fragilityFactors");

    if (!frag.enabled) {
      compEl.innerHTML = "";
      gridEl.innerHTML = "";
      factEl.innerHTML = "";
      return;
    }

    var score = frag.score || 0;
    var label = frag.label || "UNKNOWN";
    var catalystType = frag.catalystType || "unknown";

    function fragColor(s) {
      if (s <= 30) return "var(--green)";
      if (s <= 50) return "var(--amber)";
      return "var(--red)";
    }

    function fragBadgeCls(s) {
      if (s <= 30) return "green";
      if (s <= 50) return "amber";
      return "red";
    }

    var isExtreme = score > 70;
    compEl.innerHTML = '<div class="e13FragComposite' + (isExtreme ? ' extreme' : '') +
      '" style="border-left:4px solid ' + fragColor(score) + '">' +
      '<div>' +
        '<div class="e13FragScore" style="color:' + fragColor(score) + '">' + score.toFixed(0) + '</div>' +
        '<div class="e13FragBar"><div class="e13FragBarFill" style="width:' + score + '%;background:' + fragColor(score) + '"></div></div>' +
      '</div>' +
      '<div class="e13FragMeta">' +
        '<div class="e13FragLabel" style="color:' + fragColor(score) + '">' + label + ' Fragility</div>' +
        '<div class="e13FragType">Catalyst: ' + catalystType.replace(/_/g, " ") + '</div>' +
      '</div>' +
      '<div>' + badge(score.toFixed(0) + "/100", fragBadgeCls(score)) + '</div>' +
    '</div>';

    var SUB_LABELS = {
      optionsConviction: "Options Conviction",
      crossAssetConfirmation: "Cross-Asset Confirmation",
      historicalDurability: "Historical Durability",
      headlineMomentum: "Headline Momentum",
      priceActionQuality: "Price Action Quality",
    };

    var components = frag.components || {};
    var subHtml = "";
    var keys = ["optionsConviction", "crossAssetConfirmation", "historicalDurability", "headlineMomentum", "priceActionQuality"];
    keys.forEach(function (key) {
      var comp = components[key];
      if (!comp) return;
      var s = comp.score || 0;
      var sigs = comp.signals || [];
      var sigHtml = sigs.map(function (sig) {
        return '<div class="e13FragSignal">' + sig + '</div>';
      }).join("");

      subHtml += '<div class="e13FragSub">' +
        '<div class="e13FragSubName">' + (SUB_LABELS[key] || key) + '</div>' +
        '<div class="e13FragSubScore" style="color:' + fragColor(s) + '">' + s.toFixed(0) + '</div>' +
        '<div class="e13FragSubBar"><div class="e13FragSubBarFill" style="width:' + s + '%;background:' + fragColor(s) + '"></div></div>' +
        '<div class="e13FragSignals">' + sigHtml + '</div>' +
      '</div>';
    });
    gridEl.innerHTML = subHtml;

    var factors = frag.dominantFactors || [];
    if (factors.length) {
      factEl.innerHTML = factors.map(function (f) {
        return '<span class="e13ModTag" style="background:rgba(255,59,48,0.06);color:var(--red)">' + f + '</span>';
      }).join("");
    } else {
      factEl.innerHTML = "";
    }
  }


  // ── Scenario probabilities ─────────────────────────────────────────────

  function renderScenarios(sc) {
    var panel = $("scenarioPanel");
    var mods = $("modifiers");
    if (!sc.enabled) {
      panel.innerHTML = '<p style="color:var(--muted);padding:12px">Scenarios unavailable</p>';
      mods.innerHTML = "";
      return;
    }

    function scenarioBlock(name, pct, barCls, dominant) {
      var color = barCls === "cont" ? "var(--green)" : barCls === "rev" ? "var(--red)" : "var(--amber)";
      return '<div class="e13Scenario ' + (dominant ? "dominant" : "") + '">' +
        '<div class="e13ScenarioName">' + name + '</div>' +
        '<div class="e13ScenarioProb" style="color:' + color + '">' + pct.toFixed(1) + '%</div>' +
        '<div class="e13ScenarioBar"><div class="e13ScenarioBarFill ' + barCls + '" style="width:' + pct + '%"></div></div>' +
        '</div>';
    }

    panel.innerHTML = [
      scenarioBlock("Continuation", sc.continuation || 0, "cont", sc.dominantScenario === "continuation"),
      scenarioBlock("Consolidation", sc.consolidation || 0, "cons", sc.dominantScenario === "consolidation"),
      scenarioBlock("Reversion", sc.reversion || 0, "rev", sc.dominantScenario === "reversion"),
    ].join("");

    var tags = [];
    if (sc.confidence) {
      tags.push(badge("Confidence: " + sc.confidence + "%", sc.confidence > 65 ? "green" : sc.confidence > 40 ? "amber" : "red"));
    }
    if (sc.modifiers && sc.modifiers.length) {
      sc.modifiers.forEach(function (m) { tags.push('<span class="e13ModTag">' + m + '</span>'); });
    }
    if (sc.expectedRangeD5) {
      var r = sc.expectedRangeD5;
      tags.push('<span class="e13ModTag">D+5 range: ' + fmtPct(r.p25) + ' to ' + fmtPct(r.p75) + ' (med ' + fmtPct(r.median) + ')</span>');
    }
    mods.innerHTML = tags.join("");
  }


  // ── Historical analogues ───────────────────────────────────────────────

  function renderHistorical(hist, geo) {
    var statsEl = $("histStats");
    var tableEl = $("histTable");
    var geoSection = $("geoSection");
    var geoTable = $("geoTable");

    if (!hist.enabled || !hist.count) {
      statsEl.innerHTML = card("Analogues", "0", "No gaps above threshold found in 5-year history");
      tableEl.innerHTML = "";
      geoSection.style.display = "none";
      return;
    }

    var od = hist.outcomeDistribution || {};
    statsEl.innerHTML = [
      card("Analogues Found",
        '<span style="font-size:28px">' + hist.count + '</span>',
        "Gaps \u2265 " + (hist.thresholdPct || 1.5) + "% (" + (hist.directionFilter || "all") + " direction)"),
      card("Outcome Split",
        "C " + (od.continuation || 0) + "% \u00b7 S " + (od.consolidation || 0) + "% \u00b7 R " + (od.reversion || 0) + "%",
        "Continuation / Consolidation / Reversion"),
      card("Median Gap Fill",
        hist.medianIntradayGapFill != null ? hist.medianIntradayGapFill + "%" : "\u2014",
        "Intraday fill on gap day"),
      card("Median D+5 Return",
        hist.stats && hist.stats.d5 ? fmtPct(hist.stats.d5.median) : "\u2014",
        hist.stats && hist.stats.d5 ? "Range: " + fmtPct(hist.stats.d5.p25) + " to " + fmtPct(hist.stats.d5.p75) : ""),
    ].join("");

    var events = hist.events || [];
    if (!events.length) { tableEl.innerHTML = ""; }
    else {
      var rows = events.map(function (e) {
        var fr = e.forwardReturns || {};
        var oc = e.outcome;
        var ocBadge = oc === "continuation" ? badge("CONT", "green")
          : oc === "reversion" ? badge("REV", "red")
          : badge("CONS", "amber");
        return '<tr>' +
          '<td>' + (e.date || "\u2014") + '</td>' +
          '<td style="color:' + pctColor(e.gapPct) + '">' + fmtPct(e.gapPct) + '</td>' +
          '<td style="color:' + pctColor(fr.d1) + '">' + fmtPct(fr.d1) + '</td>' +
          '<td style="color:' + pctColor(fr.d3) + '">' + fmtPct(fr.d3) + '</td>' +
          '<td style="color:' + pctColor(fr.d5) + '">' + fmtPct(fr.d5) + '</td>' +
          '<td>' + ocBadge + '</td>' +
          '<td>' + (e.intradayGapFill != null ? e.intradayGapFill + "%" : "\u2014") + '</td>' +
          '</tr>';
      }).join("");

      tableEl.innerHTML = '<table class="e13Table">' +
        '<thead><tr><th>Date</th><th>Gap</th><th>D+1</th><th>D+3</th><th>D+5</th><th>Outcome</th><th>Gap Fill</th></tr></thead>' +
        '<tbody>' + rows + '</tbody></table>';
    }

    if (geo && geo.length) {
      geoSection.style.display = "block";
      var geoRows = geo.slice(0, 5).map(function (e) {
        return '<tr>' +
          '<td>' + (e.event_date || "\u2014") + '</td>' +
          '<td>' + (e.description || "\u2014") + '</td>' +
          '<td style="color:' + pctColor(e.spx_gap_pct) + '">' + fmtPct(e.spx_gap_pct) + '</td>' +
          '<td>' + (e.outcome_class || "\u2014") + '</td>' +
          '<td>' + (e.similarity_distance != null ? e.similarity_distance.toFixed(2) : "\u2014") + '</td>' +
          '</tr>';
      }).join("");

      geoTable.innerHTML = '<table class="e13Table">' +
        '<thead><tr><th>Date</th><th>Event</th><th>SPX Gap</th><th>Outcome</th><th>Similarity</th></tr></thead>' +
        '<tbody>' + geoRows + '</tbody></table>';
    } else {
      geoSection.style.display = "none";
    }
  }


  // ── Options microstructure ─────────────────────────────────────────────

  function renderOptions(opts) {
    var el = $("optionsCards");
    var cards = [];

    var dg = opts.dealerGamma || {};
    if (dg.netGammaSign) {
      var signBadge = dg.netGammaSign === "positive" ? badge("POSITIVE", "green") : badge("NEGATIVE", "red");
      cards.push(card("Dealer Gamma", signBadge,
        "Magnitude: " + (dg.magnitudeBucket || "\u2014") + " \u00b7 Net GEX: " + (dg.netGex != null ? Math.round(dg.netGex).toLocaleString() : "\u2014")));
    } else {
      cards.push(card("Dealer Gamma", "\u2014", "No live options data"));
    }

    var sk = opts.skew || {};
    if (sk.label) {
      var skBadge = sk.label.indexOf("extreme") !== -1 ? badge(sk.label, "red")
        : sk.label.indexOf("elevated") !== -1 ? badge(sk.label, "amber")
        : badge(sk.label, "muted");
      cards.push(card("25\u0394 Skew",
        sk.skew25d != null ? (sk.skew25d * 100).toFixed(1) + " vol pts" : "\u2014",
        "Put/Call ratio: " + (sk.putCallRatio != null ? sk.putCallRatio.toFixed(3) : "\u2014") + " " + skBadge));
    } else {
      cards.push(card("25\u0394 Skew", "\u2014", "Vol surface unavailable"));
    }

    var ts = opts.termStructure || {};
    if (ts.label) {
      var tsBadge = ts.label === "backwardation" ? badge(ts.label, "red")
        : ts.label === "contango" ? badge(ts.label, "green")
        : badge(ts.label, "muted");
      cards.push(card("IV Term Structure", tsBadge,
        "Slope: " + (ts.slope != null ? ts.slope.toFixed(4) : "\u2014")));
    } else {
      cards.push(card("IV Term Structure", "\u2014", ""));
    }

    var uf = opts.unusualFlow || {};
    if (uf.totalSignals != null) {
      var sentBadge = uf.netSentiment === "bullish" ? badge("BULLISH", "green")
        : uf.netSentiment === "bearish" ? badge("BEARISH", "red")
        : badge("MIXED", "muted");
      cards.push(card("Unusual Flow", uf.totalSignals + " signals " + sentBadge,
        "Calls: " + (uf.calls || 0) + " \u00b7 Puts: " + (uf.puts || 0) + " \u00b7 Sweeps: " + (uf.sweeps || 0)));
    } else {
      cards.push(card("Unusual Flow", "\u2014", "No Benzinga signals"));
    }

    el.innerHTML = cards.join("");
  }


  // ── Technicals + VIX ───────────────────────────────────────────────────

  function renderTechVix(tech, vix) {
    var el = $("techVixCards");
    var cards = [];

    if (tech.enabled && tech.ema) {
      var emas = tech.ema;
      var px = tech.livePrice || tech.lastDailyClose;
      var parts = [];
      var entries = Object.entries(emas)
        .filter(function (kv) { return kv[1] != null && kv[0].indexOf("ema") === 0; })
        .sort(function (a, b) { return parseInt(a[0].replace("ema", "")) - parseInt(b[0].replace("ema", "")); });
      entries.forEach(function (kv) { parts.push(kv[0].replace("ema", "") + ": " + kv[1].toFixed(0)); });
      cards.push(card("EMA Stack",
        px ? px.toLocaleString(undefined, { maximumFractionDigits: 0 }) : "\u2014",
        parts.join(" \u00b7 ") || "No EMA data"));
    }

    var rsi = tech.rsi || {};
    if (rsi.value != null) {
      var rsiBadge = rsi.value > 70 ? badge("OVERBOUGHT", "red")
        : rsi.value < 30 ? badge("OVERSOLD", "green")
        : badge("NEUTRAL", "muted");
      cards.push(card("RSI (14)", rsi.value.toFixed(1) + " " + rsiBadge, ""));
    }

    var bb = tech.bollinger || {};
    if (bb.enabled !== false && bb.upper != null) {
      cards.push(card("Bollinger Bands",
        (bb.lower ? bb.lower.toFixed(0) : "\u2014") + " \u2014 " + (bb.upper ? bb.upper.toFixed(0) : "\u2014"),
        "Mid: " + (bb.mid ? bb.mid.toFixed(0) : "\u2014") + " \u00b7 Width: " + (bb.width != null ? (bb.width * 100).toFixed(1) + "%" : "\u2014")));
    }

    if (vix.enabled) {
      var vixBadge = vix.changePct < -10 ? badge("CRUSHED", "green")
        : vix.changePct < -3 ? badge("DOWN", "green")
        : vix.changePct > 10 ? badge("SPIKED", "red")
        : vix.changePct > 3 ? badge("UP", "red")
        : badge("FLAT", "muted");
      cards.push(card("VIX", vix.vixNow + " " + vixBadge,
        "Prev: " + vix.prevClose + " \u00b7 Change: " + fmtPct(vix.changePct) + " \u00b7 20d MA: " + vix.ma20 +
        (vix.snapback ? ' \u00b7 <strong style="color:var(--amber)">Snapback detected</strong>' : "")));
      cards.push(card("VIX Percentile", vix.percentileRank + "th",
        (vix.aboveMa20 ? "Above" : "Below") + " 20-day MA (" + vix.ma20 + ")"));
    }

    el.innerHTML = cards.join("");
  }


  // ── Advisor ────────────────────────────────────────────────────────────

  function runAdvisor() {
    var btn = $("advisorBtn");
    var panel = $("advisorPanel");

    btn.disabled = true;
    panel.innerHTML = '<div class="e13Advisor"><div style="color:var(--muted);font-size:13px;font-weight:600">Running desk analysis\u2026</div></div>';

    var body = lastPayload ? { scanPayload: lastPayload } : {};
    var shortCall = parseFloat($("shortCall").value);
    var shortPut = parseFloat($("shortPut").value);
    var icExpiry = $("icExpiry").value;
    if (shortCall || shortPut || icExpiry) {
      body.position = {};
      if (shortCall) body.position.shortCallStrike = shortCall;
      if (shortPut) body.position.shortPutStrike = shortPut;
      if (icExpiry) body.position.expirationDate = icExpiry;
    }
    fetchJson("/api/engine13/advisor", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })
      .then(function (data) { renderAdvisor(data.advisor || {}); })
      .catch(function (err) {
        panel.innerHTML = '<div class="e13Advisor" style="border-left-color:var(--red)">' +
          '<div style="color:var(--red);font-weight:700">' + (err.message || "Advisor failed") + '</div></div>';
      })
      .finally(function () { btn.disabled = false; });
  }

  function renderAdvisor(adv) {
    var panel = $("advisorPanel");
    if (adv._fallback_reason) {
      panel.innerHTML = '<div class="e13Advisor" style="border-left-color:var(--amber)">' +
        '<div style="color:var(--amber);font-weight:700">Advisor fallback: ' + adv._fallback_reason + '</div></div>';
      return;
    }

    var v = (adv.verdict || "HOLD").toUpperCase();
    var vCls = v === "HOLD" ? "hold" : v === "ROLL" ? "roll" : "adjust";
    var conf = adv.confidence || 0;

    function section(label, body) {
      if (!body) return "";
      return '<div class="e13AdvisorSection">' +
        '<div class="e13AdvisorSectionLabel">' + label + '</div>' +
        '<div class="e13AdvisorSectionBody">' + body + '</div></div>';
    }

    var borderColor = v === "HOLD" ? "var(--green)" : v === "ROLL" ? "var(--red)" : "var(--amber)";
    panel.innerHTML = '<div class="e13Advisor" style="border-left-color:' + borderColor + '">' +
      '<div class="e13AdvisorHeader">' +
        '<div class="e13AdvisorTitle">Desk Note</div>' +
        '<div>' +
          '<span class="e13VerdictBadge ' + vCls + '">' + v + '</span> ' +
          badge("Confidence: " + conf + "%", conf > 65 ? "green" : conf > 40 ? "amber" : "red") +
          (adv._model ? ' <span style="font-size:10px;color:var(--muted);margin-left:8px">Powered by LLM \u00b7 ' + adv._model + '</span>' : '') +
        '</div>' +
      '</div>' +
      section("Reasoning", adv.reasoning) +
      section("Historical Context", adv.historicalContext) +
      section("Options Read", adv.optionsRead) +
      section("Technical Read", adv.technicalRead) +
      section("Risk Warning", adv.riskWarning) +
      section("Action Plan", adv.actionPlan) +
      '</div>';
  }


  // ── Wire up ────────────────────────────────────────────────────────────

  $("runBtn").addEventListener("click", runScan);

  // Expose for the advisor onclick
  window.runAdvisor = runAdvisor;

})();
