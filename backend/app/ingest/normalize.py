"""Turn raw package records (from the curated data/*.json per-operator files)
into normalized rows: typed fields + a natural-language `doc_text` used for
embedding and retrieval grounding.
"""
import re
import unicodedata
from typing import Optional

OPERATOR_LABELS = {
    "turkcell": "Turkcell",
    "vodafone": "Vodafone",
    "turk_telekom": "Turk Telekom",
}

LINE_TYPE_LABELS = {"prepaid": "faturasiz", "postpaid": "faturali"}

CUSTOMER_TYPE_LABELS = {
    "new": "yeni musteri",
    "existing": "mevcut musteri",
    "any": "yeni veya mevcut musteri",
}

# We write eligibility explicitly into doc_text so the embedding carries the
# "faturali" / "yeni musteri" phrases and can surface the right bucket even without a filter.
_CUSTOMER_TYPE_PHRASES = {
    "new": "yeni musteri / numara tasima veya yeni hat ile gelenlere ozel",
    "existing": "hali hazirda hatti olan mevcut musterilere ozel",
    "any": "hem yeni hem mevcut musteriler yararlanabilir",
}

# Sentinel for unlimited numeric fields. It applied to voice_minutes/sms_count from
# the start; with postpaid packages the GB fields (internet_gb, social_media_gb,
# total_internet_gb) can carry this value too.
UNLIMITED = -1

AUDIENCE_SEGMENTS = {"general", "youth", "child", "family", "public_special"}
PRODUCT_SCOPES = {"mobile_voice", "wearable", "data_only", "iot"}
COMMITMENT_STATUSES = {"committed", "no_commitment", "unknown"}
PRICE_PERIODS = {"month", "one_time", "28_day", "3_month", "6_month", "12_month"}
VALIDITY_UNITS = {"day", "week", "month"}

_YOUTH_FAMILIES = {"gnc", "freezone", "freezone_red", "selfy_1plus1"}
_CHILD_FAMILIES = {"first_turkcell", "first_line", "hit"}
_FAMILY_FAMILIES = {"family_prime", "family_start"}
_PUBLIC_FAMILIES = {"new_parliamentarian", "new_internal_affairs"}

_AGE_RANGES = {
    "gnc": (7, 25),
    "freezone": (None, 25),
    "freezone_red": (None, 25),
    "first_turkcell": (7, 15),
    "first_line": (7, 16),
    "hit": (6, 15),
}

_AUDIENCE_LABELS = {
    "general": "genel kullanima uygun",
    "youth": "genc kullanicilara yonelik",
    "child": "ilk hat / yasa ozel",
    "family": "aile kullanimi icin",
    "public_special": "kamu veya kurum uygunlugu gerektirir",
}

_BENEFIT_LABELS = {
    "unlimited_social": "sinirsiz sosyal medya",
    "unlimited_entertainment": "sinirsiz eglence platformlari",
    "bonus_data": "bonus internet",
    "double_data": "iki kat / ek internet",
    "netflix": "Netflix avantaji",
    "international_usage": "yurt disi kullanim avantaji",
    "online_only": "yalnizca online basvuru",
    "price_guarantee": "sabit fiyat garantisi",
    "long_validity": "uzun donemli kullanim",
    "unlimited_messaging": "sinirsiz mesajlasma uygulamasi",
    "unlimited_onnet_calls": "operator ici sinirsiz konusma",
    "app_specific_data": "uygulamaya ozel internet",
    "digital_perks": "dijital ayricaliklar",
    "free_delivery": "ucretsiz veya hizli hat teslimati",
    "data_sharing": "internet paylasim hakki",
    "youth_perks": "genclere ozel ek avantajlar",
    "unlimited_data": "sinirsiz genel internet",
    "unlimited_calls": "sinirsiz her yone konusma",
    "unlimited_sms": "sinirsiz SMS",
    "overage_protection": "asim korumasi",
    "streaming_perks": "dijital yayin uyelikleri",
    "premium_perks": "premium musteri ayricaliklari",
    "shared_allowance": "ortak aile kullanim havuzu",
    "safety_services": "aile veya cocuk guvenligi hizmetleri",
    "5g_ready": "5G uyumlu tarife",
}

_ELIGIBILITY_LABELS = {
    "new_line": "yeni hat",
    "number_porting": "numara tasima",
    "online_only": "yalnizca online basvuru",
    "province_restricted": "yalnizca belirli illerde",
    "parliamentarian": "parlamenter uygunlugu",
    "internal_affairs_personnel": "Icisleri personeli uygunlugu",
    "activation_window": "aktivasyondan sonra sinirli basvuru suresi",
    "identity_required": "TCKN veya YKN gerektirir",
    "age_restricted": "yas araligi kosulu",
    "parental_relationship_required": "ebeveyn veya cocuk kullanici tanimi gerektirir",
}

_UNLIMITED_WORDS = ("sinirsiz", "sınırsız", "unlimited")

# Rough day equivalents (used to derive validity_days when it's not given).
_UNIT_DAYS = {"day": 1, "week": 7, "month": 30}
_UNIT_LABELS = {"day": "gun", "week": "hafta", "month": "ay"}

# price_period -> label describing which period the price is for (doc_text).
# For multi-month packages price_try is the TOTAL charge; calling it "monthly fee" is misleading.
_PERIOD_PRICE_LABELS = {
    "month": "Aylik ucret",
    "one_time": "Tek seferlik paket ucreti",
    "28_day": "28 gunluk paket ucreti",
    "3_month": "3 aylik paket ucreti (toplam)",
    "6_month": "6 aylik paket ucreti (toplam)",
    "12_month": "12 aylik paket ucreti (toplam)",
}

def _num(text: str, pattern: str) -> Optional[float]:
    m = re.search(pattern, text, flags=re.IGNORECASE)
    return float(m.group(1).replace(",", ".")) if m else None


def parse_amount(value) -> Optional[int]:
    """Parse a minutes/SMS value that may be a number or 'sinirsiz' (unlimited). -1 = unlimited."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).strip().lower()
    if any(w in s for w in _UNLIMITED_WORDS):
        return -1
    m = re.search(r"(\d+)", s)
    return int(m.group(1)) if m else None


def parse_gb(value) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value)
    if any(word in text.lower() for word in _UNLIMITED_WORDS):
        return float(UNLIMITED)
    return _num(text, r"(\d+(?:[.,]\d+)?)\s*gb")


def parse_price(value) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return _num(str(value), r"(\d+(?:[.,]\d+)?)\s*(?:tl|₺)?")


def _amount_phrase(value: Optional[int], unit: str) -> str:
    if value is None:
        return f"belirtilmemis {unit}"
    if value == UNLIMITED:
        return f"sinirsiz {unit}"
    return f"{value} {unit}"


def _gb_amount(value) -> Optional[str]:
    """'20 GB' / 'sinirsiz' (unlimited). None -> the caller should omit the phrase entirely."""
    if value is None:
        return None
    return "sinirsiz" if value == UNLIMITED else f"{value:g} GB"


def _commitment_phrases(rec: dict) -> list[str]:
    parts = []
    if rec.get("line_type") == "postpaid":
        status = rec.get("commitment_status") or _commitment_status(rec)
        months = rec.get("commitment_months")
        if status == "committed" and months:
            parts.append(f"{months} ay taahhutludur.")
        elif status == "no_commitment":
            parts.append("Taahhutsuzdur.")
        elif status == "unknown":
            parts.append("Taahhut bilgisi kaynakta belirtilmemistir.")
        # price_try is the committed price; state the no-commitment price when the
        # source gives it (higher price you pay if you don't commit).
        no_commit = rec.get("no_commitment_price_try")
        if no_commit is not None:
            parts.append(f"Taahhutsuz fiyati {no_commit:g} TL'dir.")
    lock = rec.get("price_lock_months")
    if lock:
        unit = "fatura donemi" if rec.get("line_type") == "postpaid" else "ay"
        parts.append(f"Fiyat {lock} {unit} boyunca sabittir.")
    return parts


def _taxonomy(rec: dict) -> dict:
    family = rec.get("operator_family") or rec.get("package_group") or "standard"
    if family in _YOUTH_FAMILIES:
        audience = "youth"
    elif family in _CHILD_FAMILIES:
        audience = "child"
    elif family in _FAMILY_FAMILIES:
        audience = "family"
    elif family in _PUBLIC_FAMILIES:
        audience = "public_special"
    else:
        audience = "general"

    name = str(rec.get("name") or "").lower()
    if family == "hit_child_watch" or "akıllı" in name and "saat" in name:
        scope = "wearable"
    elif "iot" in name or "m2m" in name:
        scope = "iot"
    elif "data only" in name or "sadece internet" in name or "mobil wifi" in name:
        scope = "data_only"
    else:
        scope = rec.get("product_scope", "mobile_voice")
    age_min, age_max = _AGE_RANGES.get(family, (None, None))
    return {
        "operator_family": family,
        "audience_segment": audience,
        "product_scope": scope,
        "age_min": rec.get("age_min", age_min),
        "age_max": rec.get("age_max", age_max),
    }


def _tags(rec: dict) -> tuple[list[str], list[str]]:
    text = " ".join(str(rec.get(k) or "") for k in ("name", "conditions", "campaign")).lower()
    eligibility = set(rec.get("eligibility_tags") or [])
    benefits = set(rec.get("benefit_tags") or [])
    if "yeni hat" in text:
        eligibility.add("new_line")
    if re.search(r"numara\w*.{0,40}ta[sş][ıi]", text):
        eligibility.add("number_porting")
    if "online" in text:
        eligibility.add("online_only")
        benefits.add("online_only")
    if (
        "belirli iller" in text
        or "illere özel" in text
        or "illere ozel" in text
        or "sadece adana," in text
    ):
        eligibility.add("province_restricted")
    if any(
        phrase in text
        for phrase in (
            "aktivasyonu takip eden ilk",
            "aktivasyonunu takip eden ilk",
            "aktivasyonlarini takip eden 90",
            "geliş tarihinden itibaren ilk",
            "gelis tarihinden itibaren ilk",
            "son 90 gün içinde",
            "son 90 gun icinde",
        )
    ):
        eligibility.add("activation_window")
    if "tckn/ykn" in text or re.search(r"tc ya da yabancı kimlik|tc ya da yabanci kimlik", text):
        eligibility.add("identity_required")
    if (
        re.search(r"\b\d{1,2}\s*-\s*\d{1,2}\s*yaş", text)
        or rec.get("age_min") is not None
        or rec.get("age_max") is not None
    ):
        eligibility.add("age_restricted")
    if "ebeveyn" in text or "çocuk kullanıcı tanımlaması" in text or "cocuk kullanici tanimlamasi" in text:
        eligibility.add("parental_relationship_required")
    family = rec.get("operator_family") or rec.get("package_group")
    if family == "new_parliamentarian":
        eligibility.add("parliamentarian")
    elif family == "new_internal_affairs":
        eligibility.add("internal_affairs_personnel")
    if rec.get("social_media_gb") == UNLIMITED or "sınırsız sosyal" in text:
        benefits.add("unlimited_social")
    if "sınırsız eğlence" in text or "sinirsiz eglence" in text:
        benefits.add("unlimited_entertainment")
    if rec.get("bonus_internet_gb") not in (None, 0) or re.search(
        r"hediye\s+(?:\d+(?:[.,]\d+)?\s*)?(?:gb|internet)|sil süpür.*internet|sil supur.*internet",
        text,
    ):
        benefits.add("bonus_data")
    if family == "selfy_1plus1":
        benefits.add("double_data")
    if "netflix" in text:
        benefits.add("netflix")
    if "avrupa" in text or "yurt dış" in text or "yurt dis" in text:
        benefits.add("international_usage")
    if rec.get("price_lock_months"):
        benefits.add("price_guarantee")
    if derive_validity_days(rec) is not None and derive_validity_days(rec) >= 90:
        benefits.add("long_validity")
    if "sınırsız whatsapp" in text or "sinirsiz whatsapp" in text or "sınırsız yaay" in text:
        benefits.add("unlimited_messaging")
    if "türk telekomlularla sınırsız" in text or "turk telekomlularla sinirsiz" in text:
        benefits.add("unlimited_onnet_calls")
    if "bip" in text and (" gb" in text or "internet" in text):
        benefits.add("app_specific_data")
    if "bi' dünya fırsat" in text or "bi' dunya firsat" in text:
        benefits.add("digital_perks")
    if "ücretsiz dijital kurye" in text or "ucretsiz dijital kurye" in text or "24 saatte teslimat" in text:
        benefits.add("free_delivery")
    if "internet paylaşım" in text or "internet paylasim" in text:
        benefits.add("data_sharing")
    if "markada indirim" in text and (family in _YOUTH_FAMILIES or "genç" in text or "genc" in text):
        benefits.add("youth_perks")
    if rec.get("internet_gb") == UNLIMITED or rec.get("total_internet_gb") == UNLIMITED:
        benefits.add("unlimited_data")
    if rec.get("voice_minutes") == UNLIMITED:
        benefits.add("unlimited_calls")
    if rec.get("sms_count") == UNLIMITED:
        benefits.add("unlimited_sms")
    if any(phrase in text for phrase in ("aşımsız", "asimsiz", "aşıma kapalı", "asima kapali", "durma özelliği", "durma ozelligi", "kontrol sizde")):
        benefits.add("overage_protection")
    if any(service in text for service in ("youtube premium", "muud", "tivibu go", "e-dergi", "amazon prime video", "chatgpt go")):
        benefits.add("streaming_perks")
    if any(phrase in text for phrase in ("prime ayrıcalıkları", "prime ayricaliklari", "platinum özel", "platinum ozel", "red ayrıcalıkları", "red ayricaliklari")):
        benefits.add("premium_perks")
    if "ortak internet, dakika ve sms" in text or "ortak kullanım" in text or "ortak kullanim" in text:
        benefits.add("shared_allowance")
    if any(service in text for service in ("safe family", "find my kids", "çağrı engelleme", "cagri engelleme")):
        benefits.add("safety_services")
    if re.search(r"\b5g(?:'ye|ye)?\b", text):
        benefits.add("5g_ready")
    return sorted(eligibility), sorted(benefits)


def _commitment_status(rec: dict) -> str:
    explicit = rec.get("commitment_status")
    if explicit in COMMITMENT_STATUSES:
        return explicit
    if rec.get("commitment_months"):
        return "committed"
    text = str(rec.get("conditions") or "").lower()
    if "taahhütsüz" in text or "taahhutsuz" in text:
        return "no_commitment"
    return "no_commitment" if rec.get("line_type") == "prepaid" else "unknown"


def _canonical_id(rec: dict) -> str:
    def clean(value) -> str:
        raw = unicodedata.normalize("NFKD", str(value or "none"))
        ascii_text = raw.encode("ascii", "ignore").decode().lower()
        return re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")

    identity = (
        rec.get("operator"), rec.get("line_type"), rec.get("operator_family"),
        rec.get("internet_gb"), rec.get("social_media_gb"), rec.get("bonus_internet_gb"),
        rec.get("total_internet_gb"), rec.get("voice_minutes"), rec.get("sms_count"),
    )
    return ":".join(clean(value) for value in identity)


def _quality(rec: dict) -> tuple[str, list[str]]:
    missing = [field for field in ("price_try", "source_url") if not rec.get(field)]
    if rec.get("line_type") == "postpaid" and rec.get("commitment_status") == "unknown":
        missing.append("commitment_status")
    if rec.get("internet_gb") is None and rec.get("total_internet_gb") is None:
        missing.append("internet_gb")
    return ("review_required" if missing else "complete", missing)


def derive_validity_days(rec: dict) -> Optional[int]:
    """Use validity_days if given, otherwise derive it from validity_value + validity_unit."""
    if rec.get("validity_days") is not None:
        return int(rec["validity_days"])
    value, unit = rec.get("validity_value"), rec.get("validity_unit")
    if value is not None and unit in _UNIT_DAYS:
        return int(value) * _UNIT_DAYS[unit]
    return None


def _validity_phrase(rec: dict) -> Optional[str]:
    """None when the record carries no validity info -- for postpaid packages the
    billing period is already read from price_period, where this phrase is meaningless."""
    value, unit = rec.get("validity_value"), rec.get("validity_unit")
    days = derive_validity_days(rec)
    if value is not None and unit in _UNIT_LABELS:
        label = _UNIT_LABELS[unit]
        if days is not None and unit != "day":
            return f"{value} {label} ({days} gun) gecerli"
        return f"{value} {label} gecerli"
    if days is not None:
        return f"{days} gun gecerli"
    return None


def build_doc_text(rec: dict) -> str:
    """Fixed section order; the canonical per-package embedding/grounding document."""
    op = OPERATOR_LABELS.get(rec.get("operator", ""), rec.get("operator", ""))
    gb = rec.get("total_internet_gb")
    if gb is None:
        gb = rec.get("internet_gb")
    gb_amount = _gb_amount(gb)
    gb_phrase = f"{gb_amount} internet" if gb_amount else "belirtilmemis internet"
    price = rec.get("price_try")
    price_phrase = f"{price:g} TL" if price is not None else "fiyat belirtilmemis"
    price_label = _PERIOD_PRICE_LABELS.get(rec.get("price_period"), "Paket ucreti")

    line_label = LINE_TYPE_LABELS[rec["line_type"]]
    validity = _validity_phrase(rec)
    price_sentence = f"{price_label} {price_phrase}"
    if validity:
        price_sentence += f", {validity}"

    audience = rec.get("audience_segment", "general")
    audience_phrase = _AUDIENCE_LABELS.get(audience, audience)
    age_min, age_max = rec.get("age_min"), rec.get("age_max")
    if age_min is not None and age_max is not None:
        audience_phrase += f"; {age_min}-{age_max} yas arasi"
    elif age_max is not None:
        audience_phrase += f"; en fazla {age_max} yas"
    eligibility = ", ".join(
        _ELIGIBILITY_LABELS.get(tag, tag) for tag in rec.get("eligibility_tags") or []
    ) or "ek kosul etiketi yok"
    benefits = ", ".join(
        _BENEFIT_LABELS.get(tag, tag) for tag in rec.get("benefit_tags") or []
    ) or "ozel avantaj etiketi yok"

    parts = [
        f"Kimlik: {op} {rec.get('name', '')} {line_label} paketi; "
        f"operator ailesi {rec.get('operator_family', 'standard')}; "
        f"urun kapsami {rec.get('product_scope', 'mobile_voice')}; "
        f"kanonik kimlik {rec.get('canonical_id', 'belirtilmemis')}.",
        f"Uygunluk: {_CUSTOMER_TYPE_PHRASES[rec['customer_type']]}; {audience_phrase}; "
        f"etiketler: {eligibility}.",
        f"Icerik: {gb_phrase}, {_amount_phrase(rec.get('voice_minutes'), 'dakika')}, "
        f"{_amount_phrase(rec.get('sms_count'), 'SMS')}.",
        f"Fiyat: {price_sentence}.",
    ]
    parts.extend(_commitment_phrases(rec))

    social = rec.get("social_media_gb")
    if social == UNLIMITED:
        parts.append("Sosyal medya (TikTok, Instagram, YouTube vb.) kullanimi sinirsizdir.")
    elif social:
        parts.append(f"Bunun {social:g} GB'i sosyal medya (TikTok, Instagram, YouTube vb.) kotasidir.")
    bonus = rec.get("bonus_internet_gb")
    if bonus == UNLIMITED:
        parts.append("Bonus/paylasim interneti sinirsizdir.")
    elif bonus:
        parts.append(f"Ayrica {bonus:g} GB bonus/paylasim interneti dahildir.")
    parts.append(f"Avantajlar: {benefits}.")
    if rec.get("conditions"):
        parts.append(f"Istisnalar ve kosullar: {rec['conditions']}")
    if rec.get("campaign"):
        parts.append(f"Kampanya: {rec['campaign']}")
    parts.append(
        f"Veri kalitesi: {rec.get('data_quality_status', 'complete')}; "
        f"eksik alanlar: {', '.join(rec.get('missing_fields') or []) or 'yok'}."
    )
    parts.append(f"Kaynak: {rec.get('source_url', 'belirtilmemis')}.")
    return " ".join(p for p in parts if p).strip()


def _require_enum(rec: dict, field: str, allowed) -> str:
    """Fail the ingest loudly rather than let a package silently fall into the wrong bucket."""
    value = rec.get(field)
    if value not in allowed:
        raise ValueError(
            f"{rec.get('operator', '?')} / {rec.get('name', '?')}: "
            f"gecersiz {field}={value!r}, beklenen: {sorted(allowed)}"
        )
    return value


def normalize_record(rec: dict, *, require_mobile_voice: bool = False) -> dict:
    """Coerce a raw record to typed fields + attach doc_text. Idempotent."""
    out = dict(rec)
    # line_type / customer_type come from the file header (see ingest/load.py).
    out["line_type"] = _require_enum(rec, "line_type", LINE_TYPE_LABELS)
    out["customer_type"] = _require_enum(rec, "customer_type", _CUSTOMER_TYPE_PHRASES)
    if out["line_type"] == "prepaid":
        _require_enum(rec, "price_period", PRICE_PERIODS)
        _require_enum(rec, "validity_unit", VALIDITY_UNITS)
    out["price_try"] = parse_price(rec.get("price_try"))
    out["internet_gb"] = parse_gb(rec.get("internet_gb"))
    out["social_media_gb"] = parse_gb(rec.get("social_media_gb"))
    out["bonus_internet_gb"] = parse_gb(rec.get("bonus_internet_gb"))
    out["total_internet_gb"] = parse_gb(rec.get("total_internet_gb"))
    out["voice_minutes"] = parse_amount(rec.get("voice_minutes"))
    out["sms_count"] = parse_amount(rec.get("sms_count"))
    out["no_commitment_price_try"] = parse_price(rec.get("no_commitment_price_try"))
    out["price_lock_months"] = (
        int(rec["price_lock_months"])
        if rec.get("price_lock_months") is not None
        else None
    )
    days = derive_validity_days(rec)
    if days is not None:
        out["validity_days"] = days
    out.update(_taxonomy(out))
    _require_enum(out, "audience_segment", AUDIENCE_SEGMENTS)
    _require_enum(out, "product_scope", PRODUCT_SCOPES)
    if require_mobile_voice and out["product_scope"] != "mobile_voice":
        raise ValueError(
            f"{out.get('operator', '?')} / {out.get('name', '?')}: "
            f"RAG katalogu yalnizca mobile_voice kabul eder, product_scope={out['product_scope']}"
        )
    out["commitment_status"] = (
        "no_commitment" if out["line_type"] == "prepaid" else _commitment_status(out)
    )
    if out["line_type"] == "prepaid":
        out["commitment_months"] = None
        out["no_commitment_price_try"] = None
    out["eligibility_tags"], out["benefit_tags"] = _tags(out)
    out["canonical_id"] = rec.get("canonical_id") or _canonical_id(out)
    out["data_quality_status"], out["missing_fields"] = _quality(out)
    out["doc_text"] = build_doc_text(out)
    return out
