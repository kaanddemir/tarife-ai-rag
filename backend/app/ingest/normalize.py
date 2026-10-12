"""Turn raw package records (from the curated data/*.json per-operator files)
into normalized rows: typed fields + a natural-language `doc_text` used for
embedding and retrieval grounding.
"""
import re
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
    "new": "yalnizca operatore yeni gelen musterilere ozel",
    "existing": "hali hazirda hatti olan mevcut musterilere ozel",
    "any": "hem yeni hem mevcut musteriler yararlanabilir",
}

# Sentinel for unlimited numeric fields (voice_minutes, sms_count and the GB fields).
UNLIMITED = -1

AUDIENCE_SEGMENTS = {"general", "youth", "child", "family", "public_special"}
PRODUCT_SCOPES = {"mobile_voice", "wearable", "data_only", "iot"}
COMMITMENT_STATUSES = {"committed", "no_commitment", "unknown"}
# The period price_try pays for: "month" = one postpaid billing month; "<N>_day" = one
# prepaid package whose allowances last N days (always equal to validity_days).
PRICE_PERIODS = {"month", "28_day", "60_day", "90_day", "180_day", "360_day"}
# Whether the plan charges again by itself. "unknown" = the official source does not say;
# never inferred from "monthly" or a 28-day validity.
RENEWAL_TYPES = {"recurring", "one_time", "unknown"}
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

# ---- Structured benefits ----
# Each record carries `benefits` (a list) and `benefits_status`:
#   verified       -- the official page was read; `benefits` lists what it states (>= 1 item)
#   verified_none  -- the official page was read and lists no extra benefit (benefits == [])
#   unverified     -- the terms could not be read (e.g. rendered by JavaScript); nothing is claimed
# A benefit is what the page says, never more: a free trial is not "included", data for an
# app is not a subscription to it, and a discount is not free.
BENEFITS_STATUSES = {"verified", "verified_none", "unverified"}
# Where bonus_internet_gb can be used. App-only data is listed in `benefits` as well
# (app_data); the scope stops it being read as general internet.
BONUS_INTERNET_SCOPES = {
    "general": "tum internet kullanimlarinda gecerli ek/hediye internet",
    "tethering_only": "yalnizca internet paylasiminda (hotspot) kullanilabilen ek internet",
    "app_specific": "yalnizca belirli uygulamalarda gecerli ek internet",
}
BENEFIT_CATEGORIES = {
    "subscription",  # membership of a service (Netflix, Tivibu Go, ChatGPT Go ...)
    "app_data",      # data usable only in named apps; never the app's subscription itself
    "bonus_data",    # extra general data (campaign gift on top of the tariff)
    "voucher",       # one-off gift card / coupon with a stated value
    "discount",      # a price reduction at a partner or on a service
    "roaming",       # use of the domestic allowance abroad
    "service",       # an operator service (safety app, delivery, privileges programme ...)
}
BENEFIT_OFFERS = {
    "included",           # part of the tariff for as long as it runs
    "free_trial",         # free for duration_months, then paid or ends (after_trial)
    "discounted",         # cheaper, not free
    "requires_purchase",  # app_data that only works after the service itself is bought
}
BENEFIT_AFTER_TRIAL = {"paid", "ends", "unknown"}
BENEFIT_ELIGIBILITY = {"new_line_only"}
_STREAMING_SERVICES = ("netflix", "tivibu", "muud", "youtube premium", "dijital platform", "spotify", "amazon prime")
_SAFETY_SERVICES = ("safe family", "find my kids", "durma")
_PRIVILEGE_SERVICES = ("prime ayrıcalık", "platinum", "red ayrıcalık")
_MESSAGING_APPS = {"whatsapp", "bip", "yaay", "facebook messenger"}

# Rough day equivalents (used to derive validity_days when it's not given).
_UNIT_DAYS = {"day": 1, "week": 7, "month": 30}
_UNIT_LABELS = {"day": "gun", "week": "hafta", "month": "ay"}

# price_period -> label describing which period the price is for (doc_text).
# For multi-month packages price_try is the TOTAL charge; calling it "monthly fee" is misleading.
_PERIOD_PRICE_LABELS = {
    "month": "Aylik ucret",
    "28_day": "28 gunluk paket ucreti",
    "60_day": "60 gunluk paket ucreti (toplam)",
    "90_day": "90 gunluk paket ucreti (toplam)",
    "180_day": "180 gunluk paket ucreti (toplam)",
    "360_day": "360 gunluk paket ucreti (toplam)",
}


def renewal_phrase(rec: dict) -> Optional[str]:
    """How the plan charges again, as stated by the source (None when renewal_type is absent)."""
    kind = rec.get("renewal_type")
    if kind == "recurring":
        if rec.get("line_type") == "postpaid":
            return "Faturali tarife: ucret her fatura doneminde (aylik) yeniden alinir."
        return "Iptal edilmezse ve bakiye yeterliyse sure sonunda otomatik yenilenir."
    if kind == "one_time":
        return "Tek seferlik pakettir; kendiliginden yenilenmez, tekrar kullanmak icin yeniden alinmasi gerekir."
    if kind == "unknown":
        return "Paketin otomatik yenilenip yenilenmedigi kaynakta belirtilmemis."
    return None

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


def _is_unlimited(value) -> bool:
    return value is not None and float(value) == UNLIMITED


def general_internet_gb(internet, bonus=None, bonus_scope=None) -> Optional[float]:
    """Internet usable for anything: the general allowance plus a bonus only when its
    verified scope is "general". Social-media, hotspot-only and app-only data never count.
    UNLIMITED only when the general allowance (or a general bonus) is unlimited; None when
    the general allowance is unknown."""
    if internet is None:
        return None
    if _is_unlimited(internet):
        return float(UNLIMITED)
    if bonus_scope == "general" and bonus is not None:
        if _is_unlimited(bonus):
            return float(UNLIMITED)
        return float(internet) + float(bonus)
    return float(internet)


_BONUS_PART_LABELS = {
    "general": "ek genel",
    "tethering_only": "yalnizca paylasim (hotspot)",
    "app_specific": "uygulamaya ozel",
}


def internet_phrase(internet, social=None, bonus=None, bonus_scope=None) -> Optional[str]:
    """Describe a package's internet without overstating it. The headline is the
    general-use amount (general_internet_gb); restricted quotas are listed as parts and
    never summed into it. Only unlimited *general* internet reads as unlimited.
    Returns None when the general allowance is unknown."""
    general = general_internet_gb(internet, bonus, bonus_scope)
    if general is None:
        return None
    if _is_unlimited(general):
        return "sinirsiz genel internet"
    parts = [f"{float(internet):g} GB genel"]
    restricted_unlimited = False
    if social is not None and float(social) != 0:
        restricted_unlimited |= _is_unlimited(social)
        parts.append("sinirsiz sosyal medya" if _is_unlimited(social) else f"{float(social):g} GB sosyal medya")
    if bonus is not None and float(bonus) != 0:
        label = _BONUS_PART_LABELS.get(bonus_scope, "bonus")
        restricted_unlimited |= _is_unlimited(bonus)
        parts.append(f"sinirsiz {label}" if _is_unlimited(bonus) else f"{float(bonus):g} GB {label}")
    if len(parts) == 1:
        return f"{general:g} GB internet"
    note = " (genel internet sinirsiz degil)" if restricted_unlimited else ""
    if general != float(internet):  # a general-scope bonus adds to what can be used anywhere
        return f"toplam {general:g} GB genel kullanim internet ({' + '.join(parts)}){note}"
    return f"{' + '.join(parts)} internet{note}"


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


# Activation channels a new customer can use. Only source text that grants or
# restricts eligibility counts; a fee disclosure ("yeni hat tesisinde telsiz
# ruhsatname ucreti dahil degildir", "yeni hat alimlarinda SIM kart ucreti tahsil
# edilir") says nothing about who may buy the plan, so such sentences are ignored.
# No channel found -> unknown, never assumed.
ACTIVATION_CHANNELS = ("new_line", "number_porting")
_FEE_SENTENCE_RE = re.compile(r"[uü]cret|tahsil|dahil de[gğ]il", re.I)
_NEW_LINE_RE = re.compile(r"yeni hat")
_PORTING_RE = re.compile(r"numara\w*.{0,40}ta[sş][ıi]")


def activation_channels(rec: dict) -> list[str]:
    text = " ".join(str(rec.get(k) or "") for k in ("conditions", "campaign")).lower()
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", text) if not _FEE_SENTENCE_RE.search(s)]
    evidence = " ".join([str(rec.get("name") or "").lower(), *sentences])
    channels = []
    if _NEW_LINE_RE.search(evidence):
        channels.append("new_line")
    if _PORTING_RE.search(evidence):
        channels.append("number_porting")
    return channels


def record_channels(rec: dict) -> Optional[list[str]]:
    """The record's activation channels. An explicit `activation_channels` (list, or None
    for "the source does not say") wins; older records fall back to the text rule."""
    if "activation_channels" in rec:
        return rec["activation_channels"]
    return activation_channels(rec) or None


def channel_phrase(tags) -> str:
    """Turkish rendering of the activation channels for doc_text and the prompt."""
    tags = set(tags or [])
    if {"new_line", "number_porting"} <= tags:
        return "yeni hat veya numara tasima ile alinabilir"
    if "new_line" in tags:
        return "yalnizca yeni hat ile alinabilir (numara tasima kaynakta belirtilmemis)"
    if "number_porting" in tags:
        return "numara tasima ile alinabilir (yeni hat kaynakta belirtilmemis)"
    return "yeni hat / numara tasima kanali kaynakta belirtilmemis"


def _fmt_num(value) -> str:
    return f"{float(value):g}".replace(".", ",")


def benefit_phrase(b: dict) -> str:
    """One benefit in plain Turkish, qualifiers first-class: a trial says it is a trial,
    app data says it is app data (and whether the app must be bought)."""
    service = b.get("service") or "belirtilmemis hizmet"
    category, offer = b.get("category"), b.get("offer")
    months = b.get("duration_months")
    apps = ", ".join(b.get("apps") or []) or service
    amount = b.get("amount_gb")
    if category == "app_data":
        size = "sinirsiz" if amount == UNLIMITED else (f"{_fmt_num(amount)} GB" if amount is not None else "miktari belirtilmemis")
        text = f"{apps} uygulamalarinda gecerli {size} internet (uygulama aboneligi degil)"
        if offer == "requires_purchase":
            text += "; servis ayrica satin alinmadan kullanilamaz"
    elif category == "subscription":
        text = f"{service} uyeligi"
        if offer == "free_trial":
            text += f": ilk {months} ay ucretsiz deneme" if months else ": ucretsiz deneme (sure belirtilmemis)"
            after = b.get("after_trial")
            if after == "paid":
                price = b.get("after_trial_price_try")
                text += f", sonrasinda ucretli ({_fmt_num(price)} TL)" if price is not None else ", sonrasinda ucretli"
            elif after == "ends":
                text += ", sonrasinda sona erer"
            else:
                text += ", sonrasi kaynakta belirtilmemis"
        elif offer == "discounted":
            text += " (indirimli, ucretsiz degil)"
        elif months:
            text += f" ({months} ay)"
    elif category == "bonus_data":
        size = f"{_fmt_num(amount)} GB" if amount is not None else "miktari belirtilmemis"
        text = f"{service}: ek {size} genel internet"
        if months:
            text += f", {months} ay boyunca"
    elif category == "voucher":
        value = b.get("value_try")
        text = f"{service} hediye ceki" + (f" ({_fmt_num(value)} TL, tek seferlik)" if value is not None else "")
    elif category == "discount":
        text = f"{service} (indirim, ucretsiz degil)"
    elif category == "roaming":
        text = f"{service} (yurt disi kullanim)"
    else:
        text = service
        if offer == "free_trial" and months:
            text += f": {months} ay ucretsiz"
    if b.get("eligibility") == "new_line_only":
        text += " [yalnizca yeni hat ile gelenlere]"
    if b.get("activation"):
        text += f" [aktivasyon: {b['activation']}]"
    return text


def benefits_sentence(rec: dict) -> Optional[str]:
    status = rec.get("benefits_status")
    if status is None:
        return None
    if status == "unverified":
        return "Ek faydalar: kaynakta dogrulanamadi; ek fayda iddiasinda bulunulmaz."
    if status == "verified_none":
        return "Ek faydalar: resmi sayfada ek fayda belirtilmemis."
    return "Ek faydalar (resmi sayfaya gore): " + "; ".join(benefit_phrase(b) for b in rec.get("benefits") or []) + "."


def _structured_benefit_tags(rec: dict) -> set[str]:
    """Benefit tags from the verified `benefits` list only -- never from free text."""
    tags: set[str] = set()
    for b in rec.get("benefits") or []:
        service = str(b.get("service") or "").lower()
        category, offer = b.get("category"), b.get("offer")
        if category == "subscription" and offer in ("included", "free_trial"):
            if any(s in service for s in _STREAMING_SERVICES):
                tags.add("streaming_perks")
            if "netflix" in service:
                tags.add("netflix")
            if offer == "free_trial":
                tags.add("free_trial_subscription")
        elif category == "app_data":
            # Unlimited messaging (WhatsApp/BiP/YaaY) is not extra data for other apps.
            messaging = {a.lower() for a in b.get("apps") or []} <= _MESSAGING_APPS
            tags.add("unlimited_messaging" if messaging else "app_specific_data")
        elif category == "bonus_data":
            tags.add("bonus_data")
        elif category == "voucher":
            tags.add("gift_voucher")
        elif category == "discount":
            tags.add("digital_perks")
        elif category == "roaming":
            tags.add("international_usage")
        if category in ("service", "subscription") and any(s in service for s in _SAFETY_SERVICES):
            tags.add("safety_services")
        if category == "service" and any(s in service for s in _PRIVILEGE_SERVICES):
            tags.add("premium_perks")
        if category == "service" and "kurye" in service:
            tags.add("free_delivery")
    return tags


# Tags that a structured `benefits` list decides on its own. Text matching made
# "Netflix kullanimi sinirsizdir" (zero-rated data) look like a Netflix subscription.
_STRUCTURED_BENEFIT_TAGS = {
    "unlimited_messaging", "streaming_perks", "netflix", "free_trial_subscription", "app_specific_data", "gift_voucher",
    "digital_perks", "international_usage", "safety_services", "premium_perks",
}


def eligibility_tags(rec: dict) -> list[str]:
    """Purchase restrictions that no dedicated field expresses: province, dealer/partner/
    online channel, activation window, identity, parental link, profession. Activation
    channel (activation_channels), age (age_min/age_max) and audience (audience_segment)
    are never repeated here."""
    text = " ".join(str(rec.get(k) or "") for k in ("name", "conditions", "campaign")).lower()
    text = " ".join(str(rec.get(k) or "") for k in ("name", "conditions", "campaign")).lower()
    eligibility = set(rec.get("eligibility_tags") or [])
    # Channels live in activation_channels, ages in age_min/age_max: not repeated here.
    eligibility -= {"new_line", "number_porting", "age_restricted"}
    if "online" in text:
        eligibility.add("online_only")
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
    if "ebeveyn" in text or "çocuk kullanıcı tanımlaması" in text or "cocuk kullanici tanimlamasi" in text:
        eligibility.add("parental_relationship_required")
    family = rec.get("operator_family") or rec.get("package_group")
    if family == "new_parliamentarian":
        eligibility.add("parliamentarian")
    elif family == "new_internal_affairs":
        eligibility.add("internal_affairs_personnel")
    return sorted(eligibility)


def _field_benefit_tags(rec: dict) -> set[str]:
    """Tags that follow from structured allowance/price fields."""
    tags: set[str] = set()
    if rec.get("internet_gb") == UNLIMITED:
        tags.add("unlimited_data")
    if rec.get("voice_minutes") == UNLIMITED:
        tags.add("unlimited_calls")
    if rec.get("sms_count") == UNLIMITED:
        tags.add("unlimited_sms")
    if rec.get("social_media_gb") == UNLIMITED:
        tags.add("unlimited_social")
    if rec.get("bonus_internet_gb") not in (None, 0):
        tags.add("bonus_data")
    if rec.get("bonus_internet_scope") == "tethering_only":
        tags.add("data_sharing")
    if rec.get("price_lock_months"):
        tags.add("price_guarantee")
    if (rec.get("validity_days") or 0) >= 90:
        tags.add("long_validity")
    return tags


def benefit_tags(rec: dict) -> list[str]:
    """Benefit classification, derived on demand and never stored, so it cannot drift.

    Catalog records (with benefits_status) use only the structured `benefits` list and
    structured fields; marketing text never adds a tag. Records without structured
    benefits (older test fixtures) keep the text rules below."""
    if rec.get("benefits_status") in BENEFITS_STATUSES:
        tags = _structured_benefit_tags(rec) | _field_benefit_tags(rec)
        if any(b.get("category") == "bonus_data" for b in rec.get("benefits") or []):
            tags.add("bonus_data")
        return sorted(tags)
    return _text_benefit_tags(rec)


def _text_benefit_tags(rec: dict) -> list[str]:
    text = " ".join(str(rec.get(k) or "") for k in ("name", "conditions", "campaign")).lower()
    family = rec.get("operator_family") or rec.get("package_group")
    benefits: set[str] = set()
    if "online" in text:
        benefits.add("online_only")
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
    # Only the general allowance counts: an unlimited social/bonus quota is not
    # unlimited general internet.
    if rec.get("internet_gb") == UNLIMITED:
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
    return sorted(benefits)


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
    gb_phrase = internet_phrase(
        rec.get("internet_gb"),
        rec.get("social_media_gb"),
        rec.get("bonus_internet_gb"),
        rec.get("bonus_internet_scope"),
    ) or "belirtilmemis internet"
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
        _BENEFIT_LABELS.get(tag, tag) for tag in benefit_tags(rec)
    ) or "ozel avantaj etiketi yok"

    parts = [
        f"Kimlik: {op} {rec.get('name', '')} {line_label} paketi; "
        f"operator ailesi {rec.get('operator_family', 'standard')}; "
        f"urun kapsami {rec.get('product_scope', 'mobile_voice')}.",
        f"Uygunluk: {_CUSTOMER_TYPE_PHRASES[rec['customer_type']]}; "
        f"{channel_phrase(rec.get('activation_channels'))}; {audience_phrase}; "
        f"etiketler: {eligibility}.",
        f"Icerik: {gb_phrase}, {_amount_phrase(rec.get('voice_minutes'), 'dakika')}, "
        f"{_amount_phrase(rec.get('sms_count'), 'SMS')}.",
        f"Fiyat: {price_sentence}.",
    ]
    parts.append(renewal_phrase(rec))
    parts.extend(_commitment_phrases(rec))
    if rec.get("promo_months") and rec.get("price_after_promo_try") is not None:
        parts.append(
            f"Bu fiyat ilk {rec['promo_months']} ay icindir; sonrasinda {rec['price_after_promo_try']:g} TL'dir."
        )

    social = rec.get("social_media_gb")
    apps = ", ".join(rec.get("social_media_apps") or []) or "TikTok, Instagram, YouTube vb."
    if social == UNLIMITED:
        parts.append(f"Sosyal medya ({apps}) kullanimi sinirsizdir; bu genel internet degildir.")
    elif social:
        parts.append(f"Bunun {social:g} GB'i sosyal medya ({apps}) kotasidir.")
    bonus = rec.get("bonus_internet_gb")
    scope = BONUS_INTERNET_SCOPES.get(rec.get("bonus_internet_scope"), "bonus internet (kullanim alani belirtilmemis)")
    if bonus == UNLIMITED:
        parts.append(f"Sinirsiz {scope} dahildir.")
    elif bonus:
        parts.append(f"Ayrica {bonus:g} GB {scope} dahildir.")
    parts.append(f"Avantajlar: {benefits}.")
    if (sentence := benefits_sentence(rec)):
        parts.append(sentence)
    if rec.get("conditions"):
        parts.append(f"Istisnalar ve kosullar: {rec['conditions']}")
    if rec.get("campaign"):
        parts.append(f"Kampanya: {rec['campaign']}")
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
    out["voice_minutes"] = parse_amount(rec.get("voice_minutes"))
    out["sms_count"] = parse_amount(rec.get("sms_count"))
    out["no_commitment_price_try"] = parse_price(rec.get("no_commitment_price_try"))
    out["price_after_promo_try"] = parse_price(rec.get("price_after_promo_try"))
    out["activation_channels"] = record_channels(rec)
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
    out["eligibility_tags"] = eligibility_tags(out)
    out["doc_text"] = build_doc_text(out)
    return out
