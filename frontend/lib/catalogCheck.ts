"use client";

// Plans saved in an older chat are a record of that conversation, not a current
// offer. When such a chat is opened, the saved package ids are looked up once in
// the live catalog (ids are stable across ingests):
//   found   -> show today's facts for that same plan,
//   missing -> say the plan is no longer offered (never swap in another one),
//   failed  -> show the saved facts, clearly marked as unverified.

import { useEffect, useState } from "react";
import { fetchCurrentPackages, type Package } from "./api";
import type { Conversation } from "./types";

export type CheckState = "idle" | "checking" | "done" | "failed";

export interface CatalogCheck {
  state: CheckState;
  /** id -> current row, or null when the id is no longer in the catalog. */
  current: Record<string, Package | null>;
}

// Loaded once per page session: a plan's catalog status does not change mid-visit.
const cache: Record<string, Package | null> = {};

/** True for messages created before this page load (or by a version without timestamps). */
const SESSION_START = typeof window === "undefined" ? 0 : Date.now();
export function isHistorical(at: number | undefined): boolean {
  return at === undefined || at < SESSION_START;
}

export function useCatalogCheck(conversation: Conversation | null): CatalogCheck {
  const [check, setCheck] = useState<CatalogCheck>({ state: "idle", current: cache });

  const ids = Array.from(
    new Set(
      (conversation?.messages ?? [])
        .filter((m) => m.role === "assistant" && isHistorical(m.at))
        .flatMap((m) => (m.packages ?? []).map((p) => p.id))
    )
  );
  const key = ids.join(",");

  useEffect(() => {
    const unknown = ids.filter((id) => !(id in cache));
    if (unknown.length === 0) {
      setCheck({ state: ids.length ? "done" : "idle", current: { ...cache } });
      return;
    }
    let alive = true;
    setCheck({ state: "checking", current: { ...cache } });
    fetchCurrentPackages(unknown)
      .then((rows) => {
        const found = new Map(rows.map((r) => [r.id, r]));
        // Only ids actually asked for (the endpoint takes at most 20) get a verdict.
        for (const id of unknown.slice(0, 20)) cache[id] = found.get(id) ?? null;
        if (alive) setCheck({ state: "done", current: { ...cache } });
      })
      .catch(() => {
        if (alive) setCheck({ state: "failed", current: { ...cache } });
      });
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  return check;
}
