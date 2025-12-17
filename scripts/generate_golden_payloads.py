from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.earnings_logic import compute_breach_stats
from backend.orats_client import OratsClient, OratsResponse


FIXTURES_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "golden"


def _stable_params(params: Dict[str, Any]) -> List[List[str]]:
    return [[str(k), str(v)] for (k, v) in sorted((k, str(v)) for k, v in params.items())]


class RecordingClient:
    """
    Record ORATS responses (live once) into a tape file so tests can replay deterministically without network.
    """

    def __init__(self, inner: OratsClient) -> None:
        self._inner = inner
        self.tape: Dict[str, Any] = {"calls": []}

    def get(self, path: str, params: Dict[str, Any]) -> OratsResponse:
        resp = self._inner.get(path, params)
        entry = {
            "path": str(path),
            "params": _stable_params(params),
            "rows": resp.rows,
        }
        self.tape["calls"].append(entry)
        return resp

    # Minimal wrappers used by the app
    def hist_earnings(self, ticker: str) -> OratsResponse:
        return self.get("/hist/earnings", {"ticker": ticker})

    def hist_cores(self, ticker: str, trade_date: str, fields: str) -> OratsResponse:
        return self.get("/hist/cores", {"ticker": ticker, "tradeDate": trade_date, "fields": fields})

    def hist_dailies(self, ticker: str, trade_date: str, fields: str) -> OratsResponse:
        return self.get("/hist/dailies", {"ticker": ticker, "tradeDate": trade_date, "fields": fields})

    def hist_strikes(self, *, ticker: str, trade_date: str, fields: str, dte: str | None = None, delta: str | None = None) -> OratsResponse:
        params: Dict[str, Any] = {"ticker": ticker, "tradeDate": str(trade_date)[:10], "fields": fields}
        if dte:
            params["dte"] = dte
        if delta:
            params["delta"] = delta
        return self.get("/hist/strikes", params)

    def hist_monies_implied(self, *, ticker: str, trade_date: str, fields: str, dte: str | None = None) -> OratsResponse:
        params: Dict[str, Any] = {"ticker": ticker, "tradeDate": trade_date, "fields": fields}
        if dte:
            params["dte"] = dte
        return self.get("/hist/monies/implied", params)

    def get_skew_by_delta(
        self,
        *,
        ticker: str,
        trade_date: str,
        dte_target: int,
        deltas: list[int] | None = None,
        rights: list[str] | None = None,
    ) -> dict:
        """
        Copy of the current `OratsClient.get_skew_by_delta` logic, but routed through this wrapper
        so the underlying ORATS calls are captured in the tape.
        """
        use_deltas = deltas or [10, 25]
        use_rights = rights or ["C", "P"]

        lo = max(1, int(dte_target) - 2)
        hi = int(dte_target) + 7
        fields = "ticker,tradeDate,expirDate,dte,stockPrice,vol10,vol25,vol50,vol75,vol90"
        resp = self.hist_monies_implied(
            ticker=ticker,
            trade_date=str(trade_date)[:10],
            fields=fields,
            dte=f"{lo},{hi}",
        )
        rows = resp.rows or []
        if not rows:
            return {}

        def _to_float(v: Any) -> Optional[float]:
            try:
                if v is None:
                    return None
                f = float(v)
                if f != f:  # NaN
                    return None
                return f
            except (TypeError, ValueError):
                return None

        best = None
        best_dist = None
        for r in rows:
            dte_val = _to_float(r.get("dte"))
            if dte_val is None:
                continue
            dist = abs(dte_val - float(dte_target))
            if best is None or best_dist is None or dist < best_dist:
                best = r
                best_dist = dist
        if best is None:
            best = rows[0]

        vol10 = _to_float(best.get("vol10"))
        vol25 = _to_float(best.get("vol25"))
        vol50 = _to_float(best.get("vol50") or best.get("atmiv"))
        vol75 = _to_float(best.get("vol75"))
        vol90 = _to_float(best.get("vol90"))

        out: Dict[Any, Any] = {
            "asOfDate": str(best.get("tradeDate") or str(trade_date)[:10])[:10],
            "expirDate": str(best.get("expirDate") or "")[:10] if best.get("expirDate") else None,
            "dte": _to_float(best.get("dte")),
            "stockPrice": _to_float(best.get("stockPrice") or best.get("spotPrice")),
            "atm": vol50,
        }

        def _set(right: str, delta: int, v: Optional[float]) -> None:
            if v is None:
                return
            out[(right, int(delta))] = v

        for d in use_deltas:
            if int(d) == 25:
                if "C" in use_rights:
                    _set("C", 25, vol25)
                if "P" in use_rights:
                    _set("P", 25, vol75)
            if int(d) == 10:
                if "C" in use_rights:
                    _set("C", 10, vol10)
                if "P" in use_rights:
                    _set("P", 10, vol90)

        return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", nargs="+", default=["MU", "NKE"])
    parser.add_argument("--n", type=int, default=20)
    parser.add_argument("--years", type=int, default=5)
    parser.add_argument("--k", type=float, default=1.0)
    parser.add_argument("--today", type=str, default="2025-03-01", help="Pinned today date (YYYY-MM-DD) for deterministic fixtures.")
    args = parser.parse_args()

    # Allow using a local `.env` without exposing secrets in code or fixtures.
    # (CI should rely only on the committed tapes/payloads, not on live ORATS calls.)
    load_dotenv()

    # Ensure flags OFF (and audit telemetry explicitly pinned) for the golden baseline.
    os.environ["STRICT_REALIZED_WINDOW"] = "false"
    os.environ["USE_BETA_POSTERIOR_FOR_DECISIONING"] = "false"
    os.environ["USE_BETA_CI_FOR_CONFIDENCE"] = "false"
    os.environ["BETA_PRIOR_ALPHA"] = "1.0"
    os.environ["BETA_PRIOR_BETA"] = "1.0"
    os.environ["ADD_K_CONSISTENT_OVERSHOOT"] = "false"
    os.environ["TRADEBUILDER_ENFORCE_OTM"] = "false"
    os.environ["ADD_EVENT_SHIFT_TELEMETRY"] = "true"

    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    today = dt.date.fromisoformat(args.today)

    inner = OratsClient.from_env()
    for t in args.tickers:
        ticker = str(t).strip().upper()
        client = RecordingClient(inner)
        payload = compute_breach_stats(client=client, ticker=ticker, n=args.n, years=args.years, k=args.k, today=today)

        payload_path = FIXTURES_DIR / f"{ticker}.payload.json"
        tape_path = FIXTURES_DIR / f"{ticker}.tape.json"

        payload_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tape_path.write_text(json.dumps(client.tape, indent=2, sort_keys=True), encoding="utf-8")
        print(f"Wrote {payload_path} and {tape_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())


