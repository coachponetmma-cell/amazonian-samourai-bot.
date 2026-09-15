-- =============================================================================
-- Module 4 — Suivi Nutritionnel ("Poids de combat") & Niveaux de Service
-- À exécuter dans Supabase : SQL Editor > New query > Run (Rétrocompatible).
-- =============================================================================

-- 1. Extension de la table athlete_profiles
ALTER TABLE public.athlete_profiles ADD COLUMN IF NOT EXISTS tracking_type TEXT DEFAULT 'both';
-- Valeurs possibles: 'sport', 'nutrition', 'both'

ALTER TABLE public.athlete_profiles ADD COLUMN IF NOT EXISTS nutrition_mode TEXT;
-- Valeurs possibles: 'ocr_vision' (Mode A), 'app_tierce' (Mode B)

ALTER TABLE public.athlete_profiles ADD COLUMN IF NOT EXISTS service_tier TEXT DEFAULT '100%_ia';
-- Valeurs possibles: '100%_ia' (Niveau 1), 'hybride' (Niveau 2)

ALTER TABLE public.athlete_profiles ADD COLUMN IF NOT EXISTS weight_kg NUMERIC;
ALTER TABLE public.athlete_profiles ADD COLUMN IF NOT EXISTS activity_level TEXT;

-- Cibles de macronutriments calculées
ALTER TABLE public.athlete_profiles ADD COLUMN IF NOT EXISTS target_calories INTEGER;
ALTER TABLE public.athlete_profiles ADD COLUMN IF NOT EXISTS target_proteins INTEGER;
ALTER TABLE public.athlete_profiles ADD COLUMN IF NOT EXISTS target_fats INTEGER;
ALTER TABLE public.athlete_profiles ADD COLUMN IF NOT EXISTS target_carbs INTEGER;

-- 2. Table pour l'historique des analyses nutritionnelles (Assiettes OCR & Macros)
CREATE TABLE IF NOT EXISTS public.nutrition_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    athlete_id UUID REFERENCES public.athletes(id) ON DELETE CASCADE,
    telegram_id BIGINT,
    meal_type TEXT DEFAULT 'assiette_ocr', -- 'assiette_ocr', 'macros_app', 'texte'
    analysis_text TEXT NOT NULL,
    raw_user_input TEXT,
    photo_url TEXT,
    calories_est INTEGER,
    proteins_est INTEGER,
    fats_est INTEGER,
    carbs_est INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Index pour requêtes rapides
CREATE INDEX IF NOT EXISTS nutrition_logs_athlete_id_idx ON public.nutrition_logs(athlete_id, created_at DESC);
CREATE INDEX IF NOT EXISTS nutrition_logs_telegram_id_idx ON public.nutrition_logs(telegram_id, created_at DESC);

COMMENT ON TABLE public.nutrition_logs IS 'Historique des repas, analyses visuelles et captures MyFitnessPal.';
