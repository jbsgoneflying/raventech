/**
 * News Risk Engine - Weekly Event Calendar
 * Displays macro events, analyst ratings, and news headlines with SPX impact data.
 */

(function() {
  "use strict";

  // -----------------------------------------------------------------------------
  // State
  // -----------------------------------------------------------------------------

  const state = {
    weekOffset: 0,
    data: null,
    loading: false,
  };

  // -----------------------------------------------------------------------------
  // DOM Helpers
  // -----------------------------------------------------------------------------

  function $(id) {
    return document.getElementById(id);
  }

  function escapeHtml(str) {
    if (!str) return "";
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function setText(id, text) {
    const el = $(id);
    if (el) el.textContent = text;
  }

  function setHtml(id, html) {
    const el = $(id);
    if (el) el.innerHTML = html;
  }

  // -----------------------------------------------------------------------------
  // API
  // -----------------------------------------------------------------------------

  async function fetchWeekData(weekOffset) {
    const url = `/api/news-risk?week_offset=${encodeURIComponent(weekOffset)}`;
    const resp = await fetch(url);
    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(`API error ${resp.status}: ${text.slice(0, 200)}`);
    }
    return resp.json();
  }

  // -----------------------------------------------------------------------------
  // Rendering
  // -----------------------------------------------------------------------------

  function formatDate(dateStr) {
    const d = new Date(dateStr + "T12:00:00");
    return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  }

  function formatWeekLabel(weekStart, weekEnd) {
    const start = new Date(weekStart + "T12:00:00");
    const end = new Date(weekEnd + "T12:00:00");
    const startStr = start.toLocaleDateString("en-US", { month: "short", day: "numeric" });
    const endStr = end.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
    return `${startStr} – ${endStr}`;
  }

  function getTypeClass(type, category) {
    if (category === "FED") return "fed";
    if (category === "TREASURY") return "treasury";
    if (type === "MACRO") return "econ";
    if (type === "RATING") return "rating";
    if (type === "NEWS") return "news";
    return "econ";
  }

  function renderImportanceStars(importance) {
    const max = 5;
    const filled = Math.min(Math.max(importance || 0, 0), max);
    return "★".repeat(filled) + "☆".repeat(max - filled);
  }

  function renderImpactBadge(spxImpact) {
    if (!spxImpact || spxImpact.medianPct == null) return "";
    const dir = spxImpact.direction || "volatile";
    const pct = spxImpact.medianPct.toFixed(2);
    const sign = dir === "down" ? "-" : dir === "up" ? "+" : "±";
    return `<span class="eventImpact ${escapeHtml(dir)}">${sign}${pct}%</span>`;
  }

  function renderEventCard(event) {
    const typeClass = getTypeClass(event.type, event.category);
    const time = event.time ? escapeHtml(event.time) : "";
    const impactHtml = renderImpactBadge(event.spxImpact);
    const stars = renderImportanceStars(event.importance);
    
    return `
      <div class="eventCard type-${typeClass}" data-event-id="${escapeHtml(event.id)}">
        <div class="eventCardHeader">
          <div class="eventName">${escapeHtml(event.name)}</div>
          ${time ? `<div class="eventTime">${time}</div>` : ""}
        </div>
        <div class="eventMeta">
          <span class="eventTypeBadge ${typeClass}">${escapeHtml(event.category || event.type)}</span>
          ${impactHtml}
          <span class="importanceStars">${stars}</span>
        </div>
      </div>
    `;
  }

  function renderDayColumn(day) {
    const riskClass = (day.riskLevel || "low").toLowerCase();
    const eventsHtml = day.events.length > 0
      ? day.events.map(ev => renderEventCard(ev)).join("")
      : `<div class="emptyDay">No significant events</div>`;
    
    return `
      <div class="dayColumn">
        <div class="dayHeader">
          <div>
            <div class="dayName">${escapeHtml(day.dayName)}</div>
            <div class="dayDate">${formatDate(day.date)}</div>
          </div>
          <div class="dayRiskBadge ${riskClass}">${escapeHtml(day.riskLevel)}</div>
        </div>
        <div class="dayEvents">
          ${eventsHtml}
        </div>
      </div>
    `;
  }

  function renderWeekGrid(data) {
    const grid = $("weekGrid");
    if (!grid) return;
    
    // Filter to only trading days (Mon-Fri)
    const tradingDays = data.days.filter(d => {
      const dayName = d.dayName.toLowerCase();
      return ["monday", "tuesday", "wednesday", "thursday", "friday"].includes(dayName);
    });
    
    grid.innerHTML = tradingDays.map(day => renderDayColumn(day)).join("");
    
    // Add click handlers
    grid.querySelectorAll(".eventCard").forEach(card => {
      card.addEventListener("click", (e) => {
        const eventId = card.dataset.eventId;
        const event = findEventById(data, eventId);
        if (event) {
          showEventPopup(event);
        }
      });
    });
  }

  function findEventById(data, eventId) {
    for (const day of data.days) {
      for (const ev of day.events) {
        if (ev.id === eventId) return ev;
      }
    }
    return null;
  }

  // -----------------------------------------------------------------------------
  // Popup
  // -----------------------------------------------------------------------------

  function showEventPopup(event) {
    const popup = $("eventPopup");
    const backdrop = $("popupBackdrop");
    const content = $("popupContent");
    
    if (!popup || !backdrop || !content) return;
    
    setText("popupTitle", event.name || "Event Details");
    
    let html = "";
    
    // Event meta
    html += `<div class="eventPopupSection">`;
    html += `<div class="eventPopupSectionTitle">Event Details</div>`;
    html += `<div class="eventPopupMeta">`;
    html += `<div class="eventPopupMetaItem"><span class="k">Type:</span><span class="v">${escapeHtml(event.type)}</span></div>`;
    html += `<div class="eventPopupMetaItem"><span class="k">Category:</span><span class="v">${escapeHtml(event.category)}</span></div>`;
    if (event.time) {
      html += `<div class="eventPopupMetaItem"><span class="k">Time:</span><span class="v">${escapeHtml(event.time)} ET</span></div>`;
    }
    html += `<div class="eventPopupMetaItem"><span class="k">Importance:</span><span class="v">${renderImportanceStars(event.importance)}</span></div>`;
    if (event.ticker) {
      html += `<div class="eventPopupMetaItem"><span class="k">Ticker:</span><span class="v">${escapeHtml(event.ticker)}</span></div>`;
    }
    html += `</div></div>`;
    
    // SPX Impact
    if (event.spxImpact && event.spxImpact.medianPct != null) {
      const impact = event.spxImpact;
      const dir = impact.direction || "volatile";
      const sign = dir === "down" ? "-" : dir === "up" ? "+" : "±";
      
      html += `<div class="eventPopupSection">`;
      html += `<div class="eventPopupSectionTitle">Historical SPX Impact</div>`;
      html += `<div class="eventPopupImpact">`;
      html += `<div class="eventPopupImpactValue">${sign}${impact.medianPct.toFixed(2)}%</div>`;
      html += `<div class="eventPopupImpactLabel">Median absolute move (${impact.sampleSize || "?"} events)</div>`;
      html += `</div></div>`;
    }
    
    // Macro details
    if (event.type === "MACRO" && event.details) {
      const d = event.details;
      
      // Forecast/Previous/Actual
      if (d.forecast != null || d.previous != null || d.actual != null) {
        html += `<div class="eventPopupSection">`;
        html += `<div class="eventPopupSectionTitle">Data Points</div>`;
        html += `<div class="eventPopupMeta">`;
        if (d.forecast != null) {
          html += `<div class="eventPopupMetaItem"><span class="k">Forecast:</span><span class="v">${d.forecast}${d.unit ? " " + d.unit : ""}</span></div>`;
        }
        if (d.previous != null) {
          html += `<div class="eventPopupMetaItem"><span class="k">Previous:</span><span class="v">${d.previous}${d.unit ? " " + d.unit : ""}</span></div>`;
        }
        if (d.actual != null) {
          html += `<div class="eventPopupMetaItem"><span class="k">Actual:</span><span class="v">${d.actual}${d.unit ? " " + d.unit : ""}</span></div>`;
        }
        if (d.period) {
          html += `<div class="eventPopupMetaItem"><span class="k">Period:</span><span class="v">${escapeHtml(d.period)}</span></div>`;
        }
        html += `</div></div>`;
      }
      
      // Playbook
      if (d.playbook) {
        const pb = d.playbook;
        html += `<div class="eventPopupSection">`;
        html += `<div class="eventPopupSectionTitle">Desk Playbook</div>`;
        html += `<div class="eventPopupPlaybook">`;
        if (pb.deskView && pb.deskView.length > 0) {
          html += `<ul>`;
          pb.deskView.forEach(item => {
            html += `<li>${escapeHtml(item)}</li>`;
          });
          html += `</ul>`;
        }
        if (pb.watch && pb.watch.length > 0) {
          html += `<div style="margin-top: 8px; font-size: 11px; font-weight: 700; color: var(--muted);">WATCH:</div>`;
          html += `<ul>`;
          pb.watch.forEach(item => {
            html += `<li>${escapeHtml(item)}</li>`;
          });
          html += `</ul>`;
        }
        html += `</div></div>`;
      }
    }
    
    // Rating details
    if (event.type === "RATING" && event.details) {
      const d = event.details;
      html += `<div class="eventPopupSection">`;
      html += `<div class="eventPopupSectionTitle">Rating Details</div>`;
      html += `<div class="eventPopupRating">`;
      if (d.ratingCurrent) {
        html += `<div class="eventPopupRatingItem"><div class="label">Current Rating</div><div class="value">${escapeHtml(d.ratingCurrent)}</div></div>`;
      }
      if (d.ratingPrior) {
        html += `<div class="eventPopupRatingItem"><div class="label">Prior Rating</div><div class="value">${escapeHtml(d.ratingPrior)}</div></div>`;
      }
      if (d.ptCurrent) {
        html += `<div class="eventPopupRatingItem"><div class="label">Price Target</div><div class="value">$${escapeHtml(d.ptCurrent)}</div></div>`;
      }
      if (d.ptPrior) {
        html += `<div class="eventPopupRatingItem"><div class="label">Prior PT</div><div class="value">$${escapeHtml(d.ptPrior)}</div></div>`;
      }
      html += `</div>`;
      if (d.firm) {
        html += `<div style="margin-top: 8px; font-size: 12px;"><span class="k">Firm:</span> <span class="v">${escapeHtml(d.firm)}</span></div>`;
      }
      if (d.analyst) {
        html += `<div style="font-size: 12px;"><span class="k">Analyst:</span> <span class="v">${escapeHtml(d.analyst)}</span></div>`;
      }
      html += `</div>`;
    }
    
    // News details
    if (event.type === "NEWS" && event.details) {
      const d = event.details;
      html += `<div class="eventPopupSection">`;
      html += `<div class="eventPopupSectionTitle">News Details</div>`;
      if (d.title) {
        html += `<div style="font-size: 13px; line-height: 1.4; margin-bottom: 8px;">${escapeHtml(d.title)}</div>`;
      }
      if (d.tickers && d.tickers.length > 0) {
        html += `<div style="font-size: 12px;"><span class="k">Related Tickers:</span> <span class="v">${escapeHtml(d.tickers.join(", "))}</span></div>`;
      }
      if (d.url) {
        html += `<div style="margin-top: 8px;"><a href="${escapeHtml(d.url)}" target="_blank" rel="noopener" style="font-size: 12px; color: #3498db;">Read Full Article &rarr;</a></div>`;
      }
      html += `</div>`;
    }
    
    content.innerHTML = html;
    
    popup.classList.remove("hidden");
    backdrop.classList.remove("hidden");
  }

  function hideEventPopup() {
    const popup = $("eventPopup");
    const backdrop = $("popupBackdrop");
    
    if (popup) popup.classList.add("hidden");
    if (backdrop) backdrop.classList.add("hidden");
  }

  // -----------------------------------------------------------------------------
  // Loading & Status
  // -----------------------------------------------------------------------------

  function setLoading(isLoading, message) {
    state.loading = isLoading;
    
    const prevBtn = $("prevWeekBtn");
    const nextBtn = $("nextWeekBtn");
    
    if (prevBtn) prevBtn.disabled = isLoading;
    if (nextBtn) nextBtn.disabled = isLoading;
    
    if (window.RavenLoading) {
      if (isLoading) {
        window.RavenLoading.show({ 
          status: message || "Loading events...",
          expectedLoadMs: 15000,
          clearResults: false,
        });
      } else {
        window.RavenLoading.hide();
      }
    }
  }

  function setStatus(message, type) {
    const statusEl = $("status");
    const section = $("statusSection");
    
    if (statusEl) {
      statusEl.textContent = message;
      statusEl.className = "status";
      if (type === "error") statusEl.classList.add("status--error");
      if (type === "running") statusEl.classList.add("status--running");
    }
    
    // Hide status section on success
    if (section) {
      section.classList.toggle("hidden", type === "success");
    }
  }

  // -----------------------------------------------------------------------------
  // Main Refresh
  // -----------------------------------------------------------------------------

  async function refresh() {
    if (state.loading) return;
    
    setLoading(true, "Loading event data...");
    setStatus("Loading event data...", "running");
    
    try {
      const data = await fetchWeekData(state.weekOffset);
      state.data = data;
      
      // Update week label
      setText("weekLabel", formatWeekLabel(data.weekStart, data.weekEnd));
      
      // Update meta
      const totalEvents = data.meta?.totalEvents || 0;
      setText("weekMeta", `${totalEvents} events · As of ${data.meta?.asOfDate || "—"}`);
      
      // Render grid
      $("results").classList.remove("hidden");
      renderWeekGrid(data);
      
      setStatus("", "success");
    } catch (err) {
      console.error("Failed to load news risk data:", err);
      setStatus(`Error: ${err.message}`, "error");
    } finally {
      setLoading(false);
    }
  }

  // -----------------------------------------------------------------------------
  // Event Handlers
  // -----------------------------------------------------------------------------

  function init() {
    // Week navigation
    const prevBtn = $("prevWeekBtn");
    const nextBtn = $("nextWeekBtn");
    
    if (prevBtn) {
      prevBtn.addEventListener("click", () => {
        state.weekOffset--;
        refresh();
      });
    }
    
    if (nextBtn) {
      nextBtn.addEventListener("click", () => {
        state.weekOffset++;
        refresh();
      });
    }
    
    // Popup close
    const popupClose = $("popupClose");
    const popupBackdrop = $("popupBackdrop");
    
    if (popupClose) {
      popupClose.addEventListener("click", hideEventPopup);
    }
    
    if (popupBackdrop) {
      popupBackdrop.addEventListener("click", hideEventPopup);
    }
    
    // Escape key closes popup
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        hideEventPopup();
      }
    });
    
    // Initialize tooltips
    if (window.initTooltips) {
      window.initTooltips();
    }
    
    // Initial load
    refresh();
  }

  // Start on DOM ready
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
