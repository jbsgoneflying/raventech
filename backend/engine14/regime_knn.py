"""Engine 14 — Phase C2 KNN regime matching.

Given the user's entry-day `RegimeFeatures` and a candidate set of analogue
windows, rank the candidates by weighted L2 distance in feature space and
return the top-K nearest neighbors.

Keeping the scoring in its own module (instead of inlining in
`analogue_matcher.filter_analogues`) makes the fixture and tests trivial.
"""

from __future__ import annotations

import logging
import math
import statistics
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from backend.engine14.regime_features import RegimeFeatures

LOG = logging.getLogger("engine14.regime_knn")


# Feature order, matching `RegimeFeatures.feature_vector`:
#   [vix, vix9d, vvix, term_slope, rv20, net_gex, credit_score]
# Weights reflect *economic importance* for vol-selling regimes — feel free
# to rebalance after Phase C2 goes empirical (we could fit these via the same
# modifier-coefficients script). Sum is normalized at scoring time.
DEFAULT_FEATURE_WEIGHTS: Tuple[float, ...] = (
    1.0,   # vix
    0.8,   # vix9d
    0.6,   # vvix
    0.5,   # term_slope
    0.8,   # rv20
    0.5,   # net_gex (often missing historically)
    0.4,   # credit_stress_score
)

FEATURE_NAMES: Tuple[str, ...] = (
    "vix", "vix9d", "vvix", "termSlope", "rv20", "netGex", "creditStressScore",
)


@dataclass(frozen=True)
class KnnScore:
    trade_date: str
    distance: float
    imputation_fraction: float   # 0..1 — fraction of columns we had to impute


# ---------------------------------------------------------------------------
# Column statistics
# ---------------------------------------------------------------------------

def compute_column_stats(pool: Sequence[RegimeFeatures]) -> Tuple[List[float], List[float]]:
    """Return (medians, stdevs) for each feature column across `pool`.

    Medians are used for imputation; stdevs for z-score normalization.
    Columns with zero variance or entirely-missing data get stdev=1.0 to
    avoid divide-by-zero.
    """
    cols: List[List[float]] = [[] for _ in range(len(FEATURE_NAMES))]
    for f in pool:
        vec = f.feature_vector()
        for i, v in enumerate(vec):
            if v is not None and isinstance(v, (int, float)) and math.isfinite(float(v)):
                cols[i].append(float(v))
    medians: List[float] = []
    stdevs: List[float] = []
    for i, col in enumerate(cols):
        if col:
            medians.append(float(statistics.median(col)))
            stdevs.append(float(statistics.stdev(col)) if len(col) > 1 else 1.0)
        else:
            medians.append(0.0)
            stdevs.append(1.0)
        if stdevs[-1] <= 1e-9:
            stdevs[-1] = 1.0
    return medians, stdevs


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _z_vector(
    vec: Sequence[Optional[float]],
    *,
    medians: Sequence[float],
    stdevs: Sequence[float],
) -> Tuple[List[float], int]:
    """Return (z-scored vector, imputed_count).

    Missing entries are imputed with the median and counted so the caller
    can surface a "match quality" metric.
    """
    out: List[float] = []
    imputed = 0
    for i, v in enumerate(vec):
        if v is None or not isinstance(v, (int, float)) or not math.isfinite(float(v)):
            out.append(0.0)         # z=0 post-imputation
            imputed += 1
        else:
            out.append((float(v) - float(medians[i])) / float(stdevs[i]))
    return out, imputed


def weighted_l2(
    user_z: Sequence[float],
    cand_z: Sequence[float],
    *,
    weights: Sequence[float] = DEFAULT_FEATURE_WEIGHTS,
) -> float:
    """Weighted Euclidean distance with L2 weight normalization."""
    if not user_z or not cand_z:
        return float("inf")
    n = min(len(user_z), len(cand_z), len(weights))
    wsum = sum(float(weights[i]) for i in range(n))
    if wsum <= 0:
        return float("inf")
    acc = 0.0
    for i in range(n):
        d = float(user_z[i]) - float(cand_z[i])
        acc += (float(weights[i]) / wsum) * d * d
    return math.sqrt(acc)


def score_candidates(
    *,
    user: RegimeFeatures,
    candidates: Dict[str, RegimeFeatures],
    weights: Sequence[float] = DEFAULT_FEATURE_WEIGHTS,
) -> List[KnnScore]:
    """Compute the user→candidate distance for each window.

    `candidates` maps trade_date → RegimeFeatures. Normalization uses
    column statistics computed over `candidates ∪ {user}` so the user's
    own features participate in the distribution (avoids pathological
    z-scores when the user sits at a wing).
    """
    if not candidates:
        return []
    pool = list(candidates.values()) + [user]
    medians, stdevs = compute_column_stats(pool)
    user_z, user_imputed = _z_vector(user.feature_vector(), medians=medians, stdevs=stdevs)
    n_cols = len(FEATURE_NAMES)
    out: List[KnnScore] = []
    for td, f in candidates.items():
        c_z, c_imputed = _z_vector(f.feature_vector(), medians=medians, stdevs=stdevs)
        dist = weighted_l2(user_z, c_z, weights=weights)
        imp = (float(user_imputed + c_imputed) / (2.0 * n_cols)) if n_cols else 0.0
        out.append(KnnScore(trade_date=td, distance=float(dist), imputation_fraction=float(imp)))
    out.sort(key=lambda s: s.distance)
    return out


def knn_top_n(
    *,
    user: RegimeFeatures,
    candidates: Dict[str, RegimeFeatures],
    k: int = 80,
) -> List[KnnScore]:
    """Return the K nearest neighbors by weighted L2 distance."""
    scores = score_candidates(user=user, candidates=candidates)
    k = max(1, int(k))
    return scores[:k]


def summarize_match_quality(scores: Sequence[KnnScore]) -> Dict[str, float]:
    """Produce a display-ready summary of KNN match quality."""
    if not scores:
        return {"n": 0, "meanDistance": 0.0, "minDistance": 0.0, "maxDistance": 0.0,
                "meanImputationFraction": 0.0}
    dists = [s.distance for s in scores]
    imps = [s.imputation_fraction for s in scores]
    return {
        "n": int(len(scores)),
        "meanDistance": float(sum(dists) / len(dists)),
        "minDistance": float(min(dists)),
        "maxDistance": float(max(dists)),
        "meanImputationFraction": float(sum(imps) / len(imps)),
    }
