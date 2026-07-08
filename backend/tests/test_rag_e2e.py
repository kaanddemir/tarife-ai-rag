"""End-to-end RAG plumbing test with a mocked LLM (no API key required).

Verifies that generate_answer:
- returns the exact retrieved packages (so the comparison table shows real values),
- keeps only citations actually referenced in the answer,
- passes source-grounded messages to the model.
"""
import uuid
from types import SimpleNamespace

import app.rag.generate as generate_mod
from app.models import ChatRequest, ChatTurn, Package
from app.rag.history import contextual_question


def _pkg(operator, name, price, gb):
    return Package(
        id=uuid.uuid4(),
        operator=operator,
        name=name,
        line_type="prepaid",
        price_try=price,
        internet_gb=gb,
        voice_minutes=1000,
        sms_count=500,
        validity_days=30,
        source_url=f"https://example.com/{operator}",
        doc_text="x",
    )


class _FakeCompletions:
    def __init__(self, text):
        self._text = text

    def create(self, **kwargs):
        _FakeCompletions.last_kwargs = kwargs
        msg = SimpleNamespace(content=self._text)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


class _FakeClient:
    def __init__(self, text):
        self.chat = SimpleNamespace(completions=_FakeCompletions(text))


def test_generate_grounds_and_filters_citations(monkeypatch):
    packages = [
        _pkg("turkcell", "Hazir Kart 20 GB", 189, 20),
        _pkg("vodafone", "Kolay Paket 20 GB", 199, 20),
    ]
    fake = _FakeClient("Turkcell paketi daha uygun [S1].")
    monkeypatch.setattr(generate_mod, "_client", lambda: fake)

    resp = generate_mod.generate_answer("20 GB paketleri karsilastir", packages)

    # Table rows are limited to packages actually mentioned/cited in the answer.
    assert [p.name for p in resp.packages] == ["Hazir Kart 20 GB"]
    # Only S1 was cited in the answer -> only S1 kept.
    assert [c.id for c in resp.citations] == ["S1"]
    # Sources were embedded in the user prompt.
    user_msg = _FakeCompletions.last_kwargs["messages"][-1]["content"]
    assert "Hazir Kart 20 GB" in user_msg and "Kolay Paket 20 GB" in user_msg
    system_msg = _FakeCompletions.last_kwargs["messages"][0]["content"]
    assert "**Öneri:**" in system_msg
    assert "**Diğer alternatifler:**" in system_msg
    assert "nedeni:" in system_msg
    assert "- **Fiyat:**" in system_msg
    assert "- **Kıyas:**" in system_msg
    assert "orta detayli Markdown" in system_msg
    assert "**Sonraki adım:**" in system_msg
    assert "takip sorusu" in system_msg


def test_generate_orders_table_by_answer_citation_order(monkeypatch):
    packages = [
        _pkg("turkcell", "Hazir Kart 20 GB", 189, 20),
        _pkg("vodafone", "Kolay Paket 20 GB", 199, 20),
        _pkg("turk_telekom", "Faturasiz Genc 14 GB", 135, 14),
    ]
    fake = _FakeClient("Ana onerim Vodafone [S2]. Alternatif olarak Turk Telekom [S3] ve Turkcell [S1].")
    monkeypatch.setattr(generate_mod, "_client", lambda: fake)

    resp = generate_mod.generate_answer("ogrenciye en iyi paket", packages)

    assert [c.id for c in resp.citations] == ["S2", "S3", "S1"]
    assert [p.name for p in resp.packages] == [
        "Kolay Paket 20 GB",
        "Faturasiz Genc 14 GB",
        "Hazir Kart 20 GB",
    ]


def test_chat_request_accepts_optional_history():
    req = ChatRequest(
        question="150 TL alti var mi?",
        history=[ChatTurn(role="user", content="20 GB paketleri karsilastir")],
    )

    assert req.history[0].role == "user"
    assert req.history[0].content == "20 GB paketleri karsilastir"


def test_contextual_question_uses_recent_user_history_only():
    history = [
        ChatTurn(role="user", content=f"user {i}")
        for i in range(6)
    ] + [ChatTurn(role="assistant", content="assistant answer")]

    query = contextual_question("150 TL alti olan var mi?", history)

    assert "user 0" not in query
    assert "assistant answer" not in query
    assert "user 2" in query
    assert "150 TL alti olan var mi?" in query


def test_generate_includes_history_in_prompt(monkeypatch):
    packages = [_pkg("turkcell", "Hazir Kart 20 GB", 189, 20)]
    fake = _FakeClient("Turkcell daha uygun [S1].")
    monkeypatch.setattr(generate_mod, "_client", lambda: fake)

    generate_mod.generate_answer(
        "150 TL alti olan var mi?",
        packages,
        [ChatTurn(role="user", content="20 GB paketleri karsilastir")],
    )

    user_msg = _FakeCompletions.last_kwargs["messages"][-1]["content"]
    assert "<GECMIS>" in user_msg
    assert "20 GB paketleri karsilastir" in user_msg
    assert "<KAYNAKLAR>" in user_msg


def test_generate_handles_no_packages():
    resp = generate_mod.generate_answer("bulunmayan bir sey", [])
    assert resp.packages == []
    assert resp.citations == []
    assert "bulunamadi" in resp.answer.lower()


def test_generate_strips_reasoning_blocks(monkeypatch):
    packages = [_pkg("turkcell", "Hazir Kart 20 GB", 189, 20)]
    fake = _FakeClient("<think>private reasoning</think>\nTurkcell daha uygun [S1].")
    monkeypatch.setattr(generate_mod, "_client", lambda: fake)

    resp = generate_mod.generate_answer("20 GB paket", packages)

    assert "<think>" not in resp.answer
    assert resp.answer == "Turkcell daha uygun [S1]."
