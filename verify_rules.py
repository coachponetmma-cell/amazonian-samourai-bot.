"""
Script autonome de vérification du RULES Engine & du Workout Generator en 3 Blocs
"""
import sys
import os
import asyncio

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Ajout du chemin racine pour l'import
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from app.models.schemas import (
    DailyCheckinInput,
    ReadinessStatus,
    AthleteProfile,
    Exercise,
    WorkoutExercise,
    TierLevel,
)
from app.services.rules_engine import RulesEngine
from app.services.workout_generator import WorkoutGenerator


async def run_all_checks():
    print("==================================================================")
    print("🥋 VÉRIFICATION COACHING IA BASE V2 (3 BLOCS & LIENS VIDÉOS)")
    print("==================================================================")

    # 1. Readiness VERT
    print("\n[1/4] - Test Readiness VERT (Forme optimale)")
    cg = DailyCheckinInput(sleep_score=5, energy_score=5, fatigue_score=1, stress_score=1, soreness_score=1)
    rg = RulesEngine.calculate_readiness(cg)
    assert rg.score == 5.0 and rg.status == ReadinessStatus.GREEN
    print(f"   -> Score: {rg.score}/5.0 | Statut: {rg.status.value}")

    # 2. Readiness ORANGE
    print("\n[2/4] - Test Readiness ORANGE (Moyen - Volume Adapté)")
    co = DailyCheckinInput(sleep_score=3, energy_score=3, fatigue_score=3, stress_score=3, soreness_score=3)
    ro = RulesEngine.calculate_readiness(co)
    assert ro.score == 3.0 and ro.status == ReadinessStatus.ORANGE
    print(f"   -> Score: {ro.score}/5.0 | Statut: {ro.status.value} | Vol Multiplier: {ro.volume_multiplier} | RPE Cap: {ro.intensity_cap_rpe}")

    # 3. Pipeline Complet Séance ORANGE (3 Blocs - 45 min)
    print("\n[3/4] - Test Génération Séance ORANGE en 3 Blocs (45 min)")
    profile = AthleteProfile(
        athlete_id="ath-test-orange",
        telegram_id=99999,
        first_name="Jason",
        tier=TierLevel.TIER_1,
        available_equipment=["bodyweight", "dumbbells", "kettlebell", "trap_bar", "plates", "pullup_bar", "resistance_band", "medicine_ball"],
        injuries_and_constraints=[]
    )
    plan_orange = await WorkoutGenerator.build_workout(profile=profile, readiness=ro)
    assert len(plan_orange.blocks) == 3
    assert plan_orange.total_estimated_minutes == 45
    print(f"   -> Titre: {plan_orange.title}")
    print(f"   -> Nombre de Blocs: {len(plan_orange.blocks)}")
    for b in plan_orange.blocks:
        print(f"      • {b.title} : {len(b.exercises)} exercices ({b.target_rpe_range})")

    # 4. Formatage Message Telegram avec liens vidéos réels
    print("\n[4/4] - Test Formatage Telegram avec Liens Vidéos")
    formatted_msg = WorkoutGenerator.format_telegram_message(plan_orange, athlete_name="Jason")
    assert "[🎬 Voir la démonstration]" in formatted_msg
    print("   -> Extrait du message Telegram généré :\n")
    print(formatted_msg[:600] + "\n...\n")

    print("🔥 TOUTES LES VÉRIFICATIONS (3 BLOCS & LIENS VIDÉOS) SONT VALIDÉES ! 🔥")


if __name__ == "__main__":
    asyncio.run(run_all_checks())
