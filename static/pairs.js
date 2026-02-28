/* ── Engine 7: Thematic Relative Value (Pairs) ──────────────────────────
   Frontend controller for the pairs scanner page.
   ──────────────────────────────────────────────────────────────────────── */
(function () {
  "use strict";

  var form       = document.getElementById("e7Form");
  var runBtn     = document.getElementById("runBtn");
  var statusEl   = document.getElementById("status");
  var resultsEl  = document.getElementById("results");

  function qs(id) { return document.getElementById(id); }
  function fmt(v, d) { return v == null ? "—" : Number(v).toFixed(d == null ? 2 : d); }
  function pct(v) { return v == null ? "—" : (Number(v) * 100).toFixed(1) + "%"; }

  function gradeClass(score) {
    if (score >= 75) return "grade-aplus";
    if (score >= 60) return "grade-a";
    if (score >= 45) return "grade-b";
    return "";
  }

  function gradeLabel(score) {
    if (score >= 75) return "A+";
    if (score >= 60) return "A";
    if (score >= 45) return "B";
    return "C";
  }

  function tierClass(tier) {
    if (tier === 1) return "tier1";
    if (tier === 2) return "tier2";
    if (tier === 3) return "tier3";
    return "";
  }

  function tierLabel(tier) {
    if (tier === 1) return "Tier 1";
    if (tier === 2) return "Tier 2";
    if (tier === 3) return "Tier 3";
    return "—";
  }

  /* ── Build a pair card ─────────────────────────────────────────────── */
  function buildPairCard(sig, isIneligible) {
    var card = document.createElement("div");
    card.className = "pairCard pairsClick" + (isIneligible ? " pairCard--ineligible" : "");

    var modeClass = (sig.mode || "").replace(/\s+/g, "_").toLowerCase();
    var modeLabel = sig.mode === "mean_reversion" ? "Mean Rev" : sig.mode === "momentum" ? "Momentum" : sig.mode || "—";
    var score = sig.confidence_score || 0;
    var priceScore = sig.price_only_score;

    var html = '<div class="pairCardHeader">';
    html += '<div class="pairCardPair">';
    html += '<span class="pairCardSymbol">' + (sig.long_asset || "?") + " / " + (sig.short_asset || "?") + '</span>';
    html += '<span class="tierChip ' + tierClass(sig.tier) + '">' + tierLabel(sig.tier) + '</span>';
    html += '</div>';
    html += '<span class="pairCardMode ' + modeClass + '">' + modeLabel + '</span>';
    if (isIneligible && priceScore != null) {
      html += '<span class="pairCardGrade" style="opacity:0.6;" title="Price-only score (no theme weight)">P: ' + priceScore + '</span>';
    } else {
      html += '<span class="pairCardGrade ' + gradeClass(score) + '">' + gradeLabel(score) + " · " + Math.round(score) + '</span>';
    }
    html += '</div>';

    html += '<div class="pairCardBody">';
    html += '<div class="pairCardMetric"><span class="k">Z-Score</span><span class="v">' + fmt(sig.z_score) + '</span></div>';
    html += '<div class="pairCardMetric"><span class="k">ROC 5d</span><span class="v">' + pct(sig.momentum_5d_roc) + '</span></div>';
    html += '<div class="pairCardMetric"><span class="k">ROC 10d</span><span class="v">' + pct(sig.momentum_10d_roc) + '</span></div>';
    html += '<div class="pairCardMetric"><span class="k">Confidence</span><span class="v">' + Math.round(score) + '</span></div>';

    if (isIneligible) {
      html += '<div class="pairCardMetric"><span class="k">Score (Z)</span><span class="v">' + fmt(sig.score_z) + '</span></div>';
      html += '<div class="pairCardMetric"><span class="k">Score (Mom)</span><span class="v">' + fmt(sig.score_momentum) + '</span></div>';
      html += '<div class="pairCardMetric"><span class="k">Score (Trend)</span><span class="v">' + fmt(sig.score_trend) + '</span></div>';
    }

    if (sig.risk_units) {
      html += '<div class="pairCardMetric"><span class="k">Risk Units</span><span class="v">' + fmt(sig.risk_units, 1) + '</span></div>';
    }
    if (sig.expected_hold_days) {
      html += '<div class="pairCardMetric"><span class="k">Hold</span><span class="v">' + sig.expected_hold_days + 'd</span></div>';
    }
    html += '</div>';

    var themes = sig.theme_tags || [];
    if (themes.length) {
      html += '<div class="pairCardThemes">';
      themes.forEach(function (t) {
        html += '<span class="themeChip">' + t + '</span>';
      });
      html += '</div>';
    }

    if (sig.gateDecision) {
      var gd = sig.gateDecision;
      var gateColor = gd.action === "ALLOW" ? "rgba(52,199,89,0.8)" : gd.action === "SUPPRESS" ? "rgba(255,59,48,0.8)" : "rgba(255,149,0,0.8)";
      html += '<div class="pairCardNotes" style="color:' + gateColor + ';">Gate: ' + (gd.action || "—") + (gd.reasons ? " — " + gd.reasons.join(", ") : "") + '</div>';
    }

    if (sig.eligibility === "NOT_ELIGIBLE" && sig.ineligibility_reason) {
      html += '<div class="pairCardNotes" style="color:var(--caution);">Ineligible: ' + sig.ineligibility_reason + '</div>';
    }

    card.innerHTML = html;
    card._signalData = sig;
    return card;
  }

  /* ── Render results ────────────────────────────────────────────────── */
  function render(data) {
    var aPlus         = data.aPlus || [];
    var standard      = data.standard || [];
    var watchlist     = data.watchlist || [];
    var ineligible    = data.ineligible || [];
    var meta          = data.meta || {};
    var activeThemes  = data.activeThemes || [];
    var diagThemes    = data.themeDiagnostics || [];

    qs("statScanned").textContent  = meta.pairsAnalyzed || (aPlus.length + standard.length + watchlist.length + ineligible.length) || "20";
    qs("statEligible").textContent = aPlus.length + standard.length + watchlist.length;
    qs("statAPlus").textContent    = aPlus.length;
    qs("statThemes").textContent   = (meta.activeThemeCount != null ? meta.activeThemeCount : activeThemes.length);
    qs("statsMeta").textContent    = meta.scanDate || new Date().toISOString().slice(0, 10);

    /* Headline / scan diagnostics banner */
    var diagLines = [];
    var topics = (meta.headlineTopics || []).join("+") || "market";
    diagLines.push("Headlines: " + (meta.headlineCount || 0) + " (limit " + (meta.headlineFetchLimit || "?") + ", topics: " + topics + ")");
    diagLines.push("Window: " + (meta.headlineWindowStart || "?") + " → " + (meta.headlineWindowEnd || "?"));
    if (meta.recencyDecay) diagLines.push("Decay: " + meta.recencyDecay);
    diagLines.push("Active themes: " + (meta.activeThemeCount || 0) + " / " + diagThemes.length + " candidates");
    if (meta.dynamicThemeCount) {
      diagLines.push("Dynamic: " + meta.dynamicThemeCount + " (" + (meta.dynamicThemeNames || []).join(", ") + ")");
    }
    if (meta.activeThemeNames && meta.activeThemeNames.length) {
      diagLines.push("Active: " + meta.activeThemeNames.join(", "));
    }
    statusEl.textContent = diagLines.join(" · ");

    /* Themes section */
    var dynamicNames = new Set(meta.dynamicThemeNames || []);
    var themesGrid = qs("themesGrid");
    themesGrid.innerHTML = "";
    if (Array.isArray(activeThemes) && activeThemes.length) {
      activeThemes.forEach(function (t) {
        var name = typeof t === "string" ? t : t.label || t.theme || "";
        var themeId = typeof t === "string" ? t : t.theme || "";
        if (!name) return;
        var chip = document.createElement("span");
        var isDynamic = dynamicNames.has(themeId);
        chip.className = "themeChip" + (isDynamic ? " themeChip--dynamic" : "");
        chip.textContent = name + (isDynamic ? " [Dynamic]" : "");
        var tipParts = [];
        if (t.keyword_hits) tipParts.push(t.keyword_hits + " keyword hits");
        if (t.intensity) tipParts.push("intensity " + t.intensity);
        if (isDynamic) tipParts.push("LLM-sourced theme");
        chip.title = tipParts.join(", ");
        themesGrid.appendChild(chip);
      });
    } else {
      var noThemeHtml = '<span style="color:var(--muted); font-size:12px;">No active themes detected (' + (meta.headlineCount || 0) + ' headlines scanned). ';
      noThemeHtml += 'All ' + ineligible.length + ' pairs shown below with price-only scoring.</span>';
      themesGrid.innerHTML = noThemeHtml;
    }

    /* Theme diagnostics (all candidates — collapsible) */
    if (diagThemes.length) {
      var diagEl = document.createElement("details");
      diagEl.style.cssText = "margin-top:8px; font-size:12px; color:var(--muted);";
      var sumEl = document.createElement("summary");
      sumEl.style.cursor = "pointer";
      sumEl.textContent = "Theme Diagnostics (" + diagThemes.length + " candidates)";
      diagEl.appendChild(sumEl);
      var table = '<table style="width:100%; border-collapse:collapse; margin-top:4px; font-size:11px;">';
      table += '<tr style="text-align:left;"><th>Theme</th><th>Source</th><th>Hits</th><th>Intensity</th><th>Status</th><th>Keywords</th></tr>';
      diagThemes.forEach(function (td) {
        var color = td.active ? "var(--accent)" : "var(--muted)";
        var source = td.dynamic ? "LLM" : "Static";
        table += '<tr style="color:' + color + ';">';
        table += '<td>' + (td.label || td.theme) + '</td>';
        table += '<td>' + source + '</td>';
        table += '<td>' + td.keyword_hits + '</td>';
        table += '<td>' + (td.intensity || 0) + '</td>';
        table += '<td>' + (td.active ? "ACTIVE" : "inactive") + '</td>';
        table += '<td>' + (td.sample_keywords || []).join(", ") + '</td>';
        table += '</tr>';
      });
      table += '</table>';
      var tableDiv = document.createElement("div");
      tableDiv.innerHTML = table;
      diagEl.appendChild(tableDiv);
      themesGrid.appendChild(diagEl);
    }

    var llmAnn = data.llmAnnotation;
    if (llmAnn) {
      var note = qs("llmThemeNote");
      note.style.display = "block";
      var summary = typeof llmAnn === "string" ? llmAnn : llmAnn.macro_summary || JSON.stringify(llmAnn);
      note.textContent = "LLM annotation: " + summary;
    }

    /* A+ */
    var aplusGrid = qs("aplusGrid");
    aplusGrid.innerHTML = "";
    if (aPlus.length) {
      qs("aplusMeta").textContent = aPlus.length + " pair" + (aPlus.length !== 1 ? "s" : "");
      aPlus.forEach(function (s) { aplusGrid.appendChild(buildPairCard(s, false)); });
    } else {
      aplusGrid.innerHTML = '<div class="emptyState"><div class="emptyStateTitle">No A+ Pairs</div><div class="emptyStateBody">No pairs reached the A+ confidence threshold (75+). Check standard and watchlist sections.</div></div>';
      qs("aplusMeta").textContent = "0 pairs";
    }

    /* Standard */
    var stdGrid = qs("standardGrid");
    stdGrid.innerHTML = "";
    if (standard.length) {
      qs("standardMeta").textContent = standard.length + " pair" + (standard.length !== 1 ? "s" : "");
      standard.forEach(function (s) { stdGrid.appendChild(buildPairCard(s, false)); });
    } else {
      stdGrid.innerHTML = '<div class="emptyState"><div class="emptyStateTitle">No Standard Pairs</div><div class="emptyStateBody">No eligible pairs in the standard range.</div></div>';
      qs("standardMeta").textContent = "0 pairs";
    }

    /* Watchlist */
    var wGrid = qs("watchlistGrid");
    wGrid.innerHTML = "";
    if (watchlist.length) {
      qs("watchlistMeta").textContent = watchlist.length + " pair" + (watchlist.length !== 1 ? "s" : "");
      watchlist.forEach(function (s) { wGrid.appendChild(buildPairCard(s, false)); });
    } else {
      wGrid.innerHTML = '<div class="emptyState"><div class="emptyStateTitle">No Watchlist Pairs</div><div class="emptyStateBody">No developing setups below threshold.</div></div>';
      qs("watchlistMeta").textContent = "0 pairs";
    }

    /* Ineligible — always rendered as cards so z-score/momentum is visible */
    var inelGrid = qs("ineligibleGrid");
    inelGrid.innerHTML = "";
    if (ineligible.length) {
      qs("ineligibleMeta").textContent = ineligible.length + " pair" + (ineligible.length !== 1 ? "s" : "") + " — price-only scoring shown";
      ineligible.forEach(function (s) { inelGrid.appendChild(buildPairCard(s, true)); });
    } else {
      inelGrid.innerHTML = '<div class="emptyState"><div class="emptyStateTitle">No Ineligible Pairs</div><div class="emptyStateBody">All pairs have active theme support.</div></div>';
      qs("ineligibleMeta").textContent = "0 pairs";
    }
  }

  /* ── Submit handler ────────────────────────────────────────────────── */
  form.addEventListener("submit", function (e) {
    e.preventDefault();
    var tier     = qs("tier").value;
    var mode     = qs("mode").value;
    var minScore = qs("minScore").value;

    var params = new URLSearchParams();
    params.set("min_score", minScore);
    if (tier) params.set("tier", tier);
    if (mode) params.set("mode", mode);

    runBtn.disabled = true;
    runBtn.querySelector(".btnSpinner").style.display = "inline-block";
    statusEl.textContent = "Scanning 20 asset pairs…";
    resultsEl.classList.add("hidden");

    if (window.RavenLoading) window.RavenLoading.show("Evaluating pairs universe…");

    fetch("/api/engine7-pairs?" + params.toString())
      .then(function (r) {
        if (!r.ok) return r.json().then(function (d) { throw new Error(d.detail || r.statusText); });
        return r.json();
      })
      .then(function (data) {
        render(data);
        resultsEl.classList.remove("hidden");
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

  /* ── Clear cache button ───────────────────────────────────────────── */
  var clearBtn = document.getElementById("clearCacheBtn");
  if (clearBtn) {
    clearBtn.addEventListener("click", function () {
      clearBtn.disabled = true;
      clearBtn.textContent = "Clearing…";
      fetch("/api/engine7-pairs/clear-cache", { method: "POST" })
        .then(function (r) { return r.json(); })
        .then(function () {
          clearBtn.textContent = "Cleared ✓";
          setTimeout(function () {
            clearBtn.textContent = "Clear Cache & Rescan";
            clearBtn.disabled = false;
            form.dispatchEvent(new Event("submit", { cancelable: true }));
          }, 500);
        })
        .catch(function () {
          clearBtn.textContent = "Clear Cache & Rescan";
          clearBtn.disabled = false;
        });
    });
  }

  /* ── Desk View popup (GPT-5.2 on-click insight) ───────────────────── */
  var popup       = document.getElementById("pairsInsightPopup");
  var popupTitle  = document.getElementById("pairsInsightTitle");
  var popupBody   = document.getElementById("pairsInsightBody");
  var popupClose  = document.getElementById("pairsInsightClose");
  var popupHeader = document.getElementById("pairsInsightHeader");

  var _deskViewCache = {};
  var _activeAbort = null;

  function showPopup(cardEl) {
    var sig = cardEl._signalData;
    if (!sig) return;

    var pairLabel = (sig.long_asset || "?") + " / " + (sig.short_asset || "?");
    var cacheKey = sig.pair_id || pairLabel;
    popupTitle.textContent = "Desk View — " + pairLabel;

    /* Position near the card */
    var rect = cardEl.getBoundingClientRect();
    popup.style.top = Math.max(10, rect.top + window.scrollY - 20) + "px";
    popup.style.left = Math.min(rect.right + 16, window.innerWidth - 470) + "px";
    popup.style.display = "block";

    /* Check cache */
    if (_deskViewCache[cacheKey]) {
      renderDeskView(_deskViewCache[cacheKey]);
      return;
    }

    /* Loading state */
    popupBody.innerHTML =
      '<div class="pairsInsightLoading">' +
      '<span class="pairsInsightDot"></span><span class="pairsInsightDot"></span><span class="pairsInsightDot"></span>' +
      '<br>Generating desk view with GPT-5.2…</div>';

    /* Abort previous request */
    if (_activeAbort) { try { _activeAbort.abort(); } catch (e) {} }
    var abortCtrl = new AbortController();
    _activeAbort = abortCtrl;

    fetch("/api/engine7-pairs/desk-view", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ signal: sig }),
      signal: abortCtrl.signal,
    })
      .then(function (r) {
        if (!r.ok) return r.json().then(function (d) { throw new Error(d.detail || r.statusText); });
        return r.json();
      })
      .then(function (data) {
        _deskViewCache[cacheKey] = data;
        renderDeskView(data);
      })
      .catch(function (err) {
        if (err.name === "AbortError") return;
        popupBody.innerHTML =
          '<div class="pairsInsightSection">' +
          '<div class="pairsInsightText" style="color:rgba(255,100,100,0.9);">Error: ' + err.message + '</div></div>';
      });
  }

  var _DESK_VIEW_SECTIONS = [
    { key: "thesis",          title: "Trade Thesis",      icon: "📋" },
    { key: "market_context",  title: "Market Context",    icon: "🌐" },
    { key: "how_to_enter",    title: "How to Enter",      icon: "▶" },
    { key: "how_to_exit",     title: "How to Exit",       icon: "⏹" },
    { key: "what_breaks_it",  title: "What Breaks It",    icon: "⚠" },
    { key: "risk_management", title: "Risk Management",   icon: "🛡" },
    { key: "learning_note",   title: "Learning Note",     icon: "💡" },
  ];

  function renderDeskView(data) {
    var html = "";
    _DESK_VIEW_SECTIONS.forEach(function (sec) {
      var val = data[sec.key];
      if (!val) return;
      html += '<div class="pairsInsightSection">';
      html += '<div class="pairsInsightSectionTitle">' + sec.icon + " " + sec.title + '</div>';
      html += '<div class="pairsInsightText">' + val + '</div>';
      html += '</div>';
    });
    if (data._source) {
      html += '<div class="pairsInsightSource">Generated by ' + data._source + '</div>';
    }
    popupBody.innerHTML = html;
  }

  /* Close popup */
  if (popupClose) {
    popupClose.addEventListener("click", function () {
      popup.style.display = "none";
      if (_activeAbort) { try { _activeAbort.abort(); } catch (e) {} }
    });
  }

  /* Click on card → show desk view (skip if tooltip or details click) */
  document.addEventListener("click", function (e) {
    if (e.target.closest(".tipBtn") || e.target.closest(".tipPanel") || e.target.closest("summary") || e.target.closest("details")) return;
    var card = e.target.closest(".pairCard");
    if (card && card._signalData) {
      showPopup(card);
      return;
    }
    /* Click outside popup closes it */
    if (popup.style.display === "block" && !e.target.closest(".pairsInsightPopup")) {
      popup.style.display = "none";
    }
  });

  initDrag(popup, popupHeader, { closeSelector: ".pairsInsightClose" });

  /* ── LLM Review Status (loads on page open) ────────────────────────── */
  (function loadLlmStatus() {
    fetch("/api/engine7-pairs/dynamic-themes")
      .then(function (r) { return r.json(); })
      .then(function (d) { renderLlmStatus(d); })
      .catch(function () { renderLlmStatus(null); });
  })();

  function renderLlmStatus(data) {
    var dot = qs("llmStatusDot");
    var metaEl = qs("llmStatusMeta");
    var lastEl = qs("llmLastReview");
    var ageEl = qs("llmLastReviewAge");
    var modelEl = qs("llmModel");
    var activeCountEl = qs("llmActiveCount");
    var activeMaxEl = qs("llmActiveMax");
    var pendingCountEl = qs("llmPendingCount");
    var themesList = qs("llmThemesList");
    var auditLog = qs("llmAuditLog");

    if (!data) {
      metaEl.textContent = "Unable to load review status";
      return;
    }

    var lastReview = data.lastReview;
    var now = new Date();

    if (!lastReview) {
      dot.className = "llmStatusDot llmStatusDot--never";
      lastEl.textContent = "Never";
      ageEl.textContent = "No reviews yet";
      metaEl.textContent = "Cron not yet run — first review pending";
    } else {
      var reviewDate = new Date(lastReview + "T05:00:00Z");
      var hoursAgo = Math.round((now - reviewDate) / (1000 * 60 * 60));
      var isOk = hoursAgo < 36;
      dot.className = "llmStatusDot " + (isOk ? "llmStatusDot--ok" : "llmStatusDot--stale");
      lastEl.textContent = lastReview;
      ageEl.textContent = hoursAgo < 24 ? hoursAgo + "h ago" : Math.round(hoursAgo / 24) + "d ago";
      metaEl.textContent = isOk ? "System healthy — reviewed recently" : "Stale — last review " + Math.round(hoursAgo / 24) + "d ago";
    }

    modelEl.textContent = data.model || "—";
    activeCountEl.textContent = data.activeCount || 0;
    activeMaxEl.textContent = "of " + (data.maxActive || 3) + " max";
    pendingCountEl.textContent = data.pendingCount || 0;

    /* Theme rows */
    themesList.innerHTML = "";
    var allThemes = data.themes || {};
    var themeIds = Object.keys(allThemes);

    if (themeIds.length === 0) {
      themesList.innerHTML = '<div class="llmNoData">No dynamic themes — static coverage is sufficient. The LLM will propose themes when it detects a narrative gap.</div>';
    } else {
      themeIds.forEach(function (tid) {
        var t = allThemes[tid];
        var status = t.status || "pending";
        var row = document.createElement("div");
        row.className = "llmThemeRow";

        var badge = '<span class="llmThemeBadge llmThemeBadge--' + status + '">' + status + '</span>';
        var name = '<span class="llmThemeName">' + (t.label || tid) + '</span>';

        var details = [];
        if (t.headline_saturation) details.push(Math.round(t.headline_saturation * 100) + "% saturation");
        if (t.activation_reason) details.push(t.activation_reason.replace(/_/g, " "));
        if (t.keywords && t.keywords.length) details.push(t.keywords.length + " keywords");
        if (t.pair_mappings && t.pair_mappings.length) details.push(t.pair_mappings.length + " pairs mapped");
        if (t.first_seen) details.push("since " + t.first_seen);

        var detailHtml = details.length ? '<span class="llmThemeDetail">' + details.join(" · ") + '</span>' : '';

        row.innerHTML = badge + name + detailHtml;
        themesList.appendChild(row);

        if (t.reasoning) {
          var reasonRow = document.createElement("div");
          reasonRow.style.cssText = "font-size:11px; color:var(--muted); padding:0 0 6px 70px; font-style:italic;";
          reasonRow.textContent = t.reasoning;
          themesList.appendChild(reasonRow);
        }
      });
    }

    /* Audit log */
    var entries = (data.auditLog || []).slice().reverse();
    if (entries.length === 0) {
      auditLog.innerHTML = '<div class="llmNoData">No audit entries yet.</div>';
    } else {
      auditLog.innerHTML = "";
      entries.forEach(function (e) {
        var row = document.createElement("div");
        row.className = "llmAuditRow";
        var actionClass = "llmAuditAction--" + (e.action || "").toLowerCase();
        row.innerHTML =
          '<span class="llmAuditAction ' + actionClass + '">' + (e.action || "—") + '</span>' +
          '<span style="font-weight:600;">' + (e.theme || "—") + '</span>' +
          '<span>' + (e.reason || "") + '</span>' +
          '<span style="margin-left:auto; flex-shrink:0;">' + (e.date || "") + '</span>';
        auditLog.appendChild(row);
      });
    }
  }

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
