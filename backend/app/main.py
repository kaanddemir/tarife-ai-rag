import logging
import uuid
from contextlib import asynccontextmanager
from typing import Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.auth import AuthUser, get_current_user
from app.client_ip import client_ip, rate_limit_key, trusted_networks
from app.config import settings
from app import db
from app.db import get_session
from app.ingest.normalize import UNLIMITED
from app.models import ChatRequest, ChatResponse, Package, PackageOut
from app.quota import commit_quota, release_quota, reserve_quota
from app.rag.retrieve import gb_band_filter

# Uvicorn configures only its own loggers; without this the app's INFO/WARNING lines
# (model load and warm-up time, provider failure kinds, quota release problems) are
# dropped or unformatted. Messages never include tokens, keys, IPs or provider text.
if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

Operator = Literal["turkcell", "vodafone", "turk_telekom"]
LineType = Literal["prepaid", "postpaid"]
CustomerType = Literal["new", "existing", "any"]

# The catalog is ~120 rows; a comparison table never needs more than a handful.
MAX_COMPARE_IDS = 20


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fail at startup rather than on a user's first question: an unset key is a
    # missing .env, and the answer to that is a setup step, not a chat error.
    if not settings.provider_api_key:
        raise RuntimeError("PROVIDER_API_KEY is not set. See backend/.env.example.")

    trusted_networks()  # a malformed TRUSTED_PROXIES fails here, not per request
    from app.auth import warm_jwks

    warm_jwks()
    if settings.embed_preload:
        from app.rag.embed import preload_in_background

        preload_in_background()
    yield


# Per-client rate limiting. In-memory (single process); behind multiple workers
# a reverse proxy (nginx/Cloudflare) should enforce the real limit — this is the
# first line of defense against abuse of the paid LLM endpoint.
limiter = Limiter(key_func=rate_limit_key)

app = FastAPI(title="TarifeAI", version="0.1.0", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


class RequestSizeLimitMiddleware:
    """Bound request bodies even when Content-Length is absent or dishonest."""

    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        raw_length = headers.get(b"content-length")
        if raw_length:
            try:
                if int(raw_length) > self.max_bytes:
                    await JSONResponse(status_code=413, content={"detail": "Request body too large"})(
                        scope, receive, send
                    )
                    return
            except ValueError:
                await JSONResponse(status_code=400, content={"detail": "Invalid Content-Length"})(
                    scope, receive, send
                )
                return

        # Buffer only up to the configured bound before handing control to Starlette.
        # This also covers chunked bodies with no Content-Length; valid API bodies are
        # small JSON documents and do not need streaming semantics.
        chunks = []
        received = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body = message.get("body", b"")
            received += len(body)
            if received > self.max_bytes:
                await JSONResponse(status_code=413, content={"detail": "Request body too large"})(
                    scope, receive, send
                )
                return
            chunks.append(body)
            if not message.get("more_body", False):
                break

        replayed = False

        async def replay_receive():
            nonlocal replayed
            if replayed:
                return {"type": "http.request", "body": b"", "more_body": False}
            replayed = True
            return {"type": "http.request", "body": b"".join(chunks), "more_body": False}

        await self.app(scope, replay_receive, send)


app.add_middleware(RequestSizeLimitMiddleware, max_bytes=settings.max_request_body_bytes)


@app.exception_handler(SQLAlchemyError)
async def _database_unavailable(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    # Never echo the driver message: it can carry SQL, parameters or host names.
    logger.error("database error on %s: %s", request.url.path, type(exc).__name__)
    return JSONResponse(status_code=503, content={"detail": {"reason": "database_unavailable"}})

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    # The API is read-only over GET/POST and authenticates with a bearer header,
    # so nothing here needs the wildcards or cookie credentials.
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
    allow_credentials=False,
)


@app.get("/health")
def health() -> dict:
    """Liveness only. It deliberately does not touch the database: an endpoint
    open to everyone should not hand out catalog size or run a query per call."""
    return {"status": "ok"}


@app.get("/packages", response_model=list[PackageOut])
@limiter.limit(settings.compare_rate_limit)
def list_packages(
    request: Request,
    session: Session = Depends(get_session),
    operator: Optional[Operator] = None,
    line_type: Optional[LineType] = Query(None, description="prepaid | postpaid"),
    customer_type: Optional[CustomerType] = Query(
        None, description="new | existing (paketleri 'any' olanlar her ikisinde de doner)"
    ),
    max_price: Optional[float] = Query(None, ge=0, le=1_000_000, description="max monthly price in TL"),
    min_gb: Optional[float] = Query(None, ge=0, le=100_000, description="minimum internet GB"),
) -> list[Package]:
    stmt = select(Package).where(Package.product_scope == "mobile_voice")
    if line_type:
        stmt = stmt.where(Package.line_type == line_type)
    if customer_type:
        stmt = stmt.where(Package.customer_type.in_([customer_type, "any"]))
    if operator:
        stmt = stmt.where(Package.operator == operator)
    if max_price is not None:
        stmt = stmt.where(Package.price_try <= max_price)
    if min_gb is not None:
        # General-use internet (restricted quotas excluded); sinirsiz (-1) her esigi asar.
        general = Package.general_internet_gb
        stmt = stmt.where(or_(general == UNLIMITED, general >= min_gb))
    stmt = stmt.order_by(Package.price_try.asc().nulls_last())
    return list(session.scalars(stmt).all())


class CompareRequest(BaseModel):
    # Parsed as UUIDs here, so a malformed id is a 422 before any SQL runs.
    package_ids: list[uuid.UUID] | None = Field(None, max_length=MAX_COMPARE_IDS)
    gb: float | None = Field(None, ge=0, le=100_000)
    operator: Operator | None = None
    line_type: LineType | None = None
    customer_type: CustomerType | None = None


@app.post("/compare", response_model=list[PackageOut])
@limiter.limit(settings.compare_rate_limit)
def compare(
    request: Request, req: CompareRequest, session: Session = Depends(get_session)
) -> list[Package]:
    """Side-by-side comparison rows for the table: by explicit ids, by GB band,
    or by operator (defaults to all packages, cheapest first)."""
    stmt = select(Package).where(Package.product_scope == "mobile_voice")
    if req.line_type:
        stmt = stmt.where(Package.line_type == req.line_type)
    if req.customer_type:
        stmt = stmt.where(Package.customer_type.in_([req.customer_type, "any"]))
    if req.package_ids:
        stmt = stmt.where(Package.id.in_(req.package_ids))
    if req.gb is not None:
        stmt = stmt.where(gb_band_filter(req.gb))
    if req.operator:
        stmt = stmt.where(Package.operator == req.operator)
    stmt = stmt.order_by(Package.price_try.asc().nulls_last())
    return list(session.scalars(stmt).all())


@app.post("/chat", response_model=ChatResponse)
@limiter.limit(settings.chat_rate_limit)
def chat(
    request: Request,
    req: ChatRequest,
    user: AuthUser = Depends(get_current_user),
) -> ChatResponse:
    """No request-wide database session: each database step uses its own short
    session, so no pooled connection is held while the embedding model or the LLM
    runs (seconds to tens of seconds)."""
    from app.rag.clarify import collect_slots, should_clarify
    from app.rag.embed import EmbeddingUnavailable
    from app.rag.retrieve import retrieve_with_notes
    from app.rag.generate import ProviderUnavailable, generate_answer, generate_clarification

    reservation = reserve_quota(user, client_ip(request))  # 429 if over quota
    try:
        # Don't recommend before the need is clear: if info is missing, skip retrieval
        # entirely and give the model no sources. At most one clarification turn (see rag.clarify).
        slots = collect_slots(req.question, req.history)
        if should_clarify(slots, req.history):
            response = generate_clarification(req.question, req.history, slots.missing)
        else:
            # The session only checks out a connection at its first query, which runs
            # after the query embedding; it is returned before the LLM call.
            with db.SessionLocal() as session:
                packages, notes = retrieve_with_notes(session, req.question, req.history)
            response = generate_answer(req.question, packages, req.history, notes)
    except ProviderUnavailable as exc:
        release_quota(reservation)
        # A failed call is not a used message, and a non-2xx status keeps clients from
        # storing the notice as an assistant answer.
        raise HTTPException(status_code=503, detail={"reason": "provider_unavailable"}) from exc
    except EmbeddingUnavailable as exc:
        release_quota(reservation)
        raise HTTPException(status_code=503, detail={"reason": "embedding_unavailable"}) from exc
    except BaseException:
        release_quota(reservation)  # includes database errors and cancellations
        raise

    try:
        commit_quota(reservation)
    except SQLAlchemyError as exc:
        # The answer exists; refusing it now would waste the provider call. The
        # reservation stops counting after its TTL, so this message goes uncharged.
        logger.error("quota commit failed: %s", type(exc).__name__)
    return response
