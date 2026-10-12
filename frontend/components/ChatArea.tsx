"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useCatalogCheck } from "@/lib/catalogCheck";
import type { Conversation } from "@/lib/types";
import MessageBubble from "./MessageBubble";
import LoadingSkeleton from "./LoadingSkeleton";
import OperatorPicker, { LineTypeKey, OperatorKey } from "./OperatorPicker";

// Starter questions. Each maps to a real retrieval intent (internet amount, budget,
// audience, number porting) so the first answer lands on actual catalog data.
const SAMPLES: { q: string; hint: string; icon: string }[] = [
  { q: "30 GB üstü faturasız paketler", hint: "İnternete göre", icon: "M3 20h.01M8 20v-5M13 20v-9M18 20V5" },
  { q: "1000 TL altı faturalı tarifeler", hint: "Bütçeye göre", icon: "M4 7h14a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h11M16 13h.01" },
  { q: "Gençlere özel paketler", hint: "Yaşa özel", icon: "M12 4 2 9l10 5 10-5-10-5zM6 11v4c0 1.1 2.7 2.5 6 2.5s6-1.4 6-2.5v-4" },
  { q: "Numara taşıma kampanyaları", hint: "Operatör değiştirenlere", icon: "M7 4v13M7 4 3 8M7 4l4 4M17 20V7M17 20l4-4M17 20l-4-4" },
]

const OPERATORS = [
  { key: "turkcell", label: "Turkcell" },
  { key: "vodafone", label: "Vodafone" },
  { key: "turk_telekom", label: "Türk Telekom" },
]

interface Props {
  conversation: Conversation | null;
  loading: boolean;
  onSend: (question: string, operator: OperatorKey, lineType: LineTypeKey) => void;
  onRetry: (errorIndex: number, operator: OperatorKey, lineType: LineTypeKey) => void;
  onToggleNav: () => void;
  onNew: () => void;
}

function prefersReducedMotion(): boolean {
  return typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

export default function ChatArea({ conversation, loading, onSend, onRetry, onToggleNav, onNew }: Props) {
  const [text, setText] = useState("");
  const [operator, setOperator] = useState<OperatorKey>("all");
  const [lineType, setLineType] = useState<LineTypeKey>("all");
  const scrollRef = useRef<HTMLDivElement>(null);
  const lastRef = useRef<HTMLDivElement>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);
  const catalog = useCatalogCheck(conversation);

  const messages = conversation?.messages ?? [];
  const isEmpty = messages.length === 0;
  const last = messages[messages.length - 1];

  // Opening a conversation starts at its latest message.
  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [conversation?.id]);

  // A sent question scrolls to the bottom (the loading placeholder); a new answer
  // scrolls to its *start*, so a long answer is read from the top, not the end.
  useEffect(() => {
    const behavior: ScrollBehavior = prefersReducedMotion() ? "auto" : "smooth";
    if (loading || last?.role === "user") {
      const el = scrollRef.current;
      el?.scrollTo({ top: el.scrollHeight, behavior });
    } else if (last?.role === "assistant") {
      // Scroll only the message list: scrollIntoView would also scroll the page
      // itself, shifting the whole layout (sidebar included) upward.
      const el = scrollRef.current;
      const lastEl = lastRef.current;
      if (el && lastEl) {
        const top = lastEl.getBoundingClientRect().top - el.getBoundingClientRect().top + el.scrollTop;
        el.scrollTo({ top: top - parseFloat(getComputedStyle(el).scrollPaddingTop || "0"), behavior });
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messages.length, loading]);

  const composingRef = useRef(false);

  function submit(value?: string) {
    const q = (value ?? text).trim();
    if (!q || loading) return;
    onSend(q, operator, lineType);
    setText("");
    if (taRef.current) taRef.current.style.height = "auto";
  }

  function onInput(e: React.ChangeEvent<HTMLTextAreaElement>) {
    setText(e.target.value);
    const el = e.target;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }

  return (
    <main className="chat">
      <header className="topbar">
        <button type="button" className="icon-btn ghost" onClick={onToggleNav} aria-label="Sohbetleri aç">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M4 7h16M4 12h16M4 17h16" />
          </svg>
        </button>
        <span className="wordmark">
          Tarife<span>AI</span>
        </span>
        <button type="button" className="icon-btn ghost" onClick={onNew} aria-label="Yeni sohbet">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M12 5v14M5 12h14" />
          </svg>
        </button>
      </header>

      <div className="messages" ref={scrollRef}>
        <div className="messages-inner">
          {isEmpty ? (
            <div className="empty">
              <h1>
                Sana en uygun tarifeyi <span className="accent">birlikte</span> bulalım
              </h1>
              <p className="empty-sub">İhtiyacını yaz, tarifeleri fiyat, internet ve koşullarıyla karşılaştırayım.</p>
              <ul className="hero-ops" aria-label="Karşılaştırılan operatörler">
                {OPERATORS.map((o) => (
                  <li key={o.key}>
                    <span className={`op-dot op-${o.key}`} aria-hidden="true" />
                    {o.label}
                  </li>
                ))}
              </ul>
              <ul className="samples" aria-label="Örnek sorular">
                {SAMPLES.map((s) => (
                  <li key={s.q}>
                    <button type="button" className="sample" onClick={() => submit(s.q)}>
                      <span className="sample-icon" aria-hidden="true">
                        <svg viewBox="0 0 24 24">
                          <path d={s.icon} />
                        </svg>
                      </span>
                      <span className="sample-text">
                        <span className="sample-q">{s.q}</span>
                        <span className="sample-hint">{s.hint}</span>
                      </span>
                      <svg className="sample-arrow" viewBox="0 0 24 24" aria-hidden="true">
                        <path d="M5 12h14M13 6l6 6-6 6" />
                      </svg>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ) : (
            <div role="log" aria-live="polite" aria-relevant="additions" aria-label="Sohbet">
              {messages.map((m, i) => (
                <div key={i} ref={i === messages.length - 1 ? lastRef : undefined} className="msg">
                  <MessageBubble
                    message={m}
                    catalog={catalog}
                    onRetry={
                      m.error && m.errorKind !== "quota" && m.errorKind !== "auth" && i === messages.length - 1 && !loading
                        ? () => onRetry(i, operator, lineType)
                        : undefined
                    }
                  />
                </div>
              ))}
              {loading && <LoadingSkeleton />}
            </div>
          )}
        </div>
      </div>

      <div className="composer">
        <form
          className="composer-inner"
          onSubmit={(e) => {
            e.preventDefault();
            submit();
          }}
        >
          <div className="input-bar">
            <OperatorPicker
              value={operator}
              onChange={setOperator}
              lineType={lineType}
              onLineTypeChange={setLineType}
            />
            <label htmlFor="composer-input" className="sr-only">
              Sorunu yaz
            </label>
            <textarea
              id="composer-input"
              ref={taRef}
              rows={1}
              value={text}
              placeholder="Nasıl bir tarife arıyorsun?"
              onChange={onInput}
              enterKeyHint="send"
              onCompositionStart={() => (composingRef.current = true)}
              onCompositionEnd={() => (composingRef.current = false)}
              onKeyDown={(e) => {
                // Only skip Enter while an IME is actively composing (e.g. CJK input); keyCode 229 covers Safari.
                if (e.key === "Enter" && !e.shiftKey && !composingRef.current && e.keyCode !== 229) {
                  e.preventDefault();
                  submit();
                }
              }}
            />
            <button type="submit" className="icon-btn send-btn" disabled={loading || !text.trim()} aria-label="Gönder">
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M12 19V5" />
                <path d="M6 11l6-6 6 6" />
              </svg>
            </button>
          </div>
          <p className="disclaimer">
            Yalnızca yeni müşteri tarifeleri. Koşullar değişebilir; başvurmadan önce operatörün resmi sayfasını kontrol et.
          </p>
        </form>
      </div>
    </main>
  );
}
