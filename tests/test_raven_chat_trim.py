"""Regression: large engine payloads must not crash Raven Chat context build."""
from __future__ import annotations

import json

from backend.raven_chat import _trim_engine_data, build_chat_context


def test_trim_engine_data_large_payload_no_crash():
    huge = {"a": "x" * 50000, "b": {"nested": list(range(500))}, "c": [1, 2, 3]}
    out = _trim_engine_data(huge, max_chars=8000)
    assert out is not None
    blob = json.dumps(out, default=str)
    assert len(blob) <= 8000


def test_build_chat_context_large_engine_data():
    engine = {"ticker": "TEST", "rows": [{"x": "y" * 30000}]}
    ctx = build_chat_context("engine1", engine)
    assert "Current Engine Data" in ctx
    assert len(ctx) < 500_000


def test_trim_non_dict():
    out = _trim_engine_data(["a" * 20000], max_chars=500)  # type: ignore[arg-type]
    assert out is not None
    assert "_truncated" in out
