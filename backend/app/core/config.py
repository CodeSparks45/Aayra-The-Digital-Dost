"""
app/core/config.py

Central configuration module for Aayra - Digital Dost.
Uses Pydantic BaseSettings to validate all environment variables at startup.
The application will FAIL FAST with a clear error if any required key is missing.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, AnyHttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    All configuration is sourced from environment variables or the .env file.
    Pydantic validates types and constraints at application startup.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,       # PINECONE_API_KEY == pinecone_api_key
        extra="ignore",             # Silently ignore unknown env vars
    )

    # ── Application ──────────────────────────────────────────────────────────
    APP_NAME: str = "Aayra - Digital Dost"
    APP_VERSION: str = "2.0.0"
    ENVIRONMENT: Literal["development", "staging", "production"] = "development"
    DEBUG: bool = Field(default=False, description="Enable verbose debug logging")
    API_PREFIX: str = "/api/v1"

    # ── CORS ─────────────────────────────────────────────────────────────────
    # Comma-separated list of allowed origins, e.g. "http://localhost:3000,https://aayra.app"
    CORS_ORIGINS: str = "http://localhost:3000"

    @property
    def cors_origins_list(self) -> list[str]:
        """Parse comma-separated CORS origins into a list."""
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",")]

    # ── Google Gemini (Primary LLM) ──────────────────────────────────────────
    GOOGLE_API_KEY: str = Field(
        ...,
        description="Google AI Studio / Vertex AI API key for Gemini 1.5 Flash.",
    )
    GEMINI_MODEL: str = "gemini-1.5-flash"
    GEMINI_TEMPERATURE: float = Field(default=0.7, ge=0.0, le=2.0)
    GEMINI_MAX_OUTPUT_TOKENS: int = Field(default=2048, ge=256, le=8192)

    # ── Pinecone (Semantic / Vector Memory) ──────────────────────────────────
    PINECONE_API_KEY: str = Field(
        ...,
        description="Pinecone API key for semantic memory retrieval.",
    )
    PINECONE_INDEX_NAME: str = Field(
        default="aayra-memory",
        description="Name of the Pinecone index. Must exist in your Pinecone project.",
    )
    PINECONE_NAMESPACE: str = Field(
        default="user-memories",
        description="Namespace within the index to scope per-user memories.",
    )
    PINECONE_EMBEDDING_DIMENSION: int = Field(
        default=768,
        description="Must match the embedding model output dimension. "
                    "Google text-embedding-004 outputs 768.",
    )
    PINECONE_TOP_K: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Number of memory chunks to retrieve per query.",
    )

    # ── Neo4j (Episodic / Knowledge Graph Memory) ────────────────────────────
    NEO4J_URI: str = Field(
        ...,
        description="Neo4j connection URI. e.g. neo4j+s://xxxxx.databases.neo4j.io (Aura) "
                    "or bolt://localhost:7687 (local Docker).",
    )
    NEO4J_USERNAME: str = Field(default="neo4j")
    NEO4J_PASSWORD: str = Field(
        ...,
        description="Neo4j database password.",
    )
    NEO4J_DATABASE: str = Field(
        default="neo4j",
        description="Target database name. 'neo4j' is the default for Aura Free.",
    )

    # ── Supabase (Auth & Structured Data) ────────────────────────────────────
    SUPABASE_URL: AnyHttpUrl = Field(
        ...,
        description="Your Supabase project URL.",
    )
    SUPABASE_ANON_KEY: str = Field(
        ...,
        description="Supabase anon/public key for client-side auth.",
    )
    SUPABASE_SERVICE_ROLE_KEY: str = Field(
        ...,
        description="Supabase service role key — NEVER expose to frontend.",
    )

    # ── Hume AI (Emotional Intelligence / Voice EQ) ──────────────────────────
    HUME_API_KEY: str = Field(
        default="",
        description="Hume AI API key. Optional — EQ features degrade gracefully if absent.",
    )
    HUME_CONFIG_ID: str = Field(
        default="",
        description="Hume EVI configuration ID for the Aayra voice persona.",
    )

    # ── ElevenLabs (Text-to-Speech) ──────────────────────────────────────────
    ELEVENLABS_API_KEY: str = Field(
        default="",
        description="ElevenLabs API key. Optional — falls back to Gemini TTS if absent.",
    )
    ELEVENLABS_VOICE_ID: str = Field(
        default="",
        description="ElevenLabs voice ID for Aayra's TTS persona.",
    )

    # ── Memory & Agent Tuning ─────────────────────────────────────────────────
    MAX_CHAT_HISTORY_TURNS: int = Field(
        default=20,
        ge=5,
        le=100,
        description="Number of recent conversation turns to include in LLM context.",
    )
    MEMORY_CONSOLIDATION_CRON: str = Field(
        default="0 2 * * *",
        description="Cron expression for nightly memory consolidation job (default: 2am daily).",
    )
    AGENT_MAX_ITERATIONS: int = Field(
        default=10,
        ge=3,
        le=25,
        description="Max LangGraph agent loop iterations before forced termination.",
    )

    # ── Security ──────────────────────────────────────────────────────────────
    SECRET_KEY: str = Field(
        ...,
        description="Secret key for JWT signing. Generate with: openssl rand -hex 32",
        min_length=32,
    )
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(default=60 * 24)  # 24 hours

    # ── Field Validators ──────────────────────────────────────────────────────
    @field_validator("GEMINI_TEMPERATURE")
    @classmethod
    def validate_temperature(cls, v: float) -> float:
        if not 0.0 <= v <= 2.0:
            raise ValueError("GEMINI_TEMPERATURE must be between 0.0 and 2.0")
        return v

    @field_validator("NEO4J_URI")
    @classmethod
    def validate_neo4j_uri(cls, v: str) -> str:
        valid_schemes = ("bolt://", "bolt+s://", "neo4j://", "neo4j+s://")
        if not any(v.startswith(scheme) for scheme in valid_schemes):
            raise ValueError(
                f"NEO4J_URI must start with one of {valid_schemes}. Got: {v!r}"
            )
        return v

    # ── Computed Helpers ──────────────────────────────────────────────────────
    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    @property
    def hume_enabled(self) -> bool:
        return bool(self.HUME_API_KEY and self.HUME_CONFIG_ID)

    @property
    def elevenlabs_enabled(self) -> bool:
        return bool(self.ELEVENLABS_API_KEY and self.ELEVENLABS_VOICE_ID)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Returns a cached singleton Settings instance.
    Use this everywhere instead of instantiating Settings() directly.
    The lru_cache ensures .env is parsed only once at startup.

    Usage:
        from app.core.config import get_settings
        settings = get_settings()
    """
    return Settings()