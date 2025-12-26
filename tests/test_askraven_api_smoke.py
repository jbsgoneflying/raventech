from fastapi.testclient import TestClient

import backend.app as app_mod
class FakeStore:
    def __init__(self):
        self._m = {}

    def set_json(self, key: str, value, ttl_s: int = 0) -> bool:
        self._m[str(key)] = value
        return True

    def get_json(self, key: str):
        return self._m.get(str(key))


def test_chat_message_uses_latest_report_and_mocked_llm(monkeypatch):
    # Arrange: fake redis store with a minimal engine2 report
    store = FakeStore()
    store.set_json(
        "latest_report:engine2",
        {
            "asOfDate": "2025-12-26",
            "params": {"entryDay": "mon"},
            "underlying": {"symbol": "SPX", "isProxy": False},
            "oddsLikeNow": {"regimeBucket": "MODERATE", "macroBucket": "NORMAL", "seasonBucket": "ALL", "weeksUsed": 10, "byWidth": []},
            "technicals": {"enabled": True, "ema": {"ema21": 5000.0}, "livePrice": 5050.0},
        },
    )
    monkeypatch.setattr(app_mod, "_store", store, raising=False)

    # Mock agent call (app.py imports askraven_agent_chat into its own module namespace)
    monkeypatch.setattr(app_mod, "askraven_agent_chat", lambda **kw: "mocked reply", raising=False)

    client = TestClient(app_mod.app)
    r = client.post("/api/chat/message", json={"engine": "engine2", "message": "what are odds?", "image_ids": []})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["reply"] == "mocked reply"


def test_chat_upload_rejects_non_image(monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(app_mod, "_store", store, raising=False)
    client = TestClient(app_mod.app)
    r = client.post(
        "/api/chat/upload",
        files={"files": ("x.txt", b"not an image", "text/plain")},
    )
    assert r.status_code == 400


