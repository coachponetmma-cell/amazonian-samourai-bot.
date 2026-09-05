"""
Test de validation du Cahier des Charges Amazonian Samourai MMA Coaching IA V2
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
    get_available_exercises,
    get_athlete_by_telegram_id,
    get_all_athletes,
    get_last_7_days_workout_logs,
    get_last_7_days_checkins,
    log_workout_generation,
    log_workout_completion,
    supabase
)
from app.services.gemini import (
    analyze_checkin_with_gemini,
    generate_daily_workout,
    generate_weekly_coach_summary,
    clean_telegram_html
)


def test_priority_1_equipment_isolation():
    print("\n--- TEST PRIORITÉ 1 : RESPECT DU MATÉRIEL & ENVIRONNEMENT ---")

    # 1. Chambre d'hôtel sans matériel
    hotel_exercises = get_available_exercises("Chambre d'hôtel sans matériel")
    print(f"Nombre d'exercices autorisés en chambre d'hôtel : {len(hotel_exercises)}")
    
    forbidden_terms = ["barre", "landmine", "traction", "haltère", "kettlebell", "banc", "box", "machine", "dips"]
    violations = []
    for ex in hotel_exercises:
        mat = str(ex.get("material") or ex.get("equipment") or "").lower()
        name = ex.get("name", "").lower()
        if any(f in mat for f in forbidden_terms):
            violations.append(f"{ex['name']} (Mat: {mat})")

    if violations:
        print(f"❌ ÉCHEC : Exercices interdits détectés : {violations}")
        assert False
    else:
        print("✅ SUCCÈS : 0 exercice avec matériel lourd en chambre d'hôtel.")

    # 2. Poids du corps
    bw_exercises = get_available_exercises("Poids du corps")
    assert len(bw_exercises) == len(hotel_exercises)
    print("✅ SUCCÈS : Cohérence parfaite Poids du corps vs Chambre d'hôtel.")

    # 3. 1 Kettlebell
    kb_exercises = get_available_exercises("1 Kettlebell 16kg")
    has_kb = any("kettlebell" in str(e.get("material", "")).lower() for e in kb_exercises)
    has_bw = any("aucun" in str(e.get("material", "")).lower() for e in kb_exercises)
    assert has_kb and has_bw
    print(f"✅ SUCCÈS : Mode hybride 1 KB validé ({len(kb_exercises)} exercices).")


def test_priority_2_html_structure():
    print("\n--- TEST PRIORITÉ 2 : STRUCTURE HTML VIP & LIENS VIDÉOS ---")
    
    # Test avec données simulées
    class FakeAnalysis:
        sleep_score = 8
        energy_score = 7
        fatigue_score = 3
        equipment_available = "Chambre d'hôtel sans matériel"
        rpe = 7
        feedback_coach = "Libertad Samouraï !"

    athlete_profile = {"first_name": "Jason", "goal": "MMA / Combat", "default_equipment": "Poids du corps"}
    exercises = get_available_exercises("Chambre d'hôtel sans matériel")[:6]

    print("Génération de la séance test avec Gemini...")
    workout_html = generate_daily_workout(FakeAnalysis(), exercises, athlete_profile)

    # Vérifications HTML strictes
    assert "<b>" in workout_html and "</b>" in workout_html, "Balises <b> manquantes"
    assert "###" not in workout_html, "Markdown '###' interdit trouvé dans le rendu"
    assert "**" not in workout_html, "Markdown '**' interdit trouvé dans le rendu"
    assert "BLOC 1" in workout_html, "BLOC 1 manquant"
    assert "BLOC 2" in workout_html, "BLOC 2 manquant"
    assert "BLOC 3" in workout_html, "BLOC 3 manquant"
    assert "🔗 <a href=" in workout_html or "<a href=" in workout_html, "Lien vidéo HTML manquant"
    assert "🎬 Voir la démonstration" in workout_html, "Texte du lien vidéo manquant"

    print("✅ SUCCÈS : Format Telegram HTML VIP validé avec 0 syntaxe Markdown !")
    print("Extrait du rendu HTML généré :")
    print("-" * 50)
    print(workout_html[:500] + "\n...\n")


def test_priority_3_hebdo_summary():
    print("\n--- TEST PRIORITÉ 3 : BILAN HEBDOMADAIRE COACH (/hebdo) ---")
    
    # Récupération des données réelles des 7 derniers jours dans Supabase
    logs = get_last_7_days_workout_logs(days=7)
    checkins = get_last_7_days_checkins(days=7)
    athlete_info = {"first_name": "Jason", "goal": "MMA / Combat", "default_equipment": "Poids du corps", "status": "vip"}

    print(f"Logs trouvés (7j) : {len(logs)} | Checkins trouvés (7j) : {len(checkins)}")
    print("Génération du rapport hebdomadaire par Gemini...")
    report_html = generate_weekly_coach_summary(logs=logs, athlete_info=athlete_info, checkins=checkins)

    # Vérifications
    assert "<b>" in report_html, "Balises HTML manquantes dans le rapport"
    assert "###" not in report_html, "Markdown présent dans le rapport"
    assert "ASSIDUITÉ" in report_html or "Assiduité" in report_html or "1." in report_html
    assert "TENDANCES" in report_html or "Tendances" in report_html or "2." in report_html
    assert "CHARGE" in report_html or "Charge" in report_html or "RPE" in report_html or "3." in report_html
    assert "POINTS DE VIGILANCE" in report_html or "Vigilance" in report_html or "4." in report_html
    assert "3 AXES" in report_html or "Axes" in report_html or "5." in report_html

    print("✅ SUCCÈS : Bilan Hebdomadaire Coach généré avec succès en 5 sections !")
    print("Extrait du rapport Coach :")
    print("-" * 50)
    print(report_html[:600] + "\n...\n")


if __name__ == "__main__":
    print("🥋 LANCEMENT DE LA VALIDATION DU CAHIER DES CHARGES...")
    test_priority_1_equipment_isolation()
    test_priority_2_html_structure()
    test_priority_3_hebdo_summary()
    print("🎉 TOUTES LES PRIORITÉS DU CAHIER DES CHARGES SONT 100% VALIDÉES !")
