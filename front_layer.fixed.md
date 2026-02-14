# Raven Tech Front Layer Build
## Market Intelligence + Pre Open Roadmap System

### Objective
Build a deterministic, production ready front layer that synthesizes all existing Raven Tech engines plus new cross asset and narrative inputs into a daily and weekly desk usable roadmap.

This system must:
- Be read only and non trading
- Never generate direct buy or sell instructions
- Operate as a synthesis, compression, and early warning layer
- Be auditable, explainable, and stable
- Run automatically pre open and weekly
- Be usable in under 3 minutes by the trading desk

---

## Guiding Principles

1. Deterministic engines remain the source of truth
2. LLMs are downstream only and never generate signals
3. All outputs must cite which engine or field produced the insight
4. The system must explicitly say when to stand down
5. Everything resolves to clear conditionals, not predictions

---

## Existing Systems (Already Built)

### Deterministic Engines
- Engine 1: Earnings Breach and Structure
- Engine 2: Index Income and Dealer Gamma (SPX, SPY, QQQ)
- Engine 3: Red Dog Reversal
- Engine 4: Ichimoku Continuation
- Engine 5: Global Lead Lag and Regime
- Compare Engine
- News Risk Calendar
- Command Center
- Flow Pressure
- Weekly Signal Sequencer
- Pattern Library

These systems must NOT be modified in logic. Only structured outputs may be extended.

---

## New Systems To Build

### 1) DailyMarketState (Core Object)

Create a single canonical object written once per day.

Run time:
- Daily at 03:55 EST
- Weekly snapshot Sunday at 18:00 EST

Storage:
- Persisted snapshot with timestamp
- Retain rolling history minimum 120 days

Schema (example):
```json
{
  "date": "YYYY-MM-DD",
  "regime": {
    "state": "Risk-On | Transitional | Risk-Off | Stressed",
    "score": number,
    "drivers": ["FX", "Commodities", "Vol", "Macro"]
  },
  "flow_pressure": {
    "score": number,
    "state": "Risk-On | Neutral | Risk-Off"
  },
  "vol_state": {
    "level": number,
    "term_structure": "contango | flat | backwardation",
    "skew": "low | neutral | elevated"
  },
  "engine_gates": {
    "earnings": "allowed | selective | suppressed",
    "red_dog": "allowed | watch | suppressed",
    "ichimoku": "allowed | selective | suppressed",
    "index_income": "allowed | reduced | suppressed"
  },
  "earnings_candidates": [
    {
      "ticker": "XYZ",
      "score": number,
      "dealer_gamma": "supportive | neutral | hostile",
      "expected_move_ratio": number,
      "regime_fit": true
    }
  ],
  "index_state": {
    "SPX": {},
    "SPY": {},
    "QQQ": {}
  },
  "news_risk": {
    "today": "low | medium | high",
    "week_ahead": ["event1", "event2"]
  }
}
```

This object is the ONLY thing the LLM is allowed to read.

### 2) Cross Asset Stress Module (New)
Add structured ingestion for:

### FX
- DXY
- JPY
- CHF
- EM FX basket

### Commodities
- Crude Oil
- Copper
- Gold
- Silver

### Crypto
- BTC
- ETH
- BTC vs ETH ratio

### Volatility
- VIX spot
- VIX term slope
- VIX skew
- Front vs back month divergence

Each market produces:

Direction

Stress score

Change vs prior day

Confirmation or divergence vs equities

Outputs feed into DailyMarketState only.

### 3) News Theme Intelligence
Sources:

EODHD News

Benzinga

Do NOT store raw headlines in LLM context.

Instead build:

Theme clusters

Keyword sets per theme

Daily intensity score

Persistence score across rolling days

Acceleration score

Example themes:

AI displacement

Labor stress

Credit stress

Geopolitical escalation

Regulation pressure

Liquidity shock

Output example:

{
  "theme": "AI Displacement",
  "intensity": 72,
  "persistence_days": 9,
  "acceleration": "rising",
  "affected_sectors": ["Industrials", "Fintech", "Defense"]
}
Themes feed into DailyMarketState.

LLM Layer (Read Only)
Hard Rules
LLM never sees raw prices

LLM never sees PnL

LLM never outputs trades

LLM must cite which fields informed each statement

Inputs
Today DailyMarketState

Rolling last N DailyMarketStates

Pre defined seasonal templates

LLM Outputs
1. Pre Open Morning Brief (Daily)
Generated automatically at 04:00 EST.

Structure:

Market posture (3 sentences max)

What changed vs yesterday

Active narrative themes

Cross asset confirmations or divergences

Engine alignment summary

Conditional watch list

Explicit no trade or stand down guidance if applicable

Tone:

Plain language

No hype

No recommendations

2. Weekly Roadmap (Sunday Night)
Generated Sunday 18:05 EST.

Structure:

Weekly regime and flow summary

Expected weekly pattern

High risk calendar days

Allowed engine behaviors

Earnings focus list (max 2)

Asymmetry radar

What would break the plan

3. Asymmetry Radar (Research Only)
Purpose:

Detect rare high impact conditions

Surface slow building dislocations

Examples:

Vol underpricing vs narrative acceleration

FX stress without equity reaction

Commodity spike with muted index response

Output must always say:

Monitor only

Await confirmation

No action yet

UI Integration
New Page: Market Intelligence
Sections:

Morning Brief

Weekly Roadmap

Active Themes

Cross Asset Stress

Asymmetry Radar

This page becomes the default landing page pre open.

Existing Pages Updates
Command Center pulls summary fields from DailyMarketState

Engines display gate status from DailyMarketState

Earnings Calendar overlays regime and theme tags

News Risk calendar feeds into DailyMarketState only

Operational Requirements
All snapshots persisted and reloadable

All LLM outputs timestamped

Diff view between days

Clear source attribution per statement

Fallback mode if LLM fails

Build Order
Phase 1

DailyMarketState schema

Snapshot persistence

Cross asset ingestion

Theme scoring

Phase 2

LLM read only pipeline

Morning brief

Weekly roadmap

Phase 3

UI integration

Alerts on state changes

Asymmetry radar

Non Goals
No broker connectivity

No auto execution

No sizing logic

No trade recommendations

No optimization against PnL

Definition of Done
Desk can understand the day in under 3 minutes

Desk knows what to focus on or ignore

Desk knows when not to trade

System runs automatically and consistently

No human interpretation required to parse outputs

