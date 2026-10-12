// Placeholder while an answer is generated: a short status line (announced to
// screen readers) and a quiet outline of the answer + plan block that follows.
export default function LoadingSkeleton() {
  return (
    <div className="row assistant">
      <div className="bubble assistant">
        <p className="thinking" role="status">
          Tarifeler aranıyor ve karşılaştırılıyor…
        </p>
        <div className="skeleton" aria-hidden="true">
          <span className="sk" style={{ width: "92%" }} />
          <span className="sk" style={{ width: "97%" }} />
          <span className="sk" style={{ width: "64%" }} />
          <span className="sk sk-block" />
        </div>
      </div>
    </div>
  );
}
