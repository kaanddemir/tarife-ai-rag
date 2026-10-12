import pytest

from app.rag.retrieve import (
    detect_activation_channel,
    detect_age,
    detect_audience_segment,
    detect_customer_type,
    detect_gb,
    detect_line_type,
    detect_max_price,
    detect_operator,
    _eligibility_filters,
    gb_band_filter,
)


def test_detect_operator():
    assert detect_operator("Turkcell paketleri neler?") == "turkcell"
    assert detect_operator("türk telekom 20 gb") == "turk_telekom"
    assert detect_operator("vodafone kolay paket") == "vodafone"
    assert detect_operator("en ucuz paket") is None


def test_detect_max_price():
    assert detect_max_price("200 TL altindaki paketler") == 200.0
    assert detect_max_price("en fazla 150 lira") == 150.0
    assert detect_max_price("150 liraya kadar") == 150.0
    assert detect_max_price("20 GB paketleri karsilastir") is None
    # Regresyon: 'az' kelime sinirsiz arandiginda 'lazim' icinde eslesip sahte
    # fiyat filtresi kuruyordu.
    assert detect_max_price("20 GB paket lazım") is None
    assert detect_max_price("bana faturalı hat lazım") is None
    # "en az" is a MINIMUM; it used to be read as a ceiling (see detect_price_bounds).
    assert detect_max_price("en az 100 TL") is None
    # Regression: don't treat the GB amount as a price; the real price can come after GB.
    assert detect_max_price("20 GB'a kadar paketler") is None
    assert detect_max_price("20 GB altında 150 TL paket") == 150.0


def test_detect_gb():
    assert detect_gb("20 GB paketleri karsilastir") == 20.0
    assert detect_gb("ogrenciler icin en iyi paket") is None


def test_gb_band_keeps_unlimited_packages():
    """internet_gb=-1 (sinirsiz) her GB istegini karsilar; +/-5 bandina takilmamali."""
    sql = str(gb_band_filter(20.0).compile(compile_kwargs={"literal_binds": True}))
    assert "packages.internet_gb = -1" in sql
    assert " OR " in sql
    assert ">= 15.0" in sql and "<= 25.0" in sql
    # Measured on general-use internet: a general-scope bonus counts, restricted ones do not.
    assert "packages.bonus_internet_scope = 'general'" in sql


def test_detect_line_type():
    assert detect_line_type("faturasız 20 GB paket") == "prepaid"
    assert detect_line_type("hazır kart paketleri") == "prepaid"
    assert detect_line_type("faturalı hat almak istiyorum") == "postpaid"
    assert detect_line_type("en ucuz 20 GB paket") is None


def test_detect_line_type_does_not_confuse_faturasiz_with_faturali():
    """prepaid pattern must run first: 'faturasiz' contains 'fatura'."""
    assert detect_line_type("faturasiz paket") == "prepaid"
    assert detect_line_type("faturasız paket") == "prepaid"


def test_detect_customer_type():
    assert detect_customer_type("yeni müşteriyim, hangi paket?") == "new"
    assert detect_customer_type("numara taşıma ile geçmek istiyorum") == "new"
    assert detect_customer_type("mevcut müşteriyim") == "existing"
    assert detect_customer_type("zaten Turkcell kullanıyorum") == "existing"
    assert detect_customer_type("en ucuz 20 GB paket") is None
    # Regression: '... kullaniyorum' can be a usage phrase. Since every catalog
    # package is customer_type='new', a wrong 'existing' detection returned ZERO
    # results. Only count 'existing' when bound to an operator/line.
    assert detect_customer_type("çok internet kullanıyorum") is None
    assert detect_customer_type("sosyal medya kullanıyorum") is None
    assert detect_customer_type("Vodafone'u kullanıyorum") == "existing"


def test_detect_customer_type_ambiguous_returns_none():
    """When both signals are present, no filter is applied; the embedding does the ranking."""
    assert detect_customer_type("mevcut müşteriyim ama yeni hat açtırmak istiyorum") is None


def test_detect_audience_aliases_and_age():
    assert detect_audience_segment("çocuğuma ilk telefon hattı arıyorum") == "child"
    assert detect_audience_segment("öğrenci ve genç tarifesi") == "youth"
    assert detect_audience_segment("ailece ortak kullanım") == "family"
    assert detect_audience_segment("İçişleri personeliyim") == "public_special"
    assert detect_age("14 yaş için faturalı hat") == 14


def test_eligibility_filters_always_exclude_non_phone_products():
    sql = " AND ".join(
        str(item.compile(compile_kwargs={"literal_binds": True}))
        for item in _eligibility_filters("çocuğuma 14 yaş için faturalı hat")
    )
    assert "packages.product_scope = 'mobile_voice'" in sql
    assert "packages.audience_segment = 'child'" in sql
    assert "packages.age_min <= 14" in sql
    assert "packages.age_max >= 14" in sql


def test_existing_customer_filter_does_not_admit_new_only_rows():
    sql = " AND ".join(
        str(item.compile(compile_kwargs={"literal_binds": True}))
        for item in _eligibility_filters("mevcut müşteriyim, faturasız paket arıyorum")
    )
    assert "packages.line_type = 'prepaid'" in sql
    assert "packages.customer_type IN ('existing', 'any')" in sql
    assert "'new'" not in sql


@pytest.mark.parametrize("q", ["numaramı taşıyacağım faturasız paket", "numaramı taşırsam ne olur", "numaramızı taşıyoruz"])
def test_porting_detected_in_any_inflection(q):
    assert detect_activation_channel(q) == "number_porting"
