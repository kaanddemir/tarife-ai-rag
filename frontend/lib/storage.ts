import type { Conversation } from "./types";

const CONV_KEY = "tarifeai:conversations";
const ACTIVE_KEY = "tarifeai:activeId";
const GUEST_USED_KEY = "tarifeai:guestUsed";

function uid(): string {
  return `c_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}

export function loadConversations(): Conversation[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(CONV_KEY);
    return raw ? (JSON.parse(raw) as Conversation[]) : [];
  } catch {
    return [];
  }
}

export function saveConversations(convs: Conversation[]): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(CONV_KEY, JSON.stringify(convs));
}

export function loadActiveId(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(ACTIVE_KEY);
}

export function saveActiveId(id: string): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(ACTIVE_KEY, id);
}

/** Display-only guest message counter; the backend is the real quota authority. */
export function loadGuestUsed(): number {
  if (typeof window === "undefined") return 0;
  const raw = window.localStorage.getItem(GUEST_USED_KEY);
  const n = raw ? parseInt(raw, 10) : 0;
  return Number.isFinite(n) && n > 0 ? n : 0;
}

export function saveGuestUsed(n: number): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(GUEST_USED_KEY, String(n));
}

export function newConversation(): Conversation {
  return { id: uid(), title: "Yeni sohbet", createdAt: Date.now(), messages: [] };
}

export function titleFrom(text: string): string {
  const t = text.trim().replace(/\s+/g, " ");
  return t.length > 40 ? `${t.slice(0, 40)}…` : t || "Yeni sohbet";
}
