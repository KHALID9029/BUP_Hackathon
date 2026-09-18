from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    OPENROUTER_API_KEY: str | None = None        # optional at startup so /health works without it
    LLM_MODEL: str = "google/gemini-3.8-flash"
    LLM_FALLBACK_MODELS: str = "openai/gpt-4.1-mini,deepseek/deepseek-v4.1-flash"   # comma-separated, tried in order
    LLM_TIMEOUT_S: float = 10.0                  # per attempt; hard cap via asyncio.wait_for
    LLM_MAX_ATTEMPTS: int = 2                    # 1 normal + 1 retry with guardrail feedback
    LLM_MAX_TOKENS: int = 1000                   # 3 directives ≈ 350 tokens; headroom avoids truncation → retry → latency
    LLM_CONCURRENCY: int = 20                    # semaphore around the OpenRouter call
    PORT: int = 8000
    FRONTEND_DIST: str = str(Path(__file__).resolve().parent.parent / "frontend" / "dist")   # built UI; skipped if absent


settings = Settings()
