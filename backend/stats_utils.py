from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Tuple


def _betaln(a: float, b: float) -> float:
    return math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)


def _betacf(a: float, b: float, x: float) -> float:
    """
    Continued fraction for incomplete beta (Lentz's method).
    Based on standard Numerical Recipes / Cephes-style implementations.
    """
    max_iter = 200
    eps = 3.0e-14
    fpmin = 1.0e-300

    qab = a + b
    qap = a + 1.0
    qam = a - 1.0

    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < fpmin:
        d = fpmin
    d = 1.0 / d
    h = d

    for m in range(1, max_iter + 1):
        m2 = 2 * m

        # even step
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        h *= d * c

        # odd step
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        del_ = d * c
        h *= del_

        if abs(del_ - 1.0) < eps:
            break

    return h


def regularized_incomplete_beta(a: float, b: float, x: float) -> float:
    """
    Regularized incomplete beta I_x(a,b) in [0,1].

    Deterministic, scipy-free implementation suitable for small-sample CI calculations.
    """
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    if a <= 0.0 or b <= 0.0:
        raise ValueError("a and b must be > 0")

    # Use symmetry transform for better convergence.
    ln_bt = a * math.log(x) + b * math.log(1.0 - x) - _betaln(a, b)
    bt = math.exp(ln_bt) if ln_bt > -745.0 else 0.0  # avoid underflow into 0

    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    else:
        return 1.0 - (bt * _betacf(b, a, 1.0 - x) / b)


def beta_ppf(p: float, a: float, b: float) -> float:
    """
    Inverse CDF (quantile) for Beta(a,b), scipy-free via bisection on I_x(a,b).
    """
    if p <= 0.0:
        return 0.0
    if p >= 1.0:
        return 1.0
    if a <= 0.0 or b <= 0.0:
        raise ValueError("a and b must be > 0")

    lo = 0.0
    hi = 1.0
    # Bisection is robust; 80 iterations is plenty for double precision.
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        cdf = regularized_incomplete_beta(a, b, mid)
        if cdf < p:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


@dataclass(frozen=True)
class BetaPosterior:
    alpha: float
    beta: float

    @property
    def mean(self) -> float:
        return float(self.alpha) / float(self.alpha + self.beta)

    def ci(self, *, level: float = 0.90) -> Tuple[float, float]:
        if not (0.0 < level < 1.0):
            raise ValueError("level must be in (0,1)")
        tail = (1.0 - level) / 2.0
        lo = beta_ppf(tail, self.alpha, self.beta)
        hi = beta_ppf(1.0 - tail, self.alpha, self.beta)
        return float(lo), float(hi)


def beta_posterior_from_counts(
    *,
    successes: int,
    trials: int,
    alpha0: float = 1.0,
    beta0: float = 1.0,
) -> Optional[BetaPosterior]:
    """
    Beta-Binomial posterior for a breach probability.

    Returns a BetaPosterior(alpha0+successes, beta0+failures) or None if inputs invalid.
    """
    try:
        s = int(successes)
        n = int(trials)
    except (TypeError, ValueError):
        return None
    if n < 0 or s < 0 or s > n:
        return None
    a0 = float(alpha0)
    b0 = float(beta0)
    if a0 <= 0.0 or b0 <= 0.0:
        return None
    return BetaPosterior(alpha=a0 + s, beta=b0 + (n - s))
