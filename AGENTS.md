# AGENTS.md

## Cursor Cloud specific instructions

### Overview

Raven-Tech.co is a Python 3.12 FastAPI application — a quantitative options analytics platform with 9+ analysis engines. Static HTML/JS/CSS frontend served directly (no build step). Redis is the sole data store (no SQL database).

### Running the dev server

```bash
uvicorn backend.app:app --host 0.0.0.0 --port 8000 --reload
```

### Running tests

```bash
python3 -m pytest tests/ -v --tb=short
```

There are ~15 pre-existing test failures (golden payload snapshot drift, engine4 KeyError, MC seed changes). These are not environment issues.

### Services

| Service | How to start | Notes |
|---------|-------------|-------|
| **FastAPI app** | `uvicorn backend.app:app --host 0.0.0.0 --port 8000 --reload` | Runs on port 8000 |
| **Redis** | `sudo redis-server --daemonize yes` | Must be running before app start; app connects to `REDIS_URL` in `.env` |

### Key caveats

- **`.env` file required**: Copy `env.example` to `.env` and change `REDIS_URL` to `redis://localhost:6379/0` (the example points to a Docker Compose service name). Also set `COOKIE_SECURE=0` for local dev (no HTTPS).
- **Invite-gated auth**: Most routes require authentication. The invite code is in `INVITE_CODE` env var (default from env.example: `RAVEN-BETA-2026`). POST to `/login` with `code=<INVITE_CODE>` to get a session cookie. `/api/health` is public.
- **External API keys**: The `env.example` includes sample API keys for ORATS, EODHD, FMP, Benzinga, API Ninjas, FRED, and OpenAI. Most engines gracefully degrade if keys are invalid/missing, but the LLM Morning Brief will show auth errors without a valid OpenAI key.
- **PATH**: pip installs to `~/.local/bin` — ensure `export PATH="$HOME/.local/bin:$PATH"` is in your shell.
- **No linter configured**: The codebase has no flake8/ruff/pylint config. Use `python3 -m pytest` as the primary quality check.
