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
  line_type: string;
  price_try: number | null;
  internet_gb: number | null;
  social_media_gb: number | null;
  bonus_internet_gb: number | null;
  total_internet_gb: number | null;
  price_period: string | null;
  package_group: string | null;
  voice_minutes: number | null;
  sms_count: number | null;
  validity_days: number | null;
  conditions: string | null;
  campaign: string | null;
  source_url: string;
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

export function fmtAmount(v: number | null, unit: string): string {
  if (v === null) return "—";
  if (v === -1) return `Sınırsız ${unit}`;
  return `${v} ${unit}`;
}
