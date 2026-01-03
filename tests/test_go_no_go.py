import datetime as dt


class DummyResp:
    def __init__(self, rows):
        self.rows = rows


class DummyClient:
    def __init__(self, *, cores_row=None, monies_rows=None, strikes_rows=None):
        self._cores_row = cores_row or {}
        self._monies_rows = monies_rows or []
        self._strikes_rows = strikes_rows or []

    def cores(self, *, ticker: str, fields: str):
        return DummyResp([dict(self._cores_row)])

    def hist_monies_implied(self, *, ticker: str, trade_date: str, fields: str, dte: str | None = None):
        return DummyResp(list(self._monies_rows))

    def hist_strikes(self, *, ticker: str, trade_date: str, fields: str, dte: str | None = None, delta: str | None = None):
        return DummyResp(list(self._strikes_rows))


class DummyFlags:
    # enable z-score by default; keep Benzinga off in unit tests unless explicitly needed
    ENABLE_BENZINGA = False
    GO_IV_Z_ENABLED = True
    GO_IVP_MIN = 0.80
    GO_IV_SAMPLE_MIN = 20
    GO_IV30_FLOOR = 0.30
    GO_IV30_Z_MIN = 0.75

    GO_MIN_EARNINGS_N = 6
    GO_EM_RICHNESS_MULT = 1.05

    GO_AVG_DOLLAR_VOL20D_MIN = 200_000_000.0
    GO_OPT_DELTA_BAND_LO = 0.15
    GO_OPT_DELTA_BAND_HI = 0.20
    GO_OPT_SPREAD_MAX = 0.15
    GO_OPT_MIN_MID = 0.20
    GO_OPT_OI_MIN = 500.0
    GO_OPT_VOL_MIN = 50.0

    GO_RV5_JUMP_MAX = 1.15
    GO_RV20_JUMP_MAX = 1.10
    GO_RV5_ACCEL_TIGHTEN_TRIGGER = 1.05
    GO_FLIP_CUTOFF_BASE = 2.0
    GO_FLIP_CUTOFF_TIGHT = 2.5

    GO_FORCED_FLOW_WINDOW_TRADING_DAYS = 4
    GO_FORCED_FLOW_IMPORTANCE_HIGH_MIN = 4
    GO_FORCED_FLOW_IMPORTANCE_MED_MIN = 3
    GO_FORCED_FLOW_MANUAL_RANGES = []

    LEGAL_REG_TICKER_DENYLIST = []
    LEGAL_REG_TICKER_ALLOWLIST = []
    LEGAL_REG_KEYWORDS = []


def _mk_hist_cores_rows(values_pct, start_date="2026-01-01", field="iv30"):
    d0 = dt.date.fromisoformat(start_date)
    rows = []
    for i, v in enumerate(values_pct):
        rows.append({"tradeDate": (d0 + dt.timedelta(days=i)).isoformat(), field: float(v)})
    return rows


def _find(checks, id_):
    return next(c for c in checks if c.get("id") == id_)


def test_em_richness_missing_when_sample_too_small(monkeypatch):
    from backend import go_no_go

    monkeypatch.setattr(go_no_go, "get_flags", lambda: DummyFlags)
    monkeypatch.setattr(go_no_go, "fetch_hist_cores_range", lambda *a, **k: _mk_hist_cores_rows([35.0] * 25))
    monkeypatch.setattr(go_no_go, "compute_live_levels", lambda *a, **k: {"enabled": False})
    monkeypatch.setattr(go_no_go, "fetch_dailies_ohlc_range", lambda *a, **k: [])

    client = DummyClient(cores_row={"avgDollarVol20": 300_000_000.0}, monies_rows=[{"expirDate": "2026-01-03", "dte": 2}], strikes_rows=[])
    payload = {"ticker": "AAPL", "current": {"asOfDate": "2026-01-02", "impliedMovePct": 5.0}, "events": [{"realizedMovePct": 4.0}] * 5}
    out = go_no_go.compute_go_no_go(client, ticker="AAPL", payload=payload, benzinga_client=None)
    c = _find(out["checks"], "SN_EM_RICHNESS")
    assert c["state"] == "MISSING"
    assert c["code"] == "SN_EM_SAMPLE_TOO_SMALL"


def test_em_richness_fail_when_not_rich_enough(monkeypatch):
    from backend import go_no_go

    monkeypatch.setattr(go_no_go, "get_flags", lambda: DummyFlags)
    monkeypatch.setattr(go_no_go, "fetch_hist_cores_range", lambda *a, **k: _mk_hist_cores_rows([35.0] * 25))
    monkeypatch.setattr(go_no_go, "compute_live_levels", lambda *a, **k: {"enabled": False})
    monkeypatch.setattr(go_no_go, "fetch_dailies_ohlc_range", lambda *a, **k: [])

    client = DummyClient(cores_row={"avgDollarVol20": 300_000_000.0}, monies_rows=[{"expirDate": "2026-01-03", "dte": 2}], strikes_rows=[])
    payload = {"ticker": "AAPL", "current": {"asOfDate": "2026-01-02", "impliedMovePct": 5.0}, "events": [{"realizedMovePct": 5.0}] * 6}
    out = go_no_go.compute_go_no_go(client, ticker="AAPL", payload=payload, benzinga_client=None)
    c = _find(out["checks"], "SN_EM_RICHNESS")
    assert c["state"] == "FAIL"
    assert c["code"] == "SN_EM_NOT_RICH_ENOUGH"


def test_iv_missing_when_sample_insufficient(monkeypatch):
    from backend import go_no_go

    monkeypatch.setattr(go_no_go, "get_flags", lambda: DummyFlags)
    monkeypatch.setattr(go_no_go, "fetch_hist_cores_range", lambda *a, **k: _mk_hist_cores_rows([35.0] * 10))
    monkeypatch.setattr(go_no_go, "compute_live_levels", lambda *a, **k: {"enabled": False})
    monkeypatch.setattr(go_no_go, "fetch_dailies_ohlc_range", lambda *a, **k: [])

    client = DummyClient(cores_row={"avgDollarVol20": 300_000_000.0}, monies_rows=[{"expirDate": "2026-01-03", "dte": 2}], strikes_rows=[])
    payload = {"ticker": "AAPL", "current": {"asOfDate": "2026-01-02", "impliedMovePct": 6.0}, "events": [{"realizedMovePct": 4.0}] * 6}
    out = go_no_go.compute_go_no_go(client, ticker="AAPL", payload=payload, benzinga_client=None)
    c = _find(out["checks"], "SN_IV_ELEVATED")
    assert c["state"] == "MISSING"
    assert c["code"] == "SN_IV_SAMPLE_INSUFFICIENT"


def test_iv_fail_absolute_floor(monkeypatch):
    from backend import go_no_go

    monkeypatch.setattr(go_no_go, "get_flags", lambda: DummyFlags)
    monkeypatch.setattr(go_no_go, "fetch_hist_cores_range", lambda *a, **k: _mk_hist_cores_rows([25.0] * 25))
    monkeypatch.setattr(go_no_go, "compute_live_levels", lambda *a, **k: {"enabled": False})
    monkeypatch.setattr(go_no_go, "fetch_dailies_ohlc_range", lambda *a, **k: [])

    client = DummyClient(cores_row={"avgDollarVol20": 300_000_000.0}, monies_rows=[{"expirDate": "2026-01-03", "dte": 2}], strikes_rows=[])
    payload = {"ticker": "AAPL", "current": {"asOfDate": "2026-01-02", "impliedMovePct": 6.0}, "events": [{"realizedMovePct": 4.0}] * 6}
    out = go_no_go.compute_go_no_go(client, ticker="AAPL", payload=payload, benzinga_client=None)
    c = _find(out["checks"], "SN_IV_ELEVATED")
    assert c["state"] == "FAIL"
    assert c["code"] == "SN_IV_TOO_LOW_ABSOLUTE"


def test_iv_fail_zscore_even_if_percentile_passes(monkeypatch):
    from backend import go_no_go

    monkeypatch.setattr(go_no_go, "get_flags", lambda: DummyFlags)
    # 16 values at 30%, 4 values at 80%, ending at 30%:
    series = [30.0] * 15 + [80.0] * 4 + [30.0]
    monkeypatch.setattr(go_no_go, "fetch_hist_cores_range", lambda *a, **k: _mk_hist_cores_rows(series))
    monkeypatch.setattr(go_no_go, "compute_live_levels", lambda *a, **k: {"enabled": False})
    monkeypatch.setattr(go_no_go, "fetch_dailies_ohlc_range", lambda *a, **k: [])

    client = DummyClient(cores_row={"avgDollarVol20": 300_000_000.0}, monies_rows=[{"expirDate": "2026-01-03", "dte": 2}], strikes_rows=[])
    payload = {"ticker": "AAPL", "current": {"asOfDate": "2026-01-02", "impliedMovePct": 6.0}, "events": [{"realizedMovePct": 4.0}] * 6}
    out = go_no_go.compute_go_no_go(client, ticker="AAPL", payload=payload, benzinga_client=None)
    c = _find(out["checks"], "SN_IV_ELEVATED")
    assert c["state"] == "FAIL"
    assert c["code"] == "SN_IV_NOT_ELEVATED_Z"


def test_liquidity_missing_when_quotes_missing(monkeypatch):
    from backend import go_no_go

    monkeypatch.setattr(go_no_go, "get_flags", lambda: DummyFlags)
    monkeypatch.setattr(go_no_go, "fetch_hist_cores_range", lambda *a, **k: _mk_hist_cores_rows([35.0] * 25))
    monkeypatch.setattr(go_no_go, "compute_live_levels", lambda *a, **k: {"enabled": False})
    monkeypatch.setattr(go_no_go, "fetch_dailies_ohlc_range", lambda *a, **k: [])

    # strikes with missing put bid/ask
    strikes = [
        {"expirDate": "2026-01-03", "strike": 100, "stockPrice": 110, "putDelta": -0.17, "callDelta": 0.17, "callBidPrice": 1.0, "callAskPrice": 1.1},
        {"expirDate": "2026-01-03", "strike": 105, "stockPrice": 110, "putDelta": -0.10, "callDelta": 0.10, "putBidPrice": None, "putAskPrice": None, "callBidPrice": 1.0, "callAskPrice": 1.1},
    ]
    client = DummyClient(
        cores_row={"avgDollarVol20": 300_000_000.0},
        monies_rows=[{"expirDate": "2026-01-03", "dte": 2}],
        strikes_rows=strikes,
    )
    payload = {"ticker": "AAPL", "current": {"asOfDate": "2026-01-02", "impliedMovePct": 6.0}, "events": [{"realizedMovePct": 4.0}] * 6}
    out = go_no_go.compute_go_no_go(client, ticker="AAPL", payload=payload, benzinga_client=None)
    c = _find(out["checks"], "SN_LIQUIDITY")
    assert c["state"] == "MISSING"
    assert c["code"] == "SN_OPT_QUOTES_MISSING"


def test_macro_gamma_fails_if_magnitude_low(monkeypatch):
    from backend import go_no_go

    monkeypatch.setattr(go_no_go, "get_flags", lambda: DummyFlags)
    monkeypatch.setattr(go_no_go, "fetch_hist_cores_range", lambda *a, **k: _mk_hist_cores_rows([35.0] * 25))
    monkeypatch.setattr(go_no_go, "fetch_dailies_ohlc_range", lambda *a, **k: [])
    monkeypatch.setattr(
        go_no_go,
        "compute_live_levels",
        lambda *a, **k: {"enabled": True, "symbolUsed": "SPX", "expiry": "2026-01-03", "dealerGamma": {"netGammaSign": "positive", "magnitudeBucket": "low"}, "gexHeatmap": {"enabled": False}},
    )

    client = DummyClient(cores_row={"avgDollarVol20": 300_000_000.0}, monies_rows=[{"expirDate": "2026-01-03", "dte": 2}], strikes_rows=[])
    payload = {"ticker": "AAPL", "current": {"asOfDate": "2026-01-02", "impliedMovePct": 6.0}, "events": [{"realizedMovePct": 4.0}] * 6}
    out = go_no_go.compute_go_no_go(client, ticker="AAPL", payload=payload, benzinga_client=None)
    c = _find(out["checks"], "MACRO_GAMMA")
    assert c["state"] == "FAIL"
    assert c["code"] == "MACRO_GAMMA_TOO_SMALL"


