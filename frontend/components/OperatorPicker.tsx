"use client";

import { useEffect, useState } from "react";
import { OPERATOR_LABELS } from "@/lib/api";

export type OperatorKey = "all" | "turkcell" | "vodafone" | "turk_telekom";

const OPTIONS: { key: OperatorKey; label: string }[] = [
  { key: "all", label: "Tümü" },
  { key: "turkcell", label: OPERATOR_LABELS.turkcell },
  { key: "vodafone", label: OPERATOR_LABELS.vodafone },
  { key: "turk_telekom", label: OPERATOR_LABELS.turk_telekom },
];

interface Props {
  value: OperatorKey;
  onChange: (v: OperatorKey) => void;
}

export default function OperatorPicker({ value, onChange }: Props) {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    function onDoc() {
      setOpen(false);
    }
    document.addEventListener("click", onDoc);
    return () => document.removeEventListener("click", onDoc);
  }, [open]);

  return (
    <div className="pop-wrap" onClick={(e) => e.stopPropagation()}>
      <div className={`popover${open ? " active" : ""}`}>
        {OPTIONS.map((o) => (
          <button
            key={o.key}
            className={o.key === value ? "active" : ""}
            style={o.key === value && o.key !== "all" ? { color: `var(--${o.key})`, fontWeight: 600 } : undefined}
            onClick={() => {
              onChange(o.key);
              setOpen(false);
            }}
          >
            {o.label}
          </button>
        ))}
      </div>
      <button
        type="button"
        className={`op-toggle${value === "all" ? " round" : " set"}`}
        style={value !== "all" ? { background: `var(--${value})`, color: value === "turkcell" ? "#000" : "#fff" } : undefined}
        title="Operatör seç"
        onClick={() => setOpen((v) => !v)}
      >
        {/* SIM / antenna glyph */}
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M2 20h.01M7 20v-4M12 20v-8M17 20V8M22 4v16" />
        </svg>
        {value !== "all" && (
          <span className="op-toggle-label">{operatorLabel(value)}</span>
        )}
      </button>
    </div>
  );
}

function operatorLabel(v: OperatorKey): string {
  return OPTIONS.find((o) => o.key === v)?.label ?? "Tümü";
}
