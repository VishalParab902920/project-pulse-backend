# Project Pulse — Backend

FastAPI-based Intelligence & Orchestration Server for Project Pulse.

## Tech Stack

- **Framework:** FastAPI + Uvicorn
- **AI:** Google Gemini 3.1 Flash Lite (via `google-genai` SDK)
- **Database:** Supabase PostgreSQL + pgvector (via SQLAlchemy)
- **Storage:** Supabase Storage (image uploads)
- **Encryption:** Fernet AES-256 (BYOK key storage)

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/health` | System health check (DB + AI) |
| POST | `/api/v1/parse` | Parse text input (food/workout/biometric) |
| POST | `/api/v1/parse/audio` | Two-pass audio transcription + parsing |
| POST | `/api/v1/parse/image` | Vision analysis + optional caption |
| GET | `/api/v1/entries/pending` | Fetch pending review queue |
| GET | `/api/v1/entries/pending/count` | Badge count |
| PATCH | `/api/v1/entries/{id}` | Confirm/edit entry |
| GET | `/api/v1/profile` | Get user profile |
| PATCH | `/api/v1/profile` | Update profile |
| POST | `/api/v1/profile/byok` | Save encrypted API key |
| POST | `/api/v1/profile/byok/test` | Test API key validity |
| DELETE | `/api/v1/profile/byok` | Remove stored key |
| POST | `/api/v1/memory` | Store semantic memory |
| GET | `/api/v1/memory/search` | Vector similarity search |
| POST | `/api/v1/sync/health` | Sync biometric data |
| GET | `/api/v1/analytics` | Dashboard aggregations + AI synthesis |

## Setup

```bash
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS/Linux

pip install -r requirements.txt
cp .env.example .env         # Fill in your values
uvicorn app.main:app --reload --port 8000
```

API docs available at: http://localhost:8000/docs

## Docker

```bash
docker build -t pulse-backend .
docker run -p 8000:8000 --env-file .env pulse-backend
```

## Environment Variables

See `.env.example` for the full list. Key variables:

- `SUPABASE_DB_HOST` / `SUPABASE_DB_PASSWORD` — Database connection
- `SUPABASE_URL` / `SUPABASE_KEY` — Supabase API (storage, auth)
- `GEMINI_API_KEY` — Google AI Studio key
- `ENCRYPTION_KEY` — Fernet key for BYOK encryption
- `ALLOWED_ORIGINS` — CORS whitelist (your frontend URL)
