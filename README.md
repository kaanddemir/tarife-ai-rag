# TarifeAI

<div align="center">
  <img src="frontend/app/icon.svg" alt="TarifeAI Logo" width="96" />
</div>

<div align="center">

**A Retrieval-Augmented AI assistant that compares Turkish prepaid mobile plans.**

</div>

---

> **Status:** **Demo / Portfolio Project.** The package catalog is a curated snapshot of Turkcell, Vodafone, and Türk Telekom prepaid plans and can go stale as operators change their offers. Prompt behavior, ranking, and UI details may change as the pipeline is refined.

## Overview

TarifeAI is a grounded question-answering tool for Turkey's **Turkcell**, **Vodafone**, and **Türk Telekom** prepaid (faturasız) mobile plans, built with FastAPI, SQLAlchemy, PostgreSQL + pgvector, BGE-M3 embeddings, and an OpenAI-compatible LLM.

A user asks a question in natural Turkish. The backend retrieves the relevant plans from the database with a hybrid of SQL filters and vector similarity, the model answers **using only those retrieved sources**, and the Next.js UI renders the answer as Markdown alongside a separate comparison table built from the real plan rows. Every concrete price, GB, minute, SMS, or campaign claim is tied back to a cited source.

## Core Features

### Chat & Comparison UI

- **Natural-Language Q&A**: Ask things like `Öğrenciye en iyi paket`, `20 GB paketleri karşılaştır`, or `150 TL altı seçenek var mı?`.
- **Grounded Answers**: The model only uses the retrieved plan data; unsupported facts are refused, not invented.
- **Inline Citations**: Claims carry Wikipedia-style superscript references that link to the official source and match the source cards below.
- **Comparison Table**: The exact plans behind an answer are rendered as a side-by-side table (headline total GB, minutes, SMS, price, validity), recommendation first.
- **Conversation History**: Chats persist in the browser via LocalStorage; the last few turns feed follow-up questions.

### RAG Pipeline

- **Hybrid Retrieval**: Parses hard constraints (operator, max price, GB band) into SQL filters, then ranks the filtered set by pgvector cosine similarity.
- **Rich Plan Schema**: Captures total internet, social-media and bonus quotas, price period, package group / target audience, and validity in days.
- **Contextual Follow-ups**: The last 5 user turns enrich both the retrieval query and the prompt without treating prior assistant text as fact.
- **Server-Side Citation Grounding**: Citation cards and the comparison table are built from real retrieved rows, so a fabricated source marker cannot leak into the UI.

### Runtime Model

- **External LLM via OpenAI-Compatible API**: Generation runs against a hosted provider (Groq by default; Together / OpenRouter also work).
- **Prompt-Injection Hardening**: Sources, question, and history are fenced in distinct blocks; the system prompt treats only the sources block as real data.
- **Auth & Message Quotas**: Supabase Auth (anonymous guests and email magic link). Guests get a small free allowance (with a per-IP daily backstop); signed-in users get a per-day quota. `/chat` verifies the Supabase JWT and enforces the quota server-side, so the paid LLM can't be abused. A coarse per-IP burst limit stays as a backstop.
- **Bounded Input**: Request size is capped (question length, history depth, per-turn content) so oversized payloads are rejected early.

## Tech Stack

- **Backend**: [FastAPI](https://fastapi.tiangolo.com/)
- **Server**: [Uvicorn](https://www.uvicorn.org/)
- **ORM & DB**: [SQLAlchemy](https://www.sqlalchemy.org/), [PostgreSQL](https://www.postgresql.org/), [pgvector](https://github.com/pgvector/pgvector)
- **Embeddings**: [BGE-M3](https://huggingface.co/BAAI/bge-m3) via [FlagEmbedding](https://github.com/FlagOpen/FlagEmbedding)
- **LLM Client**: [OpenAI Python SDK](https://github.com/openai/openai-python) against an OpenAI-compatible provider ([Groq](https://groq.com/))
- **Rate Limiting**: [SlowAPI](https://github.com/laurentS/slowapi)
- **Frontend**: [Next.js 14](https://nextjs.org/), [React 18](https://react.dev/), TypeScript
- **Testing**: [pytest](https://docs.pytest.org/)

## Getting Started

### Requirements

- Python 3.10+
- Node.js 18.17+
- A PostgreSQL database with the `vector` (pgvector) extension — a free [Supabase](https://supabase.com/) project works out of the box
- A local virtual environment at `backend/.venv` when using `start.sh`
- An OpenAI-compatible provider API key (Groq by default) for chat answers

The first embedding run downloads the BGE-M3 model, which takes time and disk space.

### Setup

1. **Create a Supabase project** (free tier is enough)
   - In the dashboard: **Connect → Direct → Connection string → Session pooler** (IPv4,
     port 5432). Copy the string and put your database password in it.
   - Convert it for SQLAlchemy: change `postgresql://` to `postgresql+psycopg://` and
     append `?sslmode=require`. Do **not** use the transaction pooler (port 6543).
   - The `vector` (pgvector) extension is enabled automatically by `init_db()` on first
     run, so no manual SQL is needed.

2. **Set up the backend**
   ```bash
   cd backend
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt        # add -r requirements-dev.txt for tests
   cp .env.example .env
   ```
   Edit `backend/.env` and set the database URL and provider key:
   ```env
   DATABASE_URL=postgresql+psycopg://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require
   PROVIDER_BASE_URL=https://api.groq.com/openai/v1
   PROVIDER_API_KEY=your_api_key_here
   MODEL=your_model_id
   EMBED_MODEL=BAAI/bge-m3
   TOP_K=8
   CORS_ORIGINS=http://localhost:3000,http://127.0.0.1:3000
   CHAT_RATE_LIMIT=10/minute
   COMPARE_RATE_LIMIT=30/minute
   ```
   > After the first `load` (next step) you can enable RLS on the `packages` table in
   > Supabase (`ALTER TABLE packages ENABLE ROW LEVEL SECURITY;`) to block public REST
   > access — the backend connects as `postgres` and is unaffected.

3. **Load the plan data** (49 curated plans across the three operators)
   ```bash
   python -m app.ingest.load
   ```

4. **Enable authentication (Supabase Auth)**
   - In the dashboard: **Authentication → Providers** → enable **Anonymous sign-ins**
     (guest access) and **Email** (magic link, on by default).
   - **Authentication → URL Configuration**: add your prod domain and
     `http://localhost:3000` to Site URL / redirect URLs.
   - Copy **Settings → API → JWT Secret** into `backend/.env` as `SUPABASE_JWT_SECRET`,
     and the **Project URL** + **anon public key** into the frontend env below.

5. **Set up the frontend**
   ```bash
   cd ../frontend
   npm install
   cp .env.example .env.local   # set NEXT_PUBLIC_SUPABASE_URL + NEXT_PUBLIC_SUPABASE_ANON_KEY
   ```

### Run

1. **Start with the project script**
   ```bash
   ./start.sh
   ```

2. **Open the app**
   ```text
   http://localhost:3000
   ```

3. **Stop the servers**
   ```bash
   ./stop.sh
   ```

Manual run:

```bash
# backend
cd backend && source .venv/bin/activate
uvicorn app.main:app --reload --port 8000

# frontend
cd frontend && npm run dev
```

## Project Structure

```text
TarifeAI/
├── backend/
│   ├── app/
│   │   ├── ingest/
│   │   │   ├── data/             # Curated per-operator plan JSON (turkcell/vodafone/turk_telekom)
│   │   │   ├── load.py           # Load data/*.json → normalize → embed → upsert
│   │   │   └── normalize.py      # Typed fields + natural-language doc_text
│   │   ├── rag/
│   │   │   ├── embed.py          # BGE-M3 embeddings
│   │   │   ├── retrieve.py       # SQL filters + pgvector similarity
│   │   │   ├── prompt.py         # Grounded, fenced prompt + source rendering
│   │   │   ├── generate.py       # LLM call + citation filtering
│   │   │   └── history.py        # Bounded conversation context (last 5 turns)
│   │   ├── config.py             # Env-driven settings
│   │   ├── db.py                 # Engine, session, init
│   │   ├── main.py               # FastAPI app, routes, CORS, rate limiting
│   │   └── models.py             # SQLAlchemy ORM + Pydantic schemas
│   ├── tests/                    # normalize, retrieve, and RAG e2e tests
│   ├── requirements.txt
│   ├── requirements-dev.txt
│   └── .env.example
├── frontend/
│   ├── app/                      # Next.js App Router (page, layout, icon, styles)
│   ├── components/               # Sidebar, ChatArea, MessageBubble, AnswerMarkdown,
│   │                             #   CitationSources, ComparisonTable, OperatorPicker
│   ├── lib/                      # API client, storage, types
│   └── .env.example
├── LICENSE
├── start.sh
├── stop.sh
└── README.md
```

## Data

The catalog is curated and local-first:

- **Source Files**: Per-operator plans live in `backend/app/ingest/data/*.json` (49 plans total) and are the source of truth.
- **Database**: `python -m app.ingest.load` normalizes each record, builds a natural-language `doc_text`, embeds it, and does a full refresh of the `packages` table.
- **Key Fields**: `operator`, `name`, `price_try`, `price_period`, `internet_gb`, `social_media_gb`, `bonus_internet_gb`, `total_internet_gb`, `voice_minutes`, `sms_count`, `validity_days`, `package_group`, `conditions`, `campaign`, `source_url`, `doc_text`, `embedding`.

Inspect the loaded data with:

```bash
psql -d tarifeai -c "SELECT operator, name, total_internet_gb, price_try FROM packages ORDER BY price_try;"
```

## Routes

- `GET /` (frontend) - main chat UI
- `GET /docs` - Swagger API explorer
- `GET /health` - health check and package count
- `GET /packages` - filtered plan listing
- `POST /compare` - side-by-side comparison rows
- `POST /chat` - grounded RAG answer

## Chat API

`POST /chat` accepts `application/json` and is rate-limited per IP (`CHAT_RATE_LIMIT`, default `10/minute`).

| Field | Required | Notes |
| --- | --- | --- |
| `question` | required | User question. 1–400 characters. |
| `history` | optional | Prior turns for context. Up to 8 turns; only the last 5 are used. |
| `history[].role` | required | `user` or `assistant`. |
| `history[].content` | required | Turn text, up to 3500 characters. |

The response shape is:

```json
{
  "answer": "**Öneri:** ...",
  "citations": [
    { "id": "S1", "operator": "vodafone", "name": "...", "source_url": "https://..." }
  ],
  "packages": [
    {
      "id": "...",
      "operator": "vodafone",
      "name": "...",
      "line_type": "prepaid",
      "price_try": 300,
      "total_internet_gb": 60,
      "voice_minutes": -1,
      "sms_count": -1,
      "validity_days": 30,
      "source_url": "https://..."
    }
  ]
}
```

`citations` and `packages` are always grounded in the retrieved rows. A `voice_minutes` or `sms_count` of `-1` means unlimited (sınırsız).

## Testing

Run the backend suite:

```bash
cd backend && source .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest tests/test_normalize.py tests/test_retrieve.py -q
```

`tests/test_rag_e2e.py` requires a live database and LLM, so run it separately once those are configured.

Verify the frontend build:

```bash
cd frontend && npm run build
```

## Runtime Notes

- Generation runs against an OpenAI-compatible provider; provider errors (timeout / 5xx / auth) are caught and return a graceful message instead of a raw 500.
- Retrieval falls back to pure semantic ranking when hard filters match nothing, so a user always gets an answer.
- Schema changes require dropping the `packages` table — `init_db()` only runs `create_all` and cannot alter existing columns.
- The provider key lives only in `backend/.env` (gitignored); it is never returned in any API response or error.

## License

This project is licensed under the [MIT License](LICENSE).

## Footer

<div align="center">
  <p>Built by <a href="https://heykaan.dev">heykaan.dev</a></p>
</div>
