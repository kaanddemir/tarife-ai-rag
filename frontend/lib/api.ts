// NEXT_PUBLIC_API_BASE is inlined at build time. It must be set for the prod
// build; the localhost default is a dev convenience only.
if (!process.env.NEXT_PUBLIC_API_BASE && process.env.NODE_ENV === "production") {
  console.error(
    "NEXT_PUBLIC_API_BASE is not set — production build is pointing at http://localhost:8000."
  );
}

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

export interface Package {
  id: string;
  operator: string;
  name: string;
  canonical_id: string | null;
  line_type: string;
  customer_type: string;
  price_try: number | null;
  internet_gb: number | null;
  social_media_gb: number | null;
  bonus_internet_gb: number | null;
  total_internet_gb: number | null;
  price_period: string | null;
  commitment_months: number | null;
  commitment_status: "committed" | "no_commitment" | "unknown";
  price_lock_months: number | null;
  no_commitment_price_try: number | null;
  package_group: string | null;
  operator_family: string | null;
  audience_segment: "general" | "youth" | "child" | "family" | "public_special" | null;
  product_scope: "mobile_voice" | "wearable" | "data_only" | "iot";
  age_min: number | null;
  age_max: number | null;
  eligibility_tags: string[] | null;
  benefit_tags: string[] | null;
  voice_minutes: number | null;
  sms_count: number | null;
  validity_days: number | null;
  conditions: string | null;
  campaign: string | null;
  source_url: string;
  data_quality_status: "complete" | "review_required";
  missing_fields: string[] | null;
}

export interface Citation {
  id: string;
  operator: string;
  name: string;
  source_url: string;
}

export interface ChatTurn {
  role: "user" | "assistant";
  content: string;
}

export interface ChatResponse {
  answer: string;
  citations: Citation[];
  packages: Package[];
}

export type QuotaReason = "guest_limit" | "ip_limit" | "daily_limit";

/** Thrown when the backend rejects a message because a quota was reached (HTTP 429). */
export class QuotaError extends Error {
  reason: QuotaReason;
  constructor(reason: QuotaReason) {
    super(`quota:${reason}`);
    this.name = "QuotaError";
    this.reason = reason;
  }
}

export async function chat(
  question: string,
  history: ChatTurn[] = [],
  accessToken?: string | null
): Promise<ChatResponse> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (accessToken) headers["Authorization"] = `Bearer ${accessToken}`;

  const res = await fetch(`${API_BASE}/chat`, {
    method: "POST",
    headers,
    body: JSON.stringify({ question, history }),
  });

  if (res.status === 429) {
    const reason = await res
      .json()
      .then((b) => b?.detail?.reason as QuotaReason | undefined)
      .catch(() => undefined);
    if (reason) throw new QuotaError(reason);
  }
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}

export const OPERATOR_LABELS: Record<string, string> = {
  turkcell: "Turkcell",
  vodafone: "Vodafone",
  turk_telekom: "Türk Telekom",
};

// price_period -> short label for which period the price is for
// (mirrors backend prompt.py._PERIOD_LABELS exactly).
export const PERIOD_LABELS: Record<string, string> = {
  month: "aylık",
  one_time: "tek seferlik",
  "3_month": "3 aylık",
  "6_month": "6 aylık",
  "12_month": "12 aylık",
  "28_day": "28 günlük",
};

export const LINE_TYPE_LABELS: Record<string, string> = {
  prepaid: "Faturasız",
  postpaid: "Faturalı",
};

export const CUSTOMER_TYPE_LABELS: Record<string, string> = {
  new: "Yeni müşteri",
  existing: "Mevcut müşteri",
  any: "Herkese açık",
};

/** -1 = unlimited (same as the backend's normalize.UNLIMITED sentinel). */
export const UNLIMITED = -1;

export function fmtAmount(v: number | null, unit: string): string {
  if (v === null) return "—";
  if (v === UNLIMITED) return `Sınırsız ${unit}`;
  return `${v} ${unit}`;
}

/** If commitment is unknown, we don't render it as no-commitment. */
export function fmtCommitment(p: Package): string | null {
  if (p.commitment_status === "committed" && p.commitment_months)
    return `${p.commitment_months} ay taahhüt`;
  if (p.commitment_status === "no_commitment" && p.line_type === "postpaid")
    return "Taahhütsüz";
  if (p.commitment_status === "unknown" && p.line_type === "postpaid")
    return "Taahhüt bilgisi belirtilmemiş";
  return null;
}
