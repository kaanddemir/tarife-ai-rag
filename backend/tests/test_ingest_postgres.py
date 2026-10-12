"""Transactional ingestion tests against a DISPOSABLE PostgreSQL + pgvector database.

Skipped unless TARIFE_TEST_DATABASE_URL is set, and refused for any non-local
host so it can never run against Supabase/production. These tests DROP and
recreate tables in that database. Example:

    initdb -D /tmp/pg -U tester -A trust
    pg_ctl -D /tmp/pg -o "-k '' -c listen_addresses=127.0.0.1 -p 55439" start
    createdb -h 127.0.0.1 -p 55439 -U tester tarife_test
    TARIFE_TEST_DATABASE_URL=postgresql+psycopg://tester@127.0.0.1:55439/tarife_test \\
        python -m pytest tests/test_ingest_postgres.py
"""
import hashlib
import json
import os
import random
import uuid
from urllib.parse import urlparse

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

import app.ingest.load as load_mod
import app.rag.retrieve as retrieve_mod
from app.ingest.load import SchemaMismatchError, load_records, package_id, run
from app.ingest.validate import CatalogValidationError
from app.models import Base, MessageUsage, Package
from app.rag.retrieve import EXISTING_CUSTOMER_UNAVAILABLE, retrieve_with_notes

URL = os.environ.get("TARIFE_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TARIFE_TEST_DATABASE_URL not set (needs disposable Postgres)")

if URL and urlparse(URL.replace("+psycopg", "")).hostname not in ("localhost", "127.0.0.1", "::1"):
    raise RuntimeError("test_ingest_postgres refuses non-local databases")

DIM = 1024


def fake_embed(texts):
    """Deterministic unit vectors: same text -> same vector, run after run."""
    out = []
    for t in texts:
        rng = random.Random(hashlib.sha256(t.encode()).digest())
        vec = [rng.uniform(-1, 1) for _ in range(DIM)]
        norm = sum(x * x for x in vec) ** 0.5
        out.append([x / norm for x in vec])
    return out


@pytest.fixture
def engine():
    eng = create_engine(URL, future=True)
    with eng.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.execute(text("DROP TABLE IF EXISTS packages, message_usage"))
    Base.metadata.create_all(eng, tables=[Package.__table__, MessageUsage.__table__])
    yield eng
    eng.dispose()


def snapshot(eng):
    with eng.connect() as conn:
        rows = conn.execute(
            text("SELECT id, name, doc_text, embedding::text FROM packages ORDER BY id")
        ).all()
    return [tuple(r) for r in rows]


def count(eng, model=Package):
    with Session(eng) as s:
        return s.scalar(select(func.count()).select_from(model))


ACTIVE = 105  # 2026-10-12 refresh: active records only


def test_ingest_fills_the_catalog(engine):
    assert run(engine=engine, embed=fake_embed) == ACTIVE
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM packages")).scalar() == ACTIVE
        assert conn.execute(text("SELECT DISTINCT vector_dims(embedding) FROM packages")).scalars().all() == [DIM]
        assert conn.execute(text("SELECT count(*) FROM packages WHERE embedding IS NULL")).scalar() == 0
        dates = conn.execute(text("SELECT DISTINCT verified_at::date::text FROM packages")).scalars().all()
        assert dates == ["2026-10-12"]  # source verification date, not ingest time
        # Unknown stays SQL NULL (never the JSON literal 'null', which IS NULL misses).
        assert conn.execute(text("SELECT count(*) FROM packages WHERE activation_channels IS NULL")).scalar() == 1
        assert conn.execute(text(
            "SELECT count(*) FROM packages WHERE jsonb_typeof(activation_channels) = 'null' "
            "OR jsonb_typeof(social_media_apps) = 'null'")).scalar() == 0
        lines = conn.execute(text("SELECT DISTINCT line_type FROM packages ORDER BY 1")).scalars().all()
        assert lines == ["postpaid", "prepaid"]
        stale = conn.execute(
            text("SELECT count(*) FROM packages WHERE internet_gb <> -1 AND "
                 "doc_text LIKE '%sinirsiz genel internet%'")
        ).scalar()
        assert stale == 0
    with Session(engine) as s:
        assert s.get(Package, package_id("turk_telekom", "postpaid", "Dijital Lider Prime 40 GB")) is None
        assert s.get(Package, package_id("turk_telekom", "postpaid", "Yeni Dijital Lider 40 GB")) is not None


def test_repeated_ingest_is_identical(engine):
    run(engine=engine, embed=fake_embed)
    first = snapshot(engine)
    run(engine=engine, embed=fake_embed)
    assert snapshot(engine) == first  # same ids, text and vectors


def test_message_usage_is_preserved(engine):
    with Session(engine) as s, s.begin():
        s.add(MessageUsage(user_id=uuid.uuid4(), ip="127.0.0.1", is_anonymous=True))
    run(engine=engine, embed=fake_embed)
    run(engine=engine, embed=fake_embed)
    assert count(engine, MessageUsage) == 1


def test_embedding_failure_leaves_catalog_untouched(engine):
    run(engine=engine, embed=fake_embed)
    before = snapshot(engine)

    def boom(texts):
        raise RuntimeError("embedding model failed")

    with pytest.raises(RuntimeError):
        run(engine=engine, embed=boom)
    assert snapshot(engine) == before


def test_validation_failure_leaves_catalog_untouched(engine, monkeypatch):
    run(engine=engine, embed=fake_embed)
    before = snapshot(engine)
    broken = json.loads(json.dumps(load_records()))
    target = next(r for r in broken if r["name"] == "Yeni Dijital Lider 1 GB")
    target["internet_gb"] = -2  # an invalid quantity
    monkeypatch.setattr(load_mod, "load_records", lambda: broken)
    with pytest.raises(CatalogValidationError):
        run(engine=engine, embed=fake_embed)
    assert snapshot(engine) == before


def test_partial_insert_failure_rolls_back_everything(engine, monkeypatch):
    run(engine=engine, embed=fake_embed)
    before = snapshot(engine)
    real_row = load_mod._row
    calls = {"n": 0}

    def row_that_breaks_midway(rec, vec):
        calls["n"] += 1
        row = real_row(rec, vec)
        if calls["n"] == 60:
            row.name = "x" * 300  # exceeds varchar(256): the DB rejects it mid-insert
        return row

    monkeypatch.setattr(load_mod, "_row", row_that_breaks_midway)
    with pytest.raises(Exception):
        run(engine=engine, embed=fake_embed)
    assert snapshot(engine) == before


def test_replacement_is_atomic_for_concurrent_readers(engine):
    # An "old" catalog of 3 rows, then a replacement that pauses before commit.
    run(engine=engine, embed=fake_embed)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM packages WHERE id NOT IN (SELECT id FROM packages ORDER BY id LIMIT 3)"))
    seen = {}

    def peek(session):
        seen["inside"] = session.scalar(select(func.count()).select_from(Package))
        with engine.connect() as other:  # a separate connection = another API request
            seen["outside"] = other.execute(text("SELECT count(*) FROM packages")).scalar()

    run(engine=engine, embed=fake_embed, before_commit=peek)
    assert seen == {"inside": ACTIVE, "outside": 3}
    assert count(engine) == ACTIVE


def test_failure_at_commit_time_restores_previous_catalog(engine):
    run(engine=engine, embed=fake_embed)
    before = snapshot(engine)

    def fail(session):
        raise RuntimeError("simulated failure before commit")

    with pytest.raises(RuntimeError):
        run(engine=engine, embed=fake_embed, before_commit=fail)
    assert snapshot(engine) == before


def test_schema_mismatch_fails_without_dropping(engine):
    run(engine=engine, embed=fake_embed)
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE packages ADD COLUMN legacy_col int"))
    before = snapshot(engine)
    with pytest.raises(SchemaMismatchError, match="unexpected: \\['legacy_col'\\]"):
        run(engine=engine, embed=fake_embed)
    assert snapshot(engine) == before


def test_vector_dimension_mismatch_fails(engine):
    run(engine=engine, embed=fake_embed)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM packages"))
        conn.execute(text("ALTER TABLE packages ALTER COLUMN embedding TYPE vector(8)"))
    with pytest.raises(SchemaMismatchError, match="vector\\(8\\)"):
        run(engine=engine, embed=fake_embed)


# ---- retrieval SQL against real rows (query embedding faked) ----

@pytest.fixture
def ingested(engine, monkeypatch):
    run(engine=engine, embed=fake_embed)
    monkeypatch.setattr(retrieve_mod, "embed_query", lambda q: fake_embed([q])[0])
    with Session(engine) as session:
        yield session


def test_retrieval_filters_on_postgres(ingested):
    rows, notes = retrieve_with_notes(ingested, "faturasız 200 TL altı paket")
    assert rows and EXISTING_CUSTOMER_UNAVAILABLE not in notes
    assert all(r.line_type == "prepaid" and float(r.price_try) <= 200 for r in rows)

    rows, _ = retrieve_with_notes(ingested, "Vodafone faturalı en az 1000 TL")
    assert rows and all(r.operator == "vodafone" and r.line_type == "postpaid" and float(r.price_try) >= 1000 for r in rows)


def test_existing_customer_on_postgres_gets_flagged_new_customer_rows(ingested):
    rows, notes = retrieve_with_notes(ingested, "Turkcell müşterisiyim, faturalı paket arıyorum")
    assert rows and notes[0] == EXISTING_CUSTOMER_UNAVAILABLE
    assert all(r.customer_type == "new" for r in rows)


def test_refresh_removes_previously_ingested_closed_offers(engine):
    """Production today still holds closed offers from the 2026-07 snapshot. The
    refreshed ingest must drop them in the same atomic replacement."""
    run(engine=engine, embed=fake_embed)
    stale_id = package_id("turk_telekom", "postpaid", "Dijital Lider 10 GB")  # closed 24.07.2026
    with engine.begin() as conn:  # simulate the old catalog row still being there
        cols = [c.name for c in Package.__table__.columns]
        values = {"id": "CAST(:id AS uuid)", "name": "'Dijital Lider 10 GB'", "verified_at": "'2026-07-10'",
                  "doc_hash": "NULL", "benefits": "NULL", "benefits_status": "'unverified'"}
        conn.execute(text(
            f"INSERT INTO packages ({', '.join(cols)}) SELECT {', '.join(values.get(c, c) for c in cols)} "
            "FROM packages WHERE name = 'Yeni Dijital Lider 10 GB'"), {"id": str(stale_id)})
    assert count(engine) == ACTIVE + 1
    run(engine=engine, embed=fake_embed)
    assert count(engine) == ACTIVE
    with Session(engine) as s:
        assert s.get(Package, stale_id) is None
        prime_100 = s.get(Package, package_id("turk_telekom", "postpaid", "Yeni Nesil Prime 100 GB"))
        assert float(prime_100.price_try) == 1200  # relaunched plan keeps its id, new price


def test_porting_question_never_gets_new_line_only_offers(ingested):
    """Hoş Geldin 50 GB and Fırsat 50 GB are new-line only on the official pages."""
    rows, _ = retrieve_with_notes(ingested, "numara taşıma ile faturasız paket")
    assert rows
    for row in rows:
        channels = set(row.activation_channels or [])
        assert not ("new_line" in channels and "number_porting" not in channels), row.name
    with ingested.bind.connect() as conn:
        unknown = conn.execute(text(
            "SELECT count(*) FROM packages WHERE line_type = 'prepaid' AND activation_channels IS NULL")).scalar()
        # The channel is filtered on activation_channels only; eligibility_tags never carries it.
        assert conn.execute(text(
            "SELECT count(*) FROM packages WHERE eligibility_tags ?| ARRAY['new_line', 'number_porting']")).scalar() == 0
    assert unknown  # unknown-channel offers exist and are not filtered out by the rule above
    # Mirror rule: a new-line question never gets porting-only offers.
    rows, _ = retrieve_with_notes(ingested, "yeni hat ile faturasız paket")
    assert rows
    for row in rows:
        channels = set(row.activation_channels or [])
        assert not ("number_porting" in channels and "new_line" not in channels), row.name
