-- NutriTrack schema for Neon / PostgreSQL 15+.
-- Applied by `python -m app.migrate` (tracked in schema_migrations).
--
-- Security model: the app connects as the database owner. Every request for a
-- signed-in user runs inside a transaction that does
--   SET LOCAL ROLE nutritrack_user; SET LOCAL app.user_id = '<uuid>';
-- nutritrack_user can only touch user-data tables, and row level security
-- limits it to rows whose user_id matches app.user_id. Auth tables (users,
-- sessions, auth_tokens) are not granted to nutritrack_user at all.

do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'nutritrack_user') then
        create role nutritrack_user nologin;
    end if;
end
$$;
grant nutritrack_user to current_user;

create or replace function app_user_id() returns uuid
language sql stable
as $$ select nullif(current_setting('app.user_id', true), '')::uuid $$;

-- ---------------------------------------------------------------------------
-- Accounts (owner-only)
-- ---------------------------------------------------------------------------
create table users (
    id uuid primary key default gen_random_uuid(),
    email text not null unique check (email = lower(email) and char_length(email) <= 254),
    password_hash text not null,
    email_verified_at timestamptz,
    last_login_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table sessions (
    token_hash bytea primary key,            -- sha256 of the cookie value; raw tokens are never stored
    user_id uuid not null references users (id) on delete cascade,
    created_at timestamptz not null default now(),
    last_seen_at timestamptz not null default now(),
    expires_at timestamptz not null,
    user_agent text,
    ip text
);
create index sessions_user_idx on sessions (user_id);
create index sessions_expires_idx on sessions (expires_at);

create table auth_tokens (
    token_hash bytea primary key,
    user_id uuid not null references users (id) on delete cascade,
    purpose text not null check (purpose in ('verify_email', 'reset_password')),
    expires_at timestamptz not null,
    used_at timestamptz,
    created_at timestamptz not null default now()
);
create index auth_tokens_user_idx on auth_tokens (user_id);

-- ---------------------------------------------------------------------------
-- User data
-- ---------------------------------------------------------------------------
create table profiles (
    user_id uuid primary key references users (id) on delete cascade,
    display_name text check (char_length(display_name) <= 80),
    sex text check (sex in ('male', 'female', 'other')),
    date_of_birth date,
    height_cm double precision check (height_cm between 50 and 260),
    weight_kg double precision check (weight_kg between 20 and 350),
    activity_level text not null default 'sedentary'
        check (activity_level in ('sedentary', 'light', 'moderate', 'active', 'very_active')),
    weight_goal text not null default 'maintain' check (weight_goal in ('lose', 'maintain', 'gain')),
    diet_type text not null default 'veg' check (diet_type in ('veg', 'egg', 'non-veg', 'vegan', 'jain')),
    timezone text not null default 'Asia/Kolkata',
    updated_at timestamptz not null default now()
);

create table nutrient_goals (
    user_id uuid not null references users (id) on delete cascade,
    nutrient text not null,
    target double precision not null check (target >= 0),
    updated_at timestamptz not null default now(),
    primary key (user_id, nutrient)
);

create table custom_foods (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references users (id) on delete cascade,
    name text not null check (char_length(name) between 1 and 120),
    category text not null default 'My foods',
    per_100g jsonb not null default '{}'::jsonb,
    servings jsonb not null default '[]'::jsonb,
    created_at timestamptz not null default now()
);
create index custom_foods_user_idx on custom_foods (user_id);

create table meal_photos (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references users (id) on delete cascade,
    content bytea not null check (octet_length(content) <= 2000000),
    mime_type text not null default 'image/jpeg',
    created_at timestamptz not null default now()
);
create index meal_photos_user_idx on meal_photos (user_id);

create table food_logs (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references users (id) on delete cascade,
    logged_on date not null,
    meal text not null check (meal in ('breakfast', 'lunch', 'dinner', 'snacks')),
    food_name text not null check (char_length(food_name) between 1 and 160),
    food_ref text,
    source text not null default 'manual' check (source in ('manual', 'photo', 'text_ai', 'custom')),
    quantity double precision not null check (quantity > 0),
    unit text not null,
    grams double precision not null check (grams >= 0),
    nutrients jsonb not null default '{}'::jsonb,
    photo_id uuid references meal_photos (id) on delete set null,
    created_at timestamptz not null default now()
);
create index food_logs_user_day_idx on food_logs (user_id, logged_on);

create table user_api_keys (
    user_id uuid not null references users (id) on delete cascade,
    provider text not null check (provider in ('gemini')),
    encrypted_key text not null,
    key_hint text not null,
    updated_at timestamptz not null default now(),
    primary key (user_id, provider)
);

-- ---------------------------------------------------------------------------
-- Grants + row level security for the per-request role
-- ---------------------------------------------------------------------------
grant usage on schema public to nutritrack_user;
grant execute on function app_user_id() to nutritrack_user;
grant select, insert, update, delete on profiles, nutrient_goals, custom_foods, meal_photos, food_logs, user_api_keys
    to nutritrack_user;

do $$
declare t text;
begin
    foreach t in array array['profiles', 'nutrient_goals', 'custom_foods', 'meal_photos', 'food_logs', 'user_api_keys']
    loop
        execute format('alter table %I enable row level security', t);
        execute format(
            'create policy own_rows on %I for all to nutritrack_user using (user_id = app_user_id()) with check (user_id = app_user_id())',
            t
        );
    end loop;
end
$$;
