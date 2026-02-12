/* ── Raven-Tech 2.0 – Command Center JS ─────────────────────────────── */
(function () {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => document.querySelectorAll(sel);

  /* ── Helpers ───────────────────────────────────────────────── */

  function pillClass(label) {
    const l = (label || "").toLowerCase().replace(/[_-]/g, "");
    if (l === "riskon" || l === "tradable" || l === "supportive") return "pill--green";
    if (l === "riskoff" || l === "suppress" || l === "hostile" || l === "stressed") return "pill--red";
    return "pill--amber";
  }

  function barColor(val) {
    if (val >= 65) return "var(--green)";
    if (val <= 35) return "var(--red)";
    return "var(--amber)";
  }

  function changeArrow(delta) {
    if (delta > 0) return { cls: "ccChange--up", text: "+" + delta.toFixed(1) };
    if (delta < 0) return { cls: "ccChange--down", text: delta.toFixed(1) };
    return { cls: "ccChange--flat", text: "—" };
  }

  /* ── Flow Pressure ─────────────────────────────────────────── */

  async function loadFlowPressure() {
    try {
      const res = await fetch("/api/command-center/flow-pressure");
      if (!res.ok) throw new Error(res.statusText);
      const data = await res.json();

      const fp = data.flowPressure || {};
      const score = fp.composite_score != null ? fp.composite_score : "--";
      const label = fp.composite_label || "Neutral";

      $("#fpScore").textContent = typeof score === "number" ? Math.round(score) : score;
      const fpPill = $("#fpPill");
      fpPill.textContent = label;
      fpPill.className = "pill " + pillClass(label);

      // Change indicator from SPX
      const spx = (fp.symbols || {}).SPX || {};
      const change = spx.change_since_prior;
      if (change != null) {
        const a = changeArrow(change);
        const el = $("#fpChange");
        el.className = "ccChange " + a.cls;
        el.textContent = a.text;
      }

      // Sub-bars
      const components = spx.components || {};
      const subBars = $("#fpSubBars");
      subBars.innerHTML = "";
      const labels = {
        dealer_gamma_support: "Dealer Gamma",
        vol_term_structure_drift: "Vol Term Structure",
        em_richness_skew: "EM Richness",
        liquidity_tape_stress: "Liquidity",
        macro_event_density: "Macro Density",
      };
      for (const [key, lbl] of Object.entries(labels)) {
        const val = components[key] != null ? components[key] : 50;
        const item = document.createElement("div");
        item.className = "ccSubBarItem";
        item.innerHTML = `
          <span class="label">${lbl}</span>
          <div class="bar"><div class="fill" style="width:${val}%;background:${barColor(val)}"></div></div>
          <span class="val">${Math.round(val)}</span>
        `;
        subBars.appendChild(item);
      }

      // Card border color
      const fpCard = $("#fpCard");
      if (label === "Risk-On") fpCard.style.borderLeftColor = "var(--green)";
      else if (label === "Risk-Off") fpCard.style.borderLeftColor = "var(--red)";
      else fpCard.style.borderLeftColor = "var(--amber)";

      // Regime card
      const regime = data.regime || {};
      const regimeLabel = regime.label || regime.current_label || "—";
      const regimeScore = regime.score != null ? Math.round(regime.score) : "";
      $("#regimeLabel").textContent = regimeLabel;
      const rPill = $("#regimePill");
      rPill.textContent = regimeScore ? `Score: ${regimeScore}` : "";
      rPill.className = "pill " + pillClass(regimeLabel);

      const drivers = regime.components || {};
      const driverHtml = Object.entries(drivers)
        .sort((a, b) => b[1] - a[1])
        .slice(0, 2)
        .map(([k, v]) => `<span>${k.replace(/_/g, " ")}: ${Math.round(v)}</span>`)
        .join(" · ");
      $("#regimeDrivers").innerHTML = driverHtml || "No driver data";

      // Vol state card
      const vol = data.volState || {};
      const volDir = vol.global_vol_direction || vol.globalVolDirection || "—";
      $("#volDirection").textContent = volDir.charAt(0).toUpperCase() + volDir.slice(1);
      const volDetails = [];
      if (vol.us_iv_state || vol.usIvState) volDetails.push(`US IV: ${vol.us_iv_state || vol.usIvState}`);
      if (vol.vol_lag_state || vol.volLagState) volDetails.push(`Lag: ${vol.vol_lag_state || vol.volLagState}`);
      if (vol.structure_bias || vol.structureBias) volDetails.push(`Bias: ${vol.structure_bias || vol.structureBias}`);
      $("#volDetails").innerHTML = volDetails.join(" · ") || "No vol data available";

    } catch (e) {
      console.error("Flow pressure load failed:", e);
      $("#fpScore").textContent = "!";
    }
  }

  /* ── Sequencer ─────────────────────────────────────────────── */

  async function loadSequencer() {
    try {
      const res = await fetch("/api/command-center/sequencer");
      if (!res.ok) throw new Error(res.statusText);
      const data = await res.json();

      const days = data.tradingDays || [];
      const seq = data.sequence || {};
      const events = seq.events || [];
      const patterns = data.patterns || {};

      // Build timeline
      const timeline = $("#seqTimeline");
      timeline.innerHTML = "";
      const dayNames = ["Mon", "Tue", "Wed", "Thu", "Fri"];
      const today = new Date().toISOString().slice(0, 10);

      days.forEach((d, i) => {
        const dayEl = document.createElement("div");
        dayEl.className = "seqDay";
        if (d === today) dayEl.style.background = "rgba(0,122,255,0.06)";

        const label = document.createElement("div");
        label.className = "seqDayLabel";
        label.textContent = dayNames[i] || d.slice(5);
        dayEl.appendChild(label);

        // Find events for this day
        const dayEvents = events.filter((e) => e.date === d);
        if (dayEvents.length === 0) {
          const noEv = document.createElement("div");
          noEv.style.cssText = "font-size:10px; color:var(--muted2); margin-top:8px;";
          noEv.textContent = "—";
          dayEl.appendChild(noEv);
        } else {
          dayEvents.forEach((ev) => {
            const chip = document.createElement("div");
            chip.className = "seqChip";
            chip.textContent = (ev.event_type || "").replace(/_/g, " ").toLowerCase();
            chip.title = ev.summary || "";
            dayEl.appendChild(chip);
          });
        }
        timeline.appendChild(dayEl);
      });

      // Pattern match
      const pName = seq.pattern_match;
      const pConf = seq.pattern_confidence || 0;
      const tmpl = patterns[pName] || {};
      if (pName) {
        $("#seqPatternName").textContent = tmpl.label || pName.replace(/_/g, " ");
        $("#seqPatternConf").textContent = `Confidence: ${Math.round(pConf * 100)}% · ${seq.primary_risk || ""}`;
      } else {
        $("#seqPatternName").textContent = "No pattern matched yet";
        $("#seqPatternConf").textContent = events.length === 0 ? "No signal flips this week" : "Sequence in progress";
      }

      // Pattern library
      const list = $("#patternList");
      list.innerHTML = "";
      for (const [key, pat] of Object.entries(patterns)) {
        const item = document.createElement("div");
        item.className = "patternItem";
        const isMatch = key === pName;
        if (isMatch) item.style.background = "rgba(52,199,89,0.06)";
        item.innerHTML = `
          <div class="name">${pat.label || key}${isMatch ? ' <span class="pill pill--green" style="font-size:8px;">MATCH</span>' : ""}</div>
          <div class="desc">${pat.description || ""}</div>
        `;
        list.appendChild(item);
      }

    } catch (e) {
      console.error("Sequencer load failed:", e);
    }
  }

  /* ── Tradable Ideas ────────────────────────────────────────── */

  async function loadIdeas() {
    try {
      const res = await fetch("/api/command-center/tradable-ideas");
      if (!res.ok) throw new Error(res.statusText);
      const data = await res.json();
      const ideas = data.ideas || [];

      const tbody = $("#ideasBody");
      const empty = $("#ideasEmpty");
      tbody.innerHTML = "";

      if (ideas.length === 0) {
        empty.style.display = "block";
        return;
      }
      empty.style.display = "none";

      ideas.forEach((idea) => {
        const gate = idea.gate || {};
        const status = gate.status || "TRADABLE";
        const reasons = (gate.reasons || []).map((r) => r.label || r.code || "").join(", ");

        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td><strong>${idea.ticker || "—"}</strong></td>
          <td>${idea.engine || "—"}</td>
          <td>${idea.setupType || "—"}</td>
          <td>${idea.direction || "—"}</td>
          <td><span class="pill ${pillClass(status)}">${status}</span>${reasons ? '<br><span style="font-size:10px;color:var(--muted);">' + reasons + "</span>" : ""}</td>
          <td>${idea.score || "—"}</td>
          <td style="max-width:180px;">${idea.whyNow || "—"}</td>
          <td style="max-width:180px;">${idea.whatBreaks || "—"}</td>
        `;
        tbody.appendChild(tr);
      });
    } catch (e) {
      console.error("Ideas load failed:", e);
    }
  }

  /* ── Desk Brief ────────────────────────────────────────────── */

  async function loadDeskBrief() {
    try {
      const res = await fetch("/api/command-center/desk-brief");
      if (!res.ok) throw new Error(res.statusText);
      const data = await res.json();
      const brief = data.brief || {};

      $("#briefMarket").textContent = brief.market_state || "Unavailable";
      $("#briefBias").textContent = brief.weekly_bias || "Unavailable";
      $("#briefRisks").textContent = brief.top_risks || "Unavailable";
    } catch (e) {
      console.error("Desk brief load failed:", e);
      $("#briefMarket").textContent = "Failed to load";
    }
  }

  /* ── Bootstrap banner ─────────────────────────────────────── */

  function showBootstrapBanner(msg) {
    let banner = document.getElementById("ccBootstrapBanner");
    if (!banner) {
      banner = document.createElement("div");
      banner.id = "ccBootstrapBanner";
      banner.style.cssText =
        "position:fixed;top:0;left:0;right:0;z-index:9999;padding:10px 20px;" +
        "background:var(--amber,#f5a623);color:#000;text-align:center;font-size:13px;" +
        "font-weight:600;box-shadow:0 2px 8px rgba(0,0,0,.15);transition:opacity .3s;";
      document.body.prepend(banner);
    }
    banner.textContent = msg;
    banner.style.display = "block";
    banner.style.opacity = "1";
  }

  function hideBootstrapBanner() {
    const banner = document.getElementById("ccBootstrapBanner");
    if (banner) {
      banner.style.opacity = "0";
      setTimeout(() => { banner.style.display = "none"; }, 400);
    }
  }

  /* ── Init ──────────────────────────────────────────────────── */

  async function init() {
    // Kick off background engine bootstrap (non-blocking)
    showBootstrapBanner("Initializing engines… data will refresh automatically.");

    fetch("/api/command-center/init")
      .then((r) => r.json())
      .then((d) => {
        if (d.status === "initializing") {
          // Engines are bootstrapping; poll for data readiness
          pollForData();
        } else {
          // Already running or instant – just load
          hideBootstrapBanner();
        }
      })
      .catch(() => { hideBootstrapBanner(); });

    // Load whatever data is available right now
    await Promise.all([
      loadFlowPressure(),
      loadSequencer(),
      loadIdeas(),
      loadDeskBrief(),
    ]);
  }

  /** Re-load data sections periodically until tradable ideas appear
   *  (indicates engines have finished scanning). */
  function pollForData() {
    let attempts = 0;
    const maxAttempts = 20;  // ~5 min max polling
    const interval = 15000; // 15 seconds between polls

    const timer = setInterval(async () => {
      attempts++;
      try {
        const [ideasRes, fpRes] = await Promise.all([
          fetch("/api/command-center/tradable-ideas").then((r) => r.json()),
          fetch("/api/command-center/flow-pressure").then((r) => r.json()),
        ]);

        // Refresh all sections
        await Promise.all([
          loadFlowPressure(),
          loadSequencer(),
          loadIdeas(),
          loadDeskBrief(),
        ]);

        const hasIdeas = (ideasRes.ideas || []).length > 0;
        const hasRegime = fpRes.regime && Object.keys(fpRes.regime).length > 0;

        if ((hasIdeas && hasRegime) || attempts >= maxAttempts) {
          clearInterval(timer);
          hideBootstrapBanner();
        } else {
          showBootstrapBanner(
            `Engines loading… refreshing data (${attempts}/${maxAttempts})`
          );
        }
      } catch (e) {
        console.warn("Poll error:", e);
        if (attempts >= maxAttempts) {
          clearInterval(timer);
          hideBootstrapBanner();
        }
      }
    }, interval);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
