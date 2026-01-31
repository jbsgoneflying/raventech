from __future__ import annotations

import json
import logging
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Optional

import ssl


FMP_BASE_URL = "https://financialmodelingprep.com/stable"


class FmpError(RuntimeError):
    pass


@dataclass(frozen=True)
class FmpResponse:
    rows: list[dict]
    raw: Any


def _env_truthy(name: str) -> bool:
    v = os.getenv(name)
    if v is None:
        return False
    s = str(v).strip().lower()
    return s in ("1", "true", "t", "yes", "y", "on")


def _build_ssl_context() -> ssl.SSLContext:
    # Dev-only escape hatch.
    if os.getenv("FMP_SSL_VERIFY") is not None and not _env_truthy("FMP_SSL_VERIFY"):
        return ssl._create_unverified_context()
    cafile = os.getenv("FMP_CA_BUNDLE") or os.getenv("SSL_CERT_FILE") or os.getenv("REQUESTS_CA_BUNDLE")
    if cafile and os.path.exists(cafile):
        return ssl.create_default_context(cafile=cafile)
    try:
        import certifi  # type: ignore

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _http_get(url: str, params: Dict[str, Any], timeout_s: float) -> tuple[int, Dict[str, str], bytes]:
    q = urllib.parse.urlencode({k: str(v) for k, v in (params or {}).items() if v is not None})
    full = f"{url}?{q}" if q else url
    req = urllib.request.Request(full, method="GET", headers={"Accept": "application/json", "User-Agent": "Breach-Algo/1.0"})
    ctx = _build_ssl_context()
    with urllib.request.urlopen(req, timeout=float(timeout_s), context=ctx) as resp:
        status = int(getattr(resp, "status", 200))
        headers = {str(k): str(v) for k, v in (getattr(resp, "headers", {}) or {}).items()}
        body = resp.read() or b""
        return status, headers, body


class FmpClient:
    """Minimal FMP client for earnings calendar (stable API)."""

    def __init__(self, api_key: str, base_url: str = FMP_BASE_URL, timeout_s: float = 20.0) -> None:
        self._log = logging.getLogger(self.__class__.__name__)
        self._api_key = str(api_key)
        self._base_url = str(base_url).rstrip("/")
        self._timeout_s = float(timeout_s)

    @classmethod
    def from_env(cls) -> "FmpClient":
        key = os.getenv("FMP_API_KEY")
        if not key:
            raise FmpError("Missing required env var FMP_API_KEY")
        logging.getLogger(cls.__name__).info("Loaded FMP_API_KEY from environment (len=%d)", len(key))
        return cls(api_key=key)

    @staticmethod
    def _normalize_rows(data: Any) -> list[dict]:
        if data is None:
            return []
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
        if isinstance(data, dict):
            for key in ("data", "rows", "result", "results"):
                v = data.get(key)
                if isinstance(v, list):
                    return [x for x in v if isinstance(x, dict)]
            if all(isinstance(k, str) for k in data.keys()):
                return [data]
        return []

    def get(self, path: str, params: Dict[str, Any]) -> FmpResponse:
        url = f"{self._base_url}/{path.lstrip('/')}"
        q = dict(params or {})
        q["apikey"] = self._api_key
        status, _headers, body = _http_get(url, q, self._timeout_s)
        try:
            data = json.loads(body.decode("utf-8") or "null")
        except Exception as e:
            raise FmpError(f"FMP returned non-JSON response for {path}: {type(e).__name__}") from e

        if status >= 400:
            snippet = (body.decode("utf-8", errors="ignore") or "")[:500]
            raise FmpError(f"FMP error {status} for {path}: {snippet}")

        rows = self._normalize_rows(data)
        return FmpResponse(rows=rows, raw=data)

    def earnings_calendar(self, *, date_from: str, date_to: str, limit: Optional[int] = None) -> FmpResponse:
        params: Dict[str, Any] = {"from": str(date_from)[:10], "to": str(date_to)[:10]}
        if limit is not None:
            params["limit"] = int(limit)
        return self.get("/earnings-calendar", params)

    def quote_batch(self, symbols: list[str]) -> FmpResponse:
        """Fetch quotes for multiple symbols (includes marketCap)."""
        if not symbols:
            return FmpResponse(rows=[], raw=[])
        # FMP quote endpoint accepts comma-separated symbols
        syms = ",".join([str(s).strip().upper() for s in symbols[:100]])  # Limit to 100
        return self.get(f"/quote/{syms}", {})
    
    def get_market_caps(self, symbols: list[str]) -> Dict[str, float]:
        """Return dict of symbol -> marketCap for given symbols."""
        result: Dict[str, float] = {}
        if not symbols:
            return result
        try:
            # Process in batches of 100
            for i in range(0, len(symbols), 100):
                batch = symbols[i:i+100]
                self._log.info(f"Fetching market caps for batch {i//100 + 1}: {len(batch)} symbols")
                resp = self.quote_batch(batch)
                self._log.info(f"FMP quote response: {len(resp.rows)} rows returned")
                
                # Log sample row to debug structure
                if resp.rows and i == 0:
                    sample = resp.rows[0]
                    self._log.info(f"FMP quote sample fields: {list(sample.keys())}")
                    self._log.info(f"FMP quote sample marketCap: {sample.get('marketCap', 'NOT_PRESENT')}")
                
                for row in resp.rows:
                    sym = str(row.get("symbol") or "").upper()
                    mcap = row.get("marketCap")
                    if sym and mcap is not None:
                        try:
                            result[sym] = float(mcap)
                        except (ValueError, TypeError):
                            pass
            
            self._log.info(f"Market caps loaded: {len(result)}/{len(symbols)} symbols")
        except Exception as e:
            self._log.warning(f"Failed to fetch market caps: {e}", exc_info=True)
        return result


