"""Message-based quotas — the primary abuse control for the paid LLM endpoint.

Guests (anonymous Supabase users) get a small lifetime allowance and, as a backstop
against clearing storage to mint a fresh guest, a per-IP daily cap. Signed-in users get
a per-day allowance. Enforced before the LLM call; usage is only recorded on success.
"""
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import AuthUser
from app.config import settings
from app.models import MessageUsage


def _start_of_utc_day() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _count(session: Session, *conditions) -> int:
    stmt = select(func.count()).select_from(MessageUsage)
    for c in conditions:
        stmt = stmt.where(c)
    return session.scalar(stmt) or 0


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


def enforce_quota(session: Session, user: AuthUser, ip: str | None) -> None:
    """Raise 429 with a machine-readable reason if the caller is over their quota.

    reasons: "guest_limit" (guest lifetime), "ip_limit" (guest per-IP/day),
    "daily_limit" (signed-in per-day). The frontend maps these to login vs. wait.
    """
    if is_exempt(user):
        return

    if user.is_anonymous:
        total = _count(session, MessageUsage.user_id == user.id)
        if total >= settings.guest_message_limit:
            raise HTTPException(status_code=429, detail={"reason": "guest_limit"})

        if ip:
            ip_today = _count(
                session,
                MessageUsage.ip == ip,
                MessageUsage.is_anonymous.is_(True),
                MessageUsage.created_at >= _start_of_utc_day(),
            )
            if ip_today >= settings.guest_ip_daily_limit:
                raise HTTPException(status_code=429, detail={"reason": "ip_limit"})
    else:
        today = _count(
            session,
            MessageUsage.user_id == user.id,
            MessageUsage.created_at >= _start_of_utc_day(),
        )
        if today >= settings.daily_message_limit:
            raise HTTPException(status_code=429, detail={"reason": "daily_limit"})


def record_usage(session: Session, user: AuthUser, ip: str | None) -> None:
    """Log one consumed message. Call only after a successful answer so failed LLM
    calls don't burn the user's quota. Exempt accounts aren't logged (keeps the table
    clean and their usage uncounted)."""
    if is_exempt(user):
        return
    session.add(
        MessageUsage(user_id=user.id, ip=ip, is_anonymous=user.is_anonymous)
    )
    session.commit()
