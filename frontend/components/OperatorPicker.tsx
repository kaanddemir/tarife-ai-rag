"use client";

import { useEffect, useState } from "react";
import { OPERATOR_LABELS } from "@/lib/api";

export type OperatorKey = "all" | "turkcell" | "vodafone" | "turk_telekom";
export type LineTypeKey = "all" | "postpaid" | "prepaid";

const OPTIONS: { key: OperatorKey; label: string }[] = [
  { key: "all", label: "Tümü" },
  { key: "turkcell", label: OPERATOR_LABELS.turkcell },
  { key: "vodafone", label: OPERATOR_LABELS.vodafone },
  { key: "turk_telekom", label: OPERATOR_LABELS.turk_telekom },
];

// Bu etiketler ayni zamanda soruya eklenen onektir: backend retrieve() "faturali" /
// "faturasiz" kelimelerini yakalayip line_type'i sert filtreye cevirir.
const LINE_TYPES: { key: LineTypeKey; label: string }[] = [
  { key: "all", label: "Tümü" },
  { key: "postpaid", label: "Faturalı" },
  { key: "prepaid", label: "Faturasız" },
];

interface Props {
  value: OperatorKey;
  onChange: (v: OperatorKey) => void;
  lineType: LineTypeKey;
  onLineTypeChange: (v: LineTypeKey) => void;
}

export default function OperatorPicker({
  value,
  onChange,
  lineType,
  onLineTypeChange,
}: Props) {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    function onDoc() {
      setOpen(false);
    }
    document.addEventListener("click", onDoc);
    return () => document.removeEventListener("click", onDoc);
  }, [open]);

  const isSet = value !== "all" || lineType !== "all";

  return (
    <div className="pop-wrap" onClick={(e) => e.stopPropagation()}>
      <div className={`popover${open ? " active" : ""}`}>
        <div className="popover-label">Operatör</div>
        {OPTIONS.map((o) => (
          <button
            key={o.key}
            className={o.key === value ? "active" : ""}
            style={o.key === value && o.key !== "all" ? { color: `var(--${o.key})`, fontWeight: 600 } : undefined}
            onClick={() => onChange(o.key)}
          >
            {o.label}
          </button>
        ))}

        <div className="popover-sep" />
        <div className="popover-label">Hat tipi</div>
        {LINE_TYPES.map((o) => (
          <button
            key={o.key}
            className={o.key === lineType ? "active" : ""}
            onClick={() => onLineTypeChange(o.key)}
          >
            {o.label}
          </button>
        ))}
      </div>
      <button
        type="button"
        className={`op-toggle${isSet ? " set" : " round"}`}
        style={
          value !== "all"
            ? { background: `var(--${value})`, color: value === "turkcell" ? "#000" : "#fff" }
            : undefined
        }
        title="Operatör ve hat tipi seç"
        onClick={() => setOpen((v) => !v)}
      >
        {/* SIM / antenna glyph */}
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M2 20h.01M7 20v-4M12 20v-8M17 20V8M22 4v16" />
        </svg>
        {isSet && <span className="op-toggle-label">{toggleLabel(value, lineType)}</span>}
      </button>
    </div>
  );
}

function toggleLabel(operator: OperatorKey, lineType: LineTypeKey): string {
  const parts = [
    operator === "all" ? null : OPTIONS.find((o) => o.key === operator)?.label,
    lineType === "all" ? null : LINE_TYPES.find((o) => o.key === lineType)?.label,
  ];
  return parts.filter(Boolean).join(" · ");
}
