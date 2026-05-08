"""Counterfactual logger sidecar.

Phase 0 stub: exposes a fire-and-forget API that v1 (or v2 itself) can call
with paired inputs/outputs. Every payload lands on a Redis stream
``v2:counterfactual`` for later analysis.

Once the v2 engines are real, the v1 routes will tee every request through
this logger so we accumulate a clean v1-vs-v2 dataset before the desk ever
sees v2 verdicts.

Schema (per stream entry)::

    {
        "ts": ISO8601,
        "engine": "e1" | "e2" | "e14" | "e15" | "mi",
        "request_id": <uuid>,
        "v1_verdict": <free-form dict>,
        "v2_verdict": <free-form dict>,
        "agree": bool,
        "delta_summary": <optional str describing the disagreement>,
    }
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from typing import Any, Mapping

LOG = logging.getLogger("v2.counterfactual")


def _redis_client():
    """Lazy-import redis so v2 boots even if redis is unavailable in dev."""
    try:
        import redis  # type: ignore
    except ImportError:
        return None
    url = os.getenv("REDIS_URL", "redis://redis:6379/0")
    try:
        return redis.from_url(url, decode_responses=True, socket_connect_timeout=2.0)
    except Exception as exc:
        LOG.warning("redis client init failed: %s", exc)
        return None


def log_counterfactual(
    engine: str,
    v1_verdict: Mapping[str, Any] | None,
    v2_verdict: Mapping[str, Any] | None,
    *,
    stream_name: str = "v2:counterfactual",
    request_id: str | None = None,
    delta_summary: str | None = None,
    max_stream_len: int = 50_000,
) -> str | None:
    """Write a counterfactual entry to the Redis stream.

    Returns the stream entry ID if it landed, else None.
    """
    rid = request_id or str(uuid.uuid4())
    agree = _shallow_agree(v1_verdict, v2_verdict)
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "engine": str(engine or "unknown"),
        "request_id": rid,
        "v1_verdict": json.dumps(v1_verdict or {}, default=str)[:8000],
        "v2_verdict": json.dumps(v2_verdict or {}, default=str)[:8000],
        "agree": "1" if agree else "0",
        "delta_summary": delta_summary or "",
    }

    client = _redis_client()
    if client is None:
        LOG.info("counterfactual (no redis): engine=%s agree=%s", engine, agree)
        return None
    try:
        sid = client.xadd(stream_name, entry, maxlen=max_stream_len, approximate=True)
        return str(sid)
    except Exception as exc:
        LOG.warning("xadd failed for %s: %s", stream_name, exc)
        return None


def _shallow_agree(a: Mapping[str, Any] | None, b: Mapping[str, Any] | None) -> bool:
    """Best-effort agreement check on a verdict-shaped dict.

    We compare a small set of well-known keys (``verdict``, ``stance``,
    ``recommendation``) case-insensitively. Anything else is treated as
    "agree" so the logger isn't noisy by default.
    """
    if not a or not b:
        return a == b
    keys = ("verdict", "stance", "recommendation", "go", "label")
    for k in keys:
        if k in a and k in b:
            return str(a.get(k)).lower() == str(b.get(k)).lower()
    return True
