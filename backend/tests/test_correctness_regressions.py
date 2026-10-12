"""Deterministic correctness regression tests: no
database, embedding model, or LLM provider is touched (those are faked)."""
import re
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.main as main_mod
import app.rag.generate as generate_mod
import app.rag.retrieve as retrieve_mod
import app.rag.search as search_mod
from app.auth import AuthUser, get_current_user
from app.db import get_session
from app.ingest.load import build_catalog, load_records, merge_customer_types
from app.ingest.normalize import UNLIMITED, benefit_tags, general_internet_gb, internet_phrase, normalize_record
from app.models import ChatTurn, Package
from app.rag.clarify import already_clarified, collect_slots, should_clarify
from app.rag.generate import ProviderUnavailable, generate_answer, generate_clarification
from app.rag.history import PROVIDER_ERROR_MESSAGE, recent_history
from app.rag.prompt import build_messages, format_sources
from app.rag.retrieve import (
    EXISTING_CUSTOMER_UNAVAILABLE,
    detect_customer_type,
    detect_operator,
    detect_price_bounds,
    detect_activation_channel,
    retrieve_with_notes,
)

_PACKAGE_COLUMNS = {c.name for c in Package.__table__.columns}


def _catalog() -> list[dict]:
    """Every active record in the authoritative data files."""
    return [normalize_record(r, require_mobile_voice=True) for r in merge_customer_types(load_records())]


def _ingested() -> list[dict]:
    """What the loader puts in the database."""
    return build_catalog()[0]


def _as_package(rec: dict) -> Package:
    return Package(id=uuid.uuid4(), **{k: v for k, v in rec.items() if k in _PACKAGE_COLUMNS and k != "id"})


# ---- Priority 1: unlimited general internet vs unlimited components ----

@pytest.mark.parametrize(
    "fields, expected, forbidden",
    [
        # (internet_gb, social_media_gb, bonus_internet_gb, bonus_internet_scope)
        # Unlimited general internet.
        ((UNLIMITED, None, None, None), "sinirsiz genel internet", None),
        ((UNLIMITED, UNLIMITED, None, None), "sinirsiz genel internet", None),
        # Limited general + unlimited social: never unlimited internet.
        ((50, UNLIMITED, None, None), "50 GB genel + sinirsiz sosyal medya internet (genel internet sinirsiz degil)", "sinirsiz genel"),
        # 20 GB general + 10 GB social is not 30 GB of internet.
        ((20, 10, None, None), "20 GB genel + 10 GB sosyal medya internet", "toplam"),
        # 20 GB general + 10 GB general-scope bonus is 30 GB general-use (parts kept).
        ((20, None, 10, "general"), "toplam 30 GB genel kullanim internet (20 GB genel + 10 GB ek genel)", "sinirsiz"),
        # Hotspot-only and app-only bonuses are listed, never added.
        ((40, None, 20, "tethering_only"), "40 GB genel + 20 GB yalnizca paylasim (hotspot) internet", "toplam"),
        ((10, None, 1, "app_specific"), "10 GB genel + 1 GB uygulamaya ozel internet", "toplam"),
        # General gift data with unlimited social (Vodafone Genç Red 80 GB).
        ((40, UNLIMITED, 40, "general"), "toplam 80 GB genel kullanim internet (40 GB genel + sinirsiz sosyal medya + 40 GB ek genel) (genel internet sinirsiz degil)", "sinirsiz genel"),
        ((20, None, None, None), "20 GB internet", "sinirsiz"),
    ],
)
def test_internet_phrase(fields, expected, forbidden):
    phrase = internet_phrase(*fields)
    assert expected in phrase
    if forbidden:
        assert forbidden not in phrase


def test_internet_phrase_unknown_is_none():
    assert internet_phrase(None, None, None, None) is None
    assert general_internet_gb(None) is None


def test_real_catalog_never_overstates_general_internet():
    """Every plan whose general allowance is limited must not read as unlimited
    internet in the embedding doc, the LLM source line, or the benefit tags."""
    catalog = _catalog()
    limited_with_unlimited_total = 0
    for rec in catalog:
        source_line, _ = format_sources([_as_package(rec)])
        general_unlimited = rec["internet_gb"] == UNLIMITED
        for text in (rec["doc_text"], source_line):
            assert ("sinirsiz genel internet" in text) == general_unlimited, rec["name"]
            assert "Icerik: sinirsiz internet" not in text
        assert ("unlimited_data" in benefit_tags(rec)) == general_unlimited, rec["name"]
        if not general_unlimited and UNLIMITED in (rec["social_media_gb"], rec["bonus_internet_gb"]):
            limited_with_unlimited_total += 1
            assert "genel internet sinirsiz degil" in rec["doc_text"]
            assert "genel internet sinirsiz degil" in source_line
    # Guards the fixture: the catalog really contains the affected plan type (8 after the
    # 2026-10-12 cleanup: Yeni Nesil Prime 75/100/150, Vodafone Red x3, Genç Red x2).
    assert limited_with_unlimited_total >= 8


def test_yeni_nesil_prime_reads_as_limited_gb_plus_unlimited_social():
    # Was pinned to Yeni Nesil Prime 50 GB (closed 24.07.2026, removed); same structure.
    rec = next(r for r in _catalog() if r["name"] == "Yeni Nesil Prime 100 GB")
    assert "100 GB genel + sinirsiz sosyal medya" in rec["doc_text"]
    assert general_internet_gb(rec["internet_gb"], rec.get("bonus_internet_gb"), rec.get("bonus_internet_scope")) == 100


# ---- Priority 2: operator detection on word boundaries ----

@pytest.mark.parametrize("q", ["hattım var, faturalı 20 GB", "attığım mesaj", "ITTIFAK", "HATTIM", "bitti mi", "settings"])
def test_operator_not_detected_inside_words(q):
    assert detect_operator(q) is None


@pytest.mark.parametrize(
    "q, expected",
    [
        ("TT'den geçmek istiyorum", "turk_telekom"),
        ("tt paketleri", "turk_telekom"),
        ("TÜRK TELEKOM faturasız", "turk_telekom"),
        ("Türk Telekom'un paketleri", "turk_telekom"),
        ("turk telekom", "turk_telekom"),
        ("türktelekom", "turk_telekom"),
        ("TURKCELL faturalı", "turkcell"),
        ("Turkcell'e geçmek", "turkcell"),
        ("VODAFONE'dan", "vodafone"),
    ],
)
def test_operator_detected(q, expected):
    assert detect_operator(q) == expected


# ---- Priority 4: price bounds ----

@pytest.mark.parametrize(
    "q, bounds",
    [
        ("200 TL'ye kadar", (None, 200.0)),
        ("500 TL altı", (None, 500.0)),
        ("1.000 TL'ye kadar paket", (None, 1000.0)),
        ("99,90 TL altı", (None, 99.9)),
        ("26 yaş altı faturasız", (None, None)),
        ("26 yaş altı faturasız 200 TL'ye kadar paket", (None, 200.0)),
        ("faturalı 3 ay kadar", (None, None)),
        ("faturalı 3 ay kadar 300 TL altı", (None, 300.0)),
        ("1000 dakika faturalı", (None, None)),
        ("1.000 dakika faturalı 500 TL altı", (None, 500.0)),
        ("500 SMS altı", (None, None)),
        ("20 GB'a kadar paketler", (None, None)),
        ("en az 100 TL", (100.0, None)),
        ("100 TL'den fazla olsun", (100.0, None)),
        ("150 TL en fazla", (None, 150.0)),
        ("en az 100 TL en fazla 300 TL", (100.0, 300.0)),
        ("bütçem 200 TL civarı", (None, None)),
        ("5G paket 300 TL altı", (None, 300.0)),
    ],
)
def test_price_bounds(q, bounds):
    assert detect_price_bounds(q) == bounds


def test_budget_slot_counts_a_minimum_price():
    assert collect_slots("faturasız en az 100 TL").budget


# ---- Priority 3: existing customers against the real (new-only) catalog ----

def test_catalog_is_new_customer_only():
    """Documents the assumption behind the existing-customer note. If existing or
    'any' plans are ever added, the first retrieval pass returns them and no note
    is attached -- this test then just needs updating."""
    assert {r["customer_type"] for r in _catalog()} == {"new"}


def _fake_candidates_over_catalog(catalog):
    """Stand-in for the SQL step of rag.search: honours the customer_type and plain
    audience filters (what these tests are about) on the real catalog; distance 0."""
    def candidates(session, filters, query_vec):
        rows = catalog
        for f in filters:
            sql = str(f.compile(compile_kwargs={"literal_binds": True}))
            if sql.startswith("packages.customer_type"):
                allowed = set(re.findall(r"'([a-z]+)'", sql))
                rows = [p for p in rows if p.customer_type in allowed]
            elif sql.startswith("packages.audience_segment"):
                allowed = set(re.findall(r"'([a-z_]+)'", sql))
                rows = [p for p in rows if p.audience_segment in allowed]
        return [(p, 0.0) for p in rows]
    return candidates


@pytest.fixture
def catalog_retrieval(monkeypatch):
    catalog = [_as_package(r) for r in _ingested()]
    monkeypatch.setattr(retrieve_mod, "embed_query", lambda q: [0.0] * 1024)
    monkeypatch.setattr(search_mod, "_candidates", _fake_candidates_over_catalog(catalog))
    return catalog


@pytest.mark.parametrize("q", ["Turkcell müşterisiyim, faturalı 20 GB istiyorum", "mevcut müşteriyim faturasız paket"])
def test_existing_customer_gets_flagged_results_not_empty(catalog_retrieval, q):
    assert detect_customer_type(q) == "existing"
    packages, notes = retrieve_with_notes(None, q)
    assert packages
    assert all(p.customer_type == "new" for p in packages)
    assert notes[0] == EXISTING_CUSTOMER_UNAVAILABLE


def test_new_customer_query_has_no_note(catalog_retrieval):
    packages, notes = retrieve_with_notes(None, "numara taşıma ile faturalı 20 GB")
    assert packages and EXISTING_CUSTOMER_UNAVAILABLE not in notes


def test_existing_customer_note_reaches_prompt_only_when_set():
    pkg = _as_package(_catalog()[0])
    with_note, _ = build_messages("soru", [pkg], notes=[EXISTING_CUSTOMER_UNAVAILABLE])
    without, _ = build_messages("soru", [pkg])
    assert "<NOTLAR>" in with_note[1]["content"]
    assert "mevcut musterilere acik paket YOK" in with_note[1]["content"]
    assert "<NOTLAR>" not in without[1]["content"]


# ---- Priority 5: provider failures don't consume quota or clarification state ----

class _FailingCompletions:
    def create(self, **kwargs):
        raise TimeoutError("provider down")


class _OkCompletions:
    def __init__(self, text):
        self._text = text

    def create(self, **kwargs):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self._text))])


def _client_with(completions):
    return SimpleNamespace(chat=SimpleNamespace(completions=completions))


def test_generate_answer_raises_on_provider_failure(monkeypatch):
    monkeypatch.setattr(generate_mod, "_client", lambda: _client_with(_FailingCompletions()))
    with pytest.raises(ProviderUnavailable):
        generate_answer("soru", [_as_package(_catalog()[0])])
    with pytest.raises(ProviderUnavailable):
        generate_clarification("merhaba")


def test_generate_answer_raises_on_empty_completion(monkeypatch):
    monkeypatch.setattr(generate_mod, "_client", lambda: _client_with(_OkCompletions("<think>x</think>")))
    with pytest.raises(ProviderUnavailable):
        generate_answer("soru", [_as_package(_catalog()[0])])


def test_provider_error_turn_is_not_a_clarification_turn():
    history = [
        ChatTurn(role="user", content="merhaba"),
        ChatTurn(role="assistant", content=PROVIDER_ERROR_MESSAGE),
    ]
    assert not already_clarified(history)
    assert should_clarify(collect_slots("paket öner", history), history)
    assert [t.role for t in recent_history(history)] == ["user"]


def test_frontend_error_bubble_is_not_a_clarification_turn():
    history = [
        ChatTurn(role="user", content="paket öner"),
        ChatTurn(role="assistant", content="Şu an yanıt veremedim. Lütfen birazdan tekrar dene."),
    ]
    assert not already_clarified(history)


def test_real_clarification_still_counts():
    history = [
        ChatTurn(role="user", content="paket öner"),
        ChatTurn(role="assistant", content="Faturalı mı faturasız mı?"),
    ]
    assert already_clarified(history)


@pytest.fixture
def api(monkeypatch):
    """/chat with auth, DB, quota, retrieval and LLM faked; counts recorded usage."""
    recorded = []
    main_mod.app.dependency_overrides[get_session] = lambda: None
    main_mod.app.dependency_overrides[get_current_user] = lambda: AuthUser(id=uuid.uuid4(), is_anonymous=True)
    monkeypatch.setattr(main_mod, "reserve_quota", lambda *a: "reservation")
    monkeypatch.setattr(main_mod, "commit_quota", lambda r: recorded.append(r))
    monkeypatch.setattr(main_mod, "release_quota", lambda r: None)
    pkg = _as_package(_catalog()[0])
    monkeypatch.setattr(retrieve_mod, "retrieve_with_notes", lambda *a, **kw: ([pkg], []))
    main_mod.limiter.reset()
    yield TestClient(main_mod.app), recorded
    main_mod.app.dependency_overrides.clear()


_FULL_QUESTION = {"question": "faturalı 20 GB 500 TL altı", "history": []}


def test_chat_provider_failure_is_503_and_skips_quota(api, monkeypatch):
    client, recorded = api
    monkeypatch.setattr(generate_mod, "_client", lambda: _client_with(_FailingCompletions()))
    for body in (_FULL_QUESTION, {"question": "merhaba", "history": []}):  # answer + clarify paths
        res = client.post("/chat", json=body)
        assert res.status_code == 503
        assert res.json() == {"detail": {"reason": "provider_unavailable"}}
    assert recorded == []


def test_chat_success_records_usage_once(api, monkeypatch):
    client, recorded = api
    monkeypatch.setattr(generate_mod, "_client", lambda: _client_with(_OkCompletions("**Öneri:** bu paket [S1]")))
    res = client.post("/chat", json=_FULL_QUESTION)
    assert res.status_code == 200
    body = res.json()
    assert set(body) == {"answer", "citations", "packages"}
    assert [c["id"] for c in body["citations"]] == ["S1"]
    assert len(recorded) == 1


@pytest.mark.parametrize(
    "q, expected",
    [
        ("numara taşıma ile faturasız paket", "number_porting"),
        ("Numaramı Vodafone'a taşımak istiyorum", "number_porting"),
        ("yeni hat almak istiyorum", "new_line"),
        ("yeni hat veya numara taşıma fark etmez", None),  # both named: no restriction
        ("faturalı 20 GB", None),
    ],
)
def test_activation_channel_detection(q, expected):
    assert detect_activation_channel(q) == expected


def test_channel_filter_only_excludes_the_other_exclusive_channel():
    sql = str(retrieve_mod._channel_filter("number_porting").compile(compile_kwargs={"literal_binds": True}))
    assert "new_line" in sql and "number_porting" in sql and "IS NULL" in sql
