from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from backend.technicals import encode_image_to_data_url


def _pick(d: dict, keys: List[str]) -> dict:
    out = {}
    for k in keys:
        if k in d:
            out[k] = d.get(k)
    return out


def build_context_pack(*, engine: str, report: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build a compact, stable context pack for the LLM.
    We intentionally avoid dumping the entire report by default.
    """
    eng = str(engine)
    r = report or {}
    if not isinstance(r, dict):
        return {"engine": eng, "notes": ["Invalid report payload (not a dict)."]}

    if eng == "engine1":
        return {
            "engine": "engine1",
            "ticker": r.get("ticker"),
            "params": r.get("params"),
            "summary": r.get("summary"),
            "current": r.get("current"),
            "regime": r.get("regime"),
            "quarters": r.get("quarters"),
            "wingRecommendation": r.get("wingRecommendation"),
            "eventRisk": r.get("eventRisk"),
            "marketDealerGamma": r.get("marketDealerGamma"),
            "tickerDealerGamma": r.get("tickerDealerGamma"),
            "technicals": r.get("technicals"),
            "notes": [
                "Engine 1 context: earnings breach history + regime + overlays + technicals.",
                "When answering, cite numeric fields from this context; ask for missing trade specifics when needed (strikes/expiry/credit).",
            ],
        }

    if eng == "engine2":
        # Avoid huge grid payloads; keep only high-signal
        odds_like_now = r.get("oddsLikeNow") if isinstance(r.get("oddsLikeNow"), dict) else {}
        backtest = r.get("backtest") if isinstance(r.get("backtest"), dict) else {}
        return {
            "engine": "engine2",
            "asOfDate": r.get("asOfDate"),
            "params": r.get("params"),
            "underlying": r.get("underlying"),
            "current": r.get("current"),
            "liveContext": r.get("liveContext"),
            "oddsLikeNow": _pick(odds_like_now, ["regimeBucket", "macroBucket", "seasonBucket", "weeksUsed", "byWidth", "notes"]),
            "backtest": _pick(backtest, ["rowsUsed", "byWidth", "byQuarter", "notes"]),
            "technicals": r.get("technicals"),
            "notes": [
                "Engine 2 context: weekly expiry breach odds conditioned on regime/macro/season + live gamma context + technicals.",
                "Risk-only: no credit/PnL model unless provided by user.",
            ],
        }

    return {"engine": eng, "notes": ["Unknown engine."]}


ASKRAVEN_SYSTEM_PROMPT = """You are AskRaven, a rigorous, skeptical quant trading assistant.\n\nHard rules:\n- Ground your answer in the provided RavenTech context pack. If a number is not in the context, say so.\n- If a question depends on missing trade inputs (credit, exact expiry, wing width, underlying proxy), ask concise clarifying questions.\n- Distinguish between:\n  (1) historical odds from the engines,\n  (2) live/informational overlays (dealer gamma, live price), and\n  (3) outside reasoning.\n- No hallucinated data. No fabricated citations.\n\nOutput style:\n- Prefer bullet points.\n- Include a short \"Key numbers\" section when possible.\n- Include a short \"What would change my view\" section.\n\nCompliance:\n- Educational / risk analysis only; not financial advice.\n""".strip()


@dataclass(frozen=True)
class UploadedImage:
    content: bytes
    content_type: str
    image_id: str


def call_openai(
    *,
    question: str,
    context_pack: Dict[str, Any],
    images: List[UploadedImage],
) -> str:
    """
    Calls OpenAI Responses API. Pinned to GPT-5.2 by default.
    """
    api_key = str(os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("Missing OPENAI_API_KEY on server.")

    # Lazy import so tests can monkeypatch without requiring network.
    from openai import OpenAI  # type: ignore

    client = OpenAI(api_key=api_key)

    model = str(os.getenv("OPENAI_MODEL") or "gpt-5.2").strip()
    max_out = int(float(os.getenv("ASKRAVEN_MAX_OUTPUT_TOKENS") or 900))
    effort = str(os.getenv("OPENAI_REASONING_EFFORT") or "auto").strip().lower()

    ctx_txt = json.dumps(context_pack, ensure_ascii=False, separators=(",", ":"), indent=2)
    user_parts: List[Dict[str, Any]] = [
        {"type": "text", "text": f"RavenTech context pack:\n{ctx_txt}\n\nUser question:\n{str(question or '').strip()}"}
    ]
    for img in images or []:
        try:
            url = encode_image_to_data_url(content=img.content, content_type=img.content_type)
            user_parts.append({"type": "input_image", "image_url": url})
        except Exception:
            continue

    # Prefer Responses API if available (new SDK); otherwise fall back to Chat Completions.
    try:
        if hasattr(client, "responses") and getattr(client, "responses") is not None:
            # Prefer reasoning control when available; fall back if the SDK/model rejects it.
            try:
                resp = client.responses.create(
                    model=model,
                    input=[
                        {"role": "system", "content": [{"type": "text", "text": ASKRAVEN_SYSTEM_PROMPT}]},
                        {"role": "user", "content": user_parts},
                    ],
                    max_output_tokens=max_out,
                    reasoning={"effort": effort},
                )
            except Exception:
                resp = client.responses.create(
                    model=model,
                    input=[
                        {"role": "system", "content": [{"type": "text", "text": ASKRAVEN_SYSTEM_PROMPT}]},
                        {"role": "user", "content": user_parts},
                    ],
                    max_output_tokens=max_out,
                )

            out = getattr(resp, "output_text", None)
            if isinstance(out, str) and out.strip():
                return out.strip()

            try:
                raw = resp.model_dump() if hasattr(resp, "model_dump") else {}
            except Exception:
                raw = {}
            return json.dumps(raw, ensure_ascii=False)[:4000]
    except AttributeError:
        # Older SDKs may not expose `responses`; fall through to chat.completions.
        pass

    # Chat Completions fallback (older SDK compatibility)
    chat_messages: List[Dict[str, Any]] = [
        {"role": "system", "content": ASKRAVEN_SYSTEM_PROMPT},
        {"role": "user", "content": [{"type": "text", "text": user_parts[0]["text"]}]},
    ]
    # Append images for vision-capable models (if supported).
    img_parts = []
    for img in images or []:
        try:
            url = encode_image_to_data_url(content=img.content, content_type=img.content_type)
            img_parts.append({"type": "image_url", "image_url": {"url": url}})
        except Exception:
            continue
    if img_parts:
        chat_messages[-1]["content"].extend(img_parts)

    resp2 = client.chat.completions.create(
        model=model,
        messages=chat_messages,
        # Some newer models reject `max_tokens` and require `max_completion_tokens`.
        # We'll try the newer param first, then fall back.
        max_completion_tokens=max_out,
    )
    try:
        txt = resp2.choices[0].message.content
        return (txt or "").strip()
    except Exception:
        try:
            raw2 = resp2.model_dump() if hasattr(resp2, "model_dump") else {}
        except Exception:
            raw2 = {}
        return json.dumps(raw2, ensure_ascii=False)[:4000]


