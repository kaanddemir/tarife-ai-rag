"use client";

import { useEffect, useRef, useState } from "react";
import {
  AuthError,
  chat,
  OPERATOR_LABELS,
  QuotaError,
  RequestTimeoutError,
  ServerError,
  type ChatTurn,
} from "@/lib/api";
import type { Conversation, Message } from "@/lib/types";
import {
  loadConversations,
  saveConversations,
  loadActiveId,
  saveActiveId,
  loadGuestUsed,
  saveGuestUsed,
  loadDailyUsed,
  saveDailyUsed,
  newConversation,
  titleFrom,
} from "@/lib/storage";
import { useAuth } from "@/lib/auth";
import type { LineTypeKey, OperatorKey } from "@/components/OperatorPicker";

// retrieve.py bu kelimeleri arar; "faturasiz" once kontrol edilir cunku "fatura" icerir.
const LINE_TYPE_PREFIX: Record<Exclude<LineTypeKey, "all">, string> = {
  postpaid: "faturalı",
  prepaid: "faturasız",
};
import Sidebar from "@/components/Sidebar";
import ChatArea from "@/components/ChatArea";
import LoginModal from "@/components/LoginModal";

const GUEST_LIMIT = 5;
// Server-side daily quota for signed-in users (backend config.daily_message_limit).
// Shown in the sidebar as an allowance; kept here alongside GUEST_LIMIT.
const DAILY_LIMIT = 20;

export default function Home() {
  const { isSignedIn, email, getAccessToken, signOut } = useAuth();
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [navOpen, setNavOpen] = useState(false);
  const [hydrated, setHydrated] = useState(false);
  const [guestUsed, setGuestUsed] = useState(0);
  const [dailyUsed, setDailyUsed] = useState(0);
  const [loginOpen, setLoginOpen] = useState(false);
  const [loginReason, setLoginReason] = useState<string | undefined>();
  // Synchronous in-flight guard: `loading` state updates too late to stop a fast
  // double submit (Enter + click) from sending the same question twice.
  const sendingRef = useRef(false);

  useEffect(() => {
    setGuestUsed(loadGuestUsed());
    setDailyUsed(loadDailyUsed());
    const convs = loadConversations();
    if (convs.length) {
      setConversations(convs);
      const saved = loadActiveId();
      setActiveId(saved && convs.some((c) => c.id === saved) ? saved : convs[0].id);
    } else {
      const c = newConversation();
      setConversations([c]);
      setActiveId(c.id);
    }
    setHydrated(true);
  }, []);

  // Persist on change (after hydration).
  useEffect(() => {
    if (hydrated) saveConversations(conversations);
  }, [conversations, hydrated]);
  useEffect(() => {
    if (hydrated && activeId) saveActiveId(activeId);
  }, [activeId, hydrated]);

  const active = conversations.find((c) => c.id === activeId) ?? null;

  // Escape closes the mobile conversation drawer.
  useEffect(() => {
    if (!navOpen) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setNavOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [navOpen]);

  function handleNew() {
    if (active && active.messages.length === 0) {
      setNavOpen(false);
      return;
    }

    const empty = conversations.find((c) => c.messages.length === 0);
    if (empty) {
      setActiveId(empty.id);
      setNavOpen(false);
      return;
    }

    const c = newConversation();
    setConversations((prev) => [c, ...prev]);
    setActiveId(c.id);
    setNavOpen(false);
  }

  function handleSelect(id: string) {
    setActiveId(id);
    setNavOpen(false);
  }

  function handleDelete(id: string) {
    // Computed outside the state updater: updaters must stay pure (React may run
    // them twice), and a repeated click for an already-removed id is a no-op.
    if (!conversations.some((c) => c.id === id)) return;
    const next = conversations.filter((c) => c.id !== id);
    if (id !== activeId) {
      setConversations(next);
      return;
    }
    // Deleting the open conversation lands on a clean new chat.
    const empty = next.find((c) => c.messages.length === 0);
    const fresh = empty ?? newConversation();
    setConversations(empty ? next : [fresh, ...next]);
    setActiveId(fresh.id);
  }

  function handleRename(id: string, title: string) {
    const nextTitle = title.trim();
    if (!nextTitle) return;
    setConversations((prev) =>
      prev.map((c) => (c.id === id ? { ...c, title: titleFrom(nextTitle) } : c))
    );
  }

  function updateActive(fn: (c: Conversation) => Conversation) {
    setConversations((prev) => prev.map((c) => (c.id === activeId ? fn(c) : c)));
  }

  function openLogin(reason?: string) {
    setLoginReason(reason);
    setLoginOpen(true);
  }

  /**
   * Send a question. `replaceFrom` is the index of a failed attempt (its question
   * and error bubble) to replace, so "Tekrar dene" does not duplicate the question.
   */
  async function handleSend(
    question: string,
    operator: OperatorKey,
    lineType: LineTypeKey,
    replaceFrom?: number,
  ) {
    if (!activeId || sendingRef.current) return;

    // Pre-empt the guest cap for snappy UX; the backend still enforces the real limit.
    if (!isSignedIn && guestUsed >= GUEST_LIMIT) {
      openLogin();
      return;
    }

    const userMsg: Message = { role: "user", content: question, at: Date.now() };
    const base = (active?.messages ?? []).slice(0, replaceFrom);
    // Local error bubbles are not conversation; sending them back would skew the
    // backend's clarification state and waste history slots.
    const history: ChatTurn[] = base
      .filter((m) => !m.error)
      .slice(-5)
      .map((m) => ({ role: m.role, content: m.content }));

    updateActive((c) => ({
      ...c,
      title: c.messages.length === 0 ? titleFrom(question) : c.title,
      messages: [...c.messages.slice(0, replaceFrom), userMsg],
    }));
    sendingRef.current = true;
    setLoading(true);

    // Scope by prepending the operator name and line type — backend retrieve()
    // detects both and turns them into hard filters.
    const scope = [
      operator === "all" ? null : OPERATOR_LABELS[operator],
      lineType === "all" ? null : LINE_TYPE_PREFIX[lineType],
    ].filter(Boolean);
    const scoped = scope.length ? `${scope.join(" ")} ${question}` : question;

    try {
      const r = await chat(scoped, history, getAccessToken);
      const assistant: Message = {
        role: "assistant",
        content: r.answer,
        citations: r.citations,
        packages: r.packages,
        at: Date.now(),
      };
      updateActive((c) => ({ ...c, messages: [...c.messages, assistant] }));
      if (isSignedIn) {
        setDailyUsed((n) => {
          const next = n + 1;
          saveDailyUsed(next);
          return next;
        });
      } else {
        setGuestUsed((n) => {
          const next = n + 1;
          saveGuestUsed(next);
          return next;
        });
      }
    } catch (e) {
      if (e instanceof QuotaError) {
        if (e.reason === "daily_limit") {
          // Server says the day's quota is spent — reflect 0 remaining in the meter.
          setDailyUsed(DAILY_LIMIT);
          saveDailyUsed(DAILY_LIMIT);
          const assistant: Message = {
            role: "assistant",
            content:
              "Günlük mesaj limitine ulaştın. Yarın tekrar deneyebilirsin.",
            error: true,
            errorKind: "quota",
            at: Date.now(),
          };
          updateActive((c) => ({ ...c, messages: [...c.messages, assistant] }));
        } else {
          // guest_limit / ip_limit → sign-in unlocks more messages.
          if (!isSignedIn) {
            setGuestUsed(GUEST_LIMIT);
            saveGuestUsed(GUEST_LIMIT);
          }
          openLogin(
            e.reason === "ip_limit"
              ? "Bu ağdan bugünkü ücretsiz mesaj hakkı doldu. Giriş yaparak devam et."
              : undefined
          );
        }
      } else {
        // Every failure is an error bubble, never part of the conversation, and says
        // what went wrong without technical internals. A provider outage must not
        // read as "no tariff found".
        const [errorKind, content]: [Message["errorKind"], string] =
          e instanceof AuthError
            ? ["auth", "Oturumun doğrulanamadı. Sayfayı yenileyip tekrar giriş yapman gerekebilir."]
            : e instanceof RequestTimeoutError
              ? ["timeout", "Yanıt çok uzun sürdü ve beklemeyi bıraktım. Sorunu tekrar gönderebilirsin."]
              : e instanceof TypeError
                ? ["network", "Sunucuya ulaşılamadı. İnternet bağlantını kontrol edip tekrar dene."]
                : e instanceof ServerError && e.reason === "provider_unavailable"
                  ? ["server", "Yapay zeka servisi şu an yanıt vermiyor. Bu mesaj hakkından düşülmedi; birazdan tekrar dene."]
                  : e instanceof ServerError && e.status === 429
                    ? ["server", "Çok hızlı mesaj gönderildi. Birkaç saniye bekleyip tekrar dene."]
                    : ["server", "Şu an yanıt veremedim. Lütfen birazdan tekrar dene."];
        const assistant: Message = { role: "assistant", content, error: true, errorKind, at: Date.now() };
        updateActive((c) => ({ ...c, messages: [...c.messages, assistant] }));
      }
    } finally {
      sendingRef.current = false;
      setLoading(false);
    }
  }

  async function handleLogout() {
    await signOut();
    setGuestUsed(0);
    saveGuestUsed(0);
  }

  return (
    <div className={`app${navOpen ? " nav-open" : ""}`}>
      <div className="backdrop" aria-hidden="true" onClick={() => setNavOpen(false)} />
      <Sidebar
        conversations={conversations}
        activeId={activeId}
        onNew={handleNew}
        onSelect={handleSelect}
        onRename={handleRename}
        onDelete={handleDelete}
        isSignedIn={isSignedIn}
        email={email}
        guestUsed={guestUsed}
        guestLimit={GUEST_LIMIT}
        dailyUsed={dailyUsed}
        dailyLimit={DAILY_LIMIT}
        onLogin={() => openLogin(`Giriş yaparak günlük ${DAILY_LIMIT} mesaj hakkı kazan.`)}
        onLogout={handleLogout}
      />
      <ChatArea
        conversation={active}
        loading={loading}
        onSend={handleSend}
        onRetry={(errorIndex, operator, lineType) => {
          // The failed question is the user turn right before its error bubble.
          const question = active?.messages[errorIndex - 1];
          if (question?.role === "user") handleSend(question.content, operator, lineType, errorIndex - 1);
        }}
        onToggleNav={() => setNavOpen((v) => !v)}
        onNew={handleNew}
      />
      <LoginModal
        open={loginOpen}
        reason={loginReason}
        onClose={() => setLoginOpen(false)}
      />
    </div>
  );
}
