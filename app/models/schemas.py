from enum import Enum
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, ConfigDict, Field, field_validator


class ReadinessStatus(str, Enum):
    GREEN = "GREEN"
    ORANGE = "ORANGE"
    RED = "RED"


class TierLevel(int, Enum):
    TIER_1 = 1  # 100% IA
    TIER_2 = 2  # Hybride (2 visios/mois)
    TIER_3 = 3  # Élite Pro (1 visio/semaine)


class DailyCheckinInput(BaseModel):
    """
    Entrée d'un Check-in quotidien (1 à 5).
    """
    athlete_id: Optional[str] = None
    sleep_score: int = Field(..., ge=1, le=5, description="1 (Très mauvais) à 5 (Excellent)")
    energy_score: int = Field(..., ge=1, le=5, description="1 (À plat) à 5 (Explosif)")
    fatigue_score: int = Field(..., ge=1, le=5, description="1 (Aucune) à 5 (Épuisement total)")
    stress_score: int = Field(..., ge=1, le=5, description="1 (Zen) à 5 (Extrême)")
    soreness_score: int = Field(..., ge=1, le=5, description="1 (Aucune) à 5 (Douleur aiguë)")
    soreness_locations: List[str] = Field(default_factory=list, description="Ex: ['knee_left', 'shoulder_right']")
    raw_audio_url: Optional[str] = None
    raw_text: Optional[str] = None


class ReadinessResult(BaseModel):
    """
    Résultat du calcul de Readiness (R) et décisions associées.
    """
    score: float = Field(..., description="Score R calculé (1.0 à 5.0)")
    status: ReadinessStatus = Field(..., description="VERT, ORANGE ou ROUGE")
    details: Dict[str, float] = Field(..., description="Détail pondéré de chaque métrique")
    alerts: List[str] = Field(default_factory=list, description="Alertes de sécurité détectées")
    intensity_cap_rpe: Optional[int] = Field(default=None, description="Plafond RPE maximal recommandé")
    volume_multiplier: float = Field(default=1.0, description="Multiplicateur de volume (ex: 0.8 pour -20%)")
    recommendation: str = Field(..., description="Consigne d'entraînement pour l'athlète")


class AthleteProfile(BaseModel):
    """
    Profil athlète, matériel possédé et restrictions.
    """
    athlete_id: str
    telegram_id: int
    first_name: Optional[str] = None
    tier: TierLevel = TierLevel.TIER_1
    height_cm: Optional[float] = None
    current_weight_kg: Optional[float] = None
    target_weight_kg: Optional[float] = None
    available_equipment: List[str] = Field(default_factory=list, description="['dumbbells', 'kettlebell_16kg', 'pullup_bar', 'bodyweight']")
    injuries_and_constraints: List[str] = Field(default_factory=list, description="['NO_JUMP', 'knee_pain_left', 'shoulder_impingement']")
    goal: Optional[str] = "Combat Preparation"


class Exercise(BaseModel):
    """
    Modèle d'exercice correspondant aux 49 exercices du fichier Coaching_IA_Base_V1.
    """
    id: Optional[str] = None
    code_id: Optional[str] = None
    name: str
    family: Optional[str] = "Force"
    subfamily: Optional[str] = None
    discipline: Optional[str] = "Musculation"
    level: Optional[str] = "Débutant"
    objective: Optional[str] = None
    material: Optional[str] = "Aucun"
    main_zone: Optional[str] = None
    secondary_zones: Optional[str] = None
    joint_impact: Optional[str] = "Faible"
    duration: Optional[str] = None
    repetitions: Optional[str] = None
    series: Optional[str] = None
    rest: Optional[str] = None
    instructions: Optional[str] = None
    common_mistakes: Optional[str] = None
    progression: Optional[str] = None
    regression: Optional[str] = None
    contraindications: Optional[str] = None
    tags: Optional[str] = None
    video_url: Optional[str] = None

    # Champs de compatibilité pour les appels historiques du moteur de règles.
    # Ils acceptent category / required_equipment / contraindicated_for sans
    # modifier le format stocké dans la table exercises.
    legacy_category: Optional[str] = Field(default=None, alias="category", exclude=True)
    legacy_required_equipment: Optional[List[str]] = Field(default=None, alias="required_equipment", exclude=True)
    legacy_contraindicated_for: Optional[List[str]] = Field(default=None, alias="contraindicated_for", exclude=True)

    model_config = ConfigDict(populate_by_name=True)

    # Compatibilité avec l'ancien schéma
    @property
    def category(self) -> str:
        return self.legacy_category or self.subfamily or self.family or "Musculation"

    @property
    def primary_muscles(self) -> List[str]:
        if self.main_zone:
            return [z.strip() for z in self.main_zone.split(",") if z.strip()]
        return []

    @property
    def required_equipment(self) -> List[str]:
        if self.legacy_required_equipment is not None:
            return self.legacy_required_equipment
        if self.material and self.material.lower() not in ["aucun", "none"]:
            return [m.strip() for m in self.material.replace("/", ",").split(",") if m.strip()]
        # Aucun matériel requis : ce n'est pas une exigence "bodyweight" ou "aucun".
        return []

    @property
    def contraindicated_for(self) -> List[str]:
        if self.legacy_contraindicated_for is not None:
            return self.legacy_contraindicated_for
        if self.contraindications and self.contraindications.lower() not in ["aucune", "none"]:
            return [c.strip() for c in self.contraindications.split(",") if c.strip()]
        return []

    @property
    def cues_and_instructions(self) -> Optional[str]:
        return self.instructions


class WorkoutExercise(BaseModel):
    exercise: Exercise
    sets: int
    reps_or_duration: str
    target_rpe: int = Field(..., ge=1, le=10)
    tempo: Optional[str] = "2-0-1-0"
    rest_seconds: int = 60
    notes: Optional[str] = None


class WorkoutBlock(BaseModel):
    block_number: int
    title: str
    focus: str
    target_rpe_range: str
    exercises: List[WorkoutExercise] = Field(default_factory=list)


class WorkoutAdjustment(BaseModel):
    original_volume_multiplier: float
    max_rpe_cap: Optional[int] = None
    excluded_exercises: List[str] = Field(default_factory=list)
    safety_reasons: List[str] = Field(default_factory=list)


class WorkoutPlan(BaseModel):
    workout_id: Optional[str] = None
    athlete_id: str
    readiness_status: ReadinessStatus
    readiness_score: float
    title: str
    focus: str
    blocks: List[WorkoutBlock] = Field(default_factory=list)
    exercises: List[WorkoutExercise] = Field(default_factory=list)
    total_estimated_minutes: int
    coach_notes: str
    adjustment: Optional[WorkoutAdjustment] = None
