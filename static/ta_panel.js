/* global window, document, navigator */

// Shared TA panel renderer for Engine 1 + Engine 2.
// Deterministic, scan-first, no dependencies.

function _taClamp(x, lo, hi) {
  const n = Number(x);
  if (!Number.isFinite(n)) return lo;
  return Math.max(Number(lo), Math.min(Number(hi), n));
}

function _taFmt2(x) {
  const n = Number(x);
  return Number.isFinite(n) ? n.toFixed(2) : "—";
}

function _taEscapeHtml(s) {
  return String(s ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function _taGetMode() {
  try {
    const v = String(window.localStorage?.getItem("taDeskMode") || "").toLowerCase();
    return (v === "explain") ? "explain" : "scan";
  } catch {
    return "scan";
  }
}

function _taSetMode(mode) {
  const m = (String(mode || "").toLowerCase() === "explain") ? "explain" : "scan";
  try { window.localStorage?.setItem("taDeskMode", m); } catch { /* ignore */ }
  return m;
}

function _taConfidenceDots(conf01) {
  const c = _taClamp(conf01, 0, 1);
  const dots = (c <= 0.2) ? 1 : (c <= 0.4) ? 2 : (c <= 0.6) ? 3 : (c <= 0.8) ? 4 : 5;
  return { dots, conf: c };
}

function _taBiasFromSignals(sig) {
  const s = sig || {};
  const tr = s.trend || {};
  const st = String(tr.stack || "").toLowerCase();
  const rg = String(tr.regime || "").toLowerCase();
  const ich = String((s.ichimoku || {}).state || "").toLowerCase();

  if (st === "bull") return "bullish";
  if (st === "bear") return "bearish";

  // Mixed stack: use regime + cloud position as tiebreaker.
  if (rg === "bull" && ich === "above_cloud") return "bullish";
  if (rg === "bear" && ich === "below_cloud") return "bearish";
  return "neutral";
}

function _taConfidenceFromSignals(sig) {
  const s = sig || {};
  const tr = s.trend || {};
  const st = String(tr.stack || "").toLowerCase();
  const rg = String(tr.regime || "").toLowerCase();
  const mo = s.momentum || {};
  const macdCross = String(mo.macdCross || "").toLowerCase();
  const macdHistTrend = String(mo.macdHistTrend || "").toLowerCase();
  const ich = String((s.ichimoku || {}).state || "").toLowerCase();
  const vol = s.volatility || {};
  const squeeze = !!vol.squeeze;

  let c = 0.45;
  if (st === "bull" || st === "bear") c += 0.20;
  if (rg === "bull" || rg === "bear") c += 0.10;
  if (ich === "above_cloud" || ich === "below_cloud") c += 0.10;
  if (ich === "in_cloud") c -= 0.10;
  if (macdCross === "bullish" || macdCross === "bearish") c += 0.08;
  if (macdHistTrend === "increasing" || macdHistTrend === "decreasing") c += 0.04;
  // Squeeze is attention, not confidence.
  if (squeeze) c -= 0.03;
  return _taClamp(c, 0.05, 0.95);
}

function _taSupportChips(tech) {
  const chips = [];
  const sig = tech?.signals || {};
  const tr = sig.trend || {};
  const rg = String(tr.regime || "").toLowerCase();
  const st = String(tr.stack || "").toLowerCase();
  const ich = String((sig.ichimoku || {}).state || "").toLowerCase();
  const mo = sig.momentum || {};
  const rsiState = String(mo.rsiState || "").toLowerCase();
  const macdCross = String(mo.macdCross || "").toLowerCase();
  const vol = sig.volatility || {};
  const squeeze = !!vol.squeeze;
  const nearest = (sig.levels || {}).nearest || null;

  if (rg === "bull") chips.push("Above EMA200");
  if (rg === "bear") chips.push("Below EMA200");
  if (st === "bull") chips.push("EMA stack bull");
  if (st === "bear") chips.push("EMA stack bear");
  if (ich === "above_cloud") chips.push("Above cloud");
  if (ich === "below_cloud") chips.push("Below cloud");
  if (ich === "in_cloud") chips.push("In cloud");
  if (rsiState === "overbought") chips.push("RSI overbought");
  if (rsiState === "oversold") chips.push("RSI oversold");
  if (macdCross === "bullish") chips.push("MACD bull cross");
  if (macdCross === "bearish") chips.push("MACD bear cross");
  if (squeeze) chips.push("BB squeeze");
  if (nearest && nearest.key) chips.push(`Nearest: ${String(nearest.key)}`);

  // Keep 2–3, but avoid duplicates and avoid overly long chips.
  const seen = new Set();
  const out = [];
  for (const c of chips) {
    const k = String(c || "").trim();
    if (!k || seen.has(k)) continue;
    seen.add(k);
    out.push(k);
    if (out.length >= 3) break;
  }
  return out;
}

// ---- Micro visuals (inline SVG) ----
function _svgArcDial({ pct01, state = "neutral" } = {}) {
  const p = _taClamp(pct01, 0, 1);
  const cx = 36, cy = 36, r = 26;
  const a0 = Math.PI;       // 180°
  const a1 = 2 * Math.PI;   // 360°
  const ang = a0 + (a1 - a0) * p;
  const x = cx + r * Math.cos(ang);
  const y = cy + r * Math.sin(ang);
  const cls = `taArc taArc--${_taEscapeHtml(state)}`;
  return `
    <svg class="taVis" viewBox="0 0 72 44" role="img" aria-label="arc dial">
      <path class="taArcBase" d="M10 36 A26 26 0 0 1 62 36" fill="none" />
      <path class="${cls}" d="M10 36 A26 26 0 0 1 62 36" fill="none" />
      <circle class="taArcDot" cx="${x.toFixed(2)}" cy="${y.toFixed(2)}" r="3.2" />
    </svg>
  `;
}

function _svgCompressionBar({ pct01, state = "neutral" } = {}) {
  const p = _taClamp(pct01, 0, 1);
  const cls = `taBarFill taBarFill--${_taEscapeHtml(state)}`;
  return `
    <svg class="taVis" viewBox="0 0 72 18" role="img" aria-label="compression bar">
      <rect class="taBarBase" x="4" y="6" width="64" height="6" rx="3" />
      <rect class="${cls}" x="4" y="6" width="${(64 * p).toFixed(2)}" height="6" rx="3" />
      <circle class="taBarDot" cx="${(4 + 64 * p).toFixed(2)}" cy="9" r="2.6" />
    </svg>
  `;
}

function _svgBadge({ state = "neutral" } = {}) {
  const cls = `taBadgeDot taBadgeDot--${_taEscapeHtml(state)}`;
  return `
    <svg class="taVis" viewBox="0 0 72 18" role="img" aria-label="badge">
      <circle class="${cls}" cx="10" cy="9" r="4.5" />
      <rect class="taBadgeLine" x="20" y="7" width="46" height="4" rx="2" />
    </svg>
  `;
}

function _svgStackMini({ state = "neutral" } = {}) {
  const cls = `taStackDot taStackDot--${_taEscapeHtml(state)}`;
  return `
    <svg class="taVis" viewBox="0 0 72 26" role="img" aria-label="stacked lines mini">
      <rect class="taStackLine" x="6" y="6" width="60" height="3" rx="1.5" />
      <rect class="taStackLine" x="6" y="12" width="60" height="3" rx="1.5" />
      <rect class="taStackLine" x="6" y="18" width="60" height="3" rx="1.5" />
      <circle class="${cls}" cx="62" cy="13" r="3.2" />
    </svg>
  `;
}

function _taCopy(text) {
  const t = String(text ?? "");
  if (!t) return Promise.resolve(false);
  if (window.RavenUI && typeof window.RavenUI.copyToClipboard === "function") {
    try {
      return Promise.resolve(window.RavenUI.copyToClipboard(t));
    } catch {
      // fall through
    }
  }
  if (navigator?.clipboard?.writeText) return navigator.clipboard.writeText(t).then(() => true).catch(() => false);
  // Fallback
  return new Promise((resolve) => {
    try {
      const ta = document.createElement("textarea");
      ta.value = t;
      ta.setAttribute("readonly", "true");
      ta.style.position = "fixed";
      ta.style.left = "-9999px";
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand("copy");
      ta.remove();
      resolve(!!ok);
    } catch {
      resolve(false);
    }
  });
}

function buildTechnicalsDailyViewModel(payload, { symbolOverride = null } = {}) {
  const tech = payload?.technicals || null;
  const nar = tech?.narrative || null;
  const enabled = !!(tech && tech.enabled && nar && nar.enabled && nar.summary);
  if (!enabled) return { enabled: false };

  const symbol = String(symbolOverride || tech?.ticker || payload?.ticker || payload?.underlying?.symbol || "—").toUpperCase();
  const barDate = String(tech?.barDateUsed || tech?.asOfDate || payload?.asOfDate || "").slice(0, 10) || "—";
  const price = Number(nar?.priceUsed ?? tech?.livePrice ?? tech?.lastDailyClose);
  const priceText = Number.isFinite(price) ? _taFmt2(price) : "—";

  const bias = _taBiasFromSignals(tech?.signals);
  const conf01 = _taConfidenceFromSignals(tech?.signals);
  const dots = _taConfidenceDots(conf01);
  const chips = _taSupportChips(tech);

  const inv = Array.isArray(nar?.invalidation) ? nar.invalidation.filter(Boolean).slice(0, 3) : [];
  const notes = Array.isArray(nar?.notes) ? nar.notes.filter(Boolean) : [];
  const bullets = Array.isArray(nar?.bullets) ? nar.bullets.filter(Boolean) : [];
  const summary = String(nar.summary || "");

  // Key levels for copy (best-effort)
  const lv = (tech?.distances?.levels && typeof tech.distances.levels === "object") ? tech.distances.levels : {};
  const get = (k) => {
    const obj = lv?.[k];
    const v = obj?.level;
    return Number.isFinite(Number(v)) ? Number(v) : null;
  };
  const levels = {
    EMA200: get("ema200"),
    EMA100: get("ema100"),
    EMA50: get("ema50"),
    EMA21: get("ema21"),
    EMA8: get("ema8"),
    BBmid: get("bbMid"),
    BBupper: get("bbUpper"),
    BBlower: get("bbLower"),
    Tenkan: get("tenkan"),
    Kijun: get("kijun"),
    CloudTop: get("cloudTopNow"),
    CloudBottom: get("cloudBottomNow"),
  };

  return {
    enabled: true,
    symbol,
    barDate,
    price,
    priceText,
    bias,
    confidence01: dots.conf,
    confidenceDots: dots.dots,
    supportChips: chips,
    invalidation: inv,
    notes,
    bullets,
    summary,
    levels,
    raw: { tech, nar },
  };
}

function renderTechnicalsDailyPanel(payload, { rootId = "technicalsSection", symbolOverride = null } = {}) {
  const root = document.getElementById(rootId);
  if (!root) return;

  const mode = _taGetMode(); // scan|explain
  const vm = buildTechnicalsDailyViewModel(payload, { symbolOverride });
  root.classList.toggle("hidden", !vm.enabled);
  if (!vm.enabled) return;

  const biasLabel = vm.bias === "bullish" ? "BULLISH BIAS" : vm.bias === "bearish" ? "BEARISH BIAS" : "NEUTRAL";
  const biasCls = vm.bias === "bullish" ? "pos" : vm.bias === "bearish" ? "neg" : "neu";

  const chipsHtml = vm.supportChips.map((c) => `<span class="taChip">${_taEscapeHtml(c)}</span>`).join("");
  const dotsHtml = Array.from({ length: 5 }).map((_, i) => `<span class="taDot ${i < vm.confidenceDots ? "isOn" : ""}"></span>`).join("");

  const shortBullets = vm.bullets.slice(0, 4);
  const collapsedNarrativeHtml = shortBullets.length
    ? `<ul class="taMiniList">${shortBullets.map((b) => `<li>${_taEscapeHtml(b)}</li>`).join("")}</ul>`
    : `<div class="muted">—</div>`;

  const invHtml = vm.invalidation.length
    ? `<ul class="taList">${vm.invalidation.map((b) => `<li>${_taEscapeHtml(b)}</li>`).join("")}</ul>`
    : `<div class="muted">—</div>`;

  // Cards (max 6)
  const tech = vm.raw.tech || {};
  const sig = tech.signals || {};
  const tr = sig.trend || {};
  const mo = sig.momentum || {};
  const vol = sig.volatility || {};
  const ich = sig.ichimoku || {};

  const rsiVal = Number(tech?.rsi?.value);
  const rsiSlope = Number(tech?.rsi?.slope1d);
  const rsiArrow = Number.isFinite(rsiSlope) ? (rsiSlope > 0 ? "↑" : rsiSlope < 0 ? "↓" : "→") : "";
  const rsiState = (Number.isFinite(rsiVal) && rsiVal > 60) ? "positive" : (Number.isFinite(rsiVal) && rsiVal < 40) ? "negative" : "neutral";

  const macdCross = String(tech?.macd?.cross || "");
  const histTrend = String(tech?.macd?.histTrend || "");
  const macdState = macdCross === "bullish" || histTrend === "increasing" ? "positive" : macdCross === "bearish" || histTrend === "decreasing" ? "negative" : "neutral";

  const bbBw = Number(tech?.bollinger?.bandwidthPct);
  const bbSqueeze = !!tech?.bollinger?.squeeze;
  const bbPct01 = Number.isFinite(bbBw) ? _taClamp(bbBw / 20.0, 0, 1) : 0.0; // heuristic scale

  const ichState = String(ich?.state || "");
  const ichCardState = ichState === "above_cloud" ? "positive" : ichState === "below_cloud" ? "negative" : "neutral";

  const emaRegime = String(tr?.regime || "");
  const emaRegimeState = emaRegime === "bull" ? "positive" : emaRegime === "bear" ? "negative" : "neutral";
  const emaStack = String(tr?.stack || "");
  const emaStackState = emaStack === "bull" ? "positive" : emaStack === "bear" ? "negative" : "mixed";

  const cards = [
    {
      id: "ema200",
      title: "Trend Regime (EMA200)",
      visual: _svgBadge({ state: emaRegimeState }),
      stateLabel: emaRegime === "bull" ? "Above EMA200" : emaRegime === "bear" ? "Below EMA200" : "Unknown",
      interp: "Primary swing regime divider.",
      tooltip: `EMA200 regime: price vs EMA200. Regime=${emaRegime || "—"}.`,
      details: {
        value: (() => {
          const v = Number(vm.levels.EMA200);
          return Number.isFinite(v) ? `EMA200 ${_taFmt2(v)}` : "EMA200 —";
        })(),
        delta: (() => {
          const d = tech?.distances?.levels?.ema200 || null;
          const dp = Number(d?.diffPts);
          const pct = Number(d?.diffPct);
          if (!Number.isFinite(dp)) return null;
          const side = dp > 0 ? "above" : dp < 0 ? "below" : "at";
          const ptxt = Number.isFinite(pct) ? `${Math.abs(pct).toFixed(2)}%` : "—";
          return `Price is ${side} by ${Math.abs(dp).toFixed(2)} pts (${ptxt})`;
        })(),
        bullets: [
          "EMA200 is the primary swing regime divider.",
          "Above EMA200: pullbacks often behave like trend entries; below: rallies are lower-quality until repaired.",
        ],
        levels: [
          { label: "Price", value: vm.priceText },
          { label: "EMA200", value: Number.isFinite(Number(vm.levels.EMA200)) ? _taFmt2(vm.levels.EMA200) : "—" },
        ],
      },
    },
    {
      id: "emastack",
      title: "EMA Stack (21/50/200)",
      visual: _svgStackMini({ state: emaStackState }),
      stateLabel: emaStack === "bull" ? "Aligned ↑" : emaStack === "bear" ? "Aligned ↓" : "Mixed",
      interp: "Stack alignment frames pullback risk.",
      tooltip: `EMA stack: alignment of EMA21/50/200. Stack=${emaStack || "—"}.`,
      details: {
        value: `Stack: ${emaStack || "—"}`,
        delta: null,
        bullets: [
          "Aligned (21>50>200) supports trend-follow swing continuation; mixed stacks increase chop risk.",
          "Watch EMA21/EMA50 as the first lines that flip the short-term swing character.",
        ],
        levels: [
          { label: "EMA21", value: Number.isFinite(Number(vm.levels.EMA21)) ? _taFmt2(vm.levels.EMA21) : "—" },
          { label: "EMA50", value: Number.isFinite(Number(vm.levels.EMA50)) ? _taFmt2(vm.levels.EMA50) : "—" },
          { label: "EMA200", value: Number.isFinite(Number(vm.levels.EMA200)) ? _taFmt2(vm.levels.EMA200) : "—" },
        ],
      },
    },
    {
      id: "rsi",
      title: "RSI (14)",
      visual: _svgArcDial({ pct01: Number.isFinite(rsiVal) ? _taClamp(rsiVal / 100.0, 0, 1) : 0.5, state: rsiState }),
      stateLabel: Number.isFinite(rsiVal) ? `${_taFmt2(rsiVal)} ${rsiArrow}` : "—",
      interp: "Bull regimes often hold 40–50 on pullbacks.",
      tooltip: `RSI(14): momentum oscillator. Value=${Number.isFinite(rsiVal) ? _taFmt2(rsiVal) : "—"}; Δ1d=${Number.isFinite(rsiSlope) ? _taFmt2(rsiSlope) : "—"}.`,
      details: {
        value: Number.isFinite(rsiVal) ? `RSI(14) ${_taFmt2(rsiVal)}` : "RSI(14) —",
        delta: Number.isFinite(rsiSlope) ? `Δ1d ${rsiSlope > 0 ? "+" : ""}${_taFmt2(rsiSlope)}` : null,
        bullets: [
          "RSI is a momentum gauge; treat it as context, not a standalone signal.",
          "In bull regimes, RSI often holds ~40–50 on pullbacks; in bear regimes it often fails there.",
        ],
        levels: [],
      },
    },
    {
      id: "macd",
      title: "MACD",
      visual: _svgBadge({ state: macdState }),
      stateLabel: macdCross ? `${macdCross} cross` : (histTrend ? `hist ${histTrend}` : "No cross"),
      interp: "Acceleration check (cross + histogram).",
      tooltip: `MACD: cross=${macdCross || "none"}; histTrend=${histTrend || "—"}.`,
      details: {
        value: (() => {
          const m = Number(tech?.macd?.macd);
          const s = Number(tech?.macd?.signalLine);
          const h = Number(tech?.macd?.hist);
          const parts = [];
          parts.push(`MACD ${Number.isFinite(m) ? m.toFixed(4) : "—"}`);
          parts.push(`Signal ${Number.isFinite(s) ? s.toFixed(4) : "—"}`);
          parts.push(`Hist ${Number.isFinite(h) ? h.toFixed(4) : "—"}`);
          return parts.join(" · ");
        })(),
        delta: macdCross ? `Cross: ${macdCross}` : (histTrend ? `Histogram: ${histTrend}` : null),
        bullets: [
          "MACD cross is a regime inflection check; histogram trend is an acceleration/deceleration proxy.",
          "Treat MACD as confirmation for swing entries/exits rather than a primary trigger by itself.",
        ],
        levels: [],
      },
    },
    {
      id: "bb",
      title: "Volatility (BB)",
      visual: _svgCompressionBar({ pct01: bbPct01, state: bbSqueeze ? "neutral" : "neutral" }),
      stateLabel: bbSqueeze ? "Squeeze" : "Normal",
      interp: "Compression often precedes breakout; wait for follow-through.",
      tooltip: `Bollinger bandwidth%=${Number.isFinite(bbBw) ? _taFmt2(bbBw) : "—"}; squeeze=${bbSqueeze ? "yes" : "no"}.`,
      details: {
        value: (() => {
          const bw = Number(tech?.bollinger?.bandwidthPct);
          const pb = Number(tech?.bollinger?.percentB);
          const st = String(tech?.bollinger?.state || "");
          const bwTxt = Number.isFinite(bw) ? `${_taFmt2(bw)}%` : "—";
          const pbTxt = Number.isFinite(pb) ? pb.toFixed(2) : "—";
          return `Bandwidth ${bwTxt} · %B ${pbTxt} · State ${st || "—"}`;
        })(),
        delta: bbSqueeze ? "Squeeze: volatility compressed" : null,
        bullets: [
          "Bollinger bandwidth tracks volatility regime. Compression often precedes expansion.",
          "Squeeze is an attention state; wait for directional confirmation/follow-through.",
        ],
        levels: [
          { label: "BB mid", value: Number.isFinite(Number(vm.levels.BBmid)) ? _taFmt2(vm.levels.BBmid) : "—" },
          { label: "BB upper", value: Number.isFinite(Number(vm.levels.BBupper)) ? _taFmt2(vm.levels.BBupper) : "—" },
          { label: "BB lower", value: Number.isFinite(Number(vm.levels.BBlower)) ? _taFmt2(vm.levels.BBlower) : "—" },
        ],
      },
    },
    {
      id: "ichimoku",
      title: "Ichimoku (Cloud)",
      visual: _svgBadge({ state: ichCardState }),
      stateLabel: ichState ? ichState.replaceAll("_", " ") : "—",
      interp: "Trend strength proxy; avoid cloud re-entry.",
      tooltip: `Ichimoku cloud state=${ichState || "—"}.`,
      details: {
        value: (() => {
          const bias = String(tech?.ichimoku?.cloudNow?.cloudBias || "");
          return `State ${ichState || "—"}${bias ? ` · Cloud bias ${bias}` : ""}`;
        })(),
        delta: null,
        bullets: [
          "Above cloud: trend-follow regime; inside cloud: chop/transition; below cloud: defensive regime.",
          "Cloud re-entry is a common invalidation for trend-follow swing setups.",
        ],
        levels: [
          { label: "Tenkan", value: Number.isFinite(Number(vm.levels.Tenkan)) ? _taFmt2(vm.levels.Tenkan) : "—" },
          { label: "Kijun", value: Number.isFinite(Number(vm.levels.Kijun)) ? _taFmt2(vm.levels.Kijun) : "—" },
          { label: "Cloud top", value: Number.isFinite(Number(vm.levels.CloudTop)) ? _taFmt2(vm.levels.CloudTop) : "—" },
          { label: "Cloud bottom", value: Number.isFinite(Number(vm.levels.CloudBottom)) ? _taFmt2(vm.levels.CloudBottom) : "—" },
        ],
      },
    },
  ];

  const cardsHtml = cards.slice(0, 6).map((c) => {
    return `
      <div class="taCard" role="button" tabindex="0" data-ta-card="${_taEscapeHtml(c.id)}">
        <div class="taCardTop">
          <div class="taCardTitle">${_taEscapeHtml(c.title)}</div>
          <button
            class="taInfoBtn"
            type="button"
            data-ta-tip="${_taEscapeHtml(c.tooltip)}"
            aria-label="${_taEscapeHtml(c.title)} help"
            aria-expanded="false"
          >i</button>
        </div>
        <div class="taCardVis">${c.visual}</div>
        <div class="taCardState">${_taEscapeHtml(c.stateLabel)}</div>
        <div class="taCardInterp">${_taEscapeHtml(c.interp)}</div>
        <div class="taCardMoreRow ${mode === "explain" ? "" : "hidden"}">
          <div class="taCardExplain">${_taEscapeHtml(c.tooltip)}</div>
        </div>
      </div>
    `;
  }).join("");

  const snapshotText = `${vm.symbol} Daily | Bias: ${biasLabel} (${vm.confidenceDots}/5) | Price ${vm.priceText}`;
  const levelsTextParts = [];
  const pushLv = (k, v) => { if (Number.isFinite(Number(v))) levelsTextParts.push(`${k}: ${_taFmt2(v)}`); };
  pushLv("EMA200", vm.levels.EMA200);
  pushLv("EMA50", vm.levels.EMA50);
  pushLv("EMA21", vm.levels.EMA21);
  pushLv("EMA8", vm.levels.EMA8);
  pushLv("BBmid", vm.levels.BBmid);
  pushLv("CloudTop", vm.levels.CloudTop);
  pushLv("CloudBottom", vm.levels.CloudBottom);
  pushLv("Price", vm.price);
  const levelsText = levelsTextParts.join(" | ");

  const notesHtml = vm.notes && vm.notes.length
    ? `<details class="taDetails" ${mode === "explain" ? "open" : ""}><summary>Data quality notes</summary><ul class="taList">${vm.notes.map((n) => `<li>${_taEscapeHtml(n)}</li>`).join("")}</ul></details>`
    : "";

  root.innerHTML = `
    <div class="taPanel">
      <div class="taHeader">
        <div class="taHeaderRow">
          <div class="taHeaderTitle">${_taEscapeHtml(vm.symbol)} — Daily Technicals</div>
          <div class="taHeaderMeta">EOD: ${_taEscapeHtml(vm.barDate)} • Price: <span class="mono">${_taEscapeHtml(vm.priceText)}</span></div>
        </div>
        <div class="taHeaderRow taHeaderRow--sub">
          <div class="taBiasPill taBiasPill--${biasCls}">${_taEscapeHtml(biasLabel)}</div>
          <div class="taConf" title="Confidence (deterministic; derived from signal agreement)">${dotsHtml}</div>
          <div class="taChips">${chipsHtml}</div>
          <div class="taHeaderActions">
            <div class="taModeToggle" role="group" aria-label="Mode">
              <button class="taModeBtn ${mode === "scan" ? "isOn" : ""}" type="button" data-ta-mode="scan" aria-pressed="${mode === "scan" ? "true" : "false"}">Scan</button>
              <button class="taModeBtn ${mode === "explain" ? "isOn" : ""}" type="button" data-ta-mode="explain" aria-pressed="${mode === "explain" ? "true" : "false"}">Explain</button>
            </div>
            <button class="taActionBtn" type="button" data-ta-copy="snapshot" title="Copy snapshot">Copy snapshot</button>
          </div>
        </div>
      </div>

      <div class="taGrid" aria-label="Indicator cards">${cardsHtml}</div>

      <div class="taBreaks taGlass" aria-label="What Breaks the Trade">
        <div class="taBreaksTop">
          <div class="taBreaksTitle">What Breaks the Trade</div>
          <button class="taActionBtn" type="button" data-ta-copy="levels" title="Copy key levels">Copy levels</button>
        </div>
        <div class="taBreaksBody">${invHtml}</div>
      </div>

      <div class="taAnalysis taGlass" aria-label="Analysis">
        <div class="taAnalysisTop">
          <div class="taAnalysisTitle">Analysis</div>
          <button class="taActionBtn" type="button" data-ta-copy="narrative" title="Copy narrative">Copy narrative</button>
        </div>
        <div class="taAnalysisCollapsed ${mode === "explain" ? "hidden" : ""}">
          ${collapsedNarrativeHtml}
          <button class="taLinkBtn" type="button" data-ta-expand="1">Expand</button>
        </div>
        <div class="taAnalysisExpanded ${mode === "explain" ? "" : "hidden"}">
          <div class="taNarrativeText">${_taEscapeHtml(vm.summary)}</div>
          <button class="taLinkBtn" type="button" data-ta-collapse="1">Collapse</button>
        </div>
      </div>

      ${notesHtml}
    </div>
  `;

  // --- Popover/details interaction (card click/tap) ---
  const pop = document.createElement("div");
  pop.className = "taPopoverOverlay hidden";
  pop.innerHTML = `
    <div class="taPopover taGlass" role="dialog" aria-modal="true" aria-label="Indicator details">
      <div class="taPopoverTop">
        <div class="taPopoverTitle">Details</div>
        <button class="taPopoverClose" type="button" aria-label="Close">×</button>
      </div>
      <div class="taPopoverBody"></div>
    </div>
  `;
  root.appendChild(pop);

  const popBody = pop.querySelector(".taPopoverBody");
  const popTitle = pop.querySelector(".taPopoverTitle");
  const popClose = pop.querySelector(".taPopoverClose");

  const showCard = (cardId) => {
    const card = cards.find((x) => String(x.id) === String(cardId));
    if (!card || !popBody || !popTitle) return;
    const d = card.details || {};
    const lv = Array.isArray(d.levels) ? d.levels : [];
    const bs = Array.isArray(d.bullets) ? d.bullets : [];
    popTitle.textContent = card.title || "Details";
    popBody.innerHTML = `
      <div class="taPopoverValue">${_taEscapeHtml(String(d.value || "—"))}</div>
      ${d.delta ? `<div class="taPopoverDelta">${_taEscapeHtml(String(d.delta))}</div>` : ""}
      ${bs.length ? `<ul class="taList">${bs.map((b) => `<li>${_taEscapeHtml(String(b))}</li>`).join("")}</ul>` : ""}
      ${lv.length ? `
        <div class="taPopoverLevels">
          ${lv.map((x) => `<div class="taLevelRow"><div class="taLevelK">${_taEscapeHtml(x.label)}</div><div class="taLevelV mono">${_taEscapeHtml(x.value)}</div></div>`).join("")}
        </div>` : ""}
    `;
    pop.classList.remove("hidden");
    try { popClose?.focus(); } catch { /* ignore */ }
  };

  const hidePop = () => {
    pop.classList.add("hidden");
  };

  popClose?.addEventListener("click", hidePop);
  pop.addEventListener("click", (ev) => {
    const t = ev.target;
    // clicking outside the dialog closes
    if (t && t.classList && t.classList.contains("taPopoverOverlay")) hidePop();
  });
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") hidePop();
  });

  // Wire interactions
  root.querySelectorAll("[data-ta-mode]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const next = _taSetMode(btn.getAttribute("data-ta-mode"));
      renderTechnicalsDailyPanel(payload, { rootId, symbolOverride });
      // keep focus stable
      try { btn.focus(); } catch { /* ignore */ }
      return next;
    });
  });

  root.querySelectorAll("[data-ta-copy]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const kind = String(btn.getAttribute("data-ta-copy") || "");
      if (kind === "snapshot") await _taCopy(snapshotText);
      else if (kind === "levels") await _taCopy(levelsText || snapshotText);
      else if (kind === "narrative") await _taCopy(vm.summary || snapshotText);
    });
  });

  // Tooltip popover for card info buttons (click-to-open, touch-friendly)
  let tipEl = null;
  const closeTip = () => {
    if (tipEl && tipEl.parentNode) tipEl.parentNode.removeChild(tipEl);
    tipEl = null;
    root.querySelectorAll(".taInfoBtn[aria-expanded='true']").forEach((b) => b.setAttribute("aria-expanded", "false"));
  };
  const openTip = (btn) => {
    closeTip();
    const txt = String(btn?.getAttribute("data-ta-tip") || "");
    if (!txt) return;
    btn.setAttribute("aria-expanded", "true");
    tipEl = document.createElement("div");
    tipEl.className = "taTipPop taGlass";
    tipEl.setAttribute("role", "tooltip");
    tipEl.innerHTML = `<div class="taTipPopBody">${_taEscapeHtml(txt)}</div>`;
    document.body.appendChild(tipEl);
    const r = btn.getBoundingClientRect();
    const pad = 10;
    const left = Math.max(pad, Math.min(window.innerWidth - pad, r.left + r.width / 2));
    const top = Math.max(pad, r.bottom + 8);
    tipEl.style.left = `${left}px`;
    tipEl.style.top = `${top}px`;
    tipEl.style.transform = "translateX(-50%)";
  };

  root.querySelectorAll(".taInfoBtn").forEach((btn) => {
    btn.addEventListener("click", (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      const isOpen = String(btn.getAttribute("aria-expanded") || "false") === "true";
      if (isOpen) closeTip();
      else openTip(btn);
    });
  });
  document.addEventListener("click", (ev) => {
    const t = ev.target;
    if (t && t.closest && t.closest(".taTipPop")) return;
    if (t && t.closest && t.closest(".taInfoBtn")) return;
    closeTip();
  });
  document.addEventListener("scroll", closeTip, { passive: true });
  window.addEventListener("resize", closeTip);

  const expandBtn = root.querySelector("[data-ta-expand]");
  const collapseBtn = root.querySelector("[data-ta-collapse]");
  if (expandBtn && collapseBtn) {
    expandBtn.addEventListener("click", () => {
      root.querySelector(".taAnalysisCollapsed")?.classList.add("hidden");
      root.querySelector(".taAnalysisExpanded")?.classList.remove("hidden");
    });
    collapseBtn.addEventListener("click", () => {
      // If user hits Collapse while in Explain, treat it as "go back to Scan mode" + collapse.
      if (_taGetMode() === "explain") {
        _taSetMode("scan");
        renderTechnicalsDailyPanel(payload, { rootId, symbolOverride });
        return;
      }
      root.querySelector(".taAnalysisCollapsed")?.classList.remove("hidden");
      root.querySelector(".taAnalysisExpanded")?.classList.add("hidden");
    });
  }

  // Refresh tooltips behavior if the hosting page uses tipWrap tooltips elsewhere.
  try { if (typeof initTooltips === "function") initTooltips(); } catch { /* ignore */ }

  // Card click/tap to open details
  root.querySelectorAll("[data-ta-card]").forEach((el) => {
    const id = el.getAttribute("data-ta-card");
    el.addEventListener("click", (ev) => {
      const t = ev.target;
      // clicking the small info button should not open the popover
      if (t && t.closest && t.closest(".taInfoBtn")) return;
      showCard(id);
    });
    el.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" || ev.key === " ") {
        ev.preventDefault();
        showCard(id);
      }
    });
  });
}

window.renderTechnicalsDailyPanel = renderTechnicalsDailyPanel;
window.buildTechnicalsDailyViewModel = buildTechnicalsDailyViewModel;


