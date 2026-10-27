# NutriTrack 🥗

A responsive nutrition tracker built for Indian food, inspired by [Cronometer](https://cronometer.com/).
Snap a photo of your thali, scan a packet's barcode, or search 570+ built-in dishes. NutriTrack tracks
energy, macros, 10 vitamins, 7 minerals and water against goals based on ICMR-NIN guidelines.

**Stack:** Python · FastAPI · Neon Postgres (psycopg 3) · built-in Argon2 auth · Redis or in-memory cache ·
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
| **Auth** | Built-in: Argon2id password hashing, server-side sessions (hashed tokens, 30-day sliding expiry), login rate limiting, email verification and password reset over SMTP, change password, delete account |
| **Bring your own AI key** | Users can save their own Gemini API key. It is encrypted at rest (Fernet) and never shown again |
| **Insights** | Daily nutrition score (0-100), logging streak, week strip with per-day progress rings, and tips like "low on iron" that suggest nutrient-dense Indian foods matching your diet (veg, egg, non-veg, vegan, Jain) |
| **Profile** | Live BMI with Indian cut-offs (overweight from 23), BMR, daily burn and water need, plus change password, email confirmation and account deletion |
| **UI** | Mobile-first: sidebar on desktop, floating tab bar on phones. Display-serif headings, gradient hero with concentric calorie and macro rings, colour-coded meal cards, 26-week consistency calendar, KPI sparklines, Ctrl/⌘+K quick search. Light and dark mode, smooth animations that respect reduced motion |

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

### 1. Neon database
1. Create a project at [neon.tech](https://neon.tech). Singapore (`aws-ap-southeast-1`) is the closest region to India.
2. Copy the **pooled** connection string (host contains `-pooler`) into `DATABASE_URL`.
3. Apply the schema: `python -m app.migrate`. This creates the tables, the restricted `nutritrack_user`
   role and the row-level security policies. It is safe to run again; applied migrations are tracked in
   `schema_migrations`.

### 2. Configure
```bash
cp .env.example .env
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # -> APP_ENCRYPTION_KEY
```
Fill in `DATABASE_URL` and `APP_ENCRYPTION_KEY`. Everything else is optional:

| Variable | Purpose |
|---|---|
| `APP_ENCRYPTION_KEY` | Encrypts users' own Gemini keys. Required for "bring your own key" |
| `SMTP_*` | Email for verification and password reset (Gmail app password, Resend, Brevo, SES…) |
| `REQUIRE_EMAIL_VERIFICATION` | Block sign-in until the email is confirmed (default `false`) |
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
pytest                                 # spins up a throwaway local Postgres automatically
# or point tests at any empty database: TEST_DATABASE_URL=postgresql://... pytest
```
Or with Docker: `docker build -t nutritrack . && docker run -p 8000:8000 --env-file .env nutritrack`.
Run `python -m app.migrate` (or `docker run --env-file .env nutritrack python -m app.migrate`) after each deploy that adds a migration.

## Caching

| What | Where | Lifetime / invalidation |
|---|---|---|
| Diary days, range stats, goals, profile, custom foods | `u:<user>:v<N>:…` keys | Every write bumps the user's version `N`, which invalidates all of that user's cached data at once |
| Session lookups | `sess:<sha256(token)>` | 60 s; cleared on sign-out and password change |
| Built-in food search | `foods:search:<q>` | 1 h |
| Open Food Facts / USDA search & items | `ext:search:*`, `ext:food:*` | 1 day / 30 days |
| AI photo / text results | `ai:photo:<sha256(user, image, note)>` | 7 days, so re-analysing the same photo is free |
| Claude system prompt + food catalog | Anthropic prompt cache | `cache_control` on the stable prefix |
| Static assets | Browser | `Cache-Control: max-age=604800` with versioned URLs |

Cache outages never fail a request; the app falls back to the database.

## Security notes
- **Row-level security in Neon:** each request runs in a transaction that switches to the restricted
  `nutritrack_user` role and sets `app.user_id`. Postgres policies then only expose that user's rows,
  even if a query forgets a `user_id` filter. The auth tables are not granted to that role at all.
- Passwords are hashed with Argon2id. Session and reset tokens are 256-bit random values; only their
  SHA-256 is stored. Login and reset requests are rate limited, and responses don't reveal whether an
  email is registered.
- Sessions are `HttpOnly`, `SameSite=Lax` cookies. State-changing API calls also require an
  `X-Requested-With` header as CSRF protection. Password changes and resets revoke other sessions.
- Nutrients for database foods are computed server-side from trusted data, never taken from the client.
- Users' API keys are encrypted with a server-side key and never returned to the browser (only the last 4 characters).
- Meal photos are re-encoded server-side, stored as small JPEGs in Postgres, and only served to their owner.

## Project layout
```
app/
  main.py              FastAPI app, middleware, error handling
  auth.py              Accounts, sessions, verification/reset tokens, middleware
  db.py                Neon connection pool + per-user RLS transactions
  migrate.py           Migration runner (python -m app.migrate)
  emailer.py           SMTP email for verification and password reset
  repository.py        Data access (runs as the restricted per-user role)
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
migrations/            Schema, role and RLS policies
tests/                 pytest suite against a real (temporary) Postgres
```
