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
| POST | `/api/v2/nutrition/recipe` | Create a new recipe |
| PUT | `/api/v2/nutrition/recipe/{recipe_id}` | Update an existing recipe (atomic recalculation) |
| GET | `/api/v2/nutrition/recipes` | List user recipes with associated food/macro data |
| GET | `/api/v2/nutrition/food/search` | Search food catalog by name |
| GET | `/api/v2/nutrition/barcode/{code}` | Lookup food by barcode |

#### PUT `/api/v2/nutrition/recipe/{recipe_id}`

Atomically updates an existing recipe. Replaces all ingredients, recalculates macro composition per 100g, and updates the associated custom food entry and serving measure.

**Path Parameters:**

| Param | Type | Description |
|-------|------|-------------|
| `recipe_id` | UUID | The recipe to update |

**Request Body** (same schema as `POST /recipe`):

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | Recipe name (1–255 chars) |
| `instructions` | string | No | Preparation instructions |
| `portions` | integer | No | Number of portions (≥1, default 1) |
| `ingredients` | array | Yes | At least one `{ food_id, measure_id, quantity }` |

**Behavior:**

1. Validates the recipe exists, belongs to the user, and its food entry is not archived.
2. Deletes all existing `recipe_ingredients` for this recipe.
3. Resolves each ingredient's food + measure to compute base weight.
4. Recalculates total weight and normalized macros per 100g.
5. Updates the parent `foods` row with the new macro profile and name.
6. Updates the "serving" measure conversion factor (`total_weight / portions`).
7. Writes new ingredient mappings into `recipe_ingredients`.

**Response fields:**

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Recipe UUID |
| `food_id` | string | Associated custom food entry UUID |
| `user_id` | string | Owner profile UUID |
| `name` | string | Updated recipe name |
| `instructions` | string | Updated instructions |
| `portions` | integer | Number of portions |
| `total_weight_g` | number | Total recipe weight in grams |
| `calories_per_100` | number | Normalized calories per 100g |
| `protein_per_100` | number | Normalized protein per 100g |
| `carbs_per_100` | number | Normalized carbs per 100g |
| `fat_per_100` | number | Normalized fat per 100g |
| `measures` | array | Updated measures `{ id, measure_name, conversion_factor, is_default }` |
| `ingredients` | array | Resolved ingredients `{ food_id, weight_g }` |

**Error Responses:**

| Status | Condition |
|--------|-----------|
| 404 | Recipe not found or not owned by user |
| 404 | Associated food entry not found or archived |
| 404 | Ingredient food or measure not found |
| 400 | Measure doesn't belong to the specified food |
| 400 | Total recipe weight is zero or negative |

---

#### GET `/api/v2/nutrition/recipes`

Returns all recipes belonging to the authenticated user with full ingredient food data. Each recipe is enriched with its associated custom food entry data (macros per 100g, available measures, and computed totals based on the default serving size). Ingredient objects now include the complete food record and its measures, enabling clients to display nutritional breakdowns per ingredient without additional API calls.

Archived food entries (`is_archived = true`) are excluded. Recipes whose associated food entry has been archived (soft-deleted) are omitted from the response entirely.

**Response fields (per recipe):**

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Recipe UUID |
| `title` | string | Recipe name |
| `instructions` | string | Preparation instructions |
| `ingredients` | array | List of ingredient objects (see below) |
| `created_at` | string | ISO 8601 timestamp |

**Ingredient object fields:**

| Field | Type | Description |
|-------|------|-------------|
| `food_id` | string | UUID of the ingredient food |
| `weight_g` | number | Weight in grams |
| `food_name` | string | Food name (null if food deleted) |
| `food` | object\|null | Full food record (null if food deleted) |

**Nested `food` object fields:**

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Food UUID |
| `name` | string | Food name |
| `brand` | string\|null | Brand name |
| `barcode` | string\|null | UPC/EAN barcode |
| `base_unit` | string | Base unit ("g" or "ml") |
| `calories_per_100` | number | Calories per 100g/ml |
| `protein_per_100` | number | Protein per 100g/ml |
| `carbs_per_100` | number | Carbs per 100g/ml |
| `fat_per_100` | number | Fat per 100g/ml |
| `is_custom` | boolean | Whether user-created |
| `is_verified` | boolean | Whether verified |
| `created_by` | string\|null | Creator profile UUID |
| `measures` | array | Available measures `{ id, food_id, measure_name, conversion_factor, is_default }` |

**Response fields (per recipe, continued):**

| Field | Type | Description |
|-------|------|-------------|
| `food_id` | string | Associated custom food entry UUID |
| `calories_per_100` | number | Calories per 100g (from food entry) |
| `protein_per_100` | number | Protein per 100g |
| `carbs_per_100` | number | Carbs per 100g |
| `fat_per_100` | number | Fat per 100g |
| `measures` | array | Available measures `{ id, measure_name, conversion_factor, is_default }` |
| `total_calories` | number | Computed total calories for default serving |
| `total_protein` | number | Computed total protein for default serving |
| `total_carbs` | number | Computed total carbs for default serving |
| `total_fat` | number | Computed total fat for default serving |
| `total_weight_g` | number | Total weight in grams for default serving |

Every recipe in the response is guaranteed to have a matching non-archived custom food entry (matched by title + user).

---

#### GET `/api/v2/nutrition/food/search`

Searches the unified `foods` table by name using case-insensitive partial matching. Returns up to 20 results ordered by verification status (verified first), then alphabetically. Archived foods are excluded.

**Query Parameters:**

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `q` | string | Yes | Search term (2–100 characters) |

**Response:** Array of `FoodResponse` objects:

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Food UUID |
| `name` | string | Food name |
| `brand` | string\|null | Brand name |
| `barcode` | string\|null | UPC/EAN barcode |
| `base_unit` | string | Base unit ("g" or "ml") |
| `calories_per_100` | number | Calories per 100g/ml |
| `protein_per_100` | number | Protein per 100g/ml |
| `carbs_per_100` | number | Carbs per 100g/ml |
| `fat_per_100` | number | Fat per 100g/ml |
| `is_custom` | boolean | Whether user-created |
| `is_verified` | boolean | Whether verified |
| `measures` | array | Available measures `{ id, food_id, measure_name, conversion_factor, is_default }` |

**Error Responses:**

| Status | Condition |
|--------|-----------|
| 422 | Query string `q` missing or outside 2–100 char range |

---

#### GET `/api/v2/nutrition/barcode/{code}`

Looks up a food entry by barcode from the `foods` table. Archived foods are excluded. Includes eager-loaded measures.

**Path Parameters:**

| Param | Type | Description |
|-------|------|-------------|
| `code` | string | UPC/EAN barcode value |

**Response:** Single `FoodResponse` object (same schema as food/search results above).

**Error Responses:**

| Status | Condition |
|--------|-----------|
| 404 | Barcode not found in database |

---

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

## Migrations

One-time migration scripts live in the project root. Run them with the virtual environment activated:

```bash
python migrate_add_is_archived.py   # Adds is_archived column + index to foods table
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
| `recipes` | User-created recipes with metadata |
| `recipe_ingredients` | Ingredient mappings for recipes (food_id + weight) |

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
| `is_archived` | Boolean | Soft-delete flag (default `false`) |
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
- Removed `daily_nutrition_summaries` table
- `recipes` and `recipe_ingredients` tables retained for recipe management
- `food_id` and `user_id` in nutrition_logs now NOT NULL (previously nullable)
- Nutrition values now pre-calculated and denormalized at log time
