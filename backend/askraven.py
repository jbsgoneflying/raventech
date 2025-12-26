from __future__ import annotations

import json
import os
import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from backend.technicals import encode_image_to_data_url


def _pick(d: dict, keys: List[str]) -> dict:
    out = {}
    for k in keys:
        if k in d:
            out[k] = d.get(k)
    return out


def _content_to_text(content: Any) -> str:
    """
    Normalize OpenAI message content to plain text.
    Some SDKs/models return `message.content` as a list of parts instead of a string.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    # list-of-parts (dicts or objects with `.text`)
    if isinstance(content, list):
        parts: List[str] = []
        for p in content:
            if isinstance(p, str):
                parts.append(p)
                continue
            if isinstance(p, dict):
                # common shapes: {"type":"text","text":"..."} or {"text":"..."}
                t = p.get("text")
                if isinstance(t, str):
                    parts.append(t)
                continue
            # fallback for SDK objects
            t = getattr(p, "text", None)
            if isinstance(t, str):
                parts.append(t)
        return "\n".join([x for x in parts if x is not None])
    # fallback for objects with `.text`
    t = getattr(content, "text", None)
    if isinstance(t, str):
        return t
    return ""


def _fmt_pct(x: Any) -> str:
    try:
        if x is None:
            return "—"
        v = float(x)
        if not math.isfinite(v):
            return "—"
        return f"{v:.2f}%"
    except Exception:
        return "—"


def _fmt_num(x: Any, d: int = 2) -> str:
    try:
        if x is None:
            return "—"
        v = float(x)
        if not math.isfinite(v):
            return "—"
        return f"{v:.{int(d)}f}"
    except Exception:
        return "—"


def fallback_briefing(*, question: str, context_pack: Dict[str, Any]) -> str:
    """
    Deterministic, always-available response when the model returns empty/tool-only output.
    Uses Engine context + optional Benzinga news snapshot (if present).
    """
    eng = str(context_pack.get("engine") or "").strip() or "engine?"
    q = str(question or "").strip()

    lines: List[str] = []
    lines.append("Here’s a context-grounded briefing using RavenTech + any attached Benzinga headlines. (No web browsing beyond what’s provided.)")
    lines.append("")

    # --- Engine 2 (SPX) ---
    if eng == "engine2":
        cur = context_pack.get("current") if isinstance(context_pack.get("current"), dict) else {}
        reg = (cur.get("regime") if isinstance(cur.get("regime"), dict) else {}) if isinstance(cur, dict) else {}
        macro = (cur.get("macro") if isinstance(cur.get("macro"), dict) else {}) if isinstance(cur, dict) else {}
        live = context_pack.get("liveContext") if isinstance(context_pack.get("liveContext"), dict) else {}
        dg = live.get("dealerGamma") if isinstance(live.get("dealerGamma"), dict) else {}
        like = context_pack.get("oddsLikeNow") if isinstance(context_pack.get("oddsLikeNow"), dict) else {}

        score = reg.get("score100")
        bucket = reg.get("bucket")
        macro_mult = macro.get("multiplier")
        macro_hi = (macro.get("highImpactUS") if isinstance(macro.get("highImpactUS"), dict) else {}) if isinstance(macro, dict) else {}
        hi_top = macro_hi.get("top") if isinstance(macro_hi.get("top"), list) else []

        net_g = dg.get("netGammaSign")
        mag = dg.get("magnitudeBucket")
        spot = dg.get("spot")

        lines.append("## Snapshot")
        lines.append(f"- Regime: {_fmt_num(score, 1)} / 100 · {bucket or '—'}")
        lines.append(f"- Macro multiplier: {_fmt_num(macro_mult, 2)}×")
        if net_g or mag:
            lines.append(f"- Dealer gamma (live): {(str(net_g).upper() if net_g else '—')} · {(str(mag).upper() if mag else '—')} · spot={_fmt_num(spot, 2)}")
        lines.append("")

        lines.append("## Odds (like now)")
        byw = like.get("byWidth") if isinstance(like.get("byWidth"), list) else []
        if byw:
            for r in byw[:6]:
                if not isinstance(r, dict):
                    continue
                w = r.get("w")
                n = r.get("n")
                be = r.get("breachEitherPct")
                bp = r.get("breachPutPct")
                bc = r.get("breachCallPct")
                lines.append(f"- { _fmt_num(w,2) }× EM: breachEither={_fmt_pct(be)} (put={_fmt_pct(bp)} · call={_fmt_pct(bc)}) · n={n if n is not None else '—'}")
        else:
            lines.append("- No oddsLikeNow.byWidth available in context.")
        lines.append("")

        if hi_top:
            lines.append("## Scheduled macro that can gap / move the open (from your Benzinga macro calendar)")
            for x in hi_top[:8]:
                lines.append(f"- {str(x)}")
            lines.append("")

        # Strike / spread quick math (best-effort)
        try:
            # Prefer spot from dealer gamma, else technicals livePrice.
            px = None
            if spot is not None:
                px = float(spot)
            tech = context_pack.get("technicals") if isinstance(context_pack.get("technicals"), dict) else {}
            if px is None and tech and tech.get("livePrice") is not None:
                px = float(tech.get("livePrice"))
            if px is not None and math.isfinite(px):
                # parse strikes from question (very simple heuristic)
                import re

                nums = [int(n) for n in re.findall(r"\b(\d{4,5})\b", q)]
                nums = sorted(list(dict.fromkeys(nums)))
                # If user provided 6950/6955 style, these will show.
                if nums:
                    lines.append("## Your strikes (best-effort from your prompt)")
                    for s in nums[:4]:
                        diff = float(s) - float(px)
                        side = "above" if diff > 0 else "below"
                        lines.append(f"- Strike {s}: {abs(diff):.1f} pts {side} spot ({_fmt_num(px,2)})")
                    lines.append("")
        except Exception:
            pass

    # --- News snapshot if present ---
    news = context_pack.get("news") if isinstance(context_pack.get("news"), dict) else None
    if news and news.get("enabled") and isinstance(news.get("items"), list):
        items = [x for x in (news.get("items") or []) if isinstance(x, dict)]
        if items:
            lines.append("## Benzinga headlines snapshot (best-effort)")
            for it in items[:10]:
                title = str(it.get("title") or "").strip()
                if not title:
                    continue
                src = str(it.get("source") or "").strip()
                when = str(it.get("updated") or it.get("created") or "").strip()
                bit = title
                if src:
                    bit += f" ({src})"
                if when:
                    bit += f" · {when}"
                lines.append(f"- {bit}")
            lines.append("")

    lines.append("## What can gap the open (framework)")
    lines.append("- Macro prints (CPI/FOMC/NFP minutes/claims, etc.), surprise policy headlines, geopolitical shocks, and big single-name earnings warnings are the classic gap drivers.")
    lines.append("- In holiday/low-liquidity tape, *smaller* catalysts can move price more than usual; treat gamma + liquidity as multipliers, not predictors.")
    lines.append("")
    lines.append("## What would change my view (quick questions)")
    lines.append("- What’s the **current spot** and your **exact expiration time** (0DTE vs next session)?")
    lines.append("- What credit did you take for the 6950/6955c spread, and what’s your stop/adjust plan?")
    lines.append("- Are you holding through any of the listed macro events, or can you flatten before them?")

    return "\n".join(lines).strip()


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


ASKRAVEN_SYSTEM_PROMPT = """You are AskRaven, a rigorous, skeptical quant trading assistant.\n\nHard rules:\n- Ground your answer in the provided RavenTech context pack. If a number is not in the context, say so.\n- If a question depends on missing trade inputs (credit, exact expiry, wing width, underlying proxy), ask concise clarifying questions.\n- Distinguish between:\n  (1) historical odds from the engines,\n  (2) live/informational overlays (dealer gamma, live price), and\n  (3) outside context feeds explicitly provided in the context pack (e.g., Benzinga news).\n- No hallucinated data. No fabricated citations.\n\nConstraints:\n- You do NOT have web browsing unless explicitly provided.\n- If the user asks for “news of the day” or headlines:\n  - Use the news items provided in the context pack (if present) and label them as Benzinga news.\n  - If news items are not present, say so and proceed with context-based reasoning.\n\nOutput style:\n- Prefer bullet points.\n- Include a short \"Key numbers\" section when possible.\n- Include a short \"What would change my view\" section.\n\nCompliance:\n- Educational / risk analysis only; not financial advice.\n""".strip()


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
    base_user_text = f"RavenTech context pack:\n{ctx_txt}\n\nUser question:\n{str(question or '').strip()}"
    user_parts: List[Dict[str, Any]] = [{"type": "text", "text": base_user_text}]
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

            # If the model returned no text, retry once with a stricter instruction to always respond.
            try:
                resp_retry = client.responses.create(
                    model=model,
                    input=[
                        {
                            "role": "system",
                            "content": [
                                {
                                    "type": "text",
                                    "text": ASKRAVEN_SYSTEM_PROMPT
                                    + "\n\nIMPORTANT: You must return a non-empty text answer. If real-time news is unavailable, say so and proceed with context-based reasoning.",
                                }
                            ],
                        },
                        {"role": "user", "content": [{"type": "text", "text": base_user_text}]},
                    ],
                    max_output_tokens=max_out,
                )
                out2 = getattr(resp_retry, "output_text", None)
                if isinstance(out2, str) and out2.strip():
                    return out2.strip()
            except Exception:
                pass

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

    def _chat_once(*, use_parts: bool) -> Any:
        if use_parts:
            msgs: List[Dict[str, Any]] = [
                {"role": "system", "content": ASKRAVEN_SYSTEM_PROMPT},
                {"role": "user", "content": [{"type": "text", "text": base_user_text}]},
            ]
            if img_parts:
                msgs[-1]["content"].extend(img_parts)
        else:
            # Plain string content is often the most compatible across models.
            msgs = [
                {"role": "system", "content": ASKRAVEN_SYSTEM_PROMPT},
                {"role": "user", "content": base_user_text},
            ]
        try:
            return client.chat.completions.create(model=model, messages=msgs, max_completion_tokens=max_out)
        except TypeError:
            return client.chat.completions.create(model=model, messages=msgs, max_tokens=max_out)

    try:
        # First attempt: rich parts (supports images).
        resp2 = _chat_once(use_parts=True)
    except Exception:
        # If the parts format is rejected by the model, retry with plain string messages.
        resp2 = _chat_once(use_parts=False)

    # Some models can return tool calls with empty text content. Never return blank.
    try:
        msg_obj = resp2.choices[0].message
        txt = _content_to_text(getattr(msg_obj, "content", None)).strip()
        if txt:
            return txt

        tool_calls = getattr(msg_obj, "tool_calls", None)
        fn_call = getattr(msg_obj, "function_call", None)
        if tool_calls or fn_call:
            return fallback_briefing(question=question, context_pack=context_pack)

        # Retry once with an explicit non-empty response constraint.
        try:
            resp3 = _chat_once(use_parts=False)
            msg3 = resp3.choices[0].message
            txt3 = _content_to_text(getattr(msg3, "content", None)).strip()
            if txt3:
                return txt3
        except Exception:
            pass

        return (
            fallback_briefing(question=question, context_pack=context_pack)
        )
    except Exception:
        try:
            raw2 = resp2.model_dump() if hasattr(resp2, "model_dump") else {}
        except Exception:
            raw2 = {}
        return json.dumps(raw2, ensure_ascii=False)[:4000]


