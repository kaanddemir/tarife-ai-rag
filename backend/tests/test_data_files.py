"""Schema checks for the curated bucket files in app/ingest/data/.

These files are hand-maintained, so a typo in a header (or a package that never
got its `line_type` fixed) would otherwise only surface at ingest time -- or
worse, silently land a package in the wrong customer bucket. Every new
`{operator}_{line_type}_{customer_type}.json` is covered automatically.
"""
import json

import pytest

from app.ingest.load import DATA_DIR, load_records, merge_customer_types
from app.ingest.normalize import UNLIMITED, normalize_record

LINE_TYPES = {"prepaid", "postpaid"}
CUSTOMER_TYPES = {"new", "existing", "any"}
OPERATORS = {"turkcell", "vodafone", "turk_telekom"}
PRICE_PERIODS = {"month", "one_time", "28_day", "3_month", "6_month", "12_month"}
VALIDITY_UNITS = {"day", "week", "month"}

# Every package object carries exactly these keys -- both missing and extra are errors.
# operator/line_type/customer_type are stamped from the header, not repeated per package.
PACKAGE_KEYS = {
    "name",
    "package_group",
    "price_try",
    "price_period",
    "price_lock_months",
    "internet_gb",
    "social_media_gb",
    "bonus_internet_gb",
    "total_internet_gb",
    "voice_minutes",
    "sms_count",
    "validity_value",
    "validity_unit",
    "validity_days",
    "conditions",
    "campaign",
    "source_url",
}

POSTPAID_ONLY_KEYS = {"commitment_months", "commitment_status"}

# price_try is the committed price (postpaid). A package may optionally also carry the
# higher no-commitment price; when absent, the row simply has no such key.
OPTIONAL_PRICE_KEYS = {"no_commitment_price_try"}

DATA_FILES = sorted(DATA_DIR.glob("*.json"))


def test_data_dir_is_not_empty():
    assert DATA_FILES, f"no bucket files found in {DATA_DIR}"


@pytest.mark.parametrize("path", DATA_FILES, ids=lambda p: p.name)
def test_header_matches_filename(path):
    """Filename is the bucket contract: {operator}_{line_type}_{customer_type}.json"""
    data = json.loads(path.read_text(encoding="utf-8"))
    stem = path.stem
    line_type = next(lt for lt in LINE_TYPES if f"_{lt}_" in stem)
    operator, customer_type = stem.split(f"_{line_type}_")

    assert operator in OPERATORS, operator
    assert customer_type in CUSTOMER_TYPES, customer_type
    assert data["operator"] == operator
    assert data["line_type"] == line_type
    assert data["customer_type"] == customer_type


@pytest.mark.parametrize("path", DATA_FILES, ids=lambda p: p.name)
def test_package_count_matches(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["package_count"] == len(data["packages"])


@pytest.mark.parametrize("path", DATA_FILES, ids=lambda p: p.name)
def test_packages_carry_exactly_the_canonical_keys(path):
    """Schema contract: a missing field blows up at ingest, an extra field is silently
    dropped. We catch both here."""
    data = json.loads(path.read_text(encoding="utf-8"))
    expected = PACKAGE_KEYS | (POSTPAID_ONLY_KEYS if data["line_type"] == "postpaid" else set())
    for pkg in data["packages"]:
        keys = pkg.keys() - OPTIONAL_PRICE_KEYS
        assert keys == expected, (
            f"{path.name} / {pkg.get('name')}: "
            f"missing {sorted(expected - keys)}, extra {sorted(keys - expected)}"
        )


def _gb_fields(pkg) -> tuple:
    return (
        pkg.get("internet_gb"),
        pkg.get("social_media_gb"),
        pkg.get("bonus_internet_gb"),
        pkg.get("total_internet_gb"),
    )


@pytest.mark.parametrize("path", DATA_FILES, ids=lambda p: p.name)
def test_total_internet_gb_is_the_sum_of_its_parts(path):
    """In the source data the total for Turk Telekom Prime packages was off by one
    (49 for a 50 GB package). Unlimited (-1) components are a separate quota and don't
    enter the sum."""
    data = json.loads(path.read_text(encoding="utf-8"))
    for pkg in data["packages"]:
        internet, social, bonus, total = _gb_fields(pkg)
        if internet is None or total is None:
            continue
        if UNLIMITED in (internet, social, bonus):
            assert total == UNLIMITED, (
                f"{path.name} / {pkg['name']}: unlimited component requires "
                f"total_internet_gb={UNLIMITED}, got {total}"
            )
            continue
        if total == UNLIMITED:
            continue
        expected = internet + sum(v for v in (social, bonus) if v and v > 0)
        assert total == pytest.approx(expected), (
            f"{path.name} / {pkg['name']}: total_internet_gb={total}, expected {expected}"
        )


@pytest.mark.parametrize("path", DATA_FILES, ids=lambda p: p.name)
def test_gb_fields_are_unlimited_or_non_negative(path):
    """The source data had a package with total_internet_gb=-2; negatives other than -1 are forbidden."""
    data = json.loads(path.read_text(encoding="utf-8"))
    for pkg in data["packages"]:
        for name, value in zip(
            ("internet_gb", "social_media_gb", "bonus_internet_gb", "total_internet_gb"),
            _gb_fields(pkg),
        ):
            if value is None:
                continue
            assert value == UNLIMITED or value >= 0, f"{path.name} / {pkg['name']}: {name}={value}"


@pytest.mark.parametrize("path", DATA_FILES, ids=lambda p: p.name)
def test_commitment_and_price_lock_are_sane(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    for pkg in data["packages"]:
        for field in ("commitment_months", "price_lock_months"):
            value = pkg.get(field)
            if value is None:
                continue
            assert 1 <= value <= 36, f"{path.name} / {pkg['name']}: {field}={value}"
        # Commitment does not apply to prepaid lines.
        if data["line_type"] == "prepaid":
            assert not pkg.get("commitment_months"), pkg["name"]
            continue
        # Postpaid states the commitment status explicitly: a package falling to
        # `unknown` takes a quality penalty in retrieval and enters the prompt as "review_required".
        status = pkg["commitment_status"]
        assert status in {"committed", "no_commitment"}, f"{path.name} / {pkg['name']}: {status}"
        assert bool(pkg["commitment_months"]) == (status == "committed"), (
            f"{path.name} / {pkg['name']}: commitment_months={pkg['commitment_months']} "
            f"conflicts with commitment_status={status}"
        )


def test_every_record_normalizes():
    """load_records stamps the header onto each package; normalize_record then
    validates the enums and builds doc_text. A bad bucket raises ValueError."""
    records = load_records()
    assert records
    for rec in records:
        out = normalize_record(rec)
        assert out["line_type"] in LINE_TYPES
        assert out["customer_type"] in CUSTOMER_TYPES
        assert out["doc_text"]


def test_merge_collapses_cross_bucket_duplicates_into_any():
    records = [
        {"operator": "turkcell", "name": "Firsat 20 GB", "line_type": "prepaid", "customer_type": "new"},
        {"operator": "turkcell", "name": "Firsat 20 GB", "line_type": "prepaid", "customer_type": "existing"},
        {"operator": "turkcell", "name": "Hos Geldin 50 GB", "line_type": "prepaid", "customer_type": "new"},
    ]
    merged = merge_customer_types(records)
    by_name = {r["name"]: r["customer_type"] for r in merged}
    assert by_name == {"Firsat 20 GB": "any", "Hos Geldin 50 GB": "new"}


def test_merge_keeps_same_name_across_line_types_separate():
    """A postpaid and a prepaid package with the same name are different products; don't merge them."""
    records = [
        {"operator": "vodafone", "name": "Kolay Paket", "line_type": "prepaid", "customer_type": "new"},
        {"operator": "vodafone", "name": "Kolay Paket", "line_type": "postpaid", "customer_type": "new"},
    ]
    assert len(merge_customer_types(records)) == 2


def test_real_data_has_no_cross_bucket_duplicates_left():
    records = load_records()
    merged = merge_customer_types(records)
    keys = [(r["operator"], r["name"], r["line_type"], r["customer_type"]) for r in merged]
    assert len(keys) == len(set(keys))


def test_real_catalog_has_complete_standard_taxonomy_and_stable_ids():
    normalized = [normalize_record(r, require_mobile_voice=True) for r in merge_customer_types(load_records())]
    canonical_ids = []
    for pkg in normalized:
        assert pkg["operator_family"] == pkg["package_group"]
        assert pkg["audience_segment"] in {"general", "youth", "child", "family", "public_special"}
        assert pkg["product_scope"] == "mobile_voice"
        assert pkg["canonical_id"]
        canonical_ids.append(pkg["canonical_id"])
    assert len(canonical_ids) == len(set(canonical_ids))


def test_real_catalog_has_no_review_required_records():
    """A `review_required` package takes a +0.08 distance penalty in retrieval and
    enters the prompt as "Veri kalitesi: review_required". There must not be a single one in the catalog."""
    flagged = [
        (pkg["operator"], pkg["name"], pkg["missing_fields"])
        for pkg in (
            normalize_record(record, require_mobile_voice=True)
            for record in merge_customer_types(load_records())
        )
        if pkg["data_quality_status"] != "complete"
    ]
    assert not flagged, flagged


def test_device_only_packages_are_not_in_active_catalog():
    names = {record["name"] for record in load_records()}
    assert not any("Akıllı Çocuk Saati" in name for name in names)


def test_contentless_base_tariffs_are_not_in_active_catalog():
    """Base tariffs with no content (GB/minutes/SMS) are meaningless in a comparison catalog."""
    names = {record["name"] for record in load_records()}
    assert "Yeni İçişleri Baz Tarifesi" not in names


def test_faq_only_facts_survived_in_conditions():
    """`faq` alani kaldirilirken baska hicbir alanda gecmeyen 7 cumle `conditions`'a
    tasindi; doc_text ve prompt bu metni okuyor."""
    by_name = {record["name"]: record for record in load_records()}
    assert "devretmez" in by_name["Transfer 1 GB Paketi"]["conditions"]
    assert "SMS kanalından" in by_name["Transfer 30 GB Paketi"]["conditions"]


def test_prepaid_catalog_is_acquisition_only_and_fixed_content():
    prepaid_paths = [path for path in DATA_FILES if "_prepaid_" in path.stem]
    assert prepaid_paths
    for path in prepaid_paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["customer_type"] == "new"
        for pkg in data["packages"]:
            assert pkg["package_group"] != "digital_flexible"
            assert pkg["price_period"] in PRICE_PERIODS, f"{path.name} / {pkg['name']}"
            assert pkg["validity_unit"] in VALIDITY_UNITS, f"{path.name} / {pkg['name']}"
            assert pkg["validity_days"] > 0, f"{path.name} / {pkg['name']}"
            assert pkg["internet_gb"] is not None, f"{path.name} / {pkg['name']}"
            assert pkg["total_internet_gb"] is not None, f"{path.name} / {pkg['name']}"


def test_prepaid_catalog_contains_expected_acquisition_products_and_taxonomy():
    prepaid = {
        pkg["name"]: pkg
        for pkg in (
            normalize_record(record, require_mobile_voice=True)
            for record in load_records()
            if record["line_type"] == "prepaid"
        )
    }
    assert "Hoş Geldin 25 GB Paketi" not in prepaid
    assert "Yeni Nesil Dijital Paket" not in prepaid

    assert prepaid["5G Cepte Fırsat 20"]["price_lock_months"] == 3
    assert "number_porting" in prepaid["5G Cepte Fırsat 20"]["eligibility_tags"]
    assert prepaid["Transfer 10 GB Paketi"]["price_lock_months"] == 12
    assert "number_porting" in prepaid["Transfer 10 GB Paketi"]["eligibility_tags"]
    assert "new_line" in prepaid["Memlekete Hoş Geldin 40GB Paketi"]["eligibility_tags"]
    assert "5G Cepte Fırsat 100 GB" not in prepaid
    assert prepaid["5G Fırsat 3 Ay 100 GB Paketi"]["audience_segment"] == "youth"
    assert prepaid["5G Fırsat 3 Ay 100 GB Paketi"]["age_max"] == 25
