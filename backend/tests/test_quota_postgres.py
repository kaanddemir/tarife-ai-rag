"""Quota concurrency and /chat connection lifecycle against a DISPOSABLE PostgreSQL.

Skipped unless TARIFE_TEST_DATABASE_URL is set; refused for any non-local host
(see test_ingest_postgres.py for how to start one). Drops and recreates the quota
tables in that database.
"""
import os
import random
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.orm import sessionmaker

import app.main as main_mod
import app.quota as quota_mod
import app.rag.generate as generate_mod
import app.rag.retrieve as retrieve_mod
from app.auth import AuthUser, get_current_user
from app.config import settings
from app.ingest.load import run as ingest
from app.models import Base, MessageUsage, QuotaReservation
from app.quota import commit_quota, release_quota, reserve_quota

URL = os.environ.get("TARIFE_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TARIFE_TEST_DATABASE_URL not set (needs disposable Postgres)")

if URL and urlparse(URL.replace("+psycopg", "")).hostname not in ("localhost", "127.0.0.1", "::1"):
    raise RuntimeError("test_quota_postgres refuses non-local databases")


@pytest.fixture
def factory():
    eng = create_engine(URL, future=True, pool_size=30, max_overflow=10)
    with eng.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS message_usage, quota_reservation"))
    Base.metadata.create_all(eng, tables=[MessageUsage.__table__, QuotaReservation.__table__])
    yield sessionmaker(bind=eng, future=True)
    eng.dispose()


def _rows(factory, model, *where):
    with factory() as s:
        return s.scalar(select(func.count()).select_from(model).where(*where))


def _guest():
    return AuthUser(id=uuid.uuid4(), is_anonymous=True)


def _member():
    return AuthUser(id=uuid.uuid4(), is_anonymous=False, email="member@example.com")


def _race(n, fn):
    """Run fn n times, released together by a barrier; return (ok, reasons)."""
    barrier = threading.Barrier(n)
    ok, reasons, lock = [], [], threading.Lock()

    def worker(i):
        barrier.wait()
        try:
            result = fn(i)
            with lock:
                ok.append(result)
        except HTTPException as exc:
            with lock:
                reasons.append(exc.detail["reason"])

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not any(t.is_alive() for t in threads), "deadlock or hang"
    return ok, reasons


# ---- 5. concurrent quota enforcement ----

def test_parallel_guest_requests_cannot_exceed_the_lifetime_limit(factory):
    user = _guest()
    ok, reasons = _race(20, lambda i: reserve_quota(user, "198.51.100.1", session_factory=factory))
    assert len(ok) == settings.guest_message_limit == 5
    assert reasons == ["guest_limit"] * 15


def test_parallel_guests_on_one_ip_cannot_exceed_the_ip_limit(factory):
    ok, reasons = _race(25, lambda i: reserve_quota(_guest(), "198.51.100.2", session_factory=factory))
    assert len(ok) == settings.guest_ip_daily_limit == 15
    assert reasons == ["ip_limit"] * 10


def test_parallel_signed_in_requests_cannot_exceed_the_daily_limit(factory):
    user = _member()
    ok, reasons = _race(30, lambda i: reserve_quota(user, "198.51.100.3", session_factory=factory))
    assert len(ok) == settings.daily_message_limit == 20
    assert reasons == ["daily_limit"] * 10


def test_full_cycle_under_contention_charges_exactly_the_limit(factory):
    """Reserve -> (simulated work) -> commit or release, all in parallel."""
    user = _guest()

    def request(i):
        r = reserve_quota(user, "198.51.100.4", session_factory=factory)
        time.sleep(0.01)
        if i % 2:
            release_quota(r, session_factory=factory)  # provider failure
            return "released"
        commit_quota(r, session_factory=factory)
        return "charged"

    for _ in range(4):  # failed requests hand their slot back to later ones
        _race(10, request)
        assert _rows(factory, MessageUsage, MessageUsage.user_id == user.id) <= settings.guest_message_limit
    assert _rows(factory, QuotaReservation) == 0
    # Every released slot is still available: fill up serially to exactly the limit.
    while _rows(factory, MessageUsage, MessageUsage.user_id == user.id) < settings.guest_message_limit:
        commit_quota(reserve_quota(user, "198.51.100.4", session_factory=factory), session_factory=factory)
    with pytest.raises(HTTPException):
        reserve_quota(user, "198.51.100.4", session_factory=factory)


def test_mixed_keys_do_not_deadlock(factory):
    users = [_guest() for _ in range(6)] + [_member() for _ in range(3)]
    ips = ["203.0.113.1", "203.0.113.2", "203.0.113.3"]

    def request(i):
        r = reserve_quota(random.choice(users), random.choice(ips), session_factory=factory)
        commit_quota(r, session_factory=factory)

    ok, reasons = _race(40, request)
    assert len(ok) + len(reasons) == 40


# ---- 3/4. charging semantics ----

def test_success_is_charged_exactly_once(factory):
    user = _guest()
    r = reserve_quota(user, "198.51.100.5", session_factory=factory)
    commit_quota(r, session_factory=factory)
    commit_quota(r, session_factory=factory)  # a duplicate call is a no-op
    release_quota(r, session_factory=factory)  # and cannot un-charge
    assert _rows(factory, MessageUsage, MessageUsage.user_id == user.id) == 1
    assert _rows(factory, QuotaReservation) == 0


def test_failed_request_is_not_charged_and_frees_the_slot(factory):
    user = _guest()
    for _ in range(settings.guest_message_limit):
        commit_quota(reserve_quota(user, None, session_factory=factory), session_factory=factory)
    with pytest.raises(HTTPException):
        reserve_quota(user, None, session_factory=factory)

    other = _guest()
    held = [reserve_quota(other, None, session_factory=factory) for _ in range(settings.guest_message_limit)]
    with pytest.raises(HTTPException):
        reserve_quota(other, None, session_factory=factory)  # in-flight slots count
    release_quota(held[0], session_factory=factory)
    reserve_quota(other, None, session_factory=factory)  # a released slot is reusable
    assert _rows(factory, MessageUsage, MessageUsage.user_id == other.id) == 0


def test_crashed_request_reservation_expires(factory, monkeypatch):
    monkeypatch.setattr(settings, "quota_reservation_ttl_seconds", 1)
    user = _guest()
    for _ in range(settings.guest_message_limit):
        reserve_quota(user, None, session_factory=factory)  # never settled: "crashed"
    with pytest.raises(HTTPException):
        reserve_quota(user, None, session_factory=factory)
    time.sleep(1.2)
    reserve_quota(user, None, session_factory=factory)  # expired holds no longer count
    assert _rows(factory, QuotaReservation) == 1  # and were cleaned up


def test_commit_after_expiry_still_charges_once(factory, monkeypatch):
    monkeypatch.setattr(settings, "quota_reservation_ttl_seconds", 1)
    user = _guest()
    r = reserve_quota(user, None, session_factory=factory)
    time.sleep(1.2)
    reserve_quota(_guest(), None, session_factory=factory)  # cleans r's row up
    commit_quota(r, session_factory=factory)
    assert _rows(factory, MessageUsage, MessageUsage.user_id == user.id) == 1


def test_guest_and_signed_in_limits_stay_distinct(factory):
    ip = "198.51.100.6"
    for _ in range(settings.guest_ip_daily_limit):
        commit_quota(reserve_quota(_guest(), ip, session_factory=factory), session_factory=factory)
    with pytest.raises(HTTPException) as err:
        reserve_quota(_guest(), ip, session_factory=factory)
    assert err.value.detail["reason"] == "ip_limit"
    reserve_quota(_member(), ip, session_factory=factory)  # signed-in users ignore the guest IP cap


def test_exempt_account_is_never_recorded(factory, monkeypatch):
    monkeypatch.setattr(settings, "quota_exempt_emails", "admin@example.com")
    admin = AuthUser(id=uuid.uuid4(), is_anonymous=False, email="Admin@Example.com")
    for _ in range(settings.daily_message_limit + 5):
        r = reserve_quota(admin, None, session_factory=factory)
        assert r is None
        commit_quota(r, session_factory=factory)
    assert _rows(factory, MessageUsage) == 0


def test_yesterdays_usage_does_not_count_today(factory):
    user = _member()
    with factory() as s, s.begin():
        old = datetime.now(timezone.utc) - timedelta(days=1, hours=1)
        s.add_all(MessageUsage(user_id=user.id, ip=None, is_anonymous=False, created_at=old)
                  for _ in range(settings.daily_message_limit))
    reserve_quota(user, None, session_factory=factory)


def test_retention_keeps_every_quota_decision(factory):
    """scripts/usage_retention.sql drops IPs and signed-in rows older than two days; the
    guest lifetime cap (by user id) and today's caps must decide exactly as before."""
    from pathlib import Path

    old = datetime.now(timezone.utc) - timedelta(days=3)
    guest, member, ip = _guest(), _member(), "203.0.113.7"
    with factory() as s, s.begin():
        s.add_all(MessageUsage(user_id=guest.id, ip=ip, is_anonymous=True, created_at=old)
                  for _ in range(settings.guest_message_limit))
        s.add_all(MessageUsage(user_id=member.id, ip=None, is_anonymous=False, created_at=old) for _ in range(3))
    sql = Path(__file__).resolve().parents[1].joinpath("scripts/usage_retention.sql").read_text()
    body = "\n".join(line for line in sql.splitlines() if not line.startswith(("--", "\\")))
    with factory() as s, s.begin():  # the script's own BEGIN/COMMIT become this transaction
        for statement in (st.strip() for st in body.split(";")):
            if statement and statement.upper() not in ("BEGIN", "COMMIT"):
                s.execute(text(statement))
    assert _rows(factory, MessageUsage, MessageUsage.ip.is_not(None)) == 0
    assert _rows(factory, MessageUsage, MessageUsage.is_anonymous.is_(False)) == 0
    with pytest.raises(HTTPException) as exc:  # the guest's lifetime cap still holds
        reserve_quota(guest, ip, session_factory=factory)
    assert exc.value.detail == {"reason": "guest_limit"}
    assert reserve_quota(member, None, session_factory=factory) is not None


# ---- 6/14. /chat on Postgres: no connection held during the LLM call ----

def _fake_embed(texts):
    rng = random.Random(0)
    return [[rng.uniform(-1, 1) for _ in range(settings.embed_dim)] for _ in texts]


@pytest.fixture
def chat_on_postgres(monkeypatch):
    """The real /chat pipeline on a tiny pool; only auth, embedding and LLM are faked."""
    eng = create_engine(URL, future=True, pool_size=2, max_overflow=0, pool_timeout=1)
    with eng.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.execute(text("DROP TABLE IF EXISTS packages, message_usage, quota_reservation"))
    Base.metadata.create_all(eng, tables=[MessageUsage.__table__, QuotaReservation.__table__])
    ingest(engine=eng, embed=_fake_embed)
    checked_out = [0]
    event.listen(eng, "checkout", lambda *a: checked_out.__setitem__(0, checked_out[0] + 1))
    event.listen(eng, "checkin", lambda *a: checked_out.__setitem__(0, checked_out[0] - 1))

    factory = sessionmaker(bind=eng, future=True)
    monkeypatch.setattr(main_mod.db, "SessionLocal", factory)
    monkeypatch.setattr(quota_mod, "_session_factory", lambda: factory)
    monkeypatch.setattr(retrieve_mod, "embed_query", lambda q: _fake_embed([q])[0])
    monkeypatch.setattr(main_mod.limiter, "enabled", False)
    user = AuthUser(id=uuid.uuid4(), is_anonymous=False)
    main_mod.app.dependency_overrides[get_current_user] = lambda: user
    yield TestClient(main_mod.app, raise_server_exceptions=False), checked_out, factory, user
    main_mod.app.dependency_overrides.clear()
    eng.dispose()


def _llm(monkeypatch, behaviour):
    monkeypatch.setattr(generate_mod, "_client", lambda: SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: behaviour()))))


_Q = {"question": "faturalı 20 GB 500 TL altı", "history": []}


def test_no_connection_is_checked_out_during_the_llm_call(chat_on_postgres, monkeypatch):
    client, checked_out, factory, user = chat_on_postgres
    during = []

    def answer():
        during.append(checked_out[0])
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="Öneri [S1]"))])
    _llm(monkeypatch, answer)
    res = client.post("/chat", json=_Q)
    assert res.status_code == 200 and res.json()["citations"][0]["id"] == "S1"
    assert during == [0]
    assert checked_out[0] == 0
    assert _rows(factory, MessageUsage, MessageUsage.user_id == user.id) == 1


def test_provider_failure_on_postgres_charges_nothing(chat_on_postgres, monkeypatch):
    client, checked_out, factory, user = chat_on_postgres

    def fail():
        raise TimeoutError("provider down")
    _llm(monkeypatch, fail)
    assert client.post("/chat", json=_Q).status_code == 503
    assert _rows(factory, MessageUsage) == 0 and _rows(factory, QuotaReservation) == 0
    assert checked_out[0] == 0


def test_slow_llm_calls_do_not_exhaust_a_small_pool(chat_on_postgres, monkeypatch):
    """6 concurrent requests, 1.5 s LLM each, pool of 2 with a 1 s checkout timeout.
    Holding a connection through the LLM call (the pre-2B lifecycle) would time out
    four of them; with short sessions all succeed."""
    client, checked_out, factory, user = chat_on_postgres

    def slow():
        time.sleep(1.5)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="Öneri [S1]"))])
    _llm(monkeypatch, slow)
    statuses, lock = [], threading.Lock()

    def send():
        r = client.post("/chat", json=_Q)
        with lock:
            statuses.append(r.status_code)
    threads = [threading.Thread(target=send) for _ in range(6)]
    started = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert statuses == [200] * 6
    assert time.monotonic() - started < 6  # ran in parallel, not serialized on the pool
    assert _rows(factory, MessageUsage, MessageUsage.user_id == user.id) == 6
