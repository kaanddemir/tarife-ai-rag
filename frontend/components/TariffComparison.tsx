"use client";

// Aligned comparison of 2+ plans. One row model (lib/tariff.comparisonRows) feeds
// two layouts: a real <table> on wider screens (same attribute scanned across
// plans) and, on phones, one block per attribute with each plan's value stacked,
// so nothing has to shrink into an unreadable grid. CSS shows exactly one.

import { useState } from "react";
import type { Package } from "@/lib/api";
import { comparisonRows, operatorLabel, price30, verdict, type Row } from "@/lib/tariff";
import { CellView, CheckList, InfoTip, LineTypeTag, OperatorMark, PriceText, SourceLink, VerdictBadge } from "./TariffParts";

function PlanHead({ p, showVerdict }: { p: Package; showVerdict: boolean }) {
  const v = showVerdict ? verdict(p) : null;
  return (
    <div className="plan-head">
      <div className="plan-meta">
        <OperatorMark operator={p.operator} />
        <LineTypeTag p={p} />
      </div>
      <span className="plan-name">{p.name}</span>
      <PriceText p={p} />
      {v && <VerdictBadge v={v} />}
    </div>
  );
}

export default function TariffComparison({ packages, showVerdict = true }: { packages: Package[]; showVerdict?: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const all = comparisonRows(packages);
  const rows = expanded ? all : all.filter((r) => r.primary);
  const hidden = all.length - all.filter((r) => r.primary).length;
  const mixedPeriods = new Set(packages.map((p) => (p.line_type === "postpaid" ? 30 : p.validity_days))).size > 1;
  const failed = showVerdict ? packages.filter((p) => verdict(p)?.tone === "warn") : [];

  // Group headers only where the group changes, for both layouts.
  const withGroups = rows.map((r, i) => ({ row: r, newGroup: i === 0 || rows[i - 1].group !== r.group }));

  return (
    <section className="compare" aria-label="Tarife karşılaştırması">
      {mixedPeriods && packages.some((p) => price30(p) !== null) && (
        <div className="compare-head">
          <p className="compare-note">
            Fiyatlar farklı sürelere ait. Kıyas için faturasız paketlerin 30 günlük karşılığı ayrıca gösterildi;
            bu TarifeAI hesabıdır, operatörün aldığı ücret değildir.
          </p>
        </div>
      )}

      {/* Wide screens: real table. */}
      <div className="compare-scroll" tabIndex={0} role="region" aria-label="Karşılaştırma tablosu">
        <table className="compare-table" style={{ minWidth: 168 + packages.length * 150 }}>
          <caption className="sr-only">
            {packages.map((p) => `${operatorLabel(p.operator)} ${p.name}`).join(", ")} karşılaştırması
          </caption>
          <thead>
            <tr>
              <th scope="col" className="row-label">
                <span className="sr-only">Özellik</span>
              </th>
              {packages.map((p) => (
                <th scope="col" key={p.id}>
                  <PlanHead p={p} showVerdict={showVerdict} />
                </th>
              ))}
            </tr>
          </thead>
          {groupRows(withGroups).map((group) => (
            <tbody key={group.name}>
              <tr className="group-row">
                <th scope="colgroup" colSpan={packages.length + 1}>
                  {group.name}
                </th>
              </tr>
              {group.rows.map((r) => (
                <tr key={r.key}>
                  <th scope="row" className="row-label">
                    {r.label}
                    {r.hint && <InfoTip label={r.label} text={r.hint} />}
                  </th>
                  {r.cells.map((c, i) => (
                    <td key={packages[i].id}>
                      <CellView cell={c} />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          ))}
          <tbody>
            <tr>
              <th scope="row" className="row-label">
                Kaynak
              </th>
              {packages.map((p) => (
                <td key={p.id}>
                  <SourceLink p={p} compact />
                </td>
              ))}
            </tr>
          </tbody>
        </table>
      </div>

      {/* Phones: one block per attribute, plans stacked inside it. */}
      <div className="compare-stack">
        <ol className="stack-plans" aria-label="Karşılaştırılan tarifeler">
          {packages.map((p, i) => (
            <li key={p.id}>
              <span className="stack-index" aria-hidden="true">
                {i + 1}
              </span>
              <PlanHead p={p} showVerdict={showVerdict} />
            </li>
          ))}
        </ol>
        {withGroups.map(({ row, newGroup }) => (
          <div key={row.key}>
            {newGroup && <h4 className="stack-group">{row.group}</h4>}
            <StackRow row={row} packages={packages} />
          </div>
        ))}
        <StackRow
          row={{ key: "source", label: "Kaynak", group: "Avantajlar", primary: true, cells: [] }}
          packages={packages}
          render={(p) => <SourceLink p={p} compact />}
        />
      </div>

      <div className="compare-foot">
        {hidden > 0 && (
          <button type="button" className="btn btn-ghost" aria-expanded={expanded} onClick={() => setExpanded((v) => !v)}>
            {expanded ? "Daha az göster" : `Tüm detaylar (${hidden} satır daha)`}
          </button>
        )}
      </div>

      {expanded &&
        failed.map((p) => (
          <div key={p.id} className="notice notice-warn">
            <strong>
              {operatorLabel(p.operator)} {p.name}
            </strong>
            <CheckList v={verdict(p)!} />
          </div>
        ))}
    </section>
  );
}

function groupRows(items: { row: Row; newGroup: boolean }[]): { name: string; rows: Row[] }[] {
  const groups: { name: string; rows: Row[] }[] = [];
  for (const { row, newGroup } of items) {
    if (newGroup) groups.push({ name: row.group, rows: [] });
    groups[groups.length - 1].rows.push(row);
  }
  return groups;
}

function StackRow({
  row,
  packages,
  render,
}: {
  row: Row;
  packages: Package[];
  render?: (p: Package) => React.ReactNode;
}) {
  return (
    <dl className="stack-row">
      <dt>
        {row.label}
        {row.hint && <InfoTip label={row.label} text={row.hint} />}
      </dt>
      {packages.map((p, i) => (
        <dd key={p.id}>
          <span className="stack-who">
            <span className="stack-index" aria-hidden="true">
              {i + 1}
            </span>
            <span aria-hidden="true">{operatorLabel(p.operator)}</span>
            <span className="sr-only">{p.name}: </span>
          </span>
          {render ? render(p) : <CellView cell={row.cells[i]} />}
        </dd>
      ))}
    </dl>
  );
}
