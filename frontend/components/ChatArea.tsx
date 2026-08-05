"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import type { Conversation } from "@/lib/types";
import MessageBubble from "./MessageBubble";
import LoadingSkeleton from "./LoadingSkeleton";
import OperatorPicker, { LineTypeKey, OperatorKey } from "./OperatorPicker";

// Starter questions shown in the empty state. Each maps to a real retrieval intent
// (internet / budget / youth group / number-transfer campaigns) so the first answer
// lands on actual data. Icon is inline SVG (stroke style, matches the send button).
const SAMPLES: { q: string; icon: ReactNode }[] = [
  {
    q: "En çok internet veren paket hangisi?",
    icon: (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M3 20h.01M7 20v-4M12 20v-9M17 20V6" />
      </svg>
    ),
  },
  {
    q: "150 TL altı en uygun paketler",
    icon: (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M3 7h15a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h12" />
        <path d="M16 12.5h4v3h-4a1.5 1.5 0 0 1 0-3z" />
      </svg>
    ),
  },
  {
    q: "Gençlere özel avantajlı paketler",
    icon: (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M12 4 2 9l10 5 10-5-10-5z" />
        <path d="M6 11v4c0 1.1 2.7 2.5 6 2.5s6-1.4 6-2.5v-4" />
      </svg>
    ),
  },
  {
    q: "Numara taşıyana özel kampanyalar",
    icon: (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M7 4v13M7 4 3 8M7 4l4 4" />
        <path d="M17 20V7M17 20l4-4M17 20l-4-4" />
      </svg>
    ),
  },
];

interface Props {
  conversation: Conversation | null;
  loading: boolean;
  onSend: (question: string, operator: OperatorKey, lineType: LineTypeKey) => void;
  onToggleNav: () => void;
}

export default function ChatArea({ conversation, loading, onSend, onToggleNav }: Props) {
  const [text, setText] = useState("");
  const [operator, setOperator] = useState<OperatorKey>("all");
  const [lineType, setLineType] = useState<LineTypeKey>("all");
  const endRef = useRef<HTMLDivElement>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);

  const messages = conversation?.messages ?? [];
  const isEmpty = messages.length === 0;

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length, loading]);

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
    el.style.height = `${Math.min(el.scrollHeight, 140)}px`;
  }

  return (
    <section className="chat">
      <button className="sidebar-toggle" onClick={onToggleNav} aria-label="Menü">
        ☰
      </button>

      <div className="messages">
        <div className="messages-inner">
          {isEmpty ? (
            <div className="empty">
              <div className="welcome-card">
                <h1>
                  <span className="a">Tarife</span>
                  <span className="b">AI</span>'ya hoş geldiniz
                </h1>
                <p className="welcome-sub">
                  Turkcell, Vodafone ve Türk Telekom&apos;un faturalı ve faturasız
                  paketlerini yapay zeka ile karşılaştır, sana en uygun olanı
                  saniyeler içinde bul.
                </p>
              </div>
              <div className="how-it-works">
                <span className="title">Nasıl Çalışır?</span>
                <ol>
                  <li>
                    <span className="num">1</span>
                    <div>
                      <strong>Kriter Belirleyin:</strong> İstediğiniz paket kriterlerini yazın.
                    </div>
                  </li>
                  <li>
                    <span className="num">2</span>
                    <div>
                      <strong>Filtreleyin (İsteğe Bağlı):</strong> Sol alttaki butondan operatör seçimi yapın.
                    </div>
                  </li>
                  <li>
                    <span className="num">3</span>
                    <div>
                      <strong>Karşılaştırın:</strong> Yapay zeka en güncel ve doğrulanabilir paketleri anında listelesin.
                    </div>
                  </li>
                </ol>
              </div>
              <div className="samples-grid">
                {SAMPLES.map((s) => (
                  <button key={s.q} className="sample-card" onClick={() => submit(s.q)}>
                    <span className="sample-icon">{s.icon}</span>
                    <span>{s.q}</span>
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <>
              {messages.map((m, i) => (
                <MessageBubble key={i} message={m} />
              ))}
              {loading && <LoadingSkeleton />}
            </>
          )}
          <div ref={endRef} />
        </div>
      </div>

      <div className="composer">
        <div className="composer-inner">
          <div className="input-bar">
            <OperatorPicker
              value={operator}
              onChange={setOperator}
              lineType={lineType}
              onLineTypeChange={setLineType}
            />
            <textarea
              ref={taRef}
              rows={1}
              value={text}
              placeholder="Bir soru sorun…"
              onChange={onInput}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  submit();
                }
              }}
            />
            <button
              className="icon-btn send-btn"
              onClick={() => submit()}
              disabled={loading || !text.trim()}
              aria-label="Gönder"
            >
              <svg width="22" height="22" viewBox="0 0 24 24" aria-hidden="true">
                <path d="M12 19V5" />
                <path d="M6 11l6-6 6 6" />
              </svg>
            </button>
          </div>
          <div className="disclaimer">
            TarifeAI bir yapay zeka asistanıdır. Paket bilgileri değişebilir; kesin
            koşullar için operatörün resmi sayfasını kontrol edin.
          </div>
        </div>
      </div>
    </section>
  );
}
