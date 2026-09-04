from typing import Optional, List
from pydantic import BaseModel, Field

class GeminiCheckinAnalysis(BaseModel):
    sleep_score: Optional[int] = Field(None, description="Score de sommeil de 1 à 10")
    energy_score: Optional[int] = Field(None, description="Niveau d energie de 1 à 10")
    fatigue_score: Optional[int] = Field(None, description="Niveau de fatigue de 1 à 10")
    stress_score: Optional[int] = Field(None, description="Niveau de stress de 1 à 10")
    soreness_score: Optional[int] = Field(None, description="Niveau de courbatures de 1 à 10")
    rpe: Optional[int] = Field(None, description="RPE si mentionne (1 à 10)")
    
    equipment_available: Optional[str] = Field(
        None, 
        description="Equipement specifique mentionne pour la seance du jour (ex: poids du corps, kettlebell 16kg, elastiques, gym complete). Laisser None si non precise."
    )
    
    missing_fields: List[str] = Field(default_factory=list)
    feedback_coach: str = Field(..., description="Message court et motivant du coach")


