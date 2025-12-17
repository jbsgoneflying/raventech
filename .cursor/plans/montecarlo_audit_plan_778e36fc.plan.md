---
name: MonteCarlo_Audit_Plan
overview: Produce a repo-specific architecture map and an audit-safe integration design for an empirical earnings-gap Monte Carlo model, preserving current outputs unless MC flags are enabled. Includes proposed flags, cache keys, and loss-risk failure modes; no code changes yet.
todos:
  - id: mc-audit-md
    content: Create `MC_AUDIT.md` (repo-specific) with architecture diagram, data contracts, caching boundaries, no-lookahead alignment points, quant failure modes, and MC integration seams.
    status: completed
  - id: mc-flags-cachekeys
    content: Finalize MC-related feature flag names and define deterministic cache key/seed scheme that includes flags and shock-pool fingerprint.
    status: completed
    dependencies:
      - mc-audit-md
  - id: mc-phase1-implementation-plan
    content: After approval, implement `backend/mc_simulator.py` and additive payload fields behind flags, plus deterministic tests and UI section.
    status: completed
    dependencies:
      - mc-flags-cachekeys
---

# Phase0_MC_AUDIT_and_Integration_Plan

## What I scanned (repo reality)

- Backend entry + caching: [`/Users/joshuasmith/Desktop/Breach-Algo/backend/app.py`](/Users/joshuasmith/Desktop/Breach-Algo/backend/app.py)
- Core payload constructor: [`/Users/joshuasmith/Desktop/Breach-Algo/backend/earnings_logic.py`](/Users/joshuasmith/Desktop/Breach-Algo/backend/earnings_logic.py)
- Overlays: [`/Users/joshuasmith/Desktop/Breach-Algo/backend/regime_overlay.py`](/Users/joshuasmith/Desktop/Breach-Algo/backend/regime_overlay.py), [`/Users/joshuasmith/Desktop/Breach-Algo/backend/skew_overlay.py`](/Users/joshuasmith/Desktop/Breach-Algo/backend/skew_overlay.py)
- Wing logic: [`/Users/joshuasmith/Desktop/Breach-Algo/backend/wing_recommendation.py`](/Users/joshuasmith/Desktop/Breach-Algo/backend/wing_recommendation.py)
- Trade structure (chain + distance fallback): [`/Users/joshuasmith/Desktop/Breach-Algo/backend/trade_builder.py`](/Users/joshuasmith/Desktop/Breach-Algo/backend/trade_builder.py)
- Frontend contract: [`/Users/joshuasmith/Desktop/Breach-Algo/static/app.js`](/Users/joshuasmith/Desktop/Breach-Algo/static/app.js)
- Deterministic golden snapshots: [`/Users/joshuasmith/Desktop/Breach-Algo/tests/test_golden_payloads_snapshot.py`](/Users/joshuasmith/Desktop/Breach-Algo/tests/test_golden_payloads_snapshot.py) and fixtures under [`/Users/joshuasmith/Desktop/Breach-Algo/tests/fixtures/golden/`](/Users/joshuasmith/Desktop/Breach-Algo/tests/fixtures/golden/)

## Architecture map (routes → compute → overlays → decision → UI)

```mermaid
flowchart TD
  ui[static/index.html+static/app.js] -->|GET /api/breach| api[backend/app.py]

  api -->|cache key: (ticker,n,years,k,flags)| cache[TTLCache 6h: _breach_cache]
  api -->|miss or tradeBuilder params| compute[earnings_logic.compute_breach_stats]

  compute --> earnings[OratsClient.hist_earnings]
  compute --> dailies[OratsClient.hist_dailies close/open probing]
  compute --> cores[OratsClient.hist_cores impErnMv pricingDateUsed retries]

  compute --> regimeBacktest[regime_overlay.compute_regime_backtest_view]
  compute --> regimeNow[regime_overlay.compute_regime_overlay]
  compute --> skew[skew_overlay.compute_skew_overlay]
  compute --> wings[wing_recommendation.compute_wing_recommendation]
  compute --> tb[trade_builder.compute_trade_builder optional]

  subgraph caches[Caching boundaries]
    api
    cache
    oratsCache[OratsClient TTLCache 6h]
    regimeCache[regime_overlay TTLCache (2h current, 24h as-of)]
    skewCache[skew_overlay TTLCache 6h]
  end

  dailies --> oratsCache
  cores --> oratsCache
  earnings --> oratsCache
  regimeBacktest --> regimeCache
  regimeNow --> regimeCache
  skew --> skewCache
```

### Where “event-time alignment” happens (critical no-lookahead points)

- **Timing normalization**: `classify_timing(anncTod)` in `backend/earnings_logic.py`
- **Realized window anchors**:
  - AMC: `close(earnDate)` → `open(next trading day)`
  - BMO: `close(prior trading day)` → `open(earnDate)`
- **Implied move anchor**: `impErnMv` from `hist/cores` on `pricingDateUsed` (with bounded backoff)
- **Regime at event**: `regime_overlay.compute_regime_backtest_view()` attaches `events[].regimeAtEvent` **as-of `pricingDateUsed`** (explicit no-lookahead intent + tests)

## Data contracts (what must remain stable when MC flags are OFF)

### API: `/api/breach` payload shape (existing)

- Top-level keys currently used by UI/tests:
  - `ticker`, `params`, `summary`, `baseline`, `current`, `regime`, `regimeValidation`, `quarters`, `events`, `skipped`, `wingRecommendation`, `skewOverlay`, optional `tradeBuilder`, optional `tradeBuilderInputs`
- Per-event keys used by UI:
  - `earnDate`, `timing`, `pricingDateUsed`, `impliedMovePct`, `signedMovePct`, `breach`, `breachSide`, overshoot fields, `regimeAtEvent`, plus telemetry (`pricingDateShiftDays`, `realizedWindowShiftDays`) and `notes`

### UI binding points (important so we don’t break UX)

- `static/app.js` reads:
  - `payload.summary.*`, `payload.regime.*`, `payload.regimeValidation.*`, `payload.wingRecommendation.*`, `payload.tradeBuilder.*`, `payload.events[]`, `payload.quarters.Qx.*`
- UI already tolerates additive keys (it ignores unknown fields).

## Loss-risk failure modes (what can go wrong, desk-first)

### Data / alignment risks

- **Timing misclassification (AMC vs BMO)** → wrong close/open window → biased `signedMovePct` and `S` pool.
- **Date substitution** (missing dailies open/close or missing cores impErnMv) → event features come from shifted dates; if not surfaced, traders can’t diagnose “bad names.”
- **Unit errors** for `impErnMv` (decimal vs percent) → catastrophic strike distances and MC scaling.
- **“Today implied” mismatch vs the event being traded**: using a generic latest cores snapshot can anchor MC to the wrong date for the upcoming earnings pricing date (AMC earnDate close vs BMO prior close).

### Modeling risks

- **Non-stationarity**: earnings shock distribution changes across regimes, product cycles, and market structure; naive pooling can understate tails.
- **Conditioning sparsity**: conditioning on quarter×regime×gate can leave 1–3 events → false precision.
- **Overfitting via optimization**: wing grid-search can “discover” spurious asymmetry unless constrained + stability-gated.

### Engineering / production risks

- **Cache mixing across methodologies**: if MC flags/conditioning aren’t in cache keys, traders see drifting numbers.
- **Non-deterministic Monte Carlo** in CI: unseeded RNG or Python hash-based seeding breaks golden snapshots.
- **Latency blow-ups**: MC run per request without caching, or with too-large `nSims`, will make UI unusable.

## Monte Carlo integration points (additive + flag-gated)

### Integration seam 1 (lowest risk): after `events[]` are built and `regimeAtEvent` is attached

- Rationale: by this point we have the canonical empirical sample per event:
  - `signedMovePct`, `impliedMovePct`, `timing`, `pricingDateUsed`, `earnDate`, `quarter`, `regimeAtEvent.{label,tradeGate}`, plus breach-side/overshoot.
- This is the safest point to build the standardized shock variable:
  - **S = signedMovePct / impliedMovePct** (dimensionless)

### Integration seam 2 (structure-aware): after `tradeBuilder` is computed (if present)

- Rationale: to compute breach probability for a specific IC we need **short strikes** (and for tail loss we need long strikes and optionally credit).
- Sources of structure in this repo:
  - If chain is available: `payload.tradeBuilder.put/call.{shortStrike,longStrike,credit}`
  - If chain is not available: UI has distance-based targets using `wingRecommendation` multipliers; backend can mirror this to form an “estimated structure” (flagged and clearly labeled).

### Integration seam 3 (decision tightening): do not replace heuristics

- Add a parallel section:
  - `monteCarlo` risk metrics
  - `monteCarloOptimization` suggesting a better asymmetry than the heuristic
  - `stability` for TAS confidence
- Heuristic outputs remain the default and remain present.

## Explicit “next earnings” anchoring (required for MC inputs)

Add an additive payload block (when MC is enabled) to make the upcoming earnings anchor explicit and auditable:

- `nextEvent: {`
  - `earnDateNext`
  - `timingPlanned` (AMC/BMO/UNK)
  - `pricingDatePlanned` (the date that should anchor `impErnMv` for the *upcoming* event)
  - `impliedMovePctPlanned`
  - `impliedMoveSource` (e.g., `cores_on_pricingDate`, `cores_fallback_prior`, `current_snapshot_fallback`)
  - `notes`
- `}`

Rules (trading-first, no lookahead):

- Determine `earnDateNext` from ORATS `hist/earnings` as the nearest event date \(\ge\) “today” (pinned `today` in tests).
- Determine `timingPlanned` from `anncTod` using existing `classify_timing`.
- Compute `pricingDatePlanned` per spec (AMC: earnDate, BMO: prior trading day to earnDate).
- Compute `impliedMovePctPlanned` by attempting `hist/cores` on `pricingDatePlanned` (bounded backoff to prior trading day, and record the fallback as `impliedMoveSource` + `notes`). This is explicitly *not* a generic “latest cores row” field.

## Draft: MC_AUDIT.md (to be added after approval)

### Architecture diagram (text)

- **Route**: `GET /api/breach` (`backend/app.py`)
- **Core payload**: `compute_breach_stats` (`backend/earnings_logic.py`)
- **Event normalization**: `classify_timing` + close/open anchoring
- **ORATS data**: `hist/earnings`, `hist/dailies`, `hist/cores` (+ optional `hist/strikes`, `hist/monies/implied`)
- **Overlays**:
  - `regime_overlay.compute_regime_backtest_view` attaches `regimeAtEvent` using `pricingDateUsed` (no-lookahead)
  - `regime_overlay.compute_regime_overlay` gives current regime + `tailMultiplier` + `tradeGate`
  - `skew_overlay.compute_skew_overlay` (degraded scaffold)
- **Decision**: `wing_recommendation.compute_wing_recommendation`
- **UI**: `static/app.js` renders payload; additive keys are safe

### Monte Carlo: proposed modules + responsibilities

- New module: `backend/mc_simulator.py`
  - Build empirical shock pool from `events[]` (no lookahead)
  - Run deterministic bootstrap MC for a given structure
  - Provide structure-aware metrics and (optional) wing optimization
  - Provide bootstrap stability metrics for TAS sign

### Canonical simulation variable (per event)

- **S_signed = signedMovePct / impliedMovePct**
- Store per event (only if usable):
  - `earnDate`, `pricingDateUsed`, `quarterKey`
  - `S_signed`, `S_abs = abs(S_signed)`
  - `regimeAtEvent.label`, `regimeAtEvent.tradeGate`
  - `breachSide` and overshoot fields
  - **Exclude implied≈0 events**: do not include events where `impliedMovePct < MC_MIN_IMPLIED_MOVE_PCT` (default 0.5%). Record `excludedCounts` by reason so traders see when the pool is being thinned.

### Sampling strategy

- Bootstrap resampling of historical `S_signed`
- Optional conditioning (all must be no-lookahead):
  - by quarter (`Q1..Q4`)
  - by regime label (`Calm/Normal/Elevated/Stress`) from `regimeAtEvent`
  - by trade gate (`OK/CAUTION/NO_TRADE`) from `regimeAtEvent`
- Optional recency weighting by event index (newest gets more weight) with event-count half-life
- Always fail-safe:
  - if conditioned pool size < `minPool`, fall back to a less-conditioned pool and emit `conditioningUsed` + `notes`

Conditioning combinatorics guardrail (strict hierarchy):

- Try in order:
  - `quarter+regime+gate`
  - `quarter+regime`
  - `regime`
  - `unconditioned`
- Always emit `conditioningRequested` and `conditioningUsed`.

### Simulation mechanics (gap-at-open only)

- For a new trade with spot `P_close` and **event-anchored** `nextEvent.impliedMovePctPlanned`:
  - sample `S_signed`
  - `move_sim_pct = S_signed * impliedMovePctPlanned`
  - `P_open_sim = P_close * (1 + move_sim_pct/100)`
- Determinism:
  - seed RNG from a **stable hash** of (ticker, asOfDate, impliedMovePctPlanned, structure key, conditioning key, nSims, globalSeed, shockPoolHash)

UI/UX note (to avoid desk misinterpretation):

- Add a visible note in the MC section: **“Simulates close→open earnings gap only (no intraday path).”**

### Structure-aware risk metrics (IC)

Expose additive field when enabled:

- `monteCarlo: {

nSims,

seed,

pool: { sizeUsed, conditioningRequested, conditioningUsed, minPool, recencyWeighting },

breachProb: { put, call, either },

expectedLoss: { put, call, total },

cvar95: { put, call, total },

notes

}`

Definitions (gap at open):

- Put side breach: `P_open_sim <= K_putShort`
- Call side breach: `P_open_sim >= K_callShort`
- Loss at open (gross intrinsic; net-of-credit optionally if credit present):
  - Put spread value: `max(0, K_putShort - P_open) - max(0, K_putLong - P_open)`
  - Call spread value: `max(0, P_open - K_callShort) - max(0, P_open - K_callLong)`

### Wing optimization (compare, don’t replace)

Optimization modes must be explicit:

- **Risk-only optimizer (available now)**:
  - grid search around heuristic `putWingMultiple` / `callWingMultiple`
  - objective: minimize tail risk (e.g., `CVaR95_total`) subject to breach probability constraints (or vice versa)
  - no credit term unless we have real chain credit
- **Credit-aware optimizer (later / gated)**:
  - only enabled when `tradeBuilder` has chain-based strikes *and* `totalCredit` is present with a quality flag
  - objective: maximize `credit / tailRisk`

Guardrail: if chain credit is absent, expose `monteCarloOptimization.mode="RISK_ONLY"` and state the limitation in `notes`.

- Expose additive:
  - `monteCarloOptimization: {

optimalPutMultiple,

optimalCallMultiple,

constraints: { maxBreachProbEither, maxCvar95Total },

improvementVsHeuristic,

notes

}`

### Confidence = stability (bootstrap TAS)

- Bootstrap resample events (e.g., 500x), recompute TAS, measure:
  - `tasSignAgreementPct`
  - `tasStd`
  - `confidenceDerived` (HIGH/MED/LOW)

Stability must directly cap asymmetry (concrete rules):

- if `tasSignAgreementPct < 65` ⇒ force symmetric multipliers (adj=0)
- if `65 <= tasSignAgreementPct < 80` ⇒ cap asymmetry to 10–15% (configurable)
- if `tasSignAgreementPct >= 80` ⇒ allow full existing cap

## Proposed feature-flag names (env → `FeatureFlags`, default OFF)

Add to `backend/config.py` `FeatureFlags` (all default OFF unless noted):

- `ENABLE_MONTE_CARLO_EARNINGS` (OFF)
- `MC_ENABLE_CONDITION_ON_QUARTER` (OFF)
- `MC_ENABLE_CONDITION_ON_REGIME` (OFF)
- `MC_ENABLE_CONDITION_ON_TRADE_GATE` (OFF)
- `MC_ENABLE_RECENCY_WEIGHTING` (OFF)
- `MC_ENABLE_WING_OPTIMIZATION` (OFF)
- `MC_ENABLE_TAS_STABILITY` (OFF)
- `MC_N_SIMS` (default 5000; still OFF unless `ENABLE_MONTE_CARLO_EARNINGS`)
- `MC_BOOTSTRAP_N` (default 500 for TAS stability)
- `MC_GLOBAL_SEED` (default 1337)
- `MC_MIN_POOL` (default 12)
- `MC_OPT_MAX_MULT_DELTA` (default 0.50; grid range)
- `MC_OPT_STEP` (default 0.05)
- `MC_MAX_BREACH_EITHER_PCT` (default 25.0)
- `MC_MAX_CVAR95_TOTAL` (default: None unless you want a hard budget)

All these must be included in `FeatureFlags.cache_key()` so `backend/app.py`’s `_breach_cache_key()` remains methodology-safe.

## Proposed Monte Carlo cache keys (deterministic + flag-safe)

### 1) Shock pool cache key (derived from payload events)

- `shockPoolKey = sha256(json(sorted minimal shock rows))`
  - **Do not hash the full event dicts** (avoid churn when unrelated metadata fields are added).
  - Hash only the minimal stable representation:
    - `earnDate` (or stable event id)
    - `pricingDateUsed`
    - `impliedMovePct`
    - `signedMovePct`
    - `quarterKey`
    - `regimeLabel` (from `regimeAtEvent.label`)
    - `tradeGate` (from `regimeAtEvent.tradeGate`)
  - plus `flags.cache_fingerprint()` and `(ticker,n,years,k)` because the sampled event set depends on these.

### 2) Simulation result cache key (structure-aware)

- `mcKey = (

"mc",

ticker,

current.asOfDate,

current.stockPrice,

nextEvent.impliedMovePctPlanned,

structureKey,  # strikes or distances + wing_width + credit(optional)

conditioningKey,

nSims,

globalSeed,

shockPoolKey,

flags.cache_fingerprint(),

)`

### 3) Deterministic seed derivation

- `seed = int.from_bytes(sha256(repr(mcKey)).digest()[:8], "big") XOR globalSeed`
  - avoids Python’s randomized `hash()`

## Planned Phase1+ implementation steps (after your approval)

- Add `backend/mc_simulator.py` with:
  - shock pool builder from `events[]`
  - deterministic RNG and caching
  - IC risk metric computation
  - optional conditioning + fail-safe fallback notes
- Extend `compute_breach_stats` to append additive fields when flags enabled:
  - `monteCarlo`, `monteCarloOptimization`, `stability`
- Extend `static/index.html` + `static/app.js` to add a collapsible “Simulated Earnings Risk (MC)” section (only renders when payload has `monteCarlo`)
- Add tests:
  - MC flags OFF ⇒ golden payload byte-for-byte identical
  - MC flags ON ⇒ deterministic “MC smoke fixtures” per ticker:
    - assert reproducibility of `seed`
    - assert stable key metrics (e.g., `breachProb.either`, `cvar95.total`) within tight tolerances
    - do **not** require full-payload byte equality under MC (avoid brittle churn)
  - No-lookahead: conditioning uses `regimeAtEvent` and `pricingDateUsed` only