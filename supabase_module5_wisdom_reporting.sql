-- =============================================================================
-- Module 5 — Sagesse IA, Segmentation (Loisir vs Élite) & Suivi Quotidien Unifié
-- À exécuter dans Supabase : SQL Editor > New query > Run (Rétrocompatible).
-- =============================================================================

-- 1. Extension de la table athlete_profiles
ALTER TABLE public.athlete_profiles ADD COLUMN IF NOT EXISTS segment TEXT DEFAULT 'loisir';
-- Valeurs possibles : 'loisir' (95% IA autonome), 'elite' (Cellule Élite supervisée)

ALTER TABLE public.athlete_profiles ADD COLUMN IF NOT EXISTS target_weight_kg NUMERIC;
ALTER TABLE public.athlete_profiles ADD COLUMN IF NOT EXISTS last_checkin_at TIMESTAMPTZ;

-- 2. Table pour l'historique quotidien unifié (Poids, Calories, RPE, Énergie)
CREATE TABLE IF NOT EXISTS public.daily_metrics (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    athlete_id UUID REFERENCES public.athletes(id) ON DELETE CASCADE,
    telegram_id BIGINT,
    log_date DATE NOT NULL DEFAULT CURRENT_DATE,
    weight_kg NUMERIC,
    calories_consumed INTEGER,
    calories_target INTEGER,
    proteins_consumed INTEGER,
    fats_consumed INTEGER,
    carbs_consumed INTEGER,
    rpe_real INTEGER,
    energy_score INTEGER,
    fatigue_score INTEGER,
    sleep_score INTEGER,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Index pour requêtes chronologiques rapides (7 et 14 jours)
CREATE INDEX IF NOT EXISTS daily_metrics_athlete_date_idx ON public.daily_metrics(athlete_id, log_date DESC);
CREATE INDEX IF NOT EXISTS daily_metrics_telegram_id_idx ON public.daily_metrics(telegram_id, log_date DESC);

COMMENT ON TABLE public.daily_metrics IS 'Historique journalier unifié pour suivi de tendance, sagesse IA et rapports graphiques.';
