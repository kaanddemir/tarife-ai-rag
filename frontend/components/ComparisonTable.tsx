"use client";

import { Package, OPERATOR_LABELS, fmtAmount } from "@/lib/api";

// Show the headline (total) GB, matching what the assistant states and how the
// packages are marketed. When it differs from the base quota, expose the
// breakdown (general + social + bonus) as a hover tooltip.
function internetCell(p: Package): { text: string; title?: string } {
  const total = p.total_internet_gb ?? p.internet_gb;
  if (total === null) return { text: "—" };

  const parts: string[] = [];
  if (p.internet_gb !== null) parts.push(`${p.internet_gb} GB genel`);
  if (p.social_media_gb) parts.push(`${p.social_media_gb} GB sosyal medya`);
  if (p.bonus_internet_gb) parts.push(`${p.bonus_internet_gb} GB bonus`);

  const hasBreakdown =
    parts.length > 1 || (p.internet_gb !== null && p.internet_gb !== total);
  return {
    text: `${total} GB`,
    title: hasBreakdown ? parts.join(" + ") : undefined,
  };
}

export default function ComparisonTable({ packages }: { packages: Package[] }) {
  if (!packages.length) return null;

  return (
    <div className="table-card">
      <h3>Karşılaştırma</h3>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Operatör</th>
              <th>Paket</th>
              <th>İnternet</th>
              <th>Dakika</th>
              <th>SMS</th>
              <th>Fiyat</th>
              <th>Süre</th>
            </tr>
          </thead>
          <tbody>
            {packages.map((p, index) => (
              <tr key={p.id} className={index === 0 ? "recommended" : ""}>
                <td>
                  <span
                    className="op-badge"
                    style={{ background: `var(--${p.operator})` }}
                  />
                  {OPERATOR_LABELS[p.operator] ?? p.operator}
                </td>
                <td>
                  <a href={p.source_url} target="_blank" rel="noreferrer" className="muted">
                    {p.name}
                  </a>
                  {index === 0 && (
                    <span className="tag-recommended" title="Önerilen" aria-label="Önerilen">
                      <svg viewBox="0 0 24 24" aria-hidden="true">
                        <path d="M12 3.8l2.4 4.9 5.4.8-3.9 3.8.9 5.4L12 16.1l-4.8 2.6.9-5.4-3.9-3.8 5.4-.8L12 3.8z" />
                      </svg>
                    </span>
                  )}
                </td>
                {(() => {
                  const cell = internetCell(p);
                  return <td title={cell.title}>{cell.text}</td>;
                })()}
                <td>{fmtAmount(p.voice_minutes, "dk")}</td>
                <td>{fmtAmount(p.sms_count, "SMS")}</td>
                <td className="price">{p.price_try !== null ? `${p.price_try} TL` : "—"}</td>
                <td>{p.validity_days ? `${p.validity_days} gün` : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
