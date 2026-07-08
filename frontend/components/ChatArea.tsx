"use client";

import { useEffect, useRef, useState } from "react";
import type { Conversation } from "@/lib/types";
import MessageBubble from "./MessageBubble";
import OperatorPicker, { OperatorKey } from "./OperatorPicker";

const SAMPLES = [
  "20 GB paketleri karşılaştır",
  "En uygun fiyatlı paketi bul",
  "Operatörleri karşılaştır",
  "En çok dakika veren paket",
];

interface Props {
  conversation: Conversation | null;
  loading: boolean;
  onSend: (question: string, operator: OperatorKey) => void;
  onToggleNav: () => void;
}

export default function ChatArea({ conversation, loading, onSend, onToggleNav }: Props) {
  const [text, setText] = useState("");
  const [operator, setOperator] = useState<OperatorKey>("all");
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
    onSend(q, operator);
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
              <h1>
                <span className="a">Tarife</span>
                <span className="b">AI</span>'ya hoş geldiniz
              </h1>
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
            </div>
          ) : (
            <>
              {messages.map((m, i) => (
                <MessageBubble key={i} message={m} />
              ))}
              {loading && (
                <div className="row assistant">
                  <div className="bubble assistant">
                    <div className="thinking">
                      <span className="thinking-label">TarifeAI düşünüyor</span>
                      <span className="typing">
                        <span />
                        <span />
                        <span />
                      </span>
                    </div>
                  </div>
                </div>
              )}
            </>
          )}
          <div ref={endRef} />
        </div>
      </div>

      <div className="composer">
        <div className="composer-inner">
          {isEmpty && (
            <div className="samples">
              {SAMPLES.map((s) => (
                <button key={s} className="chip" onClick={() => submit(s)}>
                  {s}
                </button>
              ))}
            </div>
          )}
          <div className="input-bar">
            <OperatorPicker value={operator} onChange={setOperator} />
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
