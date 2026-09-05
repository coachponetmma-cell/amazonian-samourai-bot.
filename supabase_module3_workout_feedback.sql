-- Module 3 : Extension du schéma Supabase pour le Feedback de séance et l'Onboarding
-- À exécuter dans Supabase : SQL Editor > New query > Run (optionnel, rétrocompatible).

-- 1. Colonnes dédiées aux feedbacks réels dans workout_logs
ALTER TABLE public.workout_logs ADD COLUMN IF NOT EXISTS telegram_id BIGINT;
ALTER TABLE public.workout_logs ADD COLUMN IF NOT EXISTS completed BOOLEAN DEFAULT TRUE;
ALTER TABLE public.workout_logs ADD COLUMN IF NOT EXISTS rpe_real INTEGER;

-- 2. Index pour requêtes rapides
CREATE INDEX IF NOT EXISTS workout_logs_telegram_id_idx ON public.workout_logs(telegram_id);
CREATE INDEX IF NOT EXISTS workout_logs_completed_at_idx ON public.workout_logs(completed_at DESC);
