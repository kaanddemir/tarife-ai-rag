# TarifeAI

<div align="center">
  <img src="assets/tarifeai-logo.png" alt="TarifeAI Logo" width="320" />

**Türkiye’deki mobil tarifeler için kaynaklı karşılaştırma ve öneri asistanı.**
</div>

## TarifeAI Nedir?

TarifeAI, Türkiye’deki mobil tarifeleri doğal Türkçe sorularla keşfetmeyi ve karşılaştırmayı kolaylaştırır. Yanıtlarını doğrulanmış tarife kayıtlarına dayandırır ve ilgili operatör sayfalarını kaynak olarak gösterir.

Proje henüz herkese açık olarak yayımlanmamıştır. Yerel geliştirme ve ileride yapılacak kontrollü bir dağıtım için hazırlanmıştır.

## Neler Yapabilir?

- Turkcell, Vodafone ve Türk Telekom tarifelerini arar.
- Faturalı ve faturasız paketleri destekler.
- Fiyat, internet, dakika, SMS ve geçerlilik süresini karşılaştırır.
- Yaş, yeni hat, numara taşıma ve taahhüt gibi koşulları dikkate alır.
- Paket faydalarını ve kampanya bilgilerini gösterir.
- Yanıtları resmi kaynaklarla ilişkilendirir.
- Konuşma geçmişini tarayıcıda saklar.
- Supabase ile anonim veya e-posta bağlantılı giriş sunar.

## Nasıl Çalışır?

1. Kullanıcı ihtiyacını doğal Türkçeyle yazar.
2. Sorgudaki fiyat, operatör, hat türü ve kullanım ihtiyacı gibi koşullar çıkarılır.
3. SQL filtreleri ve BGE-M3 anlam benzerliği uygun tarifeleri bulur.
4. Dil modeli yalnızca bulunan kayıtlara dayanarak yanıt üretir.
5. İlgili tarifeler, karşılaştırmalar ve resmi kaynaklar arayüzde gösterilir.

## Kullanılan Teknolojiler

- **Ön yüz:** Next.js 14, React 18, TypeScript
- **Arka uç:** Python 3.12, FastAPI, SQLAlchemy
- **Veritabanı:** PostgreSQL, pgvector
- **Kimlik doğrulama:** Supabase Auth
- **Gömme modeli:** BGE-M3 (1024 boyut)
- **Dil modeli:** Groq uyumlu OpenAI API bağlantısı
- **Testler:** pytest ve Node test çalıştırıcısı

## Kurulum

Gereksinimler:

- Python 3.12
- Node.js 22
- pgvector destekli yerel veya geliştirme amaçlı PostgreSQL
- OpenAI uyumlu bir sağlayıcı anahtarı

Önce arka uç bağımlılıklarını kurun:

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Yeni veritabanına güncel şemayı yönetici bağlantısıyla kurun:

```bash
export TARIFEAI_ADMIN_DATABASE_URL='postgresql+psycopg://...'
python scripts/migrate.py --database-url-env TARIFEAI_ADMIN_DATABASE_URL install
```

Bu komut yalnızca boş bir kurulum içindir. Mevcut üretim veritabanına uygulanmamalıdır.

`backend/.env` dosyasında en az şu değerleri ayarlayın:

```dotenv
DATABASE_URL=postgresql+psycopg://...
PROVIDER_API_KEY=...
SUPABASE_URL=https://<proje>.supabase.co
```

Ardından doğrulanmış katalogu yükleyin:

```bash
python -m app.ingest.load
```

Ön yüz bağımlılıklarını kurun:

```bash
cd ../frontend
npm ci
cp .env.example .env.local
```

`frontend/.env.local` içinde Supabase proje adresini ve anonim açık anahtarı ayarlayın.

Uygulamayı depo kökünden başlatın:

```bash
cd ..
python run.py
```

Ön yüz `http://localhost:3000`, API belgeleri `http://localhost:8000/docs` adresinde açılır.

Temel kontroller:

```bash
cd backend && .venv/bin/python -m pytest tests -q
cd ../frontend && npm run typecheck && npm test && npm run build
```

PostgreSQL bütünleşme testleri yalnızca açıkça verilen geçici yerel test veritabanında çalışır. Normal testler üretim veritabanına veya Groq’a bağlanmaz.

## Proje Yapısı

```text
TarifeAI/
├── backend/
│   ├── app/             # API, kimlik, kota, veri yükleme ve RAG
│   ├── schema/          # güncel veritabanı kurulum şeması
│   ├── scripts/         # kurulum, denetim ve bakım araçları
│   └── tests/           # arka uç testleri
├── frontend/
│   ├── app/             # Next.js sayfaları ve stiller
│   ├── components/      # arayüz bileşenleri
│   ├── lib/             # API, kimlik ve tarife yardımcıları
│   └── tests/           # ön yüz testleri
├── .github/workflows/   # ön yüz ve arka uç kontrolleri
└── run.py               # yerel geliştirme başlatıcısı
```

## Veri Kaynakları

Katalogda Turkcell, Vodafone ve Türk Telekom’a ait 105 güncel tarife bulunur. Faturalı ve faturasız paketler 12 Ekim 2026 tarihinde operatörlerin resmi sayfalarından doğrulanmıştır.

Her kayıtta kaynak adresi, doğrulama tarihi, kanıt metni ve varsa önceki değerlerin geçmişi bulunur.

Veriler gerçek zamanlı olarak eşitlenmez. Güncel fiyat ve koşullar için her zaman bağlantısı verilen resmi operatör sayfası kontrol edilmelidir.

## Lisans

Proje [MIT Lisansı](LICENSE) ile sunulur. Operatör markaları ve veri doğruluğu hakkında bilgi için [sorumluluk reddi metnine](DISCLAIMER.md) bakın.

<div align="center">
  <p><a href="https://heykaan.dev">heykaan.dev</a> tarafından geliştirildi.</p>
</div>
