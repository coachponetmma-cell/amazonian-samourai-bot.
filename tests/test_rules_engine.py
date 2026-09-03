import pytest
from app.models.schemas import (
    DailyCheckinInput,
    ReadinessStatus,
    AthleteProfile,
    Exercise,
    WorkoutExercise,
    TierLevel,
)
from app.services.rules_engine import RulesEngine


def test_readiness_calculation_green():
    """
    Test un check-in optimal : Sommeil=5, Énergie=5, Fatigue=1, Stress=1, Douleur=1
    R = 0.25*5 + 0.25*5 + 0.20*(6-1) + 0.15*(6-1) + 0.15*(6-1) = 1.25 + 1.25 + 1.0 + 0.75 + 0.75 = 5.0
    """
    checkin = DailyCheckinInput(
        sleep_score=5,
        energy_score=5,
        fatigue_score=1,
        stress_score=1,
        soreness_score=1,
    )
    result = RulesEngine.calculate_readiness(checkin)
    assert result.score == 5.0
    assert result.status == ReadinessStatus.GREEN
    assert result.volume_multiplier == 1.0
    assert result.intensity_cap_rpe is None
    assert len(result.alerts) == 0


def test_readiness_calculation_orange():
    """
    Test un check-in moyen : Sommeil=3, Énergie=3, Fatigue=3, Stress=3, Douleur=3
    R = 0.25*3 + 0.25*3 + 0.20*3 + 0.15*3 + 0.15*3 = 0.75 + 0.75 + 0.60 + 0.45 + 0.45 = 3.00
    """
    checkin = DailyCheckinInput(
        sleep_score=3,
        energy_score=3,
        fatigue_score=3,
        stress_score=3,
        soreness_score=3,
    )
    result = RulesEngine.calculate_readiness(checkin)
    assert result.score == 3.00
    assert result.status == ReadinessStatus.ORANGE
    assert result.volume_multiplier == 0.80
    assert result.intensity_cap_rpe == 7


def test_readiness_calculation_red_by_score():
    """
    Test un check-in très dégradé : Sommeil=1, Énergie=1, Fatigue=5, Stress=5, Douleur=3
    R = 0.25*1 + 0.25*1 + 0.20*(6-5) + 0.15*(6-5) + 0.15*(6-3) = 0.25 + 0.25 + 0.20 + 0.15 + 0.45 = 1.30
    """
    checkin = DailyCheckinInput(
        sleep_score=1,
        energy_score=1,
        fatigue_score=5,
        stress_score=5,
        soreness_score=3,
    )
    result = RulesEngine.calculate_readiness(checkin)
    assert result.score == 1.30
    assert result.status == ReadinessStatus.RED
    assert result.volume_multiplier == 0.0
    assert result.intensity_cap_rpe == 4


def test_readiness_calculation_red_by_acute_soreness_override():
    """
    Test la règle de sécurité : Douleur >= 4 force le statut ROUGE même si le score R reste élevé.
    Sommeil=5, Énergie=5, Fatigue=1, Stress=1, Douleur=4
    R = 0.25*5 + 0.25*5 + 0.20*5 + 0.15*5 + 0.15*2 = 1.25 + 1.25 + 1.0 + 0.75 + 0.30 = 4.55
    """
    checkin = DailyCheckinInput(
        sleep_score=5,
        energy_score=5,
        fatigue_score=1,
        stress_score=1,
        soreness_score=4,
        soreness_locations=["knee_right"]
    )
    result = RulesEngine.calculate_readiness(checkin)
    assert result.score == 4.55
    assert result.status == ReadinessStatus.RED  # Doit être rouge à cause de la douleur >= 4
    assert any("Douleur aiguë" in alert for alert in result.alerts)


def test_hard_rules_equipment_and_injury_filtering():
    """
    Test le filtrage strict (HARD Rules) :
    - Équipement manquant
    - Contre-indications (ex: NO_JUMP)
    """
    profile = AthleteProfile(
        athlete_id="ath-001",
        telegram_id=12345678,
        first_name="Jason",
        tier=TierLevel.TIER_3,
        available_equipment=["dumbbells", "bodyweight"],
        injuries_and_constraints=["NO_JUMP", "knee_pain"]
    )

    exercises = [
        Exercise(
            name="Trap Bar Deadlift",
            category="Strength",
            required_equipment=["trap_bar", "plates"],
            contraindicated_for=[]
        ),
        Exercise(
            name="Box Jump Explosif",
            category="Explosivity",
            required_equipment=["bodyweight"],
            contraindicated_for=["NO_JUMP", "knee_pain"]
        ),
        Exercise(
            name="Pompes DB Pushup",
            category="Hypertrophy",
            required_equipment=["dumbbells"],
            contraindicated_for=[]
        ),
    ]

    valid, exclusions = RulesEngine.filter_exercises_for_athlete(
        exercises=exercises,
        profile=profile,
        readiness_status=ReadinessStatus.GREEN
    )

    # Trap Bar Deadlift doit être exclu (manque trap_bar)
    # Box Jump doit être exclu (conflit NO_JUMP / knee_pain)
    # Seul Pompes DB Pushup doit être valide
    assert len(valid) == 1
    assert valid[0].name == "Pompes DB Pushup"
    assert len(exclusions) == 2


def test_workout_adjustment_in_orange_mode():
    """
    Test l'adaptation d'une séance en mode ORANGE (Volume -20%, Plafonnement RPE à 7).
    """
    checkin = DailyCheckinInput(
        sleep_score=3,
        energy_score=3,
        fatigue_score=3,
        stress_score=3,
        soreness_score=3,
    )
    readiness = RulesEngine.calculate_readiness(checkin)
    assert readiness.status == ReadinessStatus.ORANGE

    sample_exercise = Exercise(
        name="Med Ball Slam",
        category="Strikers Conditioning",
        required_equipment=["medicine_ball"]
    )

    original_workout = [
        WorkoutExercise(
            exercise=sample_exercise,
            sets=5,
            reps_or_duration="6 reps",
            target_rpe=9,
            notes="Intensité max"
        )
    ]

    adjusted, adjustment = RulesEngine.adjust_workout_for_readiness(original_workout, readiness)
    assert len(adjusted) == 1
    # 5 séries * 0.8 = 4 séries
    assert adjusted[0].sets == 4
    # RPE 9 plafonné à 7
    assert adjusted[0].target_rpe == 7
    assert len(adjustment.safety_reasons) >= 2
