from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def cache_root() -> Path:
    """Base directory for everything this system WRITES at runtime.

    On Vercel the deployment filesystem is READ-ONLY except `/tmp`, so a cache that
    defaults to `data/` does not degrade — it raises OSError on the write, after the
    request has already paid for the LLM call it was trying to cache.

    `/tmp` does not survive the invocation, so this buys within-request and warm-lambda
    reuse only. That is the honest ceiling of a serverless cache and it is why every
    caller of this still guards its write: relocating the path makes caching POSSIBLE,
    it does not make it reliable. Nothing whose loss matters is stored under here —
    durable state is Postgres.

    Locally this is `data/`, unchanged, so dev behaviour is byte-identical to before.
    """
    override = os.getenv("VCBRAIN_CACHE_ROOT")
    if override:
        return Path(override)
    # VERCEL is set to "1" by the platform in every deployment and unset everywhere else.
    return Path("/tmp/vcbrain") if os.getenv("VERCEL") else Path("data")


@dataclass(frozen=True)
class Settings:
    llm_provider: str = field(default_factory=lambda: os.getenv("LLM_PROVIDER", "openai"))
    openai_api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    anthropic_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    tavily_api_key: str = field(default_factory=lambda: os.getenv("TAVILY_API_KEY", ""))
    github_token: str = field(default_factory=lambda: os.getenv("GITHUB_TOKEN", ""))
    supabase_url: str = field(default_factory=lambda: os.getenv("SUPABASE_URL", ""))
    supabase_service_key: str = field(default_factory=lambda: os.getenv("SUPABASE_SERVICE_KEY", ""))
    database_url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", ""))

    # Flip to "beta_binomial" if the Kalman filter misbehaves. Verify this works at H10.
    score_model: str = field(default_factory=lambda: os.getenv("SCORE_MODEL", "kalman"))


settings = Settings()
