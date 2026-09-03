import logging
from fastapi import APIRouter, HTTPException
from app.models.schemas import DailyCheckinInput, ReadinessResult
from app.services.rules_engine import RulesEngine

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/checkin", response_model=ReadinessResult, summary="Calcul du score de Readiness et règles de sécurité")
async def evaluate_daily_checkin(checkin: DailyCheckinInput):
    """
    Évalue un check-in quotidien et renvoie :
    - Le score R (1.0 à 5.0)
    - Le statut (GREEN, ORANGE, RED)
    - Le multiplicateur de volume et le plafond RPE
    - Les alertes de douleur ou fatigue
    - La recommandation d'entraînement
    """
    try:
        result = RulesEngine.calculate_readiness(checkin)
        return result
    except Exception as e:
        logger.error(f"Erreur lors du calcul de Readiness : {e}")
        raise HTTPException(status_code=500, detail=str(e))
