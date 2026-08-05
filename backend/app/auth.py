"""Authenticate requests using the Supabase-issued JWT.

The frontend signs the user in with Supabase (anonymously for guests, or via email magic
link) and sends the resulting access token as `Authorization: Bearer <jwt>`. Supabase
signs these tokens asymmetrically (ES256/RS256) by default; we verify the signature
against the project's public JWKS (cached) and trust the `sub` (user id) and
`is_anonymous` claims. Legacy projects on the shared HS256 secret are also supported.
"""
import logging
import ssl
import uuid
from dataclasses import dataclass
from functools import lru_cache

import certifi
import jwt
from fastapi import HTTPException, Request

from app.config import settings

logger = logging.getLogger(__name__)

# Tolerance for clock skew between this server and Supabase when checking iat/nbf/exp.
# Without it, a token minted a moment ago can look "not yet valid" and 401 a real user.
_CLOCK_SKEW_LEEWAY = 30


@dataclass(frozen=True)
class AuthUser:
    id: uuid.UUID
    is_anonymous: bool
    email: str | None = None


@lru_cache(maxsize=1)
def _jwk_client() -> "jwt.PyJWKClient":
    # One client for the app lifetime, caching both the JWK set and derived signing
    # keys so we don't hit the network on every request (default cache_keys=False would
    # refetch each time, turning a transient blip into an intermittent 401). Pass an
    # explicit certifi CA bundle so the fetch doesn't fail on systems whose default
    # urllib SSL store is incomplete (e.g. macOS Python.framework).
    ctx = ssl.create_default_context(cafile=certifi.where())
    return jwt.PyJWKClient(
        settings.supabase_jwks_url,
        cache_keys=True,
        lifespan=600,
        ssl_context=ctx,
    )


def warm_jwks() -> None:
    """Prefetch the JWKS at startup so the first real request doesn't pay the cold
    fetch (and any transient failure surfaces in logs, not as a user-facing 401)."""
    try:
        _jwk_client().get_jwk_set()
    except Exception as exc:  # noqa: BLE001 — best-effort warmup, never fatal
        logger.warning("JWKS warmup failed (will retry on first request): %s", exc)


def _verify(token: str) -> dict:
    """Verify a Supabase access token, supporting both asymmetric (ES256/RS256, the
    current default via JWKS) and legacy symmetric (HS256, shared secret) signing."""
    alg = jwt.get_unverified_header(token).get("alg", "")
    if alg == "HS256":
        # The algorithm is the caller's claim, so an unconfigured secret must not
        # fall through to a default: that would let anyone holding this repository
        # mint a valid token. No secret configured means no HS256 project.
        if not settings.supabase_jwt_secret:
            raise jwt.InvalidAlgorithmError("HS256 tokens are not accepted")
        return jwt.decode(
            token,
            settings.supabase_jwt_secret,
            algorithms=["HS256"],
            audience="authenticated",
            leeway=_CLOCK_SKEW_LEEWAY,
        )

    try:
        signing_key = _jwk_client().get_signing_key_from_jwt(token)
    except jwt.exceptions.PyJWKClientError:
        # Stale cache (e.g. key rotation) or a transient fetch error: rebuild the
        # client once and retry before giving up, so key rotation doesn't 401 users.
        _jwk_client.cache_clear()
        signing_key = _jwk_client().get_signing_key_from_jwt(token)

    return jwt.decode(
        token,
        signing_key.key,
        algorithms=["ES256", "RS256"],
        audience="authenticated",
        leeway=_CLOCK_SKEW_LEEWAY,
    )


def get_current_user(request: Request) -> AuthUser:
    """FastAPI dependency: verify the bearer token and return the caller.

    Raises 401 for a missing, malformed, or invalid token so an unauthenticated or
    forged request never reaches the quota check or the paid LLM.
    """
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Missing bearer token")

    try:
        claims = _verify(token)
    except (jwt.PyJWTError, jwt.exceptions.PyJWKClientError) as exc:
        # Debug level: the reason describes a caller-supplied token, so it belongs
        # in a deliberate debugging session rather than in the default log.
        logger.debug("token rejected: %s: %s", type(exc).__name__, exc)
        raise HTTPException(status_code=401, detail="Invalid token") from exc

    sub = claims.get("sub")
    if not sub:
        raise HTTPException(status_code=401, detail="Token has no subject")

    try:
        user_id = uuid.UUID(sub)
    except (ValueError, TypeError):
        raise HTTPException(status_code=401, detail="Token subject is not a valid id")

    # Supabase marks guest sessions with is_anonymous=true (top level and/or in
    # app_metadata); default to anonymous if the claim is absent, which is the safer tier.
    is_anonymous = bool(
        claims.get("is_anonymous", claims.get("app_metadata", {}).get("is_anonymous", True))
    )
    return AuthUser(id=user_id, is_anonymous=is_anonymous, email=claims.get("email"))
