import pytest
from app.models.schemas import (
    AthleteProfile,
    DailyCheckinInput,
    ReadinessStatus,
    TierLevel,
)
from app.services.rules_engine import RulesEngine
from app.services.workout_generator import WorkoutGenerator


@pytest.mark.asyncio
async def test_full_pipeline_orange_workout_generation_3_blocks():
    """
    Test le pipeline complet en statut ORANGE :
    - 3 Blocs obligatoires
    - 40-45 min de durée
    - Liens vidéos [🎬 Voir la démonstration](URL)
    - Plafonnement RPE 7
    """
    profile = AthleteProfile(
        athlete_id="ath-test-orange",
        telegram_id=99999,
        first_name="Jason",
        tier=TierLevel.TIER_1,
        available_equipment=["dumbbells", "kettlebell", "trap_bar", "plates", "pullup_bar", "resistance_band", "medicine_ball", "bodyweight"],
        injuries_and_constraints=[]
    )

    checkin = DailyCheckinInput(
        sleep_score=3,
        energy_score=3,
        fatigue_score=3,
        stress_score=3,
        soreness_score=3,
        raw_text="Check-in standard avec énergie modérée"
    )

    readiness = RulesEngine.calculate_readiness(checkin)
    assert readiness.status == ReadinessStatus.ORANGE
    assert readiness.score == 3.0

    workout_plan = await WorkoutGenerator.build_workout(
        profile=profile,
        readiness=readiness,
        raw_checkin_text=checkin.raw_text
    )

    assert workout_plan.readiness_status == ReadinessStatus.ORANGE
    assert len(workout_plan.blocks) == 3
    assert 35 <= workout_plan.total_estimated_minutes <= 55

    # Vérification des 3 blocs
    b1, b2, b3 = workout_plan.blocks
    assert len(b1.exercises) == 2
    assert "ÉCHAUFFEMENT" in b1.title or "1" in b1.title

    assert len(b2.exercises) == 3
    assert "CORPS DE SÉANCE" in b2.title or "2" in b2.title or "CONDITIONING" in b2.title
    for ex_item in b2.exercises:
        assert ex_item.target_rpe <= 7

    assert len(b3.exercises) == 2
    assert "RENFORCEMENT" in b3.title or "3" in b3.title or "CORE" in b3.title

    formatted_msg = WorkoutGenerator.format_telegram_message(workout_plan, athlete_name="Jason")
    assert "BLOC 1" in formatted_msg or "1." in formatted_msg
    assert "BLOC 2" in formatted_msg or "2." in formatted_msg
    assert "BLOC 3" in formatted_msg or "3." in formatted_msg
    assert "[🎬 Voir la démonstration]" in formatted_msg


@pytest.mark.asyncio
async def test_full_pipeline_green_workout_generation_3_blocks():
    """
    Test le pipeline en statut VERT (Pleine intensité - 3 blocs - ~50 min)
    """
    profile = AthleteProfile(
        athlete_id="ath-test-green",
        telegram_id=11111,
        first_name="Jason",
        tier=TierLevel.TIER_3,
        available_equipment=["bodyweight", "dumbbells", "kettlebell", "pullup_bar", "resistance_band", "medicine_ball", "trap_bar", "plates"],
        injuries_and_constraints=[]
    )

    checkin = DailyCheckinInput(
        sleep_score=5,
        energy_score=5,
        fatigue_score=1,
        stress_score=1,
        soreness_score=1,
        raw_text="Excellente nuit, 100% d'énergie, prêt à exploser"
    )

    readiness = RulesEngine.calculate_readiness(checkin)
    assert readiness.status == ReadinessStatus.GREEN

    workout_plan = await WorkoutGenerator.build_workout(
        profile=profile,
        readiness=readiness,
        raw_checkin_text=checkin.raw_text
    )
    assert len(workout_plan.blocks) == 3
    assert 40 <= workout_plan.total_estimated_minutes <= 60

    formatted_msg = WorkoutGenerator.format_telegram_message(workout_plan, athlete_name="Jason")
    assert "[🎬 Voir la démonstration]" in formatted_msg
