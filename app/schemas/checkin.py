from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class ExtractedCheckinData(BaseModel):
    """Données minimales extraites d'un check-in pour préparer une séance."""

    energy_level: Optional[int] = Field(
        None,
        ge=1,
        le=10,
        description="Niveau d'énergie global de 1 à 10",
    )
    equipment: Optional[str] = Field(
        None,
        description="Lieu et matériel disponibles pour la séance du jour",
    )
    notes: Optional[str] = Field(
        None,
        description="Sommeil, fatigue, douleurs, stress et autres notes utiles",
    )


class GeminiCheckinAnalysis(BaseModel):
    """Analyse structurée d'un check-in, compatible avec l'ancien flux."""

    is_valid_checkin: bool = Field(
        False,
        description="Vrai seulement si énergie et lieu/matériel sont exploitables",
    )
    missing_info: Optional[Literal["energy", "location_equipment"]] = Field(
        None,
        description="Première information bloquante à demander à l'athlète",
    )
    extracted_data: ExtractedCheckinData = Field(default_factory=ExtractedCheckinData)

    # Champs historiques conservés pour log_checkin et generate_daily_workout.
    sleep_score: Optional[int] = Field(None, ge=1, le=10)
    energy_score: Optional[int] = Field(None, ge=1, le=10)
    fatigue_score: Optional[int] = Field(None, ge=1, le=10)
    stress_score: Optional[int] = Field(None, ge=1, le=10)
    soreness_score: Optional[int] = Field(None, ge=1, le=10)
    rpe: Optional[int] = Field(None, ge=1, le=10)
    equipment_available: Optional[str] = Field(None)
    missing_fields: List[str] = Field(default_factory=list)
    feedback_coach: str = Field(default="", description="Message court et motivant du coach")
