/**
 * Compare Page JavaScript
 * 
 * Handles multi-ticker comparison for Engine 1 earnings ranking.
 */

(function () {
  "use strict";

  // DOM helpers
  const $ = (id) => document.getElementById(id);
  const escapeHtml = (s) =>
    String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");

  // Format helpers
  const fmt0 = (v) => (v == null ? "—" : Math.round(v).toLocaleString());
  const fmt1 = (v) => (v == null ? "—" : Number(v).toFixed(1));
  const fmt2 = (v) => (v == null ? "—" : Number(v).toFixed(2));
  const fmtPct = (v) => (v == null ? "—" : `${Number(v).toFixed(1)}%`);

  // State
  let lastPayload = null;
  let isBusy = false;

  // Status icons
  const STATUS_ICONS = {
    good: '<span class="factorStatus factorStatus--good">✓</span>',
    ok: '<span class="factorStatus factorStatus--ok">⚠</span>',
    poor: '<span class="factorStatus factorStatus--poor">✗</span>',
  };

  /**
   * Fetch comparison data from API
   */
  async function fetchComparison(tickers, k) {
    const params = new URLSearchParams({
      tickers: tickers,
      k: k,
      n: "20",
      years: "5",
    });

    const res = await fetch(`/api/breach-compare?${params}`);
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return res.json();
  }

  /**
   * Render tier summary badges
   */
  function renderTierSummary(summary) {
    const container = $("tierSummary");
    if (!summary) {
      container.innerHTML = "";
      return;
    }

    const tiers = [
      { key: "slamDunk", label: "Slam Dunk", class: "slamDunk" },
      { key: "strong", label: "Strong", class: "strong" },
      { key: "standard", label: "Standard", class: "standard" },
      { key: "caution", label: "Caution", class: "caution" },
      { key: "avoid", label: "Avoid", class: "avoid" },
    ];

    const html = tiers
      .filter((t) => summary[t.key] > 0)
      .map(
        (t) =>
          `<span class="tierCount tierCount--${t.class}">${summary[t.key]} ${t.label}</span>`
      )
      .join("");

    container.innerHTML = html;
  }

  /**
   * Get ticker logo URL
   */
  function getLogoUrl(ticker) {
    return `https://financialmodelingprep.com/image-stock/${ticker}.png`;
  }

  /**
   * Render a single ranking card
   */
  function renderRankCard(item) {
    const ticker = escapeHtml(item.ticker);
    const tier = item.tier || "avoid";
    const tierLabel = escapeHtml(item.tierLabel || "—");
    const score = fmt0(item.compositeScore);
    const rank = item.rank || "?";
    const factors = item.factors || {};

    // Factor cells
    const factorCells = [
      {
        key: "breachRate",
        label: "Breach",
        value: factors.breachRate?.label || "—",
        status: factors.breachRate?.status || "ok",
      },
      {
        key: "ivElevation",
        label: "IV Rank",
        value: factors.ivElevation?.label || "—",
        status: factors.ivElevation?.status || "ok",
      },
      {
        key: "emRichness",
        label: "EM Rich",
        value: factors.emRichness?.label || "—",
        status: factors.emRichness?.status || "ok",
      },
      {
        key: "liquidity",
        label: "Liquidity",
        value: factors.liquidity?.label || "—",
        status: factors.liquidity?.status || "ok",
      },
      {
        key: "marketRegime",
        label: "Regime",
        value: factors.marketRegime?.label || "—",
        status: factors.marketRegime?.status || "ok",
      },
    ];

    const factorHtml = factorCells
      .map(
        (f) => `
        <div class="factorCell">
          <div class="factorLabel">${f.label}</div>
          <div class="factorValue">
            ${escapeHtml(f.value)}
            ${STATUS_ICONS[f.status] || ""}
          </div>
        </div>
      `
      )
      .join("");

    // Quick stats for details
    const qs = item.quickStats || {};
    const detailsHtml = `
      <div class="detailsGrid">
        <div class="detailItem">
          <div class="detailLabel">Breach Rate</div>
          <div class="detailValue">${fmtPct(qs.breachRatePct)}</div>
        </div>
        <div class="detailItem">
          <div class="detailLabel">ORATS EM</div>
          <div class="detailValue">${fmtPct(qs.oratsEmPct ?? qs.impliedMovePct)}</div>
        </div>
        <div class="detailItem">
          <div class="detailLabel">Straddle EM</div>
          <div class="detailValue">${fmtPct(qs.straddleEmPct)}</div>
        </div>
        <div class="detailItem">
          <div class="detailLabel">Events Used</div>
          <div class="detailValue">${fmt0(qs.eventsUsed)}</div>
        </div>
        <div class="detailItem">
          <div class="detailLabel">Tail Coverage</div>
          <div class="detailValue">${factors.tailCoverage?.label || "—"} ${STATUS_ICONS[factors.tailCoverage?.status] || ""}</div>
        </div>
        <div class="detailItem">
          <div class="detailLabel">Event Risk</div>
          <div class="detailValue">${factors.eventRisk?.label || "—"} ${STATUS_ICONS[factors.eventRisk?.status] || ""}</div>
        </div>
      </div>
    `;

    // Liquidity warning banner (critical)
    const liqWarning = item.liquidityWarning;
    const warningHtml = liqWarning
      ? `<div class="liquidityWarning">⚠️ ${escapeHtml(liqWarning)}</div>`
      : "";

    return `
      <div class="rankCard rankCard--${tier}${liqWarning ? " rankCard--liqWarning" : ""}" data-ticker="${ticker}">
        ${warningHtml}
        <div class="rankCardHeader">
          <div class="rankBadge">#${rank}</div>
          <div class="rankTickerInfo">
            <img 
              class="rankTickerLogo" 
              src="${getLogoUrl(ticker)}" 
              alt="${ticker}"
              onerror="this.style.display='none'"
            />
            <span class="rankTickerSymbol">${ticker}</span>
          </div>
          <span class="rankTierBadge rankTierBadge--${tier}">${tierLabel}</span>
          <span class="rankScore">Score: ${score}</span>
        </div>
        
        <div class="factorRow">
          ${factorHtml}
        </div>
        
        <div class="rankCardActions">
          <a class="rankCardLink" href="/breach?ticker=${ticker}&k=1.0&mc=1&autorun=1" target="_blank">
            Full Analysis →
          </a>
          <button class="detailsToggle" onclick="toggleDetails(this)">
            ▼ More Details
          </button>
        </div>
        
        <div class="rankCardDetails">
          ${detailsHtml}
        </div>
      </div>
    `;
  }

  /**
   * Toggle details panel
   */
  window.toggleDetails = function (btn) {
    const card = btn.closest(".rankCard");
    const details = card.querySelector(".rankCardDetails");
    const isOpen = details.classList.contains("open");

    if (isOpen) {
      details.classList.remove("open");
      btn.textContent = "▼ More Details";
    } else {
      details.classList.add("open");
      btn.textContent = "▲ Less Details";
    }
  };

  /**
   * Render all rankings
   */
  function renderRankings(rankings) {
    const container = $("rankingsGrid");
    if (!rankings || rankings.length === 0) {
      container.innerHTML =
        '<div class="loadingState">No tickers analyzed. Enter tickers above and click Compare.</div>';
      return;
    }

    container.innerHTML = rankings.map(renderRankCard).join("");
  }

  /**
   * Render errors list
   */
  function renderErrors(errors) {
    const container = $("errorsList");
    if (!errors || errors.length === 0) {
      container.classList.add("hidden");
      container.innerHTML = "";
      return;
    }

    container.classList.remove("hidden");
    container.innerHTML = `
      <h4>Failed to analyze (${errors.length}):</h4>
      ${errors
        .map(
          (e) =>
            `<div class="errorItem"><b>${escapeHtml(e.ticker)}</b>: ${escapeHtml(e.error)}</div>`
        )
        .join("")}
    `;
  }

  /**
   * Render full results
   */
  function renderResults(payload) {
    lastPayload = payload;

    // Show results section
    $("results").classList.remove("hidden");

    // Update meta
    const meta = `${payload.tickersAnalyzed} of ${payload.tickersRequested} analyzed · k=${payload.k}× · ${payload.asOfDate}`;
    $("resultsMeta").textContent = meta;

    // Render components
    renderTierSummary(payload.summary);
    renderRankings(payload.rankings);
    renderErrors(payload.errors);
  }

  /**
   * Show loading state
   */
  function showLoading(tickerCount) {
    $("results").classList.remove("hidden");
    $("resultsMeta").textContent = `Analyzing ${tickerCount} ticker${tickerCount > 1 ? "s" : ""}...`;
    $("tierSummary").innerHTML = "";
    $("rankingsGrid").innerHTML =
      '<div class="loadingState">Fetching data and computing rankings...</div>';
    $("errorsList").classList.add("hidden");
  }

  /**
   * Show error state
   */
  function showError(message) {
    $("results").classList.remove("hidden");
    $("resultsMeta").textContent = "Error";
    $("tierSummary").innerHTML = "";
    $("rankingsGrid").innerHTML = `<div class="loadingState" style="color: #b42823;">${escapeHtml(message)}</div>`;
    $("errorsList").classList.add("hidden");
  }

  /**
   * Handle form submission
   */
  async function handleSubmit(e) {
    e.preventDefault();
    if (isBusy) return;

    const tickersInput = $("tickers").value.trim();
    const k = $("k").value;

    if (!tickersInput) {
      showError("Please enter at least one ticker.");
      return;
    }

    // Parse and validate tickers
    const tickers = tickersInput
      .toUpperCase()
      .split(/[,\s]+/)
      .filter((t) => t.length > 0);

    if (tickers.length === 0) {
      showError("Please enter at least one valid ticker.");
      return;
    }

    if (tickers.length > 10) {
      showError("Maximum 10 tickers allowed. Please reduce your list.");
      return;
    }

    isBusy = true;
    showLoading(tickers.length);

    try {
      const payload = await fetchComparison(tickers.join(","), k);
      renderResults(payload);
    } catch (err) {
      console.error("Comparison failed:", err);
      showError(err.message || "Failed to fetch comparison data.");
    } finally {
      isBusy = false;
    }
  }

  /**
   * Initialize page
   */
  function init() {
    // Form submission
    $("form").addEventListener("submit", handleSubmit);

    // Auto-uppercase ticker input
    $("tickers").addEventListener("input", function () {
      const pos = this.selectionStart;
      this.value = this.value.toUpperCase();
      this.setSelectionRange(pos, pos);
    });

    // Check for URL params (for deep linking)
    const params = new URLSearchParams(window.location.search);
    const tickersParam = params.get("tickers");
    if (tickersParam) {
      $("tickers").value = tickersParam;
      const kParam = params.get("k");
      if (kParam) $("k").value = kParam;
      // Auto-run if tickers provided
      handleSubmit(new Event("submit"));
    }

    // Initialize tooltips
    if (typeof initTooltips === "function") {
      initTooltips();
    }
  }

  // Initialize on DOM ready
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
