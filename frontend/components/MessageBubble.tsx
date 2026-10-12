import type { Package } from "@/lib/api";
import { isHistorical, type CatalogCheck } from "@/lib/catalogCheck";
import { operatorLabel } from "@/lib/tariff";
import type { Message } from "@/lib/types";
import AnswerMarkdown from "./AnswerMarkdown";
import CitationSources from "./CitationSources";
import TariffCard from "./TariffCard";
import TariffComparison from "./TariffComparison";

interface Props {
  message: Message;
  catalog: CatalogCheck;
  /** Offered on a failed reply when sending the same question again can help. */
  onRetry?: () => void;
}

function Plans({ packages, live }: { packages: Package[]; live: boolean }) {
  if (packages.length === 0) return null;
  // A verdict belongs to the question it was checked against; only show it live.
  if (packages.length === 1) return <TariffCard p={packages[0]} showVerdict={live} />;
  return <TariffComparison packages={packages} showVerdict={live} />;
}

/** Saved plans of an older chat, resolved against today's catalog. */
function HistoricalPlans({ packages, catalog }: { packages: Package[]; catalog: CatalogCheck }) {
  if (catalog.state === "checking" || catalog.state === "idle") {
    return (
      <p className="notice notice-info" role="status">
        Bu sohbetteki tarifelerin güncelliği kontrol ediliyor…
      </p>
    );
  }
  if (catalog.state === "failed") {
    return (
      <>
        <p className="notice notice-unknown" role="note">
          <strong>Eski sohbet.</strong> Aşağıdaki bilgiler sohbet tarihine ait ve şu an doğrulanamadı; güncel teklif
          olarak kabul etme.
        </p>
        <div className="stale">
          <Plans packages={packages} live={false} />
        </div>
      </>
    );
  }
  const current = packages.map((p) => catalog.current[p.id]).filter((p): p is Package => Boolean(p));
  const gone = packages.filter((p) => catalog.current[p.id] === null);
  const unchecked = packages.filter((p) => catalog.current[p.id] === undefined);
  return (
    <>
      <p className="notice notice-info" role="note">
        Eski sohbet: tarifeler bugünkü resmi bilgileriyle gösteriliyor. Yazılı cevap sohbet tarihine aittir.
      </p>
      {gone.length > 0 && (
        <div className="notice notice-warn" role="note">
          <strong>Artık sunulmuyor:</strong>{" "}
          {gone.map((p) => `${operatorLabel(p.operator)} ${p.name}`).join(", ")}. Bu tarife güncel listede yok.
        </div>
      )}
      <Plans packages={current} live={false} />
      {unchecked.length > 0 && (
        <p className="notice notice-unknown">
          {unchecked.length} tarifenin güncelliği kontrol edilemedi:{" "}
          {unchecked.map((p) => p.name).join(", ")}.
        </p>
      )}
    </>
  );
}

export default function MessageBubble({ message, catalog, onRetry }: Props) {
  if (message.role === "user") {
    return (
      <div className="row user">
        <div className="bubble user">{message.content}</div>
      </div>
    );
  }

  if (message.error) {
    const tone = message.errorKind === "quota" ? "notice-warn" : "notice-error";
    return (
      <div className="row assistant">
        <div className={`notice ${tone} msg-error`} role="alert">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M12 8v5M12 16.5v.5" />
            <circle cx="12" cy="12" r="9" />
          </svg>
          <span>{message.content}</span>
          {onRetry && (
            <button type="button" className="btn btn-small" onClick={onRetry}>
              Tekrar dene
            </button>
          )}
        </div>
      </div>
    );
  }

  const packages = message.packages ?? [];
  const historical = isHistorical(message.at);

  return (
    <div className="row assistant">
      <div className="bubble assistant">
        <AnswerMarkdown text={message.content} citations={message.citations ?? []} />
        {packages.length > 0 &&
          (historical ? <HistoricalPlans packages={packages} catalog={catalog} /> : <Plans packages={packages} live />)}
        <CitationSources citations={message.citations ?? []} copyText={message.content} />
      </div>
    </div>
  );
}
