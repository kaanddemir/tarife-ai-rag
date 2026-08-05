"use client";

import { useState } from "react";
import { useAuth } from "@/lib/auth";

interface Props {
  open: boolean;
  onClose: () => void;
  /** Optional context line, e.g. why the modal appeared (guest limit reached). */
  reason?: string;
}

export default function LoginModal({ open, onClose, reason }: Props) {
  const { signInWithEmail } = useAuth();
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!open) return null;

  async function onEmail(e: React.FormEvent) {
    e.preventDefault();
    const value = email.trim();
    if (!value || busy) return;
    setBusy(true);
    setError(null);
    try {
      await signInWithEmail(value);
      setSent(true);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(
        /rate limit/i.test(msg)
          ? "Çok fazla deneme yapıldı. Birkaç dakika sonra tekrar dene."
          : `Bağlantı gönderilemedi: ${msg}`,
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-backdrop" onClick={onClose}>
      <div
        className="login-modal"
        role="dialog"
        aria-modal="true"
        aria-label="Giriş yap"
        onClick={(e) => e.stopPropagation()}
      >
        <button className="login-close" onClick={onClose} aria-label="Kapat">
          ✕
        </button>

        <div className="login-brand">
          <span className="a">Tarife</span>
          <span className="b">AI</span>
        </div>

        <h2 className="login-title">Devam etmek için giriş yap</h2>
        <p className="login-sub">
          {reason ??
            "Ücretsiz mesaj hakkın doldu. E-postana gelecek giriş linkiyle sohbete devam edebilirsin."}
        </p>

        {sent ? (
          <div className="login-sent">
            <strong>Bağlantı gönderildi.</strong>
            <span>
              {email} adresine gelen giriş linkine tıklayarak devam et.
            </span>
          </div>
        ) : (
          <>
            <form className="login-email" onSubmit={onEmail}>
              <input
                type="email"
                inputMode="email"
                autoComplete="email"
                placeholder="E-posta adresin"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
              />
              <button type="submit" disabled={busy || !email.trim()}>
                {busy ? "Gönderiliyor…" : "Giriş Linki Gönder"}
              </button>
            </form>

            {error && <div className="login-error">{error}</div>}
          </>
        )}
      </div>
    </div>
  );
}
