import logging
from typing import List, Tuple, Dict, Any, Optional
from app.core.config import settings
from app.models.schemas import (
    DailyCheckinInput,
    ReadinessResult,
    ReadinessStatus,
    AthleteProfile,
    Exercise,
    WorkoutExercise,
    WorkoutAdjustment,
)

logger = logging.getLogger(__name__)


class RulesEngine:
    """
    Moteur de règles et de calcul de sécurité pour Coaching IA V2.
    Gère la Readiness R, les seuils d'intensité et le filtrage strict (HARD Rules).
    """

    @classmethod
    def calculate_readiness(cls, checkin: DailyCheckinInput) -> ReadinessResult:
        """
        Calcule le score de Readiness R (1.0 à 5.0) et détermine le statut (VERT, ORANGE, ROUGE).
        
        Formule :
        R = 0.25*Sommeil + 0.25*Énergie + 0.20*(6 - Fatigue) + 0.15*(6 - Stress) + 0.15*(6 - Douleur)
        """
        # Facteurs inversés pour les métriques négatives (1=Top, 5=Pire)
        inv_fatigue = 6.0 - float(checkin.fatigue_score)
        inv_stress = 6.0 - float(checkin.stress_score)
        inv_soreness = 6.0 - float(checkin.soreness_score)

        # Calcul pondéré
        w_sleep = settings.WEIGHT_SLEEP * float(checkin.sleep_score)
        w_energy = settings.WEIGHT_ENERGY * float(checkin.energy_score)
        w_fatigue = settings.WEIGHT_FATIGUE * inv_fatigue
        w_stress = settings.WEIGHT_STRESS * inv_stress
        w_soreness = settings.WEIGHT_SORENESS * inv_soreness

        raw_score = w_sleep + w_energy + w_fatigue + w_stress + w_soreness
        score = round(max(1.0, min(5.0, raw_score)), 2)

        alerts: List[str] = []
        details: Dict[str, float] = {
            "sleep_weighted": round(w_sleep, 2),
            "energy_weighted": round(w_energy, 2),
            "fatigue_weighted": round(w_fatigue, 2),
            "stress_weighted": round(w_stress, 2),
            "soreness_weighted": round(w_soreness, 2),
        }

        # Détection des alertes spécifiques
        if checkin.soreness_score >= settings.SORENESS_RED_THRESHOLD:
            locs = f" ({', '.join(checkin.soreness_locations)})" if checkin.soreness_locations else ""
            alerts.append(f"Douleur aiguë signalée : {checkin.soreness_score}/5{locs}.")

        if checkin.sleep_score <= 2:
            alerts.append(f"Sommeil dégradé : {checkin.sleep_score}/5.")

        if checkin.fatigue_score >= 4:
            alerts.append(f"Fatigue nerveuse élevée : {checkin.fatigue_score}/5.")

        # Détermination du statut de sécurité
        if checkin.soreness_score >= settings.SORENESS_RED_THRESHOLD or score < settings.THRESHOLD_ORANGE:
            status = ReadinessStatus.RED
            volume_multiplier = 0.0
            intensity_cap_rpe = 4
            recommendation = (
                "MODE ROUGE - Récupération active et mobilité uniquement. "
                "Séance d'intensité proscrite pour prévenir le surentraînement ou la blessure."
            )
        elif score < settings.THRESHOLD_GREEN:
            status = ReadinessStatus.ORANGE
            volume_multiplier = 0.80  # Réduction de 20% du volume
            intensity_cap_rpe = 7
            recommendation = (
                "MODE ORANGE - Régulation / Maintien. "
                "Volume réduit de 20% et intensité plafonnée à RPE 7. Priorité technique et propreté du geste."
            )
        else:
            status = ReadinessStatus.GREEN
            volume_multiplier = 1.00
            intensity_cap_rpe = None
            recommendation = (
                "MODE VERT - Feu vert complet. "
                "Séance à 100% du volume et intensité cible programmée. Warrior mode engagé."
            )

        return ReadinessResult(
            score=score,
            status=status,
            details=details,
            alerts=alerts,
            intensity_cap_rpe=intensity_cap_rpe,
            volume_multiplier=volume_multiplier,
            recommendation=recommendation
        )

    @classmethod
    def filter_exercises_for_athlete(
        cls,
        exercises: List[Exercise],
        profile: AthleteProfile,
        readiness_status: ReadinessStatus
    ) -> Tuple[List[Exercise], List[str]]:
        """
        Filtre strictement une liste d'exercices selon :
        1. Matériel possédé par l'athlète (HARD Rule 1)
        2. Blessures et contre-indications déclarées (HARD Rule 2)
        3. Statut Readiness R (Mode ROUGE -> Mobilité / Récupération uniquement)
        
        Retourne (exercices_valides, raisons_exclusions).
        """
        valid_exercises: List[Exercise] = []
        exclusion_reasons: List[str] = []

        available_equipment = {eq.strip().lower() for eq in profile.available_equipment}
        available_equipment.add("bodyweight")
        available_equipment.add("none")
        available_equipment.add("poids du corps")

        athlete_constraints = {c.strip().lower() for c in profile.injuries_and_constraints}

        for ex in exercises:
            # 1. Vérification du matériel requis
            ex_equipment = [eq.strip().lower() for eq in ex.required_equipment if eq.strip()]
            missing_equipment = [eq for eq in ex_equipment if eq not in available_equipment]
            if missing_equipment:
                msg = f"'{ex.name}' exclu : matériel manquant ({', '.join(missing_equipment)})"
                exclusion_reasons.append(msg)
                continue

            # 2. Vérification des contre-indications / blessures
            contraindications = [c.strip().lower() for c in ex.contraindicated_for if c.strip()]
            conflict = athlete_constraints.intersection(set(contraindications))
            if conflict:
                msg = f"'{ex.name}' exclu pour cause de blessure/contrainte ({', '.join(conflict)})"
                exclusion_reasons.append(msg)
                continue

            # 3. Filtrage selon le statut Readiness (RED = Mobilité / Récupération uniquement)
            if readiness_status == ReadinessStatus.RED:
                allowed_categories = {"mobility", "recovery", "stretching", "core"}
                if ex.category.strip().lower() not in allowed_categories:
                    msg = f"'{ex.name}' exclu : non autorisé en statut ROUGE (catégorie: {ex.category})"
                    exclusion_reasons.append(msg)
                    continue

            valid_exercises.append(ex)

        return valid_exercises, exclusion_reasons

    @classmethod
    def adjust_workout_for_readiness(
        cls,
        workout_exercises: List[WorkoutExercise],
        readiness_result: ReadinessResult
    ) -> Tuple[List[WorkoutExercise], WorkoutAdjustment]:
        """
        Ajuste les séries, répétitions et RPE d'une séance selon le résultat de Readiness.
        """
        adjusted_list: List[WorkoutExercise] = []
        safety_reasons: List[str] = []
        excluded_names: List[str] = []

        vol_mult = readiness_result.volume_multiplier
        cap_rpe = readiness_result.intensity_cap_rpe

        for we in workout_exercises:
            # En mode ROUGE, les exercices de force/explosivité sont convertis ou annulés
            if readiness_result.status == ReadinessStatus.RED:
                if we.exercise.category.strip().lower() not in {"mobility", "recovery", "stretching"}:
                    excluded_names.append(we.exercise.name)
                    safety_reasons.append(f"Exercice '{we.exercise.name}' annulé en raison du statut ROUGE.")
                    continue

            # Ajustement du volume (nombre de séries)
            adjusted_sets = we.sets
            if vol_mult < 1.0 and vol_mult > 0.0:
                adjusted_sets = max(1, round(we.sets * vol_mult))
                if adjusted_sets < we.sets:
                    safety_reasons.append(f"{we.exercise.name}: séries réduites de {we.sets} à {adjusted_sets} (-20% volume).")

            # Plafonnement du RPE
            adjusted_rpe = we.target_rpe
            if cap_rpe is not None and adjusted_rpe > cap_rpe:
                safety_reasons.append(f"{we.exercise.name}: RPE plafonné de {adjusted_rpe} à {cap_rpe}.")
                adjusted_rpe = cap_rpe

            adjusted_item = we.model_copy(update={
                "sets": adjusted_sets,
                "target_rpe": adjusted_rpe,
                "notes": f"{we.notes or ''} [Ajusté: {readiness_result.status.value}]".strip()
            })
            adjusted_list.append(adjusted_item)

        adjustment = WorkoutAdjustment(
            original_volume_multiplier=vol_mult,
            max_rpe_cap=cap_rpe,
            excluded_exercises=excluded_names,
            safety_reasons=safety_reasons
        )

        return adjusted_list, adjustment
