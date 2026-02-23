/* ── Engine 9: Credit Stress Drift — Frontend Logic ────────────────── */
(function () {
  "use strict";

  var _scanData = null;
  var _spreadChart = null;
  var _refreshTimer = null;
  var _abortCtrl = null;

  /* ── helpers ── */
  function $(id) { return document.getElementById(id); }
  function fmt(v, d) { return v != null ? Number(v).toFixed(d == null ? 1 : d) : "—"; }
  function fmtDollar(v) {
    if (v == null) return "—";
    var abs = Math.abs(v);
    if (abs >= 1e9) return "$" + (v / 1e9).toFixed(1) + "B";
    if (abs >= 1e6) return "$" + (v / 1e6).toFixed(1) + "M";
    if (abs >= 1e3) return "$" + (v / 1e3).toFixed(0) + "K";
    return "$" + v.toFixed(0);
  }
  function scoreClass(s) { return s >= 60 ? "high" : s >= 30 ? "med" : "low"; }
  function esc(s) { var d = document.createElement("div"); d.textContent = s; return d.innerHTML; }

  /* ── Scan ── */
  function runScan() {
    var btn = $("e9ScanBtn");
    var loading = $("e9Loading");
    btn.disabled = true;
    btn.textContent = "Scanning...";
    loading.style.display = "block";

    if (_abortCtrl) _abortCtrl.abort();
    _abortCtrl = new AbortController();

    fetch("/api/engine9/scan", { signal: _abortCtrl.signal })
      .then(function (r) {
        if (!r.ok) throw new Error("Scan failed: " + r.status);
        return r.json();
      })
      .then(function (data) {
        _scanData = data;
        renderAll(data);
        fetchSpreads();
        $("e9DeskNotesBtn").disabled = false;
        $("e9ThesisScanBtn").disabled = false;
        $("e9Updated").textContent = "Last updated: " + new Date().toLocaleTimeString();
      })
      .catch(function (err) {
        if (err.name === "AbortError") return;
        console.error("Engine 9 scan error:", err);
        loading.innerHTML = '<div style="color:var(--red)">Scan failed: ' + err.message + '</div>';
      })
      .finally(function () {
        btn.disabled = false;
        btn.textContent = "Run Full Scan";
      });
  }

  /* ── Render All ── */
  function renderAll(data) {
    $("e9Loading").style.display = "none";
    $("e9Content").style.display = "";
    renderPhase(data);
    renderSignals(data.signals || []);
    renderNews(data.news || {});
    renderForcedSellerMap(data.forced_seller_map || []);
    renderWatchlist(data.watchlist || {});
  }

  /* ── Section A: Phase + Triggers + Thesis ── */
  function renderPhase(data) {
    var comp = data.composite || {};
    var phase = comp.phase || 1;
    var badge = $("e9PhaseBadge");
    badge.setAttribute("data-phase", phase);
    $("e9PhaseNum").textContent = phase;
    $("e9PhaseLabel").textContent = comp.phase_label || "";
    $("e9Composite").textContent = "Composite: " + fmt(comp.composite, 1);

    var action = $("e9ActionBanner");
    if (comp.phase_action) {
      action.textContent = "Phase " + phase + " Action: " + comp.phase_action;
      action.style.display = "";
    }

    var triggers = data.triggers || [];
    var tList = $("e9TriggersList");
    tList.innerHTML = "";
    triggers.forEach(function (t) {
      var row = document.createElement("div");
      row.className = "e9TriggerRow";
      var badgeCls = "e9TriggerBadge" + (t.active ? " active" : "");
      row.innerHTML =
        '<div class="' + badgeCls + '" data-level="' + t.level + '">' + t.level + '</div>' +
        '<div class="e9TriggerInfo">' +
          '<div class="e9TriggerName">' + t.name + (t.active ? " — ACTIVE" : "") + '</div>' +
          '<div class="e9TriggerCondition">' + t.condition + '</div>' +
        '</div>';
      tList.appendChild(row);
    });

    var thesis = data.thesis_health || [];
    var tHealth = $("e9ThesisHealth");
    tHealth.innerHTML = "";
    thesis.forEach(function (ind) {
      var item = document.createElement("div");
      item.className = "e9ThesisItem";
      item.innerHTML =
        '<div class="e9ThesisDot ' + (ind.healthy ? "healthy" : "warning") + '"></div>' +
        '<span>' + ind.name + ': ' + (ind.detail || "") + '</span>';
      tHealth.appendChild(item);
    });

    var tc = (data.signals || []).find(function (s) { return s.key === "time_compression"; });
    var banner = $("e9CompressionBanner");
    if (tc && tc.triggered) {
      banner.classList.add("visible");
    } else {
      banner.classList.remove("visible");
    }
  }

  /* ── Section B: Signal Grid ── */
  function renderSignals(signals) {
    var grid = $("e9SignalGrid");
    grid.innerHTML = "";
    signals.forEach(function (sig) {
      var sc = scoreClass(sig.score);
      var weightLabel = sig.weight > 0 ? (sig.weight * 100).toFixed(0) + "%" : (sig.key === "time_compression" ? "META" : "OVERLAY");
      var card = document.createElement("div");
      card.className = "e9SigCard" + (sig.triggered ? " triggered" : "");

      var detailHtml = '<div class="e9SigDetail">' + esc(sig.detail || "") + '</div>';
      var sigData = sig.data || {};
      if (sig.key === "nlp_language" && sigData.method === "llm" && sigData.per_ticker) {
        detailHtml += '<div style="margin-top:6px;font-size:10px;color:var(--muted)">';
        Object.keys(sigData.per_ticker).forEach(function (tk) {
          var d = sigData.per_ticker[tk];
          detailHtml += '<span style="font-weight:700">' + tk + '</span>: ' + fmt(d.score, 0) + ' ';
        });
        detailHtml += '</div>';
      }
      if (sig.key === "bdc_divergence" && sigData.per_bdc) {
        detailHtml += '<div style="margin-top:6px;font-size:10px;color:var(--muted)">';
        sigData.per_bdc.forEach(function (b) {
          detailHtml += '<span style="font-weight:700">' + b.ticker + '</span>: ' + fmt(b.score, 0);
          if (b.book_value != null) detailHtml += ' (BV: $' + fmt(b.book_value, 2) + ')';
          detailHtml += ' ';
        });
        detailHtml += '</div>';
      }
      if (sig.key === "insider_selling" && sigData.per_ticker) {
        detailHtml += '<div style="margin-top:6px;font-size:10px;color:var(--muted)">';
        sigData.per_ticker.forEach(function (t) {
          var anomaly = t.anomaly_ratio > 2 ? ' style="color:var(--red);font-weight:700"' : '';
          detailHtml += '<span' + anomaly + '>' + t.ticker + ': ' + fmt(t.anomaly_ratio, 1) + 'x</span> ';
        });
        detailHtml += '</div>';
      }

      card.innerHTML =
        '<div class="e9SigHeader">' +
          '<span class="e9SigName">' + sig.label + '</span>' +
          '<span class="e9SigWeight">' + weightLabel + '</span>' +
        '</div>' +
        '<div class="e9SigScore ' + sc + '">' + fmt(sig.score, 0) + '</div>' +
        '<div class="e9SigBar"><div class="e9SigBarFill ' + sc + '" style="width:' + Math.min(sig.score, 100) + '%"></div></div>' +
        detailHtml;
      grid.appendChild(card);
    });
  }

  /* ── Section B2: News Cycle ── */
  function renderNews(news) {
    var wrap = $("e9NewsWrap");
    if (!wrap) return;
    var articles = news.articles || [];
    if (!articles.length) {
      wrap.innerHTML = '<div style="padding:12px;color:var(--muted);font-size:12px;">No credit-stress news detected in past 7 days.</div>';
      return;
    }

    var html = "";
    if (news.summary) {
      html += '<div class="e9NewsSummary">' + esc(news.summary) + '</div>';
    }
    html += '<div class="e9NewsGrid">';
    articles.slice(0, 12).forEach(function (a) {
      var rel = a.llm_relevance;
      var relClass = rel >= 7 ? "high" : rel >= 4 ? "med" : "low";
      var relLabel = rel != null ? fmt(rel, 0) + "/10" : "—";
      html += '<div class="e9NewsCard">';
      html += '<div class="e9NewsTitle">';
      if (a.link) {
        html += '<a href="' + esc(a.link) + '" target="_blank" rel="noopener">' + esc(a.title) + '</a>';
      } else {
        html += esc(a.title);
      }
      html += '</div>';
      html += '<div class="e9NewsMeta">';
      if (a.date) html += '<span>' + esc(a.date.substring(0, 10)) + '</span>';
      if (a.source) html += '<span>' + esc(a.source) + '</span>';
      html += '<span class="e9NewsRel ' + relClass + '">Relevance: ' + relLabel + '</span>';
      html += '</div>';
      if (a.matched_keywords && a.matched_keywords.length) {
        html += '<div class="e9NewsKw">Keywords: ' + a.matched_keywords.join(", ") + '</div>';
      }
      if (a.llm_reason) {
        html += '<div style="font-size:10px;color:var(--muted);margin-top:4px;">' + esc(a.llm_reason) + '</div>';
      }
      html += '</div>';
    });
    html += '</div>';
    wrap.innerHTML = html;
  }

  /* ── Section C: Forced Seller Map ── */
  function renderForcedSellerMap(entries) {
    var wrap = $("e9ForcedWrap");
    if (!entries.length) {
      wrap.innerHTML = '<div style="padding:12px;color:var(--muted);font-size:13px;">No forced seller data available</div>';
      return;
    }
    var html = '<table class="e9Table"><thead><tr>' +
      '<th>Ticker</th><th>Fragility</th><th>Leverage</th><th>Liq. Mismatch</th>' +
      '<th>Retail Exp.</th><th>Put Skew 25d</th><th>Price 20d %</th><th>Insider 30d</th>' +
      '</tr></thead><tbody>';
    entries.forEach(function (e) {
      var fc = e.fragility_score >= 60 ? "e9FragHigh" : e.fragility_score >= 30 ? "e9FragMed" : "e9FragLow";
      var priceCls = (e.price_20d_pct != null && e.price_20d_pct < 0) ? "e9Negative" : "";
      html +=
        '<tr>' +
        '<td class="e9Ticker">' + e.ticker + '</td>' +
        '<td class="' + fc + '">' + fmt(e.fragility_score, 0) + '</td>' +
        '<td>' + fmt(e.leverage, 2) + '</td>' +
        '<td>' + fmt(e.liquidity_mismatch, 2) + '</td>' +
        '<td>' + fmt(e.retail_exposure, 0) + '%</td>' +
        '<td>' + fmt(e.put_skew_25d, 3) + '</td>' +
        '<td class="' + priceCls + '">' + fmt(e.price_20d_pct, 2) + '%</td>' +
        '<td>' + fmtDollar(e.insider_net_30d) + '</td>' +
        '</tr>';
    });
    html += '</tbody></table>';
    wrap.innerHTML = html;
  }

  /* ── Section D: Tiered Watchlist ── */
  function renderWatchlist(watchlist) {
    var wrap = $("e9WatchWrap");
    wrap.innerHTML = "";

    var tierOrder = ["tier1", "tier2", "tier3", "tier4"];
    var tierLabels = {
      tier1: "Tier 1: BDCs (Direct Stress)",
      tier2: "Tier 2: Alt Managers (Sentiment + AUM)",
      tier3: "Tier 3: Credit ETFs (Confirmation)",
      tier4: "Tier 4: Vol / Tail Hedges",
    };

    tierOrder.forEach(function (tierKey) {
      var tickers = watchlist[tierKey] || [];
      if (!tickers.length) return;

      var group = document.createElement("div");
      group.className = "e9TierGroup";

      var header = document.createElement("div");
      header.className = "e9TierHeader";
      header.innerHTML =
        '<span class="e9TierChevron">&#9654;</span>' +
        '<span class="e9TierBadge ' + tierKey + '">' + tierKey.toUpperCase().replace("TIER", "T") + '</span>' +
        '<span>' + (tierLabels[tierKey] || tierKey) + ' (' + tickers.length + ')</span>';

      var body = document.createElement("div");
      body.className = "e9TierBody";

      var table = '<table class="e9Table"><thead><tr>' +
        '<th>Ticker</th><th>Price</th><th>5d %</th><th>20d %</th><th>IV Rank</th>' +
        '<th>Put Skew</th><th>Insider 30d</th><th>Score</th><th>Conviction</th>' +
        '</tr></thead><tbody>';

      tickers.forEach(function (t) {
        var chg5Cls = (t.change_5d_pct != null && t.change_5d_pct < 0) ? "e9Negative" : (t.change_5d_pct > 0 ? "e9Positive" : "");
        var chg20Cls = (t.change_20d_pct != null && t.change_20d_pct < 0) ? "e9Negative" : (t.change_20d_pct > 0 ? "e9Positive" : "");
        table +=
          '<tr>' +
          '<td class="e9Ticker">' + t.ticker + '</td>' +
          '<td>' + fmt(t.price, 2) + '</td>' +
          '<td class="' + chg5Cls + '">' + fmt(t.change_5d_pct, 2) + '%</td>' +
          '<td class="' + chg20Cls + '">' + fmt(t.change_20d_pct, 2) + '%</td>' +
          '<td>' + fmt(t.iv_rank, 0) + '</td>' +
          '<td>' + fmt(t.put_skew_25d, 3) + '</td>' +
          '<td>' + fmtDollar(t.insider_net_30d) + '</td>' +
          '<td>' + fmt(t.signal_score, 0) + '</td>' +
          '<td><span class="e9ConvBadge ' + (t.conviction || "neutral") + '">' + (t.conviction || "—") + '</span></td>' +
          '</tr>';
      });
      table += '</tbody></table>';
      body.innerHTML = table;

      header.addEventListener("click", function () {
        header.classList.toggle("open");
        body.classList.toggle("open");
      });

      group.appendChild(header);
      group.appendChild(body);
      wrap.appendChild(group);
    });

    var first = wrap.querySelector(".e9TierHeader");
    if (first) { first.classList.add("open"); first.nextElementSibling.classList.add("open"); }
  }

  /* ── Section E: Spread Chart ── */
  function fetchSpreads() {
    fetch("/api/engine9/spreads")
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) { if (data) renderSpreadChart(data); })
      .catch(function (err) { console.warn("Spread chart fetch error:", err); });
  }

  function renderSpreadChart(data) {
    var canvas = $("e9SpreadChart");
    var hy = data.hy_oas || {};
    var ig = data.ig_oas || {};
    var curve = data.curve_2s10s || {};
    var dates = hy.dates || ig.dates || [];
    var hyVals = hy.values || [];
    var igVals = ig.values || [];
    var curveVals = curve.values || [];

    if (_spreadChart) _spreadChart.destroy();

    var datasets = [];
    if (hyVals.length) {
      datasets.push({
        label: "HY OAS (bps)", data: hyVals,
        borderColor: "rgba(255,59,48,0.9)", backgroundColor: "rgba(255,59,48,0.08)",
        fill: true, tension: 0.3, pointRadius: 0, borderWidth: 2, yAxisID: "y",
      });
    }
    if (igVals.length) {
      datasets.push({
        label: "IG OAS (bps)", data: igVals,
        borderColor: "rgba(255,159,10,0.7)", backgroundColor: "transparent",
        fill: false, tension: 0.3, pointRadius: 0, borderWidth: 1.5, yAxisID: "y",
      });
    }
    if (curveVals.length) {
      datasets.push({
        label: "2s10s Curve (%)", data: curveVals,
        borderColor: "rgba(52,199,89,0.7)", backgroundColor: "transparent",
        fill: false, tension: 0.3, pointRadius: 0, borderWidth: 1.5, yAxisID: "y2",
        borderDash: [6, 3],
      });
    }

    _spreadChart = new Chart(canvas, {
      type: "line",
      data: { labels: dates, datasets: datasets },
      options: {
        responsive: true, maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { position: "top", labels: { usePointStyle: true, font: { size: 11 } } },
          tooltip: { mode: "index", intersect: false },
        },
        scales: {
          x: { ticks: { maxTicksLimit: 12, font: { size: 10 } }, grid: { display: false } },
          y: { position: "left", title: { display: true, text: "OAS (bps)", font: { size: 11 } }, grid: { color: "rgba(0,0,0,0.05)" } },
          y2: { position: "right", title: { display: true, text: "Curve (%)", font: { size: 11 } }, grid: { display: false } },
        },
      },
    });
  }

  /* ── Desk Notes ── */
  function openDeskNotes() {
    var popup = $("e9DeskPopup");
    var content = $("e9DeskContent");
    popup.classList.add("visible");
    content.innerHTML = '<div class="e9Loading"><div class="e9Spinner"></div><div>Generating desk brief...</div></div>';

    fetch("/api/engine9/desk-notes", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scan_data: _scanData }),
    })
      .then(function (r) { return r.ok ? r.json() : r.json().then(function (e) { throw new Error(e.detail || "Failed"); }); })
      .then(function (data) { renderDeskNotes(data); })
      .catch(function (err) {
        content.innerHTML = '<div style="color:var(--red)">Error: ' + err.message + '</div>';
      });
  }

  function renderDeskNotes(data) {
    var content = $("e9DeskContent");
    var html = "";

    if (data.phase_assessment) {
      html += "<h3>Phase Assessment</h3><p>" + esc(data.phase_assessment) + "</p>";
    }
    if (data.active_triggers_commentary) {
      html += "<h3>Active Triggers</h3><p>" + esc(data.active_triggers_commentary) + "</p>";
    }
    if (data.top_trades && data.top_trades.length) {
      html += "<h3>Top Trades</h3>";
      data.top_trades.forEach(function (t, i) {
        html += "<p><strong>" + (i + 1) + ". " + esc(t.instrument || t.ticker || "") + "</strong> — " + esc(t.action || "") + "<br>";
        if (t.sizing) html += "Size: " + esc(t.sizing) + "<br>";
        if (t.rationale) html += esc(t.rationale);
        html += "</p>";
      });
    }
    if (data.forced_seller_spotlight) {
      html += "<h3>Forced Seller Spotlight</h3><p>" + esc(data.forced_seller_spotlight) + "</p>";
    }
    if (data.risk_flags) {
      html += "<h3>Risk Flags</h3><p>" + esc(data.risk_flags) + "</p>";
    }
    if (data.invalidation_triggers) {
      html += "<h3>Invalidation</h3><p>" + esc(data.invalidation_triggers) + "</p>";
    }
    if (data.position_sizing_guidance) {
      html += "<h3>Position Sizing</h3><p>" + esc(data.position_sizing_guidance) + "</p>";
    }
    if (data.raw_text) {
      html += "<h3>Full Brief</h3><pre style='white-space:pre-wrap;font-size:11px;'>" + esc(data.raw_text) + "</pre>";
    }

    content.innerHTML = html || "<p>No desk notes generated.</p>";
  }

  /* ── Thesis Discovery ── */
  function openThesisScan() {
    var popup = $("e9ThesisPopup");
    var content = $("e9ThesisContent");
    popup.classList.add("visible");
    content.innerHTML = '<div class="e9Loading"><div class="e9Spinner"></div><div>Running thesis analysis — this may take 30-60 seconds...</div></div>';

    fetch("/api/engine9/thesis-scan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        scan_data: _scanData,
        news_data: (_scanData || {}).news || {},
        force: false,
      }),
    })
      .then(function (r) { return r.ok ? r.json() : r.json().then(function (e) { throw new Error(e.detail || "Failed"); }); })
      .then(function (data) { renderThesis(data); })
      .catch(function (err) {
        content.innerHTML = '<div style="color:var(--red)">Error: ' + err.message + '</div>';
      });
  }

  function renderThesis(data) {
    var content = $("e9ThesisContent");
    var html = "";

    if (data.one_liner) {
      html += '<div class="e9OneLiner">' + esc(data.one_liner) + '</div>';
    }

    if (data.conviction_level) {
      html += '<div style="font-size:11px;margin-bottom:12px;">Conviction: <strong>' + esc(data.conviction_level).toUpperCase() + '</strong></div>';
    }

    if (data.scenario_projection_30d) {
      html += '<div class="e9ThesisSection"><h4>30-Day Scenarios</h4><div class="e9ScenarioGrid">';
      var scenarios = data.scenario_projection_30d;
      ["base_case", "bull_case", "bear_case"].forEach(function (key) {
        var s = scenarios[key] || {};
        var label = key.replace("_", " ").replace(/\b\w/g, function (c) { return c.toUpperCase(); });
        html += '<div class="e9ScenarioCard"><div class="label">' + label + '</div>';
        html += '<div class="prob">' + esc(s.probability || "—") + '</div>';
        html += '<div style="font-size:10px;margin-top:4px;">' + esc(s.description || "") + '</div>';
        if (s.positioning) html += '<div style="font-size:10px;color:var(--muted);margin-top:4px;">' + esc(s.positioning) + '</div>';
        html += '</div>';
      });
      html += '</div></div>';
    }

    if (data.new_risks && data.new_risks.length) {
      html += '<div class="e9ThesisSection"><h4>New Risks Identified</h4>';
      data.new_risks.forEach(function (r) {
        html += '<div class="e9RiskItem"><strong>' + esc(r.risk || "") + '</strong>';
        html += ' <span style="color:var(--muted);">(' + esc(r.probability || "—") + ' / ' + esc(r.timeline || "—") + ')</span>';
        if (r.impact) html += '<br><span style="font-size:10px;color:var(--muted);">' + esc(r.impact) + '</span>';
        html += '</div>';
      });
      html += '</div>';
    }

    if (data.new_instruments_to_watch && data.new_instruments_to_watch.length) {
      html += '<div class="e9ThesisSection"><h4>New Instruments to Watch</h4>';
      data.new_instruments_to_watch.forEach(function (inst) {
        html += '<div class="e9InstrItem"><span style="font-weight:700;font-family:monospace;">' + esc(inst.ticker || "") + '</span>';
        html += ' <span style="font-size:9px;background:var(--hover);padding:1px 6px;border-radius:4px;">' + esc(inst.signal_type || "") + '</span>';
        html += '<br><span style="font-size:10px;color:var(--muted);">' + esc(inst.rationale || "") + '</span>';
        html += '</div>';
      });
      html += '</div>';
    }

    if (data.non_obvious_connections && data.non_obvious_connections.length) {
      html += '<div class="e9ThesisSection"><h4>Non-Obvious Connections</h4>';
      data.non_obvious_connections.forEach(function (c) {
        html += '<div class="e9ConnectionItem"><strong>' + esc(c.observation || "") + '</strong>';
        html += '<br><span style="font-size:10px;color:var(--muted);">' + esc(c.implication || "") + '</span>';
        html += '</div>';
      });
      html += '</div>';
    }

    if (data.signal_gaps && data.signal_gaps.length) {
      html += '<div class="e9ThesisSection"><h4>Signal Gaps</h4><ul style="margin:0;padding-left:18px;font-size:11px;">';
      data.signal_gaps.forEach(function (g) {
        html += '<li>' + esc(g) + '</li>';
      });
      html += '</ul></div>';
    }

    if (data.raw_text) {
      html += '<div class="e9ThesisSection"><h4>Raw Analysis</h4><pre style="white-space:pre-wrap;font-size:11px;">' + esc(data.raw_text) + '</pre></div>';
    }

    if (data.generated_at) {
      html += '<div style="font-size:9px;color:var(--muted);margin-top:12px;">Generated: ' + esc(data.generated_at) + '</div>';
    }

    content.innerHTML = html || "<p>No thesis generated.</p>";
  }

  /* ── Auto-refresh during market hours ── */
  function startAutoRefresh() {
    if (_refreshTimer) clearInterval(_refreshTimer);
    _refreshTimer = setInterval(function () {
      var now = new Date();
      var h = now.getUTCHours();
      if (h >= 13 && h < 21) {
        runScan();
      }
    }, 5 * 60 * 1000);
  }

  /* ── Init ── */
  function init() {
    $("e9ScanBtn").addEventListener("click", runScan);
    $("e9DeskNotesBtn").addEventListener("click", openDeskNotes);
    $("e9DeskPopupClose").addEventListener("click", function () {
      $("e9DeskPopup").classList.remove("visible");
    });
    $("e9ThesisScanBtn").addEventListener("click", openThesisScan);
    $("e9ThesisPopupClose").addEventListener("click", function () {
      $("e9ThesisPopup").classList.remove("visible");
    });
    startAutoRefresh();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
