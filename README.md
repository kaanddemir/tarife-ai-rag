# TarifeAI

<div align="center">
  <img src="assets/tarifeai-logo.png" alt="TarifeAI Logo" width="320" />
</div>

<div align="center">

**A Retrieval-Augmented AI assistant that compares Turkish prepaid and postpaid mobile plans.**

</div>

---

## Overview

TarifeAI answers questions in natural Turkish about **Turkcell**, **Vodafone**, and **Türk Telekom** phone plans. The backend retrieves matching plans with a hybrid of SQL filters and pgvector similarity, the model answers **using only those retrieved rows**, and the UI renders the answer next to a comparison table built from the same rows. Every price, GB, minute, or campaign claim is tied back to a cited source.

Built with FastAPI, SQLAlchemy, PostgreSQL + pgvector, BGE-M3 embeddings, an OpenAI-compatible LLM, and a Next.js 14 frontend.

## Core Features

- **Natural-Language Q&A**: Ask things like `Öğrenciye en iyi paket`, `20 GB paketleri karşılaştır`, or `150 TL altı seçenek var mı?`.
- **Grounded Answers**: The model sees only the retrieved plans; unsupported facts are refused, not invented.
- **Inline Citations**: Wikipedia-style superscript references link to the operator's own page and match the source cards below.
- **Comparison Table**: The exact plans behind an answer, side by side (total GB, minutes, SMS, price, validity), recommendation first.
- **Hybrid Retrieval**: Line type, eligibility, age, audience, and phone-only scope stay hard SQL constraints; preferences are ranked by cosine similarity.
- **Server-Side Grounding**: Citations and table rows are built from real retrieved records, so a fabricated source marker cannot reach the UI.
- **Prompt-Injection Hardening**: Sources, question, and history are fenced separately; only the sources block is treated as data.
- **Auth & Quotas**: Supabase Auth (anonymous guests, email magic link). `/chat` verifies the JWT and enforces per-user and per-IP message quotas server-side, so the paid LLM can't be abused.
- **Conversation History**: Chats persist in the browser; the last 5 turns feed follow-up questions.

## Current Limitations

- **Curated Snapshot**: 123 hand-curated plans in `backend/app/ingest/data/*.json`, not a live feed. Prices go stale until the files are updated and re-ingested — confirm against the operator's page, which every answer cites.
- **Phone Plans Only, Turkish Only**: Retrieval is scoped to `mobile_voice`; home internet, TV, and device bundles are out. Prompts, UI copy, and the `faturalı` / `faturasız` keywords are Turkish.
- **Two-Piece Deployment**: The frontend is a static export and can sit anywhere; the backend needs its own server for Python, BGE-M3, and pgvector. The first ingest downloads the model.
- **Single-Process Rate Limit**: The per-IP burst limit lives in process memory, so behind multiple workers a reverse proxy has to enforce the real one. Message quotas are in the database and unaffected.
- **No Migrations**: The catalog is a full refresh, so a schema change drops and rebuilds the `packages` table. Quota data (`message_usage`) is left intact.
- **Bounded, Non-Streaming Answers**: One reply recommends at most three plans and arrives whole, so a long one shows the loading skeleton until complete.

## Tech Stack

- **Backend**: [FastAPI](https://fastapi.tiangolo.com/), [Uvicorn](https://www.uvicorn.org/), [SlowAPI](https://github.com/laurentS/slowapi)
- **Data**: [SQLAlchemy](https://www.sqlalchemy.org/), [PostgreSQL](https://www.postgresql.org/) + [pgvector](https://github.com/pgvector/pgvector), [BGE-M3](https://huggingface.co/BAAI/bge-m3) via [FlagEmbedding](https://github.com/FlagOpen/FlagEmbedding)
- **LLM**: [OpenAI Python SDK](https://github.com/openai/openai-python) against an OpenAI-compatible provider ([Groq](https://groq.com/) by default)
- **Frontend**: [Next.js 14](https://nextjs.org/), [React 18](https://react.dev/), TypeScript
- **Testing**: [pytest](https://docs.pytest.org/)

## Getting Started

Requires Python 3.10+, Node.js 18.17+, a PostgreSQL database with pgvector (a free [Supabase](https://supabase.com/) project works), and an OpenAI-compatible provider key.

**1. Database.** In Supabase: **Connect → Direct → Connection string → Session pooler** (IPv4, port 5432). Change `postgresql://` to `postgresql+psycopg://` and append `?sslmode=require`. Do **not** use the transaction pooler (port 6543). The `vector` extension is enabled automatically on first run.

**2. Backend.**

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt        # add -r requirements-dev.txt for tests
cp .env.example .env                   # then set DATABASE_URL and PROVIDER_API_KEY
python -m app.ingest.load              # loads the 123 curated plans
```

Every setting is documented in `.env.example`. `DATABASE_URL` and `PROVIDER_API_KEY` are required; the backend refuses to start without a provider key.

> After the first load you can enable RLS on the `packages` table
> (`ALTER TABLE packages ENABLE ROW LEVEL SECURITY;`) to block public REST access — the
> backend connects as `postgres` and is unaffected.

**3. Auth.** In Supabase → **Authentication → Providers**, enable **Anonymous sign-ins** and **Email**. Under **URL Configuration**, add `http://localhost:3000` and your production domain. Copy the **Project URL** and **anon public key** into the frontend env below.

**4. Frontend.**

```bash
cd ../frontend
npm install
cp .env.example .env.local   # set NEXT_PUBLIC_SUPABASE_URL + NEXT_PUBLIC_SUPABASE_ANON_KEY
```

### Run

```bash
python run.py
```

One command starts both servers:

```text
  TarifeAI
  ────────────────────────────────────────────
  Open      http://localhost:3000
  API       http://localhost:8000/docs
  Model     qwen/qwen3-32b
  Stop      Ctrl+C
```

Ctrl+C stops both, and if either server exits on its own the other is stopped too. Missing setup — no virtualenv, no `backend/.env`, no `node_modules`, a port already taken — is reported before anything starts, with the command that fixes it.

Optional: `API_PORT` (8000), `WEB_PORT` (3000), `RELOAD` (`1` restarts the API on file changes, off by default).

To run the halves separately: `uvicorn app.main:app --port 8000` in `backend/`, `npm run dev` in `frontend/`.

## Deploy

> The two halves deploy separately. The frontend is a static export and can go on any static host; the backend needs its own server (Render / Railway / Fly, etc.). Until a backend is reachable, the site loads but chat requests fail with a graceful "Sunucuya ulaşılamadı." message.

Every `NEXT_PUBLIC_*` value is inlined at build time, so set them for the build:

```bash
cd frontend
NEXT_PUBLIC_API_BASE=https://<your-backend-host> \
NEXT_PUBLIC_SUPABASE_URL=https://<ref>.supabase.co \
NEXT_PUBLIC_SUPABASE_ANON_KEY=<anon public key> \
NEXT_PUBLIC_SITE_URL=https://<your-site> \
npm run build
```

Upload the resulting `frontend/out/`. For sub-path hosting (e.g. `<user>.github.io/TarifeAI`) also set `NEXT_PUBLIC_BASE_PATH=/TarifeAI`. Add the deployed URL to Supabase's redirect allow-list, or magic-link sign-in fails, and set the backend's `CORS_ORIGINS` to the deployed origin.

## Project Structure

```text
TarifeAI/
├── backend/app/
│   ├── ingest/         # data/*.json → normalize → embed → upsert (load.py)
│   ├── rag/            # embed, retrieve, prompt, generate, clarify, history
│   ├── main.py         # FastAPI app, routes, CORS, rate limiting
│   ├── auth.py         # Supabase JWT verification
│   ├── quota.py        # per-user and per-IP message quotas
│   ├── config.py       # env-driven settings
│   ├── db.py           # engine, session, init
│   └── models.py       # SQLAlchemy ORM + Pydantic schemas
├── frontend/
│   ├── app/            # Next.js App Router (page, layout, icon, styles)
│   ├── components/     # Sidebar, ChatArea, AnswerMarkdown, ComparisonTable, ...
│   └── lib/            # API client, auth, storage, types
├── run.py              # starts backend + frontend; Ctrl+C stops both
└── DISCLAIMER.md       # operator trademark and data accuracy notice
```

## Data

The 123 plans in `backend/app/ingest/data/*.json` are the source of truth, one file per operator and customer bucket. `python -m app.ingest.load` normalizes each record, builds a natural-language `doc_text`, embeds it, and does a full refresh of the `packages` table. See `backend/app/models.py` for the field list and `backend/app/ingest/EXTRACTION_PROMPT.md` for the schema new records must match.

## API

- `GET /health` — liveness check (no database access)
- `GET /packages` — filtered plan listing
- `POST /compare` — side-by-side comparison rows
- `POST /chat` — grounded RAG answer (requires a Supabase bearer token)
- `GET /docs` — Swagger explorer

`POST /chat` takes `question` (1–400 chars) and optional `history` (up to 8 turns of `{role, content}`, last 5 used; content up to 3500 chars). It returns:

```json
{
  "answer": "**Öneri:** ... [S1]",
  "citations": [{ "id": "S1", "operator": "vodafone", "name": "...", "source_url": "https://..." }],
  "packages": [{ "id": "...", "operator": "vodafone", "price_try": 300, "total_internet_gb": 60, "voice_minutes": -1 }]
}
```

`citations` and `packages` are always grounded in the retrieved rows. `-1` means unlimited (sınırsız).

## Testing

```bash
cd backend && source .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest tests/ -q --ignore=tests/test_rag_e2e.py   # e2e needs a live DB and LLM
cd ../frontend && npm run build                             # verify the static export
```

## Runtime Notes

- Provider errors (timeout / 5xx / auth) return a graceful message instead of a raw 500, and retrieval falls back to pure semantic ranking when hard filters match nothing.
- The provider key lives only in `backend/.env` (gitignored) and is never returned in any response or error.
- `SUPABASE_JWT_SECRET` has no default. Left unset, HS256 tokens are rejected outright and only Supabase's asymmetric (ES256/RS256) tokens are verified against the project's public JWKS — so no shipped placeholder can sign a valid token.
- Schema changes require dropping the `packages` table; `init_db()` only runs `create_all` and cannot alter existing columns.

## License

This project is licensed under the [MIT License](LICENSE).

Turkcell, Vodafone, and Türk Telekom are trademarks of their respective owners; see
[DISCLAIMER](DISCLAIMER.md).

## Footer

<div align="center">
  <p>Built by <a href="https://heykaan.dev">heykaan.dev</a></p>
</div>
