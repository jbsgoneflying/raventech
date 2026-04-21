/* ================================================================== */
/* Raven Desk Insight v2 — shared frontend module.                     */
/*                                                                     */
/* One popup + renderer + fetcher across every page. Each page calls   */
/*                                                                     */
/*   DeskInsight.bind({                                                */
/*     engineId:           "e1",                                       */
/*     dividerSelector:    ".deskDivider[data-insight]",               */
/*     slugTitles:         { breach_stats: "Breach Statistics", ... }, */
/*     getCardData:        (slug) => { ... },                          */
/*     getScenarioContext: ()     => { ... },                          */
/*   });                                                               */
/*                                                                     */
/* DeskInsight auto-creates the popup DOM, auto-injects "i" buttons    */
/* on every divider, renders the nine-section schema, and supports    */
/* cross-engine navigation via related_cards chips with breadcrumbs.   */
/* ================================================================== */
(function (global) {
  "use strict";

  // ---- Constants --------------------------------------------------

  const ENDPOINT = "/api/desk-insight";
  const CATALOG_ENDPOINT = "/api/desk-insight/catalog";
  const SECTION_ORDER = [
    { key: "what_this_shows",  label: "What This Shows",  icon: "" },
    { key: "how_to_read_it",   label: "How To Read It",   icon: "" },
    { key: "quant_mechanics",  label: "Quant Mechanics",  icon: "" },
    { key: "how_to_use_it",    label: "How To Use It",    icon: "" },
    { key: "example_scenario", label: "Example Scenario", icon: "" },
    { key: "watch_for",        label: "Watch For",        icon: "" },
    { key: "common_mistakes",  label: "Common Mistakes",  icon: "" },
    { key: "desk_takeaway",    label: "Desk Takeaway",    icon: "" },
  ];
  const POPUP_MAX_WIDTH  = 500;
  const POPUP_MAX_HEIGHT_FRAC = 0.82;
  const POPUP_MARGIN     = 12;
  const BREADCRUMB_DEPTH = 3;

  // ---- Module state ----------------------------------------------

  // Map of engineId -> binding config.
  const bindings = Object.create(null);

  // Shared in-memory response cache: key = engine|slug|hash -> payload.
  const responseCache = Object.create(null);

  // Titles index resolved from GET /api/desk-insight/catalog once per page.
  // Used to label cross-engine chips when the LLM didn't supply a label.
  let catalogTitles = null;  // { engine: { slug: title } }
  let catalogPromise = null;

  // Navigation stack for cross-engine back-navigation.
  let navStack = [];
  let activeButton = null;

  // ---- Utilities --------------------------------------------------

  function $(id) { return document.getElementById(id); }

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function hashPayload(obj) {
    try {
      const s = JSON.stringify(obj);
      let h = 0;
      for (let i = 0; i < s.length; i++) { h = (h * 31 + s.charCodeAt(i)) | 0; }
      return String(h);
    } catch (e) { return String(Date.now()); }
  }

  function ensureCatalogTitles() {
    if (catalogTitles) return Promise.resolve(catalogTitles);
    if (catalogPromise) return catalogPromise;
    catalogPromise = fetch(CATALOG_ENDPOINT)
      .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
      .then(function (j) {
        catalogTitles = (j && j.engines) || {};
        return catalogTitles;
      })
      .catch(function () {
        // Feature flag off or catalog endpoint unavailable — cross-engine
        // chips fall back to their LLM-supplied labels only.
        catalogTitles = {};
        return catalogTitles;
      });
    return catalogPromise;
  }

  function resolveRelatedLabel(engine, slug, label) {
    if (label) return label;
    const t = catalogTitles && catalogTitles[engine] && catalogTitles[engine][slug];
    return t || slug;
  }

  // ---- Popup DOM (auto-created once per page) --------------------

  function ensurePopupDom() {
    if ($("deskInsightPopup")) return;
    const pop = document.createElement("div");
    pop.className = "deskInsightPopup";
    pop.id = "deskInsightPopup";
    pop.setAttribute("role", "dialog");
    pop.setAttribute("aria-labelledby", "deskInsightTitle");
    pop.innerHTML = [
      '<div class="deskInsightHeader" id="deskInsightHeader">',
      '  <div class="deskInsightHeaderText">',
      '    <span class="deskInsightTitle" id="deskInsightTitle">Desk Insight</span>',
      '    <span class="deskInsightSubtitle" id="deskInsightSubtitle"></span>',
      '  </div>',
      '  <button class="deskInsightClose" id="deskInsightClose" type="button" aria-label="Close">&times;</button>',
      '</div>',
      '<div class="deskInsightBreadcrumb" id="deskInsightBreadcrumb" style="display:none;"></div>',
      '<div class="deskInsightBody" id="deskInsightBody">',
      '  <div class="deskInsightLoading">',
      '    <span class="deskInsightDot"></span><span class="deskInsightDot"></span><span class="deskInsightDot"></span>',
      '    <br>Generating desk insight…',
      '  </div>',
      '</div>',
    ].join("");
    document.body.appendChild(pop);

    $("deskInsightClose").addEventListener("click", closePopup);
    if (typeof global.initDrag === "function") {
      try { global.initDrag(pop, $("deskInsightHeader"), { closeSelector: "#deskInsightClose" }); }
      catch (e) { /* ignore */ }
    }
    document.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape" && pop.style.display === "block") closePopup();
    });
    document.addEventListener("mousedown", function (ev) {
      if (pop.style.display !== "block") return;
      const t = ev.target;
      if (t && t.closest && (t.closest("#deskInsightPopup") || t.closest(".deskInsightBtn"))) return;
      closePopup();
    });
  }

  function openPopup(title, subtitle, anchor) {
    ensurePopupDom();
    const pop = $("deskInsightPopup");
    $("deskInsightTitle").textContent = title || "Desk Insight";
    $("deskInsightSubtitle").textContent = subtitle || "";
    $("deskInsightBody").innerHTML =
      '<div class="deskInsightLoading">' +
      '<span class="deskInsightDot"></span><span class="deskInsightDot"></span><span class="deskInsightDot"></span>' +
      '<br>Generating desk insight…</div>';

    const vw = global.innerWidth;
    const vh = global.innerHeight;
    const pw = POPUP_MAX_WIDTH;
    const ph = Math.min(560, Math.floor(vh * POPUP_MAX_HEIGHT_FRAC));
    const r = anchor && anchor.getBoundingClientRect ? anchor.getBoundingClientRect() : null;
    let left, top;
    if (r) {
      left = Math.max(POPUP_MARGIN, Math.min(vw - pw - POPUP_MARGIN, r.right + POPUP_MARGIN));
      top  = Math.max(POPUP_MARGIN, Math.min(vh - ph - POPUP_MARGIN, r.top));
    } else {
      left = Math.max(POPUP_MARGIN, Math.floor(vw / 2 - pw / 2));
      top  = Math.max(POPUP_MARGIN, Math.floor(vh / 4));
    }
    pop.style.left = left + "px";
    pop.style.top  = top  + "px";
    pop.style.display = "block";
    return pop;
  }

  function closePopup() {
    const pop = $("deskInsightPopup");
    if (pop) pop.style.display = "none";
    if (activeButton) {
      activeButton.setAttribute("aria-expanded", "false");
      activeButton = null;
    }
    navStack = [];
    const bc = $("deskInsightBreadcrumb");
    if (bc) { bc.style.display = "none"; bc.innerHTML = ""; }
  }

  // ---- Breadcrumb rendering --------------------------------------

  function renderBreadcrumb() {
    const bc = $("deskInsightBreadcrumb");
    if (!bc) return;
    if (navStack.length <= 1) { bc.style.display = "none"; bc.innerHTML = ""; return; }
    const bits = [];
    navStack.slice(0, -1).forEach(function (step, idx) {
      bits.push(
        '<button type="button" data-desk-nav-idx="' + idx + '">' +
        escapeHtml(step.title) + '</button>'
      );
      bits.push('<span class="deskInsightBreadcrumb-sep">›</span>');
    });
    bits.push('<span>' + escapeHtml(navStack[navStack.length - 1].title) + '</span>');
    bc.innerHTML = bits.join("");
    bc.style.display = "flex";
    Array.prototype.forEach.call(
      bc.querySelectorAll("button[data-desk-nav-idx]"),
      function (btn) {
        btn.addEventListener("click", function () {
          const idx = parseInt(btn.getAttribute("data-desk-nav-idx"), 10);
          if (isNaN(idx) || idx < 0 || idx >= navStack.length) return;
          const step = navStack[idx];
          navStack = navStack.slice(0, idx);  // drop tail including current
          openInsightForStep(step, /*anchor*/ null);
        });
      }
    );
  }

  // ---- Rendering --------------------------------------------------

  function renderInsight(data) {
    const body = $("deskInsightBody");
    if (!body) return;
    if (!data) {
      body.innerHTML = '<div class="deskInsightLoading">No insight data.</div>';
      return;
    }
    let html = "";
    if (data._source === "fallback" && data._fallback_reason) {
      html +=
        '<div class="deskInsightFallbackBanner">Spec fallback · ' +
        escapeHtml(data._fallback_reason) + '</div>';
    }
    SECTION_ORDER.forEach(function (sec) {
      const v = data[sec.key];
      if (!v) return;
      const extraClass = (sec.key === "desk_takeaway") ? " deskTakeaway" : "";
      html +=
        '<div class="deskInsightSection">' +
          '<div class="deskInsightSectionTitle">' + escapeHtml(sec.label) + '</div>' +
          '<div class="deskInsightText' + extraClass + '">' + escapeHtml(v) + '</div>' +
        '</div>';
    });
    // Related cards footer.
    const related = Array.isArray(data.related_cards) ? data.related_cards : [];
    if (related.length) {
      const chips = related.map(function (rc) {
        const eng = escapeHtml(rc.engine || "");
        const slug = escapeHtml(rc.slug || "");
        const label = escapeHtml(resolveRelatedLabel(rc.engine, rc.slug, rc.label));
        return (
          '<button type="button" class="deskInsightRelatedChip" ' +
          'data-related-engine="' + eng + '" data-related-slug="' + slug + '">' +
          '<span class="deskInsightRelatedEngine">' + eng + '</span>' +
          '<span>' + label + '</span>' +
          '</button>'
        );
      }).join("");
      html +=
        '<div class="deskInsightSection">' +
          '<div class="deskInsightSectionTitle">Related Cards</div>' +
          '<div class="deskInsightRelatedCards">' + chips + '</div>' +
        '</div>';
    }
    // Source footer.
    const srcBits = [];
    if (data._source) srcBits.push(data._source);
    if (data._meta && data._meta.model) srcBits.push(data._meta.model);
    if (data._engine) srcBits.push("engine=" + data._engine);
    if (srcBits.length) {
      html += '<div class="deskInsightSource">' + escapeHtml(srcBits.join(" · ")) + '</div>';
    }
    body.innerHTML = html || '<div class="deskInsightLoading">No insight content returned.</div>';

    // Wire related-card chip clicks.
    Array.prototype.forEach.call(
      body.querySelectorAll(".deskInsightRelatedChip"),
      function (chip) {
        chip.addEventListener("click", function (ev) {
          ev.preventDefault();
          ev.stopPropagation();
          const eng  = chip.getAttribute("data-related-engine");
          const slug = chip.getAttribute("data-related-slug");
          if (!eng || !slug) return;
          openInsightForChip(eng, slug);
        });
      }
    );
  }

  function renderError(detail) {
    const body = $("deskInsightBody");
    if (!body) return;
    body.innerHTML =
      '<div class="deskInsightLoading deskInsightErrorBanner">' +
      'Failed to load desk insight: ' + escapeHtml(String(detail)) +
      '</div>';
  }

  // ---- Fetch / cache ---------------------------------------------

  function fetchInsight(engine, slug, cardData, scenarioContext) {
    const ckey = engine + "|" + slug + "|" + hashPayload(cardData);
    if (responseCache[ckey]) return Promise.resolve(responseCache[ckey]);
    return fetch(ENDPOINT, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        engine:          engine,
        cardType:        slug,
        cardData:        cardData == null ? {} : cardData,
        scenarioContext: scenarioContext || {},
      }),
    }).then(function (r) {
      return r.json().then(function (j) {
        if (!r.ok) {
          const detail = (j && (j.detail || j.error)) || ("HTTP " + r.status);
          throw new Error(detail);
        }
        return j;
      });
    }).then(function (j) { responseCache[ckey] = j; return j; });
  }

  // ---- Open / navigate -------------------------------------------

  function titleFor(engine, slug, binding) {
    if (binding && binding.slugTitles && binding.slugTitles[slug]) {
      return binding.slugTitles[slug];
    }
    const t = catalogTitles && catalogTitles[engine] && catalogTitles[engine][slug];
    return t || slug;
  }

  function openInsightForSlug(engine, slug, anchor) {
    const binding = bindings[engine];
    const title = titleFor(engine, slug, binding);
    const subtitle = engine.toUpperCase() + " · " + slug;
    openPopup(title, subtitle, anchor);
    navStack.push({ engine: engine, slug: slug, title: title });
    if (navStack.length > BREADCRUMB_DEPTH + 1) navStack.shift();
    renderBreadcrumb();

    // If the engine is bound on this page, use its extractors; otherwise
    // still try to fetch with empty payload (cross-engine navigation).
    let cardData = {};
    let scenarioContext = {};
    if (binding) {
      try { cardData = binding.getCardData ? binding.getCardData(slug) : {}; }
      catch (e) { cardData = {}; }
      try { scenarioContext = binding.getScenarioContext ? binding.getScenarioContext() : {}; }
      catch (e) { scenarioContext = {}; }
    }

    fetchInsight(engine, slug, cardData, scenarioContext)
      .then(renderInsight)
      .catch(function (e) { renderError(e && e.message || e); });
  }

  function openInsightForChip(engine, slug) {
    ensureCatalogTitles().then(function () {
      openInsightForSlug(engine, slug, /*anchor*/ null);
    });
  }

  function openInsightForStep(step, anchor) {
    const engine = step.engine;
    const slug   = step.slug;
    const binding = bindings[engine];
    const title = titleFor(engine, slug, binding);
    const subtitle = engine.toUpperCase() + " · " + slug;
    openPopup(title, subtitle, anchor);
    navStack.push({ engine: engine, slug: slug, title: title });
    renderBreadcrumb();

    let cardData = {};
    let scenarioContext = {};
    if (binding) {
      try { cardData = binding.getCardData ? binding.getCardData(slug) : {}; }
      catch (e) { cardData = {}; }
      try { scenarioContext = binding.getScenarioContext ? binding.getScenarioContext() : {}; }
      catch (e) { scenarioContext = {}; }
    }
    fetchInsight(engine, slug, cardData, scenarioContext)
      .then(renderInsight)
      .catch(function (e) { renderError(e && e.message || e); });
  }

  // ---- Info-button injector --------------------------------------

  function injectButtonsFor(binding) {
    const selector = binding.dividerSelector || ".deskDivider[data-insight]";
    Array.prototype.forEach.call(
      document.querySelectorAll(selector),
      function (div) {
        if (div.querySelector(".deskInsightBtn")) return;
        if (!div.querySelector(".deskDividerText")) {
          const wrap = document.createElement("span");
          wrap.className = "deskDividerText";
          while (div.firstChild) wrap.appendChild(div.firstChild);
          div.appendChild(wrap);
        }
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "deskInsightBtn";
        btn.setAttribute("aria-label", "Open desk insight");
        btn.setAttribute("aria-expanded", "false");
        btn.title = "Open desk insight";
        btn.textContent = "i";
        div.appendChild(btn);
      }
    );
  }

  function onDocumentClick(ev) {
    const btn = ev.target && ev.target.closest && ev.target.closest(".deskInsightBtn");
    if (!btn) return;
    const div = btn.closest(".deskDivider[data-insight]");
    if (!div) return;
    ev.preventDefault();
    ev.stopPropagation();

    const slug = div.getAttribute("data-insight");
    const engine = div.getAttribute("data-insight-engine")
      || Object.keys(bindings)[0];  // default to first binding
    if (!slug || !engine) return;

    const pop = $("deskInsightPopup");
    if (activeButton === btn && pop && pop.style.display === "block") {
      closePopup();
      return;
    }
    if (activeButton) activeButton.setAttribute("aria-expanded", "false");
    activeButton = btn;
    btn.setAttribute("aria-expanded", "true");
    navStack = [];
    openInsightForSlug(engine, slug, btn);
  }

  // ---- Public API -------------------------------------------------

  const DeskInsight = {
    /**
     * Bind desk-insight behavior to the current page.
     *
     * @param {object} opts
     * @param {string} opts.engineId            — engine id (e.g. "e1", "e14", "market-intel").
     * @param {string} [opts.dividerSelector]   — CSS selector for insight dividers.
     * @param {object} [opts.slugTitles]        — optional {slug: title} override for popup headers.
     * @param {function} [opts.getCardData]     — (slug) => cardData JSON slice.
     * @param {function} [opts.getScenarioContext] — () => scenario context JSON.
     */
    bind: function (opts) {
      opts = opts || {};
      if (!opts.engineId) { console.warn("DeskInsight.bind: engineId is required"); return; }

      // Stamp engine onto every matching divider so cross-bound pages
      // know which engine each divider belongs to.
      const selector = opts.dividerSelector || ".deskDivider[data-insight]";
      Array.prototype.forEach.call(
        document.querySelectorAll(selector),
        function (d) {
          if (!d.hasAttribute("data-insight-engine")) {
            d.setAttribute("data-insight-engine", opts.engineId);
          }
        }
      );

      bindings[opts.engineId] = {
        engineId:           opts.engineId,
        dividerSelector:    selector,
        slugTitles:         opts.slugTitles || {},
        getCardData:        opts.getCardData || function () { return {}; },
        getScenarioContext: opts.getScenarioContext || function () { return {}; },
      };

      ensurePopupDom();
      ensureCatalogTitles();
      injectButtonsFor(bindings[opts.engineId]);
    },

    /** Manually re-scan the DOM for new dividers — call after dynamic render. */
    refresh: function () {
      Object.keys(bindings).forEach(function (eid) {
        injectButtonsFor(bindings[eid]);
      });
    },

    /** Wipe the in-memory response cache — call when a scenario is re-run. */
    clearCache: function () {
      Object.keys(responseCache).forEach(function (k) { delete responseCache[k]; });
    },

    /** Programmatically open a specific engine+slug tooltip. */
    open: function (engine, slug) {
      openInsightForChip(engine, slug);
    },

    /** Close any open popup. */
    close: closePopup,
  };

  // Auto-bind: pages that include desk-insight.js and a
  // <meta name="raven-desk-engine" content="e1"> tag in the <head>
  // get a zero-code binding. For pages that need per-slug extractors
  // (getCardData / getScenarioContext), call DeskInsight.bind()
  // explicitly from the page script.
  function autoBind() {
    const meta = document.querySelector('meta[name="raven-desk-engine"]');
    if (!meta) return;
    const engine = (meta.getAttribute("content") || "").trim();
    if (!engine) return;
    if (bindings[engine]) return;   // already explicitly bound
    DeskInsight.bind({ engineId: engine });
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", autoBind);
  } else {
    autoBind();
  }

  // Single delegated click handler — wired once per page.
  document.addEventListener("click", onDocumentClick);

  global.DeskInsight = DeskInsight;
})(window);
