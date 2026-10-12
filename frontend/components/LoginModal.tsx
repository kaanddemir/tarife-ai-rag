"use client";

import { useEffect, useState } from "react";
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

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

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
          : "Bağlantı gönderilemedi. E-posta adresini kontrol edip tekrar dene.",
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
        aria-labelledby="login-title"
        onClick={(e) => e.stopPropagation()}
      >
        <button type="button" className="icon-btn ghost login-close" onClick={onClose} aria-label="Kapat">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M6 6l12 12M18 6L6 18" />
          </svg>
        </button>

        <span className="wordmark">
          Tarife<span>AI</span>
        </span>

        <h2 id="login-title" className="login-title">Devam etmek için giriş yap</h2>
        <p className="login-sub">
          {reason ??
            "Ücretsiz mesaj hakkın doldu. E-postana gelecek giriş linkiyle sohbete devam edebilirsin."}
        </p>

        {sent ? (
          <div className="login-sent" role="status">
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
                aria-label="E-posta adresin"
                autoFocus
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
              />
              <button type="submit" className="btn btn-primary" disabled={busy || !email.trim()}>
                {busy ? "Gönderiliyor…" : "Giriş Linki Gönder"}
              </button>
            </form>

            {error && (
              <p className="login-error" role="alert">
                {error}
              </p>
            )}
          </>
        )}
      </div>
    </div>
  );
}
