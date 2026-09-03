-- Module 1 — Onboarding Telegram
-- À exécuter une seule fois dans Supabase : SQL Editor > New query > Run.

create table if not exists public.athlete_profiles (
    athlete_id uuid primary key references public.athletes(id) on delete cascade,
    goal text,
    default_equipment text,
    injuries_history text,
    preferred_schedule text,
    language text not null default 'fr',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

comment on table public.athlete_profiles is
    'Profil utilisé pour personnaliser les séances du bot Telegram.';
