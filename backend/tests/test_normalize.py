from app.ingest.normalize import (
    build_doc_text,
    normalize_record,
    parse_amount,
    parse_gb,
    parse_price,
)


def test_parse_amount_handles_unlimited_and_numbers():
    assert parse_amount("sinirsiz") == -1
    assert parse_amount("Sınırsız") == -1
    assert parse_amount("1000 dakika") == 1000
    assert parse_amount(500) == 500
    assert parse_amount(None) is None


def test_parse_gb_and_price():
    assert parse_gb("20 GB") == 20.0
    assert parse_gb("12,5 gb") == 12.5
    assert parse_gb(15) == 15.0
    assert parse_price("149 TL") == 149.0
    assert parse_price("89,90 ₺") == 89.90


def test_normalize_record_sets_prepaid_and_doc_text():
    rec = {
        "operator": "turkcell",
        "name": "Hazir Kart 20 GB",
        "price_try": "149 TL",
        "internet_gb": "20 GB",
        "voice_minutes": "sinirsiz",
        "sms_count": 500,
        "validity_days": 30,
        "source_url": "https://example.com",
    }
    out = normalize_record(rec)
    assert out["line_type"] == "prepaid"
    assert out["price_try"] == 149.0
    assert out["internet_gb"] == 20.0
    assert out["voice_minutes"] == -1
    assert "sinirsiz dakika" in out["doc_text"]
    assert "Turkcell" in out["doc_text"]
    assert "20 GB" in out["doc_text"]


def test_build_doc_text_includes_campaign_and_faq():
    rec = {
        "operator": "vodafone",
        "name": "Kolay Paket",
        "internet_gb": 10,
        "price_try": 99,
        "voice_minutes": 500,
        "sms_count": 500,
        "validity_days": 30,
        "campaign": "5 GB hediye",
        "faq": [{"q": "Kimler alabilir?", "a": "Herkes"}],
    }
    doc = build_doc_text(rec)
    assert "Kampanya: 5 GB hediye" in doc
    assert "SSS:" in doc
