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

_UNLIMITED_WORDS = ("sinirsiz", "sınırsız", "unlimited")

# Kaba gun karsiliklari (validity_days verilmemisse turetmek icin).
_UNIT_DAYS = {"day": 1, "week": 7, "month": 30}
_UNIT_LABELS = {"day": "gun", "week": "hafta", "month": "ay"}

# package_group -> kisa uygunluk / hedef kitle ipucu (doc_text zenginlestirme).
_GROUP_HINTS = {
    "gnc": "GNC / 25 yas alti genc musterilere ozel",
    "freezone": "FreeZone / 26 yas alti genc musterilere ozel",
    "welcome": "yeni musteri / numara tasima ile gelenlere ozel",
    "transfer": "numara tasima veya yeni hat ile gelenlere ozel",
    "61cell": "561 ile baslayan 61CELL hatlarina ozel",
}


def _num(text: str, pattern: str) -> Optional[float]:
    m = re.search(pattern, text, flags=re.IGNORECASE)
    return float(m.group(1).replace(",", ".")) if m else None


def parse_amount(value) -> Optional[int]:
    """Parse a minutes/SMS value that may be a number or 'sinirsiz'. -1 = unlimited."""
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
    return _num(str(value), r"(\d+(?:[.,]\d+)?)\s*gb")


def parse_price(value) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return _num(str(value), r"(\d+(?:[.,]\d+)?)\s*(?:tl|₺)?")


def _amount_phrase(value: Optional[int], unit: str) -> str:
    if value is None:
        return f"belirtilmemis {unit}"
    if value == -1:
        return f"sinirsiz {unit}"
    return f"{value} {unit}"


def derive_validity_days(rec: dict) -> Optional[int]:
    """validity_days verilmisse onu, degilse validity_value + validity_unit'ten turet."""
    if rec.get("validity_days") is not None:
        return int(rec["validity_days"])
    value, unit = rec.get("validity_value"), rec.get("validity_unit")
    if value is not None and unit in _UNIT_DAYS:
        return int(value) * _UNIT_DAYS[unit]
    return None


def _validity_phrase(rec: dict) -> str:
    value, unit = rec.get("validity_value"), rec.get("validity_unit")
    days = derive_validity_days(rec)
    if value is not None and unit in _UNIT_LABELS:
        label = _UNIT_LABELS[unit]
        if days is not None and unit != "day":
            return f"{value} {label} ({days} gun) gecerli"
        return f"{value} {label} gecerli"
    if days is not None:
        return f"{days} gun gecerli"
    return "gecerlilik belirtilmemis"


def build_doc_text(rec: dict) -> str:
    """Natural-language description of a package for embedding + grounding."""
    op = OPERATOR_LABELS.get(rec.get("operator", ""), rec.get("operator", ""))
    gb = rec.get("total_internet_gb") or rec.get("internet_gb")
    gb_phrase = f"{gb:g} GB internet" if gb is not None else "belirtilmemis internet"
    price = rec.get("price_try")
    price_phrase = f"{price:g} TL" if price is not None else "fiyat belirtilmemis"

    parts = [
        f"{op} {rec.get('name', '')} faturasiz (prepaid) paketi.",
        f"{gb_phrase}, {_amount_phrase(rec.get('voice_minutes'), 'dakika')}, "
        f"{_amount_phrase(rec.get('sms_count'), 'SMS')}.",
        f"Aylik ucret {price_phrase}, {_validity_phrase(rec)}.",
    ]
    social = rec.get("social_media_gb")
    if social:
        parts.append(f"Bunun {social:g} GB'i sosyal medya (TikTok, Instagram, YouTube vb.) kotasidir.")
    bonus = rec.get("bonus_internet_gb")
    if bonus:
        parts.append(f"Ayrica {bonus:g} GB bonus/paylasim interneti dahildir.")
    hint = _GROUP_HINTS.get(rec.get("package_group", ""))
    if hint:
        parts.append(f"Hedef kitle: {hint}.")
    if rec.get("conditions"):
        parts.append(f"Kosullar: {rec['conditions']}")
    if rec.get("campaign"):
        parts.append(f"Kampanya: {rec['campaign']}")
    for item in rec.get("faq") or []:
        parts.append(f"SSS: {item.get('q', '')} {item.get('a', '')}")
    return " ".join(p for p in parts if p).strip()


def normalize_record(rec: dict) -> dict:
    """Coerce a raw record to typed fields + attach doc_text. Idempotent."""
    out = dict(rec)
    out["line_type"] = "prepaid"  # v1 scope
    out["price_try"] = parse_price(rec.get("price_try"))
    out["internet_gb"] = parse_gb(rec.get("internet_gb"))
    out["social_media_gb"] = parse_gb(rec.get("social_media_gb"))
    out["bonus_internet_gb"] = parse_gb(rec.get("bonus_internet_gb"))
    out["total_internet_gb"] = parse_gb(rec.get("total_internet_gb"))
    out["voice_minutes"] = parse_amount(rec.get("voice_minutes"))
    out["sms_count"] = parse_amount(rec.get("sms_count"))
    days = derive_validity_days(rec)
    if days is not None:
        out["validity_days"] = days
    out["doc_text"] = build_doc_text(out)
    return out
