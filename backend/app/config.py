from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """App configuration, loaded from environment / .env."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Set via .env — Supabase session pooler (psycopg driver, SSL required). The
    # placeholder below is intentionally non-functional so a missing .env fails loudly.
    # repr=False on every secret: a Settings repr appears in tracebacks, pytest
    # assertion output and debug logs, and must never carry credentials.
    database_url: str = Field(
        "postgresql+psycopg://USER:PASSWORD@HOST:5432/postgres?sslmode=require", repr=False
    )
    db_pool_size: int = 5
    db_max_overflow: int = 5
    db_pool_timeout_seconds: int = 10
    db_pool_recycle_seconds: int = 1800

    # Comma-separated list of allowed CORS origins (set the real frontend origin in prod).
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    # --- Auth (Supabase) ---
    # Supabase project URL (e.g. https://<ref>.supabase.co). Used to fetch the public
    # JWKS for verifying asymmetric (ES256/RS256) access tokens, which is Supabase's
    # current default. Not secret.
    supabase_url: str = ""
    # Legacy shared JWT secret (Settings → API → JWT Secret). Only used for older
    # projects that still sign tokens with HS256; asymmetric projects use the JWKS above.
    # Empty by default on purpose: a default committed here would be a secret anyone
    # could sign a token with, so HS256 verification stays off until one is configured.
    supabase_jwt_secret: str = Field("", repr=False)

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
    provider_api_key: str = Field("", repr=False)
    model: str = "qwen/qwen3.8-27b"
    # Bounded provider calls (the SDK default is a 600 s timeout with 2 retries).
    # Worst case per LLM call ~= (retries + 1) * timeout + backoff (<1 s); keep it well
    # below QUOTA_RESERVATION_TTL_SECONDS and the frontend's request timeout (90 s).
    provider_connect_timeout_seconds: float = 5.0
    provider_timeout_seconds: float = 30.0
    provider_max_retries: int = 1

    # Quota reservations older than this are ignored and cleaned up: a request that
    # crashed mid-flight cannot hold a user's quota forever. Must exceed the longest
    # possible /chat request.
    quota_reservation_ttl_seconds: int = 180

    # Reverse proxies whose X-Forwarded-For is trusted (comma-separated IPs/CIDRs).
    # Empty = trust nothing and use the TCP peer address (safe default). See README
    # "Client IP behind a proxy".
    trusted_proxies: str = ""

    # Reject oversized request bodies before FastAPI parses JSON. The largest valid
    # /chat payload is far smaller than this; the margin accommodates UTF-8 and JSON
    # framing without allowing an unauthenticated caller to make the server buffer an
    # arbitrarily large body.
    max_request_body_bytes: int = 32 * 1024

    # Retrieval
    embed_model: str = "BAAI/bge-m3"
    embed_dim: int = 1024
    top_k: int = 8
    # Load and warm BGE-M3 (~1.8 GB RSS on CPU) in a background thread at startup instead of on the
    # first question. Each worker process loads its own copy; see README "Embedding model".
    embed_preload: bool = True


settings = Settings()
