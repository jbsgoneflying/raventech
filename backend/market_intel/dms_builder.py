"""DMS v2 assembler — single service that replaces the inline builder
previously living in ``backend/routers/front_layer.py::_build_live_dms``.

Layers v2 fields on top of the legacy DMS schema so downstream consumers
(Raven Chat, E14 conditioning) continue to function without branching.

New top-level additions on the DMS dict:

- ``regime.probs``:               {risk_on, transitional, stressed}
- ``regime.confidence``:          float
- ``regime.transition_risk_1d``:  float
- ``regime.factor_contributions``: {factor: log-lik delta}
- ``regime.anomaly_score``:       float
- ``regime.data_quality``:        {ok, stale, missing, insufficient, model_source}
- ``regime.model_version`` / ``regime.calibrated_at``
- ``cross_asset_stress.pc1_proxy_stress`` / ``per_asset_loadings``
- ``market_intel``:               (convenience block for LLMs + chat)

Legacy fields (``regime.state``, ``regime.score``, ``regime.drivers``,
``vol_state``, ``engine_gates``, ``cross_asset_stress.composite_score``)
remain populated for backwards compat.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Any, Dict, List, Optional

from backend.daily_market_state import (
    DailyMarketState,
    _derive_engine_gates,
    _derive_news_risk,
    build_daily_market_state,
)
from backend.market_intel.cross_asset_v2 import build_cross_asset_v2
from backend.market_intel.regime_service import (
    RegimeSnapshot,
    regime_snapshot,
)

LOG = logging.getLogger("market_intel.dms_builder")


def build_dms_v2(
    *,
    today_str: str,
    store: Any,
    eodhd_client: Any = None,
    benzinga_client: Any = None,
) -> Dict[str, Any]:
    """Assemble the v2 DMS. Returns a dict (not a dataclass) because the
    v2 fields don't cleanly fit the legacy dataclass schema and we want
    to stream straight to the frontend.

    ``store`` is the Redis client; ``eodhd_client`` / ``benzinga_client``
    are optional and are resolved from env when absent.
    """
    from backend.config import get_flags
    flags = get_flags()

    # ------------------------------------------------------------------
    # Engine 5 snapshot (for vol direction + legacy regime label)
    # ------------------------------------------------------------------
    engine5_snapshot: Optional[dict] = None
    legacy_regime: Dict[str, Any] = {}
    regime_source: Dict[str, Any] = {}
    vol_direction = ""
    iv_stress = 50.0
    try:
        from backend.engine5_snapshot import select_best_snapshot
        engine5_snapshot = select_best_snapshot(store) if store else None
        if engine5_snapshot:
            snap_data = engine5_snapshot.get("data", {})
            legacy_regime = snap_data.get("regime", {})
            vol_ll = snap_data.get("volLeadLag", {})
            vol_direction = str(vol_ll.get("volLagState") or vol_ll.get("vol_lag_state", ""))
            iv_stress = float(legacy_regime.get("components", {}).get("iv_stress", 50.0))
            regime_source = {
                "snapshot_id":           engine5_snapshot.get("id", ""),
                "snapshot_generated_at": engine5_snapshot.get("generated_at", ""),
                "snapshot_grade":        engine5_snapshot.get("grade", ""),
            }
    except Exception as e:
        LOG.warning("DMS v2: Engine 5 unavailable: %s", e)

    # ------------------------------------------------------------------
    # Resolve clients
    # ------------------------------------------------------------------
    if eodhd_client is None:
        try:
            from backend.eodhd_client import EodhdClient
            eodhd_client = EodhdClient.from_env()
        except Exception:
            eodhd_client = None

    # ------------------------------------------------------------------
    # SPX 1d return (for cross-asset equity relationship + gate logic)
    # ------------------------------------------------------------------
    spx_return_1d = 0.0
    if eodhd_client is not None:
        try:
            spx_resp = eodhd_client.get_eod("GSPC.INDX", period="d")
            bars = sorted(spx_resp.rows, key=lambda b: str(b.get("date", "")))
            if len(bars) >= 2:
                cur = float(bars[-1].get("adjusted_close") or bars[-1].get("close", 0))
                prv = float(bars[-2].get("adjusted_close") or bars[-2].get("close", 0))
                if prv:
                    spx_return_1d = round((cur - prv) / abs(prv) * 100, 4)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Market Intelligence regime snapshot (canonical)
    # ------------------------------------------------------------------
    try:
        mi_snap: RegimeSnapshot = regime_snapshot(
            eodhd_client=eodhd_client,
            engine5_snapshot=engine5_snapshot,
        )
    except Exception as e:
        LOG.warning("DMS v2: regime_snapshot failed: %s", e)
        mi_snap = RegimeSnapshot(as_of=today_str, source="legacy_fallback")

    # ------------------------------------------------------------------
    # Cross-asset v2
    # ------------------------------------------------------------------
    try:
        xa = build_cross_asset_v2(
            eodhd_client=eodhd_client,
            spx_return_1d=spx_return_1d,
        )
        xa_dict = xa.to_dict()
    except Exception as e:
        LOG.warning("DMS v2: cross-asset v2 failed: %s", e)
        xa_dict = {}

    # ------------------------------------------------------------------
    # Sequencer + calendar + news themes (unchanged path from v1)
    # ------------------------------------------------------------------
    seq_summary: Dict[str, Any] = {}
    try:
        from backend.sequencer import (
            SequencerEvent,
            build_weekly_sequence,
            current_week_id,
        )
        wk = current_week_id()
        events_raw = []
        if store:
            events_raw = store.get_json(f"sequencer:week:{wk}") or []
        events = [SequencerEvent.from_dict(e) for e in events_raw] if events_raw else []
        seq = build_weekly_sequence(week_id=wk, events=events)
        seq_summary = seq.to_dict()
    except Exception as e:
        LOG.debug("DMS v2: sequencer unavailable: %s", e)

    event_count = 0
    high_sev = 0
    upcoming: List[str] = []
    try:
        from backend.calendar_api import build_calendar_payload
        cal = build_calendar_payload(mode="week")
        events = cal.get("events", [])
        event_count = len(events)
        high_sev = sum(
            1 for ev in events
            if str(ev.get("importance", "")).lower() in ("high", "critical")
        )
        upcoming = [str(ev.get("title", "")) for ev in events[:5] if ev.get("title")]
    except Exception as e:
        LOG.debug("DMS v2: calendar unavailable: %s", e)

    themes_list: List[dict] = []
    try:
        from backend.news_theme_intelligence import (
            extract_headlines_from_eodhd,
            extract_headlines_from_benzinga,
            load_theme_history,
            persist_theme_snapshot,
            score_themes,
        )
        headlines: List[str] = []
        if eodhd_client is not None:
            try:
                resp = eodhd_client.get_news(topic="market", limit=50)
                headlines.extend(extract_headlines_from_eodhd(resp.rows))
            except Exception:
                pass
        if benzinga_client is None:
            try:
                from backend.benzinga_client import BenzingaClient
                benzinga_client = BenzingaClient.from_env()
            except Exception:
                benzinga_client = None
        if benzinga_client is not None:
            try:
                resp = benzinga_client.news(page_size=50)
                headlines.extend(extract_headlines_from_benzinga(resp.rows))
            except Exception:
                pass
        if headlines:
            prior = load_theme_history(
                store, n_days=flags.FRONT_LAYER_THEME_LOOKBACK_DAYS,
            ) if store else []
            ts = score_themes(headlines=headlines, prior_snapshots=prior, date_str=today_str)
            themes_list = ts.themes
            if store:
                persist_theme_snapshot(ts, store)
    except Exception as e:
        LOG.debug("DMS v2: themes unavailable: %s", e)

    # ------------------------------------------------------------------
    # Assemble legacy DMS dataclass first (backwards compat), then layer v2
    # ------------------------------------------------------------------
    legacy_dms: DailyMarketState = build_daily_market_state(
        date_str=today_str,
        regime=legacy_regime,
        regime_source=regime_source,
        vol_direction=vol_direction,
        iv_stress=iv_stress,
        vix_level=None,
        earnings_candidates=None,
        index_state=None,
        event_count_5d=event_count,
        high_severity_count=high_sev,
        upcoming_events=upcoming,
        cross_asset_stress={
            # Keep v1 shape for any legacy consumer.
            "timestamp":        xa_dict.get("timestamp", ""),
            "readings":         xa_dict.get("readings", []),
            "composite_score":  xa_dict.get("composite_score", 50.0),
            "composite_label":  xa_dict.get("composite_label", "Neutral"),
        } if xa_dict else None,
        news_themes=themes_list,
        sequencer_summary=seq_summary,
        asymmetry_signals=None,
        post_event_extensions=None,
    )
    out = legacy_dms.to_dict()

    # ------------------------------------------------------------------
    # Layer v2 fields
    # ------------------------------------------------------------------
    v2_regime = {
        **(out.get("regime") or {}),
        "probs":                mi_snap.probs,
        "label":                mi_snap.label,
        "confidence":           mi_snap.confidence,
        "transition_risk_1d":   mi_snap.transition_risk_1d,
        "factor_contributions": mi_snap.factor_contributions,
        "anomaly_score":        mi_snap.anomaly_score,
        "data_quality":         mi_snap.data_quality,
        "model_version":        mi_snap.model_version,
        "calibrated_at":        mi_snap.calibrated_at,
        "source":               mi_snap.source,
        "confidence_band":      mi_snap.confidence_band,
        "factor_readings":      mi_snap.factor_readings,
    }
    # Promote canonical mi_snap label/score into legacy fields whenever we
    # have a real source. Without this the legacy ``regime.state`` /
    # ``regime.score`` keep echoing build_daily_market_state defaults
    # (Transitional / 50.0) even on days when the HMM has actual readings,
    # which left v2's regime encoder seeing 136 days of identical features
    # in production.
    canonical_label = (mi_snap.label or "").strip()
    legacy_state = (out.get("regime") or {}).get("state")
    has_real_signal = bool(mi_snap.source) and mi_snap.source != "legacy_fallback"
    if canonical_label and (has_real_signal or not legacy_state):
        v2_regime["state"] = canonical_label
    else:
        v2_regime.setdefault("state", legacy_state or canonical_label)
    if mi_snap.probs and has_real_signal:
        v2_regime["score"] = _stress_score_from_probs(mi_snap.probs)
    elif "score" not in v2_regime:
        v2_regime["score"] = (out.get("regime") or {}).get("score", 50.0)
    out["regime"] = v2_regime

    # Canonical vol_state (overwrites legacy derivation for consistency).
    if mi_snap.vol_state:
        # Keep legacy fields alongside new ones.
        merged_vol = {**(out.get("vol_state") or {}), **mi_snap.vol_state}
        out["vol_state"] = merged_vol

    # Cross-asset v2 fields.
    if xa_dict:
        out["cross_asset_stress"] = {
            **(out.get("cross_asset_stress") or {}),
            "pc1_proxy_stress":   xa_dict.get("pc1_proxy_stress", 0.0),
            "pc1_proxy_band":     xa_dict.get("pc1_proxy_band", {}),
            "per_asset_loadings": xa_dict.get("per_asset_loadings", {}),
            "universe_coverage":  xa_dict.get("universe_coverage", {}),
        }

    # ------------------------------------------------------------------
    # Skeleton-default detector
    # ------------------------------------------------------------------
    # Flag DMS docs that are entirely the dataclass defaults so downstream
    # consumers (v2 regime encoder, audit dashboards) can filter them out.
    # The fingerprint we look for is exactly what build_daily_market_state
    # produces when called with no real engine data:
    #   regime.state == Transitional, regime.score == 50,
    #   vol_state.level == 25 / term_structure == flat / skew == neutral,
    #   news_risk.today == low, mi_snap.source missing or legacy_fallback.
    out["data_quality"] = {
        "skeleton_default": _is_skeleton_default(out, mi_snap),
        "regime_source":    mi_snap.source or "missing",
        "had_engine5":      bool(engine5_snapshot),
        "generated_at":     out.get("generated_at"),
    }

    # Dedicated market_intel block for LLM consumption + chat context.
    out["market_intel"] = {
        "regime":                mi_snap.probs,
        "regime_label":          mi_snap.label,
        "regime_confidence":     mi_snap.confidence,
        "transition_risk_1d":    mi_snap.transition_risk_1d,
        "anomaly_score":         mi_snap.anomaly_score,
        "factor_readings":       mi_snap.factor_readings,
        "factor_contributions":  mi_snap.factor_contributions,
        "data_quality":          mi_snap.data_quality,
        "vol_state":             mi_snap.vol_state,
        "cross_asset_loadings":  xa_dict.get("per_asset_loadings", {}) if xa_dict else {},
        "pc1_proxy_stress":      xa_dict.get("pc1_proxy_stress", 0.0) if xa_dict else 0.0,
        "source":                mi_snap.source,
        "model_version":         mi_snap.model_version,
    }

    return out


# ---------------------------------------------------------------------------
# Helpers (module-level so they can be unit-tested directly)
# ---------------------------------------------------------------------------


def _stress_score_from_probs(probs: Dict[str, float]) -> float:
    """Convert HMM cluster probs into the legacy 0-100 stress score.

    Risk-On contributes 0, Transitional 50, Stressed 100. Designed so a
    pure Risk-On day scores ~10, pure Stressed ~90 — matching the bands
    the rest of v1 uses.
    """
    if not isinstance(probs, dict) or not probs:
        return 50.0
    risk_on      = float(probs.get("risk_on", 0.0) or 0.0)
    transitional = float(probs.get("transitional", 0.0) or 0.0)
    stressed     = float(probs.get("stressed", 0.0) or 0.0)
    total = risk_on + transitional + stressed
    if total <= 0:
        return 50.0
    risk_on, transitional, stressed = (
        risk_on / total, transitional / total, stressed / total,
    )
    return round(0.0 * risk_on + 50.0 * transitional + 100.0 * stressed, 2)


def _is_skeleton_default(dms: Dict[str, Any], mi_snap: Any) -> bool:
    """True when the DMS doc is exactly the build_daily_market_state default
    fingerprint (no engine data fed in)."""
    regime = dms.get("regime") or {}
    vol = dms.get("vol_state") or {}
    news = dms.get("news_risk") or {}
    return (
        float(regime.get("score", 0.0)) == 50.0
        and str(regime.get("state", "")) == "Transitional"
        and float(vol.get("level", 0.0)) == 25.0
        and str(vol.get("term_structure", "")) == "flat"
        and str(vol.get("skew", "")) == "neutral"
        and str(news.get("today", "")) == "low"
        and (not mi_snap.source or mi_snap.source == "legacy_fallback")
    )
