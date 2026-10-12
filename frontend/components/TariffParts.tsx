"use client";

// Small building blocks shared by the recommendation card and the comparison view,
// so both show operator, price, verdict and unknown values the same way.

import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { Package } from "@/lib/api";
import { fmtDate, lineTypeLabel, operatorLabel, price, type Cell, type Verdict } from "@/lib/tariff";

export function OperatorMark({ operator }: { operator: string }) {
  return (
    <span className="op-mark">
      <span className={`op-dot op-${operator}`} aria-hidden="true" />
      {operatorLabel(operator)}
    </span>
  );
}

export function LineTypeTag({ p }: { p: Package }) {
  return <span className="tag">{lineTypeLabel(p)}</span>;
}

/** The charged price with its real period, never a derived value. */
export function PriceText({ p, size = "md" }: { p: Package; size?: "md" | "lg" }) {
  const { amount, period } = price(p);
  return (
    <span className={`price price-${size}`}>
      <strong>{amount}</strong>
      {period && <span className="price-period">{period}</span>}
    </span>
  );
}

const ICON: Record<string, string> = {
  good: "M5 12.5l4 4L19 7",
  warn: "M12 8v5M12 16.5v.5",
  unknown: "M9.5 9a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .9-1 1.5v.7M12 17v.5",
};

/** Text + icon, so the verdict never depends on colour alone. */
export function VerdictBadge({ v }: { v: Verdict }) {
  return (
    <span className={`badge badge-${v.tone}`}>
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d={ICON[v.tone] ?? ICON.unknown} />
      </svg>
      {v.label}
    </span>
  );
}

export function CheckList({ v }: { v: Verdict }) {
  if (v.checks.length === 0) return null;
  return (
    <ul className="checks" aria-label="Şartlarınla eşleşme">
      {v.checks.map((c) => (
        <li key={c.label} className={c.ok === true ? "ok" : c.ok === false ? "no" : "unk"}>
          <span className="check-mark" aria-hidden="true">
            {c.ok === true ? "✓" : c.ok === false ? "✕" : "?"}
          </span>
          <span>
            {c.label}
            <span className="sr-only">
              {c.ok === true ? ": karşılıyor" : c.ok === false ? ": karşılamıyor" : ": kaynakta doğrulanamadı"}
            </span>
          </span>
        </li>
      ))}
    </ul>
  );
}

// Only one explanation is open at a time: opening one announces its id, the rest close.
const TIP_EVENT = "tarifeai:tip-open";
const TIP_W = 240;

/**
 * Small "i" (extra detail) or "!" (a condition worth checking) next to a value.
 * Opens on click, tap, Enter or Space; Escape, an outside click, scrolling or
 * opening another one closes it. The bubble is portalled with fixed positioning
 * so the comparison's scroll container and sticky label column cannot clip it.
 */
export function InfoTip({ label, text, kind = "info" }: { label: string; text: string; kind?: "info" | "warn" }) {
  const id = useId();
  const btn = useRef<HTMLButtonElement>(null);
  const tip = useRef<HTMLSpanElement>(null);
  const [pos, setPos] = useState<{ left: number; top?: number; bottom?: number } | null>(null);

  useEffect(() => {
    if (!pos) return;
    const close = () => setPos(null);
    const onOther = (e: Event) => (e as CustomEvent<string>).detail !== id && close();
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      close();
      btn.current?.focus();
    };
    const onDown = (e: PointerEvent) => {
      const t = e.target as Node;
      if (!btn.current?.contains(t) && !tip.current?.contains(t)) close();
    };
    window.addEventListener(TIP_EVENT, onOther);
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onDown);
    window.addEventListener("scroll", close, true);
    window.addEventListener("resize", close);
    return () => {
      window.removeEventListener(TIP_EVENT, onOther);
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onDown);
      window.removeEventListener("scroll", close, true);
      window.removeEventListener("resize", close);
    };
  }, [pos, id]);

  function toggle() {
    if (pos) return setPos(null);
    const r = btn.current!.getBoundingClientRect();
    const left = Math.max(8, Math.min(r.left + r.width / 2 - TIP_W / 2, window.innerWidth - TIP_W - 8));
    // Below the icon unless that would run off the bottom of the viewport.
    const below = r.bottom + 160 < window.innerHeight;
    setPos(below ? { left, top: r.bottom + 6 } : { left, bottom: window.innerHeight - r.top + 6 });
    window.dispatchEvent(new CustomEvent(TIP_EVENT, { detail: id }));
  }

  const tipId = `${id}-tip`;
  return (
    <span className="info">
      <button
        ref={btn}
        type="button"
        className={`info-btn info-${kind}`}
        aria-label={`${label}: ${kind === "warn" ? "dikkat edilecek koşul" : "ek bilgi"}`}
        aria-expanded={pos !== null}
        aria-describedby={pos ? tipId : undefined}
        onClick={toggle}
      >
        {kind === "warn" ? "!" : "i"}
      </button>
      {pos &&
        createPortal(
          <span ref={tip} id={tipId} className="info-tip" role="tooltip" style={{ ...pos, width: TIP_W }}>
            {text}
          </span>,
          document.body
        )}
    </span>
  );
}

export function CellView({ cell }: { cell: Cell }) {
  if (cell.items) {
    return (
      <ul className="cell-list">
        {cell.items.map((c) => (
          <li key={c.text}>
            <CellView cell={c} />
          </li>
        ))}
      </ul>
    );
  }
  return (
    <span className={`cell tone-${cell.tone ?? "default"}`}>
      <span className="cell-text">
        {cell.text}
        {cell.warn && <InfoTip kind="warn" label={cell.text} text={cell.warn} />}
        {cell.info && <InfoTip label={cell.text} text={cell.info} />}
      </span>
      {cell.note && <span className="cell-note">{cell.note}</span>}
    </span>
  );
}

export function SourceLink({ p, compact = false }: { p: Package; compact?: boolean }) {
  const date = fmtDate(p.verified_at);
  return (
    <span className="source">
      <a href={p.source_url} target="_blank" rel="noreferrer">
        {compact ? "Resmi sayfa" : `${operatorLabel(p.operator)} resmi sayfası`}
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5" />
        </svg>
        <span className="sr-only"> (yeni sekmede açılır)</span>
      </a>
      {date && <span className="source-date">Doğrulama: {date}</span>}
    </span>
  );
}
