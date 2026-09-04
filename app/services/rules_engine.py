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
    Moteur de rÃ¨gles et de calcul de sÃ©curitÃ© pour Coaching IA V2.
    GÃ¨re la Readiness R, les seuils d'intensitÃ© et le filtrage strict (HARD Rules).
    """

    @classmethod
    def calculate_readiness(cls, checkin: DailyCheckinInput) -> ReadinessResult:
        """
        Calcule le score de Readiness R (1.0 Ã  5.0) et dÃ©termine le statut (VERT, ORANGE, ROUGE).
        
        Formule :
        R = 0.25*Sommeil + 0.25*Ã‰nergie + 0.20*(6 - Fatigue) + 0.15*(6 - Stress) + 0.15*(6 - Douleur)
        """
        # Facteurs inversÃ©s pour les mÃ©triques nÃ©gatives (1=Top, 5=Pire)
        inv_fatigue = 6.0 - float(checkin.fatigue_score)
        inv_stress = 6.0 - float(checkin.stress_score)
        inv_soreness = 6.0 - float(checkin.soreness_score)

        # Calcul pondÃ©rÃ©
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

        # DÃ©tection des alertes spÃ©cifiques
        if checkin.soreness_score >= settings.SORENESS_RED_THRESHOLD:
            locs = f" ({', '.join(checkin.soreness_locations)})" if checkin.soreness_locations else ""
            alerts.append(f"Douleur aiguÃ« signalÃ©e : {checkin.soreness_score}/5{locs}.")

        if checkin.sleep_score <= 2:
            alerts.append(f"Sommeil dÃ©gradÃ© : {checkin.sleep_score}/5.")

        if checkin.fatigue_score >= 4:
            alerts.append(f"Fatigue nerveuse Ã©levÃ©e : {checkin.fatigue_score}/5.")

        # DÃ©termination du statut de sÃ©curitÃ©
        if checkin.soreness_score >= settings.SORENESS_RED_THRESHOLD or score < settings.THRESHOLD_ORANGE:
            status = ReadinessStatus.RED
            volume_multiplier = 0.0
            intensity_cap_rpe = 4
            recommendation = (
                "MODE ROUGE - RÃ©cupÃ©ration active et mobilitÃ© uniquement. "
                "SÃ©ance d'intensitÃ© proscrite pour prÃ©venir le surentraÃ®nement ou la blessure."
            )
        elif score < settings.THRESHOLD_GREEN:
            status = ReadinessStatus.ORANGE
            volume_multiplier = 0.80  # RÃ©duction de 20% du volume
            intensity_cap_rpe = 7
            recommendation = (
                "MODE ORANGE - RÃ©gulation / Maintien. "
                "Volume rÃ©duit de 20% et intensitÃ© plafonnÃ©e Ã  RPE 7. PrioritÃ© technique et propretÃ© du geste."
            )
        else:
            status = ReadinessStatus.GREEN
            volume_multiplier = 1.00
            intensity_cap_rpe = None
            recommendation = (
                "MODE VERT - Feu vert complet. "
                "SÃ©ance Ã  100% du volume et intensitÃ© cible programmÃ©e. Warrior mode engagÃ©."
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
        1. MatÃ©riel possÃ©dÃ© par l'athlÃ¨te (HARD Rule 1)
        2. Blessures et contre-indications dÃ©clarÃ©es (HARD Rule 2)
        3. Statut Readiness R (Mode ROUGE -> MobilitÃ© / RÃ©cupÃ©ration uniquement)
        
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
            # 1. VÃ©rification du matÃ©riel requis
            ex_equipment = [eq.strip().lower() for eq in ex.required_equipment if eq.strip()]
            missing_equipment = [eq for eq in ex_equipment if eq not in available_equipment]
            if missing_equipment:
                msg = f"'{ex.name}' exclu : matÃ©riel manquant ({', '.join(missing_equipment)})"
                exclusion_reasons.append(msg)
                continue

            # 2. VÃ©rification des contre-indications / blessures
            contraindications = [c.strip().lower() for c in ex.contraindicated_for if c.strip()]
            conflict = athlete_constraints.intersection(set(contraindications))
            if conflict:
                msg = f"'{ex.name}' exclu pour cause de blessure/contrainte ({', '.join(conflict)})"
                exclusion_reasons.append(msg)
                continue

            # 3. Filtrage selon le statut Readiness (RED = MobilitÃ© / RÃ©cupÃ©ration uniquement)
            if readiness_status == ReadinessStatus.RED:
                allowed_categories = {"mobility", "recovery", "stretching", "core"}
                if ex.category.strip().lower() not in allowed_categories:
                    msg = f"'{ex.name}' exclu : non autorisÃ© en statut ROUGE (catÃ©gorie: {ex.category})"
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
        Ajuste les sÃ©ries, rÃ©pÃ©titions et RPE d'une sÃ©ance selon le rÃ©sultat de Readiness.
        """
        adjusted_list: List[WorkoutExercise] = []
        safety_reasons: List[str] = []
        excluded_names: List[str] = []

        vol_mult = readiness_result.volume_multiplier
        cap_rpe = readiness_result.intensity_cap_rpe

        for we in workout_exercises:
            # En mode ROUGE, les exercices de force/explosivitÃ© sont convertis ou annulÃ©s
            if readiness_result.status == ReadinessStatus.RED:
                if we.exercise.category.strip().lower() not in {"mobility", "recovery", "stretching"}:
                    excluded_names.append(we.exercise.name)
                    safety_reasons.append(f"Exercice '{we.exercise.name}' annulÃ© en raison du statut ROUGE.")
                    continue

            # Ajustement du volume (nombre de sÃ©ries)
            adjusted_sets = we.sets
            if vol_mult < 1.0 and vol_mult > 0.0:
                adjusted_sets = max(1, round(we.sets * vol_mult))
                if adjusted_sets < we.sets:
                    safety_reasons.append(f"{we.exercise.name}: sÃ©ries rÃ©duites de {we.sets} Ã  {adjusted_sets} (-20% volume).")

            # Plafonnement du RPE
            adjusted_rpe = we.target_rpe
            if cap_rpe is not None and adjusted_rpe > cap_rpe:
                safety_reasons.append(f"{we.exercise.name}: RPE plafonnÃ© de {adjusted_rpe} Ã  {cap_rpe}.")
                adjusted_rpe = cap_rpe

            adjusted_item = we.model_copy(update={
                "sets": adjusted_sets,
                "target_rpe": adjusted_rpe,
                "notes": f"{we.notes or ''} [AjustÃ©: {readiness_result.status.value}]".strip()
            })
            adjusted_list.append(adjusted_item)

        adjustment = WorkoutAdjustment(
            original_volume_multiplier=vol_mult,
            max_rpe_cap=cap_rpe,
            excluded_exercises=excluded_names,
            safety_reasons=safety_reasons
        )

        return adjusted_list, adjustment

