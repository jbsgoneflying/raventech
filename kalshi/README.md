# Kalshi Flow Monitor

Real-time unusual activity detection for prediction markets. Connects to Kalshi's public data feeds, computes anomaly scores per market, and surfaces high-conviction late/large/aggressive flow through a live dashboard.

## Architecture

```
┌─────────────┐     ┌─────────────────────────────────────────────┐
│  Kalshi API  │────▶│  Backend (Node.js + Express + TypeScript)   │
│  REST + WS   │     │                                             │
└─────────────┘     │  ┌──────────┐ ┌──────────┐ ┌────────────┐  │
                    │  │ Ingestion│→│ Features │→│ Alert Engine│  │
                    │  └──────────┘ └──────────┘ └─────┬──────┘  │
                    │        ↕            ↕             ↓         │
                    │  ┌──────────┐ ┌──────────┐  ┌─────────┐    │
                    │  │ Postgres │ │  Redis   │  │   SSE   │    │
                    │  └──────────┘ └──────────┘  └────┬────┘    │
                    └──────────────────────────────────┬──────────┘
                                                       ↓
                    ┌──────────────────────────────────────────────┐
                    │  Frontend (Next.js + Tailwind)               │
                    │  /alerts  — Live alert dashboard              │
                    │  /market/[ticker]  — Drill-down view          │
                    └──────────────────────────────────────────────┘
```

## Quick Start

### 1. Prerequisites

- Node.js 20+
- Docker & Docker Compose
- Kalshi API key + RSA private key (optional but recommended)

### 2. Setup

```bash
cd kalshi

# Copy env and configure
cp .env.example .env
# Edit .env — set KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY_PATH

# Start Postgres + Redis
docker compose up postgres redis -d

# Install dependencies
npm install

# Run database migrations
npm run db:migrate

# Start dev servers (API + Web)
npm run dev
```

- API: http://localhost:3100
- Dashboard: http://localhost:3000
- Health check: http://localhost:3100/health

### 3. Full Docker

```bash
docker compose up --build
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check + ingestion stats |
| GET | `/api/alerts` | Paginated alerts (filters: `min_score`, `alert_type`, `market_ticker`) |
| GET | `/api/alerts/stream` | SSE real-time alert stream |
| GET | `/api/markets` | List monitored markets (search, status filter) |
| GET | `/api/markets/:ticker` | Market detail + trades + book + alerts |
| GET | `/api/markets/:ticker/trades` | Recent trades for market |
| GET | `/api/config` | Current scoring weights + presets |
| PUT | `/api/config` | Update scoring weights |
| GET | `/api/config/stats` | Ingestion metrics |

## Anomaly Scoring

Each trade is scored on a **[0, 100]** scale from 5 weighted components:

| Component | Weight | What it measures |
|-----------|--------|------------------|
| **Size** | 0.25 | Z-score of trade quantity vs 60m rolling baseline (MAD-based) |
| **Late** | 0.20 | Sigmoid function peaking as market close approaches |
| **Impact** | 0.20 | Price movement in the 10s after the trade |
| **Liquidity** | 0.20 | Depth ratio + fraction of top book levels consumed |
| **Persistence** | 0.15 | Directional flow imbalance + novelty of large prints |

### Alert Types

- **LARGE_LATE_PRINT** — Big trade near market close
- **LIQUIDITY_SWEEP** — Single trade consuming multiple book levels
- **FAST_PRICE_IMPACT** — Trade causing rapid price movement
- **SUSTAINED_IMBALANCE** — Persistent one-sided aggressive flow

### Tuning

Weights are configurable via:
1. Environment variables (`WEIGHT_SIZE`, `WEIGHT_LATE`, etc.)
2. `PUT /api/config` with custom weights or preset name
3. Presets: `balanced` (default), `size_hunter`, `late_flow`, `impact_focused`

### Alert Gating

- **Cooldown**: Max 1 alert per market per 2 minutes (unless score jumps by 15+)
- **Min liquidity**: Markets with `open_interest < 50` are filtered out

## Replay Mode

Replay historical events to validate scoring:

```bash
npm run replay -- --from "2026-02-13T00:00:00Z" --to "2026-02-14T00:00:00Z" --speed 5 --json
```

## Kalshi API Access

| Feature | Without API Key | With API Key |
|---------|----------------|--------------|
| Market discovery | Yes | Yes |
| Trade stream | Yes (public WS) | Yes |
| Ticker updates | Yes (public WS) | Yes |
| Orderbook depth | No | Yes |
| Sweep/depth features | Disabled | Active |

## Project Structure

```
kalshi/
  packages/shared/     # Zod schemas + scoring functions (shared FE/BE)
  apps/api/            # Node.js backend (Express + WS client)
  apps/web/            # Next.js dashboard
  docker-compose.yml   # Postgres + Redis + API + Web
```

## Extending to Other Exchanges

The architecture is exchange-agnostic. Each table has an `exchange` column, and the shared schemas include exchange discriminators. To add Polymarket or another source, implement a new ingestion adapter in `apps/api/src/` — the scoring pipeline and dashboard work unchanged.
