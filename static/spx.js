/* global window, document */

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

function fmtPct(x, d = 2) {
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

function _pickMeaningfulClusters(sideClusters, spot, strikeStep) {
  const xs = Array.isArray(sideClusters) ? sideClusters : [];
  if (!xs.length) return [];

  const top = xs[0];
  const topTotal = Number(top?.totalOI || 0);
  const topPeak = Number(top?.peakStrike ?? top?.maxStrike);
  const topDist = (Number.isFinite(spot) && Number.isFinite(topPeak)) ? Math.abs(topPeak - spot) : Number.POSITIVE_INFINITY;

  const out = [top];
  for (let i = 1; i < xs.length && out.length < 3; i++) {
    const c = xs[i];
    const total = Number(c?.totalOI || 0);
    const peak = Number(c?.peakStrike ?? c?.maxStrike);
    const dist = (Number.isFinite(spot) && Number.isFinite(peak)) ? Math.abs(peak - spot) : Number.POSITIVE_INFINITY;

    const bigEnough = (topTotal > 0) ? (total / topTotal) >= 0.6 : false;
    const closerToSpot = dist + 1e-9 < topDist;
    const separated = (Number.isFinite(strikeStep) && strikeStep > 0 && Number.isFinite(peak) && Number.isFinite(topPeak))
      ? Math.abs(peak - topPeak) >= 2 * strikeStep
      : false;

    if (bigEnough || closerToSpot || separated) out.push(c);
  }
  return out;
}

function _fmtClusterLine(c) {
  const peak = c?.peakStrike ?? c?.maxStrike;
  return `${fmt0(peak)} (${fmt0(c?.totalOI)}) · range=${fmt0(c?.minStrike)}–${fmt0(c?.maxStrike)} · n=${fmt0(c?.nStrikes)}`;
}

let lastPayload = null;

function setLoading(isLoading) {
  const btn = $("runBtn");
  if (!btn) return;
  btn.disabled = !!isLoading;
  btn.classList.toggle("isLoading", !!isLoading);
}

function initTooltips() {
  const wraps = Array.from(document.querySelectorAll(".tipWrap"));
  const closeAll = () => {
    wraps.forEach(w => {
      w.classList.remove("isOpen");
      const b = w.querySelector(".tipBtn");
      if (b) b.setAttribute("aria-expanded", "false");
    });
  };

  wraps.forEach((w) => {
    const btn = w.querySelector(".tipBtn");
    if (!btn) return;
    btn.addEventListener("click", (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      const isOpen = w.classList.contains("isOpen");
      closeAll();
      if (!isOpen) {
        w.classList.add("isOpen");
        btn.setAttribute("aria-expanded", "true");
      }
    });
  });

  document.addEventListener("click", (ev) => {
    const t = ev.target;
    if (t && t.closest && t.closest(".tipWrap")) return;
    closeAll();
  });

  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") closeAll();
  });
}

async function fetchJson(url, { timeoutMs = 90000 } = {}) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), Number(timeoutMs));
  try {
    const r = await fetch(url, { signal: ctrl.signal });
    const txt = await r.text();
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}: ${txt.slice(0, 300)}`);
    return JSON.parse(txt);
  } catch (e) {
    if (String(e?.name || "").toLowerCase() === "aborterror") {
      throw new Error(`Request timed out after ${Math.round(timeoutMs / 1000)}s`);
    }
    throw e;
  } finally {
    clearTimeout(t);
  }
}

async function checkFlags() {
  try {
    const f = await fetchJson("/api/flags");
    return f || {};
  } catch {
    return {};
  }
}

function getMacroCap() {
  const cap = Number(window.__FLAGS?.ENGINE2_MACRO_MULTIPLIER_CAP);
  return Number.isFinite(cap) && cap > 1 ? cap : 1.8;
}

function macroLevel(mult, cap) {
  const m = Number(mult);
  const c = Number(cap);
  if (!Number.isFinite(m) || !Number.isFinite(c) || c <= 1) return "—";
  const t = (m - 1.0) / (c - 1.0); // 0..1
  if (t < 0.25) return `Low (near baseline)`;
  if (t < 0.60) return `Moderate`;
  return `High (near cap)`;
}

function toCsv(rows) {
  const esc = (v) => {
    const s = String(v ?? "");
    if (s.includes(",") || s.includes('"') || s.includes("\n")) return `"${s.replaceAll('"', '""')}"`;
    return s;
  };
  if (!rows || !rows.length) return "";
  const cols = Object.keys(rows[0]);
  const head = cols.join(",");
  const body = rows.map(r => cols.map(c => esc(r[c])).join(",")).join("\n");
  return `${head}\n${body}\n`;
}

function downloadText(filename, text) {
  const blob = new Blob([text], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function render(payload) {
  lastPayload = payload;
  const status = $("status");
  const results = $("results");
  if (results) results.classList.toggle("hidden", false);
  if (status) status.classList.remove("isError", "isRunning", "isOk");

  const meta = $("snapshotMeta");
  if (meta) meta.textContent = `asOf=${payload?.asOfDate || "—"} · entry=${payload?.params?.entryDay || "—"} · lookback=${payload?.params?.years || "—"}y · seasonality=${payload?.params?.seasonalityMode || "—"}`;

  const like = payload?.oddsLikeNow || {};
  const recMain = $("recMain");
  const recNote = $("recNote");
  if (recMain) {
    const rb = like?.regimeBucket || "—";
    const mb = like?.macroBucket || "—";
    const sb = like?.seasonBucket || "—";
    const n = like?.weeksUsed;
    recMain.textContent = `Bucket: ${rb} · ${mb} · ${sb}`;
    if (Number.isFinite(Number(n))) recMain.textContent += ` · n=${Number(n)}`;
  }
  if (recNote) {
    const rows = Array.isArray(like?.byWidth) ? like.byWidth : [];
    const notes = Array.isArray(like?.notes) ? like.notes.filter(Boolean) : [];
    const oddsLine = (r) => {
      const w = Number(r?.w);
      const n = Number(r?.n);
      const be = r?.breachEitherPct;
      return `<span class="pill pill--mini neutral">${Number.isFinite(w) ? w.toFixed(2) : "—"}× EM</span><span class="mono">${be === null || be === undefined ? "—" : Number(be).toFixed(2)}%</span><span class="ref">n=${Number.isFinite(n) ? n : "—"}</span>`;
    };
    recNote.innerHTML = `
      <div class="snapshotLines">
        <div class="snapLine">
          <div class="snapKey">Odds</div>
          <div class="pillRow">
            ${rows.length ? rows.map(r => `<span class="pillRow" style="gap:10px;">${oddsLine(r)}</span>`).join("<span class='spacerDot'>·</span>") : "—"}
          </div>
        </div>
        ${notes.length ? `<details class="snapDetails"><summary>Details</summary><div class="snapDetailBody">${escapeHtml(notes.join(" "))}</div></details>` : ""}
      </div>
    `;
  }

  const reg = payload?.current?.regime || {};
  const regimeMain = $("regimeMain");
  const regimeNote = $("regimeNote");
  if (regimeMain) {
    const s = reg.score100 !== null && reg.score100 !== undefined ? Number(reg.score100).toFixed(1) : "—";
    const b = reg.bucket || "—";
    regimeMain.textContent = `${s} / 100 · ${b}`;
  }
  if (regimeNote) {
    const c = reg?.components || {};
    const chips = [];
    if (c.trend !== null && c.trend !== undefined) chips.push({ k: "trend", v: Number(c.trend).toFixed(2) });
    if (c.volatility !== null && c.volatility !== undefined) chips.push({ k: "vol", v: Number(c.volatility).toFixed(2) });
    if (c.stress !== null && c.stress !== undefined) chips.push({ k: "stress", v: Number(c.stress).toFixed(2) });
    if (c.event !== null && c.event !== undefined) chips.push({ k: "event", v: Number(c.event).toFixed(2) });
    if (c.dispersion !== null && c.dispersion !== undefined) chips.push({ k: "disp", v: Number(c.dispersion).toFixed(2) });
    regimeNote.innerHTML = chips.length
      ? `<div class="chipRow">${chips.map(x => `<span class="chip"><span class="k">${escapeHtml(x.k)}</span><span class="mono">${escapeHtml(x.v)}</span></span>`).join("")}</div>`
      : "—";
  }

  const und = payload?.underlying || {};
  const underlying = $("underlying");
  const underlyingNote = $("underlyingNote");
  if (underlying) underlying.textContent = und.symbol || "—";
  if (underlyingNote) underlyingNote.textContent = (und.isProxy ? `Proxy used. ${Array.isArray(und.notes) ? und.notes.join(" ") : ""}` : "Direct") || "—";

  // Actionable VWAP level (daily; proxy)
  const vwap = payload?.current?.vwap || {};
  const vwapMain = $("vwapMain");
  const vwapNote = $("vwapNote");
  if (vwapMain || vwapNote) {
    const enabled = !!vwap?.enabled;
    if (!enabled) {
      if (vwapMain) vwapMain.textContent = "—";
      if (vwapNote) {
        const notes = Array.isArray(vwap?.notes) ? vwap.notes.filter(Boolean) : [];
        vwapNote.textContent = notes[0] || "VWAP level unavailable.";
      }
    } else {
      if (vwapMain) vwapMain.textContent = fmt2(vwap?.value);
      if (vwapNote) {
        const lp = vwap?.livePrice;
        const bd = vwap?.barDateUsed || "—";
        const mode = String(vwap?.mode || "");
        const modeLabel = (mode === "orats_daily_vwap")
          ? "ORATS daily VWAP"
          : (mode === "rolling_daily_typical_price_vwap")
            ? `Rolling VWAP proxy (window=${String(vwap?.window ?? "—")})`
            : (mode === "daily_typical_price")
              ? "Typical price (H+L+C)/3"
              : (mode ? mode : "—");

        const d = vwap?.distance || null;
        const side = String(d?.side || "");
        const dp = Number(d?.diffPts);
        const dpc = Number(d?.diffPct);

        const spotTxt = Number.isFinite(Number(lp)) ? Number(lp).toFixed(2) : "—";
        let distTxt = "distance=—";
        if (Number.isFinite(dp)) {
          const absPts = Math.abs(dp).toFixed(2);
          const pctTxt = Number.isFinite(dpc) ? `${Math.abs(dpc).toFixed(2)}%` : "—";
          if (side === "above") distTxt = `spot above by ${absPts} pts (${pctTxt})`;
          else if (side === "below") distTxt = `spot below by ${absPts} pts (${pctTxt})`;
          else if (side === "at") distTxt = `spot ≈ VWAP`;
          else distTxt = `Δ=${dp.toFixed(2)} pts`;
        }

        vwapNote.textContent = `bar=${String(bd)} · spot=${spotTxt} · ${distTxt} · ${modeLabel}`;
      }
    }
  }

  const macro = payload?.current?.macro || {};
  const macroMain = $("macroMain");
  const macroNote = $("macroNote");
  const macroMult = $("macroMult");
  const macroFlags = $("macroFlags");
  if (macroMain) {
    const c = macro?.highImpactUS?.count;
    const top = Array.isArray(macro?.highImpactUS?.top) ? macro.highImpactUS.top : [];
    macroMain.textContent = (c !== null && c !== undefined) ? `High-impact US events: ${c}` : "—";
    if (macroNote) {
      if (top.length) {
        macroNote.innerHTML = `<div class="macroEventList">${top.map(x => `<div class="macroEventLine">${escapeHtml(x)}</div>`).join("")}</div>`;
      } else if (Array.isArray(macro?.notes) && macro.notes.length) {
        macroNote.textContent = macro.notes.join(" ");
      } else {
        // If Benzinga is enabled and returns no high-impact events, show a clear "none" state.
        const count = (c !== null && c !== undefined) ? Number(c) : null;
        macroNote.textContent = (count === 0) ? "No high-impact US events detected for this window." : "—";
      }
    }
  }
  const multVal = (macro?.multiplier !== null && macro?.multiplier !== undefined) ? Number(macro.multiplier) : null;
  if (macroMult) macroMult.textContent = (multVal !== null && Number.isFinite(multVal)) ? Number(multVal).toFixed(2) + "×" : "—";

  // Tooltip range + interpretation (if present in DOM)
  const macroCapEl = $("macroCap");
  const macroRelEl = $("macroRel");
  if (macroCapEl || macroRelEl) {
    const cap = getMacroCap();
    if (macroCapEl) macroCapEl.textContent = Number(cap).toFixed(2);
    if (macroRelEl) {
      macroRelEl.textContent = (multVal !== null && Number.isFinite(multVal))
        ? `${Number(multVal).toFixed(2)}× → ${macroLevel(multVal, cap)}`
        : "—";
    }
  }

  if (macroFlags) {
    const f = macro?.flags || {};
    const bits = ["CPI","FOMC","NFP","OPEX","REFUNDING"].filter(k => f && f[k]);
    const mult = Number(macro?.multiplier);
    const hi = ["CPI","FOMC","NFP"].some(k => f && f[k]);
    const bucket = (Number.isFinite(mult) && (mult >= 1.25 || hi)) ? "MACRO" : "NORMAL";
    const pills = [`<span class="pill pill--mini neutral">${escapeHtml(bucket)}</span>`]
      .concat(bits.map(k => `<span class="pill pill--mini neutral">${escapeHtml(k)}</span>`));
    macroFlags.innerHTML = `<div class="pillRow">${pills.join("")}</div>`;
    macroFlags.classList.remove("hidden");
  }

  const bt = payload?.backtest || {};
  const btMain = $("btMain");
  const btNote = $("btNote");
  if (btMain) btMain.textContent = (bt.rowsUsed !== null && bt.rowsUsed !== undefined) ? `${bt.rowsUsed}` : "—";
  if (btNote) btNote.textContent = "Weeks used in backtest (filtered for missing prices/IV).";

  // Live dealer gamma context (informational only) — render BOTH weekly + nearest-daily views
  const lc = payload?.liveContext || null;
  const weekly = lc?.weeklyFriday || null;
  const daily = lc?.nearestDaily || null;

  // Backwards compatibility: if backend returns legacy liveContext shape, treat it as weekly.
  const weeklyFallback = (!weekly && lc && lc.dealerGamma) ? {
    enabled: !!lc.enabled,
    symbolUsed: lc.symbolUsed,
    expiry: lc.expiry,
    dealerGamma: lc.dealerGamma,
    oiClusters: lc.oiClusters,
    warnings: lc.warnings,
    notes: lc.notes,
  } : null;

  function renderLive(prefix, view) {
    const dgMain = $(prefix === "W" ? "dgMainW" : "dgMainD");
    const dgNote = $(prefix === "W" ? "dgNoteW" : "dgNoteD");
    const dgTop = $(prefix === "W" ? "dgTopW" : "dgTopD");
    const dgOi = $(prefix === "W" ? "dgOiW" : "dgOiD");
    const oiMeta = $(prefix === "W" ? "oiMetaW" : "oiMetaD");
    const oiPut = $(prefix === "W" ? "oiPutW" : "oiPutD");
    const oiCall = $(prefix === "W" ? "oiCallW" : "oiCallD");
    if (!dgMain || !dgNote || !dgTop || !dgOi || !oiMeta || !oiPut || !oiCall) return;

    const dg = view?.dealerGamma || null;
    const oi = view?.oiClusters || null;
    const enabled = !!(view && view.enabled && dg && dg.netGammaSign);
    if (!enabled) {
      dgMain.textContent = "—";
      const notes = Array.isArray(view?.notes) ? view.notes.filter(Boolean) : [];
      const warn = Array.isArray(view?.warnings) ? view.warnings.filter(Boolean) : [];
      dgNote.textContent = notes[0] || warn[0] || "Live context unavailable.";
      dgTop.textContent = "";
      dgOi.textContent = "—";
      oiMeta.textContent = "—";
      oiPut.textContent = "Put: —";
      oiCall.textContent = "Call: —";
      return;
    }

    dgMain.textContent = `${String(dg.netGammaSign || "").toUpperCase()} · ${String(dg.magnitudeBucket || "").toUpperCase()}`;
    dgNote.textContent = `symbol=${String(view.symbolUsed || "—")} · expiry=${String(view.expiry || "—")} · spot=${Number(dg.spot || 0).toFixed(2)} · band=±${Math.round(Number(dg.bandPct || 0.05) * 100)}% · weighting=${String(dg.weightingMode || "—")}`;

    const tops = Array.isArray(dg.topGammaStrikes) ? dg.topGammaStrikes : [];
    dgTop.textContent = tops.length ? `Top strikes: ${tops.map(x => `${Number(x.strike).toFixed(0)}${String(x.side || "")}`).join(" · ")}` : "";

    const putWall = oi && typeof oi === "object" ? oi.putWall : null;
    const callWall = oi && typeof oi === "object" ? oi.callWall : null;
    const putStrike = putWall && (putWall.peakStrike ?? putWall.maxStrike);
    const callStrike = callWall && (callWall.peakStrike ?? callWall.maxStrike);
    const putTxt = putWall && Number.isFinite(Number(putStrike)) ? `${Number(putStrike).toFixed(0)} (${Number(putWall.totalOI || 0).toFixed(0)})` : "—";
    const callTxt = callWall && Number.isFinite(Number(callStrike)) ? `${Number(callStrike).toFixed(0)} (${Number(callWall.totalOI || 0).toFixed(0)})` : "—";
    dgOi.textContent = `OI walls: put=${putTxt} | call=${callTxt}`;

    // OI clusters card
    const spot = Number(oi?.spot);
    const step = Number(oi?.strikeStep);
    const band = Number(oi?.bandPct);
    oiMeta.textContent = `expiry=${String(oi?.expiry || view.expiry || "—")} · spot=${fmt0(spot)} · band=±${Math.round((Number.isFinite(band) ? band : 0.05) * 100)}% · step=${fmt0(step)}`;

    const puts = _pickMeaningfulClusters(oi?.putClusters, spot, step).map(_fmtClusterLine);
    const calls = _pickMeaningfulClusters(oi?.callClusters, spot, step).map(_fmtClusterLine);
    oiPut.textContent = puts.length ? `Put: ${puts.join(" | ")}` : "Put: —";
    oiCall.textContent = calls.length ? `Call: ${calls.join(" | ")}` : "Call: —";
  }

  renderLive("W", weekly || weeklyFallback);
  renderLive("D", daily);

  const oddsMeta = $("oddsMeta");
  if (oddsMeta) {
    const rb = like?.regimeBucket || "—";
    const mb = like?.macroBucket || "—";
    const sb = like?.seasonBucket || "—";
    const n = like?.weeksUsed;
    oddsMeta.textContent = `bucket=${rb}/${mb}/${sb} · lookback=2y · weeksUsed=${Number.isFinite(Number(n)) ? Number(n) : "—"}`;
  }

  const tbody = $("oddsBody");
  if (tbody) {
    const rows = Array.isArray(like?.byWidth) ? like.byWidth : [];
    tbody.innerHTML = rows.map(r => {
      const w = Number(r.w);
      const n = r.n ?? "—";
      const be = r.breachEitherPct;
      const bp = r.breachPutPct;
      const bc = r.breachCallPct;
      const ar = r.avgAbsRetPct;
      return `<tr>
        <td class="mono">${Number.isFinite(w) ? w.toFixed(2) : "—"}</td>
        <td class="num mono">${escapeHtml(String(n))}</td>
        <td class="num mono">${be === null || be === undefined ? "—" : fmtPct(be, 2)}</td>
        <td class="num mono">${bp === null || bp === undefined ? "—" : fmtPct(bp, 2)}</td>
        <td class="num mono">${bc === null || bc === undefined ? "—" : fmtPct(bc, 2)}</td>
        <td class="num mono">${ar === null || ar === undefined ? "—" : fmtPct(ar, 2)}</td>
      </tr>`;
    }).join("");
  }

  if (status) {
    // Keep the UI clean: hide status on success; it only matters for running/errors.
    status.textContent = "";
    status.classList.remove("isRunning", "isError");
    status.classList.add("hidden");
  }
}

async function run() {
  const status = $("status");
  const entryDay = $("entryDay")?.value || "mon";
  const seasonalityMode = $("seasonalityMode")?.value || "none";
  // Desk-locked params
  const years = "2";
  const widths = "1.0,1.5,2.0";
  const weeksLimit = "0";

  const qs = new URLSearchParams({
    entry_day: entryDay,
    years: String(years),
    widths: String(widths),
    seasonality_mode: String(seasonalityMode),
    weeks_limit: String(weeksLimit),
  });

  try {
    setLoading(true);
    if (status) {
      status.textContent = "Running…";
      status.classList.remove("isError", "isOk");
      status.classList.add("isRunning");
      status.classList.remove("hidden");
    }
    const payload = await fetchJson(`/api/spx-ic?${qs.toString()}`);
    render(payload);
  } catch (e) {
    if (status) {
      status.textContent = `Error: ${String(e?.message || e)}`;
      status.classList.remove("isRunning", "isOk");
      status.classList.add("isError");
      status.classList.remove("hidden");
    }
    const results = $("results");
    if (results) results.classList.toggle("hidden", true);
  } finally {
    setLoading(false);
  }
}

async function main() {
  const status = $("status");
  const flags = await checkFlags();
  window.__FLAGS = flags || {};
  if (!flags?.ENABLE_ENGINE2_SPX_IC) {
    if (status) {
      status.textContent = "Engine 2 disabled. Set ENABLE_ENGINE2_SPX_IC=1 and restart the server.";
      status.classList.remove("isRunning", "isOk");
      status.classList.add("isError");
    }
    setLoading(true);
    return;
  }

  const form = $("spxForm");
  if (form) {
    form.addEventListener("submit", (ev) => {
      ev.preventDefault();
      run();
    });
  }

  initTooltips();
  // AskRaven removed

  // Do NOT auto-run: user must review selections and click Run.
  const results = $("results");
  if (results) results.classList.toggle("hidden", true);
  if (status) {
    status.textContent = "Select parameters, then click Run.";
    status.classList.remove("isError", "isRunning", "isOk");
  }
}

main();



