import pytest

from app.ingest.normalize import (
    benefit_tags,
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


def _rec(**overrides) -> dict:
    base = {
        "operator": "turkcell",
        "name": "Hazir Kart 20 GB",
        "line_type": "prepaid",
        "customer_type": "existing",
        "price_try": "149 TL",
        "price_period": "month",
        "internet_gb": "20 GB",
        "voice_minutes": "sinirsiz",
        "sms_count": 500,
        "validity_days": 30,
        "validity_unit": "day",
        "source_url": "https://example.com",
    }
    return {**base, **overrides}


def _typed(**overrides) -> dict:
    """build_doc_text tipli degerler bekler (normalize_record'un ciktisi gibi)."""
    return _rec(**{"price_try": 149.0, "internet_gb": 20.0, "voice_minutes": -1, **overrides})


def test_normalize_record_types_fields_and_builds_doc_text():
    out = normalize_record(_rec())
    assert out["line_type"] == "prepaid"
    assert out["customer_type"] == "existing"
    assert out["price_try"] == 149.0
    assert out["internet_gb"] == 20.0
    assert out["voice_minutes"] == -1
    assert "sinirsiz dakika" in out["doc_text"]
    assert "Turkcell" in out["doc_text"]
    assert "20 GB" in out["doc_text"]


def test_normalize_record_keeps_line_and_customer_type_from_input():
    """line_type is no longer fixed to 'prepaid' -- it comes from the file header."""
    out = normalize_record(_rec(line_type="postpaid", customer_type="new"))
    assert out["line_type"] == "postpaid"
    assert out["customer_type"] == "new"
    assert "faturali paketi" in out["doc_text"]
    assert "yeni gelen musterilere ozel" in out["doc_text"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"line_type": "faturasiz"},  # a label, not the enum value
        {"customer_type": "mevcut"},
        {"line_type": None},
    ],
)
def test_normalize_record_rejects_unknown_enum(overrides):
    rec = _rec(**overrides)
    # The missing-key case (absent entirely, not None) must also fail the same way.
    rec = {k: v for k, v in rec.items() if v is not None}
    with pytest.raises(ValueError):
        normalize_record(rec)


@pytest.mark.parametrize(
    "overrides",
    [
        {"price_period": None},
        {"price_period": "variable"},
        {"validity_unit": None},
        {"validity_unit": "custom"},
    ],
)
def test_prepaid_rejects_missing_or_nonstandard_periods(overrides):
    with pytest.raises(ValueError):
        normalize_record(_rec(**overrides))


def test_build_doc_text_labels_postpaid_and_omits_missing_validity():
    doc = build_doc_text(
        _typed(line_type="postpaid", customer_type="any", validity_days=None, price_period="month")
    )
    assert "faturali paketi" in doc
    assert "hem yeni hem mevcut musteriler" in doc
    assert "gecerli" not in doc  # no validity -> the phrase must not be written at all


def test_build_doc_text_renders_unlimited_gb_not_minus_one():
    doc = build_doc_text(
        _typed(line_type="postpaid", customer_type="new", internet_gb=-1)
    )
    assert "sinirsiz genel internet" in doc
    assert "-1" not in doc


def test_build_doc_text_renders_unlimited_social_as_a_sentence():
    """Because social_media_gb=-1 is truthy, it used to write '-1 GB'i sosyal medya'."""
    doc = build_doc_text(_typed(line_type="postpaid", customer_type="new", social_media_gb=-1))
    assert "Sosyal medya (TikTok, Instagram, YouTube vb.) kullanimi sinirsizdir; bu genel internet degildir." in doc
    assert "-1 GB" not in doc


def test_build_doc_text_omits_zero_social_quota():
    doc = build_doc_text(_typed(social_media_gb=0))
    assert "sosyal medya" not in doc.lower()


def test_build_doc_text_states_commitment_and_price_lock():
    doc = build_doc_text(
        _typed(line_type="postpaid", customer_type="new", commitment_months=12, price_lock_months=6)
    )
    assert "12 ay taahhutludur." in doc
    assert "Fiyat 6 fatura donemi boyunca sabittir." in doc


def test_build_doc_text_marks_missing_postpaid_commitment_unknown():
    doc = build_doc_text(_typed(line_type="postpaid", customer_type="new"))
    assert "Taahhut bilgisi kaynakta belirtilmemistir." in doc
    assert "Taahhutsuzdur." not in doc


def test_build_doc_text_omits_commitment_for_prepaid():
    """Prepaid lines have no commitment concept; even saying 'Taahhutsuzdur' is noise."""
    doc = build_doc_text(_typed())
    assert "aahhut" not in doc


def test_prepaid_price_lock_is_rendered_without_commitment_language():
    out = normalize_record(_rec(price_lock_months="3", commitment_months=12))
    assert out["commitment_status"] == "no_commitment"
    assert out["commitment_months"] is None
    assert out["price_lock_months"] == 3
    assert "Fiyat 3 ay boyunca sabittir." in out["doc_text"]
    assert "aahhut" not in out["doc_text"]


def test_prepaid_eligibility_tags_are_derived_deterministically():
    out = normalize_record(
        _rec(
            conditions=(
                "Numarasını Turkcell'e taşıyan yeni hat müşterilerine, belirli illerde "
                "online başvuru ile sunulur."
            )
        )
    )
    # Channels go to activation_channels, never into eligibility_tags.
    assert out["eligibility_tags"] == ["online_only", "province_restricted"]
    assert out["activation_channels"] == ["new_line", "number_porting"]


def test_prepaid_operational_eligibility_tags_are_derived_from_source_text():
    out = normalize_record(
        _rec(
            conditions=(
                "Bu paketler sadece Adana, Antalya ve İzmir'de yapılan yeni hat "
                "aktivasyonlarında geçerlidir. Müşteri aktivasyonu takip eden ilk 60 gün "
                "içinde, TC ya da Yabancı kimlik numarası ile ve 7-26 yaş arasında yararlanabilir."
            )
        )
    )
    # Age lives in age_min/age_max and the channel in activation_channels.
    assert out["eligibility_tags"] == ["activation_window", "identity_required", "province_restricted"]


def test_prepaid_shared_benefits_are_derived_from_fields_and_campaign_text():
    out = normalize_record(
        _rec(
            validity_value=3,
            validity_unit="month",
            validity_days=90,
            price_period="90_day",
            price_lock_months=12,
            bonus_internet_gb=5,
            campaign=(
                "Sınırsız WhatsApp mesajlaşma, Türk Telekomlularla sınırsız konuşma, "
                "BiP'te geçerli 1 GB, Bi' Dünya Fırsat, 24 saatte teslimat ve "
                "internet paylaşım hakkı."
            ),
        )
    )
    assert benefit_tags(out) == [  # legacy record (no benefits_status): text rules
        "app_specific_data",
        "bonus_data",
        "data_sharing",
        "digital_perks",
        "free_delivery",
        "long_validity",
        "price_guarantee",
        "unlimited_calls",
        "unlimited_messaging",
        "unlimited_onnet_calls",
    ]


def test_postpaid_common_benefits_are_derived_from_quota_and_campaign():
    out = normalize_record(
        _rec(
            name="5G Aile Prime",
            line_type="postpaid",
            customer_type="new",
            package_group="family_prime",
            commitment_months=12,
            internet_gb="sınırsız",
            voice_minutes="sınırsız",
            sms_count="sınırsız",
            conditions="Tarife aşımsızdır.",
            campaign=(
                "Ortak internet, dakika ve SMS kullanımı, 3 ay YouTube Premium, "
                "Prime ayrıcalıkları, 12 ay Safe Family ve her ay hediye GB."
            ),
        )
    )
    assert benefit_tags(out) == [
        "5g_ready",
        "bonus_data",
        "overage_protection",
        "premium_perks",
        "safety_services",
        "shared_allowance",
        "streaming_perks",
        "unlimited_calls",
        "unlimited_data",
        "unlimited_sms",
    ]


def test_postpaid_taxonomy_and_parent_requirement_add_eligibility_tags():
    out = normalize_record(
        _rec(
            line_type="postpaid",
            customer_type="new",
            package_group="first_turkcell",
            commitment_months=12,
            conditions="Hat sahibi ile kullanıcının ebeveynlik koşulları geçerlidir.",
        )
    )
    assert out["eligibility_tags"] == ["parental_relationship_required"]


def test_build_doc_text_includes_campaign():
    doc = build_doc_text(
        _typed(
            operator="vodafone",
            name="Kolay Paket",
            customer_type="new",
            campaign="5 GB hediye",
        )
    )
    assert "Kampanya: 5 GB hediye" in doc


def test_normalize_assigns_child_taxonomy_and_age_range():
    out = normalize_record(
        _rec(
            line_type="postpaid",
            customer_type="new",
            package_group="first_turkcell",
            commitment_months=12,
        )
    )
    assert out["operator_family"] == "first_turkcell"
    assert out["audience_segment"] == "child"
    assert (out["age_min"], out["age_max"]) == (7, 15)
    assert "ilk hat / yasa ozel" in out["doc_text"]


def test_postpaid_no_commitment_price_is_parsed_and_stated():
    out = normalize_record(
        _rec(
            operator="vodafone",
            line_type="postpaid",
            customer_type="new",
            commitment_months=12,
            no_commitment_price_try="450 TL",
        )
    )
    assert out["no_commitment_price_try"] == 450.0
    assert "Taahhutsuz fiyati 450 TL'dir." in out["doc_text"]


def test_prepaid_never_carries_no_commitment_price():
    out = normalize_record(_rec(no_commitment_price_try="450 TL"))
    assert out["no_commitment_price_try"] is None
    assert "Taahhutsuz fiyati" not in out["doc_text"]


def test_non_mobile_product_is_rejected_for_rag_catalog():
    with pytest.raises(ValueError, match="yalnizca mobile_voice"):
        normalize_record(
            _rec(
                line_type="postpaid",
                customer_type="new",
                package_group="hit_child_watch",
            ),
            require_mobile_voice=True,
        )


def test_id_survives_price_and_allowance_changes_and_canonical_id_is_gone():
    """`id` (uuid5 of operator|line_type|name) is the only identity: a repricing or a GB
    change keeps it. The old canonical_id fingerprint changed in exactly those cases."""
    from app.ingest.load import package_id
    before = normalize_record(_rec(name="Paket 20 GB", price_try=200.0))
    after = normalize_record(_rec(name="Paket 20 GB", price_try=250.0, internet_gb=25))
    assert "canonical_id" not in before and "canonical_id" not in after
    key = lambda r: package_id(r["operator"], r["line_type"], r["name"])  # noqa: E731
    assert key(before) == key(after)
    assert "kanonik kimlik" not in before["doc_text"]
