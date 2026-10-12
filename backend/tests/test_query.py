"""Turkish query understanding (rag.query): one message -> TariffQuery, and
conversation-level merging. Offline and deterministic."""
import pytest

from app.models import ChatTurn
from app.rag.history import PROVIDER_ERROR_MESSAGE
from app.rag.query import conversation_query, parse_turn
from retrieval_benchmark_cases import CASES, HELDOUT, history_for


def _value(v):
    return set(v) if isinstance(v, frozenset) else v


@pytest.mark.parametrize("case", [c for c in CASES + HELDOUT if c.slots], ids=lambda c: c.id)
def test_benchmark_slots(case):
    q = conversation_query(case.turns[-1], history_for(case))
    for slot, expected in case.slots.items():
        got = _value(getattr(q, slot))
        assert got == expected or (expected == set() and got is None), (slot, got)


@pytest.mark.parametrize("text, slot, value", [
    ("Numaramı taşıyacağım.", "channel", "number_porting"),
    ("Numaramı taşırsam ne var?", "channel", "number_porting"),
    ("Hattımı Vodafone'a geçirmek istiyorum.", "channel", "number_porting"),
    ("Yeni hat alacağım.", "channel", "new_line"),
    ("Faturasız olsun.", "line_type", "prepaid"),
    ("Faturalı istemiyorum.", "line_type", "prepaid"),
    ("Taahhüt istemiyorum.", "no_commitment", True),
    ("taahhüt yok", "no_commitment", True),
    ("Aylık 600 lirayı geçmesin.", "max_price", 600.0),
    ("Aylık 600 lirayı geçmesin.", "monthly_budget", True),
    ("En az 40 GB.", "min_gb", 40.0),
    ("40 GB üstü.", "min_gb", 40.0),
    ("10 GB'a kadar", "max_gb", 10.0),
    ("Sınırsız internet.", "unlimited_internet", True),
    ("Sosyal medya sınırsız olsun.", "unlimited_social", True),
    ("Sosyal medya sınırsız olsun.", "unlimited_internet", None),
    ("Netflix üyeliği hediye olsun.", "subscriptions", {"netflix"}),
    ("Netflix kotamdan yemesin.", "apps", {"netflix"}),
    ("Netflix kotamdan yemesin.", "subscriptions", None),
    ("Deneme üyeliği istemiyorum.", "no_trials", True),
    ("En ucuzunu göster.", "objective", "cheapest"),
    ("En çok internet veren hangisi?", "objective", "most_data"),
    ("Otomatik yenilenmesin.", "renewal", "one_time"),
    ("otomatik yenilensin", "renewal", "recurring"),
    ("20 GB sosyal medya", "min_gb", None),  # a restricted quota is not general internet
    ("26 yaşındayım", "max_price", None),
    ("3 aylık paket", "max_price", None),
    ("1000 dakika", "max_price", None),
    ("en az 1000 TL", "objective", None),
])
def test_turkish_phrases(text, slot, value):
    assert _value(getattr(parse_turn(text), slot)) == value


def test_price_range_with_both_bounds():
    q = parse_turn("500 ile 800 TL arası")
    assert (q.min_price, q.max_price) == (500.0, 800.0)


@pytest.mark.parametrize("text", ["hattım için paket", "attığım mesajlar", "İTTİFAK"])
def test_no_operator_false_positive(text):
    assert parse_turn(text).operators is None


def test_operator_negation_and_any():
    assert set(parse_turn("Turkcell olmasın").operators) == {"vodafone", "turk_telekom"}
    assert conversation_query("operatör fark etmez", [ChatTurn(role="user", content="Turkcell faturalı")]).operators is None


def _turns(*texts):
    out = []
    for t in texts:
        out += [ChatTurn(role="user", content=t), ChatTurn(role="assistant", content="Önerim [S1].")]
    return out


def test_newest_budget_replaces_older_one():
    q = conversation_query("Bütçem 900 TL oldu.", _turns("Vodafone, en az 30 GB, 700 TL altı."))
    assert q.max_price == 900.0 and q.min_gb == 30.0 and set(q.operators) == {"vodafone"}


def test_additive_operator_widens_instead_of_replacing():
    q = conversation_query("Aslında Turkcell de olabilir.", _turns("Vodafone faturasız"))
    assert set(q.operators) == {"vodafone", "turkcell"}
    q = conversation_query("Turkcell'e bakalım", _turns("Vodafone faturasız"))
    assert set(q.operators) == {"turkcell"}


def test_objective_follow_up_keeps_constraints():
    q = conversation_query("Peki en ucuzu?", _turns("faturasız 30 GB 600 TL altı"))
    assert (q.objective, q.line_type, q.min_gb, q.max_price) == ("cheapest", "prepaid", 30.0, 600.0)


def test_commitment_can_be_allowed_again():
    assert conversation_query("taahhüt olabilir", _turns("taahhütsüz faturalı")).no_commitment is False


def test_new_search_resets_context():
    q = conversation_query("Yeni bir arama yapalım: faturasız 50 GB", _turns("Vodafone faturalı 20 GB 500 TL altı"))
    assert (q.operators, q.max_price, q.line_type, q.min_gb) == (None, None, "prepaid", 50.0)


def test_assistant_text_and_error_bubbles_never_become_constraints():
    history = [
        ChatTurn(role="user", content="faturalı 20 GB"),
        ChatTurn(role="assistant", content="Vodafone 300 TL altı paketler: ... [S1]"),
        ChatTurn(role="assistant", content=PROVIDER_ERROR_MESSAGE),
    ]
    q = conversation_query("bir de bakar mısın", history)
    assert q.operators is None and q.max_price is None and q.min_gb == 20.0


def test_past_payment_is_not_a_budget():
    assert parse_turn("Eskiden 1500 TL ödüyordum").max_price is None
    assert parse_turn("50 GB, 100 TL, taahhütsüz").max_price == 100.0


# ---- Spoken numbers, slang, negated minimums ----

@pytest.mark.parametrize("text, expected", [
    ("faturasız, iki yüz elli lirayı geçmesin", {"line_type": "prepaid", "max_price": 250.0}),
    ("faturalı bir tarife, yüz gigadan fazla internet", {"line_type": "postpaid", "min_gb": 100.0}),
    ("bin liranın altında faturalı, otuz gb yeter", {"line_type": "postpaid", "max_price": 1000.0, "min_gb": 30.0}),
    ("bin beş yüz TL'ye kadar", {"max_price": 1500.0}),
    ("50 giga lazım", {"min_gb": 50.0}),
    ("vodafon faturali sinirsiz net", {"operators": frozenset({"vodafone"}), "line_type": "postpaid",
                                       "unlimited_internet": True}),
    ("faturasız paket, üç yüz liradan fazla vermem", {"line_type": "prepaid", "max_price": 300.0}),
    ("300 TL üstü olmasın", {"max_price": 300.0}),
    ("300 TL üstü", {"min_price": 300.0}),
    ("300 liradan fazla ödeyebilirim", {"min_price": 300.0}),
])
def test_number_words_and_negated_minimums(text, expected):
    assert parse_turn(text).stated() == expected


@pytest.mark.parametrize("text", ["bir paket öner", "net söyle hangisi", "300 TL altı bir paket"])
def test_number_words_need_a_unit(text):
    stated = parse_turn(text).stated()
    assert "min_gb" not in stated and stated.get("max_price") in (None, 300.0) and "min_price" not in stated
