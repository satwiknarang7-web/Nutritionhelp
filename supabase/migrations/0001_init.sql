-- NutriTrack schema. Run in the Supabase SQL editor or with `supabase db push`.
-- Every table is protected by row level security: users can only touch their own rows.

-- ---------------------------------------------------------------------------
-- Profiles (1:1 with auth.users)
-- ---------------------------------------------------------------------------
create table if not exists public.profiles (
    id uuid primary key references auth.users (id) on delete cascade,
    display_name text,
    sex text check (sex in ('male', 'female', 'other')),
    date_of_birth date,
    height_cm numeric(5, 1) check (height_cm between 50 and 260),
    weight_kg numeric(5, 1) check (weight_kg between 20 and 350),
    activity_level text not null default 'sedentary'
        check (activity_level in ('sedentary', 'light', 'moderate', 'active', 'very_active')),
    weight_goal text not null default 'maintain' check (weight_goal in ('lose', 'maintain', 'gain')),
    diet_type text not null default 'veg' check (diet_type in ('veg', 'egg', 'non-veg', 'vegan', 'jain')),
    timezone text not null default 'Asia/Kolkata',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

-- Create a profile row automatically when someone signs up.
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer set search_path = ''
as $$
begin
    insert into public.profiles (id, display_name)
    values (new.id, coalesce(new.raw_user_meta_data ->> 'display_name', split_part(new.email, '@', 1)))
    on conflict (id) do nothing;
    return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
    after insert on auth.users
    for each row execute function public.handle_new_user();

-- ---------------------------------------------------------------------------
-- Daily nutrient goals (one row per user per nutrient)
-- ---------------------------------------------------------------------------
create table if not exists public.nutrient_goals (
    user_id uuid not null references auth.users (id) on delete cascade,
    nutrient text not null,
    target numeric(10, 2) not null check (target >= 0),
    updated_at timestamptz not null default now(),
    primary key (user_id, nutrient)
);

-- ---------------------------------------------------------------------------
-- User-created foods (homemade recipes, packaged foods, saved AI estimates)
-- ---------------------------------------------------------------------------
create table if not exists public.custom_foods (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users (id) on delete cascade,
    name text not null check (char_length(name) between 1 and 120),
    category text not null default 'My foods',
    per_100g jsonb not null default '{}'::jsonb,
    servings jsonb not null default '[]'::jsonb,
    created_at timestamptz not null default now()
);
create index if not exists custom_foods_user_idx on public.custom_foods (user_id);

-- ---------------------------------------------------------------------------
-- Food diary
-- ---------------------------------------------------------------------------
create table if not exists public.food_logs (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users (id) on delete cascade,
    logged_on date not null,
    meal text not null check (meal in ('breakfast', 'lunch', 'dinner', 'snacks')),
    food_name text not null check (char_length(food_name) between 1 and 160),
    food_ref text,                      -- built-in food id, custom food uuid, or null
    source text not null default 'manual' check (source in ('manual', 'photo', 'text_ai', 'custom')),
    quantity numeric(10, 2) not null check (quantity > 0),
    unit text not null,
    grams numeric(10, 2) not null check (grams >= 0),
    nutrients jsonb not null default '{}'::jsonb,   -- totals for this entry
    photo_path text,
    created_at timestamptz not null default now()
);
create index if not exists food_logs_user_day_idx on public.food_logs (user_id, logged_on);

-- ---------------------------------------------------------------------------
-- Users' own AI provider keys (encrypted by the app server before storage)
-- ---------------------------------------------------------------------------
create table if not exists public.user_api_keys (
    user_id uuid not null references auth.users (id) on delete cascade,
    provider text not null check (provider in ('gemini')),
    encrypted_key text not null,
    key_hint text not null,             -- last 4 characters, for display only
    updated_at timestamptz not null default now(),
    primary key (user_id, provider)
);

-- ---------------------------------------------------------------------------
-- Row level security
-- ---------------------------------------------------------------------------
alter table public.profiles enable row level security;
alter table public.nutrient_goals enable row level security;
alter table public.custom_foods enable row level security;
alter table public.food_logs enable row level security;
alter table public.user_api_keys enable row level security;

drop policy if exists "own profile" on public.profiles;
create policy "own profile" on public.profiles
    for all to authenticated
    using ((select auth.uid()) = id) with check ((select auth.uid()) = id);

drop policy if exists "own goals" on public.nutrient_goals;
create policy "own goals" on public.nutrient_goals
    for all to authenticated
    using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);

drop policy if exists "own custom foods" on public.custom_foods;
create policy "own custom foods" on public.custom_foods
    for all to authenticated
    using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);

drop policy if exists "own logs" on public.food_logs;
create policy "own logs" on public.food_logs
    for all to authenticated
    using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);

drop policy if exists "own api keys" on public.user_api_keys;
create policy "own api keys" on public.user_api_keys
    for all to authenticated
    using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);

-- ---------------------------------------------------------------------------
-- Private storage bucket for meal photos: <user_id>/<file>
-- ---------------------------------------------------------------------------
insert into storage.buckets (id, name, public)
values ('meal-photos', 'meal-photos', false)
on conflict (id) do nothing;

drop policy if exists "own meal photos read" on storage.objects;
create policy "own meal photos read" on storage.objects
    for select to authenticated
    using (bucket_id = 'meal-photos' and (storage.foldername(name))[1] = (select auth.uid())::text);

drop policy if exists "own meal photos write" on storage.objects;
create policy "own meal photos write" on storage.objects
    for insert to authenticated
    with check (bucket_id = 'meal-photos' and (storage.foldername(name))[1] = (select auth.uid())::text);

drop policy if exists "own meal photos delete" on storage.objects;
create policy "own meal photos delete" on storage.objects
    for delete to authenticated
    using (bucket_id = 'meal-photos' and (storage.foldername(name))[1] = (select auth.uid())::text);
