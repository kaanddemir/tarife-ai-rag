"""Message-based quotas — the primary abuse control for the paid LLM endpoint.

Guests (anonymous Supabase users) get a small lifetime allowance and, as a backstop
against clearing storage to mint a fresh guest, a per-IP daily cap. Signed-in users get
a per-day allowance.

Concurrency-safe accounting via reservations:

  reserve_quota  one short transaction: take a transaction-scoped advisory lock per
                 quota key (user, and the guest's IP), count consumed messages plus
                 live reservations, then insert a reservation. Parallel requests for
                 the same key serialize on the lock, so they cannot all pass the check.
  commit_quota   on success: the reservation becomes one message_usage row (one
                 transaction, so it is charged exactly once).
  release_quota  on failure (provider down, embedding error, crash path): the
                 reservation is deleted and nothing is charged.

A reservation whose request died without either call stops counting after
QUOTA_RESERVATION_TTL_SECONDS and is deleted by the next reserve. The locks live in
PostgreSQL, so this holds across threads, worker processes and instances, not just
inside one Python process. Each transaction is short: no connection is held while
the embedding model or the LLM runs.
"""
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.auth import AuthUser
from app.config import settings
from app.models import MessageUsage, QuotaReservation

logger = logging.getLogger(__name__)


def _session_factory() -> sessionmaker:
    from app.db import SessionLocal

    return SessionLocal


@dataclass
class Reservation:
    id: uuid.UUID
    user_id: uuid.UUID
    ip: str | None
    is_anonymous: bool
    settled: bool = field(default=False)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _start_of_utc_day(now: datetime) -> datetime:
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def is_exempt(user: AuthUser) -> bool:
    """True for allowlisted signed-in accounts (your own testing/admin email)."""
    if not user.email:
        return False
    exempt = {
        e.strip().lower()
        for e in settings.quota_exempt_emails.split(",")
        if e.strip()
    }
    return user.email.lower() in exempt


def _count(session: Session, model, *conditions) -> int:
    return session.scalar(select(func.count()).select_from(model).where(*conditions)) or 0


def _used(session: Session, now: datetime, *, user_id=None, ip=None, since=None) -> int:
    """Consumed messages plus live reservations for one quota key."""
    usage, held = [], [QuotaReservation.expires_at > now]
    if user_id is not None:
        usage.append(MessageUsage.user_id == user_id)
        held.append(QuotaReservation.user_id == user_id)
    if ip is not None:
        usage += [MessageUsage.ip == ip, MessageUsage.is_anonymous.is_(True)]
        held += [QuotaReservation.ip == ip, QuotaReservation.is_anonymous.is_(True)]
    if since is not None:
        usage.append(MessageUsage.created_at >= since)
    return _count(session, MessageUsage, *usage) + _count(session, QuotaReservation, *held)


def _lock(session: Session, keys: list[str]) -> None:
    # Sorted, so two requests that share a key always lock in the same order (no
    # deadlock). Released automatically at commit/rollback.
    for key in sorted(keys):
        session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": key})


def _check(session: Session, user: AuthUser, ip: str | None, now: datetime) -> None:
    """Raise 429 with a machine-readable reason if the caller is over their quota.

    reasons: "guest_limit" (guest lifetime), "ip_limit" (guest per-IP/day),
    "daily_limit" (signed-in per-day). The frontend maps these to login vs. wait.
    """
    today = _start_of_utc_day(now)
    if user.is_anonymous:
        if _used(session, now, user_id=user.id) >= settings.guest_message_limit:
            raise HTTPException(status_code=429, detail={"reason": "guest_limit"})
        if ip and _used(session, now, ip=ip, since=today) >= settings.guest_ip_daily_limit:
            raise HTTPException(status_code=429, detail={"reason": "ip_limit"})
    elif _used(session, now, user_id=user.id, since=today) >= settings.daily_message_limit:
        raise HTTPException(status_code=429, detail={"reason": "daily_limit"})


def reserve_quota(user: AuthUser, ip: str | None, *, session_factory=None) -> Reservation | None:
    """Hold one message slot, or raise 429. Returns None for exempt accounts (their
    usage is not recorded, which keeps the table clean and their usage uncounted)."""
    if is_exempt(user):
        return None
    factory = session_factory or _session_factory()
    now = _now()
    keys = [f"quota:user:{user.id}"]
    if user.is_anonymous and ip:
        keys.append(f"quota:ip:{ip}")
    with factory() as session, session.begin():
        _lock(session, keys)
        session.execute(delete(QuotaReservation).where(QuotaReservation.expires_at <= now))
        _check(session, user, ip, now)
        row = QuotaReservation(
            user_id=user.id,
            ip=ip,
            is_anonymous=user.is_anonymous,
            expires_at=now + timedelta(seconds=settings.quota_reservation_ttl_seconds),
        )
        session.add(row)
        session.flush()
        return Reservation(id=row.id, user_id=user.id, ip=ip, is_anonymous=user.is_anonymous)


def commit_quota(reservation: Reservation | None, *, session_factory=None) -> None:
    """Charge one message. Idempotent per reservation: a second call is a no-op."""
    if reservation is None or reservation.settled:
        return
    factory = session_factory or _session_factory()
    with factory() as session, session.begin():
        # Even if the reservation already expired and was cleaned up, the request did
        # succeed, so it is still charged -- once.
        session.execute(delete(QuotaReservation).where(QuotaReservation.id == reservation.id))
        session.add(
            MessageUsage(user_id=reservation.user_id, ip=reservation.ip, is_anonymous=reservation.is_anonymous)
        )
    reservation.settled = True


def release_quota(reservation: Reservation | None, *, session_factory=None) -> None:
    """Return the slot without charging. Never raises: if the database is down, the
    reservation simply expires after the TTL."""
    if reservation is None or reservation.settled:
        return
    reservation.settled = True
    factory = session_factory or _session_factory()
    try:
        with factory() as session, session.begin():
            session.execute(delete(QuotaReservation).where(QuotaReservation.id == reservation.id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("quota release failed (%s); the reservation will expire", type(exc).__name__)
