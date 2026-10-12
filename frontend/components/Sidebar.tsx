"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { Conversation } from "@/lib/types";
import ThemeToggle from "./ThemeToggle";

interface Props {
  conversations: Conversation[];
  activeId: string | null;
  onNew: () => void;
  onSelect: (id: string) => void;
  onRename: (id: string, title: string) => void;
  onDelete: (id: string) => void;
  isSignedIn: boolean;
  email: string | null;
  guestUsed: number;
  guestLimit: number;
  dailyUsed: number;
  dailyLimit: number;
  onLogin: () => void;
  onLogout: () => void;
}

export default function Sidebar({
  conversations,
  activeId,
  onNew,
  onSelect,
  onRename,
  onDelete,
  isSignedIn,
  email,
  guestUsed,
  guestLimit,
  dailyUsed,
  dailyLimit,
  onLogin,
  onLogout,
}: Props) {
  const [menuId, setMenuId] = useState<string | null>(null);
  // The conversation awaiting delete confirmation, captured when "Sil" is chosen
  // so the dialog always acts on that id, not on whatever is active later.
  const [pendingDelete, setPendingDelete] = useState<Conversation | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draftTitle, setDraftTitle] = useState("");

  // An open menu closes on Escape or a click anywhere else.
  useEffect(() => {
    if (!menuId) return;
    const close = () => setMenuId(null);
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && close();
    document.addEventListener("click", close);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("click", close);
      document.removeEventListener("keydown", onKey);
    };
  }, [menuId]);

  function startRename(conversation: Conversation) {
    setEditingId(conversation.id);
    setDraftTitle(conversation.title);
    setMenuId(null);
  }

  function finishRename(id: string) {
    onRename(id, draftTitle);
    setEditingId(null);
    setDraftTitle("");
  }

  const remaining = isSignedIn ? Math.max(0, dailyLimit - dailyUsed) : Math.max(0, guestLimit - guestUsed);
  const limit = isSignedIn ? dailyLimit : guestLimit;

  return (
    <aside className="sidebar" aria-label="Sohbetler">
      <div className="sidebar-head">
        <span className="wordmark">
          Tarife<span>AI</span>
        </span>
      </div>

      <button type="button" className="btn btn-primary new-btn" onClick={onNew}>
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M12 5v14M5 12h14" />
        </svg>
        Yeni sohbet
      </button>

      <h2 className="conv-label">Sohbetler</h2>
      <ul className="conv-list">
        {conversations.map((c) => (
          <li key={c.id} className={`conv${c.id === activeId ? " active" : ""}`}>
            {editingId === c.id ? (
              <input
                className="conv-title-input"
                aria-label="Sohbet adı"
                value={draftTitle}
                autoFocus
                onChange={(e) => setDraftTitle(e.target.value)}
                onBlur={() => finishRename(c.id)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") finishRename(c.id);
                  if (e.key === "Escape") {
                    setEditingId(null);
                    setDraftTitle("");
                  }
                }}
              />
            ) : (
              <button
                type="button"
                className="conv-select"
                aria-current={c.id === activeId ? "page" : undefined}
                onClick={() => onSelect(c.id)}
              >
                {c.title}
              </button>
            )}
            <button
              type="button"
              className="conv-menu-btn"
              aria-label={`"${c.title}" seçenekleri`}
              aria-haspopup="menu"
              aria-expanded={menuId === c.id}
              onClick={(e) => {
                e.stopPropagation();
                setMenuId((id) => (id === c.id ? null : c.id));
              }}
            >
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <circle cx="5" cy="12" r="1.6" />
                <circle cx="12" cy="12" r="1.6" />
                <circle cx="19" cy="12" r="1.6" />
              </svg>
            </button>
            {menuId === c.id && (
              <div className="conv-menu" role="menu" onClick={(e) => e.stopPropagation()}>
                <button type="button" role="menuitem" onClick={() => startRename(c)}>
                  Yeniden adlandır
                </button>
                <button
                  type="button"
                  role="menuitem"
                  className="danger"
                  onClick={() => {
                    setMenuId(null);
                    setPendingDelete(c);
                  }}
                >
                  Sil
                </button>
              </div>
            )}
          </li>
        ))}
      </ul>

      <div className="auth-row">
      <button
        type="button"
        className="auth-btn"
        onClick={isSignedIn ? onLogout : onLogin}
      >
        <svg viewBox="0 0 24 24" aria-hidden="true">
          {isSignedIn ? (
            <>
              <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
              <path d="M16 17l5-5-5-5" />
              <path d="M21 12H9" />
            </>
          ) : (
            <>
              <path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4" />
              <path d="M10 17l5-5-5-5" />
              <path d="M15 12H3" />
            </>
          )}
        </svg>
        {isSignedIn ? "Çıkış yap" : "Giriş yap"}
      </button>
      <ThemeToggle />
      </div>

      <div className="sidebar-foot">
        <span className="avatar" aria-hidden="true">
          {isSignedIn && email ? email[0].toUpperCase() : "M"}
        </span>
        <div className="foot-main">
          <span className="foot-name" title={isSignedIn ? (email ?? undefined) : undefined}>
            {isSignedIn ? (email ? email.split("@")[0] : "Hesabım") : "Misafir"}
          </span>
          <span className="meter">
            {remaining}/{limit} Mesaj
            <span className="sr-only"> hakkı kaldı{isSignedIn ? " (bugün)" : ""}</span>
          </span>
          <span className="meter-bar" aria-hidden="true">
            <span style={{ width: `${(remaining / limit) * 100}%` }} />
          </span>
        </div>
      </div>
      {pendingDelete && (
        <ConfirmDelete
          onCancel={() => setPendingDelete(null)}
          onConfirm={() => {
            const id = pendingDelete.id;
            setPendingDelete(null);
            onDelete(id);
          }}
        />
      )}
    </aside>
  );
}

function ConfirmDelete({ onCancel, onConfirm }: { onCancel: () => void; onConfirm: () => void }) {
  const cancelRef = useRef<HTMLButtonElement>(null);
  const onCancelRef = useRef(onCancel);
  onCancelRef.current = onCancel;

  // Focus the safe choice; Escape cancels; focus returns to where it was.
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    cancelRef.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onCancelRef.current();
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      previous?.focus?.();
    };
  }, []);

  // Portal: on mobile the sidebar is a transformed drawer, which would otherwise
  // trap this fixed-position dialog inside it.
  return createPortal(
    <div className="login-backdrop" onClick={onCancel}>
      <div
        className="login-modal confirm-modal"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="confirm-title"
        aria-describedby="confirm-desc"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 id="confirm-title" className="confirm-title">Sohbet silinsin mi?</h2>
        <p id="confirm-desc" className="confirm-desc">Bu işlem geri alınamaz.</p>
        <div className="confirm-actions">
          <button ref={cancelRef} type="button" className="btn" onClick={onCancel}>
            Vazgeç
          </button>
          <button type="button" className="btn btn-danger" onClick={onConfirm}>
            Sil
          </button>
        </div>
      </div>
    </div>,
    document.body
  );
}
