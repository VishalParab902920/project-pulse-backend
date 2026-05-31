# Project Pulse — Backend

FastAPI-based Intelligence & Orchestration Server for Project Pulse.

## Setup

1. Create a virtual environment:
   ```bash
   python -m venv venv
   venv\Scripts\activate  # Windows
   # source venv/bin/activate  # macOS/Linux
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Configure environment:
   ```bash
   copy .env.example .env
   # Edit .env with your actual Supabase and Gemini credentials
   ```

4. Run the development server:
   ```bash
   uvicorn app.main:app --reload --port 8000
   ```

5. Open API docs at: http://localhost:8000/docs

## Project Structure

```
backend/
├── app/
│   ├── main.py          # FastAPI app entry point
│   ├── config.py        # Pydantic settings (env vars)
│   ├── database.py      # SQLAlchemy engine & session
│   ├── models/          # SQLAlchemy ORM models
│   ├── schemas/         # Pydantic request/response schemas
│   ├── routers/         # API route modules
│   ├── services/        # Business logic (AI pipeline, fallback, memory)
│   └── utils/           # Shared utilities
├── requirements.txt
├── .env.example
└── README.md
```
