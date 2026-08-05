"use client";

import type { CSSProperties } from "react";
import {
  Package,
  OPERATOR_LABELS,
  PERIOD_LABELS,
  UNLIMITED,
  fmtAmount,
} from "@/lib/api";

function gbLabel(gb: number, suffix: string): string {
  return gb === UNLIMITED ? `Sınırsız ${suffix}` : `${gb} GB ${suffix}`;
}

// Show the general internet allowance as the hero figure and any add-on quotas
// (social / bonus) as separate chips, instead of collapsing everything into a
// single total — a 100 GB total made of 50 general + 50 social reads as
// "50 GB internet" + "50 GB Sosyal Medya".
function internetBreakdown(p: Package): { hero: number | null; parts: string[] } {
  const hero = p.internet_gb;

  const parts: string[] = [];
  if (p.social_media_gb !== null && p.social_media_gb !== 0)
    parts.push(gbLabel(p.social_media_gb, "Sosyal Medya"));
  if (p.bonus_internet_gb !== null && p.bonus_internet_gb !== 0)
    parts.push(gbLabel(p.bonus_internet_gb, "Bonus"));

  return { hero, parts };
}

export default function ComparisonTable({ packages }: { packages: Package[] }) {
  if (!packages.length) return null;

  return (
    <div className="table-card">
      <h3>Karşılaştırma</h3>
      <div className="cmp-grid">
        {packages.map((p, index) => {
          const { hero, parts } = internetBreakdown(p);
          const period = p.price_period ? PERIOD_LABELS[p.price_period] : undefined;
          const recommended = index === 0;

          // No fill — only the border carries the operator's brand colour.
          const tint = {
            borderColor: `color-mix(in srgb, var(--${p.operator}) 65%, var(--border))`,
          } as CSSProperties;

          return (
            <article
              key={p.id}
              className="cmp-card"
              style={tint}
            >
              <div className="cmp-top">
                <span className="cmp-op">
                  {OPERATOR_LABELS[p.operator] ?? p.operator}
                </span>
                {recommended && (
                  <span className="tag-recommended" title="Önerilen" aria-label="Önerilen">
                    <svg viewBox="0 0 24 24" aria-hidden="true">
                      <path d="M12 3.8l2.4 4.9 5.4.8-3.9 3.8.9 5.4L12 16.1l-4.8 2.6.9-5.4-3.9-3.8 5.4-.8L12 3.8z" />
                    </svg>
                    Önerilen
                  </span>
                )}
              </div>

              <a
                href={p.source_url}
                target="_blank"
                rel="noreferrer"
                className="cmp-name"
              >
                {p.name}
              </a>

              <div className="cmp-hero">
                <strong>
                  {hero === null ? "—" : hero === UNLIMITED ? "Sınırsız" : `${hero} GB`}
                </strong>
                <span>internet</span>
              </div>
              {/* Always render so the spec row below lines up across cards. */}
              <div className="cmp-chips">
                {parts.map((part) => (
                  <span key={part} className="cmp-chip">
                    {part}
                  </span>
                ))}
              </div>

              <div className="cmp-specs">
                <span>
                  <svg viewBox="0 0 24 24" aria-hidden="true">
                    <path d="M6.6 10.8a15 15 0 0 0 6.6 6.6l2.2-2.2c.3-.3.7-.4 1-.2 1.1.4 2.3.6 3.6.6.6 0 1 .4 1 1V20c0 .6-.4 1-1 1A17 17 0 0 1 3 4c0-.6.4-1 1-1h3.6c.6 0 1 .4 1 1 0 1.3.2 2.5.6 3.6.1.3 0 .7-.2 1L6.6 10.8z" />
                  </svg>
                  {fmtAmount(p.voice_minutes, "dk")}
                </span>
                <span>
                  <svg viewBox="0 0 24 24" aria-hidden="true">
                    <path d="M4 4h16c.6 0 1 .4 1 1v11c0 .6-.4 1-1 1H8l-4 4V5c0-.6.4-1 1-1z" />
                  </svg>
                  {fmtAmount(p.sms_count, "SMS")}
                </span>
                {p.validity_days && (
                  <span>
                    <svg viewBox="0 0 24 24" aria-hidden="true">
                      <path d="M7 2v2H5c-1.1 0-2 .9-2 2v13c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2h-2V2h-2v2H9V2H7zM5 9h14v10H5V9z" />
                    </svg>
                    {p.validity_days} gün
                  </span>
                )}
              </div>

              <div className="cmp-footer">
                <span className="cmp-price">
                  {p.price_try !== null ? `${p.price_try} TL` : "—"}
                  {period && <em>/ {period}</em>}
                </span>
                <a
                  href={p.source_url}
                  target="_blank"
                  rel="noreferrer"
                  className="cmp-detail"
                >
                  Detay →
                </a>
              </div>
            </article>
          );
        })}
      </div>
    </div>
  );
}
