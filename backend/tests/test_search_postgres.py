"""The whole retrieval benchmark on real rows and real SQL (query embedding faked):
the hard-constraint, eligibility, objective and no-match parts do not depend on the
embedding model, so they must hold exactly."""
import pytest
from sqlalchemy.orm import Session

import app.rag.retrieve as retrieve_mod
from app.ingest.load import build_catalog, run
from app.rag.retrieve import retrieve_with_notes
from retrieval_benchmark_cases import CASES, HELDOUT, expected_top, history_for
from test_ingest_postgres import URL, engine, fake_embed  # noqa: F401 -- fixture reuse

pytestmark = pytest.mark.skipif(not URL, reason="TARIFE_TEST_DATABASE_URL not set (needs disposable Postgres)")


@pytest.fixture(scope="function")
def session(engine, monkeypatch):  # noqa: F811
    run(engine=engine, embed=fake_embed)
    monkeypatch.setattr(retrieve_mod, "embed_query", lambda q: fake_embed([q])[0])
    with Session(engine) as s:
        yield s


def test_benchmark_holds_on_postgres(session):
    records = build_catalog()[0]
    by_name = {r["name"]: r for r in records}
    failures = []
    for case in CASES + HELDOUT:
        packages, notes = retrieve_with_notes(session, case.turns[-1], history_for(case))
        confirmed = [by_name[p.name] for p in packages if p.retrieval_status == "match"]
        if any(not case.must(r) for r in confirmed):
            failures.append((case.id, "violates a hard constraint"))
        if case.empty and confirmed:
            failures.append((case.id, "presented a match where none exists"))
        if case.empty and not notes:
            failures.append((case.id, "no explanation for the empty result"))
        top = expected_top(case, records)
        if top and (not confirmed or confirmed[0]["name"] not in top):
            failures.append((case.id, f"top-1 {confirmed[0]['name'] if confirmed else None} not in {top}"))
        relevant = [r for r in records if case.must(r)]
        if relevant and not case.empty and not confirmed:
            failures.append((case.id, "no confirmed result although matches exist"))
    assert failures == []


def test_regression_sets_hold_their_hard_constraints_on_postgres(session):
    """Held-out + development phrasings: no confirmed result may violate a hard
    constraint and no-match cases must not present a match."""
    from retrieval_regression_cases import EXTRA_HELDOUT, REGRESSION_DEV, REGRESSION_HELDOUT

    by_name = {r["name"]: r for r in build_catalog()[0]}
    failures = []
    for case in REGRESSION_HELDOUT + EXTRA_HELDOUT + REGRESSION_DEV:
        packages, _ = retrieve_with_notes(session, case.turns[-1], history_for(case))
        confirmed = [by_name[p.name] for p in packages if p.retrieval_status == "match"]
        if any(not case.must(r) for r in confirmed) or (case.empty and confirmed):
            failures.append(case.id)
    assert failures == []


def test_retrieved_rows_serialize_after_the_session_closes(engine, monkeypatch):  # noqa: F811
    """/chat closes its session before the LLM call and serializes afterwards; the
    deferred embedding column must never be needed for that."""
    from app.models import PackageOut

    run(engine=engine, embed=fake_embed)
    monkeypatch.setattr(retrieve_mod, "embed_query", lambda q: fake_embed([q])[0])
    with Session(engine) as s:
        packages, _ = retrieve_with_notes(s, "faturalı 40 GB", [])
    assert packages
    out = [PackageOut.model_validate(p) for p in packages]
    assert all(o.benefit_tags is not None for o in out)
