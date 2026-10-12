import type { Conversation, Message } from "./types";

const CONV_KEY = "tarifeai:conversations";
const ACTIVE_KEY = "tarifeai:activeId";
const GUEST_USED_KEY = "tarifeai:guestUsed";
const DAILY_USED_KEY = "tarifeai:dailyUsed";

/** Local calendar day, used to auto-reset the signed-in daily counter. */
function todayKey(): string {
  const d = new Date();
  return `${d.getFullYear()}-${d.getMonth() + 1}-${d.getDate()}`;
}

// localStorage can throw (quota exceeded, storage disabled, some private modes).
// Persistence is a convenience: a failed write must never break the chat.
function safeGet(key: string): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function safeSet(key: string, value: string): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // ignore: the in-memory state stays correct for this session
  }
}

function uid(): string {
  return `c_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}

function isObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/**
 * Keep what an older or hand-edited save can still render; drop only the broken
 * parts. A package without an id, name or source link is removed from its
 * message (the answer text stays), so a stale object can never render as a plan.
 */
function cleanMessage(v: unknown): Message | null {
  if (!isObject(v)) return null;
  if ((v.role !== "user" && v.role !== "assistant") || typeof v.content !== "string") return null;
  const m = v as unknown as Message;
  const packages = Array.isArray(m.packages)
    ? m.packages.filter(
        (p) => isObject(p) && typeof p.id === "string" && typeof p.name === "string" && typeof p.source_url === "string"
      )
    : undefined;
  const citations = Array.isArray(m.citations)
    ? m.citations.filter((c) => isObject(c) && typeof c.id === "string" && typeof c.source_url === "string")
    : undefined;
  return { ...m, packages, citations };
}

function cleanConversation(v: unknown): Conversation | null {
  if (!isObject(v) || typeof v.id !== "string" || !Array.isArray(v.messages)) return null;
  return {
    id: v.id,
    title: typeof v.title === "string" && v.title ? v.title : "Sohbet",
    createdAt: typeof v.createdAt === "number" ? v.createdAt : 0,
    messages: v.messages.map(cleanMessage).filter((m): m is Message => m !== null),
  };
}

export function loadConversations(): Conversation[] {
  try {
    const raw = safeGet(CONV_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(parsed)) return [];
    return parsed.map(cleanConversation).filter((c): c is Conversation => c !== null);
  } catch {
    return [];
  }
}

export function saveConversations(convs: Conversation[]): void {
  safeSet(CONV_KEY, JSON.stringify(convs));
}

export function loadActiveId(): string | null {
  return safeGet(ACTIVE_KEY);
}

export function saveActiveId(id: string): void {
  safeSet(ACTIVE_KEY, id);
}

/** Display-only guest message counter; the backend is the real quota authority. */
export function loadGuestUsed(): number {
  const raw = safeGet(GUEST_USED_KEY);
  const n = raw ? parseInt(raw, 10) : 0;
  return Number.isFinite(n) && n > 0 ? n : 0;
}

export function saveGuestUsed(n: number): void {
  safeSet(GUEST_USED_KEY, String(n));
}

/**
 * Display-only signed-in daily counter; resets when the calendar day changes.
 * Approximate (per-device, local midnight); the backend is the real authority.
 */
export function loadDailyUsed(): number {
  try {
    const raw = safeGet(DAILY_USED_KEY);
    if (!raw) return 0;
    const { date, count } = JSON.parse(raw) as { date: string; count: number };
    if (date !== todayKey()) return 0;
    return Number.isFinite(count) && count > 0 ? count : 0;
  } catch {
    return 0;
  }
}

export function saveDailyUsed(n: number): void {
  safeSet(DAILY_USED_KEY, JSON.stringify({ date: todayKey(), count: n }));
}

export function newConversation(): Conversation {
  return { id: uid(), title: "Yeni sohbet", createdAt: Date.now(), messages: [] };
}

export function titleFrom(text: string): string {
  const t = text.trim().replace(/\s+/g, " ");
  return t.length > 40 ? `${t.slice(0, 40)}…` : t || "Yeni sohbet";
}
