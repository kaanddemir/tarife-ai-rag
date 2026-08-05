// Placeholder shown while an answer is generating. Mirrors the real assistant
// layout (answer text + comparison cards) so content settles in without a jump,
// which reads calmer than a spinner. Reuses .cmp-card / .cmp-grid for exact fit.

// A skeleton card leans on .cmp-card's own flex-column + gap for spacing; each
// grey bar is just a sized .sk element. Slightly varied widths avoid a robotic look.
function SkeletonCard() {
  return (
    <div className="cmp-card" aria-hidden="true">
      <span className="sk" style={{ height: 12, width: "45%" }} />
      <span className="sk" style={{ height: 14, width: "82%" }} />
      <span className="sk sk-hero" style={{ width: "58%" }} />
      <span className="sk" style={{ height: 12, width: "92%" }} />
      <span className="sk" style={{ height: 12, width: "68%" }} />
      <span className="sk" style={{ height: 18, width: "42%", marginTop: "auto" }} />
    </div>
  );
}

export default function LoadingSkeleton() {
  return (
    <div className="row assistant">
      <div className="bubble assistant full">
        <div className="thinking">
          <span className="thinking-label">TarifeAI düşünüyor</span>
        </div>

        <div className="skeleton-answer" aria-hidden="true">
          <span className="sk sk-line" style={{ width: "94%" }} />
          <span className="sk sk-line" style={{ width: "99%" }} />
          <span className="sk sk-line" style={{ width: "76%" }} />
          <span className="sk sk-line" style={{ width: "88%" }} />
        </div>

        <div className="table-card" style={{ marginTop: 16 }}>
          <span className="sk sk-title" />
          <div className="cmp-grid">
            <SkeletonCard />
            <SkeletonCard />
            <SkeletonCard />
          </div>
        </div>
      </div>
    </div>
  );
}
