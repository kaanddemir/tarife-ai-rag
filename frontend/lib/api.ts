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
  customer_type: string;
  price_try: number | null;
  internet_gb: number | null;
  social_media_gb: number | null;
  bonus_internet_gb: number | null;
  // Where the bonus works; null when there is no bonus.
  bonus_internet_scope?: "general" | "tethering_only" | "app_specific" | null;
  // General-use internet: general allowance + a bonus only when its scope is general.
  general_internet_gb?: number | null;
  // 30-day comparison value of a prepaid package (not a billed amount); null otherwise.
  price_30d_equivalent?: number | null;
  price_period: string | null;
  // Whether the plan charges again by itself; "unknown" = source does not say.
  renewal_type?: "recurring" | "one_time" | "unknown";
  commitment_months: number | null;
  commitment_status: "committed" | "no_commitment" | "unknown";
  price_lock_months: number | null;
  no_commitment_price_try: number | null;
  // Compatibility alias: always equal to operator_family and derived by the API.
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
  // Canonical catalog fields.
  activation_channels?: ("new_line" | "number_porting")[] | null; // null = source does not say
  benefits?: Benefit[] | null;
  benefits_status?: "verified" | "verified_none" | "unverified" | null;
  social_media_apps?: string[] | null;
  promo_months?: number | null;
  price_after_promo_try?: number | null;
  // When the facts were last checked on the official page (ISO date-time).
  verified_at?: string | null;
  // Retrieval verdict, only on /chat results (null elsewhere / older chats).
  retrieval_status?: "match" | "unverified" | "compromise" | null;
  // Per-requirement result: [label, true | false | null = could not be checked].
  retrieval_checks?: [string, boolean | null][] | null;
}

/** One benefit as the official page states it (see backend ingest/normalize.py). */
export interface Benefit {
  category: "subscription" | "app_data" | "bonus_data" | "voucher" | "discount" | "roaming" | "service";
  service: string;
  offer: "included" | "free_trial" | "discounted" | "requires_purchase";
  duration_months?: number;
  after_trial?: "paid" | "ends" | "unknown";
  after_trial_price_try?: number;
  amount_gb?: number | null; // -1 = unlimited
  apps?: string[];
  value_try?: number;
  eligibility?: "new_line_only";
  activation?: string;
  note?: string;
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

/** Thrown when the session cannot be (re)authenticated after one refresh attempt. */
export class AuthError extends Error {
  constructor() {
    super("auth");
    this.name = "AuthError";
  }
}

/** Any other non-OK reply; `reason` is the backend's short code when it sent one. */
export class ServerError extends Error {
  status: number;
  reason?: string;
  constructor(status: number, reason?: string) {
    super(`API error ${status}`);
    this.name = "ServerError";
    this.status = status;
    this.reason = reason;
  }
}

/** Thrown when the backend does not answer within CHAT_TIMEOUT_MS. */
export class RequestTimeoutError extends Error {
  constructor() {
    super("timeout");
    this.name = "RequestTimeoutError";
  }
}

// Above the backend's worst case (embedding + up to two bounded provider attempts),
// so the client gives up only when the server is truly stuck.
export const CHAT_TIMEOUT_MS = 90_000;

/** Returns the current access token; `refresh` forces a session refresh first. */
export type TokenProvider = (refresh: boolean) => Promise<string | null>;

async function postChat(
  body: string,
  token: string | null,
  signal: AbortSignal
): Promise<Response> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  return fetch(`${API_BASE}/chat`, { method: "POST", headers, body, signal });
}

export async function chat(
  question: string,
  history: ChatTurn[] = [],
  getToken: TokenProvider
): Promise<ChatResponse> {
  const body = JSON.stringify({ question, history });
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), CHAT_TIMEOUT_MS);
  try {
    let res = await postChat(body, await getToken(false), controller.signal);
    if (res.status === 401) {
      // Expired or revoked token: refresh once and retry once. A second 401 stops
      // here, so a broken session can never loop.
      const fresh = await getToken(true);
      if (!fresh) throw new AuthError();
      res = await postChat(body, fresh, controller.signal);
      if (res.status === 401) throw new AuthError();
    }

    if (res.status === 429) {
      const reason = await res
        .json()
        .then((b) => b?.detail?.reason as QuotaReason | undefined)
        .catch(() => undefined);
      if (reason) throw new QuotaError(reason);
    }
    if (!res.ok) {
      const reason = await res
        .json()
        .then((b) => (typeof b?.detail?.reason === "string" ? (b.detail.reason as string) : undefined))
        .catch(() => undefined);
      throw new ServerError(res.status, reason);
    }
    return (await res.json()) as ChatResponse;
  } catch (e) {
    if (controller.signal.aborted) throw new RequestTimeoutError();
    throw e;
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Current catalog rows for package ids saved in an older chat (POST /compare).
 * Ids are stable across ingests, so a missing id means the tariff is no longer
 * offered -- the caller must say so, never substitute another plan.
 */
export async function fetchCurrentPackages(ids: string[]): Promise<Package[]> {
  const res = await fetch(`${API_BASE}/compare`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ package_ids: ids.slice(0, 20) }),
  });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return (await res.json()) as Package[];
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
  "28_day": "28 günlük",
  "60_day": "60 günlük",
  "90_day": "90 günlük",
  "180_day": "180 günlük",
  "360_day": "360 günlük",
  // Pre-2D.2 values, kept so packages saved in older chats still get a label.
  one_time: "tek seferlik",
  "3_month": "3 aylık",
  "6_month": "6 aylık",
  "12_month": "12 aylık",
};

/** -1 = unlimited (same as the backend's normalize.UNLIMITED sentinel). */
export const UNLIMITED = -1;
