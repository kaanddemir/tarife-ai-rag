from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """App configuration, loaded from environment / .env."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Set via .env — Supabase session pooler (psycopg driver, SSL required). The
    # placeholder below is intentionally non-functional so a missing .env fails loudly.
    database_url: str = (
        "postgresql+psycopg://USER:PASSWORD@HOST:5432/postgres?sslmode=require"
    )

    # Comma-separated list of allowed CORS origins (set the real frontend origin in prod).
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    # --- Auth (Supabase) ---
    # Supabase project URL (e.g. https://<ref>.supabase.co). Used to fetch the public
    # JWKS for verifying asymmetric (ES256/RS256) access tokens, which is Supabase's
    # current default. Not secret.
    supabase_url: str = ""
    # Legacy shared JWT secret (Settings → API → JWT Secret). Only used for older
    # projects that still sign tokens with HS256; asymmetric projects use the JWKS above.
    supabase_jwt_secret: str = "CHANGE_ME_SUPABASE_JWT_SECRET"

    @property
    def supabase_jwks_url(self) -> str:
        return f"{self.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"

    # --- Message quotas (the primary abuse control; see quota.py) ---
    # Guest (anonymous) users: total messages before they must sign in.
    guest_message_limit: int = 5
    # Backstop against clearing storage to reset the guest cap: max anonymous messages
    # per source IP per day (allows a few devices behind one NAT, not unlimited).
    guest_ip_daily_limit: int = 15
    # Signed-in users: messages per calendar day (UTC).
    daily_message_limit: int = 20
    # Comma-separated emails exempt from all message quotas (your own test/admin account).
    # Only applies to signed-in users, since anonymous tokens carry no email.
    quota_exempt_emails: str = ""

    # Per-client rate limits (slowapi syntax, e.g. "20/minute"). These are only a coarse
    # burst backstop now — real per-user control is the message quota above. /compare only
    # hits the DB. Both overridable via env for prod tuning.
    chat_rate_limit: str = "20/minute"
    compare_rate_limit: str = "30/minute"

    # Hosted open-model API (OpenAI-compatible): Groq / Together / OpenRouter / etc.
    provider_base_url: str = "https://api.groq.com/openai/v1"
    provider_api_key: str = "changeme"
    model: str = "qwen/qwen3.6-27b"

    # Retrieval
    embed_model: str = "BAAI/bge-m3"
    embed_dim: int = 1024
    top_k: int = 8


settings = Settings()
