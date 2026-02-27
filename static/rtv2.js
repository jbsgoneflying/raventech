/* ── RTv2.0 Dashboard JS ─────────────────────────────────────── */

const API = '/api/rtv2';
let _data = null;

/* ── Helpers ────────────────────────────────────────────────── */
function $(id) { return document.getElementById(id); }
function h(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }
function fmt(n, d) { return (typeof n === 'number') ? n.toFixed(d === undefined ? 2 : d) : '—'; }
function pct(n) { return (typeof n === 'number') ? (n * 100).toFixed(1) + '%' : '—'; }

function stateColor(state) {
  const m = { ON_TRACK: 'green', NEAR_TARGET: 'blue', RISK_INCREASING: 'amber', THESIS_WEAKENING: 'orange', INVALIDATED: 'red' };
  return m[(state || '').toUpperCase()] || 'gray';
}

function actionClass(action) {
  const m = { HOLD: 'green', TRIM: 'amber', TIGHTEN: 'amber', EXIT: 'red', REVIEW: 'blue' };
  return m[(action || '').toUpperCase()] || 'gray';
}

function regimeColor(r) {
  const m = { 'Risk-On': 'green', 'Transitional': 'amber', 'Risk-Off': 'red', 'Stressed': 'red' };
  return m[r] || 'gray';
}

function bucketColor(b) {
  const m = { income_core: 'green', directional: 'blue', opportunistic: 'amber' };
  return m[b] || 'gray';
}

function showOverlay(msg) {
  const ov = $('ravenOverlay');
  if (ov) { ov.style.display = 'flex'; $('ravenStatus').textContent = msg || 'Loading…'; }
}
function hideOverlay() {
  const ov = $('ravenOverlay');
  if (ov) ov.style.display = 'none';
}
function setProgress(pct) {
  const f = $('ravenProgressFill');
  if (f) f.style.width = pct + '%';
}


/* ── Main load (passive — reads cached state) ──────────────── */
async function rv2Load() {
  showOverlay('Loading RTv2.0 dashboard…');
  setProgress(10);
  try {
    const resp = await fetch(API + '/init');
    if (!resp.ok) throw new Error('Init failed: ' + resp.status);
    setProgress(60);
    _data = await resp.json();
    setProgress(80);
    renderAll(_data);
    setProgress(100);
    setTimeout(hideOverlay, 300);
  } catch (e) {
    console.error('RTv2 init error:', e);
    $('ravenStatus').textContent = 'Failed to load: ' + e.message;
    setProgress(100);
    setTimeout(hideOverlay, 2000);
  }
}

/* ── Full refresh (bootstraps engines → ingests signals → returns dashboard) */
async function rv2Refresh() {
  showOverlay('Bootstrapping engines…');
  setProgress(5);
  const btn = $('rv2Refresh');
  if (btn) { btn.disabled = true; btn.textContent = 'Running…'; }

  try {
    $('ravenStatus').textContent = 'Running Engine 5 (regime), Engine 3 (Red Dog), Engine 4 (Ichimoku)…';
    setProgress(10);

    const resp = await fetch(API + '/refresh', { method: 'POST' });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.detail || 'Refresh failed: ' + resp.status);
    }
    setProgress(70);

    $('ravenStatus').textContent = 'Rendering dashboard…';
    _data = await resp.json();
    setProgress(85);
    renderAll(_data);

    // Show ingestion summary
    const ref = _data._refresh || {};
    const msg = `Scanned: ${(ref.engines_scanned || []).join(', ') || 'none'} · ` +
                `Signals: ${ref.signals_extracted || 0} · Trades queued: ${ref.trades_created || 0}`;
    renderRefreshStatus(msg, ref.engine_status || {});

    setProgress(100);
    setTimeout(hideOverlay, 300);
  } catch (e) {
    console.error('RTv2 refresh error:', e);
    $('ravenStatus').textContent = 'Refresh failed: ' + e.message;
    setProgress(100);
    setTimeout(hideOverlay, 2000);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Refresh Dashboard'; }
  }
}

function renderRefreshStatus(msg, engineStatus) {
  let el = $('rv2RefreshStatus');
  if (!el) {
    const bar = document.querySelector('.controlBar');
    if (bar) {
      el = document.createElement('div');
      el.id = 'rv2RefreshStatus';
      el.style.cssText = 'font-size:11px;color:var(--muted);padding:6px 16px;border-top:1px solid var(--border)';
      bar.parentNode.insertBefore(el, bar.nextSibling);
    }
  }
  if (!el) return;
  let html = msg;
  for (const [eid, st] of Object.entries(engineStatus)) {
    const ok = st === 'ok' || st === 'no_data';
    html += ` · <span class="rv2Pill rv2Pill--${ok ? 'green' : 'red'}">${h(eid)}: ${h(st)}</span>`;
  }
  el.innerHTML = html;
}


/* ── Render all panels ──────────────────────────────────────── */
function renderAll(d) {
  renderContextCards(d);
  renderAllocation(d);
  renderRisk(d);
  renderPositions(d);
  renderQueue(d);
  renderPerformance(d);
}


/* ── Context cards (Regime, Flow, Vol) ──────────────────────── */
function renderContextCards(d) {
  // Regime
  const regime = d.regime || 'Transitional';
  $('rv2RegimeVal').textContent = regime;
  $('rv2RegimeVal').className = 'rv2BigVal';
  const rc = d.regime_card || {};
  const driversText = (rc.drivers || []).join(' · ');
  $('rv2RegimeSub').textContent = driversText || (regime === 'Transitional' ? 'Click Refresh Dashboard to load regime data' : 'No drivers reported');

  // Flow
  const fc = d.flow_card || {};
  const flowScore = fc.composite_score != null ? fmt(fc.composite_score, 0) : (fc.score != null ? fmt(fc.score, 0) : '—');
  $('rv2FlowVal').textContent = flowScore;
  $('rv2FlowSub').textContent = fc.label || fc.flow_label || '';

  // Vol
  const vc = d.vol_card || {};
  $('rv2VolVal').textContent = vc.vol_state || vc.vol_direction || '—';
  $('rv2VolSub').textContent = vc.vol_direction ? 'Direction: ' + vc.vol_direction : '';
}


/* ── Allocation bars ────────────────────────────────────────── */
function renderAllocation(d) {
  const alloc = (d.allocation || {}).buckets || {};
  const container = $('rv2AllocBars');
  let html = '';
  for (const [name, b] of Object.entries(alloc)) {
    if (name === 'cash_reserve') continue;
    const used = parseFloat(b.used_ru || 0);
    const max = parseFloat(b.max_ru || 1);
    const pctW = Math.min(100, (used / max) * 100);
    const col = bucketColor(name);
    const label = name.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
    html += `<div class="rv2Bar">
      <span class="label">${h(label)}</span>
      <div class="track"><div class="fill fill--${col}" style="width:${pctW}%"></div></div>
      <span class="val">${fmt(used,1)} / ${fmt(max,1)} RU</span>
    </div>`;
  }
  container.innerHTML = html || '<div class="rv2Empty">No allocation data. Click <strong>Refresh Dashboard</strong> to bootstrap engines.</div>';

  const a = d.allocation || {};
  $('rv2AllocSummary').textContent = `Regime: ${a.regime || '—'} · Total RU: ${fmt(a.total_used_ru,1)} / ${fmt(a.portfolio_ru_cap,0)}`;
}


/* ── Risk dashboard ─────────────────────────────────────────── */
function renderRisk(d) {
  const r = d.risk || {};
  const container = $('rv2RiskContent');
  let html = '';

  html += `<div class="rv2Bar"><span class="label">Portfolio RU</span>
    <div class="track"><div class="fill fill--${r.ru_utilisation_pct > 80 ? 'red' : r.ru_utilisation_pct > 50 ? 'amber' : 'green'}" style="width:${r.ru_utilisation_pct || 0}%"></div></div>
    <span class="val">${fmt(r.total_ru,1)} / ${fmt(r.portfolio_ru_cap,0)}</span></div>`;

  html += `<div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:8px">`;
  html += `<span class="rv2Pill rv2Pill--${regimeColor(r.regime)}">Regime: ${h(r.regime || '—')}</span>`;
  html += `<span class="rv2Pill rv2Pill--${r.directional_tilt === 'neutral' ? 'green' : 'amber'}">Tilt: ${h(r.directional_tilt || 'neutral')}</span>`;
  if (r.credit_stress_warning) html += `<span class="rv2Pill rv2Pill--red">E9 Stress: ${fmt(r.e9_level,0)}</span>`;
  if (r.drawdown_warning) html += `<span class="rv2Pill rv2Pill--red">DD: ${pct(r.weekly_drawdown_pct)}</span>`;
  html += `</div>`;

  if (r.sector_warnings && r.sector_warnings.length) {
    html += `<div style="margin-top:8px;font-size:11px;color:var(--red)">`;
    r.sector_warnings.forEach(w => { html += `<div>⚠ ${h(w)}</div>`; });
    html += `</div>`;
  }

  container.innerHTML = html;
}


/* ── Positions table ────────────────────────────────────────── */
function renderPositions(d) {
  const positions = d.positions || [];
  const summary = d.positions_summary || {};

  // health pills
  const hc = $('rv2HealthPills');
  let pills = '';
  if (summary.ON_TRACK) pills += `<span class="rv2Pill rv2Pill--green">${summary.ON_TRACK} On Track</span> `;
  if (summary.NEAR_TARGET) pills += `<span class="rv2Pill rv2Pill--blue">${summary.NEAR_TARGET} Near Target</span> `;
  if (summary.RISK_INCREASING) pills += `<span class="rv2Pill rv2Pill--amber">${summary.RISK_INCREASING} Risk ↑</span> `;
  if (summary.THESIS_WEAKENING) pills += `<span class="rv2Pill rv2Pill--orange">${summary.THESIS_WEAKENING} Weakening</span> `;
  if (summary.INVALIDATED) pills += `<span class="rv2Pill rv2Pill--red">${summary.INVALIDATED} Invalidated</span> `;
  hc.innerHTML = pills || '<span class="rv2Pill rv2Pill--gray">No positions</span>';

  const container = $('rv2PositionsTable');
  if (!positions.length) {
    container.innerHTML = '<div class="rv2Empty">No active positions. Click <strong>Refresh Dashboard</strong> to scan engines, or use <strong>Manual Trade Entry</strong> below.</div>';
    return;
  }

  let html = `<table class="rv2Table"><thead><tr>
    <th>Ticker</th><th>Engine</th><th>Type</th><th>Dir</th>
    <th>Entry</th><th>Stop</th><th>Target</th><th>RU</th>
    <th>P&L</th><th>Days</th><th>State</th><th>Action</th><th>Reason</th>
    <th></th>
  </tr></thead><tbody>`;

  for (const p of positions) {
    const sc = stateColor(p.position_state);
    const ac = actionClass(p.suggested_action);
    html += `<tr>
      <td><strong>${h(p.ticker || '')}</strong></td>
      <td><span class="rv2Pill rv2Pill--gray">${h(p.engine_source || '')}</span></td>
      <td style="font-size:11px">${h(p.trade_type || '')}</td>
      <td>${h(p.direction || '')}</td>
      <td class="mono">${fmt(p.entry_price)}</td>
      <td class="mono">${fmt(p.thesis_stop)}</td>
      <td class="mono">${fmt(p.thesis_target)}</td>
      <td class="mono">${fmt(p.derived_ru, 1)}</td>
      <td class="mono" style="color:${(p.current_pnl_pct||0) >= 0 ? 'var(--green)' : 'var(--red)'}">${pct(p.current_pnl_pct)}</td>
      <td>${p.days_in_trade || 0}d</td>
      <td><span class="rv2Dot rv2Dot--${sc}"></span><span class="rv2Pill rv2Pill--${sc}">${h(p.position_state || '')}</span></td>
      <td><span class="rv2Pill rv2Pill--${ac}">${h(p.suggested_action || '')}</span></td>
      <td style="font-size:10px;color:var(--muted);max-width:200px">${h(p.state_reason || '')}</td>
      <td>${p.suggested_action === 'EXIT'
        ? `<button class="rv2Btn rv2Btn--red" onclick="rv2CloseTrade('${p.trade_id}')">Close</button>`
        : ''}</td>
    </tr>`;
  }

  html += '</tbody></table>';
  container.innerHTML = html;
}


/* ── Idea queue table ───────────────────────────────────────── */
function renderQueue(d) {
  const queue = d.queue || [];
  const container = $('rv2QueueTable');
  if (!queue.length) {
    container.innerHTML = '<div class="rv2Empty">No ideas in queue. Click <strong>Refresh Dashboard</strong> to scan Engine 3 (Red Dog), Engine 4 (Ichimoku), and others.</div>';
    return;
  }

  let html = `<table class="rv2Table"><thead><tr>
    <th>Ticker</th><th>Engine</th><th>Bucket</th><th>UPS</th><th>State</th><th>RU</th><th>Created</th><th></th>
  </tr></thead><tbody>`;

  for (const q of queue) {
    html += `<tr>
      <td><strong>${h(q.ticker || '')}</strong></td>
      <td><span class="rv2Pill rv2Pill--gray">${h(q.engine_source || '')}</span></td>
      <td><span class="rv2Pill rv2Pill--${bucketColor(q.bucket)}">${h(q.bucket || '')}</span></td>
      <td class="mono" style="font-weight:700">${fmt(q.ups_score, 1)}</td>
      <td><span class="rv2Pill rv2Pill--gray">${h(q.lifecycle_state || '')}</span></td>
      <td class="mono">${fmt(q.derived_ru, 1)}</td>
      <td style="font-size:10px">${(q.created_at || '').slice(0, 10)}</td>
      <td>
        ${q.lifecycle_state === 'QUEUED' ? `<button class="rv2Btn rv2Btn--blue" onclick="rv2StageTrade('${q.trade_id}')">Stage</button>` : ''}
        ${q.lifecycle_state === 'STAGED' ? `<button class="rv2Btn rv2Btn--green" onclick="rv2ActivatePrompt('${q.trade_id}')">Activate</button>` : ''}
      </td>
    </tr>`;
  }

  html += '</tbody></table>';
  container.innerHTML = html;
}


/* ── Performance scorecard ──────────────────────────────────── */
function renderPerformance(d) {
  const perf = d.performance || {};
  const container = $('rv2PerfContent');
  const engines = perf.engines || {};
  const buckets = perf.buckets || {};

  if (!Object.keys(engines).length && !Object.keys(buckets).length) {
    container.innerHTML = '<div class="rv2Empty">No performance data yet. Close trades to build the scorecard. Metrics accumulate after trades are completed.</div>';
    return;
  }

  let html = '<div style="margin-bottom:12px"><strong style="font-size:11px;color:var(--muted)">ENGINE METRICS (90-day)</strong></div>';
  html += `<table class="rv2Table"><thead><tr><th>Engine</th><th>Trades</th><th>Win Rate</th><th>Avg Return/RU</th><th>Avg Days</th><th>Streak</th></tr></thead><tbody>`;
  for (const [eid, m] of Object.entries(engines)) {
    const wr = m.win_rate != null ? pct(m.win_rate) : '—';
    const streak = m.consecutive_wins > 0 ? `W${m.consecutive_wins}` : m.consecutive_losses > 0 ? `L${m.consecutive_losses}` : '—';
    const streakCol = m.consecutive_wins > 0 ? 'green' : m.consecutive_losses > 0 ? 'red' : 'gray';
    html += `<tr>
      <td><strong>${h(eid)}</strong></td>
      <td>${m.trade_count || 0}</td>
      <td>${wr}</td>
      <td class="mono">${fmt(m.avg_return_per_ru)}</td>
      <td>${fmt(m.avg_days_held, 1)}</td>
      <td><span class="rv2Pill rv2Pill--${streakCol}">${streak}</span></td>
    </tr>`;
  }
  html += '</tbody></table>';

  if (Object.keys(buckets).length) {
    html += '<div style="margin:16px 0 8px"><strong style="font-size:11px;color:var(--muted)">BUCKET METRICS (90-day)</strong></div>';
    html += `<table class="rv2Table"><thead><tr><th>Bucket</th><th>Trades</th><th>Win Rate</th><th>Total P&L</th><th>Drawdown</th></tr></thead><tbody>`;
    for (const [bid, m] of Object.entries(buckets)) {
      const label = bid.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
      html += `<tr>
        <td><strong>${h(label)}</strong></td>
        <td>${m.trade_count || 0}</td>
        <td>${pct(m.win_rate)}</td>
        <td class="mono" style="color:${(m.total_pnl||0)>=0?'var(--green)':'var(--red)'}">$${fmt(m.total_pnl)}</td>
        <td class="mono">$${fmt(m.worst_drawdown)}</td>
      </tr>`;
    }
    html += '</tbody></table>';
  }

  container.innerHTML = html;
}


/* ── Actions ────────────────────────────────────────────────── */
async function rv2SubmitManual() {
  const body = {
    ticker:  $('mTicker').value.toUpperCase().trim(),
    direction: $('mDirection').value,
    entry_price: parseFloat($('mEntryPrice').value),
    units: parseInt($('mUnits').value, 10),
    trade_type: $('mTradeType').value,
    thesis_stop: parseFloat($('mStop').value),
    thesis_target: parseFloat($('mTarget').value),
    thesis_max_days: parseInt($('mMaxDays').value, 10) || 10,
    invalidation_conditions: $('mInvalidation').value.split('\n').map(s => s.trim()).filter(Boolean),
    bucket: $('mBucket').value,
    sector: $('mSector').value.trim(),
    notes: $('mNotes').value.trim(),
  };

  if (!body.ticker || !body.entry_price || !body.units || !body.thesis_stop || !body.thesis_target) {
    alert('Please fill in all required fields (ticker, entry price, units, stop, target).');
    return;
  }

  try {
    const resp = await fetch(API + '/positions/manual', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body) });
    if (!resp.ok) { const e = await resp.json().catch(() => ({})); throw new Error(e.detail || resp.statusText); }
    const result = await resp.json();
    alert(`Trade created: ${result.trade_id}\nDerived RU: ${fmt(result.derived_ru || result.ru_info?.capped_ru, 2)}`);
    rv2Load();
  } catch (e) {
    alert('Error: ' + e.message);
  }
}

async function rv2StageTrade(tradeId) {
  try {
    const resp = await fetch(API + `/trades/${tradeId}/stage`, { method: 'POST' });
    if (!resp.ok) { const e = await resp.json().catch(() => ({})); throw new Error(e.detail || resp.statusText); }
    rv2Load();
  } catch (e) { alert('Stage error: ' + e.message); }
}

async function rv2ActivatePrompt(tradeId) {
  const price = prompt('Enter entry price:');
  if (!price) return;
  const units = prompt('Enter units (shares/contracts):');
  if (!units) return;

  try {
    const body = { entry_price: parseFloat(price), units: parseInt(units, 10), direction: 'long' };
    const resp = await fetch(API + `/trades/${tradeId}/activate`, { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body) });
    if (!resp.ok) { const e = await resp.json().catch(() => ({})); throw new Error(e.detail || resp.statusText); }
    rv2Load();
  } catch (e) { alert('Activate error: ' + e.message); }
}

async function rv2CloseTrade(tradeId) {
  const pnl = prompt('Enter P&L in dollars (negative for loss):');
  if (pnl === null) return;
  const reason = prompt('Exit reason (target/stop/desk_discretion):', 'desk_discretion');
  if (!reason) return;

  try {
    const body = { pnl_dollars: parseFloat(pnl) || 0, exit_reason: reason };
    const resp = await fetch(API + `/trades/${tradeId}/close`, { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body) });
    if (!resp.ok) { const e = await resp.json().catch(() => ({})); throw new Error(e.detail || resp.statusText); }
    rv2Load();
  } catch (e) { alert('Close error: ' + e.message); }
}


/* ── Boot ───────────────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', rv2Load);
