"""Engine 1 v2 — MI v2 regime overlay wire test.

When ``ENABLE_MI_V2`` is True, :func:`compute_breach_stats` overlays the
MI v2 canonical regime label + probabilities onto the ``regime`` dict.
``compute_e1_desk_consensus`` already reads ``regime.label`` — so this
test asserts the overlay actually arrives.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import backend.earnings_logic as el


class _FakeRegimeSnap(SimpleNamespace):
    pass


class _Resp:
    def __init__(self, rows):
        self.rows = rows


class _ShimClient:
    """Returns enough per-call rows to reach the MI-overlay code path."""
    def hist_earnings(self, ticker):
        # Return an empty list — compute_breach_stats falls through quickly.
        return _Resp([])


@pytest.fixture
def forced_mi_v2(monkeypatch):
    """Force MI v2 on + stub regime_snapshot to return a synthetic reading."""
    from backend.config import get_flags
    f = get_flags()

    class _F:
        pass
    for k, v in vars(f).items():
        setattr(_F, k, v)
    _F.ENABLE_MI_V2 = True

    monkeypatch.setattr(el, "get_flags", lambda: _F)

    snap = _FakeRegimeSnap(
        label="Stressed",
        probabilities={"Risk-On": 0.05, "Transitional": 0.15, "Stressed": 0.80},
        vol_state="stress",
        source="v2_hmm",
    )
    # Overlay imports from backend.market_intel at call time.
    import backend.market_intel as mi
    monkeypatch.setattr(mi, "regime_snapshot", lambda force_refresh=False: snap)
    return snap


def test_breach_stats_overlays_mi_v2_regime(forced_mi_v2, monkeypatch):
    """When MI v2 is on, regime['label'] is sourced from the snapshot."""
    # Also stub the legacy regime_overlay call so we don't touch the network.
    monkeypatch.setattr(
        el, "compute_regime_overlay",
        lambda *a, **kw: {"label": "LEGACY_LABEL", "tailMultiplier": 1.0, "guidance": {}},
    )
    monkeypatch.setattr(
        el, "compute_regime_backtest_view",
        lambda *a, **kw: ([], {}),
    )
    monkeypatch.setattr(
        el, "_current_snapshot",
        lambda *a, **kw: {"stockPrice": 100.0, "impliedMovePct": 5.0, "asOfDate": "2026-04-21"},
    )

    out = el.compute_breach_stats(
        client=_ShimClient(),
        ticker="NVDA",
        n=20,
        years=5,
        k=1.0,
        today=__import__("datetime").date(2026, 4, 21),
    )

    # The overlay should have replaced the legacy label with the MI v2 label.
    assert out["regime"]["label"] == "Stressed"

    # And stashed the full snapshot under regime.mi_v2
    mi_v2 = out["regime"].get("mi_v2")
    assert isinstance(mi_v2, dict)
    assert mi_v2["label"] == "Stressed"
    assert mi_v2["vol_state"] == "stress"
    assert mi_v2["probabilities"]["Stressed"] == pytest.approx(0.80)


def test_breach_stats_falls_back_when_mi_v2_disabled(monkeypatch):
    """With MI v2 off, the legacy regime label survives (no overlay)."""
    from backend.config import get_flags
    f = get_flags()

    class _F:
        pass
    for k, v in vars(f).items():
        setattr(_F, k, v)
    _F.ENABLE_MI_V2 = False

    monkeypatch.setattr(el, "get_flags", lambda: _F)

    monkeypatch.setattr(
        el, "compute_regime_overlay",
        lambda *a, **kw: {"label": "Normal", "tailMultiplier": 1.0, "guidance": {}},
    )
    monkeypatch.setattr(
        el, "compute_regime_backtest_view",
        lambda *a, **kw: ([], {}),
    )
    monkeypatch.setattr(
        el, "_current_snapshot",
        lambda *a, **kw: {"stockPrice": 100.0, "impliedMovePct": 5.0, "asOfDate": "2026-04-21"},
    )

    out = el.compute_breach_stats(
        client=_ShimClient(), ticker="NVDA", n=20, years=5, k=1.0,
        today=__import__("datetime").date(2026, 4, 21),
    )
    assert out["regime"]["label"] == "Normal"
    assert "mi_v2" not in out["regime"]


def test_wing_console_uses_mi_v2_regime(forced_mi_v2):
    """build_wing_console should pull regime from market_intel.regime_snapshot()."""
    from backend.engine1 import build_wing_console

    payload = {
        "ticker": "NVDA",
        "current": {"stockPrice": 100.0, "impliedMovePct": 5.0},
        "nextEvent": {"impliedMovePctPlanned": 5.0},
        "events": [{"signedMovePct": r * 5.0, "impliedMovePct": 5.0} for r in
                   [0.3, 0.6, 0.8, 0.4, 0.7, 0.9, 1.1, 0.5, 0.8, 0.4]],
        "tradeBuilder": {},
    }
    console = build_wing_console(
        ticker="NVDA", event_date="2026-05-28", event_timing="AMC",
        payload=payload,
    )
    assert console.regime_label == "Stressed"
    assert console.regime_prob == pytest.approx(0.80)
