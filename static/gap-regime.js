/* ── Engine 13 — Gap Regime Scanner ─────────────────────────────────────── */
"use strict";

let lastPayload = null;

// ── Helpers ──────────────────────────────────────────────────────────────

function fetchJson(url, opts) {
  return fetch(url, opts).then(r => {
    if (r.redirected && r.url.includes("/login")) throw new Error("Session expired — please refresh.");
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
  });
}

function pctColor(v) {
  if (v == null) return "var(--muted)";
  return v > 0 ? "var(--green)" : v < 0 ? "var(--red)" : "var(--muted)";
}

function fmtPct(v, decimals) {
  if (v == null) return "—";
  const d = decimals != null ? decimals : 2;
  const prefix = v > 0 ? "+" : "";
  return prefix + v.toFixed(d) + "%";
}

function card(label, value, caption) {
  return `<div class="e13Card">
    <div class="e13CardLabel">${label}</div>
    <div class="e13CardValue">${value}</div>
    ${caption ? `<div class="e13CardCaption">${caption}</div>` : ""}
  </div>`;
}

function badge(text, cls) {
  return `<span class="e13Badge e13Badge--${cls}">${text}</span>`;
}


// ── Scan ─────────────────────────────────────────────────────────────────

async function runScan() {
  const btn = document.getElementById("runBtn");
  const loading = document.getElementById("loading");
  const results = document.getElementById("results");
  const errorPanel = document.getElementById("errorPanel");

  btn.disabled = true;
  loading.style.display = "block";
  results.style.display = "none";
  errorPanel.style.display = "none";

  const threshold = parseFloat(document.getElementById("gapThreshold").value) || 1.5;

  try {
    const data = await fetchJson(`/api/engine13/scan?gap_threshold=${threshold}`);
    lastPayload = data;
    renderAll(data);
    results.style.display = "block";
  } catch (err) {
    errorPanel.textContent = err.message || "Scan failed";
    errorPanel.style.display = "block";
  } finally {
    loading.style.display = "none";
    btn.disabled = false;
  }
}


// ── Render orchestrator ──────────────────────────────────────────────────

function renderAll(data) {
  renderGapCards(data.gap || {});
  renderScenarios(data.scenarios || {});
  renderHistorical(data.historicalAnalogues || {}, data.geopoliticalAnalogues);
  renderOptions(data.optionsMicrostructure || {});
  renderTechVix(data.technicals || {}, data.vixBehaviour || {});
  document.getElementById("advisorPanel").innerHTML = "";
}


// ── Gap header cards ─────────────────────────────────────────────────────

function renderGapCards(gap) {
  const el = document.getElementById("gapCards");
  if (!gap.enabled) { el.innerHTML = card("Gap", "No gap detected", "SPX opened near prior close"); return; }

  const dirBadge = gap.direction === "up"
    ? badge("UP", "green")
    : badge("DOWN", "red");

  const pctBadge = gap.percentileRank > 95 ? badge("P" + gap.percentileRank, "red")
    : gap.percentileRank > 80 ? badge("P" + gap.percentileRank, "amber")
    : badge("P" + gap.percentileRank, "muted");

  el.innerHTML = [
    card("SPX Gap", `${fmtPct(gap.gapPct, 2)} ${dirBadge}`, `Prev close: ${gap.prevClose || "—"} · Open: ${gap.todayOpen || "—"}`),
    card("Gap Percentile", `${gap.percentileRank}th ${pctBadge}`, "Rank vs 5-year daily gaps"),
    card("Live Price", gap.livePrice ? gap.livePrice.toLocaleString() : "—",
      gap.gapFillPct != null ? `Gap fill: ${gap.gapFillPct}% intraday` : "Intraday gap fill: pending"),
    card("Catalyst", gap.catalystTag || "Unknown", "From Daily Market State themes"),
  ].join("");
}


// ── Scenario probabilities ───────────────────────────────────────────────

function renderScenarios(sc) {
  const panel = document.getElementById("scenarioPanel");
  const mods = document.getElementById("modifiers");
  if (!sc.enabled) { panel.innerHTML = "<p style='color:var(--muted)'>Scenarios unavailable</p>"; mods.innerHTML = ""; return; }

  function scenarioBlock(name, pct, barCls, dominant) {
    return `<div class="e13Scenario ${dominant ? "dominant" : ""}">
      <div class="e13ScenarioName">${name}</div>
      <div class="e13ScenarioProb" style="color:${barCls === "cont" ? "var(--green)" : barCls === "rev" ? "var(--red)" : "var(--amber)"}">${pct.toFixed(1)}%</div>
      <div class="e13ScenarioBar"><div class="e13ScenarioBarFill ${barCls}" style="width:${pct}%"></div></div>
    </div>`;
  }

  panel.innerHTML = [
    scenarioBlock("Continuation", sc.continuation || 0, "cont", sc.dominantScenario === "continuation"),
    scenarioBlock("Consolidation", sc.consolidation || 0, "cons", sc.dominantScenario === "consolidation"),
    scenarioBlock("Reversion", sc.reversion || 0, "rev", sc.dominantScenario === "reversion"),
  ].join("");

  let modHtml = "";
  if (sc.confidence) modHtml += badge(`Confidence: ${sc.confidence}%`, sc.confidence > 65 ? "green" : sc.confidence > 40 ? "amber" : "red") + " ";
  if (sc.modifiers && sc.modifiers.length) {
    modHtml += sc.modifiers.map(m => `<span class="e13ModTag">${m}</span>`).join("");
  }
  if (sc.expectedRangeD5) {
    const r = sc.expectedRangeD5;
    modHtml += `<span class="e13ModTag">D+5 range: ${fmtPct(r.p25)} to ${fmtPct(r.p75)} (med ${fmtPct(r.median)})</span>`;
  }
  mods.innerHTML = modHtml;
}


// ── Historical analogues ─────────────────────────────────────────────────

function renderHistorical(hist, geo) {
  const statsEl = document.getElementById("histStats");
  const tableEl = document.getElementById("histTable");

  if (!hist.enabled || !hist.count) {
    statsEl.innerHTML = card("Analogues", "0", "No gaps above threshold found in 5-year history");
    tableEl.innerHTML = "";
    return;
  }

  const od = hist.outcomeDistribution || {};
  statsEl.innerHTML = [
    card("Analogues Found", hist.count, `Gaps ≥ ${hist.thresholdPct || 1.5}% (${hist.directionFilter || "all"} direction)`),
    card("Outcome Split",
      `C ${od.continuation || 0}% · S ${od.consolidation || 0}% · R ${od.reversion || 0}%`,
      "Continuation / Consolidation / Reversion"),
    card("Median Gap Fill", hist.medianIntradayGapFill != null ? hist.medianIntradayGapFill + "%" : "—", "Intraday fill on gap day"),
    card("Median D+5 Return",
      hist.stats && hist.stats.d5 ? fmtPct(hist.stats.d5.median) : "—",
      hist.stats && hist.stats.d5 ? `Range: ${fmtPct(hist.stats.d5.p25)} to ${fmtPct(hist.stats.d5.p75)}` : ""),
  ].join("");

  // Events table
  const events = hist.events || [];
  if (!events.length) { tableEl.innerHTML = ""; return; }

  let rows = events.map(e => {
    const fr = e.forwardReturns || {};
    const oc = e.outcome;
    const ocBadge = oc === "continuation" ? badge("CONT", "green")
      : oc === "reversion" ? badge("REV", "red")
      : badge("CONS", "amber");
    return `<tr>
      <td>${e.date || "—"}</td>
      <td style="color:${pctColor(e.gapPct)}">${fmtPct(e.gapPct)}</td>
      <td style="color:${pctColor(fr.d1)}">${fmtPct(fr.d1)}</td>
      <td style="color:${pctColor(fr.d3)}">${fmtPct(fr.d3)}</td>
      <td style="color:${pctColor(fr.d5)}">${fmtPct(fr.d5)}</td>
      <td>${ocBadge}</td>
      <td>${e.intradayGapFill != null ? e.intradayGapFill + "%" : "—"}</td>
    </tr>`;
  }).join("");

  tableEl.innerHTML = `<table class="e13Table">
    <thead><tr><th>Date</th><th>Gap</th><th>D+1</th><th>D+3</th><th>D+5</th><th>Outcome</th><th>Gap Fill</th></tr></thead>
    <tbody>${rows}</tbody>
  </table>`;

  // Geopolitical analogues
  if (geo && geo.length) {
    let geoRows = geo.slice(0, 5).map(e => `<tr>
      <td>${e.event_date || "—"}</td>
      <td>${e.description || "—"}</td>
      <td style="color:${pctColor(e.spx_gap_pct)}">${fmtPct(e.spx_gap_pct)}</td>
      <td>${e.outcome_class || "—"}</td>
      <td>${e.similarity_distance != null ? e.similarity_distance.toFixed(2) : "—"}</td>
    </tr>`).join("");

    tableEl.innerHTML += `<div style="margin-top:12px;font-size:11px;font-weight:700;text-transform:uppercase;color:var(--muted);margin-bottom:6px">Geopolitical Shock Analogues</div>
    <table class="e13Table">
      <thead><tr><th>Date</th><th>Event</th><th>SPX Gap</th><th>Outcome</th><th>Similarity</th></tr></thead>
      <tbody>${geoRows}</tbody>
    </table>`;
  }
}


// ── Options microstructure ───────────────────────────────────────────────

function renderOptions(opts) {
  const el = document.getElementById("optionsCards");
  const cards = [];

  // Dealer gamma
  const dg = opts.dealerGamma || {};
  if (dg.netGammaSign) {
    const signBadge = dg.netGammaSign === "positive" ? badge("POSITIVE", "green") : badge("NEGATIVE", "red");
    cards.push(card("Dealer Gamma", `${signBadge}`,
      `Magnitude: ${dg.magnitudeBucket || "—"} · Net GEX: ${dg.netGex != null ? Math.round(dg.netGex).toLocaleString() : "—"}`));
  } else {
    cards.push(card("Dealer Gamma", "—", "No live options data"));
  }

  // Skew
  const sk = opts.skew || {};
  if (sk.label) {
    const skBadge = sk.label.includes("extreme") ? badge(sk.label, "red")
      : sk.label.includes("elevated") ? badge(sk.label, "amber")
      : badge(sk.label, "muted");
    cards.push(card("25Δ Skew", sk.skew25d != null ? (sk.skew25d * 100).toFixed(1) + " vol pts" : "—",
      `Put/Call ratio: ${sk.putCallRatio != null ? sk.putCallRatio.toFixed(3) : "—"} ${skBadge}`));
  } else {
    cards.push(card("25Δ Skew", "—", "Vol surface unavailable"));
  }

  // Term structure
  const ts = opts.termStructure || {};
  if (ts.label) {
    const tsBadge = ts.label === "backwardation" ? badge(ts.label, "red")
      : ts.label === "contango" ? badge(ts.label, "green")
      : badge(ts.label, "muted");
    cards.push(card("IV Term Structure", `${tsBadge}`,
      `Slope: ${ts.slope != null ? ts.slope.toFixed(4) : "—"}`));
  } else {
    cards.push(card("IV Term Structure", "—", ""));
  }

  // Unusual flow
  const uf = opts.unusualFlow || {};
  if (uf.totalSignals != null) {
    const sentBadge = uf.netSentiment === "bullish" ? badge("BULLISH", "green")
      : uf.netSentiment === "bearish" ? badge("BEARISH", "red")
      : badge("MIXED", "muted");
    cards.push(card("Unusual Flow", `${uf.totalSignals} signals ${sentBadge}`,
      `Calls: ${uf.calls || 0} · Puts: ${uf.puts || 0} · Sweeps: ${uf.sweeps || 0}`));
  } else {
    cards.push(card("Unusual Flow", "—", "No Benzinga signals"));
  }

  el.innerHTML = cards.join("");
}


// ── Technicals + VIX ─────────────────────────────────────────────────────

function renderTechVix(tech, vix) {
  const el = document.getElementById("techVixCards");
  const cards = [];

  // EMA stack
  if (tech.enabled && tech.ema) {
    const emas = tech.ema;
    const px = tech.livePrice || tech.lastDailyClose;
    let emaStr = Object.entries(emas)
      .filter(([k, v]) => v != null && k.startsWith("ema"))
      .sort(([a], [b]) => parseInt(a.replace("ema", "")) - parseInt(b.replace("ema", "")))
      .map(([k, v]) => `${k.replace("ema", "")}: ${v.toFixed(0)}`)
      .join(" · ");
    cards.push(card("EMA Stack", px ? px.toLocaleString(undefined, {maximumFractionDigits: 0}) : "—",
      emaStr || "No EMA data"));
  }

  // RSI
  const rsi = tech.rsi || {};
  if (rsi.value != null) {
    const rsiBadge = rsi.value > 70 ? badge("OVERBOUGHT", "red")
      : rsi.value < 30 ? badge("OVERSOLD", "green")
      : badge("NEUTRAL", "muted");
    cards.push(card("RSI (14)", `${rsi.value.toFixed(1)} ${rsiBadge}`, ""));
  }

  // Bollinger
  const bb = tech.bollinger || {};
  if (bb.enabled !== false && bb.upper != null) {
    cards.push(card("Bollinger Bands",
      `${bb.lower ? bb.lower.toFixed(0) : "—"} — ${bb.upper ? bb.upper.toFixed(0) : "—"}`,
      `Mid: ${bb.mid ? bb.mid.toFixed(0) : "—"} · Width: ${bb.width != null ? (bb.width * 100).toFixed(1) + "%" : "—"}`));
  }

  // VIX
  if (vix.enabled) {
    const vixBadge = vix.changePct < -10 ? badge("CRUSHED", "green")
      : vix.changePct < -3 ? badge("DOWN", "green")
      : vix.changePct > 10 ? badge("SPIKED", "red")
      : vix.changePct > 3 ? badge("UP", "red")
      : badge("FLAT", "muted");
    cards.push(card("VIX", `${vix.vixNow} ${vixBadge}`,
      `Prev: ${vix.prevClose} · Change: ${fmtPct(vix.changePct)} · 20d MA: ${vix.ma20}` +
      (vix.snapback ? ` · <strong style="color:var(--amber)">Snapback detected</strong>` : "")));
    cards.push(card("VIX Percentile", `${vix.percentileRank}th`,
      `${vix.aboveMa20 ? "Above" : "Below"} 20-day MA (${vix.ma20})`));
  }

  el.innerHTML = cards.join("");
}


// ── Advisor ──────────────────────────────────────────────────────────────

async function runAdvisor() {
  const btn = document.getElementById("advisorBtn");
  const panel = document.getElementById("advisorPanel");

  btn.disabled = true;
  panel.innerHTML = `<div class="e13Advisor"><div style="color:var(--muted);font-size:13px;font-weight:600">Running desk analysis...</div></div>`;

  try {
    const body = lastPayload ? { scanPayload: lastPayload } : {};
    const data = await fetchJson("/api/engine13/advisor", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    renderAdvisor(data.advisor || {});
  } catch (err) {
    panel.innerHTML = `<div class="e13Advisor" style="border-left-color:var(--red)">
      <div style="color:var(--red);font-weight:700">${err.message || "Advisor failed"}</div>
    </div>`;
  } finally {
    btn.disabled = false;
  }
}

function renderAdvisor(adv) {
  const panel = document.getElementById("advisorPanel");
  if (adv._fallback_reason) {
    panel.innerHTML = `<div class="e13Advisor" style="border-left-color:var(--amber)">
      <div style="color:var(--amber);font-weight:700">Advisor fallback: ${adv._fallback_reason}</div>
    </div>`;
    return;
  }

  const v = (adv.verdict || "HOLD").toUpperCase();
  const vCls = v === "HOLD" ? "hold" : v === "ROLL" ? "roll" : "adjust";
  const conf = adv.confidence || 0;

  function section(label, body) {
    if (!body) return "";
    return `<div class="e13AdvisorSection">
      <div class="e13AdvisorSectionLabel">${label}</div>
      <div class="e13AdvisorSectionBody">${body}</div>
    </div>`;
  }

  panel.innerHTML = `<div class="e13Advisor" style="border-left-color:${v === "HOLD" ? "var(--green)" : v === "ROLL" ? "var(--red)" : "var(--amber)"}">
    <div class="e13AdvisorHeader">
      <div class="e13AdvisorTitle">Desk Note</div>
      <div>
        <span class="e13VerdictBadge ${vCls}">${v}</span>
        ${badge("Confidence: " + conf + "%", conf > 65 ? "green" : conf > 40 ? "amber" : "red")}
        ${adv._model ? `<span style="font-size:10px;color:var(--muted);margin-left:8px">Powered by LLM · ${adv._model}</span>` : ""}
      </div>
    </div>
    ${section("Reasoning", adv.reasoning)}
    ${section("Historical Context", adv.historicalContext)}
    ${section("Options Read", adv.optionsRead)}
    ${section("Technical Read", adv.technicalRead)}
    ${section("Risk Warning", adv.riskWarning)}
    ${section("Action Plan", adv.actionPlan)}
  </div>`;
}


// ── Auto-run on load ─────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => { runScan(); });
