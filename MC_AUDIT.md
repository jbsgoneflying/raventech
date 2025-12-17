# MC_AUDIT.md — Monte Carlo Earnings Gap Risk (Audit-Safe Design)

Date: 2025-12-17  
Owner: Quant Engineering  
Scope: Add **empirical Monte Carlo** earnings gap risk modeling to this repo **without changing any default behavior** (all MC outputs are **flag-gated** and **additive-only**).

---

## Architecture Map (routes → compute layers → overlays → decision logic → UI)

### Text diagram (source of truth)

- **Route**: `GET /api/breach` in `backend/app.py`
  - Owns: singleton `OratsClient`, payload caching, and static file serving
- **Core compute**: `backend/earnings_logic.py::compute_breach_stats`
  - Owns: event normalization (AMC/BMO), implied vs realized move, breach stats, quarter seasonality, and payload assembly
- **Overlays** (called by `compute_breach_stats`, must remain no-lookahead):
  - `backend/regime_overlay.py`
    - `compute_regime_backtest_view` attaches `events[].regimeAtEvent` **as-of `pricingDateUsed`** (no lookahead)
    - `compute_regime_overlay` returns current regime label, tailMultiplier, tradeGate guidance
  - `backend/skew_overlay.py` (degraded scaffold; safe default)
  - `backend/wing_recommendation.py` computes TAS and wing multipliers (heuristic baseline)
- **Optional structure builder**: `backend/trade_builder.py` (chain-based strikes or safe fallback)
- **UI**: `static/index.html` + `static/app.js`
  - Reads stable payload keys and renders Summary / Regime / Wings / Quarter Seasonality / Earnings table

### Mermaid call graph

```mermaid
flowchart TD
  ui[static/index.html+static/app.js] -->|GET_/api/breach| api[backend/app.py]

  api -->|cache(6h)| breachCache[_breach_cache_TTLCache]
  api --> compute[earnings_logic.compute_breach_stats]

  compute --> earnings[OratsClient.hist_earnings]
  compute --> dailies[OratsClient.hist_dailies]
  compute --> cores[OratsClient.hist_cores]

  compute --> regimeBacktest[regime_overlay.compute_regime_backtest_view]
  compute --> regimeNow[regime_overlay.compute_regime_overlay]
  compute --> skew[skew_overlay.compute_skew_overlay]
  compute --> wings[wing_recommendation.compute_wing_recommendation]
  compute --> tradeBuilder[trade_builder.compute_trade_builder_optional]

  compute -->|flagged_additive| mc[mc_simulator.run_mc_additive]
```

---

## Data contracts (non-negotiable)

### Default behavior unchanged

- When MC flags are **OFF**:
  - **No new fields** are added to payload
  - Existing fields and UX remain unchanged
  - Golden payload regression tests must remain byte-for-byte identical

### Additive-only API extensions (when enabled)

New top-level keys (only when enabled):

- `nextEvent` (explicit anchoring for “the trade we’re about to do”)
- `monteCarlo` (structure-aware gap risk metrics)
- `monteCarloOptimization` (risk-only optimizer now; credit-aware later)
- `stability` (bootstrap stability for TAS and asymmetry caps)

---

## Event-time alignment points (no lookahead allowed)

This repo already has explicit event anchoring points; MC must reuse them:

- **Earnings timing normalization**: `classify_timing(anncTod)` produces AMC/BMO
- **Realized move window**:
  - AMC: close(earnDate) → open(next trading day)
  - BMO: close(prior trading day) → open(earnDate)
- **Implied move anchoring for historical events**:
  - Use `impErnMv` from `hist/cores` on `pricingDateUsed` (bounded backoff)
- **Regime-at-event (no lookahead)**:
  - `regimeAtEvent` must be computed **as-of `pricingDateUsed`** using trailing windows only

---

## Quant risks (loss-first failure modes)

### Data / alignment risks

- **AMC/BMO misclassification** shifts realized window by ~1 day → wrong `signedMovePct` and wrong shock pool
- **Date substitutions** (missing dailies open/close; missing cores) bias realized vs implied and can silently thin the pool
- **Unit errors** in `impErnMv` (decimal vs percent conventions) create 10×–100× scaling mistakes
- **Upcoming-event implied mismatch**: “latest cores row” is not necessarily the earnings pricing date; MC must anchor implied move to the upcoming event’s pricing date explicitly

### Modeling risks

- **Non-stationarity**: earnings shock distribution differs across regimes, quarters, cycles
- **Conditioning sparsity**: quarter×regime×gate can collapse sample size and create fake precision
- **Optimization overfit**: grid-searching multipliers can “discover” spurious asymmetry without stability gating

### Engineering risks

- **Cache mixing across methodologies** if flags are not included in cache keys
- **Non-deterministic MC** breaks CI and desk trust if not seeded and cached
- **Latency blow-ups** if MC runs are not cached and bounded

---

## Monte Carlo integration points (safe seams)

### 1) Shock pool builder (after regimeAtEvent is attached)

Use existing per-event fields already computed without lookahead:

- Canonical variable:
  - \( S = \\frac{\\text{signedMovePct}}{\\text{impliedMovePct}} \\)
  - dimensionless “earnings shock standardized by implied”

Per event store minimal shock row:

- `earnDate`, `pricingDateUsed`
- `impliedMovePct`, `signedMovePct`
- `quarterKey`
- `regimeLabel` and `tradeGate` from `regimeAtEvent`

Hard guardrail:

- **Exclude** events where `impliedMovePct < MC_MIN_IMPLIED_MOVE_PCT` (default 0.5%) to prevent blow-ups
- Record `excludedCounts` and reasons

### 2) Upcoming trade anchor (`nextEvent`)

Add (when MC enabled):

- `nextEvent.pricingDatePlanned` and `nextEvent.impliedMovePctPlanned`

Rationale:

- Traders must know the simulation is anchored to the **earnings pricing date** (AMC: earnDate close; BMO: prior close)

### 3) Structure-aware risk metrics (IC)

Compute risk for:

- Strike-based IC (preferred): `tradeBuilder` has short/long strikes
- Distance-based estimated IC (fallback): derive strikes from spot, implied move, wing multipliers, and default width

Metrics (gap-at-open only):

- `breachProb.{put,call,either}`
- `expectedLoss.{put,call,total}` (intrinsic at open)
- `cvar95.{put,call,total}` (expected shortfall at 95%)

### 4) Wing optimization (compare only; do not replace heuristics)

Two modes:

- **Risk-only optimizer** (now): optimize tail risk subject to breach probability constraints (no credit model)
- **Credit-aware optimizer** (later): only when chain-based `totalCredit` is present and marked high quality

### 5) Stability → asymmetry caps

Bootstrap resample events, recompute TAS repeatedly, and cap asymmetry based on sign stability:

- sign agreement < 65% ⇒ symmetric
- 65–80% ⇒ cap asymmetry to 10–15%
- ≥ 80% ⇒ allow full cap

---

## Determinism and caching (audit-safe)

### Deterministic seed

Derive seed from a stable hash of:

- ticker
- asOfDate (pricing snapshot)
- `nextEvent.impliedMovePctPlanned`
- structure key (strikes or distances)
- conditioning key (requested + used)
- nSims, global seed
- **shockPoolKey** (hash of minimal shock rows only)

### Cache keys

- **Shock pool key**: hash of minimal shock rows (not full event dicts)
- **MC result key**: includes flags fingerprint so methodologies never mix

---

## UX requirements (desk-first)

Add a collapsible UI section:

- **Simulated Earnings Risk (MC)**
  - Breach probability (put/call/either)
  - Tail risk (CVaR95)
  - Wing optimization (heuristic vs MC-optimal)
  - Note: **“Simulates close→open earnings gap only (no intraday path).”**


