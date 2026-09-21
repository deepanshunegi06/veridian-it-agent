"""Which model answers. One env var, not a refactor.

The agent's behaviour must not depend on a vendor. Tool calling is the only
capability required, and every provider below supports it, so swapping is a
configuration change and the tests stay meaningful.
"""

from __future__ import annotations

from functools import lru_cache

from langchain_core.language_models.chat_models import BaseChatModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_provider: str = "groq"
    llm_model: str = "openai/gpt-oss-120b"
    groq_api_key: str = ""
    google_api_key: str = ""
    openai_api_key: str = ""
    opencode_api_key: str = ""
    opencode_base_url: str = "https://opencode.ai/zen/go/v1"
    opencode_session_id: str = "veridian-it-desk-1"

    # A request that has not finished by here is not going to. The cap exists so
    # a model that loops on find_policy cannot hold a socket open forever.
    max_steps: int = 10
    cors_origins: str = "http://localhost:3000"

    # Persistence settings are declared here as well as in env.example so
    # pydantic-settings loads them from .env for normal Uvicorn launches.
    # app.db reads an explicit process environment value first, which keeps
    # tests able to force the in-memory backend with MONGODB_URI="".
    mongodb_uri: str = ""
    veridian_db: str = "veridian_it_agent"

    # --- auth / deployment -------------------------------------------------
    # ENV=dev enables seeded demo accounts (see app/auth.py). Any other value
    # disables dev defaults; demo passwords must then come from env.
    env: str = "dev"
    jwt_secret: str = "dev-only-secret-change-me-0123456789"
    jwt_algorithm: str = "HS256"
    jwt_expires_minutes: int = 480
    auth_cookie_name: str = "veridian_access"

    # Demo seed accounts. Emails/passwords are env-configurable; the defaults
    # below are development-only and are only used when ENV=dev.
    seed_demo_users: bool = True
    demo_employee_email: str = "employee@veridian.local"
    demo_agent_email: str = "agent@veridian.local"
    demo_admin_email: str = "admin@veridian.local"
    demo_employee_password: str = "Employee-Demo-01!"
    demo_agent_password: str = "Agent-Demo-01!"
    demo_admin_password: str = "Admin-Demo-01!"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


def build_llm(temperature: float = 0.1) -> BaseChatModel:
    """Low temperature on purpose: this is a policy desk, not a brainstorm."""
    s = get_settings()
    provider = s.llm_provider.lower()

    if provider == "groq":
        from langchain_groq import ChatGroq

        return ChatGroq(
            model=s.llm_model,
            temperature=temperature,
            api_key=s.groq_api_key,
            timeout=30,
            # The free tier rate limits and the SDK honours Retry-After, so
            # waiting is cheaper than failing a turn halfway through a request.
            max_retries=5,
        )

    if provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model=s.llm_model,
            temperature=temperature,
            google_api_key=s.google_api_key,
            max_retries=5,
        )

    if provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=s.llm_model, temperature=temperature, api_key=s.openai_api_key, timeout=30
        )

    if provider in ("opencode", "opencode-go"):
        # OpenCode Go is OpenAI-compatible: MiMo-V2.5 lives at
        # https://opencode.ai/zen/go/v1/chat/completions with model id mimo-v2.5.
        # Docs: https://opencode.ai/docs/go/ (endpoints table + session header).
        from langchain_openai import ChatOpenAI

        if not s.opencode_api_key:
            raise ValueError("OPENCODE_API_KEY is not set")
        return ChatOpenAI(
            model=s.llm_model or "mimo-v2.5",
            temperature=temperature,
            api_key=s.opencode_api_key,
            base_url=s.opencode_base_url.rstrip("/"),
            timeout=60,
            max_retries=3,
            default_headers={
                "User-Agent": "veridian-it-agent/1.0",
                "x-opencode-session": s.opencode_session_id,
            },
        )

    raise ValueError(f"unknown llm_provider: {s.llm_provider}")
