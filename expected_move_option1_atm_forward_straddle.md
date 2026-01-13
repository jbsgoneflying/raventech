# Live Expected Move (Near-Dated Expiry) — **Option 1: ATM-Forward Straddle (Gold Standard for E[|Move|])**

This method computes the **risk-neutral expected absolute move** to the near-dated expiration date using the **ATM-forward straddle**. It is model-light, robust intraday, and ties directly to no-arbitrage pricing.

---

## 1) What you are computing

Let:

- \(S_0\): current spot
- \(T\): time to expiration in **years**
- \(r\): continuously-compounded risk-free rate for maturity \(T\)
- \(DF = e^{-rT}\): discount factor
- \(F\): **forward** price for maturity \(T\)
- \(C(K,T)\), \(P(K,T)\): call/put **prices** (European-style definition; in practice you use listed US equity options and handle known caveats)

### Key identity (European no-arbitrage)

For any strike \(K\):

\[
C(K,T) + P(K,T) = DF \cdot \mathbb{E}^{\mathbb{Q}}\left[\,|S_T - K|\,\right]
\]

Therefore at **\(K = F\)**:

\[
\boxed{
\mathbb{E}^{\mathbb{Q}}\left[\,|S_T - F|\,\right] = \frac{C(F,T) + P(F,T)}{DF}
}
\]

This is the **expected absolute deviation** of the terminal stock price from the forward.

---

## 2) Inputs you need (live)

**Required**
- Full option chain for the chosen near-dated expiry:
  - Strikes \(K_i\)
  - Bid/ask (or at least mid) for calls and puts at each strike
- Risk-free rate or discount factor \(DF\) for maturity \(T\)

**Optional (only if you want extra precision)**
- Dividend schedule / implied borrow / funding curve  
  (Note: you can avoid explicit dividends by inferring \(F\) via put-call parity across strikes.)

---

## 3) Step-by-step algorithm (production-grade)

### Step 0 — Choose expiry and compute \(T\)

Let expiration timestamp be \(t_{exp}\) and current time \(t_0\). Compute:

\[
T = \frac{t_{exp}-t_0}{365.0 \text{ or } 365.25}
\]

Use a consistent daycount across your system (many use ACT/365 for equities; ACT/365.25 also common). The key is consistency.

Compute:

\[
DF = e^{-rT}
\]

where \(r\) comes from your short-rate curve/OIS/bills (your choice; be consistent).

---

### Step 1 — Infer the forward \(F\) via put-call parity (robust to dividends)

For each strike \(K\) in a *liquid neighborhood* around spot:

\[
F(K) = K + \frac{C(K)-P(K)}{DF}
\]

Implementation notes:
- Use **mid prices**:
  - \(C_{mid}=(C_{bid}+C_{ask})/2\), same for \(P\)
- Prefer strikes with:
  - tight spreads
  - non-zero bid
  - healthy volume/open interest (if available)
- Compute a **robust aggregate** forward:
  - weighted median of \(\{F(K)\}\) using weight \(w_K = 1/\text{spread}_K\) (or \(1/\text{(spread}^2)\))
  - or simple median across selected strikes

\[
\boxed{
F = \text{weighted-median}\{F(K)\}
}
\]

This avoids needing an explicit dividend forecast and generally behaves well intraday.

---

### Step 2 — Obtain straddle price at \(K=F\) (interpolate to ATM-forward)

You typically do **not** have a listed strike exactly equal to \(F\). Let:
- \(K_L\): largest strike below \(F\)
- \(K_U\): smallest strike above \(F\)

Interpolate call and put **prices** to strike \(F\).

**Linear interpolation on price (robust and model-light):**

\[
C(F) \approx C(K_L) + (F-K_L)\cdot\frac{C(K_U)-C(K_L)}{K_U-K_L}
\]

\[
P(F) \approx P(K_L) + (F-K_L)\cdot\frac{P(K_U)-P(K_L)}{K_U-K_L}
\]

Compute the **PV straddle**:

\[
\text{StraddlePV} = C(F) + P(F)
\]

---

### Step 3 — Convert PV straddle to expected absolute move (dollars)

\[
\boxed{
\text{EAbsMove} = \frac{\text{StraddlePV}}{DF}
= \frac{C(F)+P(F)}{DF}
}
\]

This returns the **risk-neutral expected absolute move** in **dollars** from the forward to expiration:
\[
\text{EAbsMove} = \mathbb{E}^{\mathbb{Q}}\left[|S_T - F|\right]
\]

---

## 4) Outputs you can display in the app

### Primary outputs
- **Expected absolute move (dollars)**:
  \[
  \text{EAbsMove}
  \]
- **Expected absolute move (percent of spot)**:
  \[
  \text{EAbsMovePctSpot} = \frac{\text{EAbsMove}}{S_0}
  \]
- **Expected absolute move (percent of forward)**:
  \[
  \text{EAbsMovePctFwd} = \frac{\text{EAbsMove}}{F}
  \]

### Optional user-facing range (heuristic)
Some apps present an “expected move range” as:

\[
[F-\text{EAbsMove},\;F+\text{EAbsMove}]
\]

**Important:** This is not a statistically calibrated confidence interval. It’s a symmetric band around the forward using expected absolute deviation.

---

## 5) Practical production considerations (US single-name equity options)

US equity options are **American-style** and can exhibit early exercise effects, especially around dividends. The identity above is exact for **European** options; in practice it still works well when you:

### Liquidity / quality filters
- Use near-ATM strikes with tight spreads
- Avoid deep ITM options (largest early-exercise distortions)
- Skip quotes with:
  - crossed/locked markets
  - stale timestamps
  - zero bids (unless you have a policy for them)

### Forward inference robustness
- Compute \(F(K)\) across multiple strikes and use a robust center (median/weighted median)
- Downweight wide spreads / low liquidity

### Dividend caveats
- Around known discrete dividends (especially near expiry), parity can be distorted.
- If you need maximum accuracy around ex-div:
  - incorporate a dividend schedule and price forward explicitly, **or**
  - adjust American quotes to European equivalents (binomial / BAW early-exercise premium), then apply the formula.

For most near-dated “live expected move” use cases, the parity-inferred forward + ATM-forward straddle remains the most stable and “gold standard” implementation.

---

## 6) Minimal pseudocode (implementation outline)

```text
inputs: option_chain(expiry), r, t0, texp

T  = yearfrac(t0, texp)
DF = exp(-r*T)

# 1) infer forward across a set of strikes near spot
F_candidates = []
for K in strikes_near_spot:
    Cmid = (Cbid(K)+Cask(K))/2
    Pmid = (Pbid(K)+Pask(K))/2
    if quote_is_good(Cmid, Pmid, spreads, etc):
        Fk = K + (Cmid - Pmid)/DF
        weight = 1 / (call_spread(K) + put_spread(K))
        store(F_candidates, (Fk, weight))

F = weighted_median(F_candidates)

# 2) interpolate call/put prices to strike F
KL = max{K: K <= F}
KU = min{K: K >= F}

C_F = lin_interp(F, KL, KU, Cmid(KL), Cmid(KU))
P_F = lin_interp(F, KL, KU, Pmid(KL), Pmid(KU))

# 3) expected absolute move
StraddlePV = C_F + P_F
EAbsMove   = StraddlePV / DF

return EAbsMove, EAbsMove/S0, EAbsMove/F
```

---

## 7) Summary

**Gold-standard live expected move (expected absolute move to expiry):**

\[
\boxed{
\text{EAbsMove} = \frac{C(F)+P(F)}{DF}
}
\]

where \(F\) is best inferred live via robust put-call parity across liquid strikes:

\[
\boxed{
F(K)=K+\frac{C(K)-P(K)}{DF},\quad F=\text{robust aggregate of }F(K)
}
\]

This is the cleanest, most defensible “hard-code” expected move for near-dated US single-name options, with minimal modeling assumptions and excellent intraday stability.
