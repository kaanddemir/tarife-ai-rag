// Display rules for tariffs (lib/tariff.ts): the commercial-integrity guarantees the
// recommendation card and comparison view rely on. Run: npm test.
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import ts from "typescript";

// Transpile lib/api.ts and lib/tariff.ts side by side so the relative import resolves.
const dir = mkdtempSync(join(tmpdir(), "tarifeai-tariff-"));
for (const name of ["api", "tariff"]) {
  const src = readFileSync(new URL(`../lib/${name}.ts`, import.meta.url), "utf8");
  const js = ts
    .transpileModule(src, { compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 } })
    .outputText.replace(/from "\.\/api"/g, 'from "./api.mjs"');
  writeFileSync(join(dir, `${name}.mjs`), js);
}
const t = await import(join(dir, "tariff.mjs"));

/** Minimal API-shaped package; fields mirror backend PackageOut. */
function pkg(over = {}) {
  return {
    id: "x", operator: "vodafone", name: "Test", line_type: "prepaid", customer_type: "new",
    price_try: 300, internet_gb: 50, social_media_gb: null, bonus_internet_gb: null,
    bonus_internet_scope: null, general_internet_gb: 50, price_30d_equivalent: 100,
    price_period: "90_day", renewal_type: "one_time", commitment_months: null,
    commitment_status: "no_commitment", price_lock_months: null, no_commitment_price_try: null,
    package_group: null, operator_family: null, audience_segment: "general", product_scope: "mobile_voice",
    age_min: null, age_max: null, eligibility_tags: [], benefit_tags: [], voice_minutes: 1000,
    sms_count: 1000, validity_days: 90, conditions: null, campaign: null, source_url: "https://x",
    activation_channels: ["new_line"], benefits: [], benefits_status: "verified_none",
    ...over,
  };
}

test("money uses Turkish separators, like the backend", () => {
  assert.equal(t.fmtTL(1250), "1.250 TL");
  assert.equal(t.fmtTL(99.9), "99,90 TL");
  assert.equal(t.fmtTL(300), "300 TL");
});

test("prepaid price shows its real period, never 'aylık'", () => {
  // Old saves stored price_period "month" for a 90-day package; validity_days wins.
  const p = t.price(pkg({ price_period: "month" }));
  assert.equal(p.amount, "300 TL");
  assert.equal(p.period, "/ 90 gün");
  assert.equal(p.billing, "Tek ödeme, 90 gün geçerli");
  assert.equal(t.price(pkg({ line_type: "postpaid", validity_days: null })).period, "/ ay");
});

test("the 30-day value comes only from the API and is labelled as TarifeAI's calculation", () => {
  assert.equal(t.price30(pkg()), 100);
  assert.equal(t.price30(pkg({ price_30d_equivalent: undefined })), null); // never recomputed
  const row = t.comparisonRows([pkg(), pkg({ id: "y", line_type: "postpaid", price_30d_equivalent: null })])
    .find((r) => r.key === "price30");
  assert.match(row.hint, /TarifeAI hesabı/);
  assert.match(row.hint, /faturalamaz/);
});

test("restricted data is never counted as general internet", () => {
  const p = pkg({
    internet_gb: 20, general_internet_gb: 20, social_media_gb: 30, social_media_apps: ["Instagram"],
    bonus_internet_gb: 10, bonus_internet_scope: "tethering_only",
  });
  assert.equal(t.generalInternetCell(p).text, "20 GB");
  const restricted = t.restrictedData(p).map((c) => c.text);
  assert.deepEqual(restricted, ["30 GB sosyal medya", "10 GB bonus"]);
  // A general-scope bonus is part of general internet and says so.
  const general = pkg({ internet_gb: 20, general_internet_gb: 30, bonus_internet_gb: 10, bonus_internet_scope: "general" });
  assert.deepEqual(t.generalInternetCell(general), { text: "30 GB", info: "Bu miktara 10 GB bonus internet dahildir." });
});

test("unlimited and unknown are explicit", () => {
  assert.equal(t.generalInternetCell(pkg({ general_internet_gb: -1 })).text, "Sınırsız");
  assert.equal(t.generalInternetCell(pkg({ general_internet_gb: null, internet_gb: null })).text, "Belirtilmemiş");
  assert.equal(t.minutesCell(pkg({ voice_minutes: null })).tone, "unknown");
  assert.equal(t.renewalCell(pkg({ renewal_type: "unknown" })).text, "Belirtilmemiş");
  assert.equal(t.channelCell(pkg({ activation_channels: null })).text, "Belirtilmemiş");
});

test("a free trial is never shown as an included subscription", () => {
  const p = pkg({
    benefits: [
      { category: "subscription", service: "Netflix", offer: "free_trial", duration_months: 6, after_trial: "paid", after_trial_price_try: 259.99 },
      { category: "subscription", service: "Muud", offer: "included" },
      { category: "app_data", service: "e-dergi", offer: "requires_purchase" },
    ],
  });
  const items = t.benefitItems(p);
  assert.equal(items[0].offer, "6 ay ücretsiz deneme");
  assert.match(items[0].detail, /Sonrasında 259,99 TL ücretli/);
  assert.equal(items[1].offer, "Dahil");
  // App data that needs a purchase is neither a benefit nor restricted data.
  assert.equal(items.length, 2);
  assert.equal(t.restrictedData(p).length, 0);
});

test("eligibility restrictions are surfaced, not hidden", () => {
  const p = pkg({ audience_segment: "youth", age_max: 26, eligibility_tags: ["dealer_only", "province_restricted"] });
  const who = t.eligibilitySummary(p);
  assert.equal(who.tone, "warn");
  assert.match(who.text, /Gençlere özel, En fazla 26 yaş/);
  assert.match(who.text, /Yalnızca bayiden/);
  assert.match(who.text, /Yalnızca belirli illerde/);
  // How-to-apply conditions move behind "!", but are never dropped.
  const window = t.eligibilitySummary(pkg({ eligibility_tags: ["activation_window", "identity_required"] }));
  assert.equal(window.warn, "Başvuru dönemi kısıtlıdır. Kimlik doğrulaması gerekir.");
});

test("a verdict exists only when the backend sent one", () => {
  assert.equal(t.verdict(pkg()), null); // no 'Önerilen' without a grounded reason
  const v = t.verdict(pkg({ retrieval_status: "compromise", retrieval_checks: [["en fazla 200 TL", false]] }));
  assert.equal(v.tone, "warn");
  assert.deepEqual(v.checks, [{ label: "en fazla 200 TL", ok: false }]);
});

test("comparison rows: groups stay contiguous and empty rows are dropped", () => {
  const rows = t.comparisonRows([pkg(), pkg({ id: "y" })]);
  const keys = rows.map((r) => r.key);
  assert.ok(!keys.includes("commitment"), "no commitment row for prepaid-only sets");
  assert.ok(!keys.includes("restricted"), "no app-data row when nobody has any");
  assert.ok(!keys.includes("promo"));
  const groups = rows.map((r) => r.group).filter((g, i, a) => i === 0 || a[i - 1] !== g);
  assert.equal(new Set(groups).size, groups.length);
  const mixed = t.comparisonRows([pkg(), pkg({ id: "y", line_type: "postpaid", commitment_status: "committed", commitment_months: 12 })]);
  assert.deepEqual(mixed.find((r) => r.key === "commitment").cells.map((c) => c.text), ["Yok", "12 ay taahhüt"]);
});

test("a package saved before newer fields existed never renders NaN or undefined", () => {
  const legacy = pkg({ line_type: "postpaid", commitment_status: "committed", commitment_months: 12, validity_days: null });
  for (const k of ["no_commitment_price_try", "price_after_promo_try", "promo_months", "general_internet_gb",
    "price_30d_equivalent", "price_lock_months", "verified_at", "retrieval_status"]) delete legacy[k];
  const flat = (c) => (c.items ? c.items.flatMap(flat) : [c.text, c.note ?? "", c.info ?? "", c.warn ?? ""]);
  const texts = t.comparisonRows([legacy, pkg({ id: "y" })]).flatMap((r) => r.cells.flatMap(flat));
  for (const s of texts) assert.doesNotMatch(String(s), /NaN|undefined|null/);
  assert.equal(t.noCommitmentPriceCell(legacy).text, "Belirtilmemiş");
});
