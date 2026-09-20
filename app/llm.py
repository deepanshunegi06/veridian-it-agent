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

    # A request that has not finished by here is not going to. The cap exists so
    # a model that loops on find_policy cannot hold a socket open forever.
    max_steps: int = 10
    cors_origins: str = "http://localhost:3000"

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

    raise ValueError(f"unknown llm_provider: {s.llm_provider}")
