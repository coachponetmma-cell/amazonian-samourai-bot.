"""
Script de validation pour l'Onboarding /start et le Log de Feedback de fin de séance
"""
import sys
import os

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.supabase_service import (
    athlete_profile_exists,
    save_new_athlete_profile,
    get_athlete_by_telegram_id,
    log_workout_completion,
    get_last_7_days_workout_logs,
    supabase
)
from app.services.gemini import parse_debrief_with_gemini


def test_onboarding_verification_and_persistence():
    print("\n--- TEST 1 : ONBOARDING & ATHLETE_PROFILES ---")

    # 1. Vérification athlète existant
    existing_id = 8083308752
    exists = athlete_profile_exists(existing_id)
    print(f"Vérification athlète existant ({existing_id}) : {exists}")
    assert exists is True, "L'athlète Jason devrait être détecté comme existant"

    # 2. Vérification athlète inexistant
    fake_id = 777666555444
    not_exists = athlete_profile_exists(fake_id)
    print(f"Vérification athlète inconnu ({fake_id}) : {not_exists}")
    assert not_exists is False, "Un ID fictif ne devrait pas être détecté comme existant"

    # 3. Enregistrement d'un nouvel athlète (simulation de la fin de l'onboarding)
    test_id = 999888777111
    print(f"Simulation de l'onboarding pour le nouvel athlète ID: {test_id}...")
    new_profile = save_new_athlete_profile(
        telegram_id=test_id,
        athlete_name="Marc Dubois",
        goal="MMA Pro / Muay Thai",
        default_equipment="Poids du corps + 2 Kettlebells 16kg",
        injuries="Légère raideur genou gauche",
        username="marcdubois_mma"
    )
    assert new_profile is not None
    print(f"Profil sauvegardé : {new_profile['first_name']} {new_profile['last_name']} (ID: {new_profile['athlete_id']})")

    # Vérification que le profil est désormais bien détecté comme existant
    assert athlete_profile_exists(test_id) is True, "Le profil créé devrait être détecté comme existant"

    # Récupération via get_athlete_by_telegram_id
    ath_fetched = get_athlete_by_telegram_id(test_id)
    assert ath_fetched["first_name"] == "Marc"
    assert ath_fetched["goal"] == "MMA Pro / Muay Thai"
    print("✅ SUCCÈS : Enregistrement et lecture du profil en BDD 100% validés !")

    # Nettoyage
    try:
        supabase.table("athlete_profiles").delete().eq("athlete_id", new_profile["id"]).execute()
        supabase.table("athletes").delete().eq("id", new_profile["id"]).execute()
        print("Nettoyage du profil de test effectué.")
    except Exception as e:
        print(f"Info nettoyage : {e}")


def test_workout_feedback_logging():
    print("\n--- TEST 2 : LOG FEEDBACK / FINIR LA SÉANCE ---")

    test_tg_id = 8083308752
    test_ath = get_athlete_by_telegram_id(test_tg_id)
    ath_id = test_ath.get("id")

    print(f"Enregistrement d'un feedback de fin de séance pour l'athlète {ath_id}...")
    log_res = log_workout_completion(
        athlete_id=ath_id,
        telegram_id=test_tg_id,
        rpe_real=8,
        feedback_text="Séance terminée ! Super cardio sur les sprawls, RPE 8 ressenti.",
        completed=True
    )
    assert log_res is not None, "Le log de complétion n'a pas pu être enregistré"
    print(f"Log enregistré avec succès (ID: {log_res.get('id')})")

    # Vérification dans get_last_7_days_workout_logs
    recent_logs = get_last_7_days_workout_logs(athlete_id=ath_id, days=1)
    found = any(l.get("id") == log_res.get("id") for l in recent_logs)
    assert found is True, "Le log enregistré doit apparaître dans les 7 derniers jours"
    print("✅ SUCCÈS : Enregistrement du log de complétion dans workout_logs 100% validé !")

    # Nettoyage
    try:
        supabase.table("workout_logs").delete().eq("id", log_res["id"]).execute()
        print("Nettoyage du log de test effectué.")
    except Exception as e:
        print(f"Info nettoyage : {e}")


def test_gemini_debrief_parsing():
    print("\n--- TEST 3 : ANALYSE DU DÉBRIEFING PAR GEMINI ---")
    feedback_text = "RPE 9, séance très intense sur le finisseur, congestion folle sur les épaules."
    result = parse_debrief_with_gemini(feedback_text)
    print(f"Résultat analyse débriefing : RPE={result.get('rpe_real')} | Coach: {result.get('coach_reply')}")
    assert result.get("rpe_real") in [8, 9, 10], "Le RPE extrait devrait être proche de 9"
    assert len(result.get("coach_reply", "")) > 10, "Le retour coach doit être rédigé"
    print("✅ SUCCÈS : Analyse intelligente du débriefing validée !")


if __name__ == "__main__":
    print("🥋 LANCEMENT DE LA VALIDATION DU MODULE ONBOARDING & FEEDBACK...")
    test_onboarding_verification_and_persistence()
    test_workout_feedback_logging()
    test_gemini_debrief_parsing()
    print("\n🎉 TOUTES LES FONCTIONNALITÉS DU CAHIER DES CHARGES SONT 100% VALIDÉES !")
