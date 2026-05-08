"""v2 runtime configuration.

Everything that v1 reads from ``.env`` is shared (Redis, ORATS, EODHD,
Benzinga, OpenAI/Anthropic). v2-specific knobs are prefixed ``V2_``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _bool(name: str, default: bool = False) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _str(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    try:
        return int(raw) if raw else default
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class V2Config:
    # Auth - shared cookie with v1 so a desk session works on both subdomains.
    invite_code: str
    auth_secret: str
    auth_cookie_name: str
    auth_cookie_ttl_s: int
    public_access: bool

    # Service
    service_name: str
    bind_host: str
    bind_port: int

    # Cross-origin: v1 desk talking to v2 API directly.
    cors_origins: tuple[str, ...]

    # Counterfactual logger
    counterfactual_redis_stream: str
    counterfactual_enabled: bool

    # Anthropic / OpenAI keys reused from v1 env (we prefer Anthropic in v2).
    anthropic_api_key: str
    anthropic_model_default: str
    anthropic_model_extended: str
    openai_api_key: str

    # Foundation Brain feature flags - all OFF in Phase 0; will turn on as
    # each module is trained.
    enable_regime_encoder: bool
    enable_contrastive_analogues: bool
    enable_conformal_calibration: bool
    enable_path_generator: bool
    enable_learned_ranker: bool
    enable_agent_committee: bool


def get_config() -> V2Config:
    cors = _str(
        "V2_CORS_ORIGINS",
        "https://app.raven-tech.co,https://raven-tech.co,https://v2.app.raven-tech.co,http://localhost:8000,http://localhost:8001",
    )
    origins = tuple(o.strip() for o in cors.split(",") if o.strip())
    return V2Config(
        invite_code=_str("INVITE_CODE"),
        auth_secret=_str("AUTH_SECRET"),
        auth_cookie_name=_str("AUTH_COOKIE_NAME", "raven_session"),
        auth_cookie_ttl_s=_int("AUTH_COOKIE_TTL_S", 7 * 24 * 60 * 60),
        public_access=_bool("PUBLIC_ACCESS", True),
        service_name=_str("V2_SERVICE_NAME", "raven-tech-v2"),
        bind_host=_str("V2_BIND_HOST", "0.0.0.0"),
        bind_port=_int("V2_BIND_PORT", 8001),
        cors_origins=origins,
        counterfactual_redis_stream=_str(
            "V2_COUNTERFACTUAL_STREAM", "v2:counterfactual"
        ),
        counterfactual_enabled=_bool("V2_COUNTERFACTUAL_ENABLED", True),
        anthropic_api_key=_str("ANTHROPIC_API_KEY"),
        anthropic_model_default=_str(
            "V2_ANTHROPIC_MODEL_DEFAULT", "claude-sonnet-4-5-20250929"
        ),
        anthropic_model_extended=_str(
            "V2_ANTHROPIC_MODEL_EXTENDED", "claude-opus-4-1-20250805"
        ),
        openai_api_key=_str("OPENAI_API_KEY"),
        enable_regime_encoder=_bool("V2_ENABLE_REGIME_ENCODER", False),
        enable_contrastive_analogues=_bool("V2_ENABLE_CONTRASTIVE_ANALOGUES", False),
        enable_conformal_calibration=_bool("V2_ENABLE_CONFORMAL", False),
        enable_path_generator=_bool("V2_ENABLE_PATH_GENERATOR", False),
        enable_learned_ranker=_bool("V2_ENABLE_LEARNED_RANKER", False),
        enable_agent_committee=_bool("V2_ENABLE_AGENT_COMMITTEE", False),
    )
