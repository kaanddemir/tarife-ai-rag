"use client";

import { useState } from "react";
import { OPERATOR_LABELS, type Citation } from "@/lib/api";

interface Props {
  citations: Citation[];
  copyText?: string;
}

function operatorLabel(operator: string): string {
  return OPERATOR_LABELS[operator] ?? operator;
}

function siteName(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return "resmi kaynak";
  }
}

export default function CitationSources({ citations, copyText }: Props) {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);

  if (citations.length === 0 && !copyText) return null;

  function cleanCopyText(text: string): string {
    return text
      .replace(/\s*\[S\d+\]/g, "")
      .replace(/[ \t]+\n/g, "\n")
      .replace(/\n{3,}/g, "\n\n")
      .trim();
  }

  async function copyAnswer() {
    if (!copyText) return;
    await navigator.clipboard.writeText(cleanCopyText(copyText));
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1400);
  }

  return (
    <div className="sources">
      <div className="sources-actions">
        {citations.length > 0 && (
          <button
            type="button"
            className="sources-toggle"
            aria-expanded={open}
            onClick={() => setOpen((v) => !v)}
          >
            <svg className="action-icon" viewBox="0 0 24 24" aria-hidden="true">
              <path d="M4 6.5A2.5 2.5 0 0 1 6.5 4h11A2.5 2.5 0 0 1 20 6.5v11a2.5 2.5 0 0 1-2.5 2.5h-11A2.5 2.5 0 0 1 4 17.5v-11z" />
              <path d="M8 9h8M8 12h8M8 15h5" />
            </svg>
            {open ? "Kaynakları gizle" : `Kaynakları göster (${citations.length})`}
          </button>
        )}

        {copyText && (
          <button type="button" className="copy-btn" onClick={copyAnswer}>
            <svg className="action-icon" viewBox="0 0 24 24" aria-hidden="true">
              {copied ? (
                <path d="M5 12.5l4 4L19 7" />
              ) : (
                <>
                  <path d="M8 8h9a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2v-9a2 2 0 0 1 2-2z" />
                  <path d="M5 16H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
                </>
              )}
            </svg>
            {copied ? "Kopyalandı" : "Kopyala"}
          </button>
        )}
      </div>

      {open && (
        <div className="cite-list">
          {citations.map((c, index) => (
            <a
              key={c.id}
              className="cite-card"
              href={c.source_url}
              target="_blank"
              rel="noreferrer"
            >
              <span className="cite-num">{index + 1}</span>
              <span className="cite-body">
                <span className="cite-name">{c.name}</span>
                <span className="cite-meta">
                  <span className="cite-op">{operatorLabel(c.operator)}</span>
                  <span className="cite-sep">·</span>
                  <span className="cite-site">{siteName(c.source_url)}</span>
                </span>
              </span>
              <svg
                className="cite-ext"
                viewBox="0 0 24 24"
                aria-hidden="true"
              >
                <path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5" />
              </svg>
            </a>
          ))}
        </div>
      )}
    </div>
  );
}
