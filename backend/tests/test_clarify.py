from app.models import ChatTurn
from app.rag.clarify import (
    collect_slots,
    detect_budget_signal,
    detect_escape_hatch,
    detect_usage_signal,
    should_clarify,
)


def _clarifies(question: str, history: list[ChatTurn] | None = None) -> bool:
    return should_clarify(collect_slots(question, history), history)


def test_should_clarify():
    # Line type is required; additionally one of the budget or usage signals is needed.
    assert _clarifies("faturalıyım, tarife lazım")  # only line type -> insufficient
    assert _clarifies("150 TL altı tarife")         # no line type (eligibility filter)
    assert _clarifies("merhaba")                     # greeting
    assert not _clarifies("faturasız 20 GB lazım")   # line type + usage
    assert not _clarifies("faturalı 200 TL bütçem var")  # line type + budget
    assert not _clarifies("faturalı, fark etmez sen öner")  # escape hatch skips the gate


def test_loop_breaker():
    # If the last assistant turn had no citation (= it was a clarification), don't ask again.
    asked = [
        ChatTurn(role="user", content="faturalıyım"),
        ChatTurn(role="assistant", content="Bütçen ne kadar? Ne kadar internet kullanıyorsun?"),
    ]
    assert not _clarifies("bilmiyorum ki", asked)
    assert not _clarifies("faturalıyım", asked)

    # The last assistant turn was a real recommendation ([S#] present) -- a new vague ask should still be clarified.
    recommended = [
        ChatTurn(role="user", content="faturasız hat"),
        ChatTurn(role="assistant", content="**Öneri:** Şu paket uygun [S1]."),
    ]
    assert _clarifies("bir de faturalı bakalım", recommended)


def test_slots_accumulate_and_missing():
    # The user can spread info across turns: line type was given in an earlier turn.
    history = [ChatTurn(role="user", content="faturalı hat arıyorum")]
    assert not _clarifies("200 TL bütçem var", history)

    # missing lists only the absent slots.
    assert collect_slots("faturalıyım, tarife lazım").missing == ["budget", "usage"]
    assert collect_slots("faturasız 20 GB, 150 TL").missing == []


def test_signal_detectors():
    # Budget: detect_max_price needs a bound word; the budget signal also catches bare money.
    assert detect_budget_signal("150 lira")
    assert detect_budget_signal("300₺ ayırdım")
    assert not detect_budget_signal("20 GB paket lazım")
    # Usage: quantitative (GB) or qualitative phrases.
    assert detect_usage_signal("çok internet kullanıyorum")
    assert detect_usage_signal("sınırsız sosyal medya")
    assert not detect_usage_signal("faturalıyım, tarife lazım")
    # Escape hatch.
    assert detect_escape_hatch("fark etmez sen öner")
    assert not detect_escape_hatch("faturalı hat istiyorum")
