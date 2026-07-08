"use client";

import { useState } from "react";
import type { Conversation } from "@/lib/types";

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
  onLogin,
  onLogout,
}: Props) {
  const [menuId, setMenuId] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draftTitle, setDraftTitle] = useState("");

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

  return (
    <aside className="sidebar">
      <div className="sidebar-head">
        <div className="wordmark">
          <span className="a">Tarife</span>
          <span className="b">AI</span>
        </div>
      </div>

      <button className="new-btn" onClick={onNew}>
        <span>＋</span> Yeni Sohbet
      </button>

      <div className="conv-label">Sohbetler</div>
      <div className="conv-list">
        {conversations.length === 0 && (
          <div className="conv-preview" style={{ padding: "8px 12px" }}>
            Henüz sohbet yok.
          </div>
        )}
        {conversations.map((c) => (
          <div
            key={c.id}
            className={`conv${c.id === activeId ? " active" : ""}`}
            onClick={() => onSelect(c.id)}
          >
            {editingId === c.id ? (
              <input
                className="conv-title-input"
                value={draftTitle}
                autoFocus
                onClick={(e) => e.stopPropagation()}
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
              <div className="conv-title">{c.title}</div>
            )}
            <button
              className="conv-menu-btn"
              title="Sohbet seçenekleri"
              aria-label="Sohbet seçenekleri"
              aria-expanded={menuId === c.id}
              onClick={(e) => {
                e.stopPropagation();
                setMenuId((id) => (id === c.id ? null : c.id));
              }}
            >
              <svg className="dots-icon" viewBox="0 0 24 24" aria-hidden="true">
                <circle cx="5" cy="12" r="2.3" />
                <circle cx="12" cy="12" r="2.3" />
                <circle cx="19" cy="12" r="2.3" />
              </svg>
            </button>
            {menuId === c.id && (
              <div className="conv-menu" onClick={(e) => e.stopPropagation()}>
                <button type="button" onClick={() => startRename(c)}>
                  <svg className="menu-icon" viewBox="0 0 24 24" aria-hidden="true">
                    <path d="M4 20h4.2L18.9 9.3l-4.2-4.2L4 15.8V20z" />
                    <path d="M13.7 6.1l4.2 4.2" />
                  </svg>
                  Yeniden adlandır
                </button>
                <button
                  type="button"
                  className="danger"
                  onClick={() => {
                    setMenuId(null);
                    onDelete(c.id);
                  }}
                >
                  <svg className="menu-icon" viewBox="0 0 24 24" aria-hidden="true">
                    <path d="M6 6l12 12M18 6L6 18" />
                  </svg>
                  Sil
                </button>
              </div>
            )}
          </div>
        ))}
      </div>

      <div className="sidebar-foot">
        <div className="avatar">{isSignedIn && email ? email[0].toUpperCase() : "U"}</div>
        <div className="foot-main">
          {isSignedIn ? (
            <>
              <div className="foot-name truncate" title={email ?? undefined}>
                {email ?? "Hesabım"}
              </div>
              <div className="foot-sub">Giriş yapıldı</div>
            </>
          ) : (
            <>
              <div className="foot-name">Misafir Kullanıcı</div>
              <div className="guest-meter">
                <b>{Math.max(0, guestLimit - guestUsed)}</b> / {guestLimit} Ücretsiz Mesaj
              </div>
            </>
          )}
        </div>
        {isSignedIn ? (
          <button className="foot-action" onClick={onLogout}>
            Çıkış
          </button>
        ) : (
          <button className="foot-action" onClick={onLogin}>
            Giriş yap
          </button>
        )}
      </div>
    </aside>
  );
}
