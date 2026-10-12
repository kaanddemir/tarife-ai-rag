"""Canonical answer context: the only facts the LLM may use, built
deterministically from the catalog row and retrieval result.

Each source is rendered as labelled sections so the model can tell apart
  RESMI      -- what the operator's official page states (catalog fields),
  HESAP      -- what TarifeAI calculated (30-day comparison price), never billed,
  KOSULLU    -- benefits/conditions that only apply under a stated condition,
  BILINMIYOR -- fields the source does not state (never to be guessed),
  ESLESME    -- how the row fares against the user's requirements (from rag.search).

Money and amounts are formatted here once, so the LLM copies numbers instead of
computing them; rag.answer_check later accepts only these numbers.
"""
from __future__ import annotations

from typing import Optional

from app.ingest.normalize import (
    CUSTOMER_TYPE_LABELS,
    LINE_TYPE_LABELS,
    UNLIMITED,
    benefit_phrase,
    channel_phrase,
    general_internet_gb,
    internet_phrase,
)
from app.models import Package

OPERATOR_TR = {"turkcell": "Turkcell", "vodafone": "Vodafone", "turk_telekom": "Türk Telekom"}

_PERIOD_TR = {"month": "ay"}


def fmt_tl(value) -> str:
    """Turkish money: thousands with '.', decimals with ',' only when present."""
    v = float(value)
    if abs(v - round(v)) < 0.005:
        return f"{int(round(v)):,}".replace(",", ".") + " TL"
    whole, frac = f"{v:,.2f}".split(".")
    return f"{whole.replace(',', '.')},{frac} TL"


def period_label(p: Package) -> str:
    if p.price_period == "month" or p.line_type == "postpaid":
        return "aylık"
    return f"{p.validity_days} günlük" if p.validity_days else "süresi belirtilmemiş"


def price_30d(p: Package) -> Optional[float]:
    """30-day comparison value of a prepaid package; None when it equals the price."""
    value = p.price_30d_equivalent  # one rule, shared with the API (models.Package)
    return None if value is None else int(value)


def _official(p: Package) -> list[str]:
    lines = []
    if p.price_try is None:
        lines.append("Fiyat: kaynakta belirtilmemiş.")
    elif p.line_type == "postpaid":
        price = f"Fiyat: {fmt_tl(p.price_try)} / ay"
        if p.commitment_status == "committed" and p.commitment_months:
            price += f" ({p.commitment_months} ay taahhütlü fiyat)"
        elif p.commitment_status == "no_commitment":
            price += " (taahhütsüz)"
        lines.append(price + ".")
        if p.commitment_status == "committed":
            lines.append(f"Taahhütsüz fiyat: {fmt_tl(p.no_commitment_price_try)} / ay." if p.no_commitment_price_try
                         is not None else "Taahhütsüz fiyat: kaynakta belirtilmemiş.")
        if p.promo_months and p.price_after_promo_try is not None:
            lines.append(f"Tanıtım: ilk {p.promo_months} ay {fmt_tl(p.price_try)}, sonrasında "
                         f"{fmt_tl(p.price_after_promo_try)} / ay.")
        if p.price_lock_months:
            lines.append(f"Fiyat sabitleme: {p.price_lock_months} fatura dönemi.")
    else:
        lines.append(f"Fiyat: {fmt_tl(p.price_try)}, tek ödemeyle {p.validity_days} gün geçerli "
                     f"(faturasız paket; aylık fatura değildir).")
    internet = internet_phrase(p.internet_gb, p.social_media_gb, p.bonus_internet_gb, p.bonus_internet_scope)
    lines.append(f"İnternet: {internet}." if internet else "İnternet: kaynakta belirtilmemiş.")
    if p.social_media_gb is not None and p.social_media_gb != 0:
        apps = ", ".join(p.social_media_apps or [])
        lines.append("Sosyal medya kotasının uygulamaları: " + (apps if apps else "kaynakta belirtilmemiş") + ".")
    minutes = "sınırsız" if p.voice_minutes == UNLIMITED else (f"{p.voice_minutes} dakika" if p.voice_minutes
                                                                else "belirtilmemiş")
    sms = "sınırsız" if p.sms_count == UNLIMITED else (f"{p.sms_count} SMS" if p.sms_count else "belirtilmemiş")
    lines.append(f"Konuşma: {minutes}; SMS: {sms}.")
    if p.line_type == "prepaid":
        renewal = {"recurring": "süre bitince otomatik yenilenir (iptal edilmezse, bakiye yeterliyse)",
                   "one_time": "tek seferlik; otomatik yenilenmez"}.get(p.renewal_type)
        if renewal:
            lines.append(f"Yenileme: {renewal}.")
    lines.append(f"Kanal: {channel_phrase(p.activation_channels)}.")
    return lines


def _conditional(p: Package) -> list[str]:
    out = []
    if p.audience_segment and p.audience_segment != "general":
        who = {"youth": "gençlere özel", "child": "çocuklara özel (ilk hat)", "family": "aile tarifesi",
               "public_special": "kurum/meslek grubuna özel"}.get(p.audience_segment, p.audience_segment)
        ages = (f", {p.age_min}-{p.age_max} yaş" if p.age_min is not None and p.age_max is not None
                else (f", en fazla {p.age_max} yaş" if p.age_max is not None else ""))
        out.append(f"Hedef kitle: {who}{ages}.")
    tag_tr = {
        "parliamentarian": "yalnızca milletvekillerine", "partner_channel_only": "yalnızca anlaşmalı satış kanalında",
        "dealer_only": "yalnızca bayiden", "province_restricted": "yalnızca belirli illerde",
        "identity_required": "kimlik doğrulaması gerekir", "activation_window": "belirli bir başvuru döneminde",
        "online_only": "yalnızca online başvuru", "parental_relationship_required": "ebeveyn adına açılır",
    }
    for t in p.eligibility_tags or []:
        out.append(f"Koşul: {tag_tr.get(t, t)}.")
    for b in p.benefits or []:
        if b.get("eligibility") == "new_line_only":
            out.append(f"'{b.get('service')}' yalnızca yeni hat alanlara.")
    return out


def _unknown(p: Package) -> list[str]:
    out = []
    if p.line_type == "prepaid" and p.renewal_type == "unknown":
        out.append("Otomatik yenilenip yenilenmediği kaynakta belirtilmemiş.")
    if p.activation_channels is None:
        out.append("Yeni hat / numara taşıma kanalı kaynakta belirtilmemiş.")
    if p.line_type == "postpaid" and not p.price_lock_months:
        out.append("Fiyat sabitleme süresi kaynakta belirtilmemiş.")
    for b in p.benefits or []:
        if b.get("offer") == "free_trial" and b.get("after_trial") in (None, "unknown"):
            out.append(f"'{b.get('service')}' denemesinden sonra ne olacağı (ücret) kaynakta belirtilmemiş.")
    if p.social_media_gb and not p.social_media_apps:
        out.append("Sosyal medya kotasının hangi uygulamaları kapsadığı kaynakta belirtilmemiş.")
    return out


def _benefits(p: Package) -> list[str]:
    if p.benefits_status == "unverified":
        return ["Avantajlar: resmi sayfada doğrulanamadı; avantaj iddiasında bulunma."]
    if not p.benefits:
        return ["Avantajlar: resmi sayfada ek avantaj yok."]
    out = []
    for b in p.benefits:
        line = benefit_phrase(b)
        if b.get("activation"):
            line += f"; nasıl alınır: {b['activation']}"
        out.append("- " + line)
    return ["Avantajlar (resmi sayfa):", *out]


def _checks(p: Package) -> list[str]:
    checks = getattr(p, "retrieval_checks", None) or []
    out = []
    for label, result in checks:
        mark = {True: "karşılıyor", False: "KARŞILAMIYOR", None: "DOĞRULANAMADI (kaynakta yok)"}[result]
        out.append(f"{label}: {mark}")
    return out


def fact_block(sid: str, p: Package) -> str:
    """One source, sectioned. Read top to bottom by the model."""
    op = OPERATOR_TR.get(p.operator, p.operator)
    who = CUSTOMER_TYPE_LABELS.get(p.customer_type, p.customer_type)
    head = f"[{sid}] {op} — {p.name} ({LINE_TYPE_LABELS.get(p.line_type, p.line_type)}, {who})"
    status = getattr(p, "retrieval_status", None)
    if status == "compromise":
        head += " — UZLAŞMA ALTERNATİFİ (kullanıcının bir şartını karşılamıyor)"
    elif status == "unverified":
        head += " — DOĞRULANAMAYAN ADAY"
    parts = [head]
    verified = str(p.verified_at)[:10] if p.verified_at else "belirtilmemiş"
    parts.append(f"  RESMİ (operatör sayfası, doğrulama {verified}):")
    parts += [f"    {line}" for line in _official(p) + _benefits(p)]
    calc = price_30d(p)
    if calc is not None:
        parts.append(f"  HESAP (TarifeAI, yalnızca kıyas için; operatör böyle faturalamaz): 30 günlük karşılığı "
                     f"yaklaşık {fmt_tl(calc)}.")
    cond = _conditional(p)
    if cond:
        parts.append("  KOŞULLU (herkes için geçerli sanma):")
        parts += [f"    {line}" for line in cond]
    unknown = _unknown(p)
    if unknown:
        parts.append("  BİLİNMİYOR (tahmin etme, 'kaynakta belirtilmemiş' de):")
        parts += [f"    {line}" for line in unknown]
    checks = _checks(p)
    if checks:
        parts.append("  ESLEŞME (kullanıcının şartları): " + "; ".join(checks) + ".")
    parts.append(f"  Kaynak: {p.source_url}")
    return "\n".join(parts)


def general_gb_value(p: Package) -> Optional[float]:
    g = general_internet_gb(p.internet_gb, p.bonus_internet_gb, p.bonus_internet_scope)
    return None if g is None else float(g)


def comparison_block(packages: list[Package], sids: list[str]) -> Optional[str]:
    """Deterministic side-by-side facts for 2+ sources on one comparable basis."""
    if len(packages) < 2:
        return None
    rows = []
    for sid, p in zip(sids, packages):
        if p.price_try is None:
            continue
        basis = float(p.price_try) if p.line_type == "postpaid" else (
            float(p.price_try) * 30 / p.validity_days if p.validity_days else None)
        if basis is None:
            continue
        gb = general_gb_value(p)
        rows.append((sid, p, round(basis), gb))
    if len(rows) < 2:
        return None
    lines = ["<KIYAS>", "(TarifeAI hesabı. Aylık fiyat ile faturasız paketin 30 günlük karşılığı aynı tabanda; "
             "taahhütlü fiyatlar taahhütlü, taahhütsüz istenirse taahhütsüz fiyat ayrı belirtilir.)"]
    for sid, p, basis, gb in rows:
        gb_txt = "sınırsız genel" if gb == UNLIMITED else (f"{gb:g} GB genel" if gb is not None else "belirtilmemiş")
        commit = ""
        if p.line_type == "postpaid":
            commit = (f", {p.commitment_months} ay taahhütlü" if p.commitment_status == "committed" else ", taahhütsüz")
        lines.append(f"[{sid}] 30 günlük kıyas: {fmt_tl(basis)}{commit}; internet: {gb_txt}")
    cheapest = min(rows, key=lambda r: r[2])
    lines.append(f"30 günlük kıyasta en düşük: [{cheapest[0]}] ({fmt_tl(cheapest[2])}).")
    finite = [r for r in rows if r[3] is not None]
    if finite:
        most = max(finite, key=lambda r: float("inf") if r[3] == UNLIMITED else r[3])
        lines.append(f"En çok genel internet: [{most[0]}].")
    lines.append("</KIYAS>")
    return "\n".join(lines)
