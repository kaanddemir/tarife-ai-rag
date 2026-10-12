"""Test-session safety net: the app's own engine and LLM client must never point at
the developer's real backend/.env targets (the production Supabase database, a paid
provider key). Environment variables take precedence over .env in pydantic-settings,
and this runs before any test imports app.config. Database tests build their own
engines from TARIFE_TEST_DATABASE_URL (local hosts only)."""
import os

os.environ["DATABASE_URL"] = "postgresql+psycopg://offline-tests@127.0.0.1:9/unreachable"
os.environ["PROVIDER_API_KEY"] = "test-key-not-real"
os.environ["PROVIDER_BASE_URL"] = "http://127.0.0.1:9/v1"
os.environ["SUPABASE_URL"] = "http://127.0.0.1:9"
os.environ["SUPABASE_JWT_SECRET"] = ""
os.environ["QUOTA_EXEMPT_EMAILS"] = ""
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

# Second, harder line: no socket may leave this machine during tests (Groq, Supabase,
# JWKS, model hubs). Loopback stays open for the disposable Postgres and local fakes.
import ipaddress  # noqa: E402
import socket  # noqa: E402

_real_connect = socket.socket.connect


def _loopback_only_connect(self, address):
    if self.family in (socket.AF_INET, socket.AF_INET6):
        host = address[0]
        try:
            local = ipaddress.ip_address(host).is_loopback
        except ValueError:
            local = host == "localhost"
        if not local:
            raise RuntimeError(f"test tried to open an external connection to {host!r}")
    return _real_connect(self, address)


socket.socket.connect = _loopback_only_connect
