import types
import time

import pytest

from backend.askraven import askraven_agent_chat


class _FakeResp:
    def __init__(self, *, output_text: str = "", output: list | None = None, rid: str = "r1"):
        self.output_text = output_text
        self.id = rid
        self._output = output or []

    def model_dump(self):
        return {"id": self.id, "output": self._output}


class _FakeResponses:
    def __init__(self, calls_out: list):
        self._calls_out = calls_out
        self._n = 0

    def create(self, **kwargs):
        self._calls_out.append(kwargs)
        self._n += 1
        # First call: request a tool call
        if self._n == 1:
            return _FakeResp(
                output_text="",
                rid="r1",
                output=[
                    {
                        "type": "function_call",
                        "name": "orats_get_live_spot",
                        "arguments": {"ticker": "SPX"},
                        "call_id": "tc1",
                    }
                ],
            )
        # Second call: after tool_result, return final text
        return _FakeResp(output_text="final answer", rid="r2", output=[])


class _FakeOpenAI:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.calls = []
        self.responses = _FakeResponses(self.calls)


class _FakeOrats:
    def live_summaries(self, *, ticker: str, fields: str | None = None):
        class R:
            rows = [{"spotPrice": 6923.0, "stockPrice": 6923.0, "tradeDate": "2025-12-26"}]

        return R()


def test_agent_loop_executes_function_tool_and_returns_final_text(monkeypatch):
    fake_openai = types.SimpleNamespace(OpenAI=_FakeOpenAI)
    monkeypatch.setitem(__import__("sys").modules, "openai", fake_openai)
    monkeypatch.setenv("OPENAI_API_KEY", "x" * 10)
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5.2")
    monkeypatch.setenv("ASKRAVEN_ENABLE_ORATS_TOOLS", "1")
    monkeypatch.setenv("ASKRAVEN_ENABLE_BENZINGA_TOOLS", "0")
    monkeypatch.setenv("ASKRAVEN_ENABLE_WEB", "0")
    monkeypatch.setenv("ASKRAVEN_BUDGET", "tight")

    out = askraven_agent_chat(
        question="What is spot?",
        context_pack={"engine": "engine2", "current": {}, "liveContext": {}, "technicals": {}},
        images=[],
        orats_client=_FakeOrats(),
        benzinga_client=None,
    )
    assert out == "final answer"


def test_agent_loop_chat_completions_fallback_when_no_responses(monkeypatch):
    # Fake OpenAI client without `responses`, but with chat.completions tool calling.
    calls = {"n": 0}

    class _ToolCall:
        def __init__(self):
            self.id = "tc1"

            class Fn:
                name = "orats_get_live_spot"
                arguments = '{"ticker":"SPX"}'

            self.function = Fn()

    class _Msg:
        def __init__(self, content, tool_calls=None):
            self.content = content
            self.tool_calls = tool_calls

    class _Resp:
        def __init__(self, msg):
            self.choices = [types.SimpleNamespace(message=msg)]

    class _ChatCompletions:
        def create(self, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return _Resp(_Msg("", tool_calls=[_ToolCall()]))
            return _Resp(_Msg("final answer", tool_calls=None))

    class _Chat:
        completions = _ChatCompletions()

    class _OpenAI_NoResponses:
        def __init__(self, api_key: str):
            self.api_key = api_key
            self.chat = _Chat()

    fake_openai = types.SimpleNamespace(OpenAI=_OpenAI_NoResponses)
    monkeypatch.setitem(__import__("sys").modules, "openai", fake_openai)
    monkeypatch.setenv("OPENAI_API_KEY", "x" * 10)
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5.2")
    monkeypatch.setenv("ASKRAVEN_ENABLE_ORATS_TOOLS", "1")
    monkeypatch.setenv("ASKRAVEN_ENABLE_BENZINGA_TOOLS", "0")
    monkeypatch.setenv("ASKRAVEN_ENABLE_WEB", "1")
    monkeypatch.setenv("ASKRAVEN_BUDGET", "tight")

    out = askraven_agent_chat(
        question="Use tools",
        context_pack={"engine": "engine2", "current": {}, "liveContext": {}, "technicals": {}},
        images=[],
        orats_client=_FakeOrats(),
        benzinga_client=None,
    )
    assert out == "final answer"


def test_agent_loop_budget_exhaustion(monkeypatch):
    # Force the loop to immediately exceed time budget by setting wall_s tiny.
    fake_openai = types.SimpleNamespace(OpenAI=_FakeOpenAI)
    monkeypatch.setitem(__import__("sys").modules, "openai", fake_openai)
    monkeypatch.setenv("OPENAI_API_KEY", "x" * 10)
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5.2")
    monkeypatch.setenv("ASKRAVEN_BUDGET", "tight")

    # monkeypatch time.time to simulate elapsed time inside askraven_agent_chat
    t0 = time.time()
    seq = [t0, t0 + 999]

    def _fake_time():
        return seq.pop(0) if seq else t0 + 999

    monkeypatch.setattr(time, "time", _fake_time)

    out = askraven_agent_chat(
        question="Anything",
        context_pack={"engine": "engine2", "current": {}, "liveContext": {}, "technicals": {}},
        images=[],
        orats_client=_FakeOrats(),
        benzinga_client=None,
    )
    assert "budget exhausted" in out.lower()


