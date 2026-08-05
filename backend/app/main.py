from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Depends, FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.auth import AuthUser, get_current_user
from app.config import settings
from app.db import get_session, init_db
from app.ingest.normalize import UNLIMITED
from app.models import ChatRequest, ChatResponse, Package, PackageOut
from app.quota import enforce_quota, record_usage
from app.rag.retrieve import gb_band_filter


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fail at startup rather than on a user's first question: an unset key is a
    # missing .env, and the answer to that is a setup step, not a chat error.
    if not settings.provider_api_key:
        raise RuntimeError("PROVIDER_API_KEY is not set. See backend/.env.example.")

    init_db()
    from app.auth import warm_jwks

    warm_jwks()
    yield


# Per-client rate limiting. In-memory (single process); behind multiple workers
# a reverse proxy (nginx/Cloudflare) should enforce the real limit — this is the
# first line of defense against abuse of the paid LLM endpoint.
limiter = Limiter(key_func=get_remote_address)

app = FastAPI(title="TarifeAI", version="0.1.0", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

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
    operator: Optional[str] = None,
    line_type: Optional[str] = Query(None, description="prepaid | postpaid"),
    customer_type: Optional[str] = Query(
        None, description="new | existing (paketleri 'any' olanlar her ikisinde de doner)"
    ),
    max_price: Optional[float] = Query(None, description="max monthly price in TL"),
    min_gb: Optional[float] = Query(None, description="minimum internet GB"),
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
        # Sinirsiz (-1) her min_gb esigini asar.
        stmt = stmt.where(or_(Package.internet_gb == UNLIMITED, Package.internet_gb >= min_gb))
    stmt = stmt.order_by(Package.price_try.asc().nulls_last())
    return list(session.scalars(stmt).all())


class CompareRequest(BaseModel):
    package_ids: list[str] | None = None
    gb: float | None = None
    operator: str | None = None
    line_type: str | None = None
    customer_type: str | None = None


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
    session: Session = Depends(get_session),
    user: AuthUser = Depends(get_current_user),
) -> ChatResponse:
    from app.rag.clarify import collect_slots, should_clarify
    from app.rag.history import contextual_question
    from app.rag.retrieve import retrieve
    from app.rag.generate import generate_answer, generate_clarification

    ip = get_remote_address(request)
    enforce_quota(session, user, ip)

    # Don't recommend before the need is clear: if info is missing, skip retrieval
    # entirely and give the model no sources. At most one clarification turn (see rag.clarify).
    slots = collect_slots(req.question, req.history)
    if should_clarify(slots, req.history):
        response = generate_clarification(req.question, req.history, slots.missing)
    else:
        packages = retrieve(session, contextual_question(req.question, req.history))
        response = generate_answer(req.question, packages, req.history)

    record_usage(session, user, ip)
    return response
