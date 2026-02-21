/* ── Engine 7: Thematic Relative Value (Pairs) ──────────────────────────
   Frontend controller for the pairs scanner page.
   ──────────────────────────────────────────────────────────────────────── */
(function () {
  "use strict";

  var form       = document.getElementById("e7Form");
  var runBtn     = document.getElementById("runBtn");
  var statusEl   = document.getElementById("status");
  var resultsEl  = document.getElementById("results");

  /* ── Helpers ────────────────────────────────────────────────────────── */
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
  function buildPairCard(sig) {
    var card = document.createElement("div");
    card.className = "pairCard pairsClick";

    var modeClass = (sig.mode || "").replace(/\s+/g, "_").toLowerCase();
    var modeLabel = sig.mode === "mean_reversion" ? "Mean Rev" : sig.mode === "momentum" ? "Momentum" : sig.mode || "—";
    var score = sig.confidence || sig.score || 0;

    var html = '<div class="pairCardHeader">';
    html += '<div class="pairCardPair">';
    html += '<span class="pairCardSymbol">' + (sig.longAsset || sig.long_asset || "?") + " / " + (sig.shortAsset || sig.short_asset || "?") + '</span>';
    html += '<span class="tierChip ' + tierClass(sig.tier) + '">' + tierLabel(sig.tier) + '</span>';
    html += '</div>';
    html += '<span class="pairCardMode ' + modeClass + '">' + modeLabel + '</span>';
    html += '<span class="pairCardGrade ' + gradeClass(score) + '">' + gradeLabel(score) + " · " + Math.round(score) + '</span>';
    html += '</div>';

    html += '<div class="pairCardBody">';
    html += '<div class="pairCardMetric"><span class="k">Z-Score</span><span class="v">' + fmt(sig.zScore || sig.z_score) + '</span></div>';
    html += '<div class="pairCardMetric"><span class="k">ROC 5d</span><span class="v">' + pct(sig.roc5d || sig.roc_5d) + '</span></div>';
    html += '<div class="pairCardMetric"><span class="k">ROC 10d</span><span class="v">' + pct(sig.roc10d || sig.roc_10d) + '</span></div>';
    html += '<div class="pairCardMetric"><span class="k">Confidence</span><span class="v">' + Math.round(score) + '</span></div>';
    if (sig.suggestedRiskUnits || sig.risk_units) {
      html += '<div class="pairCardMetric"><span class="k">Risk Units</span><span class="v">' + fmt(sig.suggestedRiskUnits || sig.risk_units, 1) + '</span></div>';
    }
    if (sig.holdingPeriod || sig.holding_period) {
      html += '<div class="pairCardMetric"><span class="k">Hold</span><span class="v">' + (sig.holdingPeriod || sig.holding_period) + 'd</span></div>';
    }
    html += '</div>';

    var themes = sig.themes || sig.supportingThemes || sig.supporting_themes || [];
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

    card.innerHTML = html;
    return card;
  }

  /* ── Render results ────────────────────────────────────────────────── */
  function render(data) {
    var aPlus    = data.aPlus || [];
    var standard = data.standard || [];
    var watchlist = data.watchlist || [];
    var ineligible = data.ineligible || [];
    var total = aPlus.length + standard.length + watchlist.length + ineligible.length;

    qs("statScanned").textContent  = total || "20";
    qs("statEligible").textContent = aPlus.length + standard.length + watchlist.length;
    qs("statAPlus").textContent    = aPlus.length;
    qs("statThemes").textContent   = data.activeThemeCount || data.active_theme_count || "—";
    qs("statsMeta").textContent    = data.asOf || data.as_of || new Date().toISOString().slice(0, 10);

    /* Themes section */
    var themesGrid = qs("themesGrid");
    themesGrid.innerHTML = "";
    var themeNames = data.activeThemes || data.active_themes || [];
    if (Array.isArray(themeNames) && themeNames.length) {
      themeNames.forEach(function (t) {
        var name = typeof t === "string" ? t : t.theme || t.name || "";
        if (!name) return;
        var chip = document.createElement("span");
        chip.className = "themeChip";
        chip.textContent = name;
        themesGrid.appendChild(chip);
      });
    } else {
      themesGrid.innerHTML = '<span style="color:var(--muted); font-size:12px;">No active themes detected</span>';
    }

    if (data.llmAnnotation || data.llm_annotation) {
      var note = qs("llmThemeNote");
      note.style.display = "block";
      note.textContent = "LLM annotation: " + (data.llmAnnotation || data.llm_annotation);
    }

    /* A+ */
    var aplusGrid = qs("aplusGrid");
    aplusGrid.innerHTML = "";
    if (aPlus.length) {
      qs("aplusMeta").textContent = aPlus.length + " pair" + (aPlus.length !== 1 ? "s" : "");
      aPlus.forEach(function (s) { aplusGrid.appendChild(buildPairCard(s)); });
    } else {
      aplusGrid.innerHTML = '<div class="emptyState"><div class="emptyStateTitle">No A+ Pairs</div><div class="emptyStateBody">No pairs reached the A+ confidence threshold (75+). Check standard and watchlist sections.</div></div>';
      qs("aplusMeta").textContent = "0 pairs";
    }

    /* Standard */
    var stdGrid = qs("standardGrid");
    stdGrid.innerHTML = "";
    if (standard.length) {
      qs("standardMeta").textContent = standard.length + " pair" + (standard.length !== 1 ? "s" : "");
      standard.forEach(function (s) { stdGrid.appendChild(buildPairCard(s)); });
    } else {
      stdGrid.innerHTML = '<div class="emptyState"><div class="emptyStateTitle">No Standard Pairs</div><div class="emptyStateBody">No eligible pairs in the standard range.</div></div>';
      qs("standardMeta").textContent = "0 pairs";
    }

    /* Watchlist */
    var wGrid = qs("watchlistGrid");
    wGrid.innerHTML = "";
    if (watchlist.length) {
      qs("watchlistMeta").textContent = watchlist.length + " pair" + (watchlist.length !== 1 ? "s" : "");
      watchlist.forEach(function (s) { wGrid.appendChild(buildPairCard(s)); });
    } else {
      wGrid.innerHTML = '<div class="emptyState"><div class="emptyStateTitle">No Watchlist Pairs</div><div class="emptyStateBody">No developing setups below threshold.</div></div>';
      qs("watchlistMeta").textContent = "0 pairs";
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
        statusEl.textContent = "Scan complete.";
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
