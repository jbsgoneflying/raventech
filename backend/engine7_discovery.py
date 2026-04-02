"""Engine 7 – Dynamic Pair Discovery.

Screens a pool of ETFs (sector SPDRs, factor, thematic) for statistically
promising pairs using rolling correlation and Engle-Granger cointegration.
Discovered pairs are ephemeral — they supplement the fixed 20-pair library
for one scan cycle and are not persisted unless manually promoted.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

_LOG = logging.getLogger(__name__)

# SPDR sector ETFs + popular factor/thematic ETFs to screen
DISCOVERY_POOL: List[str] = [
    "XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY",
    "IWM", "IWF", "IWD",
    "MTUM", "QUAL", "VLUE", "SIZE",
    "ARKK", "TAN", "LIT", "KWEB", "CQQQ",
    "GDX", "XME", "KRE", "XHB", "IBB",
]

MIN_BARS = 60
CORR_WINDOW = 40
COINT_PVALUE_THRESHOLD = 0.05
MIN_ROLLING_CORR = 0.55


@dataclass
class DiscoveredPair:
    pair_id: str
    long_ticker: str
    short_ticker: str
    rolling_corr: float
    coint_pvalue: float
    half_life: Optional[float] = None
    discovery_reason: str = ""


def _extract_closes(bars: List[dict]) -> List[float]:
    """Extract close prices from bar dicts, sorted by date."""
    sorted_bars = sorted(bars, key=lambda b: str(b.get("date", "")))
    return [float(b["close"]) for b in sorted_bars if b.get("close") is not None]


def _rolling_correlation(x: List[float], y: List[float], window: int = CORR_WINDOW) -> float:
    """Pearson correlation over the last `window` observations."""
    n = min(len(x), len(y), window)
    if n < 20:
        return 0.0
    xw, yw = x[-n:], y[-n:]
    mx = sum(xw) / n
    my = sum(yw) / n
    cov = sum((xw[i] - mx) * (yw[i] - my) for i in range(n)) / n
    sx = math.sqrt(sum((v - mx) ** 2 for v in xw) / n)
    sy = math.sqrt(sum((v - my) ** 2 for v in yw) / n)
    if sx < 1e-10 or sy < 1e-10:
        return 0.0
    return cov / (sx * sy)


def _engle_granger_coint(x: List[float], y: List[float]) -> Tuple[float, float]:
    """Simplified Engle-Granger cointegration test.

    Runs OLS y ~ x, then an ADF-style test on the residuals.
    Returns (adf_stat, approx_pvalue).  Uses a simple heuristic
    p-value mapping since we avoid scipy dependency.
    """
    n = min(len(x), len(y))
    if n < 30:
        return 0.0, 1.0

    xv, yv = x[-n:], y[-n:]
    mx = sum(xv) / n
    my = sum(yv) / n

    # OLS: y = alpha + beta * x + epsilon
    sxx = sum((xi - mx) ** 2 for xi in xv)
    if sxx < 1e-12:
        return 0.0, 1.0
    sxy = sum((xv[i] - mx) * (yv[i] - my) for i in range(n))
    beta = sxy / sxx
    alpha = my - beta * mx

    residuals = [yv[i] - alpha - beta * xv[i] for i in range(n)]

    # ADF test on residuals (lag-1): delta_r = gamma * r_{t-1} + error
    dr = [residuals[i] - residuals[i - 1] for i in range(1, len(residuals))]
    r_lag = residuals[:-1]
    m = len(dr)
    if m < 10:
        return 0.0, 1.0

    mr = sum(r_lag) / m
    mdr = sum(dr) / m
    srr = sum((r_lag[i] - mr) ** 2 for i in range(m))
    if srr < 1e-12:
        return 0.0, 1.0
    srd = sum((r_lag[i] - mr) * (dr[i] - mdr) for i in range(m))
    gamma = srd / srr
    se_gamma_num = sum((dr[i] - mdr - gamma * (r_lag[i] - mr)) ** 2 for i in range(m))
    se_gamma = math.sqrt(se_gamma_num / max(1, m - 2)) / math.sqrt(srr)
    if se_gamma < 1e-12:
        return 0.0, 1.0
    adf_stat = gamma / se_gamma

    # Rough p-value mapping for Engle-Granger (2 variables, constant)
    # Critical values: 1% ~ -3.90, 5% ~ -3.34, 10% ~ -3.04
    if adf_stat < -3.90:
        pvalue = 0.01
    elif adf_stat < -3.34:
        pvalue = 0.05
    elif adf_stat < -3.04:
        pvalue = 0.10
    elif adf_stat < -2.58:
        pvalue = 0.20
    else:
        pvalue = 0.50

    return adf_stat, pvalue


def _half_life(residuals: List[float]) -> Optional[float]:
    """Estimate mean-reversion half-life from residuals via AR(1)."""
    if len(residuals) < 10:
        return None
    dr = [residuals[i] - residuals[i - 1] for i in range(1, len(residuals))]
    r_lag = residuals[:-1]
    m = len(dr)
    mr = sum(r_lag) / m
    srr = sum((r_lag[i] - mr) ** 2 for i in range(m))
    if srr < 1e-12:
        return None
    srd = sum((r_lag[i] - mr) * dr[i] for i in range(m))
    gamma = srd / srr
    if gamma >= 0:
        return None
    return round(-math.log(2) / gamma, 1)


def discover_pairs(
    bars_by_ticker: Dict[str, List[dict]],
    *,
    pool: Optional[List[str]] = None,
    max_pairs: int = 5,
    corr_window: int = CORR_WINDOW,
    min_corr: float = MIN_ROLLING_CORR,
    coint_threshold: float = COINT_PVALUE_THRESHOLD,
) -> List[DiscoveredPair]:
    """Screen ETF pool for cointegrated pairs.

    Args:
        bars_by_ticker: {ticker: [bar_dict, ...]} with "date" and "close" keys.
        pool: Ticker pool to screen. Defaults to DISCOVERY_POOL.
        max_pairs: Maximum pairs to return.

    Returns list of DiscoveredPair sorted by cointegration p-value (best first).
    """
    tickers = pool or DISCOVERY_POOL
    available = {t: _extract_closes(bars_by_ticker.get(f"{t}.US", bars_by_ticker.get(t, [])))
                 for t in tickers}
    available = {t: c for t, c in available.items() if len(c) >= MIN_BARS}

    if len(available) < 2:
        _LOG.info("Engine7 discovery: only %d tickers with sufficient data", len(available))
        return []

    candidates: List[DiscoveredPair] = []
    checked = set()
    ticker_list = sorted(available.keys())

    for i, t1 in enumerate(ticker_list):
        for t2 in ticker_list[i + 1:]:
            key = (t1, t2)
            if key in checked:
                continue
            checked.add(key)

            c1, c2 = available[t1], available[t2]
            corr = _rolling_correlation(c1, c2, corr_window)
            if abs(corr) < min_corr:
                continue

            adf_stat, pvalue = _engle_granger_coint(c1, c2)
            if pvalue > coint_threshold:
                continue

            n = min(len(c1), len(c2))
            x, y = c1[-n:], c2[-n:]
            mx = sum(x) / n
            sxx = sum((xi - mx) ** 2 for xi in x)
            if sxx < 1e-12:
                continue
            sxy = sum((x[j] - mx) * (y[j] - sum(y) / n) for j in range(n))
            beta = sxy / sxx
            alpha = sum(y) / n - beta * mx
            resid = [y[j] - alpha - beta * x[j] for j in range(n)]
            hl = _half_life(resid)

            # Determine long/short based on z-score of spread
            if len(resid) >= 2:
                spread_mean = sum(resid) / len(resid)
                spread_std = math.sqrt(sum((r - spread_mean) ** 2 for r in resid) / len(resid))
                z = (resid[-1] - spread_mean) / spread_std if spread_std > 0.01 else 0
                long_t, short_t = (t2, t1) if z < 0 else (t1, t2)
            else:
                long_t, short_t = t1, t2

            candidates.append(DiscoveredPair(
                pair_id=f"disc_{long_t}_{short_t}",
                long_ticker=long_t,
                short_ticker=short_t,
                rolling_corr=round(corr, 4),
                coint_pvalue=round(pvalue, 4),
                half_life=hl,
                discovery_reason=f"corr={corr:.2f}, coint p={pvalue:.3f}"
                + (f", hl={hl:.0f}d" if hl else ""),
            ))

    candidates.sort(key=lambda p: p.coint_pvalue)
    result = candidates[:max_pairs]
    _LOG.info(
        "Engine7 discovery: screened %d pairs from %d tickers, found %d cointegrated (returning %d)",
        len(checked), len(available), len(candidates), len(result),
    )
    return result
