## ORATS Earnings Implied-Move Breach Web App

Small FastAPI + plain HTML app that:
- Accepts a US equity ticker
- Computes over the last 20 earnings (~5 years):
  - **Breach rate (%)**
  - **Average “above breach %”** (conditional on breach)
- **V2**: **Quarter Seasonality** breakdown (Q1–Q4) with per-quarter breach/near-breach metrics + a recommendation label
- **V2.1**: **Seasonality Score** per quarter (deltas vs baseline earnings behavior)
- Displays a detailed per-earnings table with implied vs realized moves and breach flags

### Architecture
- **Backend**: FastAPI
  - `GET /api/breach?ticker=XYZ&n=20&years=5&k=1.0`
  - Engine 2 (SPX weekly IC, separate): `GET /api/spx-ic?...` (feature-flagged)
  - ORATS token is read from **env var `ORATS_TOKEN`** (never sent to the browser)
  - Caching:
    - ORATS raw responses cached in-memory (TTL 6h)
    - `/api/breach` responses cached in-memory (TTL 6h)
    - `/api/spx-ic` responses cached in-memory (TTL 30m)
- **Frontend**: `static/index.html` + minimal JS/CSS, served by FastAPI
  - Engine 2 page: `/spx` (served from `static/spx.html`)

### Setup

1) Create a `.env` locally (this repo includes `env.example` as a template):

```bash
cp env.example .env
```

Edit `.env` and set:
- `ORATS_TOKEN=...` (required)
- `PORT=8000` (optional)

2) Create a venv + install deps:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

### Run

```bash
source .venv/bin/activate
PORT=${PORT:-8000}
uvicorn backend.app:app --host 0.0.0.0 --port "$PORT" --reload
```

Then open:
- `http://localhost:8000`

### Engine 2: SPX Weekly Iron Condor (risk-only)
Engine 2 is a separate “risk map” engine for weekly short SPX iron condors.

It does **not** try to predict returns or optimize PnL. It answers:
- “In this regime/macro/seasonality bucket, what IC geometry is least dangerous?”

Data sources:
- **ORATS**: EOD + daily OHLC for SPX (or SPY proxy) and sector ETFs
- **Benzinga**: Economic Calendar proximity and event flags (CPI/FOMC/NFP/OpEx/etc)
- **ORATS Live (optional, current-only)**: strike-level Greeks + dealer gamma context (informational only; does not change historical odds)

Enable in `.env`:
- `ENABLE_ENGINE2_SPX_IC=1`
 
Optional (current-only overlays):
- ORATS Live outputs use a short TTL cache and are **informational only**.
- Dealer gamma is computed from live strike gamma concentration near spot (±5%), weighted by OI when available.

Open:
- `http://localhost:8000/spx`

Definitions:
- **EM (1σ)**: Expected move from ORATS ATM implied vol for the holding horizon.
- **Breach**: Expiry close outside the short strikes (risk-only).
- **Outside wings**: Expiry close beyond the long strikes.
- **MAE (pts)**: Max adverse excursion using daily high/low over the holding window.
- **Bayesian smoothing**: probabilities use a Beta(1,1) prior for sparse bins.

### API usage

Example:

```bash
curl "http://localhost:8000/api/breach?ticker=AAPL&n=20&years=5&k=1.0"
```

Engine 2 example:

```bash
curl "http://localhost:8000/api/spx-ic?entry_day=mon&years=3&seasonality_mode=quarter&risk_target_breach_pct=25&weeks_limit=120"
```

The response matches the JSON contract in `ORATS_Earnings_EM_Breach_Spec.txt`.

### Quarter Seasonality (V2)
The `/api/breach` response now includes a top-level `quarters` object with keys `Q1..Q4`, computed from the same filtered event set.

Per quarter we expose:
- breach stats (at the request’s `k`)
- near-breach rates at thresholds **0.8** and **0.9** based on \( \text{ratio} = \frac{\text{realizedMovePct}}{\text{impliedMovePct}} \)
- a simple **recommendation** label: `Tight` / `Standard` / `Wide` / `Avoid`

Note: recommendation uses a heuristic that evaluates **breach rate at k=1.0 internally** (so it stays comparable even if you change `k` in the request).

### Seasonality Score (V2.1)
The response also includes:
- a top-level `baseline` object (computed over the same usable event set)
- `quarters[Qx].seasonality` with deltas vs baseline:
  - `breach_delta_pp`
  - `ratio_delta`
  - `overshoot_delta_pp`
  - `z_breach`

Low sample handling:
- If `events_used < 3` for a quarter, `seasonality` fields are `null` and the recommendation is **`Avoid (low sample)`**.

### Tests

Tests are mocked (no ORATS calls) and cover:
- trading-day probing helper
- a small end-to-end breach + quarter aggregation calculation with mocked ORATS responses

Run:

```bash
pytest -q
```


