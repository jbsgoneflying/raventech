/* global window, document */

/**
 * Engine 4: Ichimoku Cloud Continuation Scanner
 * Client-side JavaScript for the Ichimoku Continuation UI
 */

function $(id) { return document.getElementById(id); }

function escapeHtml(s) {
  const t = String(s ?? "");
  return t
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function fmtPct(x, d = 1) {
  const n = Number(x);
  if (!Number.isFinite(n)) return "—";
  return `${n.toFixed(d)}%`;
}

function fmt0(x) {
  const n = Number(x);
  return Number.isFinite(n) ? n.toFixed(0) : "—";
}

function fmt2(x) {
  const n = Number(x);
  return Number.isFinite(n) ? n.toFixed(2) : "—";
}

function fmtMoney(x) {
  const n = Number(x);
  if (!Number.isFinite(n)) return "—";
  return `$${n.toFixed(2)}`;
}

// State
let lastPayload = null;

function setLoading(isLoading) {
  const btn = $("runBtn");
  if (!btn) return;
  btn.disabled = !!isLoading;
  btn.classList.toggle("isLoading", !!isLoading);
  document.body.classList.toggle("isApiLoading", !!isLoading);
}

function setStatus(msg, type = "ok") {
  const el = $("status");
  if (!el) return;
  el.textContent = msg;
  el.className = `status is${type.charAt(0).toUpperCase()}${type.slice(1)}`;
}

// -----------------------------------------------------------------------------
// API
// -----------------------------------------------------------------------------

async function fetchScan(direction, minScore) {
  const params = new URLSearchParams();
  if (direction) params.set("direction", direction);
  if (minScore !== undefined) params.set("min_score", minScore);
  
  const url = `/api/engine4-ichimoku?${params.toString()}`;
  const resp = await fetch(url);
  
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.detail || `HTTP ${resp.status}`);
  }
  
  return resp.json();
}

// -----------------------------------------------------------------------------
// Render Functions
// -----------------------------------------------------------------------------

function renderStats(payload) {
  const scanned = payload.scannedCount ?? 0;
  const setups = payload.setupsFound ?? 0;
  const aplus = (payload.aPlus || []).length;
  const duration = payload.meta?.scanDurationMs ?? 0;
  
  $("statScanned").textContent = fmt0(scanned);
  $("statSetups").textContent = fmt0(setups);
  $("statAPlus").textContent = fmt0(aplus);
  $("statDuration").textContent = duration > 0 ? `${(duration / 1000).toFixed(1)}s` : "—";
  
  $("statsMeta").textContent = `${payload.asOfDate || "—"}`;
  $("statsSection").style.display = "flex";
}

function renderGammaContext(payload) {
  const gamma = payload.marketGamma || {};
  const spx = gamma.spx || {};
  const ndx = gamma.ndx || {};
  
  // SPX Gamma
  const spxSign = spx.netGammaSign || "unknown";
  const spxSignEl = $("spxGammaSign");
  if (spxSign === "positive") {
    spxSignEl.innerHTML = `<span class="gammaPositive">POSITIVE</span>`;
  } else if (spxSign === "negative") {
    spxSignEl.innerHTML = `<span class="gammaNegative">NEGATIVE</span>`;
  } else {
    spxSignEl.textContent = "—";
  }
  
  const spxEnv = spx.environment || "unknown";
  const spxEnvEl = $("spxGammaEnv");
  if (spxEnv === "supportive") {
    spxEnvEl.innerHTML = `<span class="gammaEnvSupportive">Supportive</span>`;
  } else if (spxEnv === "challenging") {
    spxEnvEl.innerHTML = `<span class="gammaEnvChallenging">Challenging</span>`;
  } else {
    spxEnvEl.textContent = "—";
  }
  
  $("spxGammaNote").textContent = spx.recommendation || "";
  
  // NDX Gamma
  const ndxSign = ndx.netGammaSign || "unknown";
  const ndxSignEl = $("ndxGammaSign");
  if (ndxSign === "positive") {
    ndxSignEl.innerHTML = `<span class="gammaPositive">POSITIVE</span>`;
  } else if (ndxSign === "negative") {
    ndxSignEl.innerHTML = `<span class="gammaNegative">NEGATIVE</span>`;
  } else {
    ndxSignEl.textContent = "—";
  }
  
  const ndxEnv = ndx.environment || "unknown";
  const ndxEnvEl = $("ndxGammaEnv");
  if (ndxEnv === "supportive") {
    ndxEnvEl.innerHTML = `<span class="gammaEnvSupportive">Supportive</span>`;
  } else if (ndxEnv === "challenging") {
    ndxEnvEl.innerHTML = `<span class="gammaEnvChallenging">Challenging</span>`;
  } else {
    ndxEnvEl.textContent = "—";
  }
  
  $("ndxGammaNote").textContent = ndx.recommendation || "";
  
  $("gammaSection").style.display = "block";
}

function renderSignalCard(signal) {
  const ticker = escapeHtml(signal.ticker || "");
  const direction = signal.direction || "bullish";
  const grade = signal.quality?.grade || "C";
  const score = signal.quality?.score ?? 0;
  const status = signal.status || "pending";
  
  const levels = signal.levels || {};
  const ichimoku = signal.ichimoku || {};
  const indicators = signal.indicators || {};
  const tags = signal.tags || [];
  
  // Grade class
  let gradeClass = "grade-c";
  if (grade === "A+") gradeClass = "grade-aplus";
  else if (grade === "A") gradeClass = "grade-a";
  else if (grade === "B") gradeClass = "grade-b";
  
  // Build tags HTML
  let tagsHtml = "";
  for (const tag of tags.slice(0, 6)) {
    const isPositive = ["Chikou Clear", "Vol Surge", "Strong Close", "Kijun Rising", "Kijun Falling", 
                        "RSI Confirm", "Cloud Aligned", "Cloud Optimal", "Gamma Supportive"].includes(tag);
    const isWarning = ["Earnings Warning"].includes(tag);
    const tagClass = isPositive ? "positive" : (isWarning ? "warning" : "");
    tagsHtml += `<span class="tagChip ${tagClass}">${escapeHtml(tag)}</span>`;
  }
  
  // Index badge
  const indexBadge = signal.indexMembership === "nasdaq100" ? "NDX" : 
                     signal.indexMembership === "both" ? "S&P/NDX" : "S&P";
  
  return `
    <div class="signalCard" data-ticker="${ticker}">
      <div class="signalCardHeader">
        <div class="signalCardTicker">
          <span class="signalCardSymbol">${ticker}</span>
          <span class="signalCardDirection ${direction}">${direction}</span>
          ${status !== "pending" ? `<span class="signalCardStatus ${status}">${status}</span>` : ""}
        </div>
        <div style="display: flex; align-items: center; gap: 8px;">
          <span style="font-size: 10px; color: var(--muted); font-weight: 600;">${indexBadge}</span>
          <span class="signalCardGrade ${gradeClass}">${grade} (${score})</span>
        </div>
      </div>
      <div class="signalCardBody">
        <div class="signalCardMetric">
          <span class="k">Entry</span>
          <span class="v">${fmtMoney(levels.entryTrigger)}</span>
        </div>
        <div class="signalCardMetric">
          <span class="k">Stop</span>
          <span class="v">${fmtMoney(levels.stopLoss)}</span>
        </div>
        <div class="signalCardMetric">
          <span class="k">Target 1</span>
          <span class="v">${fmtMoney(levels.target1)}</span>
        </div>
        <div class="signalCardMetric">
          <span class="k">Risk</span>
          <span class="v">${fmtMoney(levels.riskDollars)}</span>
        </div>
        <div class="signalCardMetric">
          <span class="k">RSI</span>
          <span class="v">${fmt0(indicators.rsi)}</span>
        </div>
        <div class="signalCardMetric">
          <span class="k">Vol Ratio</span>
          <span class="v">${fmt2(indicators.volumeRatio)}x</span>
        </div>
      </div>
      <div class="signalCardIchimoku">
        <div class="ichimokuValue">
          <span class="label">Tenkan</span>
          <span class="value">${fmt2(ichimoku.tenkan)}</span>
        </div>
        <div class="ichimokuValue">
          <span class="label">Kijun</span>
          <span class="value">${fmt2(ichimoku.kijun)}</span>
        </div>
        <div class="ichimokuValue">
          <span class="label">Cloud</span>
          <span class="value">${ichimoku.cloudBias || "—"}</span>
        </div>
      </div>
      ${tagsHtml ? `<div class="signalCardTags">${tagsHtml}</div>` : ""}
    </div>
  `;
}

function renderSignals(payload) {
  const aplus = payload.aPlus || [];
  const others = payload.others || [];
  
  // A+ Section
  const aplusGrid = $("aplusGrid");
  const aplusSection = $("aplusSection");
  $("aplusCount").textContent = aplus.length;
  
  if (aplus.length > 0) {
    aplusGrid.innerHTML = aplus.map(renderSignalCard).join("");
    aplusSection.style.display = "block";
  } else {
    aplusSection.style.display = "none";
  }
  
  // Others Section
  const othersGrid = $("othersGrid");
  const othersSection = $("othersSection");
  $("othersCount").textContent = others.length;
  
  if (others.length > 0) {
    othersGrid.innerHTML = others.map(renderSignalCard).join("");
    othersSection.style.display = "block";
  } else {
    othersSection.style.display = "none";
  }
  
  // Empty State
  const emptySection = $("emptySection");
  if (aplus.length === 0 && others.length === 0) {
    emptySection.style.display = "block";
  } else {
    emptySection.style.display = "none";
  }
}

function render(payload) {
  lastPayload = payload;
  renderStats(payload);
  renderGammaContext(payload);
  renderSignals(payload);
}

// -----------------------------------------------------------------------------
// Event Handlers
// -----------------------------------------------------------------------------

async function handleScan() {
  const direction = $("directionSelect").value || "";
  const minScore = parseInt($("minScoreInput").value, 10) || 50;
  
  setLoading(true);
  setStatus("Scanning universe...", "ok");
  
  try {
    const payload = await fetchScan(direction, minScore);
    render(payload);
    
    const total = payload.setupsFound ?? 0;
    const aplus = (payload.aPlus || []).length;
    setStatus(`Found ${total} setups (${aplus} A+).`, "ok");
  } catch (err) {
    console.error("Scan failed:", err);
    setStatus(`Error: ${err.message}`, "error");
  } finally {
    setLoading(false);
  }
}

function handleCardClick(e) {
  const card = e.target.closest(".signalCard");
  if (!card) return;
  
  const ticker = card.dataset.ticker;
  if (!ticker) return;
  
  // Open single-ticker detail (could be a modal or new page)
  window.open(`/api/engine4-ichimoku/${ticker}`, "_blank");
}

// -----------------------------------------------------------------------------
// Initialization
// -----------------------------------------------------------------------------

function init() {
  // Button handler
  const runBtn = $("runBtn");
  if (runBtn) {
    runBtn.addEventListener("click", handleScan);
  }
  
  // Enter key in inputs
  const minScoreInput = $("minScoreInput");
  if (minScoreInput) {
    minScoreInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") handleScan();
    });
  }
  
  // Card click handler
  document.addEventListener("click", handleCardClick);
  
  // Auto-run scan on load
  handleScan();
}

// Run on DOM ready
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
