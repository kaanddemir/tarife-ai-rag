"use client";

import { useEffect, useState } from "react";
import { chat, OPERATOR_LABELS, QuotaError, type ChatTurn } from "@/lib/api";
import type { Conversation, Message } from "@/lib/types";
import {
  loadConversations,
  saveConversations,
  loadActiveId,
  saveActiveId,
  loadGuestUsed,
  saveGuestUsed,
  newConversation,
  titleFrom,
} from "@/lib/storage";
import { useAuth } from "@/lib/auth";
import type { OperatorKey } from "@/components/OperatorPicker";
import Sidebar from "@/components/Sidebar";
import ChatArea from "@/components/ChatArea";
import LoginModal from "@/components/LoginModal";

const GUEST_LIMIT = 5;

export default function Home() {
  const { isSignedIn, email, getAccessToken, signOut } = useAuth();
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [navOpen, setNavOpen] = useState(false);
  const [hydrated, setHydrated] = useState(false);
  const [guestUsed, setGuestUsed] = useState(0);
  const [loginOpen, setLoginOpen] = useState(false);
  const [loginReason, setLoginReason] = useState<string | undefined>();

  // Load from localStorage on mount.
  useEffect(() => {
    setGuestUsed(loadGuestUsed());
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
    setConversations((prev) => {
      const next = prev.filter((c) => c.id !== id);
      if (id === activeId) {
        if (next.length) setActiveId(next[0].id);
        else {
          const c = newConversation();
          setActiveId(c.id);
          return [c];
        }
      }
      return next;
    });
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

  async function handleSend(question: string, operator: OperatorKey) {
    if (!activeId) return;

    // Pre-empt the guest cap for snappy UX; the backend still enforces the real limit.
    if (!isSignedIn && guestUsed >= GUEST_LIMIT) {
      openLogin();
      return;
    }

    const userMsg: Message = { role: "user", content: question };
    const history: ChatTurn[] = (active?.messages ?? [])
      .slice(-5)
      .map((m) => ({ role: m.role, content: m.content }));

    updateActive((c) => ({
      ...c,
      title: c.messages.length === 0 ? titleFrom(question) : c.title,
      messages: [...c.messages, userMsg],
    }));
    setLoading(true);

    // Scope to an operator by prepending its name — backend retrieve() detects it.
    const scoped =
      operator === "all" ? question : `${OPERATOR_LABELS[operator]} ${question}`;

    try {
      const token = await getAccessToken();
      const r = await chat(scoped, history, token);
      const assistant: Message = {
        role: "assistant",
        content: r.answer,
        citations: r.citations,
        packages: r.packages,
      };
      updateActive((c) => ({ ...c, messages: [...c.messages, assistant] }));
      if (!isSignedIn) {
        setGuestUsed((n) => {
          const next = n + 1;
          saveGuestUsed(next);
          return next;
        });
      }
    } catch (e) {
      if (e instanceof QuotaError) {
        if (e.reason === "daily_limit") {
          const assistant: Message = {
            role: "assistant",
            content:
              "Günlük mesaj limitine ulaştın. Yarın tekrar deneyebilirsin.",
            error: true,
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
        const assistant: Message = {
          role: "assistant",
          content: `Yanıt alınamadı: ${(e as Error).message}. (Backend çalışıyor mu ve PROVIDER_API_KEY ayarlı mı?)`,
          error: true,
        };
        updateActive((c) => ({ ...c, messages: [...c.messages, assistant] }));
      }
    } finally {
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
      <div className="backdrop" onClick={() => setNavOpen(false)} />
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
        onLogin={() => openLogin("Giriş yaparak günlük 20 mesaj hakkı kazan.")}
        onLogout={handleLogout}
      />
      <ChatArea
        conversation={active}
        loading={loading}
        onSend={handleSend}
        onToggleNav={() => setNavOpen((v) => !v)}
      />
      <LoginModal
        open={loginOpen}
        reason={loginReason}
        onClose={() => setLoginOpen(false)}
      />
    </div>
  );
}
