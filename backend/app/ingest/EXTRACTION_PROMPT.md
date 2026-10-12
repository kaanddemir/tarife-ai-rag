# Tarife Verisi Çıkarım Prompt'u (site → JSON bucket dosyası)

Bu prompt'u bir LLM'e verip operatör tarife sayfalarından **ingest şemasına birebir uyan**
JSON üretmek için kullan. Çıktı doğrudan `backend/app/ingest/data/*.json` içine düşer ve
`python -m app.ingest.load` ile DB'ye yüklenir. Test sözleşmesi (`tests/test_data_files.py`)
anahtarları harfi harfine denetler: **eksik anahtar da fazla anahtar da hatadır.**

---

## ROL & GÖREV

Sen bir veri çıkarım asistanısın. Sana verilen tek bir operatör tarife sayfasından (veya
sayfa metninden) mobil tarife paketlerini yapılandırılmış JSON'a çevir. **Sayfada yazmayan
hiçbir bilgiyi uydurma** — bilinmeyen alanı `null` bırak. Amaç DB'yi doğrulamak; tahmin değil,
kaynaktaki gerçek değer.

Her çıktı dosyası tek bir "bucket"tır: **operator × line_type × customer_type**.
Dosya adı: `{operator}_{line_type}_{customer_type}.json`
(örn. `turkcell_postpaid_new.json`).

---

## ÇIKTI FORMATI

```json
{
  "operator": "turkcell",
  "line_type": "postpaid",
  "customer_type": "new",
  "packages": [ { ... paket ... }, { ... } ]
}
```

- `operator`, `line_type`, `customer_type` yalnızca **başlıkta** bir kez yazılır; her pakete
  tekrar yazma (ingest başlığı her pakete kendisi damgalıyor).
- `packages` içindeki her nesne **aşağıdaki anahtarların TAMAMINI** taşımalı (opsiyonel olanlar hariç).

---

## ALTIN ÖRNEK (postpaid, taahhütlü)

```json
{
  "name": "5G Ultra Dijital 5 GB",
  "package_group": "digital",
  "price_try": 350.0,
  "price_period": "month",
  "commitment_months": 12,
  "commitment_status": "committed",
  "price_lock_months": 12,
  "no_commitment_price_try": null,
  "internet_gb": 5,
  "social_media_gb": null,
  "bonus_internet_gb": null,
  "voice_minutes": 1000,
  "sms_count": 250,
  "validity_value": 1,
  "validity_unit": "month",
  "validity_days": 30,
  "conditions": "Yeni hat veya numara taşıma ile Turkcell Faturalı'ya gelen uygun bireysel ses hattı müşterilerine sunulur. Taahhütlü aylık fiyat sabit fiyat dönemi boyunca geçerlidir.",
  "campaign": "12 ay boyunca her ay sosyal medyada geçerli 5 GB hediye internet.",
  "source_url": "https://www.turkcell.com.tr/paket-ve-tarifeler/yeni-musteri-paketleri/5g-ultra-dijital-5-gb"
}
```

---

## BAŞLIK ALANLARI (enum)

| Alan | İzinli değerler |
|------|-----------------|
| `operator` | `turkcell` \| `vodafone` \| `turk_telekom` |
| `line_type` | `prepaid` (faturasız) \| `postpaid` (faturalı) |
| `customer_type` | `new` (yeni müşteri) \| `existing` (mevcut) \| `any` |

---

## PAKET ALANLARI

**Her pakette ZORUNLU (17 anahtar):**

| Alan | Tip | Kural |
|------|-----|-------|
| `name` | string | Sayfadaki paket adı, aynen. |
| `package_group` | string (enum, aşağıda) | Pazarlama ailesi; kitle/yaş/aile buradan türetilir. |
| `price_try` | number \| null | Postpaid'de **taahhütlü** fiyat. `499,90 TL` → `499.90`. |
| `price_period` | enum | Fiyatın karşılığı: `month` (faturalı, bir fatura ayı) \| `28_day` \| `60_day` \| `90_day` \| `180_day` \| `360_day` (faturasız, bir paket; `validity_days` ile aynı). |
| `renewal_type` | enum | `recurring` (kendiliğinden tekrar ücretlenir), `one_time` ("tek seferlik", "yenilenmez", "tekrar etmez"), `unknown` (sayfa söylemiyor). "Aylık" ya da 28 gün geçerlilikten çıkarım yapma. |
| `price_lock_months` | int \| null | Fiyatın sabit kalacağı ay sayısı. **Taahhütten bağımsız** (aşağı bak). |
| `internet_gb` | number \| null | Genel internet. Sınırsızsa `-1`. |
| `social_media_gb` | number \| null | Sosyal medya kotası. Sınırsızsa `-1`. Yoksa `null`. |
| `bonus_internet_gb` | number \| null | Ek/hediye internet. Yoksa `null`. |
| `bonus_internet_scope` | string \| null | Bonus varsa zorunlu: `general` (tüm kullanımlarda), `tethering_only` (yalnızca internet paylaşımı), `app_specific` (yalnızca belirli uygulama; uygulamayı `benefits` içinde `app_data` olarak da yaz). Bonus yoksa `null`. |
| `voice_minutes` | int \| null | Dakika. Sınırsızsa `-1`. |
| `sms_count` | int \| null | SMS. Sınırsızsa `-1`. |
| `validity_value` | int \| null | Geçerlilik miktarı (örn. `4`). |
| `validity_unit` | enum \| null | `day` \| `week` \| `month` |
| `validity_days` | int \| null | Faturasız paketin hak geçerliliği, gün (4 hafta → `28`, 3 ay → `90`). Faturalıda `null`. |
| `conditions` | string \| null | Koşullar metni, **aynen** (bkz. Kritik Kural 5). |
| `campaign` | string \| null | Kampanya/hediye metni, aynen. |
| `source_url` | string | Paketin birebir kaynak URL'i. |

**Yalnızca POSTPAID'de (2 ek zorunlu anahtar):**

| Alan | Tip | Kural |
|------|-----|-------|
| `commitment_months` | int \| null | Taahhüt süresi (ay). Taahhütsüzse `null`. |
| `commitment_status` | enum | `committed` \| `no_commitment` \| `unknown` |

> Prepaid dosyalarında `commitment_months`/`commitment_status` **yazma** (prepaid'de taahhüt yok).

**Opsiyonel (varsa yaz, yoksa hiç ekleme):**

| Alan | Tip | Kural |
|------|-----|-------|
| `no_commitment_price_try` | number \| null | Aynı tarifenin **taahhütsüz** (daha yüksek) fiyatı. Sayfa iki fiyat veriyorsa doldur. |

---

## `package_group` SÖZLÜĞÜ

Bu etiket kitle/yaş/aile türetimini sürükler. Uygun olanı seç; yeni bir aile görürsen
kebab/snake-case kısa bir ad ver ve bana bildir (yeni grup eklenebilir).

- **Genç kitle (youth):** `gnc`, `freezone`, `freezone_red`, `selfy_1plus1`
- **Çocuk / ilk hat (child):** `first_turkcell` (7-15 yaş), `first_line` (7-16), `hit` (6-15)
- **Aile (family):** `family_prime`, `family_start`
- **Kamu/kurum (public_special):** `new_parliamentarian`, `new_internal_affairs`
- **Genel/standart:** `digital`, `digital_leader`, `digital_leader_prime`, `energy`,
  `long_term`, `new_generation_prime`, `new_leader`, `new_leader_prime`, `platinum`,
  `platinum_black`, `red`, `prime_netflix`, `plus`, `wide`, `welcome`, `transfer`,
  `number_porting`, `new_line`, `online_plus`, `opportunity`, `province_special`,
  `customizable`, `standard`

---

## KRİTİK KURALLAR

**1. Ham gir, türetileni yazma.** Şu alanları **ASLA** üretme — bunları ingest pipeline'ı
`conditions`/`campaign`/`package_group`/GB alanlarından kendisi hesaplar:
`operator_family`, `audience_segment`, `product_scope`, `age_min`, `age_max`,
`eligibility_tags`, `doc_text` (fayda etiketleri ve genel kullanım interneti okunurken türetilir),
Sen sadece yukarıdaki paket alanlarını ver.

**2. Bilinmeyen = `null`, uydurma yok.** Sayfada olmayan sayıyı tahmin etme. Kritik alan
(fiyat, internet, taahhüt) eksikse `null` bırak — doğrulayıcı kaydı reddeder ve eksik bilgi
raporlanır. Uydurulmuş "temiz" veri, doğrulamanın amacını bozar.

**3. Sınırsız = `-1`.** "Sınırsız"/"limitsiz" internet/dakika/SMS için ilgili alana `-1` yaz
(metne "sınırsız" yazma). Örn. sınırsız dakika → `voice_minutes: -1`.

**4. Fiyat ikilisi.** Postpaid'de `price_try` = **taahhütlü** fiyattır. Sayfa "taahhütsüz
fiyat" da veriyorsa onu `no_commitment_price_try`'a koy. Prepaid'de `no_commitment_price_try`
hep boş (taahhüt kavramı yok).

**5. Taahhüt ≠ fiyat kilidi — ikisi ayrı.**
   - `commitment_months` / `commitment_status`: seni **bağlayan** taahhüt (erken çıkışta cezai şart).
   - `price_lock_months`: fiyatın **artmayacağı** süre; bağlamaz. Taahhütsüz bir paket bile
     `price_lock_months` taşıyabilir (örn. prepaid "Transfer" paketleri: `no_commitment` ama fiyat 12 ay sabit).
   - Değerleri çakışırsa bile ikisini de doldur; farklı olabilirler.

**6. `conditions` ve `campaign` metnini AYNEN koru — etiketler bunlardan türer.** Pipeline
şu ifadeleri kelimesi kelimesine arar; sayfada geçiyorsa metinde bırak:
   - Uygunluk: `yeni hat`, `numara taşı...`, `online`, `belirli iller` / `illere özel`
   - Avantaj: `sınırsız sosyal`, `sınırsız eğlence`, `netflix`, `avrupa` / `yurt dışı`
   - İçerik servis adlarını **aynen** yaz: **Netflix, Spotify, Amazon Prime, ChatGPT, YaaY,
     BluTV, Exxen** vb. (bunlardan bazıları henüz etiketlenmiyor; adları korursan sonradan
     etiketlenebilir). Servis adını parafraz etme.

**7. Toplam GB yazma.** Genel internet `internet_gb`, sosyal medya `social_media_gb`, ek internet `bonus_internet_gb` + `bonus_internet_scope`. Sosyal medya / paylaşım / uygulamaya özel kotayı genel internete ekleme.

**8. Geçerlilik üçlüsü tutarlı olsun.** `validity_value`+`validity_unit` ile `validity_days`
aynı süreyi göstermeli (4 hafta = 28 gün, 1 ay = 30 gün).

**9. Bir bucket = bir dosya.** Farklı operator/line_type/customer_type'ı karıştırma; her
kombinasyon ayrı dosya.

---

## ÇIKTIDAN ÖNCE ÖZ-KONTROL

- [ ] Her paket tam olarak 17 zorunlu anahtarı taşıyor (postpaid ise +2); fazla/eksik yok.
- [ ] Enum alanları izinli değerlerden birinde.
- [ ] Sınırsızlar `-1`, bilinmeyenler `null`; hiçbir sayı uydurulmadı.
- [ ] Toplam GB alanı yok; ek internetin kapsamı (`bonus_internet_scope`) yazıldı.
- [ ] `conditions`/`campaign` kaynaktaki anahtar ifadeleri ve servis adlarını aynen içeriyor.
- [ ] `source_url` her pakette gerçek ve pakete özel.
- [ ] Türetilen alanlar (operator_family, tags, doc_text, …) **yazılmadı**.

## Ingest validation (enforced by `app/ingest/validate.py`)

- No `total_internet_gb`. An unlimited social quota never makes general internet unlimited.
- Prepaid `price_period` must be `<validity_days>_day`. A package bought once that does not renew ("tekrar etmez", "bir kez faydalanabilir") is `renewal_type: one_time`; its period is still its validity (e.g. `90_day`).
- Activation channel: write "yeni hat" / "numara taşıma" into `conditions` only when the source grants eligibility through it. A fee clause ("yeni hat tesisinde … ücreti dahil değildir") is not eligibility.


## Provenance fields (enforced by `validate_provenance`)

The data files hold **current offers only**. Do not add closed, replaced or unconfirmed offers.

- `verified_at` (ISO date, required): the date the facts were checked on the official page.
- `evidence` (required): a quote from the official page supporting the record (price, allowances, eligibility). Never a closure notice: a page saying "müşteri alımına kapatılmıştır", "abone alımına kapalıdır" or "Kampanya Bitti" means the offer does not belong in the file.
- `history` (optional): when a verified update replaces a value, keep the old values as `[{"as_of": "<previous date>", "fields": {...}}]`.
- A missing listing or broken URL is not proof that an offer ended. If a plan cannot be confirmed, leave it out.
- A relaunch under the same name keeps the record (update price/URL, old values in `history`); a renamed successor is a new record.
- Write the channel into `conditions` only from an eligibility statement (e.g. "Sadece son 90 gün içerisinde yeni hat tesisi yoluyla gelen müşteriler", Vodafone `candidateType` NEW/MNP).


## Canonical fields (enforced by `validate.py`)

Every record states what its official page says, **including "nothing" and "unknown"**:

- `activation_channels` (required): `["new_line"]`, `["number_porting"]` or both, only from an eligibility sentence ("Tarife Kapsamı", "Kimler yararlanabilir", Vodafone `candidateType`). Not stated → `null`. Never infer it from a fee clause.
- `benefits_status` (required): `verified` (the page was read and lists ≥ 1 benefit), `verified_none` (read, nothing listed), `unverified` (terms not readable, e.g. rendered by JavaScript).
- `benefits` (required list; `[]` for `verified_none` / `unverified`). One object per benefit:
  - `category`: `subscription` | `app_data` | `bonus_data` | `voucher` | `discount` | `roaming` | `service`
  - `offer`: `included` | `free_trial` | `discounted` | `requires_purchase`
  - `service` (required); optional `duration_months`, `after_trial` (`paid`/`ends`/`unknown`, required for `free_trial`), `after_trial_price_try`, `amount_gb` (`-1` unlimited), `apps`, `value_try`, `eligibility` (`new_line_only`), `activation`, `note`.
- `social_media_apps` (optional): the apps a `social_media_gb` quota covers, as listed on the page.
- `promo_months` + `price_after_promo_try` (optional, together): an introductory price ("İlk ay 100 TL, ikinci ay ve takip eden aylarda 190 TL").
- `eligibility_tags` (optional): `dealer_only`, `partner_channel_only`, `province_restricted` when the page restricts the sales channel or region. Never `new_line`/`number_porting` (use `activation_channels`) or `age_restricted` (use `age_min`/`age_max`).

Classification rules:
- "X'te sınırsız internet" / "X kullanımları sınırsızdır" is `app_data` for X, **not** a subscription to X.
- "… için verilen internet hakkına servis hizmeti dahil değildir" → `app_data` with `offer: requires_purchase`.
- "ilk 6 ay hediye", "3 ay ücretsiz" → `free_trial` with `duration_months`; `after_trial: paid` only if the page names a later fee, otherwise `unknown`.
- "%35 indirim", "7.000 TL'ye varan indirim", "1 alana 1 hediye" → `discount`, never free.
- A gift only for new lines ("Yeni Hat Alanlara 10 GB Hediye") → `bonus_data` with `eligibility: new_line_only`; do not add it to `bonus_internet_gb`.
- An operator-wide programme (Bi' Dünya Fırsat, Prime/Platinum/Red ayrıcalıkları) is listed only when the tariff page lists it, as `discount`/`service`.
