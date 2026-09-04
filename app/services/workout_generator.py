import json
import urllib.parse
import logging
from typing import List, Optional, Dict
from pydantic import BaseModel, Field

from app.core.config import settings
from app.models.schemas import (
    AthleteProfile,
    DailyCheckinInput,
    ReadinessResult,
    ReadinessStatus,
    Exercise,
    WorkoutExercise,
    WorkoutBlock,
    WorkoutPlan,
    WorkoutAdjustment,
)
from app.services.rules_engine import RulesEngine
from app.services.exercise_service import ExerciseService
from app.services.gemini_service import GeminiService

logger = logging.getLogger(__name__)


# ==============================================================================
# SCHÃ‰MAS DE SORTIE STRUCTURÃ‰E POUR LE LLM GEMINI
# ==============================================================================
class LLMExerciseItem(BaseModel):
    exercise_name: str = Field(..., description="Nom exact de l'exercice choisi parmi les 49 exercices disponibles")
    sets: int = Field(..., description="Nombre de sÃ©ries (ex: 2 pour Ã©chauffement, 3-4 pour corps de sÃ©ance, 2-3 pour renfo/core)")
    reps_or_duration: str = Field(..., description="Ex: '8-10 reps', '45 s', '12/jambe', '60 s'")
    target_rpe: int = Field(..., description="IntensitÃ© RPE de 1 Ã  10 (PlafonnÃ© Ã  7 en ORANGE, 4 en ROUGE)")
    tempo: str = Field(default="2-0-1-0", description="Tempo d'exÃ©cution (ex: '2-0-1-0', 'Explosif', 'ContrÃ´lÃ©', 'Fluide')")
    rest_seconds: int = Field(default=60, description="Temps de repos en secondes entre les sÃ©ries (ex: 45, 60, 90)")
    coach_cue: str = Field(..., description="Consigne technique et mot d'attention personnalisÃ© de Jason Ponet")


class LLMBlockItem(BaseModel):
    block_number: int = Field(..., description="1, 2 ou 3")
    title: str = Field(..., description="Titre du bloc (ex: 'BLOC 1 : Ã‰CHAUFFEMENT & MOBILITÃ‰ DYNAMIQUE')")
    focus: str = Field(..., description="Focus du bloc (ex: 'MobilitÃ© vertÃ©brale, ouverture de hanches et mise en route')")
    target_rpe_range: str = Field(..., description="Fourchette RPE (ex: 'RPE 4-5', 'RPE 7 Max', 'RPE 8-9')")
    exercises: List[LLMExerciseItem] = Field(..., description="Liste des exercices (2 pour Bloc 1, 3 pour Bloc 2, 2 pour Bloc 3)")


class LLMWorkoutResponse(BaseModel):
    title: str = Field(..., description="Titre percutant et adaptÃ© de la sÃ©ance")
    focus: str = Field(..., description="Objectif principal de la sÃ©ance du jour")
    total_estimated_minutes: int = Field(..., description="DurÃ©e estimÃ©e de la sÃ©ance (40-45 min en Orange, ~50 min en Vert, ~30 min en Rouge)")
    coach_speech: str = Field(..., description="Mot d'encouragement de Jason Ponet (Warrior mindset, direct, technique, personnalisÃ©)")
    blocks: List[LLMBlockItem] = Field(..., description="Les 3 blocs obligatoires (Bloc 1: 2 exercices, Bloc 2: 3 exercices, Bloc 3: 2 exercices)")


WORKOUT_GENERATION_SYSTEM_PROMPT = """
Tu es Jason Ponet (Amazonian Samourai), combattant pro de MMA et Head Coach international.
Tu conÃ§ois une sÃ©ance d'entraÃ®nement UNIQUE, SCIENTIFIQUEMENT STRUCTURÃ‰E et SUR-MESURE pour l'athlÃ¨te Ã  partir de son check-in du jour.

### RÃˆGLES DE CONCEPTION DE LA SÃ‰ANCE (3 BLOCS OBLIGATOIRES - 7 EXERCICES AU TOTAL) :
1. **BLOC 1 : Ã‰CHAUFFEMENT / MOBILITÃ‰ DYNAMIQUE (EXACTEMENT 2 exercices)**
   - Choisis 2 exercices parmi la famille 'MobilitÃ©' ou Ã©chauffement articulaire (ex: World's Greatest Stretch, Cat-Cow, MobilitÃ© des hanches 90/90, Ouverture thoracique, MobilitÃ© des chevilles).
   - IntensitÃ© : RPE 4-5, 2 sÃ©ries, tempo fluide.

2. **BLOC 2 : CORPS DE SÃ‰ANCE / CONDITIONING & FORCE (EXACTEMENT 3 exercices)**
   - Choisis 3 exercices parmi 'Force (Bas du corps)', 'Force (Haut du corps poussÃ©e)', 'Force (Haut du corps tirage)', 'Full Body', 'Boxe' ou 'Cardio'.
   - **En statut VERT (R >= 3.8) :** IntensitÃ© haute (RPE 8-9), 3 Ã  4 sÃ©ries, puissance et explosivitÃ©.
   - **En statut ORANGE (2.5 <= R < 3.8) :** RÃ©gulation (-20% volume), 3 sÃ©ries, INTENSITÃ‰ PLAFONNÃ‰E Ã€ RPE 7 STRICT. ZÃ©ro Ã©chec musculaire.
   - **En statut ROUGE (R < 2.5 ou Douleur >= 4) :** MobilitÃ©, dÃ©charge articulaire et flux sanguin uniquement (RPE 3-4, zÃ©ro charge lourde).

3. **BLOC 3 : RENFORCEMENT, CORE & POSTURE (EXACTEMENT 2 exercices)**
   - Choisis 2 exercices parmi la famille 'Gainage', 'Core', 'MobilitÃ©' ou renforcement postural (ex: Plank, Side Plank, Bird Dog, Dead Bug, Mountain Climbers, etc.).
   - IntensitÃ© : RPE 6-7, 2 Ã  3 sÃ©ries.

4. **SÃ‰LECTION STRICTE DANS LE CATALOGUE DES 49 EXERCICES :**
   - Utilise UNIQUEMENT les noms exacts d'exercices prÃ©sents dans la liste fournie ci-dessous afin que les liens vidÃ©os YouTube rÃ©els soient automatiquement associÃ©s.
   - Adapte le choix des exercices aux Ã©ventuelles douleurs ou blessures dÃ©clarÃ©es par l'athlÃ¨te (ex: pas de Box Jump ou Walking Lunge si mal aux genoux, pas de DÃ©veloppÃ© militaire si douleur Ã©paule).

RÃ©ponds EXCLUSIVEMENT avec le format JSON respectant le schÃ©ma attendu.
"""


class WorkoutGenerator:
    """
    GÃ©nÃ©rateur de sÃ©ances personnalisÃ© propulsÃ© par Gemini 3.6 Flash et le catalogue des 49 exercices rÃ©els.
    """

    @classmethod
    def _get_clean_video_url(cls, matched_ex: Optional[Exercise], fallback_name: str) -> str:
        """
        Retourne la vraie URL YouTube Shorts de l'exercice ou un lien direct de recherche YouTube.
        """
        if matched_ex and matched_ex.video_url and matched_ex.video_url.startswith("http"):
            return matched_ex.video_url
        query = urllib.parse.quote(f"{fallback_name} exercice musculation")
        return f"https://www.youtube.com/results?search_query={query}"

    @classmethod
    async def build_workout(
        cls,
        profile: AthleteProfile,
        readiness: ReadinessResult,
        candidate_exercises: Optional[List[Exercise]] = None,
        raw_checkin_text: Optional[str] = None
    ) -> WorkoutPlan:
        """
        GÃ©nÃ¨re une sÃ©ance sur-mesure via Gemini en piochant dans les 49 exercices de la base.
        """
        if candidate_exercises is None:
            candidate_exercises = await ExerciseService.get_all_exercises()

        # Filtrage HARD Rules
        valid_exercises, exclusions = RulesEngine.filter_exercises_for_athlete(
            exercises=candidate_exercises,
            profile=profile,
            readiness_status=readiness.status
        )

        # Indexer les exercices par nom et code_id pour retrouver les URLs exactes
        exercise_map: Dict[str, Exercise] = {}
        for ex in candidate_exercises:
            exercise_map[ex.name.lower().strip()] = ex
            if ex.code_id:
                exercise_map[ex.code_id.lower().strip()] = ex

        # RÃ©sumÃ© du catalogue pour le prompt
        catalog_by_family: Dict[str, List[str]] = {}
        for ex in valid_exercises:
            fam = f"{ex.family} - {ex.subfamily}" if ex.subfamily else ex.family
            catalog_by_family.setdefault(fam, []).append(f"{ex.name} (MatÃ©riel: {ex.material})")

        catalog_summary = "\n".join([
            f"â€¢ **{fam}** : {', '.join(items)}" for fam, items in catalog_by_family.items()
        ])

        # Appel LLM Gemini
        gemini_client = GeminiService.get_client()
        llm_response: Optional[LLMWorkoutResponse] = None

        if gemini_client:
            try:
                from google.genai import types

                user_prompt = f"""
ATHLÃˆTE :
- **Nom :** {profile.first_name or 'AthlÃ¨te'}
- **Objectif :** {profile.goal}
- **MatÃ©riel possÃ©dÃ© :** {', '.join(profile.available_equipment) if profile.available_equipment else 'Aucun (Poids du corps)'}
- **Blessures / Contraintes :** {', '.join(profile.injuries_and_constraints) if profile.injuries_and_constraints else 'Aucune'}

CHECK-IN DU JOUR :
- **Message / Ressenti :** "{raw_checkin_text or 'Check-in standard'}"
- **Score Readiness (R) :** {readiness.score}/5.0
- **Statut de SÃ©curitÃ© :** {readiness.status.value} (Plafond RPE: {readiness.intensity_cap_rpe or 'Libre'}, Vol Multiplier: {readiness.volume_multiplier})
- **Alertes de SÃ©curitÃ© :** {', '.join(readiness.alerts) if readiness.alerts else 'Aucune'}

CATALOGUE DES 49 EXERCICES DISPONIBLES ET VALIDÃ‰S :
{catalog_summary}

GÃ©nÃ¨re la sÃ©ance en 3 blocs obligatoires (Bloc 1: 2 exercices, Bloc 2: 3 exercices, Bloc 3: 2 exercices) parfaitement adaptÃ©e Ã  l'Ã©tat du jour.
"""

                response = gemini_client.models.generate_content(
                    model="gemini-3.6-flash",
                    contents=[WORKOUT_GENERATION_SYSTEM_PROMPT, user_prompt],
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=LLMWorkoutResponse,
                        temperature=0.7
                    )
                )

                parsed_json = json.loads(response.text)
                llm_response = LLMWorkoutResponse(**parsed_json)
                logger.info("SÃ©ance gÃ©nÃ©rÃ©e avec succÃ¨s par Gemini 3.6 Flash.")

            except Exception as e:
                logger.error(f"Erreur gÃ©nÃ©ration Gemini: {e}")
                llm_response = None

        blocks: List[WorkoutBlock] = []

        if llm_response and llm_response.blocks:
            title = llm_response.title
            focus = llm_response.focus
            total_minutes = llm_response.total_estimated_minutes
            coach_notes = llm_response.coach_speech

            for b_item in llm_response.blocks:
                block_exercises: List[WorkoutExercise] = []
                for ex_item in b_item.exercises:
                    clean_name = ex_item.exercise_name.lower().strip()
                    
                    matched_ex = exercise_map.get(clean_name)
                    if not matched_ex:
                        for k, v in exercise_map.items():
                            if k in clean_name or clean_name in k:
                                matched_ex = v
                                break

                    final_video_url = cls._get_clean_video_url(matched_ex, ex_item.exercise_name)

                    if matched_ex:
                        exercise_obj = matched_ex.model_copy(update={"video_url": final_video_url})
                    else:
                        exercise_obj = Exercise(
                            name=ex_item.exercise_name,
                            family="Force",
                            material="Aucun",
                            video_url=final_video_url,
                            instructions=ex_item.coach_cue
                        )

                    # Plafonnement de sÃ©curitÃ©
                    target_rpe = ex_item.target_rpe
                    if readiness.intensity_cap_rpe and target_rpe > readiness.intensity_cap_rpe:
                        target_rpe = readiness.intensity_cap_rpe

                    block_exercises.append(
                        WorkoutExercise(
                            exercise=exercise_obj,
                            sets=ex_item.sets,
                            reps_or_duration=ex_item.reps_or_duration,
                            target_rpe=target_rpe,
                            tempo=ex_item.tempo,
                            rest_seconds=ex_item.rest_seconds,
                            notes=ex_item.coach_cue
                        )
                    )

                blocks.append(
                    WorkoutBlock(
                        block_number=b_item.block_number,
                        title=b_item.title,
                        focus=b_item.focus,
                        target_rpe_range=b_item.target_rpe_range,
                        exercises=block_exercises
                    )
                )

        else:
            # Fallback direct si API indisponible
            title = f"{'ðŸŸ¢' if readiness.status == ReadinessStatus.GREEN else ('ðŸŸ¡' if readiness.status == ReadinessStatus.ORANGE else 'ðŸ”´')} SÃ‰ANCE {readiness.status.value} - COACHING AMAZONIAN SAMOURAI"
            focus = "Conditioning, renforcement et mobilitÃ©"
            total_minutes = 45 if readiness.status != ReadinessStatus.RED else 30
            coach_notes = "SÃ©ance structurÃ©e selon ton score de Readiness. Reste concentrÃ© sur la prÃ©cision et l'intensitÃ© juste."

            # Bloc 1 : MobilitÃ© (2 exs)
            b1 = [ex for ex in valid_exercises if ex.family == "MobilitÃ©"][:2]
            if len(b1) < 2:
                b1 = valid_exercises[:2]

            block1 = WorkoutBlock(
                block_number=1,
                title="BLOC 1 : Ã‰CHAUFFEMENT & MOBILITÃ‰ DYNAMIQUE",
                focus="Activation articulaire et respiration",
                target_rpe_range="RPE 4-5",
                exercises=[
                    WorkoutExercise(
                        exercise=ex,
                        sets=2,
                        reps_or_duration="10 reps ou 45 s",
                        target_rpe=5,
                        tempo="Fluide",
                        rest_seconds=45,
                        notes=ex.instructions or "Activation progressive."
                    )
                    for ex in b1
                ]
            )

            # Bloc 2 : Corps de sÃ©ance (3 exs)
            b1_names = {e.name for e in b1}
            b2 = [ex for ex in valid_exercises if ex.family in ["Force", "Full Body", "Cardio", "Boxe"] and ex.name not in b1_names][:3]
            if len(b2) < 3:
                b2 = [ex for ex in valid_exercises if ex.name not in b1_names][:3]

            block2 = WorkoutBlock(
                block_number=2,
                title="BLOC 2 : CORPS DE SÃ‰ANCE & CONDITIONING",
                focus="Puissance, force et explosivitÃ©",
                target_rpe_range="RPE 7 Max" if readiness.status == ReadinessStatus.ORANGE else "RPE 8-9",
                exercises=[
                    WorkoutExercise(
                        exercise=ex,
                        sets=3,
                        reps_or_duration="8-10 reps",
                        target_rpe=7 if readiness.status == ReadinessStatus.ORANGE else 8,
                        tempo="Explosif",
                        rest_seconds=75,
                        notes=ex.instructions or "Vitesse maximale."
                    )
                    for ex in b2
                ]
            )

            # Bloc 3 : Gainage / Core (2 exs)
            used_names = {e.name for e in b1 + b2}
            b3 = [ex for ex in valid_exercises if ex.family == "Gainage" and ex.name not in used_names][:2]
            if len(b3) < 2:
                b3 = [ex for ex in valid_exercises if ex.name not in used_names][:2]

            block3 = WorkoutBlock(
                block_number=3,
                title="BLOC 3 : RENFORCEMENT, CORE & POSTURE",
                focus="StabilitÃ© et soliditÃ© du tronc",
                target_rpe_range="RPE 6-7",
                exercises=[
                    WorkoutExercise(
                        exercise=ex,
                        sets=3,
                        reps_or_duration="45 s ou 12 reps",
                        target_rpe=7,
                        tempo="ContrÃ´lÃ©",
                        rest_seconds=60,
                        notes=ex.instructions or "Gainage verrouillÃ©."
                    )
                    for ex in b3
                ]
            )

            blocks = [block1, block2, block3]

        all_exercises: List[WorkoutExercise] = []
        for blk in blocks:
            all_exercises.extend(blk.exercises)

        return WorkoutPlan(
            athlete_id=profile.athlete_id,
            readiness_status=readiness.status,
            readiness_score=readiness.score,
            title=title,
            focus=focus,
            blocks=blocks,
            exercises=all_exercises,
            total_estimated_minutes=total_minutes,
            coach_notes=coach_notes
        )

    @classmethod
    def format_telegram_message(cls, plan: WorkoutPlan, athlete_name: str = "Guerrier") -> str:
        """
        Formate le plan d'entraÃ®nement en 3 Blocs avec les vrais liens vidÃ©os YouTube Shorts.
        """
        status_emoji = "ðŸŸ¢" if plan.readiness_status == ReadinessStatus.GREEN else ("ðŸŸ¡" if plan.readiness_status == ReadinessStatus.ORANGE else "ðŸ”´")
        status_label = plan.readiness_status.value

        msg = []
        msg.append("ðŸ¥‹ **COACHING AMAZONIAN SAMOURAI**")
        msg.append(f"AthlÃ¨te : **{athlete_name}** | Statut : {status_emoji} **{status_label}** (Score R : `{plan.readiness_score}/5.0`)\n")
        msg.append(f"ðŸ“‹ **{plan.title}**")
        msg.append(f"ðŸŽ¯ **Focus :** {plan.focus}")
        msg.append(f"â±ï¸ **DurÃ©e totale estimÃ©e :** ~{plan.total_estimated_minutes} min\n")

        msg.append(f"ðŸ’¬ **Le Mot du Coach (Jason Ponet) :**\n_{plan.coach_notes}_\n")

        if plan.adjustment and plan.adjustment.safety_reasons:
            msg.append("ðŸ›¡ï¸ **Ajustements de SÃ©curitÃ© :**")
            for reason in plan.adjustment.safety_reasons:
                msg.append(f" â€¢ {reason}")
            msg.append("")

        for block in plan.blocks:
            msg.append("â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”")
            msg.append(f"ðŸ”¥ **{block.title}** ({len(block.exercises)} exercices)")
            msg.append(f"ðŸ“Œ _{block.focus}_ | IntensitÃ© cible : `{block.target_rpe_range}`\n")

            for idx, item in enumerate(block.exercises, 1):
                ex = item.exercise
                video_url = ex.video_url or cls._get_clean_video_url(ex, ex.name)
                video_link_md = f"[ðŸŽ¬ Voir la dÃ©monstration]({video_url})"

                msg.append(f"**{block.block_number}.{idx} - {ex.name}**")
                msg.append(f"   ðŸ“Š **Volume :** {item.sets} sÃ©ries Ã— {item.reps_or_duration}")
                msg.append(f"   âš¡ **IntensitÃ© :** RPE {item.target_rpe}/10 | Tempo : {item.tempo} | Repos : {item.rest_seconds}s")
                if item.notes:
                    msg.append(f"   ðŸ’¡ **Consigne :** _{item.notes}_")
                msg.append(f"   ðŸ”— {video_link_md}\n")

        msg.append("â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”")
        msg.append("ðŸ‘Š *AprÃ¨s la sÃ©ance, envoie ton dÃ©briefing vocal ou texte (RPE ressenti, sensations).*")
        msg.append("ðŸ”¥ **Libertad & Performance.**")

        return "\n".join(msg)

