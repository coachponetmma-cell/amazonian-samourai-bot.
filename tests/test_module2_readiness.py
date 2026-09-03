from app.models.schemas import DailyCheckinInput, ReadinessStatus
from app.services.telegram_bot import _extract_rpe, _is_debrief, calculate_module2_readiness


def checkin(sleep: int, energy: int, stress: int, pain: int) -> DailyCheckinInput:
    return DailyCheckinInput(
        sleep_score=sleep, energy_score=energy, fatigue_score=3,
        stress_score=stress, soreness_score=pain,
    )


def test_module2_readiness_optimal():
    score, label, result = calculate_module2_readiness(checkin(5, 5, 1, 1))
    assert score == 5.0
    assert label == "FORME_OPTIMALE"
    assert result.status == ReadinessStatus.GREEN


def test_module2_readiness_moderate():
    score, label, result = calculate_module2_readiness(checkin(3, 3, 3, 3))
    assert score == 3.0
    assert label == "CHARGE_MODEREE"
    assert result.status == ReadinessStatus.ORANGE
    assert result.intensity_cap_rpe == 7


def test_module2_readiness_recovery():
    score, label, result = calculate_module2_readiness(checkin(1, 1, 5, 3))
    assert score == 1.3
    assert label == "RECUPERATION_ACTIVE"
    assert result.status == ReadinessStatus.RED


def test_debrief_detection_and_rpe_extraction():
    assert _is_debrief("Séance validée, RPE 8/10, bonne patate")
    assert _extract_rpe("Séance validée, RPE 8/10") == 8
    assert _extract_rpe("RPE 10") == 10
    assert _extract_rpe("Bonne séance") is None
