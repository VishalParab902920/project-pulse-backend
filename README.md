# Project Pulse — Backend

FastAPI-based Intelligence & Orchestration Server for Project Pulse.

## Tech Stack

- **Framework:** FastAPI + Uvicorn
- **AI:** Google Gemini 3.1 Flash Lite (via `google-genai` SDK)
- **Database:** Supabase PostgreSQL + pgvector (via SQLAlchemy)
- **Storage:** Supabase Storage (image uploads)
- **Encryption:** Fernet AES-256 (BYOK key storage)

## API Endpoints

### V2 Endpoints (Current)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET, HEAD | `/api/v2/health` | Service health check |

### V1 Endpoints (Legacy)

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

## Database Schema (V2.5)

### Nutrition Models

| Table | Description |
|-------|-------------|
| `foods` | Normalized food reference table (replaces `food_dictionary`) |
| `food_measures` | Measurement units for food items (e.g., "1 cup", "100g") |
| `nutrition_logs_v2` | User food log with pre-calculated nutrition values |

#### Foods Table

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID | Primary key |
| `name` | String(255) | Food name |
| `brand` | String(100) | Brand (optional) |
| `barcode` | String(100) | UPC/EAN barcode (optional, unique) |
| `base_unit` | String(10) | Base unit ("g" or "ml"), default "g" |
| `calories_per_100` | Numeric(10,2) | Calories per 100g/ml |
| `protein_per_100` | Numeric(10,2) | Protein per 100g/ml |
| `carbs_per_100` | Numeric(10,2) | Carbs per 100g/ml |
| `fat_per_100` | Numeric(10,2) | Fat per 100g/ml |
| `is_custom` | Boolean | User-created custom food |
| `created_by` | UUID | Profile reference |
| `created_at` | DateTime | Creation timestamp |

#### FoodMeasures Table

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID | Primary key |
| `food_id` | UUID | Foreign key to `foods` |
| `measure_name` | String(50) | e.g., "cup", "tbsp", "piece" |
| `conversion_factor` | Numeric(10,4) | Multiplier to base unit |
| `is_default` | Boolean | Default measurement for this food |

#### NutritionLogsV2 Table

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID | Primary key |
| `user_id` | UUID | Foreign key to `profiles` (NOT NULL) |
| `logged_at` | DateTime | Log timestamp |
| `meal_type` | String(50) | breakfast, lunch, dinner, snack |
| `food_id` | UUID | Foreign key to `foods` (NOT NULL) |
| `measure_id` | UUID | Foreign key to `food_measures` |
| `quantity` | Numeric(10,2) | Number of measures |
| `calculated_qty_base` | Numeric(10,2) | Pre-calculated base unit quantity |
| `calculated_calories` | Numeric(10,2) | Pre-calculated calories |
| `calculated_protein` | Numeric(10,2) | Pre-calculated protein |
| `calculated_carbs` | Numeric(10,2) | Pre-calculated carbs |
| `calculated_fat` | Numeric(10,2) | Pre-calculated fat |
| `created_at` | DateTime | Creation timestamp |

### Breaking Changes (V2 → V2.5)

- `food_dictionary` table renamed to `foods`
- `nutrition_logs` table renamed to `nutrition_logs_v2`
- Removed `recipes`, `recipe_ingredients`, `daily_nutrition_summaries` tables
- `food_id` and `user_id` in nutrition_logs now NOT NULL (previously nullable)
- Nutrition values now pre-calculated and denormalized at log time
