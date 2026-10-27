# NutriTrack 🥗

A responsive nutrition tracker built for Indian food, inspired by [Cronometer](https://cronometer.com/).
Snap a photo of your thali, scan a packet's barcode, or search 570+ built-in dishes. NutriTrack tracks
energy, macros, 10 vitamins, 7 minerals and water against goals based on ICMR-NIN guidelines.

**Stack:** Python · FastAPI · Supabase (Postgres, Auth, Storage) · Redis or in-memory cache ·
Gemini or Claude vision · vanilla JS + Chart.js

## Features

| | |
|---|---|
| **Food logging** | Search (handles aliases like *chapati / phulka / rotli* and typos like *biriyani*), barcode scan, meal photo, free-text description ("2 aloo parathas and a cup of chai"), custom foods, recipe builder, copy yesterday |
| **Computer vision** | Every item on a plate is detected separately with a portion estimate in Indian units (katori, roti, glass). Recognised dishes are linked to the curated database. Users review and adjust before logging |
| **Nutrients** | Energy, water, protein, carbs, fiber, sugars, fat, saturated fat, cholesterol, vitamins A, B1, B2, B3, B6, folate, B12, C, D, E, calcium, iron, magnesium, phosphorus, zinc, potassium, sodium |
| **Water** | Quick add (glass, bottle, 1 L, custom). Total water includes moisture from drinks and food, as in Cronometer |
| **Goals** | Editable per-nutrient daily targets. "Suggest from my profile" uses Mifflin-St Jeor energy plus ICMR-NIN 2020 RDAs by sex, and a water target of 35 ml/kg |
| **Reports** | Day, week, month, year or custom range. Energy, macros, water and any-nutrient charts against goals, macro and meal split, top foods, and a full average-vs-goal table |
| **Auth** | Supabase email/password, email confirmation, password reset. httpOnly session cookies with transparent refresh |
| **Bring your own AI key** | Users can save their own Gemini API key. It is encrypted at rest (Fernet) and never shown again |
| **UI** | Mobile-first, sidebar on desktop and bottom tab bar on phones, light and dark mode, animated rings, bars, water glass and celebrations, respects reduced motion |

## How food coverage works

No single list can contain every food, so NutriTrack layers five sources:

1. **Curated Indian database** (`app/data/indian_foods*.csv`): 570+ foods across North, South, East,
   West and North-East Indian cuisines (Kashmiri, Punjabi, Rajasthani, Gujarati, Maharashtrian, Goan,
   Kerala, Tamil, Karnataka, Andhra/Telangana, Bengali, Odia, Assamese and NE, Bihari, Parsi, Sindhi,
   Hyderabadi), plus street food, sweets, drinks, raw ingredients, fruits and common international dishes.
   Values are per 100 g, with Indian household servings.
2. **Open Food Facts**: packaged foods by name or **barcode**, with Indian products preferred. No key needed.
3. **USDA FoodData Central**: generic ingredients and global branded foods with full micronutrients.
   Works with `DEMO_KEY`. Get a free key for production use.
4. **Recipe builder**: any home dish from its ingredients, with optional cooked weight.
5. **AI estimation** (photo or text) for anything else, with "Save to My foods" for next time.

> Nutrition values for home-cooked dishes are approximations compiled from IFCT 2017 / USDA references
> and standard recipes. Oil, ghee and portion sizes vary between kitchens. For medical nutrition therapy,
> follow your dietitian's numbers.

## Setup

### 1. Supabase
1. Create a project at [supabase.com](https://supabase.com).
2. Open **SQL Editor**, paste `supabase/migrations/0001_init.sql` and run it. This creates the tables,
   row-level security policies, the signup trigger and a private `meal-photos` storage bucket.
   Alternatively, run `supabase db push` with the Supabase CLI.
3. **Authentication → URL Configuration**: add `https://YOUR_DOMAIN/auth/callback`
   (and `http://localhost:8000/auth/callback`) to the redirect URLs. Email confirmation links and
   password reset links land there.
4. Copy the project URL and anon/publishable key from **Project Settings → API**.

### 2. Configure
```bash
cp .env.example .env
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # -> APP_ENCRYPTION_KEY
```
Fill in `SUPABASE_URL`, `SUPABASE_ANON_KEY` and `APP_ENCRYPTION_KEY`. All AI keys are optional:

| Variable | Purpose |
|---|---|
| `APP_ENCRYPTION_KEY` | Encrypts users' own Gemini keys. Required for "bring your own key" |
| `GEMINI_API_KEY` | Optional app-wide Gemini key for users who have not added their own |
| `GEMINI_MODEL` | Defaults to `gemini-2.5-flash` |
| `ANTHROPIC_API_KEY` | Optional app-wide Claude fallback (`claude-opus-5-5`, with server-side refusal fallback) |
| `USDA_API_KEY` | FoodData Central key (defaults to the rate-limited `DEMO_KEY`) |
| `REDIS_URL` | Shared cache across workers. Without it an in-process TTL cache is used |
| `COOKIE_SECURE` | Set `false` only for plain-http local development |

AI provider order per request: **the user's own Gemini key → server Gemini key → server Claude key**.
With none configured, photo and describe show a prompt to add a key, and everything else still works.

### 3. Run
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload          # http://localhost:8000
pytest                                 # 43 tests, no network or Supabase needed
```
Or with Docker: `docker build -t nutritrack . && docker run -p 8000:8000 --env-file .env nutritrack`.

## Caching

| What | Where | Lifetime / invalidation |
|---|---|---|
| Diary days, range stats, goals, profile, custom foods | `u:<user>:v<N>:…` keys | Every write bumps the user's version `N`, which invalidates all of that user's cached data at once |
| Auth token checks | `auth:<sha256(token)>` | 60 s, never past the token's expiry |
| Built-in food search | `foods:search:<q>` | 1 h |
| Open Food Facts / USDA search & items | `ext:search:*`, `ext:food:*` | 1 day / 30 days |
| AI photo / text results | `ai:photo:<sha256(user, image, note)>` | 7 days, so re-analysing the same photo is free |
| Claude system prompt + food catalog | Anthropic prompt cache | `cache_control` on the stable prefix |
| Static assets | Browser | `Cache-Control: max-age=604800` with versioned URLs |

Cache outages never fail a request; the app falls back to the database.

## Security notes
- Every query runs with the user's own Supabase JWT, so Postgres row-level security enforces
  ownership in addition to explicit `user_id` filters.
- Nutrients for database foods are computed server-side from trusted data, never taken from the client.
- Session tokens are in `HttpOnly`, `SameSite=Lax` cookies. State-changing API calls also require an
  `X-Requested-With` header as CSRF protection.
- Users' API keys are stored encrypted with a server-side key and are never returned to the browser
  (only the last 4 characters).
- Uploaded photos are re-encoded server-side and stored in a private bucket under the user's folder.

## Project layout
```
app/
  main.py              FastAPI app, middleware, error handling
  auth.py              Supabase Auth, cookie sessions, refresh middleware
  repository.py        Supabase data access (RLS-scoped per user)
  services.py          Cached reads + food resolution across sources
  cache.py             Redis / in-memory cache with per-user versioning
  ai.py                Gemini / Claude vision + text estimation
  external_foods.py    Open Food Facts + USDA FoodData Central
  food_db.py           Built-in food database + fuzzy search
  nutrients.py         Nutrient registry, ICMR-NIN goals, goal suggestions
  stats.py             Daily summaries and range reports
  routers/api.py       JSON API
  routers/pages.py     Pages and auth forms
  templates/, static/  Jinja templates, CSS, JS
supabase/migrations/   Schema, RLS policies, storage bucket
tests/                 pytest suite with an in-memory repository
```
