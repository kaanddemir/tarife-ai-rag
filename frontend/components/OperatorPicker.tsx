"use client";

import { useEffect, useState } from "react";
import { OPERATOR_LABELS } from "@/lib/api";

export type OperatorKey = "all" | "turkcell" | "vodafone" | "turk_telekom";
export type LineTypeKey = "all" | "postpaid" | "prepaid";

const OPTIONS: { key: OperatorKey; label: string }[] = [
  { key: "all", label: "Tüm operatörler" },
  { key: "turkcell", label: OPERATOR_LABELS.turkcell },
  { key: "vodafone", label: OPERATOR_LABELS.vodafone },
  { key: "turk_telekom", label: OPERATOR_LABELS.turk_telekom },
];

// Bu etiketler ayni zamanda soruya eklenen onektir: backend retrieve() "faturali" /
// "faturasiz" kelimelerini yakalayip line_type'i sert filtreye cevirir.
const LINE_TYPES: { key: LineTypeKey; label: string }[] = [
  { key: "all", label: "Faturalı ve faturasız" },
  { key: "postpaid", label: "Faturalı" },
  { key: "prepaid", label: "Faturasız" },
];

interface Props {
  value: OperatorKey;
  onChange: (v: OperatorKey) => void;
  lineType: LineTypeKey;
  onLineTypeChange: (v: LineTypeKey) => void;
}

export default function OperatorPicker({ value, onChange, lineType, onLineTypeChange }: Props) {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    const close = () => setOpen(false);
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && close();
    document.addEventListener("click", close);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("click", close);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const isSet = value !== "all" || lineType !== "all";
  const label = toggleLabel(value, lineType);

  return (
    <div className="pop-wrap" onClick={(e) => e.stopPropagation()}>
      {open && (
        <div className="popover" role="group" aria-label="Arama filtresi">
          <fieldset>
            <legend>Operatör</legend>
            {OPTIONS.map((o) => (
              <button
                type="button"
                key={o.key}
                aria-pressed={o.key === value}
                onClick={() => onChange(o.key)}
              >
                {o.key !== "all" && <span className={`op-dot op-${o.key}`} aria-hidden="true" />}
                {o.label}
              </button>
            ))}
          </fieldset>
          <fieldset>
            <legend>Hat tipi</legend>
            {LINE_TYPES.map((o) => (
              <button
                type="button"
                key={o.key}
                aria-pressed={o.key === lineType}
                onClick={() => onLineTypeChange(o.key)}
              >
                {o.label}
              </button>
            ))}
          </fieldset>
        </div>
      )}
      <button
        type="button"
        className={`filter-btn${isSet ? " set" : ""}`}
        aria-expanded={open}
        aria-label={isSet ? `Filtre: ${label}. Değiştir` : "Operatör ve hat tipi filtresi"}
        onClick={() => setOpen((v) => !v)}
      >
        {value !== "all" ? (
          <span className={`op-dot op-${value}`} aria-hidden="true" />
        ) : (
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M4 6h16M7 12h10M10 18h4" />
          </svg>
        )}
        {isSet && <span className="filter-label">{label}</span>}
      </button>
    </div>
  );
}

function toggleLabel(operator: OperatorKey, lineType: LineTypeKey): string {
  const parts = [
    operator === "all" ? null : OPERATOR_LABELS[operator],
    lineType === "all" ? null : LINE_TYPES.find((o) => o.key === lineType)?.label,
  ];
  return parts.filter(Boolean).join(" · ");
}
