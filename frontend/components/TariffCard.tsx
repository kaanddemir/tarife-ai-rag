// One recommended plan: what it is, what it costs and for how long, what internet
// is actually usable, who can buy it, and why it fits (the backend's checks).

import type { Package } from "@/lib/api";
import {
  benefitItems,
  channelCell,
  commitmentCell,
  eligibility,
  fmtTL,
  generalInternetCell,
  minutesCell,
  noCommitmentPriceCell,
  price,
  price30,
  priceLockCell,
  promoCell,
  renewalCell,
  restrictedData,
  smsCell,
  validityCell,
  verdict,
  type Cell,
} from "@/lib/tariff";
import { CellView, CheckList, InfoTip, LineTypeTag, OperatorMark, PriceText, SourceLink, VerdictBadge } from "./TariffParts";

function Fact({ label, cell }: { label: string; cell: Cell }) {
  return (
    <div className="fact">
      <dt>{label}</dt>
      <dd>
        <CellView cell={cell} />
      </dd>
    </div>
  );
}

export default function TariffCard({ p, showVerdict = true }: { p: Package; showVerdict?: boolean }) {
  const v = showVerdict ? verdict(p) : null;
  const v30 = price30(p);
  const restricted = restrictedData(p);
  const limits = eligibility(p);
  const promo = promoCell(p);
  const benefits = benefitItems(p);
  const postpaid = p.line_type === "postpaid";

  return (
    <article className="tcard" aria-label={`${p.name}, ${price(p).amount} ${price(p).period}`}>
      <header className="tcard-head">
        <div className="tcard-meta">
          <OperatorMark operator={p.operator} />
          <LineTypeTag p={p} />
        </div>
        {v && <VerdictBadge v={v} />}
      </header>
      <h3 className="tcard-name">{p.name}</h3>

      <div className="tcard-price">
        <PriceText p={p} size="lg" />
        <span className="tcard-billing">{price(p).billing}</span>
        {v30 !== null && (
          <span className="calc-note">
            Kıyas için 30 günlük karşılığı ≈ {fmtTL(v30)} <span>(TarifeAI hesabı, fatura tutarı değil)</span>
          </span>
        )}
        {promo && (
          <span className="calc-note warn">
            {promo.text}, {promo.note?.toLocaleLowerCase("tr-TR")}
          </span>
        )}
      </div>

      <dl className="facts">
        <Fact label="Genel internet" cell={generalInternetCell(p)} />
        <Fact label="Konuşma" cell={minutesCell(p)} />
        <Fact label="SMS" cell={smsCell(p)} />
        <Fact label={postpaid ? "Taahhüt" : "Geçerlilik"} cell={postpaid ? commitmentCell(p) : validityCell(p)} />
      </dl>

      {restricted.length > 0 && (
        <div className="tcard-section">
          <h4>
            Uygulamaya özel internet <InfoTip label="Uygulamaya özel internet" text="Yalnızca belirtilen uygulamalarda geçer; genel internete eklenmez." />
          </h4>
          <ul className="plain-list">
            {restricted.map((c) => (
              <li key={c.text}>
                <CellView cell={c} />
              </li>
            ))}
          </ul>
        </div>
      )}

      {limits.length > 0 && (
        <div className="tcard-section notice notice-warn" role="note">
          <h4>Kimler alabilir</h4>
          <ul className="plain-list">
            {limits.map((c) => (
              <li key={c.text}>{c.text}</li>
            ))}
          </ul>
        </div>
      )}

      {v && v.checks.length > 0 && (
        <div className="tcard-section">
          <h4>Şartlarınla eşleşme</h4>
          <CheckList v={v} />
        </div>
      )}

      <details className="more">
        <summary>Tüm detaylar</summary>
        <dl className="facts facts-wide">
          {postpaid && <Fact label="Taahhütsüz fiyat" cell={noCommitmentPriceCell(p)} />}
          {postpaid && <Fact label="Fiyat sabitleme" cell={priceLockCell(p)} />}
          {postpaid && <Fact label="Geçerlilik" cell={validityCell(p)} />}
          <Fact label="Yenileme" cell={renewalCell(p)} />
          <Fact label="Başvuru şekli" cell={channelCell(p)} />
        </dl>
        {p.benefits_status === "unverified" ? (
          <p className="muted-line">Ek avantajlar resmi sayfada doğrulanamadı.</p>
        ) : benefits.length > 0 ? (
          <div className="tcard-section">
            <h4>Ek avantajlar</h4>
            <ul className="benefits">
              {benefits.map((b) => (
                <li key={b.name}>
                  <span className="benefit-name">{b.name}</span>
                  <span className={`chip tone-${b.tone}`}>{b.offer}</span>
                  {b.detail && <span className="cell-note">{b.detail}</span>}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        {p.conditions && (
          <div className="tcard-section">
            <h4>Resmi sayfadaki koşullar</h4>
            <p className="conditions">{p.conditions}</p>
          </div>
        )}
      </details>

      <footer className="tcard-foot">
        <SourceLink p={p} />
      </footer>
    </article>
  );
}
