"""Structured search (rag.search) on real catalog rows held in memory: three-valued
constraints, price basis, ranking and labelled compromises. The SQL step is replaced
by a fake that returns every row; eligibility SQL is covered in test_search_postgres."""
import uuid

import pytest

import app.rag.search as search_mod
from app.ingest.load import build_catalog
from app.models import Package
from app.rag.query import TariffQuery, parse_turn
from app.rag.search import COMPROMISE, MATCH, UNVERIFIED, comparison_price, constraints, rank, search

_COLUMNS = {c.name for c in Package.__table__.columns}


def _pkg(**overrides) -> Package:
    rec = {**_BASE, **overrides}
    return Package(id=uuid.uuid4(), **{k: v for k, v in rec.items() if k in _COLUMNS and k != "id"})


_BASE = next(r for r in build_catalog()[0] if r["line_type"] == "prepaid")


@pytest.fixture
def catalog(monkeypatch):
    rows = [Package(id=uuid.uuid4(), **{k: v for k, v in r.items() if k in _COLUMNS and k != "id"})
            for r in build_catalog()[0]]
    general = [p for p in rows if p.audience_segment == "general"]
    monkeypatch.setattr(search_mod, "_candidates", lambda s, f, v: [(p, 0.0) for p in general])
    return general


def test_unknown_is_never_a_match():
    q = TariffQuery(renewal="one_time")
    (renewal,) = [c for c in constraints(q) if c.key == "renewal"]
    assert renewal.check(_pkg(renewal_type="unknown")) is None
    assert renewal.check(_pkg(renewal_type="recurring")) is False
    assert renewal.check(_pkg(renewal_type="one_time")) is True


def test_restricted_quota_is_not_general_internet():
    (min_gb,) = [c for c in constraints(TariffQuery(min_gb=30)) if c.key == "min_gb"]
    assert min_gb.check(_pkg(internet_gb=20, social_media_gb=10)) is False
    assert min_gb.check(_pkg(internet_gb=20, bonus_internet_gb=10, bonus_internet_scope="tethering_only")) is False
    assert min_gb.check(_pkg(internet_gb=20, bonus_internet_gb=10, bonus_internet_scope="general")) is True
    assert min_gb.check(_pkg(internet_gb=-1)) is True
    (unl,) = [c for c in constraints(TariffQuery(unlimited_internet=True)) if c.key == "unlimited_internet"]
    assert unl.check(_pkg(internet_gb=50, social_media_gb=-1)) is False


def test_subscription_is_not_app_data_and_trials_can_be_refused():
    trial = {"category": "subscription", "service": "Netflix Temel Paket", "offer": "free_trial"}
    data = {"category": "app_data", "service": "Netflix", "offer": "included", "apps": ["Netflix"]}
    sub = lambda q: [c for c in constraints(q) if c.key.startswith("subscription")][0]  # noqa: E731
    app = [c for c in constraints(TariffQuery(apps=frozenset({"netflix"}))) if c.key.startswith("app")][0]
    assert sub(TariffQuery(subscriptions=frozenset({"netflix"}))).check(_pkg(benefits=[trial])) is True
    assert sub(TariffQuery(subscriptions=frozenset({"netflix"}))).check(_pkg(benefits=[data])) is False
    assert sub(TariffQuery(subscriptions=frozenset({"netflix"}), no_trials=True)).check(_pkg(benefits=[trial])) is False
    assert app.check(_pkg(benefits=[trial], social_media_gb=None)) is False
    assert app.check(_pkg(benefits=[data])) is True


def test_comparison_price_keeps_original_and_uses_ongoing_basis():
    p = _pkg(price_try=300, validity_days=90, price_period="90_day")
    assert comparison_price(p, TariffQuery()) == pytest.approx(100.0)
    assert float(p.price_try) == 300  # never overwritten
    promo = _pkg(line_type="postpaid", price_try=100, promo_months=1, price_after_promo_try=190, validity_days=None)
    assert comparison_price(promo, TariffQuery()) == 190
    committed = _pkg(line_type="postpaid", validity_days=None, price_try=500, commitment_status="committed",
                     no_commitment_price_try=900)
    assert comparison_price(committed, TariffQuery(no_commitment=True)) == 900


def test_cheapest_uses_30_day_equivalent_with_deterministic_ties():
    a = _pkg(name="A", price_try=200, validity_days=28)
    b = _pkg(name="B", price_try=250, validity_days=90)
    ordered = rank([(a, 0.1), (b, 0.9)], TariffQuery(objective="cheapest"))
    assert [p.name for p, _ in ordered] == ["B", "A"]


def test_most_data_puts_unlimited_first():
    a = _pkg(name="A", internet_gb=100)
    b = _pkg(name="B", internet_gb=-1)
    assert rank([(a, 0), (b, 0)], TariffQuery(objective="most_data"))[0][0].name == "B"


def test_unverified_rows_rank_after_matches_and_are_flagged(catalog):
    result = search(None, parse_turn("faturasız otomatik yenilenmesin"), [0.0], k=40)
    statuses = [p.retrieval_status for p in result.packages]
    assert MATCH in statuses and UNVERIFIED in statuses
    assert statuses == sorted(statuses, key=lambda s: s != MATCH)
    for p in result.packages:
        # A one-time offer limited to some provinces stays unverified: the user's province is unknown.
        confirmed = p.renewal_type == "one_time" and "province_restricted" not in (p.eligibility_tags or [])
        assert confirmed == (p.retrieval_status == MATCH)
        if p.retrieval_status == UNVERIFIED:
            assert "DOGRULANAMADI" in p.retrieval_flags[0]


def test_no_match_returns_labelled_compromises_only(catalog):
    result = search(None, parse_turn("faturasız 200 TL altı sınırsız internet"), [0.0], k=8)
    assert result.packages
    assert all(p.retrieval_status == COMPROMISE for p in result.packages)
    assert all("UZLASMA" in p.retrieval_flags[0] for p in result.packages)
    assert "TAMAMINI karsilayan paket katalogda YOK" in result.notes[0]


def test_hard_requirements_are_never_silently_relaxed(catalog):
    result = search(None, parse_turn("faturasız 200 TL altı"), [0.0], k=8)
    assert result.packages and all(p.retrieval_status == MATCH for p in result.packages)
    assert all(p.line_type == "prepaid" and float(p.price_try) <= 200 for p in result.packages)


def test_prepaid_flags_show_both_prices(catalog):
    result = search(None, parse_turn("faturasız en ucuz"), [0.0], k=3)
    top = result.packages[0]
    assert top.validity_days > 30
    assert any("30 gunluk karsiligi" in f and "bir faturalama degildir" in f for f in top.retrieval_flags)
