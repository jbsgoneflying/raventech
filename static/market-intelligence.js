/* ── Raven-Tech Market Intelligence ─────────────────────────────────
   Front Layer: synthesizes all engines into a daily roadmap.
   Read-only. No trade recommendations.
   ──────────────────────────────────────────────────────────────────── */
(function () {
  "use strict";

  /* ── DOM refs ─────────────────────────────────────── */
  var runBtn           = document.getElementById("miRunBtn");
  var refreshBtn       = document.getElementById("miRefreshBtn");
  var refreshBanner    = document.getElementById("miRefreshBanner");
  var diffToggle       = document.getElementById("miDiffToggle");
  var overlay          = document.getElementById("ravenOverlay");
  var progressFill     = document.getElementById("ravenProgressFill");
  var statusLabel      = document.getElementById("ravenStatus");

  var topRow           = document.getElementById("miTopRow");
  var briefCard        = document.getElementById("miBriefCard");
  var roadmapCard      = document.getElementById("miRoadmapCard");
  var bottomGrid       = document.getElementById("miBottomGrid");
  var asymCard         = document.getElementById("miAsymCard");
  var diffPanel        = document.getElementById("miDiffPanel");

  var regimeScore      = document.getElementById("miRegimeScore");
  var regimeLabel      = document.getElementById("miRegimeLabel");
  var regimeTs         = document.getElementById("miRegimeTs");
  var engineGates      = document.getElementById("miEngineGates");
  var fpScore          = document.getElementById("miFpScore");
  var fpLabel          = document.getElementById("miFpLabel");
  var volState         = document.getElementById("miVolState");
  var briefTs          = document.getElementById("miBriefTs");
  var briefStandDown   = document.getElementById("miBriefStandDown");
  var briefContent     = document.getElementById("miBriefContent");
  var roadmapTs        = document.getElementById("miRoadmapTs");
  var roadmapContent   = document.getElementById("miRoadmapContent");
  var themesContainer  = document.getElementById("miThemes");
  var stressGrid       = document.getElementById("miStressGrid");
  var asymContent      = document.getElementById("miAsymContent");
  var diffContent      = document.getElementById("miDiffContent");

  var showDiff = false;

  /* ── Helpers ──────────────────────────────────────── */
  function h(tag, cls, html) {
    var el = document.createElement(tag);
    if (cls) el.className = cls;
    if (html !== undefined) el.innerHTML = html;
    return el;
  }

  function pillClass(label) {
    var l = (label || "").toLowerCase().replace(/[^a-z]/g, "");
    if (l === "riskon")     return "pill pill--green";
    if (l === "riskoff")    return "pill pill--red";
    if (l === "stressed")   return "pill pill--red";
    if (l === "neutral" || l === "transitional") return "pill pill--amber";
    return "pill pill--blue";
  }

  function gatePill(status) {
    var s = (status || "").toLowerCase();
    if (s === "allowed")    return '<span class="pill pill--green">' + status + '</span>';
    if (s === "selective" || s === "watch" || s === "reduced") return '<span class="pill pill--amber">' + status + '</span>';
    if (s === "suppressed") return '<span class="pill pill--red">' + status + '</span>';
    return '<span class="pill pill--blue">' + status + '</span>';
  }

  function accelArrow(accel) {
    if (accel === "rising")  return '<span class="accel--rising">&#x25B2; Rising</span>';
    if (accel === "falling") return '<span class="accel--falling">&#x25BC; Falling</span>';
    return '<span class="accel--stable">&#x25AC; Stable</span>';
  }

  function stressColor(score) {
    if (score >= 70) return "var(--red)";
    if (score >= 55) return "var(--amber)";
    if (score <= 35) return "var(--green)";
    return "var(--blue)";
  }

  function themeBarColor(intensity) {
    if (intensity >= 70) return "var(--red)";
    if (intensity >= 40) return "var(--amber)";
    return "var(--green)";
  }

  function fmt(ts) {
    if (!ts) return "";
    try { return new Date(ts).toLocaleString(); } catch (e) { return ts; }
  }

  function setProgress(pct, msg) {
    if (progressFill) progressFill.style.width = pct + "%";
    if (statusLabel) statusLabel.textContent = msg;
  }

  function showOverlay() { if (overlay) overlay.style.display = "flex"; }
  function hideOverlay() { if (overlay) overlay.style.display = "none"; }

  /* ── Data fetch ──────────────────────────────────── */
  function fetchJSON(url) {
    return fetch(url).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    });
  }

  /* ── Render: Regime + Flow Pressure ─────────────── */
  function renderDMS(dms) {
    topRow.style.display = "grid";

    var regime = dms.regime || {};
    regimeScore.textContent = (regime.score || 0).toFixed(0);
    regimeLabel.textContent = regime.state || "--";
    regimeLabel.className = "miRegimeBig " + pillClass(regime.state).replace("pill ", "");
    regimeTs.textContent = fmt(dms.generated_at);

    // Engine gates
    var gates = dms.engine_gates || {};
    var gatesHtml = "";
    var gateNames = { earnings: "Earnings", red_dog: "Red Dog", ichimoku: "Ichimoku", index_income: "Index Income" };
    for (var gk in gateNames) {
      if (gates[gk] !== undefined) {
        gatesHtml += '<span style="margin-right:8px;font-size:11px;">' + gateNames[gk] + ': ' + gatePill(gates[gk]) + '</span>';
      }
    }
    engineGates.innerHTML = gatesHtml;

    // Flow pressure
    var fp = dms.flow_pressure || {};
    fpScore.textContent = (fp.score || 0).toFixed(0);
    fpLabel.textContent = fp.state || "--";

    // Vol state
    var vs = dms.vol_state || {};
    volState.innerHTML =
      '<span style="font-weight:700;">Term:</span> ' + (vs.term_structure || "--") +
      ' &nbsp; <span style="font-weight:700;">Skew:</span> ' + (vs.skew || "--") +
      ' &nbsp; <span style="font-weight:700;">Level:</span> ' + ((vs.level || 0).toFixed(1));
  }

  /* ── Render: Morning Brief ─────────────────────── */
  function renderBrief(brief) {
    briefCard.style.display = "block";
    var isFallback = brief._source === "fallback";
    briefTs.textContent = fmt(brief._generated_at) + (isFallback ? " (fallback)" : "");

    // If fallback, show the reason prominently
    if (isFallback && brief._fallback_reason) {
      briefStandDown.innerHTML = '<div class="miStandDown" style="background:rgba(255,149,0,0.08);color:#995c00;">' +
        '<b>LLM unavailable:</b> ' + brief._fallback_reason +
        '<br><small>Showing placeholder text. Fix the issue and click Refresh Live Data.</small></div>';
      briefContent.innerHTML = "";
      return;
    }

    // Stand-down banner
    var sd = brief.stand_down || "";
    if (sd && sd.toLowerCase().indexOf("no stand-down") === -1 && sd.toLowerCase().indexOf("not required") === -1) {
      briefStandDown.innerHTML = '<div class="miStandDown">' + sd + '</div>';
    } else {
      briefStandDown.innerHTML = '<div class="miStandDown miStandDown--clear">No stand-down required.</div>';
    }

    var fields = [
      { key: "market_posture",     label: "Market Posture" },
      { key: "changes_vs_yesterday", label: "What Changed" },
      { key: "active_themes",     label: "Active Themes" },
      { key: "cross_asset_signals", label: "Cross-Asset Signals" },
      { key: "engine_alignment",   label: "Engine Alignment" },
      { key: "watch_list",         label: "Watch List" },
    ];

    var html = "";
    fields.forEach(function (f) {
      var val = brief[f.key] || "";
      if (typeof val === "object") val = JSON.stringify(val);
      html += '<div class="miBriefItem"><div class="miBriefLabel">' + f.label + '</div><div class="miBriefText">' + val + '</div></div>';
    });
    briefContent.innerHTML = html;
  }

  /* ── Render: Weekly Roadmap ────────────────────── */
  function renderRoadmap(roadmap) {
    roadmapCard.style.display = "block";
    var isFallback = roadmap._source === "fallback";
    roadmapTs.textContent = fmt(roadmap._generated_at) + (isFallback ? " (fallback)" : "");

    // If fallback, show the reason instead of placeholder text
    if (isFallback && roadmap._fallback_reason) {
      roadmapContent.innerHTML = '<div style="padding:8px 0;color:#995c00;font-size:12px;">' +
        '<b>LLM unavailable:</b> ' + roadmap._fallback_reason + '</div>';
      return;
    }

    var sections = [
      { key: "regime_flow_summary", label: "Regime & Flow Summary" },
      { key: "expected_pattern",    label: "Expected Pattern" },
      { key: "engine_behaviors",    label: "Engine Behaviors" },
      { key: "asymmetry_radar",     label: "Asymmetry Radar" },
      { key: "break_the_plan",      label: "What Would Break the Plan" },
    ];

    var html = "";
    sections.forEach(function (s) {
      var val = roadmap[s.key] || "";
      if (typeof val === "object") val = JSON.stringify(val);
      html += '<div class="miRoadmapSection">' +
        '<div class="miRoadmapHead">' + s.label + '</div>' +
        '<div class="miRoadmapBody">' + val + '</div></div>';
    });

    // High risk days
    var hrd = roadmap.high_risk_days || [];
    if (hrd.length > 0) {
      html += '<div class="miRoadmapSection"><div class="miRoadmapHead">High-Risk Days</div><div class="miRoadmapBody"><ul class="miRoadmapList">';
      hrd.forEach(function (d) { html += "<li>" + d + "</li>"; });
      html += "</ul></div></div>";
    }

    // Earnings focus
    var ef = roadmap.earnings_focus || [];
    if (ef.length > 0) {
      html += '<div class="miRoadmapSection"><div class="miRoadmapHead">Earnings Focus (max 2)</div><div class="miRoadmapBody">';
      ef.forEach(function (t) {
        html += '<span class="pill pill--blue" style="margin-right:6px;">' + t + '</span>';
      });
      html += "</div></div>";
    }

    roadmapContent.innerHTML = html;

    if (roadmap._source === "fallback") {
      roadmapTs.textContent += " (fallback)";
    }
  }

  /* ── Render: Active Themes ─────────────────────── */
  function renderThemes(dms) {
    var themes = dms.news_themes || [];
    if (themes.length === 0) {
      themesContainer.innerHTML = '<div class="miEmpty">No active themes detected.</div>';
      return;
    }

    // Show only themes with intensity > 0
    var active = themes.filter(function (t) { return (t.intensity || 0) > 0; });
    if (active.length === 0) {
      themesContainer.innerHTML = '<div class="miEmpty">All themes below threshold.</div>';
      return;
    }

    var html = "";
    active.forEach(function (t) {
      var barColor = themeBarColor(t.intensity || 0);
      html += '<div class="miThemeCard">' +
        '<div class="miThemeHeader">' +
          '<span class="miThemeName">' + (t.theme || "") + '</span>' +
          '<span class="miThemeIntensity" style="color:' + barColor + ';">' + (t.intensity || 0).toFixed(0) + '/100</span>' +
        '</div>' +
        '<div class="miThemeBar"><div class="miThemeBarFill" style="width:' + (t.intensity || 0) + '%;background:' + barColor + ';"></div></div>' +
        '<div class="miThemeMeta">' +
          accelArrow(t.acceleration || "stable") +
          ' &middot; ' + (t.persistence_days || 0) + 'd persistent' +
          ' &middot; ' + (t.keyword_hits || 0) + ' hits' +
        '</div>' +
        '<div class="miThemeMeta">Sectors: ' + (t.affected_sectors || []).join(", ") + '</div>' +
      '</div>';
    });
    themesContainer.innerHTML = html;
  }

  /* ── Render: Cross-Asset Stress ────────────────── */
  function renderStress(dms) {
    var xa = dms.cross_asset_stress || {};
    var readings = xa.readings || [];

    if (readings.length === 0) {
      stressGrid.innerHTML = '<div class="miEmpty">No cross-asset data available.</div>';
      return;
    }

    var html = '';
    // Composite header
    html += '<div class="miStressItem" style="grid-column:1/-1;border-left:3px solid ' + stressColor(xa.composite_score || 50) + ';">' +
      '<div class="miStressName">COMPOSITE</div>' +
      '<span class="miStressScore" style="color:' + stressColor(xa.composite_score || 50) + ';">' + (xa.composite_score || 50).toFixed(0) + '</span>' +
      ' <span class="' + pillClass(xa.composite_label) + '">' + (xa.composite_label || "Neutral") + '</span>' +
    '</div>';

    // Store readings for popup access
    _lastReadings = readings;

    readings.forEach(function (r, idx) {
      var sc = r.stress_score || 50;
      var relCls = "";
      if (r.equity_relationship === "confirming") relCls = "miStressRel--confirming";
      if (r.equity_relationship === "diverging") relCls = "miStressRel--diverging";

      html += '<div class="miStressItem miStressCard" data-reading-idx="' + idx + '" title="Click for desk insight">' +
        '<div class="miStressName">' + (r.name || r.symbol || "") + '</div>' +
        '<div><span class="miStressScore" style="color:' + stressColor(sc) + ';">' + sc.toFixed(0) + '</span>' +
        ' <span class="miStressDir">' + (r.direction || "flat") + '</span></div>' +
        '<div class="miStressChange">' + ((r.change_vs_prior || 0) >= 0 ? "+" : "") + (r.change_vs_prior || 0).toFixed(2) + '%</div>' +
        '<div class="miStressRel ' + relCls + '">' + (r.equity_relationship || "neutral") + ' vs equities</div>' +
      '</div>';
    });

    stressGrid.innerHTML = html;
  }

  /* ── Render: Asymmetry Radar ───────────────────── */
  function renderAsymmetry(dms) {
    var signals = dms.asymmetry_signals || [];

    if (signals.length === 0) {
      asymCard.style.display = "block";
      asymContent.innerHTML = '<div class="miEmpty">No asymmetries detected. All clear.</div>';
      return;
    }

    asymCard.style.display = "block";
    var html = "";
    signals.forEach(function (s) {
      html += '<div class="miAsymCard">' +
        '<div class="miAsymType">' + (s.type || "").replace(/_/g, " ") + '</div>' +
        '<div class="miAsymDesc">' + (s.description || "") + '</div>' +
        '<div class="miAsymAction">' + (s.action || "Monitor only.") + '</div>' +
        '<div style="font-size:10px;color:var(--muted);margin-top:4px;">Sources: ' + (s.sources || []).join(", ") + '</div>' +
      '</div>';
    });
    asymContent.innerHTML = html;
  }

  /* ── Render: Diff ──────────────────────────────── */
  function renderDiff(diffData) {
    diffPanel.style.display = showDiff ? "block" : "none";
    if (!showDiff) return;

    if (!diffData || !diffData.has_changes) {
      diffContent.innerHTML = '<div class="miEmpty">No changes detected between ' + (diffData.from_date || "yesterday") + ' and ' + (diffData.to_date || "today") + '.</div>';
      return;
    }

    var changes = diffData.changes || {};
    var html = '<div style="font-size:12px;margin-bottom:8px;">Comparing <b>' + diffData.from_date + '</b> to <b>' + diffData.to_date + '</b></div>';

    for (var section in changes) {
      html += '<div style="margin-bottom:8px;"><span style="font-weight:800;font-size:11px;text-transform:uppercase;">' + section + '</span>';
      var sectionChanges = changes[section];
      if (typeof sectionChanges === "object" && sectionChanges !== null) {
        for (var field in sectionChanges) {
          var ch = sectionChanges[field];
          if (ch && typeof ch === "object" && ch.old !== undefined) {
            html += '<div style="font-size:11px;padding-left:12px;"><span style="color:var(--muted);">' + field + ':</span> <span style="color:var(--red);">' + JSON.stringify(ch.old) + '</span> &rarr; <span style="color:var(--green);">' + JSON.stringify(ch["new"]) + '</span></div>';
          }
        }
      }
      html += '</div>';
    }
    diffContent.innerHTML = html;
  }

  /* ── Main load sequence ────────────────────────── */
  function loadAll() {
    showOverlay();
    setProgress(5, "Fetching DailyMarketState...");
    runBtn.disabled = true;

    fetchJSON("/api/front-layer/daily-market-state")
      .then(function (dms) {
        setProgress(30, "Rendering market state...");
        renderDMS(dms);
        renderThemes(dms);
        renderStress(dms);
        renderAsymmetry(dms);
        bottomGrid.style.display = "grid";

        setProgress(50, "Generating Morning Brief...");
        return fetchJSON("/api/front-layer/morning-brief").then(function (brief) {
          renderBrief(brief);
          setProgress(70, "Loading Weekly Roadmap...");
          return fetchJSON("/api/front-layer/weekly-roadmap");
        }).then(function (roadmap) {
          renderRoadmap(roadmap);
          setProgress(85, "Checking day-over-day changes...");
          return fetchJSON("/api/front-layer/diff");
        }).then(function (diffData) {
          renderDiff(diffData);
          setProgress(100, "Market Intelligence loaded.");
        });
      })
      .catch(function (err) {
        console.error("Market Intelligence load error:", err);
        setProgress(100, "Error: " + err.message);
      })
      .finally(function () {
        setTimeout(hideOverlay, 600);
        runBtn.disabled = false;
      });
  }

  /* ── Refresh Live Data ──────────────────────────── */
  function refreshLiveData() {
    if (!refreshBtn) return;
    refreshBtn.disabled = true;
    refreshBtn.classList.add("isRefreshing");
    refreshBtn.textContent = "Refreshing...";
    if (refreshBanner) {
      refreshBanner.className = "miRefreshBanner";
      refreshBanner.style.display = "block";
      refreshBanner.textContent = "Pulling fresh data from all engines and data sources...";
    }

    fetch("/api/front-layer/refresh", { method: "POST" })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (result) {
        // Show success banner
        if (refreshBanner) {
          var ts = result.refreshed_at ? fmt(result.refreshed_at) : "now";
          var llm = result.llm || {};
          var fallbackReason = llm.fallback_reason || llm.brief_error || "";
          var briefStatus = result.brief_regenerated
            ? '<span style="color:#1b8a3e;">Brief generated (LLM)</span>'
            : '<span style="color:#995c00;">Brief fallback' +
              (fallbackReason ? ' — ' + fallbackReason : (llm.openai_key_set ? ' — check logs' : ' — no OpenAI key')) +
              '</span>';
          refreshBanner.className = "miRefreshBanner miRefreshBanner--ok";
          refreshBanner.innerHTML =
            '<b>Live data refreshed</b> at ' + ts +
            ' &middot; Regime: ' + ((result.regime || {}).state || "?") +
            ' (' + ((result.regime || {}).score || 0).toFixed(0) + ')' +
            ' &middot; Flow: ' + ((result.flow_pressure || {}).state || "?") +
            ' &middot; Cross-asset: ' + (result.cross_asset_readings || 0) + ' readings' +
            ' &middot; ' + (result.theme_count || 0) + ' themes' +
            ' &middot; ' + briefStatus;
        }

        // Now reload all panels with the fresh data
        return fetchJSON("/api/front-layer/daily-market-state").then(function (dms) {
          renderDMS(dms);
          renderThemes(dms);
          renderStress(dms);
          renderAsymmetry(dms);
          topRow.style.display = "grid";
          bottomGrid.style.display = "grid";

          return fetchJSON("/api/front-layer/morning-brief");
        }).then(function (brief) {
          renderBrief(brief);
        });
      })
      .catch(function (err) {
        console.error("Refresh error:", err);
        if (refreshBanner) {
          refreshBanner.className = "miRefreshBanner";
          refreshBanner.style.display = "block";
          refreshBanner.textContent = "Refresh failed: " + err.message;
        }
      })
      .finally(function () {
        refreshBtn.disabled = false;
        refreshBtn.classList.remove("isRefreshing");
        refreshBtn.textContent = "Refresh Live Data";
      });
  }

  /* ── Event bindings ────────────────────────────── */
  if (runBtn) {
    runBtn.addEventListener("click", loadAll);
  }

  if (refreshBtn) {
    refreshBtn.addEventListener("click", refreshLiveData);
  }

  if (diffToggle) {
    diffToggle.addEventListener("click", function () {
      showDiff = !showDiff;
      diffToggle.textContent = showDiff ? "Hide Changes" : "Show Changes";
      if (showDiff && diffPanel.style.display === "none") {
        fetchJSON("/api/front-layer/diff").then(renderDiff).catch(function () {});
      }
      diffPanel.style.display = showDiff ? "block" : "none";
    });
  }

  /* ── Asset Insight Popup (dark draggable) ──────── */
  var _lastReadings = [];
  var _insightCache = {};  // keyed by asset name to avoid repeat LLM calls

  var insightPopup  = document.getElementById("miInsightPopup");
  var insightHeader = document.getElementById("miInsightHeader");
  var insightTitle  = document.getElementById("miInsightTitle");
  var insightClose  = document.getElementById("miInsightClose");
  var insightBody   = document.getElementById("miInsightBody");

  // Drag state
  var _dragState = { isDragging: false, offsetX: 0, offsetY: 0 };

  function startInsightDrag(e) {
    if (!insightPopup) return;
    if (e.target.closest(".miInsightClose")) return;
    _dragState.isDragging = true;
    insightPopup.classList.add("isDragging");
    var clientX = e.touches ? e.touches[0].clientX : e.clientX;
    var clientY = e.touches ? e.touches[0].clientY : e.clientY;
    var rect = insightPopup.getBoundingClientRect();
    _dragState.offsetX = clientX - rect.left;
    _dragState.offsetY = clientY - rect.top;
    e.preventDefault();
  }

  function doInsightDrag(e) {
    if (!_dragState.isDragging) return;
    if (!insightPopup) return;
    var clientX = e.touches ? e.touches[0].clientX : e.clientX;
    var clientY = e.touches ? e.touches[0].clientY : e.clientY;
    var newX = clientX - _dragState.offsetX;
    var newY = clientY - _dragState.offsetY;
    var maxX = window.innerWidth - insightPopup.offsetWidth;
    var maxY = window.innerHeight - insightPopup.offsetHeight;
    newX = Math.max(0, Math.min(newX, maxX));
    newY = Math.max(0, Math.min(newY, maxY));
    insightPopup.style.left = newX + "px";
    insightPopup.style.top = newY + "px";
  }

  function endInsightDrag() {
    if (!_dragState.isDragging) return;
    if (insightPopup) insightPopup.classList.remove("isDragging");
    _dragState.isDragging = false;
  }

  document.addEventListener("mousemove", doInsightDrag);
  document.addEventListener("mouseup", endInsightDrag);
  document.addEventListener("touchmove", doInsightDrag, { passive: false });
  document.addEventListener("touchend", endInsightDrag);

  function showInsightPopup(reading, clickEvent) {
    if (!insightPopup) return;
    var name = reading.name || reading.symbol || "Asset";
    insightTitle.textContent = name + " — Desk Insight";

    // Loading state
    insightBody.innerHTML =
      '<div class="miInsightLoading">' +
      '<span class="miInsightDot"></span>' +
      '<span class="miInsightDot"></span>' +
      '<span class="miInsightDot"></span>' +
      '<br>Generating desk insight...</div>';

    // Position near click
    var posX = (clickEvent ? clickEvent.clientX : window.innerWidth / 2) + 20;
    var posY = (clickEvent ? clickEvent.clientY : window.innerHeight / 2) - 120;
    if (posX + 420 > window.innerWidth) posX = (clickEvent ? clickEvent.clientX : 200) - 420;
    if (posX < 16) posX = 16;
    if (posY < 16) posY = 16;
    if (posY + 400 > window.innerHeight) posY = window.innerHeight - 420;
    insightPopup.style.left = posX + "px";
    insightPopup.style.top = posY + "px";
    insightPopup.style.display = "block";

    // Attach drag to header
    insightHeader.removeEventListener("mousedown", startInsightDrag);
    insightHeader.removeEventListener("touchstart", startInsightDrag);
    insightHeader.addEventListener("mousedown", startInsightDrag);
    insightHeader.addEventListener("touchstart", startInsightDrag, { passive: false });

    // Check cache
    var cacheKey = name;
    if (_insightCache[cacheKey]) {
      renderInsight(_insightCache[cacheKey], reading);
      return;
    }

    // Fetch from LLM
    fetch("/api/front-layer/asset-insight", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ asset: reading }),
    })
    .then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    })
    .then(function (insight) {
      _insightCache[cacheKey] = insight;
      renderInsight(insight, reading);
    })
    .catch(function (err) {
      insightBody.innerHTML =
        '<div class="miInsightText" style="color:rgba(255,200,150,0.8);">Insight unavailable: ' + err.message + '</div>';
    });
  }

  function renderInsight(insight, reading) {
    var sc = (reading.stress_score || 50).toFixed(0);
    var dir = reading.direction || "flat";
    var rel = reading.equity_relationship || "neutral";
    var chg = ((reading.change_vs_prior || 0) >= 0 ? "+" : "") + (reading.change_vs_prior || 0).toFixed(2) + "%";

    var html = '';
    // Meta row
    html += '<div class="miInsightMeta">' +
      '<div class="miInsightMetaItem">Stress Score<br><span class="miInsightMetaValue" style="color:' + stressColor(parseFloat(sc)) + ';">' + sc + '/100</span></div>' +
      '<div class="miInsightMetaItem">Direction<br><span class="miInsightMetaValue">' + dir + ' (' + chg + ')</span></div>' +
      '<div class="miInsightMetaItem">vs Equities<br><span class="miInsightMetaValue">' + rel + '</span></div>' +
      '<div class="miInsightMetaItem">Asset Class<br><span class="miInsightMetaValue">' + (reading.asset_class || "--") + '</span></div>' +
    '</div>';

    var sections = [
      { key: "what_its_telling_us", title: "What This Asset Is Telling Us" },
      { key: "why_it_matters",      title: "Why It Matters for Equities" },
      { key: "context",             title: "Context" },
      { key: "desk_takeaway",       title: "Desk Takeaway" },
    ];

    sections.forEach(function (s) {
      var val = insight[s.key] || "";
      if (!val) return;
      var isDesk = s.key === "desk_takeaway";
      html += '<div class="miInsightSection">' +
        '<div class="miInsightSectionTitle">' + s.title + '</div>' +
        '<div class="miInsightText"' + (isDesk ? ' style="font-weight:700;color:rgba(255,255,255,0.95);"' : '') + '>' + val + '</div>' +
      '</div>';
    });

    // Source + fallback reason
    if (insight._source === "fallback" && insight._fallback_reason) {
      html += '<div class="miInsightSource" style="color:rgba(255,180,100,0.6);">Fallback: ' + insight._fallback_reason + '</div>';
    } else if (insight._source === "llm") {
      html += '<div class="miInsightSource">Generated by LLM &middot; Read-only &middot; Not a trade recommendation</div>';
    }

    insightBody.innerHTML = html;
  }

  function hideInsightPopup() {
    if (insightPopup) insightPopup.style.display = "none";
  }

  // Close button
  if (insightClose) {
    insightClose.addEventListener("click", hideInsightPopup);
  }

  // Escape to close
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") hideInsightPopup();
  });

  // Click on stress cards
  if (stressGrid) {
    stressGrid.addEventListener("click", function (e) {
      var card = e.target.closest(".miStressCard");
      if (!card) return;
      var idx = parseInt(card.getAttribute("data-reading-idx"), 10);
      if (isNaN(idx) || !_lastReadings[idx]) return;
      showInsightPopup(_lastReadings[idx], e);
    });
  }

  /* ── Backfill status check ──────────────────────── */
  var backfillStatus = document.getElementById("miBackfillStatus");

  function checkBackfillStatus() {
    if (!backfillStatus) return;
    fetchJSON("/api/front-layer/backfill-status")
      .then(function (data) {
        backfillStatus.style.display = "flex";
        if (data.seeded) {
          var range = data.date_range || {};
          var crossAssetDays = (data.days || []).filter(function (d) { return d.has_cross_asset; }).length;
          var themeDays = (data.days || []).filter(function (d) { return d.has_themes; }).length;
          backfillStatus.className = "miBackfillStatus miBackfillStatus--seeded";
          backfillStatus.innerHTML =
            '<span class="miBackfillDot miBackfillDot--green"></span>' +
            '<span><b>Historical data seeded</b> &middot; ' +
            data.snapshot_count + ' snapshots (' +
            (range.earliest || "?") + ' to ' + (range.latest || "?") + ')' +
            ' &middot; Cross-asset: ' + crossAssetDays + 'd' +
            ' &middot; Themes: ' + themeDays + 'd' +
            '</span>';
        } else {
          backfillStatus.className = "miBackfillStatus miBackfillStatus--empty";
          backfillStatus.innerHTML =
            '<span class="miBackfillDot miBackfillDot--amber"></span>' +
            '<span><b>No historical data</b> &middot; ' +
            'Run <code>python3 scripts/backfill_front_layer.py</code> to seed 14 days of cross-asset and theme history for richer insights.</span>';
        }
      })
      .catch(function () {
        // Silently ignore – non-critical
      });
  }

  // Check backfill status on page load
  checkBackfillStatus();

  /* ── Auto-load on page open ────────────────────── */
  hideOverlay();

})();
