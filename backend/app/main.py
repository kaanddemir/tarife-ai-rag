from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Depends, FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import AuthUser, get_current_user
from app.config import settings
from app.db import get_session, init_db
from app.models import ChatRequest, ChatResponse, Package, PackageOut
from app.quota import enforce_quota, record_usage


@asynccontextmanager
async def lifespan(app: FastAPI):
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
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health(session: Session = Depends(get_session)) -> dict:
    count = session.scalar(select(Package).with_only_columns(Package.id).limit(1))
    total = session.query(Package).count()
    return {"status": "ok", "packages": total, "has_data": count is not None}


@app.get("/packages", response_model=list[PackageOut])
def list_packages(
    session: Session = Depends(get_session),
    operator: Optional[str] = None,
    line_type: str = "prepaid",
    max_price: Optional[float] = Query(None, description="max monthly price in TL"),
    min_gb: Optional[float] = Query(None, description="minimum internet GB"),
) -> list[Package]:
    stmt = select(Package).where(Package.line_type == line_type)
    if operator:
        stmt = stmt.where(Package.operator == operator)
    if max_price is not None:
        stmt = stmt.where(Package.price_try <= max_price)
    if min_gb is not None:
        stmt = stmt.where(Package.internet_gb >= min_gb)
    stmt = stmt.order_by(Package.price_try.asc().nulls_last())
    return list(session.scalars(stmt).all())


class CompareRequest(BaseModel):
    package_ids: list[str] | None = None
    gb: float | None = None
    operator: str | None = None


@app.post("/compare", response_model=list[PackageOut])
@limiter.limit(settings.compare_rate_limit)
def compare(
    request: Request, req: CompareRequest, session: Session = Depends(get_session)
) -> list[Package]:
    """Side-by-side comparison rows for the table: by explicit ids, by GB band,
    or by operator (defaults to all prepaid, cheapest first)."""
    stmt = select(Package).where(Package.line_type == "prepaid")
    if req.package_ids:
        stmt = stmt.where(Package.id.in_(req.package_ids))
    if req.gb is not None:
        stmt = stmt.where(Package.internet_gb >= req.gb - 5).where(
            Package.internet_gb <= req.gb + 5
        )
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
    from app.rag.history import contextual_question
    from app.rag.retrieve import retrieve
    from app.rag.generate import generate_answer

    ip = get_remote_address(request)
    enforce_quota(session, user, ip)

    packages = retrieve(session, contextual_question(req.question, req.history))
    response = generate_answer(req.question, packages, req.history)
    record_usage(session, user, ip)
    return response
