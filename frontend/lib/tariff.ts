// One authoritative place that turns an API package into Turkish display values.
// The recommendation card and the comparison view both read from here, so a price,
// period or allowance can never be shown two different ways.
//
// Rules this module enforces (mirrors backend app/rag/facts.py):
// - the payable price is always shown with its real period ("/ ay", "/ 90 gün");
// - the 30-day value is TarifeAI's calculation, labelled as such, never a charge;
// - general-use internet never includes social-media / app / hotspot-only quotas;
// - a missing fact is shown as "Belirtilmemiş", never guessed.

import {
  OPERATOR_LABELS,
  PERIOD_LABELS,
  UNLIMITED,
  type Benefit,
  type Package,
} from "./api";

export type Tone = "default" | "muted" | "unknown" | "warn" | "good";

export interface Cell {
  text: string;
  /** Second, quieter line that stays visible (e.g. which apps a quota covers). */
  note?: string;
  /** Secondary detail behind an "i" icon; never a restriction. */
  info?: string;
  /** A condition worth checking, behind a "!" icon. Critical restrictions stay in text/note. */
  warn?: string;
  tone?: Tone;
  /** Several separate facts in one cell, each with its own note. */
  items?: Cell[];
}

const UNKNOWN: Cell = { text: "Belirtilmemiş", tone: "unknown" };
const NOT_APPLICABLE: Cell = { text: "—", tone: "muted" };

// ---------- primitive formatters ----------

/** "1.250 TL", "99,90 TL" — same rule as the backend's facts.fmt_tl. */
export function fmtTL(value: number): string {
  const rounded = Math.round(value * 100) / 100;
  const whole = Number.isInteger(rounded);
  return (
    rounded.toLocaleString("tr-TR", {
      minimumFractionDigits: whole ? 0 : 2,
      maximumFractionDigits: 2,
    }) + " TL"
  );
}

/** "100 GB", "1,5 GB", "Sınırsız". */
export function fmtGB(value: number): string {
  if (value === UNLIMITED) return "Sınırsız";
  return `${value.toLocaleString("tr-TR", { maximumFractionDigits: 2 })} GB`;
}

function fmtCount(value: number | null, unit: string): Cell {
  if (value == null) return UNKNOWN;
  if (value === UNLIMITED) return { text: "Sınırsız" };
  return { text: `${value.toLocaleString("tr-TR")} ${unit}` };
}

export const minutesCell = (p: Package): Cell => fmtCount(p.voice_minutes, "dk");
export const smsCell = (p: Package): Cell => fmtCount(p.sms_count, "SMS");

export function fmtDate(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleDateString("tr-TR", { day: "numeric", month: "long", year: "numeric" });
}

export function operatorLabel(operator: string): string {
  return OPERATOR_LABELS[operator] ?? operator;
}

export function lineTypeLabel(p: Package): string {
  return p.line_type === "postpaid" ? "Faturalı" : p.line_type === "prepaid" ? "Faturasız" : p.line_type;
}

function isPostpaid(p: Package): boolean {
  return p.line_type === "postpaid";
}

// ---------- price ----------

export interface Price {
  /** "1.000 TL" or "Fiyat belirtilmemiş". */
  amount: string;
  /** "/ ay", "/ 90 gün". Empty when the period is unknown. */
  period: string;
  /** Plain-language billing, e.g. "Tek ödeme, 90 gün geçerli". */
  billing: string;
}

/** The price the operator actually charges, with the period it covers. */
export function price(p: Package): Price {
  const amount = p.price_try == null ? "Fiyat belirtilmemiş" : fmtTL(p.price_try);
  if (isPostpaid(p)) return { amount, period: "/ ay", billing: "Her ay faturalanır" };
  if (p.validity_days) {
    const days = p.validity_days;
    const billing =
      p.renewal_type === "recurring"
        ? `${days} günde bir yenilenir`
        : p.renewal_type === "one_time"
          ? `Tek ödeme, ${days} gün geçerli`
          : `${days} gün geçerli`;
    return { amount, period: `/ ${days} gün`, billing };
  }
  // Records saved before validity_days existed: fall back to the stored period label.
  const label = p.price_period ? PERIOD_LABELS[p.price_period] : undefined;
  return { amount, period: label ? `/ ${label}` : "", billing: label ? label : "Süre belirtilmemiş" };
}

/** TarifeAI's 30-day comparison value (prepaid, non-30-day). Never a billed amount. */
export function price30(p: Package): number | null {
  const v = p.price_30d_equivalent;
  return typeof v === "number" ? v : null;
}

// ---------- internet ----------

/** General-use internet (bonus counts only when the page says it is general). */
export function generalInternet(p: Package): number | null {
  if (p.general_internet_gb !== undefined) return p.general_internet_gb;
  // For older stored records, only the plain allowance is known to be general.
  return p.internet_gb;
}

function appsText(apps: string[] | null | undefined): string | undefined {
  if (!apps || apps.length === 0) return undefined;
  return apps.length > 4 ? `${apps.slice(0, 4).join(", ")} +${apps.length - 4}` : apps.join(", ");
}

/** Quotas that only work for certain apps or for hotspot — never general internet. */
export function restrictedData(p: Package): Cell[] {
  const out: Cell[] = [];
  if (p.social_media_gb != null && p.social_media_gb !== 0) {
    out.push({
      text: `${fmtGB(p.social_media_gb)} sosyal medya`,
      note: appsText(p.social_media_apps) ?? "Hangi uygulamalar olduğu belirtilmemiş",
    });
  }
  if (p.bonus_internet_gb && p.bonus_internet_scope && p.bonus_internet_scope !== "general") {
    out.push({
      text: `${fmtGB(p.bonus_internet_gb)} bonus`,
      note: p.bonus_internet_scope === "tethering_only" ? "Yalnızca paylaşım (hotspot)" : "Yalnızca belirli uygulamalar",
    });
  }
  for (const b of p.benefits ?? []) {
    if (b.category !== "app_data" || b.offer === "requires_purchase") continue;
    // Already covered by the social-media quota above when it lists the same apps.
    if (p.social_media_gb && b.apps?.every((a) => p.social_media_apps?.includes(a))) continue;
    out.push({ text: b.service, note: b.note });
  }
  return out;
}

export function generalInternetCell(p: Package): Cell {
  const gb = generalInternet(p);
  if (gb == null) return UNKNOWN;
  const cell: Cell = { text: gb === UNLIMITED ? "Sınırsız" : fmtGB(gb) };
  if (p.bonus_internet_gb && p.bonus_internet_scope === "general" && gb !== UNLIMITED)
    cell.info = `Bu miktara ${fmtGB(p.bonus_internet_gb)} bonus internet dahildir.`;
  return cell;
}

// ---------- contract & duration ----------

export function commitmentCell(p: Package): Cell {
  if (!isPostpaid(p)) return { text: "Yok", info: "Faturasız paketlerde taahhüt olmaz." };
  if (p.commitment_status === "committed")
    return p.commitment_months ? { text: `${p.commitment_months} ay taahhüt` } : { text: "Taahhütlü", note: "Süre belirtilmemiş" };
  if (p.commitment_status === "no_commitment") return { text: "Taahhütsüz" };
  return UNKNOWN;
}

export function noCommitmentPriceCell(p: Package): Cell {
  if (!isPostpaid(p) || p.commitment_status !== "committed") return NOT_APPLICABLE;
  return p.no_commitment_price_try != null ? { text: `${fmtTL(p.no_commitment_price_try)} / ay` } : UNKNOWN;
}

export function priceLockCell(p: Package): Cell {
  if (!isPostpaid(p)) return NOT_APPLICABLE;
  return p.price_lock_months ? { text: `${p.price_lock_months} fatura dönemi sabit` } : UNKNOWN;
}

export function promoCell(p: Package): Cell | null {
  if (!p.promo_months || p.price_after_promo_try == null || p.price_try == null)
    return null;
  return {
    text: `İlk ${p.promo_months} ay ${fmtTL(p.price_try)}`,
    note: `Sonra ${fmtTL(p.price_after_promo_try)} / ay`,
    tone: "warn",
  };
}

export function validityCell(p: Package): Cell {
  if (isPostpaid(p)) return { text: "Fatura dönemi boyunca" };
  return p.validity_days ? { text: `${p.validity_days} gün` } : UNKNOWN;
}

export function renewalCell(p: Package): Cell {
  if (isPostpaid(p)) return { text: "Her ay devam eder" };
  if (p.renewal_type === "recurring")
    return { text: "Otomatik yenilenir", info: "İptal edilmezse ve bakiye yeterliyse süre sonunda yenilenir." };
  if (p.renewal_type === "one_time") return { text: "Tek seferlik", info: "Süre sonunda kendiliğinden yenilenmez." };
  return UNKNOWN;
}

// ---------- eligibility ----------

const AUDIENCE: Record<string, string> = {
  youth: "Gençlere özel",
  child: "Çocuklara özel",
  family: "Aile tarifesi",
  public_special: "Kurum / meslek grubuna özel",
};

const ELIGIBILITY_TAGS: Record<string, string> = {
  parliamentarian: "Yalnızca milletvekillerine",
  partner_channel_only: "Yalnızca anlaşmalı satış kanalında",
  dealer_only: "Yalnızca bayiden",
  province_restricted: "Yalnızca belirli illerde",
  identity_required: "Kimlik doğrulaması gerekir",
  activation_window: "Belirli bir başvuru döneminde",
  online_only: "Yalnızca online başvuru",
  parental_relationship_required: "Ebeveyn adına açılır",
};

// Conditions on how to apply rather than who qualifies: shown behind a "!" so the
// cell stays scannable. Everything else (age, audience, region, channel) stays visible.
const SECONDARY_TAGS: Record<string, string> = {
  activation_window: "Başvuru dönemi kısıtlıdır.",
  identity_required: "Kimlik doğrulaması gerekir.",
  online_only: "Yalnızca online başvuru yapılabilir.",
};

/** Who can buy it: audience/age first, then each restriction the page states. */
export function eligibility(p: Package): Cell[] {
  const out: Cell[] = [];
  const who = p.audience_segment && p.audience_segment !== "general" ? AUDIENCE[p.audience_segment] : null;
  const ages =
    p.age_min != null && p.age_max != null
      ? `${p.age_min}–${p.age_max} yaş`
      : p.age_max != null
        ? `En fazla ${p.age_max} yaş`
        : p.age_min != null
          ? `En az ${p.age_min} yaş`
          : null;
  if (who || ages) out.push({ text: [who, ages].filter(Boolean).join(", "), tone: "warn" });
  for (const tag of p.eligibility_tags ?? []) {
    out.push({ text: ELIGIBILITY_TAGS[tag] ?? tag, tone: "warn" });
  }
  return out;
}

export function eligibilitySummary(p: Package): Cell {
  const tags = p.eligibility_tags ?? [];
  const secondary = tags.filter((t) => t in SECONDARY_TAGS).map((t) => SECONDARY_TAGS[t]);
  const critical = eligibility({ ...p, eligibility_tags: tags.filter((t) => !(t in SECONDARY_TAGS)) });
  const base = p.customer_type === "new" ? "Yeni müşteriler" : p.customer_type === "existing" ? "Mevcut müşteriler" : "Herkes";
  const warn = secondary.length ? secondary.join(" ") : undefined;
  if (critical.length === 0) return { text: base, warn };
  return { text: critical.map((c) => c.text).join(" · "), note: base, tone: "warn", warn };
}

export function channelCell(p: Package): Cell {
  const ch = p.activation_channels;
  if (ch === undefined || ch === null) return UNKNOWN;
  const labels = ch.map((c) => (c === "new_line" ? "Yeni hat" : c === "number_porting" ? "Numara taşıma" : c));
  if (labels.length === 1) return { text: `Yalnızca ${labels[0].toLocaleLowerCase("tr-TR")}`, tone: "warn" };
  return { text: labels.join(", ") };
}

// ---------- benefits ----------

/** How a benefit is offered, in words a customer reads correctly. */
export function benefitOffer(b: Benefit): { label: string; tone: Tone } {
  switch (b.offer) {
    case "free_trial":
      return { label: b.duration_months ? `${b.duration_months} ay ücretsiz deneme` : "Ücretsiz deneme", tone: "warn" };
    case "discounted":
      return { label: "İndirimli", tone: "default" };
    case "requires_purchase":
      return { label: "Ek satın alma gerekir", tone: "warn" };
    default:
      return { label: b.duration_months ? `${b.duration_months} ay dahil` : "Dahil", tone: "good" };
  }
}

function afterTrial(b: Benefit): string | undefined {
  if (b.offer !== "free_trial") return undefined;
  if (b.after_trial === "paid")
    return b.after_trial_price_try ? `Sonrasında ${fmtTL(b.after_trial_price_try)} ücretli` : "Sonrasında ücretli";
  if (b.after_trial === "ends") return "Deneme bitince sona erer";
  return "Deneme sonrası belirtilmemiş";
}

export interface BenefitItem {
  name: string;
  offer: string;
  tone: Tone;
  detail?: string;
}

/** Benefits other than app-specific data (that lives under internet). */
export function benefitItems(p: Package): BenefitItem[] {
  return (p.benefits ?? [])
    .filter((b) => b.category !== "app_data")
    .map((b) => {
      const { label, tone } = benefitOffer(b);
      const extra = [
        b.category === "bonus_data" && b.amount_gb ? fmtGB(b.amount_gb) : null,
        b.category === "voucher" && b.value_try ? `${fmtTL(b.value_try)} değerinde` : null,
        b.eligibility === "new_line_only" ? "Yalnızca yeni hat alanlara" : null,
        afterTrial(b),
        b.activation ? `Nasıl alınır: ${b.activation}` : null,
      ].filter(Boolean);
      return { name: b.service, offer: label, tone, detail: extra.join(" · ") || undefined };
    });
}

export function benefitsCell(p: Package): Cell {
  if (p.benefits_status === "unverified") return { text: "Resmi sayfada doğrulanamadı", tone: "unknown" };
  const items = benefitItems(p);
  if (items.length === 0) return { text: "Ek avantaj yok", tone: "muted" };
  const trials = items.filter((i) => i.offer.includes("deneme")).length;
  return {
    text: `${items.length} avantaj`,
    note: trials ? `${trials} tanesi ücretsiz deneme` : undefined,
  };
}

// ---------- match verdict (from the backend's retrieval, never inferred here) ----------

export interface Verdict {
  label: string;
  tone: Tone;
  /** Requirement-by-requirement result, as the backend checked it. */
  checks: { label: string; ok: boolean | null }[];
}

export function verdict(p: Package): Verdict | null {
  const checks = (p.retrieval_checks ?? []).map(([label, ok]) => ({ label, ok }));
  switch (p.retrieval_status) {
    case "match":
      return { label: "Şartlarına uyuyor", tone: "good", checks };
    case "unverified":
      return { label: "Bazı şartlar doğrulanamadı", tone: "unknown", checks };
    case "compromise":
      return { label: "Alternatif: bir şartını karşılamıyor", tone: "warn", checks };
    default:
      return null;
  }
}

// ---------- comparison rows ----------

export type Group = "Fiyat" | "İnternet" | "Konuşma ve SMS" | "Sözleşme ve süre" | "Kimler alabilir" | "Avantajlar";

export interface Row {
  key: string;
  label: string;
  group: Group;
  /** Shown before "Tüm detaylar" is opened. */
  primary: boolean;
  cells: Cell[];
  /** Optional explanation shown under the row label. */
  hint?: string;
}

function listCell(cells: Cell[], empty: Cell): Cell {
  if (cells.length === 0) return empty;
  if (cells.length === 1) return cells[0];
  return { text: cells.map((c) => c.text).join(", "), items: cells };
}

/**
 * Rows for an aligned comparison. Rows that say nothing for any plan in this set
 * (e.g. no plan has a promo) are dropped instead of showing a column of dashes.
 */
export function comparisonRows(packages: Package[]): Row[] {
  const anyPrepaid30 = packages.some((p) => price30(p) !== null);
  const anyPostpaidCommitted = packages.some((p) => isPostpaid(p) && p.commitment_status === "committed");
  const anyPostpaid = packages.some(isPostpaid);
  const anyPromo = packages.some((p) => promoCell(p) !== null);
  const anyRestricted = packages.some((p) => restrictedData(p).length > 0);
  const anyVerdict = packages.some((p) => verdict(p)?.checks.length);

  const rows: Row[] = [
    {
      key: "billing",
      label: "Ödeme",
      group: "Fiyat",
      primary: true,
      cells: packages.map((p) => ({ text: price(p).billing })),
    },
  ];
  if (anyPrepaid30) {
    rows.push({
      key: "price30",
      label: "30 günlük karşılığı",
      hint: "TarifeAI hesabı, yalnızca kıyas için. Operatör bu tutarı faturalamaz.",
      group: "Fiyat",
      primary: true,
      cells: packages.map((p) => {
        const v = price30(p);
        if (v !== null) return { text: `≈ ${fmtTL(v)}`, tone: "muted" };
        return isPostpaid(p) ? { text: "Aylık fiyatı ile aynı", tone: "muted" } : { text: "Fiyatı ile aynı", tone: "muted" };
      }),
    });
  }
  if (anyPromo) {
    rows.push({
      key: "promo",
      label: "Tanıtım fiyatı",
      group: "Fiyat",
      primary: true,
      cells: packages.map((p) => promoCell(p) ?? NOT_APPLICABLE),
    });
  }
  if (anyPostpaidCommitted) {
    rows.push({
      key: "nocommit",
      label: "Taahhütsüz fiyat",
      group: "Fiyat",
      primary: false,
      cells: packages.map(noCommitmentPriceCell),
    });
  }
  if (anyPostpaid) {
    rows.push({ key: "lock", label: "Fiyat sabitleme", group: "Fiyat", primary: false, cells: packages.map(priceLockCell) });
  }
  rows.push({
    key: "general",
    label: "Genel internet",
    hint: "Her uygulamada kullanılabilen internet.",
    group: "İnternet",
    primary: true,
    cells: packages.map(generalInternetCell),
  });
  if (anyRestricted) {
    rows.push({
      key: "restricted",
      label: "Uygulamaya özel",
      hint: "Yalnızca belirtilen uygulamalarda geçer; genel internete eklenmez.",
      group: "İnternet",
      primary: true,
      cells: packages.map((p) => listCell(restrictedData(p), { text: "Yok", tone: "muted" })),
    });
  }
  if (anyPostpaid) {
    // Prepaid plans never have a commitment; the row only matters when a postpaid plan is in the set.
    rows.push({ key: "commitment", label: "Taahhüt", group: "Sözleşme ve süre", primary: true, cells: packages.map(commitmentCell) });
  }
  rows.push(
    { key: "minutes", label: "Konuşma", group: "Konuşma ve SMS", primary: true, cells: packages.map(minutesCell) },
    { key: "sms", label: "SMS", group: "Konuşma ve SMS", primary: true, cells: packages.map(smsCell) },
    { key: "validity", label: "Geçerlilik", group: "Sözleşme ve süre", primary: false, cells: packages.map(validityCell) },
    { key: "renewal", label: "Yenileme", group: "Sözleşme ve süre", primary: false, cells: packages.map(renewalCell) },
    { key: "who", label: "Kimler alabilir", group: "Kimler alabilir", primary: true, cells: packages.map(eligibilitySummary) },
    { key: "channel", label: "Başvuru şekli", group: "Kimler alabilir", primary: false, cells: packages.map(channelCell) },
    { key: "benefits", label: "Ek avantajlar", group: "Avantajlar", primary: false, cells: packages.map(benefitsCell) },
  );
  if (anyVerdict) {
    rows.push({
      key: "checks",
      label: "Şartlarınla eşleşme",
      group: "Kimler alabilir",
      primary: false,
      cells: packages.map((p) => {
        const v = verdict(p);
        if (!v || v.checks.length === 0) return NOT_APPLICABLE;
        const failed = v.checks.filter((c) => c.ok === false).map((c) => c.label);
        const unknown = v.checks.filter((c) => c.ok === null).map((c) => c.label);
        if (failed.length) return { text: `Karşılamıyor: ${failed.join(", ")}`, tone: "warn" };
        if (unknown.length) return { text: `Doğrulanamadı: ${unknown.join(", ")}`, tone: "unknown" };
        return { text: "Hepsini karşılıyor", tone: "good" };
      }),
    });
  }
  // Keep each group contiguous regardless of push order (Array.sort is stable).
  const order: Group[] = ["Fiyat", "İnternet", "Konuşma ve SMS", "Sözleşme ve süre", "Kimler alabilir", "Avantajlar"];
  return rows.sort((a, b) => order.indexOf(a.group) - order.indexOf(b.group));
}
