/* Engine 15 — Earnings IC Scenario UI
 *
 * Step 1: ticker scan -> /api/earnings-ic/scan (Engine 1 payload + coverage)
 * Step 2: user enters wings + planned exit -> /api/earnings-ic/scenario
 * Step 3: render results (entry, planned-exit caveat, outcome distribution,
 *         conditioning modifiers, MTM chart, exit rules, matched events)
 */
(function () {
  'use strict';

  const $ = (id) => document.getElementById(id);

  // ------------------------------------------------------------------
  // State
  // ------------------------------------------------------------------
  const state = {
    scan: null,         // raw /scan payload
    scenario: null,     // raw /scenario payload
    mtmChart: null,
  };

  // ------------------------------------------------------------------
  // Utilities
  // ------------------------------------------------------------------
  function fmtPct(v, digits) {
    if (v === null || v === undefined || Number.isNaN(v)) return '—';
    return `${Number(v).toFixed(digits != null ? digits : 1)}%`;
  }
  function fmtNum(v, digits) {
    if (v === null || v === undefined || Number.isNaN(v)) return '—';
    return Number(v).toFixed(digits != null ? digits : 2);
  }
  function fmtInt(v) {
    if (v === null || v === undefined || Number.isNaN(v)) return '—';
    return String(Math.round(Number(v)));
  }
  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function card(label, value, caption) {
    const c = el('div', 'e15Card');
    c.appendChild(el('div', 'e15CardLabel', label));
    c.appendChild(el('div', 'e15CardValue', value));
    if (caption) c.appendChild(el('div', 'e15CardCaption', caption));
    return c;
  }
  function banner(msg, type) {
    const b = $('banner');
    b.className = 'e15Banner' + (type ? ' ' + type : '');
    b.textContent = msg;
    b.style.display = msg ? '' : 'none';
  }
  function setStatus(elId, text) { $(elId).textContent = text; }
  // Defensive no-op when the id doesn't exist (v2 collapsed some legacy
  // sections into always-visible top-row elements; callers that still
  // reference the retired ids should not crash the scan).
  function show(id) {
    const el = $(id);
    if (!el) return;
    el.classList.remove('hidden');
    el.style.display = '';
  }
  function hide(id) {
    const el = $(id);
    if (!el) return;
    el.classList.add('hidden');
  }

  async function postJson(url, body, extraHeaders) {
    const headers = { 'Content-Type': 'application/json' };
    if (extraHeaders) Object.assign(headers, extraHeaders);
    const r = await fetch(url, {
      method: 'POST',
      headers,
      body: JSON.stringify(body || {}),
    });
    const txt = await r.text();
    let data = null;
    try { data = txt ? JSON.parse(txt) : null; } catch (e) { data = { _raw: txt }; }
    if (!r.ok) {
      const msg = (data && data.detail) ? data.detail : (data && data._raw) || r.statusText;
      throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
    }
    return data;
  }
  async function getJson(url) {
    const r = await fetch(url);
    const txt = await r.text();
    let data = null;
    try { data = txt ? JSON.parse(txt) : null; } catch (e) { data = { _raw: txt }; }
    if (!r.ok) {
      const msg = (data && data.detail) ? data.detail : (data && data._raw) || r.statusText;
      throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
    }
    return data;
  }

  // Add N business days to an ISO YYYY-MM-DD date string.
  function shiftBizDays(iso, days) {
    if (!iso) return iso;
    const d = new Date(iso + 'T00:00:00');
    let rem = Math.abs(days);
    const step = days >= 0 ? 1 : -1;
    while (d.getDay() === 0 || d.getDay() === 6) d.setDate(d.getDate() + step);
    while (rem > 0) {
      d.setDate(d.getDate() + step);
      if (d.getDay() !== 0 && d.getDay() !== 6) rem--;
    }
    return d.toISOString().slice(0, 10);
  }

  // ------------------------------------------------------------------
  // Step 1: /api/earnings-ic/scan
  // ------------------------------------------------------------------
  // E15 v2 — single-page Command Deck. doCalculate runs scan + scenario
  // end-to-end from one form submit. doScan is retained only as a silent
  // helper for the scan pass when the desk hasn't yet completed the form.
  async function doScan(evt) {
    if (evt) evt.preventDefault();
    const t = ($('ticker').value || '').trim().toUpperCase();
    if (!t) return null;
    $('ticker').value = t;
    if (window.RavenUI && RavenUI.setEngineTabTitle) RavenUI.setEngineTabTitle(t);
    try {
      const n = parseInt($('historyN').value || '20', 10);
      const data = await postJson('/api/earnings-ic/scan', { ticker: t, n, years: 5 });
      state.scan = data;
      renderScanResult(data);
      return data;
    } catch (err) {
      banner('Scan failed: ' + err.message, 'error');
      return null;
    }
  }

  // ------------------------------------------------------------------
  // Single Calculate entry point. Runs scan first (if missing), then
  // scenario, then paints the full Command Deck. Also echoes the
  // wing-console handoff metadata back in the status bar when present.
  async function doCalculate(evt) {
    if (evt) evt.preventDefault();
    const t = ($('ticker').value || '').trim().toUpperCase();
    if (!t) {
      banner('Enter a ticker.', 'error');
      return;
    }
    $('ticker').value = t;
    if (window.RavenUI && RavenUI.setEngineTabTitle) RavenUI.setEngineTabTitle(t);

    // Require earnings date + timing before continuing (matches E1 v2).
    const evDate = $('earningsDate').value || '';
    const evTiming = String($('earningsTiming').value || '').toUpperCase();
    if (!evDate || !['AMC', 'BMO', 'UNK'].includes(evTiming)) {
      banner('Enter earnings date + timing before running.', 'error');
      return;
    }

    setStatus('runStatus', 'Running Engine 1 scan…');
    $('runBtn').disabled = true;
    banner('');
    hide('results');
    hide('e1Summary');

    try {
      // Always run (or re-run) the scan to keep E1 and E15 in lock-step.
      const scan = await doScan();
      if (!scan) {
        setStatus('runStatus', 'Error');
        return;
      }
      // Prefill strikes / expiry from the scan when the desk left them blank.
      prefillScenarioForm(scan);

      setStatus('runStatus', 'Running Command Deck replay…');
      const body = collectScenarioBody();
      if (!body) {
        setStatus('runStatus', 'Error');
        return;
      }
      const data = await postJson('/api/earnings-ic/scenario', body);
      state.scenario = data;
      if (window.DeskInsight) {
        window.DeskInsight.clearCache();
        window.DeskInsight.refresh();
      }
      renderScenario(data);
      setStatus('runStatus', data.wingConsoleHandoff
        ? `Done — simulated E1 Wing Console rank ${Number(data.wingConsoleHandoff.placementRank) + 1} pick.`
        : 'Done.'
      );
      show('results');
    } catch (err) {
      banner('Command Deck failed: ' + err.message, 'error');
      setStatus('runStatus', 'Error');
    } finally {
      $('runBtn').disabled = false;
    }
  }

  function renderScanResult(data) {
    const s = (data && data.engine1Summary) || {};
    const e1 = (data && data.engine1) || {};
    // Back-compat: if the server didn't send engine1Summary (older builds),
    // fall back to reading the raw payload inline.
    const current = e1.current || {};
    const vrp = e1.vrpAnalysis || {};
    // NOTE: E1's deskConsensus is intentionally NOT surfaced on the E15
    // scan card — an E15 workflow assumes the desk is committing to the
    // trade, so a GO/LEAN_PASS/PASS up-vote isn't a decision input here.
    const next = e1.nextEvent || {};
    const em = e1.expectedMove || {};
    const st = e1.strikeTargets || {};

    const stockPrice = s.stockPrice != null ? s.stockPrice : current.stockPrice;
    const asOfDate = s.asOfDate || current.asOfDate;
    const vrpScore = s.vrpScore != null ? s.vrpScore : vrp.vrpScore;
    const ivElev = s.ivElevation != null ? s.ivElevation : vrp.ivElevation;
    const oratsEmPct = s.oratsEmPct != null ? s.oratsEmPct : current.impliedMovePct;
    const delayedEmPct = s.delayedEmPct != null ? s.delayedEmPct : current.delayedImpliedMovePct;
    const delayedUpdated = s.delayedUpdatedAt || current.delayedUpdatedAt;
    const straddlePct = s.straddleEmPct != null ? s.straddleEmPct : em.expectedMovePct;
    const straddleDollars = s.straddleEmDollars != null ? s.straddleEmDollars : em.expectedMoveDollars;
    const straddleExpiry = s.straddleExpiry || em.expiry;
    const straddleSource = (s.straddleSource || em.source || '').toLowerCase();
    const strike1x = (s.strikeTargets && s.strikeTargets.whitePct) != null ? s.strikeTargets.whitePct : st.whitePct;
    const strike15x = (s.strikeTargets && s.strikeTargets.bluePct) != null ? s.strikeTargets.bluePct : st.bluePct;
    const strike2x = (s.strikeTargets && s.strikeTargets.redPct) != null ? s.strikeTargets.redPct : st.redPct;
    const strikeEmSource = (s.strikeTargets && s.strikeTargets.emSource) || st.emSource;
    // Next-event fields are only read from the raw engine1 payload now —
    // engine1Summary no longer re-surfaces them because the authoritative
    // earnings date + AMC/BMO timing live in the Step 2 form (desk input).
    // The scan card shows E1's suggestion as a prefill hint only.
    const nextDate = next.earnDate || next.earnDateNext || next.date;
    const nextTiming = next.timing || next.timingPlanned || next.anncTod;
    const pricingExpiry = next.pricingExpiry;
    const nextSource = next.source;
    const breach1x = s.emBreachRate1xPct;
    const breach15x = s.emBreachRate15xPct;
    const breach2x = s.emBreachRate2xPct;
    const breachN = s.emBreachN != null ? s.emBreachN : ((e1.summary || {}).events_used);
    const regimeLabel = s.regimeLabel || (e1.regime || {}).label;
    const regimeTail = s.regimeTailMultiplier != null ? s.regimeTailMultiplier : (e1.regime || {}).tailMultiplier;
    const eventRiskLabel = s.eventRiskLabel || (e1.eventRisk || {}).label;
    const evN = s.historyN != null ? s.historyN : (e1.events || []).length;

    const srcLabel = (s.emPctSource === 'live' || !s.emPctSource) ? 'Live' : 'Delayed';
    const nextSrcCaption = nextSource === 'friday_expiry_fallback'
      ? 'Friday-exp fallback — confirm date'
      : (nextSource === 'orats_snapshot' ? 'ORATS snapshot' : '');
    const regimeCaption = regimeLabel
      ? `Regime ${regimeLabel}${regimeTail != null ? ' (' + Number(regimeTail).toFixed(2) + 'x)' : ''}${eventRiskLabel ? ' · Risk ' + eventRiskLabel : ''}`
      : '';

    const cards = $('e1Cards');
    cards.innerHTML = '';

    // 1. Stock Price (EOD anchor + headline EM)
    const stockCaption = asOfDate
      ? `EOD ${String(asOfDate).slice(0,10)}${Number.isFinite(oratsEmPct) || Number.isFinite(delayedEmPct) ? ' · EM ' + fmtPct(oratsEmPct != null ? oratsEmPct : delayedEmPct, 2) + ' (' + srcLabel + ')' : ''}`
      : (Number.isFinite(oratsEmPct) || Number.isFinite(delayedEmPct)) ? 'EM ' + fmtPct(oratsEmPct != null ? oratsEmPct : delayedEmPct, 2) : '—';
    cards.appendChild(card('Stock Price', fmtNum(stockPrice, 2), stockCaption));

    // 2. VRP + IV elevation
    cards.appendChild(card('VRP Score',
      vrpScore != null ? Number(vrpScore).toFixed(2) : '—',
      ivElev != null ? `IV elev: ${fmtPct(ivElev, 1)}` : regimeCaption || ''));

    // 3. Next Event — E1 suggestion; the desk's form entry below is authoritative
    const nextCaption = [nextTiming || '—',
      pricingExpiry ? `Exp: ${String(pricingExpiry).slice(0,10)}` : '',
      nextSrcCaption,
      'E1 suggestion — desk overrides in Step 2'].filter(Boolean).join(' · ');
    cards.appendChild(card('Next Event (E1)', nextDate || '—', nextCaption));

    // 4. Straddle EM ($ and %, expiry, source)
    const straddleCaption = [
      Number.isFinite(straddleDollars) ? `$${Number(straddleDollars).toFixed(2)} pts` : '',
      straddleExpiry ? `Exp: ${String(straddleExpiry).slice(0,10)}` : '',
      straddleSource ? straddleSource.toUpperCase() : ''
    ].filter(Boolean).join(' · ') || '—';
    cards.appendChild(card('Straddle EM', fmtPct(straddlePct, 2), straddleCaption));

    // 5. ORATS EM (EOD + delayed block)
    const oratsCardVal = fmtPct(oratsEmPct, 2);
    const oratsCaption = `EOD${asOfDate ? ' ' + String(asOfDate).slice(0,10) : ''} · Delayed ${fmtPct(delayedEmPct, 2)}${delayedUpdated ? ' @ ' + String(delayedUpdated).slice(11,16) : ''}`;
    cards.appendChild(card('ORATS EM', oratsCardVal, oratsCaption));

    // 6. Strike Targets (1x / 1.5x / 2x)
    const strikeCardVal = Number.isFinite(strike1x) ? `${fmtPct(strike1x, 2)} / ${fmtPct(strike15x, 2)} / ${fmtPct(strike2x, 2)}` : '—';
    const strikeCaption = `1× / 1.5× / 2× EM wing distance${strikeEmSource ? ' · ' + strikeEmSource : ''}`;
    cards.appendChild(card('Strike Targets', strikeCardVal, strikeCaption));

    // 7. Regime / Event Risk chip (replaces the old Desk Consensus tile —
    // an E15 run assumes the desk has committed, so E1's up/down vote is
    // not surfaced here. We show regime context instead.)
    const regimeCardVal = regimeLabel || '—';
    const regimeCardCap = [
      regimeTail != null ? `Tail ${Number(regimeTail).toFixed(2)}x` : '',
      eventRiskLabel ? `Risk ${eventRiskLabel}` : '',
    ].filter(Boolean).join(' · ') || '—';
    cards.appendChild(card('Regime / Risk', regimeCardVal, regimeCardCap));

    // 8. EM Breach (multi-row)
    const breachVal = (breach1x != null || breach15x != null || breach2x != null)
      ? `${fmtPct(breach1x, 0)} / ${fmtPct(breach15x, 0)} / ${fmtPct(breach2x, 0)}`
      : '—';
    cards.appendChild(card('EM Breach', breachVal,
      `1× / 1.5× / 2× historical breach${breachN != null ? ' · n=' + breachN : ''}`));

    // 9. Events Harvested
    cards.appendChild(card('Events Harvested', String(evN),
      evN >= 8 ? 'Sufficient for replay' : 'Thin; consider running backfill'));

    // Coverage box
    const cov = (data.chainCoverage || {});
    const covBox = $('coverageBox');
    const days = cov.daysCovered || 0;
    if (days > 0) {
      covBox.textContent =
        `Chain cache: ${days} days of history for ${data.ticker} ` +
        (cov.minDate ? `(${cov.minDate} → ${cov.maxDate})` : '') +
        `, ${cov.totalRows || 0} rows. ` +
        (days >= 2 * 8
          ? 'Ready for replay.'
          : 'Cache is thin — /scenario may backfill on-demand (can take 10-30s).');
      covBox.style.display = '';
    } else {
      covBox.textContent =
        `No chain cache for ${data.ticker} yet. The first scenario run will backfill on-demand — ` +
        `expect a 10-30s wait while ORATS historical slices warm up.`;
      covBox.style.display = '';
    }

    // Pre-fill Step 2 form. v2 collapsed scenarioForm into commandDeckForm
    // which is always visible, so only e1Summary needs an explicit show().
    show('e1Summary');
    prefillScenarioForm(data);
  }

  function prefillScenarioForm(scan) {
    const s = scan.engine1Summary || {};
    const e1 = scan.engine1 || {};
    const next = e1.nextEvent || {};
    const current = e1.current || {};
    const em = e1.expectedMove || {};

    // v2: The form is the authority. E1's `nextEvent` is a *suggestion*
    // used only to seed blank fields. The two desk-reported bugs that
    // motivated this rewrite were:
    //   (a) Clobbering manual date edits every time the desk clicked
    //       Calculate (the old code unconditionally wrote to all date
    //       inputs, so any fix got reset).
    //   (b) Accepting E1's pricingExpiry even when it fell *before* the
    //       computed entry date (stale / crossed-wire E1 payloads).
    //
    // Rules:
    //   - Never overwrite a value the desk (or the deep-link URL) has
    //     already set.
    //   - When the form is blank, seed from E1.
    //   - Derived expiry must be >= entry + 1 biz day — if E1's
    //     suggestion fails that check, fall back to a 4-biz-day shift
    //     from the earnings date.
    const fmt = (v) => (v == null ? '' : String(v)).slice(0, 10);
    const getVal = (id) => String($(id).value || '').trim();
    const setIfBlank = (id, v) => {
      if (!v) return;
      if (!getVal(id)) $(id).value = v;
    };

    const formEarn = getVal('earningsDate');
    const formTiming = String(getVal('earningsTiming') || '').toUpperCase();
    const e1Earn = fmt(s.nextEventDate || next.earnDate || next.earnDateNext || next.date || '');
    const e1Timing = String(s.anncTod || next.timing || next.timingPlanned || next.anncTod || 'BMO').toUpperCase();
    const earnDate = formEarn || e1Earn;
    const timing = (['BMO', 'AMC', 'UNK'].includes(formTiming))
      ? formTiming
      : (['BMO', 'AMC', 'UNK'].includes(e1Timing) ? e1Timing : 'BMO');

    setIfBlank('earningsDate', e1Earn);
    setIfBlank('earningsTiming', timing);

    if (earnDate) {
      const entry = timing === 'AMC' ? earnDate : shiftBizDays(earnDate, -1);
      const plannedExit = timing === 'AMC' ? shiftBizDays(earnDate, 1) : earnDate;
      setIfBlank('entryDate', entry);
      setIfBlank('plannedExitDate', plannedExit);

      // Expiry: prefer E1's straddle/pricing expiry IFF it's strictly
      // after the entry date; else fall back to a 4-biz-day shift from
      // the earnings date. Guarantees the backend's entry<expiry check
      // passes for the auto-prefill path.
      const currentExpiry = getVal('expiry');
      if (!currentExpiry) {
        const pricingExpiry = fmt(
          s.nextEventPricingExpiry || s.straddleExpiry ||
          next.pricingExpiry || em.expiry || ''
        );
        let expVal = pricingExpiry || shiftBizDays(earnDate, 4);
        // If the E1-suggested expiry is <= entry (stale / crossed-wire
        // E1 payload), replace with a safe default.
        if (expVal && expVal <= entry) {
          expVal = shiftBizDays(entry, 4);
        }
        $('expiry').value = expVal;
      }
    } else {
      const pricingExpiry = fmt(
        s.nextEventPricingExpiry || s.straddleExpiry ||
        next.pricingExpiry || em.expiry || ''
      );
      setIfBlank('expiry', pricingExpiry);
    }

    // Strike + credit prefill — only seed blanks so the deep-link
    // handoff (wc_key+rank) and manual adjustments survive a
    // Calculate re-click. The backend's hydrate_from_wing_console
    // will override these from the cached Wing Console placement
    // when wc_key is present.
    const stock = (s.stockPrice != null ? s.stockPrice
      : (s.straddleSpotPrice != null ? s.straddleSpotPrice : current.stockPrice));
    const emPct = s.delayedEmPct != null ? s.delayedEmPct
      : s.oratsEmPct != null ? s.oratsEmPct
      : s.straddleEmPct != null ? s.straddleEmPct
      : (current.delayedImpliedMovePct || current.impliedMovePct || em.expectedMovePct || null);

    const stockNum = stock != null ? Number(stock) : null;
    const emNum = emPct != null ? Number(emPct) : null;
    if (stockNum && emNum) {
      const emDollars = stockNum * (emNum / 100.0);
      const tickStep = stockNum < 50 ? 1 : (stockNum < 200 ? 1 : 5);
      const snap = (v) => Math.round(v / tickStep) * tickStep;
      const shortPut = snap(stockNum - 1.5 * emDollars);
      const longPut = snap(shortPut - Math.max(tickStep * 2, 2));
      const shortCall = snap(stockNum + 1.5 * emDollars);
      const longCall = snap(shortCall + Math.max(tickStep * 2, 2));
      setIfBlank('shortPut', shortPut);
      setIfBlank('longPut', longPut);
      setIfBlank('shortCall', shortCall);
      setIfBlank('longCall', longCall);
      const wing = Math.max(shortPut - longPut, longCall - shortCall, tickStep);
      setIfBlank('creditReceived', (wing * 0.2).toFixed(2));
    }
  }

  // ------------------------------------------------------------------
  // Legacy two-step runner — kept for callers that still expect this
  // name; the v2 Command Deck calls doCalculate directly. Retained in
  // case any bookmarks or tests still reference the old entry point.
  // ------------------------------------------------------------------
  async function doScenario(evt) {
    return doCalculate(evt);
  }

  function collectScenarioBody() {
    const t = ($('ticker').value || '').trim().toUpperCase();
    const body = {
      ticker: t,
      entryDate: $('entryDate').value,
      expiry: $('expiry').value,
      earningsDate: $('earningsDate').value,
      earningsTiming: $('earningsTiming').value,
      plannedExitDate: $('plannedExitDate').value,
      plannedExitOffsetHours: parseFloat($('plannedExitOffsetHours').value),
      longPut: parseFloat($('longPut').value),
      shortPut: parseFloat($('shortPut').value),
      shortCall: parseFloat($('shortCall').value),
      longCall: parseFloat($('longCall').value),
      creditReceived: parseFloat($('creditReceived').value),
      profitTargetPct: parseFloat($('profitTargetPct').value),
      stopLossPct: parseFloat($('stopLossPct').value),
      // v2: Command Deck always includes the E1 summary + wingConsoleMini
      // in the response body. includeE1Payload retained for legacy cache
      // parity but now defaults true.
      includeE1Payload: true,
    };
    const season = $('seasonMode').value;
    if (season === 'quarter') body.seasonMode = 'quarter';
    // E1 Wing Console handoff (populated from URL ?wc_key=...&rank=...).
    const wcKey = ($('wingConsoleCacheKey').value || '').trim();
    if (wcKey) {
      body.wingConsoleCacheKey = wcKey;
      const rank = parseInt($('wingConsolePlacementRank').value || '0', 10);
      body.placementRank = Number.isFinite(rank) ? rank : 0;
    }

    for (const k of ['entryDate','expiry','earningsDate','plannedExitDate',
                      'longPut','shortPut','shortCall','longCall','creditReceived']) {
      if (body[k] === '' || body[k] === null || Number.isNaN(body[k])) {
        banner(`Missing field: ${k}`, 'error');
        return null;
      }
    }
    if (!(body.longPut < body.shortPut && body.shortPut < body.shortCall
          && body.shortCall < body.longCall)) {
      banner('Strikes must satisfy: longPut < shortPut < shortCall < longCall', 'error');
      return null;
    }
    // v2: match the backend's expiry-after-entry rule client-side so the
    // desk sees a clean actionable error instead of a generic 400 from
    // /api/earnings-ic/scenario (which previously surfaced as the
    // "Command Deck failed: expiry must be after entryDate" banner).
    if (body.entryDate && body.expiry && body.expiry <= body.entryDate) {
      banner(
        'Expiry (' + body.expiry + ') must be AFTER Entry Date (' + body.entryDate + '). ' +
        'Open the Advanced panel and fix the dates, then click Calculate again.',
        'error'
      );
      return null;
    }
    if (body.plannedExitDate && body.entryDate && body.plannedExitDate < body.entryDate) {
      banner(
        'Planned Exit Date (' + body.plannedExitDate + ') is before Entry Date (' + body.entryDate + '). ' +
        'Open the Advanced panel and fix the dates.',
        'error'
      );
      return null;
    }
    return body;
  }

  // ------------------------------------------------------------------
  // Renderers
  // ------------------------------------------------------------------
  function renderScenario(d) {
    if (!d) return;
    if ((d.eventsUsed || 0) === 0) {
      banner((d.notes || ['No events used.'])[0], 'error');
    }
    // v2 Command Deck cards (new): E1 summary + Wing Console mini grid
    // + cross-check badge + crush reading live at the top. The rest of
    // the render pipeline stays unchanged.
    if (d.engine1Summary) {
      renderScanResult({ engine1Summary: d.engine1Summary, engine1: d.engine1 || null });
      show('e1Summary');
    }
    renderWingConsoleMini(d);
    renderCrossCheck(d);
    renderCrushReading(d);
    refreshE15SourceChip(d);
    renderHandoffBanner(d);
    renderEntryState(d);
    renderPlannedExit(d);
    renderCreditRichness(d);
    renderOutcomeDistribution('outcomePanel', d.outcomeDistribution || {});
    renderAdjusted(d);
    renderModifiers(d);
    renderMtmChart(d);
    renderExpectedValue(d);
    renderExitRules(d);
    renderMatchedEvents(d);
    renderDroppedEvents(d);
    renderNotes(d);
    scheduleStrikeScan(d);
  }

  // ------------------------------------------------------------------
  // Strike Scanner — "what if you moved the short put out 2 strikes?"
  // ------------------------------------------------------------------
  let _scanTimer = null;
  let _scanGen = 0;          // generation counter, lets us cancel stale runs
  let _scanLastResult = null;

  function scheduleStrikeScan(scenarioData, { immediate = false } = {}) {
    if (!scenarioData || !Array.isArray(scenarioData.matchedEvents) || scenarioData.matchedEvents.length < 3) {
      hideStrikeScan();
      return;
    }
    if (_scanTimer) { clearTimeout(_scanTimer); _scanTimer = null; }
    const delay = immediate ? 0 : 250;
    _scanTimer = setTimeout(() => runStrikeScan(scenarioData), delay);
  }

  async function runStrikeScan(scenarioData) {
    const body = collectScenarioBody();
    if (!body) { hideStrikeScan(); return; }
    const gen = ++_scanGen;

    const divider = $('strikeScanDivider');
    const panel = $('strikeScanPanel');
    const status = $('strikeScanStatus');
    if (divider) divider.style.display = '';
    if (panel) panel.style.display = '';
    if (status) {
      status.style.display = '';
      status.textContent = 'scanning…';
    }

    try {
      const resp = await postJson('/api/earnings-ic/strike-scan', {
        scenarioRequest: body,
        baseline:        scenarioData,
        snapMaxPts:      5.0,
        fillMode:        'nbbo',
      });
      if (gen !== _scanGen) return;  // stale response, ignore
      _scanLastResult = resp;
      renderStrikeScan(resp);
    } catch (err) {
      if (gen !== _scanGen) return;
      const msg = (err && err.message) || String(err);
      // Sad-path: chain not cached or thin pool — silently hide the panel
      // and surface a console hint. Never block the main render.
      console.warn('strike-scan failed:', msg);
      hideStrikeScan();
    }
  }

  function hideStrikeScan() {
    const divider = $('strikeScanDivider');
    const panel = $('strikeScanPanel');
    if (divider) divider.style.display = 'none';
    if (panel) panel.style.display = 'none';
  }

  function renderStrikeScan(d) {
    const verdictEl = $('strikeScanVerdict');
    const altsEl = $('strikeScanAlts');
    const status = $('strikeScanStatus');
    const countEl = $('strikeScanCount');
    const tableEl = $('strikeScanTable');
    if (!verdictEl || !altsEl) return;

    const verdict = String(d.verdict || 'optimal');
    const scannedN = Number(d.scanned_n || 0);
    if (status) {
      status.textContent = `scanned ${scannedN}`;
    }

    // Verdict header card.
    verdictEl.innerHTML = '';
    const verdictClass = {
      dominating:           'e15ScanVerdict--green',
      safer_alternative:    'e15ScanVerdict--amber',
      richer_alternative:   'e15ScanVerdict--amber',
      optimal:              'e15ScanVerdict--grey',
    }[verdict] || 'e15ScanVerdict--grey';
    verdictEl.className = `e15ScanVerdict ${verdictClass}`;
    const verdictLabel = {
      dominating:           'Better Trade Available',
      safer_alternative:    'Equivalent-but-Safer Alternative',
      richer_alternative:   'Richer Alternative at Similar Risk',
      optimal:              'As Good As It Gets',
    }[verdict] || 'Scan Result';

    const headline = String(d.headline || '');
    const baseline = d.baseline || {};
    const chainMeta = d.chain_meta || {};
    const fallbackNote = chainMeta.fallback
      ? `<div class="e15ScanFallbackNote">Pricing from cached chain on ${escapeHtml(String(chainMeta.used_trade_date || ''))} (entry-day chain ${escapeHtml(String(chainMeta.requested_trade_date || ''))} not yet cached). Relative deltas remain informative.</div>`
      : '';
    const deskCredit = (baseline.desk_credit != null && Math.abs(baseline.desk_credit - baseline.credit) > 0.01)
      ? ` · desk collected ${fmtNum(baseline.desk_credit, 2)}`
      : '';
    verdictEl.innerHTML = `
      <div class="e15ScanVerdictBadge">${verdictLabel}</div>
      <div class="e15ScanVerdictHeadline">${escapeHtml(headline)}</div>
      <div class="e15ScanVerdictMeta">
        baseline: credit ${fmtNum(baseline.credit, 2)}${deskCredit} · breach ${fmtPct((baseline.p_breach || 0) * 100, 1)}
        · scanned ${scannedN} alternatives
      </div>
      ${fallbackNote}
    `;

    // Top alternatives (up to 3).
    altsEl.innerHTML = '';
    const alts = Array.isArray(d.top_alternatives) ? d.top_alternatives : [];
    if (alts.length === 0) {
      altsEl.innerHTML = '<div class="e15ScanEmpty">No Pareto-dominating alternatives in this scan.</div>';
    }
    alts.forEach((alt, idx) => {
      altsEl.appendChild(buildScanAltCard(alt, idx));
    });

    // Disclosure table — full sorted candidate list.
    const all = Array.isArray(d.all_candidates) ? d.all_candidates : [];
    if (countEl) countEl.textContent = String(all.length);
    if (tableEl) {
      tableEl.innerHTML = buildScanTable(all);
    }
  }

  function buildScanAltCard(alt, idx) {
    const card = document.createElement('div');
    card.className = 'e15ScanAltCard';
    const s = alt.strikes || {};
    const structureLabel = {
      iron_condor:    'Iron Condor',
      iron_fly:       'Iron Fly',
      asymmetric_ic:  'Asymmetric IC',
      put_vertical:   'Put Vertical',
      call_vertical:  'Call Vertical',
    }[String(s.structure || '')] || 'Alternative';

    const strikesHtml = [
      s.shortPut  != null ? `SP ${Number(s.shortPut).toFixed(0)}`  : '',
      s.longPut   != null ? `LP ${Number(s.longPut).toFixed(0)}`   : '',
      s.shortCall != null ? `SC ${Number(s.shortCall).toFixed(0)}` : '',
      s.longCall  != null ? `LC ${Number(s.longCall).toFixed(0)}`  : '',
    ].filter(Boolean).join(' · ');

    card.innerHTML = `
      <div class="e15ScanAltHeader">
        <span class="e15ScanAltStructure">${structureLabel}</span>
        <span class="e15ScanAltStrikes">${strikesHtml}</span>
      </div>
      <div class="e15ScanAltDeltas">
        <span class="e15ScanDelta ${deltaClass(alt.delta_credit_pct, true)}">credit ${fmtSignedPct(alt.delta_credit_pct)}</span>
        <span class="e15ScanDelta ${deltaClass(-alt.delta_breach_pct, true)}">breach ${fmtSignedPct(alt.delta_breach_pct)}</span>
        <span class="e15ScanDelta ${deltaClass(alt.delta_ev_pct, true)}">EV ${fmtSignedPct(alt.delta_ev_pct)}</span>
      </div>
      <div class="e15ScanAltRationale">${escapeHtml(alt.rationale || '')}</div>
      <div class="e15ScanAltMetrics">
        credit ${fmtNum(alt.credit, 2)} · breach ${fmtPct((alt.p_breach || 0) * 100, 1)} · max loss ${fmtNum(alt.max_loss, 2)}
      </div>
      <button class="e15ScanApplyBtn" data-alt-idx="${idx}">Apply to form</button>
    `;
    const btn = card.querySelector('.e15ScanApplyBtn');
    if (btn) {
      btn.addEventListener('click', () => applyStrikeAlternative(alt));
    }
    return card;
  }

  function buildScanTable(all) {
    if (!all.length) return '<tbody></tbody>';
    const head = `
      <thead>
        <tr>
          <th>Structure</th><th>SP</th><th>LP</th><th>SC</th><th>LC</th>
          <th>Credit</th><th>ΔCredit</th><th>Breach</th><th>ΔBreach</th>
          <th>EV</th><th>ΔEV</th>
        </tr>
      </thead>`;
    const rows = all.slice(0, 60).map(c => {
      const s = c.strikes || {};
      return `<tr>
        <td>${escapeHtml(String(s.structure || '—'))}</td>
        <td>${s.shortPut  != null ? Number(s.shortPut).toFixed(0)  : '—'}</td>
        <td>${s.longPut   != null ? Number(s.longPut).toFixed(0)   : '—'}</td>
        <td>${s.shortCall != null ? Number(s.shortCall).toFixed(0) : '—'}</td>
        <td>${s.longCall  != null ? Number(s.longCall).toFixed(0)  : '—'}</td>
        <td>${fmtNum(c.credit, 2)}</td>
        <td class="${deltaClass(c.delta_credit_pct, true)}">${fmtSignedPct(c.delta_credit_pct)}</td>
        <td>${fmtPct((c.p_breach || 0) * 100, 1)}</td>
        <td class="${deltaClass(-c.delta_breach_pct, true)}">${fmtSignedPct(c.delta_breach_pct)}</td>
        <td>${fmtNum(c.ev, 2)}</td>
        <td class="${deltaClass(c.delta_ev_pct, true)}">${fmtSignedPct(c.delta_ev_pct)}</td>
      </tr>`;
    }).join('');
    return head + `<tbody>${rows}</tbody>`;
  }

  function applyStrikeAlternative(alt) {
    const s = alt && alt.strikes;
    if (!s) return;
    const setVal = (id, val) => {
      const el = $(id);
      if (!el) return;
      if (val == null) {
        // Two-leg verticals leave the other side blank — but the form
        // requires all four legs, so we keep the existing baseline value
        // there to avoid breaking the next /scenario submit.
        return;
      }
      el.value = String(val);
      el.dispatchEvent(new Event('change', { bubbles: true }));
    };
    setVal('shortPut',  s.shortPut);
    setVal('longPut',   s.longPut);
    setVal('shortCall', s.shortCall);
    setVal('longCall',  s.longCall);
    banner('Strikes applied. Re-run Calculate to re-price the scenario.', 'info');
  }

  function deltaClass(delta, higherIsBetter) {
    const v = Number(delta);
    if (!Number.isFinite(v) || Math.abs(v) < 0.5) return 'e15ScanDelta--flat';
    const good = higherIsBetter ? v > 0 : v < 0;
    return good ? 'e15ScanDelta--good' : 'e15ScanDelta--bad';
  }

  function fmtSignedPct(v) {
    const n = Number(v);
    if (!Number.isFinite(n)) return '—';
    const sign = n > 0 ? '+' : '';
    return `${sign}${n.toFixed(1)}%`;
  }

  function escapeHtml(s) {
    return String(s || '').replace(/[&<>"']/g, ch => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[ch] || ch));
  }

  // ------------------------------------------------------------------
  // v2 Command Deck renderers
  // ------------------------------------------------------------------

  function refreshE15SourceChip(d) {
    const chip = $('e15EventSourceChip');
    if (!chip) return;
    // Pull from engine1.nextEvent.override_source if it rode along;
    // default to user_override when the handoff banner is present.
    let src = 'unknown';
    const e1 = d && d.engine1 || {};
    const ne = e1.nextEvent || {};
    if (ne.override_source) src = String(ne.override_source).toLowerCase();
    if (d && d.wingConsoleHandoff) src = 'user_override';
    const allowed = new Set([
      'user_override', 'orats_cores', 'benzinga', 'cadence_estimate', 'unknown',
    ]);
    const s = allowed.has(src) ? src : 'unknown';
    chip.className = `e1SourceChip e1SourceChip--${s}`;
    const labels = {
      user_override: 'Override',
      orats_cores:   'ORATS',
      benzinga:      'Benzinga',
      cadence_estimate: 'Estimated',
      unknown:       '',
    };
    chip.textContent = labels[s] || '';
    chip.title = labels[s] ? `Earnings-date source: ${labels[s].toLowerCase()}` : '';
  }

  function renderHandoffBanner(d) {
    const host = $('banner');
    if (!host) return;
    if (!d || !d.wingConsoleHandoff) {
      if (host.classList.contains('e15HandoffBanner')) {
        host.classList.remove('e15HandoffBanner');
        host.style.display = 'none';
        host.textContent = '';
      }
      return;
    }
    const h = d.wingConsoleHandoff;
    host.className = 'e15HandoffBanner';
    host.style.display = 'flex';
    host.textContent = (
      `Simulated E1 Wing Console rank ${Number(h.placementRank) + 1} pick — ` +
      `EM ${Number(h.emMult).toFixed(2)} × ${Number(h.wingPts).toFixed(1)}pt wings · ` +
      `composite ${Number(h.compositeScore).toFixed(1)}.`
    );
  }

  // Paint the top-3 placements into the mini panel. Shared by the
  // "scenario payload already had it" path and the cold-cache auto-warm
  // fallback that hits /api/breach/wing-console directly.
  function _paintWingConsoleMiniRows(panel, placements) {
    panel.innerHTML = '';
    placements.slice(0, 3).forEach(function (p, i) {
      const row = el('div', 'e15WingMini' + (i === 0 ? ' e15WingMini--top' : ''));
      row.innerHTML = (
        `<div class="e15WingMiniRank">#${i + 1}${i === 0 ? ' ★' : ''}</div>` +
        `<div>EM ${Number(p.em_mult).toFixed(2)}</div>` +
        `<div>${Number(p.wing_pts).toFixed(1)}pt</div>` +
        `<div>${Number(p.short_put_strike).toFixed(0)} / ${Number(p.short_call_strike).toFixed(0)}</div>` +
        `<div>$${Number(p.credit_dollars).toFixed(0)}</div>` +
        `<div class="e15WingMiniScore">${Number(p.composite_score).toFixed(1)}</div>` +
        `<button type="button" class="e15WingMiniSimulate" data-rank="${i}">Simulate</button>`
      );
      panel.appendChild(row);
    });
    panel.querySelectorAll('.e15WingMiniSimulate').forEach(function (btn) {
      btn.addEventListener('click', function () {
        const rank = Number(btn.getAttribute('data-rank') || 0);
        // Use the same scenario body but re-run with the selected rank —
        // the backend's hydrator will replace strikes/credit from that
        // placement's slot before validating.
        $('wingConsolePlacementRank').value = String(rank);
        doCalculate();
      });
    });
  }

  // Auto-warm fallback: the scenario payload came back with
  // `wingConsoleMini: null`, which means E1's in-memory scoring-context
  // cache was cold for this ticker/event (10-min TTL, wiped on every
  // backend restart — so it's effectively "cold most of the time").
  // Rather than silently hiding the panel (which makes E15 look broken
  // to the desk right after a deploy), fire a /api/breach/wing-console
  // POST to warm the context and paint the top-3 placements when it
  // returns. This keeps E15 self-contained: no need to first visit
  // /breach for the same ticker.
  async function _warmWingConsoleMini(panel, ticker, eventDate, eventTiming) {
    panel.innerHTML = (
      `<div class="e15WingMiniWarming" style="` +
        `grid-column:1/-1;padding:10px 14px;` +
        `color:var(--muted);font-size:12px;line-height:1.5;">` +
        `Warming Wing Console context for ${ticker}… ` +
        `(first run on a ticker can take 20–60s; ` +
        `subsequent runs are instant while cache is hot)` +
      `</div>`
    );
    try {
      const data = await postJson('/api/breach/wing-console', {
        ticker: ticker,
        event_date: eventDate,
        event_timing: eventTiming,
        n: 20,
        years: 5,
      });
      const placements = (data && Array.isArray(data.placements))
        ? data.placements : [];
      if (!placements.length) {
        panel.innerHTML = (
          `<div style="grid-column:1/-1;padding:10px 14px;` +
            `color:var(--muted);font-size:12px;">` +
            `Wing Console has no placements for ${ticker} ` +
            `(event pool too thin or cache miss).` +
          `</div>`
        );
        return;
      }
      _paintWingConsoleMiniRows(panel, placements);
    } catch (err) {
      panel.innerHTML = (
        `<div style="grid-column:1/-1;padding:10px 14px;` +
          `color:#b91c1c;font-size:12px;line-height:1.5;">` +
          `Wing Console warm-up failed: ${err && err.message ? err.message : err}. ` +
          `Try opening E1 (/breach) for ${ticker} once, then re-run here.` +
        `</div>`
      );
    }
  }

  function renderWingConsoleMini(d) {
    const divider = $('wingConsoleMiniDivider');
    const panel = $('wingConsoleMiniPanel');
    if (!panel || !divider) return;
    const wc = d && d.wingConsoleMini;
    const hasPlacements =
      wc && Array.isArray(wc.placements) && wc.placements.length > 0;

    if (hasPlacements) {
      divider.style.display = '';
      panel.style.display = 'grid';
      _paintWingConsoleMiniRows(panel, wc.placements);
      return;
    }

    // Cold-cache fallback: scenario payload didn't carry a mini. Kick
    // off a warm-up fetch using the scenario's ticker / earnings context
    // so the desk doesn't see an empty gap where the Wing Console used
    // to be. We only attempt this when we actually have the inputs to
    // address the E1 endpoint; otherwise fall back to the silent-hide.
    const req = (d && d.request) || {};
    const ticker = String(req.ticker || ($('ticker') && $('ticker').value) || '').trim().toUpperCase();
    const eventDate = String(req.earningsDate || req.earnings_date || ($('earningsDate') && $('earningsDate').value) || '').trim();
    const eventTiming = String(req.earningsTiming || req.earnings_timing || ($('earningsTiming') && $('earningsTiming').value) || '').trim().toUpperCase();

    if (!ticker || !eventDate || !['AMC', 'BMO'].includes(eventTiming)) {
      divider.style.display = 'none';
      panel.style.display = 'none';
      panel.innerHTML = '';
      return;
    }

    divider.style.display = '';
    panel.style.display = 'grid';
    _warmWingConsoleMini(panel, ticker, eventDate, eventTiming);
  }

  function renderCrossCheck(d) {
    const divider = $('crossCheckDivider');
    const panel = $('crossCheckPanel');
    if (!panel || !divider) return;
    const cx = d && d.e1WingMAECrossCheck;
    if (!cx || cx.source === 'unavailable' || cx.source === 'missing_inputs') {
      divider.style.display = 'none';
      panel.style.display = 'none';
      panel.innerHTML = '';
      return;
    }
    divider.style.display = '';
    panel.style.display = 'grid';
    const badgeClass = `e15CrossCheckBadge e15CrossCheckBadge--${cx.source}`;
    const labels = {
      convergent:       'MATCH',
      mild_divergence:  'MILD GAP',
      divergent:        'DIVERGE',
    };
    const label = labels[cx.source] || cx.source.toUpperCase();
    panel.innerHTML = (
      `<div class="${badgeClass}">` +
        `<span class="e15CrossCheckLabel">${label}</span>` +
        `<div>${cx.note || ''}` +
        (cx.divergence != null
          ? `<div style="font-size:11px;opacity:0.8;margin-top:4px;">` +
              `divergence=${Number(cx.divergence).toFixed(2)} · ` +
              `E1 MAE p95=${fmtPct(cx.e1_mae_p95_pct, 1)} · ` +
              `E15 WK+breach=${fmtPct((cx.e15_white_knuckle_pct || 0) + (cx.e15_breach_pct || 0), 1)}` +
            `</div>`
          : ''
        ) +
        `</div>` +
      `</div>`
    );
  }

  function renderCrushReading(d) {
    const divider = $('crushReadingDivider');
    const panel = $('crushReadingPanel');
    if (!panel || !divider) return;
    const cr = d && d.crushReading;
    if (!cr || cr.factor == null) {
      divider.style.display = 'none';
      panel.style.display = 'none';
      panel.innerHTML = '';
      return;
    }
    divider.style.display = '';
    panel.style.display = 'grid';
    const source = cr.source || 'fixed';
    const factor = Number(cr.factor);
    const detail = source === 'empirical'
      ? `n=${cr.n_events} analogues · IQR ${fmtNum(cr.p25, 2)}–${fmtNum(cr.p75, 2)}`
      : (cr.fallback_reason || 'fixed default');
    panel.innerHTML = (
      `<div class="e15CrushCard">` +
        `<div class="e15CrushCard__label">Intraday crush factor</div>` +
        `<div class="e15CrushCard__factor">${factor.toFixed(2)}×</div>` +
        `<div class="e15CrushCard__detail">source: <strong>${source}</strong> · ${detail}</div>` +
      `</div>`
    );
  }

  function renderEntryState(d) {
    const cards = $('entryCards');
    cards.innerHTML = '';
    const es = d.entryState || {};
    const req = d.request || {};
    cards.appendChild(card('Ticker', req.ticker || '—',
      `Entry: ${req.entry_date || '—'}  →  Expiry: ${req.expiry || '—'}`));
    cards.appendChild(card('Entry Spot', fmtNum(es.userSpot, 2),
      `Wing: ${fmtNum(es.wingWidth, 2)} pts`));
    cards.appendChild(card('1σ EM (entry→expiry)', fmtPct(es.userEmPct, 2),
      es.userEmSource || ''));
    cards.appendChild(card('Credit / Max Loss',
      `$${fmtNum(req.credit_received, 2)}`,
      `MaxLoss ≈ $${fmtNum((es.wingWidth || 0) - (req.credit_received || 0), 2)}`));
    cards.appendChild(card('Events Used',
      `${d.eventsUsed || 0} / ${d.eventsConsidered || 0}`,
      (d.dataQuality && d.dataQuality.minEventsMet) ? 'Min pool met' : 'Thin sample'));
    cards.appendChild(card('Profit Target / Stop',
      `${fmtInt(req.profit_target_pct)}% / ${fmtInt(req.stop_loss_pct)}%`,
      'Of credit received'));
  }

  function renderPlannedExit(d) {
    $('plannedExitCaveat').textContent =
      (d.plannedExit && d.plannedExit.fidelityCaveat) || '';
    const cards = $('plannedExitCards');
    cards.innerHTML = '';
    const pe = d.plannedExit || {};
    const req = d.request || {};
    cards.appendChild(card('Earnings', req.earnings_date || '—',
      `${req.earnings_timing || '—'}`));
    cards.appendChild(card('Entry → Exit',
      `${req.entry_date || '—'} → ${pe.plannedExitDate || '—'}`,
      `+${fmtNum(pe.plannedExitOffsetHours, 1)}h after open`));
    cards.appendChild(card('Hold (biz days)', fmtInt(pe.holdBizDays),
      'Time-stop enforced in replay'));
    cards.appendChild(card('Crush Factor', fmtNum(pe.intradayCrushFactor, 2),
      'EOD→AM exit approximation'));
    cards.appendChild(card('Fill Model',
      (d.fillModel && d.fillModel.mode) || '—',
      (d.fillModel && d.fillModel.mode === 'mid_penalty')
        ? `+${fmtInt(d.fillModel.penaltyPct)}% half-spread` : ''));
  }

  function renderCreditRichness(d) {
    // Credit richness compares the user's forward credit to the
    // per-analogue mean natural credit. Surfaces the pre-market
    // wide-spread / stale-IV risk visibly before trade submit.
    const cr = d && d.creditRichness;
    let host = $('creditRichnessPanel');
    if (!host) {
      // Inject a minimal container next to the planned-exit cards so we
      // don't require template edits.
      const anchor = $('plannedExitCards') || $('entryCards');
      if (!anchor || !anchor.parentNode) return;
      host = document.createElement('div');
      host.id = 'creditRichnessPanel';
      host.style.marginTop = '8px';
      host.style.padding = '10px 12px';
      host.style.borderRadius = '8px';
      host.style.fontSize = '13px';
      anchor.parentNode.insertBefore(host, anchor.nextSibling);
    }
    if (!cr || cr.analogueMean == null) {
      host.style.display = 'none';
      return;
    }
    host.style.display = 'block';
    const tone = cr.verdict === 'user_cheap' ? ['#fef3c7', '#92400e']
      : cr.verdict === 'user_rich' ? ['#fee2e2', '#991b1b']
      : ['#ecfdf5', '#065f46'];
    host.style.background = tone[0];
    host.style.color = tone[1];
    const userCr = (cr.userCredit != null) ? `$${Number(cr.userCredit).toFixed(2)}` : '—';
    const meanCr = (cr.analogueMean != null) ? `$${Number(cr.analogueMean).toFixed(2)}` : '—';
    const delta  = (cr.deltaPct != null) ? `${cr.deltaPct >= 0 ? '+' : ''}${Number(cr.deltaPct).toFixed(0)}%` : '—';
    host.innerHTML = `<b>Credit richness (${cr.verdict || '—'}):</b> `
      + `user ${userCr} vs. ${cr.n || 0}-event analogue mean ${meanCr} (${delta}).`
      + (cr.note ? `<div style="margin-top:4px;opacity:.85">${cr.note}</div>` : '');
  }

  const OUTCOME_META = {
    fullCollect:  { label: 'Full Collect',  color: '#15803d' },
    earlyTarget:  { label: 'Early Target',  color: '#0ea5e9' },
    whiteKnuckle: { label: 'White Knuckle', color: '#64748b' },
    breach:       { label: 'Breach',        color: '#b91c1c' },
    stopOut:      { label: 'Stop Out',      color: '#d97706' },
  };

  function renderOutcomeDistribution(panelId, dist) {
    const panel = $(panelId);
    panel.innerHTML = '';
    const keys = ['fullCollect', 'earlyTarget', 'whiteKnuckle', 'breach', 'stopOut'];
    let dominant = keys[0];
    let best = -1;
    keys.forEach(k => {
      const pct = ((dist[k] || {}).pct) || 0;
      if (pct > best) { best = pct; dominant = k; }
    });
    keys.forEach(k => {
      const meta = OUTCOME_META[k];
      const v = dist[k] || { pct: 0, n: 0, avgPnlPct: 0, avgDays: 0 };
      const c = el('div', 'e15OutcomeCard' + (k === dominant ? ' dominant' : ''));
      c.appendChild(el('div', 'e15OutcomeName', meta.label));
      const pctEl = el('div', 'e15OutcomePct', fmtPct(v.pct, 0));
      pctEl.style.color = meta.color;
      c.appendChild(pctEl);
      c.appendChild(el('div', 'e15OutcomeMeta',
        `n=${v.n || 0} · Avg P&L ${fmtPct(v.avgPnlPct, 0)}`));
      const bar = el('div', 'e15OutcomeBar');
      const fill = el('div', 'e15OutcomeBarFill');
      fill.style.width = `${Math.max(0, Math.min(100, Number(v.pct) || 0))}%`;
      fill.style.background = meta.color;
      bar.appendChild(fill);
      c.appendChild(bar);
      panel.appendChild(c);
    });
  }

  function renderAdjusted(d) {
    const adj = d.adjustedOutcomeDistribution;
    const cs = d.conditioningSummary;
    if (!adj || !Object.keys(adj).length) {
      $('adjustedDividerLabel').style.display = 'none';
      $('adjustedOutcomePanel').style.display = 'none';
      $('conditioningSummary').style.display = 'none';
      return;
    }
    $('adjustedDividerLabel').style.display = '';
    $('adjustedOutcomePanel').style.display = '';
    if (cs && cs.summary) {
      const el = $('conditioningSummary');
      el.textContent = cs.summary;
      el.style.display = '';
    } else {
      $('conditioningSummary').style.display = 'none';
    }
    renderOutcomeDistribution('adjustedOutcomePanel', adj);
  }

  function renderModifiers(d) {
    const mods = d.conditioningModifiers || {};
    const modifiers = mods.modifiers || [];
    const panel = $('modifiersPanel');
    const divider = $('modifiersDivider');
    panel.innerHTML = '';
    if (!modifiers.length) {
      panel.style.display = 'none';
      divider.style.display = 'none';
      return;
    }
    divider.style.display = '';
    panel.style.display = '';
    modifiers.forEach(m => {
      const c = el('div', 'e15Card');
      c.appendChild(el('div', 'e15CardLabel', m.name || 'modifier'));
      const tilt = m.tailMult != null ? `×${Number(m.tailMult).toFixed(2)} tails` : '';
      const wr = m.wrShiftPct != null ? `${m.wrShiftPct > 0 ? '+' : ''}${Number(m.wrShiftPct).toFixed(1)}pp WR` : '';
      c.appendChild(el('div', 'e15CardValue', [tilt, wr].filter(Boolean).join(' · ') || '—'));
      if (m.reason || m.note) {
        c.appendChild(el('div', 'e15CardCaption', m.reason || m.note));
      }
      panel.appendChild(c);
    });
  }

  function renderMtmChart(d) {
    const timeline = d.mtmTimeline || [];
    if (state.mtmChart) { state.mtmChart.destroy(); state.mtmChart = null; }
    if (!timeline.length || typeof Chart === 'undefined') return;
    const ctx = $('mtmChart').getContext('2d');
    // Backend (_build_mtm_timeline) emits rows sorted entry→expiry with a
    // `dte` field (days-to-expiry). Convert to "business day since entry"
    // for the desk-friendly axis. Fallbacks: explicit `day`, then index.
    const maxDte = (() => {
      for (const p of timeline) {
        if (Number.isFinite(p && p.dte)) return Number(p.dte);
      }
      return null;
    })();
    const labels = timeline.map((p, i) => {
      if (p && Number.isFinite(p.day)) return `D${p.day}`;
      if (maxDte !== null && Number.isFinite(p && p.dte)) return `D${maxDte - Number(p.dte)}`;
      return `D${i}`;
    });
    const p10 = timeline.map(p => p.p10);
    const p50 = timeline.map(p => p.p50);
    const p90 = timeline.map(p => p.p90);
    state.mtmChart = new Chart(ctx, {
      type: 'line',
      data: {
        labels,
        datasets: [
          { label: 'P10', data: p10, borderColor: '#b91c1c', backgroundColor: 'rgba(185,28,28,0.1)', borderWidth: 1.5, tension: 0.2 },
          { label: 'P50', data: p50, borderColor: '#2563eb', backgroundColor: 'rgba(37,99,235,0.15)', borderWidth: 2, tension: 0.2 },
          { label: 'P90', data: p90, borderColor: '#15803d', backgroundColor: 'rgba(21,128,61,0.1)', borderWidth: 1.5, tension: 0.2 },
        ],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { position: 'bottom' } },
        scales: {
          y: { title: { display: true, text: 'P&L %' } },
          x: { title: { display: true, text: 'Biz day since entry' } },
        },
      },
    });
  }

  function renderExpectedValue(d) {
    const ev = d.expectedValue || {};
    const panel = $('evPanel');
    panel.innerHTML = '';
    panel.appendChild(card('Mean P&L', fmtPct(ev.meanPnlPct, 1)));
    panel.appendChild(card('Median P&L', fmtPct(ev.medianPnlPct, 1)));
    panel.appendChild(card('Sharpe-proxy', fmtNum(ev.sharpeProxy, 2),
      'μ/σ across replayed events'));
    const ci = d.outcomeDistributionCI || {};
    if (ci.fullCollect && ci.fullCollect.ci) {
      const full = ci.fullCollect;
      panel.appendChild(card('FullCollect 90% CI',
        `[${fmtPct(full.ci[0], 0)}, ${fmtPct(full.ci[1], 0)}]`,
        `n=${(ci._meta || {}).n || 0} bootstrap iters`));
    }
  }

  function renderExitRules(d) {
    const eo = d.exitRulesOptimization || {};
    const panel = $('exitRulesPanel');
    panel.innerHTML = '';
    panel.appendChild(card('Recommended PT',
      fmtInt(eo.recommendedProfitTarget) + '%',
      'of credit'));
    panel.appendChild(card('Recommended SL',
      fmtInt(eo.recommendedStopLoss) + '%',
      'of credit'));
    panel.appendChild(card('Time-stop (days)',
      fmtInt(eo.recommendedTimeStopDays),
      'Hard exit after N biz days'));
    const delta = eo.deltaFromDefault || {};
    const wrDelta = delta.winRatePct;
    const pnlDelta = delta.avgPnlPct;
    panel.appendChild(card('Δ vs default',
      `${wrDelta != null ? ((wrDelta >= 0 ? '+' : '') + fmtPct(wrDelta, 1)) : '—'} WR`,
      pnlDelta != null ? `${(pnlDelta >= 0 ? '+' : '') + fmtPct(pnlDelta, 1)} avg P&L` : ''));
  }

  function renderMatchedEvents(d) {
    const events = d.matchedEvents || [];
    const wrap = $('matchedEventsWrap');
    wrap.innerHTML = '';
    if (!events.length) {
      wrap.appendChild(el('div', 'e15SectionSubtle', 'No analogue events.'));
      return;
    }
    const tableWrap = el('div', 'e15TableWrap');
    const tableScroll = el('div', 'e15TableScroll');
    const table = el('table', 'e15Table');
    const thead = el('thead');
    const trh = el('tr');
    ['Earn Date','Timing','Entry','Exit','Expiry','Outcome','Exit Day','P&L','MAE','Breached','EM %','Realized %','Analogue Credit'].forEach(h => {
      trh.appendChild(el('th', null, h));
    });
    thead.appendChild(trh);
    table.appendChild(thead);
    const tbody = el('tbody');
    events.forEach(ev => {
      const tr = el('tr');
      tr.appendChild(el('td', null, ev.earnDate || '—'));
      tr.appendChild(el('td', null, ev.anncTod || '—'));
      tr.appendChild(el('td', null, ev.entryDateHist || '—'));
      tr.appendChild(el('td', null, ev.plannedExitDateHist || '—'));
      tr.appendChild(el('td', null, ev.expiryHist || '—'));
      tr.appendChild(el('td', null, ev.outcome || '—'));
      tr.appendChild(el('td', null, fmtInt(ev.exitDay)));
      const pnlTd = el('td', null, fmtPct(ev.pnlPct, 1));
      if (ev.pnlPct != null) pnlTd.className = ev.pnlPct >= 0 ? 'win' : 'loss';
      tr.appendChild(pnlTd);
      tr.appendChild(el('td', null, fmtPct(ev.mae, 1)));
      tr.appendChild(el('td', null, ev.breached ? 'yes' : ''));
      tr.appendChild(el('td', null, fmtPct(ev.impliedMovePct, 2)));
      tr.appendChild(el('td', null, fmtPct(ev.realizedMovePct, 2)));
      tr.appendChild(el('td', null,
        (ev.analogueEntryCredit != null) ? `$${Number(ev.analogueEntryCredit).toFixed(2)}` : '—'));
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    tableScroll.appendChild(table);
    tableWrap.appendChild(tableScroll);
    wrap.appendChild(tableWrap);
  }

  function renderDroppedEvents(d) {
    const drops = d.droppedEvents || [];
    const wrap = $('droppedEventsWrap');
    const divider = $('droppedDivider');
    wrap.innerHTML = '';
    if (!drops.length) {
      wrap.style.display = 'none';
      divider.style.display = 'none';
      return;
    }
    divider.style.display = '';
    wrap.style.display = '';
    const tableWrap = el('div', 'e15TableWrap');
    const tableScroll = el('div', 'e15TableScroll');
    const table = el('table', 'e15Table');
    const thead = el('thead');
    const trh = el('tr');
    ['Earn Date', 'Reason'].forEach(h => trh.appendChild(el('th', null, h)));
    thead.appendChild(trh);
    table.appendChild(thead);
    const tbody = el('tbody');
    drops.forEach(x => {
      const tr = el('tr');
      tr.appendChild(el('td', null, x.earnDate || x.date || '—'));
      tr.appendChild(el('td', null, x.reason || '—'));
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    tableScroll.appendChild(table);
    tableWrap.appendChild(tableScroll);
    wrap.appendChild(tableWrap);
  }

  function renderNotes(d) {
    const ul = $('notesList');
    ul.innerHTML = '';
    (d.notes || []).forEach(n => {
      const li = document.createElement('li');
      li.textContent = n;
      ul.appendChild(li);
    });
  }

  // ------------------------------------------------------------------
  // Actions
  // ------------------------------------------------------------------
  async function doJournal() {
    if (!state.scenario) return;
    setStatus('actionStatus', 'Saving to journal…');
    try {
      const data = await postJson('/api/earnings-ic/journal', {
        request: state.scenario.request,
        scenario: state.scenario,
      });
      setStatus('actionStatus',
        `Saved. Trade ID: ${data.tradeId}. ` +
        `View: ${data.viewUrl || '/earnings-ic?tradeId=' + data.tradeId}`);
    } catch (err) {
      setStatus('actionStatus', 'Journal failed: ' + err.message);
    }
  }

  async function doAdvisor() {
    if (!state.scenario) return;
    setStatus('actionStatus', 'Running advisor…');
    $('advisorPanel').innerHTML = '';
    try {
      const data = await postJson('/api/earnings-ic/advisor', {
        scenario: state.scenario,
        engine1: state.scenario.engine1 || ((state.scan || {}).engine1),
      });
      renderAdvisor(data);
      setStatus('actionStatus', 'Advisor complete.');
    } catch (err) {
      setStatus('actionStatus', 'Advisor failed: ' + err.message);
    }
  }

  function renderAdvisor(a) {
    const p = $('advisorPanel');
    p.innerHTML = '';
    if (!a) return;

    const header = el('div');
    header.style.cssText = 'display:flex;gap:10px;align-items:baseline;flex-wrap:wrap;margin-bottom:8px;';
    const v = a.verdict || '—';
    const vChip = el('span', 'e15Chip');
    vChip.textContent = v;
    if (v === 'GO') vChip.className = 'e15Chip tailwind';
    else if (v === 'PASS') vChip.className = 'e15Chip headwind';
    else vChip.className = 'e15Chip neutral';
    vChip.style.cssText += 'font-size:13px;padding:4px 12px;';
    header.appendChild(vChip);
    if (a.confidence != null) {
      header.appendChild(el('span', 'e15SectionSubtle',
        `Confidence ${a.confidence}% · Stance: ${a.stance || 'neutral'}`));
    }
    p.appendChild(header);

    if (a.narrative) {
      const n = el('div', 'e15Caveat', a.narrative);
      p.appendChild(n);
    }
    if (a.deskNote) {
      const dn = el('div');
      dn.style.cssText = 'font-size:12px;font-style:italic;color:var(--muted);margin-bottom:10px;';
      dn.textContent = '"' + a.deskNote + '"';
      p.appendChild(dn);
    }

    if (Array.isArray(a.keyPoints) && a.keyPoints.length) {
      p.appendChild(el('div', 'e15CardLabel', 'Key points'));
      const ul = el('ul', 'e15NoteList');
      a.keyPoints.forEach(b => {
        const li = document.createElement('li');
        li.textContent = b;
        ul.appendChild(li);
      });
      p.appendChild(ul);
    }
    if (Array.isArray(a.risks) && a.risks.length) {
      p.appendChild(el('div', 'e15CardLabel', 'Risks'));
      const ul = el('ul', 'e15NoteList');
      a.risks.forEach(b => {
        const li = document.createElement('li');
        li.textContent = b;
        ul.appendChild(li);
      });
      p.appendChild(ul);
    }
    if (Array.isArray(a.suggestedAdjustments) && a.suggestedAdjustments.length) {
      p.appendChild(el('div', 'e15CardLabel', 'Suggested adjustments'));
      const ul = el('ul', 'e15NoteList');
      a.suggestedAdjustments.forEach(x => {
        const li = document.createElement('li');
        li.innerHTML = `<strong>[${x.type}]</strong> ${x.suggestion}`
          + (x.rationale ? ` <span style="color:var(--muted);">— ${x.rationale}</span>` : '');
        ul.appendChild(li);
      });
      p.appendChild(ul);
    }
    if (a.plannedExitNote) {
      p.appendChild(el('div', 'e15SectionSubtle', 'Planned-exit: ' + a.plannedExitNote));
    }
    const src = a._source ? `Source: ${a._source}` : '';
    const model = a._model ? ` · ${a._model}` : '';
    const fb = a._fallback_reason ? ` · ${a._fallback_reason}` : '';
    if (src || model || fb) {
      p.appendChild(el('div', 'e15SectionSubtle', src + model + fb));
    }
  }

  async function doReconcile() {
    if (!state.scenario) return;
    setStatus('actionStatus', 'Reconciling…');
    try {
      const data = await postJson('/api/earnings-ic/reconcile', {
        scenario: state.scenario,
      });
      const chip = (data.reconcile && data.reconcile.creditChip) || {};
      setStatus('actionStatus',
        `Reconcile: ${chip.status || 'unknown'}` +
        (chip.note ? ` — ${chip.note}` : ''));
    } catch (err) {
      setStatus('actionStatus', 'Reconcile failed: ' + err.message);
    }
  }

  async function doBackfill() {
    const t = ($('ticker').value || '').trim().toUpperCase();
    if (!t) return;
    const token = prompt('X-Admin-Token?');
    if (!token) return;
    setStatus('runStatus', 'Starting backfill…');
    try {
      const data = await postJson(
        '/api/earnings-ic/backfill',
        { ticker: t },
        { 'X-Admin-Token': token },
      );
      setStatus('runStatus', `Backfill kicked off for ${t}. ${JSON.stringify(data.state?.params || {})}`);
      pollBackfillStatus(t, token);
    } catch (err) {
      setStatus('runStatus', 'Backfill failed: ' + err.message);
    }
  }

  async function pollBackfillStatus(ticker, token) {
    for (let i = 0; i < 120; i++) {
      await new Promise(r => setTimeout(r, 2000));
      try {
        const s = await getJson('/api/earnings-ic/backfill/status?ticker=' + encodeURIComponent(ticker));
        const p = s.progress || {};
        if (!s.running) {
          const cov = s.coverage || {};
          setStatus('runStatus',
            `Backfill done. ${s.result?.succeeded || 0} succeeded, ${s.result?.failed || 0} failed. ` +
            `Cache: ${cov.daysCovered || 0} days.`);
          return;
        }
        setStatus('runStatus',
          `Backfill running: ${p.completed || 0}/${p.total || '?'} ok=${p.succeeded || 0} fail=${p.failed || 0}`);
      } catch (err) { /* swallow */ }
    }
  }

  // ------------------------------------------------------------------
  // Desk Insight v2 — per-card extractor
  // ------------------------------------------------------------------
  // Shared popup + fetcher + cache live in static/desk-insight.js. This
  // page just owns the slug → card-data slicer + the scenario-context
  // builder; we register those with DeskInsight.bind() at init().
  function extractCardData(slug, payload) {
    if (!payload) return {};
    switch (slug) {
      case 'e1_summary_strip':
        return payload.engine1Summary || {};
      case 'wing_console_mini':
        return payload.wingConsoleMini || {};
      case 'e1_wing_mae_crosscheck':
        return payload.e1WingMAECrossCheck || {};
      case 'crush_reading':
        return payload.crushReading || {};
      case 'event_analogue_row': {
        // Sample first-and-last matched events so the card has something
        // to render even before the desk clicks a specific row.
        const rows = payload.matchedEvents || [];
        return {
          total: rows.length,
          sample: rows.length > 0 ? [rows[0], rows[rows.length - 1]] : [],
          note: 'Each analogue row replays the desk\'s chosen strikes through that historical earnings event.',
        };
      }
      case 'planned_exit_outcome': {
        const dist = payload.outcomeDistribution || {};
        const ci = payload.outcomeDistributionCI || {};
        return {
          plannedExit: payload.plannedExit || {},
          fullCollect: dist.fullCollect || null,
          earlyTarget: dist.earlyTarget || null,
          whiteKnuckle: dist.whiteKnuckle || null,
          stopOut: dist.stopOut || null,
          breach: dist.breach || null,
          ci: ci,
        };
      }
      case 'entry_state':
        return {
          entryState: payload.entryState || {},
          plannedExit: payload.plannedExit || {},
          eventsUsed: payload.eventsUsed,
          eventsConsidered: payload.eventsConsidered,
          fillModel: payload.fillModel || null,
          creditRichness: payload.creditRichness || null,
        };
      case 'planned_exit_timing':
        return {
          plannedExit: payload.plannedExit || {},
          request: {
            entryDate: (payload.request || {}).entry_date,
            expiry: (payload.request || {}).expiry,
            earningsDate: (payload.request || {}).earnings_date,
            earningsTiming: (payload.request || {}).earnings_timing,
          },
        };
      case 'outcome_distribution_empirical':
        return {
          distribution: payload.outcomeDistribution || {},
          ci: payload.outcomeDistributionCI || null,
          fillModel: payload.fillModel || null,
          eventsUsed: payload.eventsUsed,
        };
      case 'adjusted_distribution':
        return {
          empirical: payload.outcomeDistribution || {},
          adjusted: payload.adjustedOutcomeDistribution || {},
          summary: payload.conditioningSummary || null,
        };
      case 'conditioning_summary':
        return payload.conditioningSummary || {};
      case 'conditioning_modifiers':
        return payload.conditioningModifiers || {};
      case 'mtm_timeline': {
        // Keep payload compact: send first / mid / last percentile rows.
        var tl = payload.mtmTimeline || [];
        if (tl.length <= 5) return { timeline: tl, plannedExit: payload.plannedExit || {} };
        var mid = Math.floor(tl.length / 2);
        return {
          timeline: [tl[0], tl[mid], tl[tl.length - 1]],
          nSteps: tl.length,
          plannedExit: payload.plannedExit || {},
        };
      }
      case 'expected_value':
        return {
          expectedValue: payload.expectedValue || {},
          ci: (payload.outcomeDistributionCI || {}).fullCollect || null,
          eventsUsed: payload.eventsUsed,
        };
      case 'exit_rules_card': {
        var opt = payload.exitRulesOptimization || {};
        return {
          recommendedProfitTarget: opt.recommendedProfitTarget,
          recommendedStopLoss: opt.recommendedStopLoss,
          recommendedTimeStopDays: opt.recommendedTimeStopDays,
          deltaFromDefault: opt.deltaFromDefault || null,
          gridSize: (opt.grid || []).length,
          plannedHoldBizDays: (payload.plannedExit || {}).holdBizDays,
        };
      }
      case 'matched_events': {
        var rows = payload.matchedEvents || [];
        return {
          total: rows.length,
          sample: rows.slice(0, 12).map(function (r) {
            return {
              earnDate: r.earnDate, anncTod: r.anncTod,
              outcome: r.outcome, exitDay: r.exitDay,
              pnlPct: r.pnlPct, mae: r.mae, breached: r.breached,
              impliedMovePct: r.impliedMovePct,
              realizedMovePct: r.realizedMovePct,
            };
          }),
        };
      }
      case 'dropped_events': {
        var drops = payload.droppedEvents || [];
        return {
          total: drops.length,
          matched: (payload.matchedEvents || []).length,
          sample: drops.slice(0, 10).map(function (d) {
            return { earnDate: d.earnDate || d.date, reason: d.reason };
          }),
        };
      }
      case 'notes_caveats':
        return { notes: payload.notes || [], dataQuality: payload.dataQuality || null };
      case 'actions_panel':
        return {
          hasJournal: true,
          hasAdvisor: true,
          hasReconcile: true,
          note: 'Buttons trigger /journal, /advisor, /reconcile against this scenario.',
        };
      case 'credit_richness':
        return payload.creditRichness || {};
      case 'vrp_crush_verdict': {
        var e1 = payload.engine1Summary || {};
        return {
          vrpScore: e1.vrpScore,
          ivElevation: e1.ivElevation,
          plannedExit: payload.plannedExit || {},
        };
      }
      default:
        return {};
    }
  }

  function buildScenarioContext() {
    if (!state.scenario) {
      return { note: 'No scenario has been run yet — explain generically.' };
    }
    var sc = state.scenario || {};
    var es = sc.entryState || {};
    var req = sc.request || {};
    return {
      ticker: req.ticker,
      entryDate: req.entry_date,
      expiry: req.expiry,
      earningsDate: req.earnings_date,
      earningsTiming: req.earnings_timing,
      plannedExitDate: req.planned_exit_date,
      shortPut: req.short_put, longPut: req.long_put,
      shortCall: req.short_call, longCall: req.long_call,
      creditReceived: req.credit_received,
      profitTargetPct: req.profit_target_pct,
      stopLossPct: req.stop_loss_pct,
      userSpot: es.userSpot,
      userEmPct: es.userEmPct,
      eventsUsed: sc.eventsUsed,
      eventsConsidered: sc.eventsConsidered,
      fillModelMode: (sc.fillModel || {}).mode || null,
    };
  }

  // ------------------------------------------------------------------
  // Init
  // ------------------------------------------------------------------
  function refreshCalculateEnabled() {
    const btn = $('runBtn');
    if (!btn) return;
    const t = ($('ticker').value || '').trim();
    const d = $('earningsDate').value || '';
    const tm = String($('earningsTiming').value || '').toUpperCase();
    btn.disabled = !(t && d && ['AMC', 'BMO', 'UNK'].includes(tm));
  }

  function init() {
    const form = $('commandDeckForm');
    if (form) form.addEventListener('submit', doCalculate);

    $('journalBtn').addEventListener('click', doJournal);
    $('advisorBtn').addEventListener('click', doAdvisor);
    $('reconcileBtn').addEventListener('click', doReconcile);
    const backfillBtn = $('backfillBtn');
    if (backfillBtn) backfillBtn.addEventListener('click', doBackfill);

    // Calculate enablement tracks ticker + earnings date + timing.
    ['ticker', 'earningsDate', 'earningsTiming'].forEach(function (id) {
      const el = $(id);
      if (el) el.addEventListener('input', refreshCalculateEnabled);
      if (el) el.addEventListener('change', refreshCalculateEnabled);
    });
    refreshCalculateEnabled();

    // Strike Scanner: debounced re-run when any leg or credit changes —
    // lets the desk tweak the form and watch the verdict refresh live.
    ['shortPut', 'longPut', 'shortCall', 'longCall', 'creditReceived'].forEach(function (id) {
      const el = $(id);
      if (!el) return;
      const handler = function () {
        if (state.scenario) scheduleStrikeScan(state.scenario);
      };
      el.addEventListener('change', handler);
    });

    // Live tab-title updates so multiple E15 tabs are distinguishable in
    // the Chrome tab strip. See ui_kit.js#setEngineTabTitle for format.
    const tickerInput = $('ticker');
    if (tickerInput && window.RavenUI && RavenUI.setEngineTabTitle) {
      tickerInput.addEventListener('input', function () {
        RavenUI.setEngineTabTitle(tickerInput.value);
      });
    }

    if (window.DeskInsight) {
      window.DeskInsight.bind({
        engineId:           'e15',
        dividerSelector:    '.deskDivider[data-insight]',
        slugTitles: {
          e1_summary_strip:               'Engine 1 Summary',
          wing_console_mini:              'Wing Console (E1 top picks)',
          e1_wing_mae_crosscheck:         'E1 Cross-Check Badge',
          crush_reading:                  'Intraday Crush Reading',
          entry_state:                    'Entry State',
          planned_exit_timing:            'Planned Exit Timing',
          outcome_distribution_empirical: 'Outcome Distribution (Empirical)',
          adjusted_distribution:          'Adjusted Distribution',
          conditioning_summary:           'Conditioning Summary',
          conditioning_modifiers:         'Conditioning Modifiers',
          mtm_timeline:                   'MTM Timeline',
          expected_value:                 'Expected Value',
          exit_rules_card:                'Exit Rules (Planned Hold)',
          matched_events:                 'Matched Events',
          dropped_events:                 'Dropped Events',
          notes_caveats:                  'Notes & Caveats',
          actions_panel:                  'Actions',
          credit_richness:                'Credit Richness',
          vrp_crush_verdict:              'VRP / Vol Crush Verdict',
        },
        getCardData:        function (slug) { return extractCardData(slug, state.scenario); },
        getScenarioContext: buildScenarioContext,
      });
    }

    // Check feature flag
    getJson('/api/earnings-ic/health').then(h => {
      if (!h.enabled) {
        banner('Engine 15 is disabled on the server (ENABLE_ENGINE15_EARNINGS_IC=0).', 'error');
        if ($('runBtn')) $('runBtn').disabled = true;
      }
    }).catch(() => { /* ignore */ });

    // Deep-links:
    //   /earnings-ic?ticker=NVDA                             — prefill ticker, run scan
    //   /earnings-ic?ticker=NVDA&event_date=2026-05-28&event_timing=AMC  — full prefill
    //   /earnings-ic?ticker=NVDA&wc_key=...&rank=0          — Wing Console handoff
    //   /earnings-ic?tradeId=...                             — journal replay (legacy)
    try {
      const q = new URLSearchParams(window.location.search);
      const t = (q.get('ticker') || '').trim().toUpperCase();
      const ed = (q.get('event_date') || q.get('eventDate') || q.get('earningsDate') || '').trim();
      const et = (q.get('event_timing') || q.get('eventTiming') || q.get('earningsTiming') || '').trim().toUpperCase();
      const wcKey = (q.get('wc_key') || q.get('wing_console_cache_key') || '').trim();
      const rank = (q.get('rank') || q.get('placement_rank') || '0').trim();

      if (t) {
        $('ticker').value = t;
        if (window.RavenUI && RavenUI.setEngineTabTitle) RavenUI.setEngineTabTitle(t);
      }
      if (ed) $('earningsDate').value = ed;
      if (et && ['AMC', 'BMO', 'UNK'].includes(et)) $('earningsTiming').value = et;
      if (wcKey) {
        $('wingConsoleCacheKey').value = wcKey;
        $('wingConsolePlacementRank').value = String(parseInt(rank, 10) || 0);
      }
      refreshCalculateEnabled();

      // Auto-run Command Deck when the handoff or full prefill is present.
      const canAutorun = !!(t && ed && ['AMC', 'BMO', 'UNK'].includes(et));
      if (canAutorun) {
        doCalculate();
      } else if (t) {
        // Ticker-only deep link: kick off a scan so the desk sees E1
        // summary + has Advanced prefilled; they still need to enter
        // date + timing before the full Calculate fires.
        doScan();
      }
    } catch (e) { /* ignore */ }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
