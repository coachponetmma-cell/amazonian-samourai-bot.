-- Module 2 — Check-ins, alertes douleur et débriefs.
-- À exécuter une seule fois dans Supabase : SQL Editor > New query > Run.

create table if not exists public.checkins (
    id uuid primary key default gen_random_uuid(),
    athlete_id uuid not null references public.athletes(id) on delete cascade,
    sleep_score integer not null check (sleep_score between 1 and 5),
    energy_score integer not null check (energy_score between 1 and 5),
    fatigue_score integer check (fatigue_score between 1 and 5),
    stress_score integer not null check (stress_score between 1 and 5),
    pain_score integer not null check (pain_score between 1 and 5),
    pain_location text,
    readiness_score numeric(3, 2) not null,
    readiness_status text not null check (readiness_status in ('FORME_OPTIMALE', 'CHARGE_MODEREE', 'RECUPERATION_ACTIVE')),
    raw_content text,
    source text not null check (source in ('text', 'voice')),
    created_at timestamptz not null default now()
);

create table if not exists public.alerts (
    id uuid primary key default gen_random_uuid(),
    athlete_id uuid not null references public.athletes(id) on delete cascade,
    alert_type text not null,
    pain_score integer check (pain_score between 1 and 5),
    pain_location text,
    details text,
    created_at timestamptz not null default now()
);

create table if not exists public.debriefs (
    id uuid primary key default gen_random_uuid(),
    athlete_id uuid not null references public.athletes(id) on delete cascade,
    rpe integer check (rpe between 1 and 10),
    feedback text not null,
    created_at timestamptz not null default now()
);

-- Compatibilité si une table avait été créée auparavant avec un schéma incomplet.
alter table public.checkins add column if not exists pain_location text;
alter table public.checkins add column if not exists fatigue_score integer;
alter table public.checkins add column if not exists raw_content text;
alter table public.checkins add column if not exists source text;
alter table public.alerts add column if not exists pain_location text;
alter table public.debriefs add column if not exists rpe integer;
alter table public.debriefs add column if not exists feedback text;

create index if not exists checkins_athlete_created_at_idx on public.checkins (athlete_id, created_at desc);
create index if not exists alerts_athlete_created_at_idx on public.alerts (athlete_id, created_at desc);
create index if not exists debriefs_athlete_created_at_idx on public.debriefs (athlete_id, created_at desc);
