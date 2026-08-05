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
  "total_internet_gb": 5,
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
| `price_period` | enum | `month` \| `28_day` \| `3_month` \| `6_month` \| `12_month` \| `one_time` |
| `price_lock_months` | int \| null | Fiyatın sabit kalacağı ay sayısı. **Taahhütten bağımsız** (aşağı bak). |
| `internet_gb` | number \| null | Genel internet. Sınırsızsa `-1`. |
| `social_media_gb` | number \| null | Sosyal medya kotası. Sınırsızsa `-1`. Yoksa `null`. |
| `bonus_internet_gb` | number \| null | Bonus/paylaşım internet. Yoksa `null`. |
| `total_internet_gb` | number \| null | Başlık/toplam GB = genel + sosyal + bonus. Sınırsızsa `-1`. |
| `voice_minutes` | int \| null | Dakika. Sınırsızsa `-1`. |
| `sms_count` | int \| null | SMS. Sınırsızsa `-1`. |
| `validity_value` | int \| null | Geçerlilik miktarı (örn. `4`). |
| `validity_unit` | enum \| null | `day` \| `week` \| `month` |
| `validity_days` | int \| null | Gün cinsinden (4 hafta → `28`, 1 ay → `30`). |
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
`eligibility_tags`, `benefit_tags`, `doc_text`, `data_quality_status`, `missing_fields`,
`canonical_id`. Sen sadece yukarıdaki paket alanlarını ver.

**2. Bilinmeyen = `null`, uydurma yok.** Sayfada olmayan sayıyı tahmin etme. Kritik alan
(fiyat, internet, taahhüt) eksikse `null` bırak — pipeline paketi otomatik `review_required`
işaretler. Uydurulmuş "temiz" veri, doğrulamanın amacını bozar.

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

**7. `total_internet_gb` = genel + sosyal + bonus.** Sınırsız bileşen varsa toplam da `-1`.

**8. Geçerlilik üçlüsü tutarlı olsun.** `validity_value`+`validity_unit` ile `validity_days`
aynı süreyi göstermeli (4 hafta = 28 gün, 1 ay = 30 gün).

**9. Bir bucket = bir dosya.** Farklı operator/line_type/customer_type'ı karıştırma; her
kombinasyon ayrı dosya.

---

## ÇIKTIDAN ÖNCE ÖZ-KONTROL

- [ ] Her paket tam olarak 17 zorunlu anahtarı taşıyor (postpaid ise +2); fazla/eksik yok.
- [ ] Enum alanları izinli değerlerden birinde.
- [ ] Sınırsızlar `-1`, bilinmeyenler `null`; hiçbir sayı uydurulmadı.
- [ ] `total_internet_gb` = bileşenlerin toplamı.
- [ ] `conditions`/`campaign` kaynaktaki anahtar ifadeleri ve servis adlarını aynen içeriyor.
- [ ] `source_url` her pakette gerçek ve pakete özel.
- [ ] Türetilen alanlar (operator_family, tags, doc_text, …) **yazılmadı**.
