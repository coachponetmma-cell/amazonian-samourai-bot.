from typing import List, Optional
from fastapi import APIRouter, Query, HTTPException
from pydantic import BaseModel
from app.models.schemas import Exercise, AthleteProfile, ReadinessStatus
from app.services.exercise_service import ExerciseService
from app.services.rules_engine import RulesEngine

router = APIRouter()


class FilterExercisesRequest(BaseModel):
    profile: AthleteProfile
    readiness_status: ReadinessStatus
    category: Optional[str] = None


class FilterExercisesResponse(BaseModel):
    valid_exercises: List[Exercise]
    excluded_reasons: List[str]


@router.get("/exercises", response_model=List[Exercise], summary="Récupération du catalogue d'exercices")
async def get_exercises(category: Optional[str] = Query(None, description="Filtrer par catégorie")):
    """
    Récupère la liste des exercices disponibles (depuis Supabase ou catalogue local fallback).
    """
    if category:
        return await ExerciseService.get_exercises_by_category(category)
    return await ExerciseService.get_all_exercises()


@router.post("/exercises/filter", response_model=FilterExercisesResponse, summary="Filtrage strict par équipement, blessures et Readiness")
async def filter_exercises(payload: FilterExercisesRequest):
    """
    Applique les HARD Rules pour exclure les exercices incompatibles avec l'équipement,
    les blessures déclarées ou le statut de fatigue.
    """
    all_exercises = await ExerciseService.get_all_exercises()
    if payload.category:
        all_exercises = [ex for ex in all_exercises if ex.category.lower() == payload.category.lower()]

    valid, exclusions = RulesEngine.filter_exercises_for_athlete(
        exercises=all_exercises,
        profile=payload.profile,
        readiness_status=payload.readiness_status
    )
    return FilterExercisesResponse(valid_exercises=valid, excluded_reasons=exclusions)


@router.post("/exercises/seed", summary="Injection du catalogue par défaut dans Supabase")
async def seed_exercises():
    """
    Injecte les exercices de base (MMA, Striking, Plyo, Mobilité) dans la table 'exercises' de Supabase.
    """
    count = await ExerciseService.seed_exercises_to_supabase()
    return {"status": "success", "exercises_seeded": count}
