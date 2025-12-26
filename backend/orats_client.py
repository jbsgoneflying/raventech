from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from cachetools import TTLCache
import urllib.parse
import urllib.request


ORATS_BASE_URL = "https://api.orats.io/datav2"
ORATS_LIVE_BASE_URL = "https://api.orats.io/datav2/live"


class OratsError(RuntimeError):
    pass


@dataclass(frozen=True)
class OratsResponse:
    """Normalized ORATS response container.

    ORATS endpoints sometimes return a raw list or an object wrapper; this keeps
    calling code simple by always exposing `.rows` as a list.
    """

    rows: list[dict]
    raw: Any


def _http_get(url: str, params: Dict[str, Any], timeout_s: float) -> tuple[int, Dict[str, str], bytes]:
    q = urllib.parse.urlencode({k: str(v) for k, v in (params or {}).items() if v is not None})
    full = f"{url}?{q}" if q else url
    req = urllib.request.Request(full, method="GET", headers={"Accept": "application/json", "User-Agent": "Breach-Algo/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=float(timeout_s)) as resp:
            status = int(getattr(resp, "status", 200))
            headers = {str(k): str(v) for k, v in (getattr(resp, "headers", {}) or {}).items()}
            body = resp.read() or b""
            return status, headers, body
    except urllib.error.HTTPError as e:  # type: ignore[attr-defined]
        status = int(getattr(e, "code", 500))
        headers = {str(k): str(v) for k, v in (getattr(e, "headers", {}) or {}).items()}
        body = e.read() if hasattr(e, "read") else b""
        return status, headers, body


class OratsClient:
    """ORATS Delayed Data API v2 client with caching + rate-limit awareness."""

    def __init__(
        self,
        token: str,
        base_url: str = ORATS_BASE_URL,
        timeout_s: float = 20.0,
        cache_ttl_s: int = 6 * 60 * 60,
        cache_maxsize: int = 10_000,
        live_cache_ttl_s: int = 10,
        live_cache_maxsize: int = 2_000,
    ) -> None:
        self._log = logging.getLogger(self.__class__.__name__)
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s
        # Compatibility hook for unit tests that monkeypatch `._session.get(...)`.
        # If set, we will use it instead of urllib.
        self._session = None
        self._cache = TTLCache(maxsize=cache_maxsize, ttl=cache_ttl_s)
        self._cache_lock = threading.Lock()
        self._live_base_url = ORATS_LIVE_BASE_URL
        self._live_cache = TTLCache(maxsize=live_cache_maxsize, ttl=live_cache_ttl_s)
        self._live_cache_lock = threading.Lock()

    @classmethod
    def from_env(cls) -> "OratsClient":
        token = os.getenv("ORATS_TOKEN")
        if not token:
            raise OratsError("Missing required env var ORATS_TOKEN")
        logging.getLogger(cls.__name__).info("Loaded ORATS_TOKEN from environment (len=%d)", len(token))
        return cls(token=token)

    def _cache_get(self, key: Tuple[Any, ...]) -> Optional[OratsResponse]:
        with self._cache_lock:
            return self._cache.get(key)

    def _cache_set(self, key: Tuple[Any, ...], value: OratsResponse) -> None:
        with self._cache_lock:
            self._cache[key] = value

    def _live_cache_get(self, key: Tuple[Any, ...]) -> Optional[OratsResponse]:
        with self._live_cache_lock:
            return self._live_cache.get(key)

    def _live_cache_set(self, key: Tuple[Any, ...], value: OratsResponse) -> None:
        with self._live_cache_lock:
            self._live_cache[key] = value

    def get(self, path: str, params: Dict[str, Any]) -> OratsResponse:
        """GET an ORATS v2 endpoint under /datav2.

        Adds token query param (never returned to frontend).
        Caches the normalized response.
        """
        # cache key should not include token
        key = ("GET", path, tuple(sorted((k, str(v)) for k, v in params.items())))
        cached = self._cache_get(key)
        if cached is not None:
            return cached

        url = f"{self._base_url}/{path.lstrip('/')}"
        q = dict(params)
        q["token"] = self._token

        # Extra rate-limit friendliness: if ORATS returns 429 without Retry-After,
        # do a small manual backoff and retry a couple times.
        last_err: Optional[Exception] = None
        for attempt in range(1, 4):
            try:
                # Prefer a monkeypatched session if present (tests).
                sess = getattr(self, "_session", None)
                if sess is not None and callable(getattr(sess, "get", None)):
                    resp = sess.get(url, params=q, timeout=self._timeout_s)
                    status = int(getattr(resp, "status_code", 0) or 0)
                    headers = getattr(resp, "headers", {}) or {}
                    try:
                        data = resp.json()
                    except Exception:
                        data = {}
                    body = (getattr(resp, "text", "") or "").encode("utf-8")
                else:
                    status, headers, body = _http_get(url, q, self._timeout_s)
                    data = None
                if status == 429:
                    retry_after = headers.get("Retry-After")
                    sleep_s = float(retry_after) if retry_after else min(2.0 * attempt, 6.0)
                    self._log.warning("ORATS 429 rate-limited; sleeping %.1fs (attempt %d/3)", sleep_s, attempt)
                    time.sleep(sleep_s)
                    continue

                # For certain hist endpoints, ORATS returns 404 on dates with no data (weekends/holidays).
                # We treat that as "empty result" so callers can probe for the nearest trading day.
                if status == 404 and path in ("/hist/dailies", "/hist/cores", "/hist/monies/implied", "/hist/strikes"):
                    try:
                        if data is None:
                            data = json.loads(body.decode("utf-8") or "{}")
                    except Exception:
                        data = {"data": []}
                    # Force empty rows even if the body is a dict like {"message":"Not Found."}
                    out = OratsResponse(rows=[], raw=data)
                    self._cache_set(key, out)
                    return out

                # Auth / entitlement errors should not be retried.
                if status in (401, 403):
                    snippet = (body.decode("utf-8", errors="ignore") or "")[:500]
                    raise OratsError(f"ORATS auth/entitlement error {status} for {path}: {snippet}")

                if status >= 400:
                    # include a small response snippet to help debugging
                    snippet = (body.decode("utf-8", errors="ignore") or "")[:500]
                    raise OratsError(f"ORATS error {status} for {path}: {snippet}")

                if data is None:
                    data = json.loads(body.decode("utf-8") or "{}")
                rows = self._normalize_rows(data)
                out = OratsResponse(rows=rows, raw=data)
                self._cache_set(key, out)
                return out
            except (Exception, OratsError) as e:
                last_err = e
                # Don't retry auth/entitlement errors.
                if isinstance(e, OratsError) and ("auth/entitlement error 401" in str(e) or "auth/entitlement error 403" in str(e)):
                    raise
                # brief exponential backoff; urllib3 Retry already does some, but this
                # helps for JSON decode edge cases and intermittent failures
                time.sleep(min(0.5 * (2**(attempt - 1)), 3.0))

        raise OratsError(f"Failed ORATS request after retries: {path} params={params}") from last_err

    def get_live(self, path: str, params: Dict[str, Any]) -> OratsResponse:
        """GET an ORATS live endpoint under /datav2/live with short-TTL caching."""
        key = ("GET_LIVE", path, tuple(sorted((k, str(v)) for k, v in params.items())))
        cached = self._live_cache_get(key)
        if cached is not None:
            return cached

        url = f"{self._live_base_url}/{path.lstrip('/')}"
        q = dict(params)
        q["token"] = self._token

        last_err: Optional[Exception] = None
        for attempt in range(1, 4):
            try:
                sess = getattr(self, "_session", None)
                if sess is not None and callable(getattr(sess, "get", None)):
                    resp = sess.get(url, params=q, timeout=self._timeout_s)
                    status = int(getattr(resp, "status_code", 0) or 0)
                    headers = getattr(resp, "headers", {}) or {}
                    try:
                        data = resp.json()
                    except Exception:
                        data = {}
                    body = (getattr(resp, "text", "") or "").encode("utf-8")
                else:
                    status, headers, body = _http_get(url, q, self._timeout_s)
                    data = None
                if status == 429:
                    retry_after = headers.get("Retry-After")
                    sleep_s = float(retry_after) if retry_after else min(2.0 * attempt, 6.0)
                    self._log.warning("ORATS live 429 rate-limited; sleeping %.1fs (attempt %d/3)", sleep_s, attempt)
                    time.sleep(sleep_s)
                    continue

                if status in (401, 403):
                    snippet = (body.decode("utf-8", errors="ignore") or "")[:500]
                    raise OratsError(f"ORATS auth/entitlement error {status} for LIVE {path}: {snippet}")

                if status >= 400:
                    snippet = (body.decode("utf-8", errors="ignore") or "")[:500]
                    raise OratsError(f"ORATS error {status} for LIVE {path}: {snippet}")

                if data is None:
                    data = json.loads(body.decode("utf-8") or "{}")
                rows = self._normalize_rows(data)
                out = OratsResponse(rows=rows, raw=data)
                self._live_cache_set(key, out)
                return out
            except (Exception, OratsError) as e:
                last_err = e
                if isinstance(e, OratsError) and ("auth/entitlement error 401" in str(e) or "auth/entitlement error 403" in str(e)):
                    raise
                time.sleep(min(0.5 * (2**(attempt - 1)), 3.0))

        raise OratsError(f"Failed ORATS LIVE request after retries: {path} params={params}") from last_err

    @staticmethod
    def _normalize_rows(data: Any) -> list[dict]:
        # ORATS sometimes returns:
        # - a list of dicts
        # - an object wrapper like {"data": [...]} or {"rows": [...]}
        if data is None:
            return []
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
        if isinstance(data, dict):
            for key in ("data", "rows", "result", "results"):
                v = data.get(key)
                if isinstance(v, list):
                    return [x for x in v if isinstance(x, dict)]
            # if it's a single-row dict, wrap it
            if all(isinstance(k, str) for k in data.keys()):
                return [data]
        return []

    # Convenience wrappers for the three required endpoints
    def hist_earnings(self, ticker: str) -> OratsResponse:
        return self.get("/hist/earnings", {"ticker": ticker})

    # Snapshot cores endpoint (no tradeDate). Used for forward-looking earnings calendar fields like nextErn/nextErnTod.
    def cores(self, *, ticker: str, fields: str) -> OratsResponse:
        return self.get("/cores", {"ticker": ticker, "fields": fields})

    def hist_cores(self, ticker: str, trade_date: str, fields: str) -> OratsResponse:
        return self.get("/hist/cores", {"ticker": ticker, "tradeDate": trade_date, "fields": fields})

    def hist_dailies(self, ticker: str, trade_date: str, fields: str) -> OratsResponse:
        return self.get("/hist/dailies", {"ticker": ticker, "tradeDate": trade_date, "fields": fields})

    def hist_strikes(
        self,
        *,
        ticker: str,
        trade_date: str,
        fields: str,
        dte: str | None = None,
        delta: str | None = None,
    ) -> OratsResponse:
        params: Dict[str, Any] = {"ticker": ticker, "tradeDate": str(trade_date)[:10], "fields": fields}
        if dte:
            params["dte"] = dte  # format: "lo,hi"
        if delta:
            params["delta"] = delta  # format: "lo,hi"
        return self.get("/hist/strikes", params)

    def hist_monies_implied(
        self,
        *,
        ticker: str,
        trade_date: str,
        fields: str,
        dte: str | None = None,
    ) -> OratsResponse:
        params: Dict[str, Any] = {"ticker": ticker, "tradeDate": trade_date, "fields": fields}
        if dte:
            params["dte"] = dte  # format: "lo,hi"
        return self.get("/hist/monies/implied", params)

    # --- Live Data API wrappers ---
    def live_expirations(self, *, ticker: str) -> OratsResponse:
        # Docs: GET /datav2/live/expirations?ticker=...
        return self.get_live("/expirations", {"ticker": ticker})

    def live_strikes(
        self,
        *,
        ticker: str,
        fields: str | None = None,
    ) -> OratsResponse:
        params: Dict[str, Any] = {"ticker": ticker}
        if fields:
            params["fields"] = fields
        return self.get_live("/strikes", params)

    def live_strikes_by_expiry(
        self,
        *,
        ticker: str,
        expiry: str,
        fields: str | None = None,
    ) -> OratsResponse:
        # Docs: GET /datav2/live/strikes/monthly?ticker=...&expiry=YYYY-MM-DD,YYYY-MM-DD
        exp = str(expiry).strip()
        # ORATS docs sometimes show a comma-separated expiry range. Make this robust by
        # expanding a single YYYY-MM-DD into "YYYY-MM-DD,YYYY-MM-DD".
        if exp and ("," not in exp):
            exp = f"{exp[:10]},{exp[:10]}"
        params: Dict[str, Any] = {"ticker": ticker, "expiry": exp}
        if fields:
            params["fields"] = fields
        return self.get_live("/strikes/monthly", params)

    def live_summaries(self, *, ticker: str, fields: str | None = None) -> OratsResponse:
        # Docs list a Live "Summaries" endpoint. We keep fields optional for robustness.
        params: Dict[str, Any] = {"ticker": ticker}
        if fields:
            params["fields"] = fields
        return self.get_live("/summaries", params)

    # --- Skew scaffolding (Phase 4) ---
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
        Placeholder for a volatility-surface / delta-slice skew fetch.

        Expected (future) return shape:
          {("C", 25): iv, ("P", 25): iv, ("C", 10): iv, ("P", 10): iv, "atm": iv}

        This repo currently does not include ORATS endpoint knowledge for skew.
        Callers must treat this as optional and degrade safely.
        """
        # Implementation using ORATS "monies implied" surface snapshot.
        # ORATS provides seed vols keyed by CALL delta (vol10, vol25, vol50, vol75, vol90, ...).
        # We map:
        # - 25Δ put ≈ vol75 (75 call-delta)
        # - 10Δ put ≈ vol90 (90 call-delta)
        use_deltas = deltas or [10, 25]
        use_rights = rights or ["C", "P"]

        lo = max(1, int(dte_target) - 2)
        hi = int(dte_target) + 7
        fields = "ticker,tradeDate,expirDate,dte,stockPrice,vol10,vol25,vol50,vol75,vol90"
        # During market hours ORATS EOD for "today" may be unavailable. Deterministically
        # step back a few calendar days to find the most recent surface row.
        rows: list[dict] = []
        used_trade_date = str(trade_date)[:10]
        for step in range(0, 7):  # today .. 6 days back
            td = used_trade_date
            if step > 0:
                try:
                    import datetime as dt

                    base = dt.date.fromisoformat(str(used_trade_date)[:10])
                    td = (base - dt.timedelta(days=step)).isoformat()
                except Exception:
                    td = used_trade_date
            resp = self.hist_monies_implied(
                ticker=ticker,
                trade_date=str(td)[:10],
                fields=fields,
                dte=f"{lo},{hi}",
            )
            rows = resp.rows or []
            if rows:
                used_trade_date = str(td)[:10]
                break
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
            "asOfDate": str(best.get("tradeDate") or str(used_trade_date)[:10])[:10],
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


