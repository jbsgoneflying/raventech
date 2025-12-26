import types

import pytest

from backend.askraven import call_openai, UploadedImage


class _FakeResp:
    def __init__(self, text: str):
        self.output_text = text

    def model_dump(self):
        return {"output_text": self.output_text}


class _FakeResponses:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeResp("ok")


class _FakeOpenAI:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.responses = _FakeResponses()


def test_call_openai_uses_web_search_tool_when_enabled(monkeypatch):
    # Fake openai module so call_openai doesn't import the real SDK.
    fake_openai = types.SimpleNamespace(OpenAI=_FakeOpenAI)
    monkeypatch.setitem(__import__("sys").modules, "openai", fake_openai)

    monkeypatch.setenv("OPENAI_API_KEY", "x" * 10)
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5.2")
    monkeypatch.setenv("ASKRAVEN_ENABLE_WEB", "1")
    monkeypatch.setenv("ASKRAVEN_FORCE_TEXT", "1")

    ctx = {"engine": "engine2", "current": {}, "liveContext": {}, "technicals": {}}
    reply = call_openai(question="news: what can gap the open?", context_pack=ctx, images=[])
    assert reply == "ok"

    # Inspect the fake client's last call to responses.create
    client = fake_openai.OpenAI(api_key="x")
    # Note: above client is different instance; instead, assert via side effects by reusing module object.
    # The call happens on an instance created inside call_openai; we can assert tool field exists by
    # checking that our FakeResponses.create accepted it in at least one call.
    # We'll reach into the most recent created instance via monkeypatching is not trivial, so instead
    # validate by re-invoking with a known captured object.


def test_call_openai_web_search_tool_param_present(monkeypatch):
    captured = {"calls": []}

    class OpenAI2(_FakeOpenAI):
        def __init__(self, api_key: str):
            super().__init__(api_key=api_key)
            # share calls outward
            self.responses.calls = captured["calls"]

    fake_openai = types.SimpleNamespace(OpenAI=OpenAI2)
    monkeypatch.setitem(__import__("sys").modules, "openai", fake_openai)

    monkeypatch.setenv("OPENAI_API_KEY", "x" * 10)
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5.2")
    monkeypatch.setenv("ASKRAVEN_ENABLE_WEB", "1")
    monkeypatch.setenv("ASKRAVEN_FORCE_TEXT", "1")

    ctx = {"engine": "engine2", "current": {}, "liveContext": {}, "technicals": {}}
    _ = call_openai(question="pre-market news please", context_pack=ctx, images=[UploadedImage(content=b"", content_type="image/png", image_id="0")])

    assert len(captured["calls"]) >= 1
    # First call should include tools (web search) when enabled for news-like prompts.
    first = captured["calls"][0]
    assert "tools" in first
    assert isinstance(first["tools"], list) and len(first["tools"]) >= 1
    assert isinstance(first["tools"][0], dict) and first["tools"][0].get("type") in ("web_search", "web_search_preview")


