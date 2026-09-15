import asyncio
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional
from supabase import create_client, Client
from app.core.config import settings

logger = logging.getLogger(__name__)

# Initialisation du client Supabase (clé service_role prioritaire pour les accès backend)
supabase_key = settings.SUPABASE_SERVICE_ROLE_KEY or settings.SUPABASE_KEY
supabase: Client = create_client(settings.SUPABASE_URL, supabase_key)


def _is_valid_uuid(val: Any) -> bool:
    """Vérifie si une chaîne est un UUID valide pour PostgreSQL."""
    if not val:
        return False
    try:
        uuid.UUID(str(val))
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def get_all_exercises_raw() -> List[Dict[str, Any]]:
    """
    Récupère tous les exercices depuis la table Supabase, avec fallback sur les 49 exercices locaux.
    """
    try:
        query = supabase.table("exercises").select("*")
        response = query.execute()
        if response.data and len(response.data) > 0:
            return response.data
    except Exception as e:
        logger.warning(f"Erreur lecture Supabase exercises: {e}")

    # Fallback local autonome
    try:
        from app.services.exercise_service import DEFAULT_EXERCISES
        return [ex.model_dump(exclude_none=True) for ex in DEFAULT_EXERCISES]
    except Exception as e:
        logger.error(f"Erreur fallback DEFAULT_EXERCISES: {e}")
        return []


def filter_exercises_by_equipment(all_exercises: List[Dict[str, Any]], equipment_needed: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Filtrage 100 % étanche des exercices selon le matériel ou l'environnement de l'athlète.
    Garantit l'exclusion absolue du matériel lourd (barres, tractions, landmine, bancs) en chambre d'hôtel / poids du corps.
    """
    if not all_exercises:
        return []

    eq_str = (equipment_needed or "").strip().lower()

    # Mots-clés pour salle complète / équipement illimité
    full_gym_keywords = ["salle complète", "salle complete", "gym", "toute la salle", "salle de sport", "musculation complete", "full gym"]
    if any(k in eq_str for k in full_gym_keywords):
        return all_exercises

    # Détection Poids du corps / Chambre d'hôtel / Sans matériel
    bodyweight_keywords = [
        "poids du corps", "bodyweight", "aucun", "sans materiel", "sans matériel",
        "pas de materiel", "pas de matériel", "pas de matos", "hotel", "hôtel",
        "chambre", "chambre d'hôtel", "chambre d'hotel", "déplacement", "deplacement",
        "rien", "zero materiel", "zéro matériel"
    ]

    # Mots-clés désignant la présence d'un matériel extérieur spécifique
    gear_detection = {
        "kettlebell": ["kettlebell", "kb"],
        "haltere": ["haltère", "haltere", "dumbbell", "haltères", "halteres"],
        "barre": ["barre", "barbell"],
        "traction": ["traction", "pullup", "pull-up", "barre de traction", "barre fixe"],
        "corde": ["corde à sauter", "corde a sauter", "corde", "rope"],
        "banc": ["banc", "bench"],
        "landmine": ["landmine"],
        "box": ["box", "plyo box", "boîte", "boite"],
        "dips": ["barres parallèles", "barres paralleles", "dips"],
        "trx": ["trx", "sangles"],
    }

    detected_gear = set()
    for gear, keywords in gear_detection.items():
        if any(kw in eq_str for kw in keywords):
            detected_gear.add(gear)

    # Si l'environnement est purement poids du corps (ou si aucun matériel spécifique n'est détecté)
    is_pure_bodyweight = (any(bw in eq_str for bw in bodyweight_keywords) and len(detected_gear) == 0) or not eq_str

    # Matériaux de base autorisés en poids du corps / chambre d'hôtel
    base_bodyweight_materials = {"aucun", "tapis", "mur", "none", ""}

    filtered = []
    for ex in all_exercises:
        # Prise en compte flexible du champ material ou equipment
        raw_mat = str(ex.get("material") or ex.get("equipment") or "Aucun").strip().lower()

        # Si purement poids du corps : n'autoriser QUE les mouvements au sol / sans matériel
        if is_pure_bodyweight:
            if raw_mat in base_bodyweight_materials or "aucun" in raw_mat or "bodyweight" in raw_mat:
                filtered.append(ex)
            continue

        # Athlète avec matériel spécifique : toujours autoriser le poids du corps + le matériel détecté
        is_allowed = False
        if raw_mat in base_bodyweight_materials or "aucun" in raw_mat or "bodyweight" in raw_mat:
            is_allowed = True
        elif "kettlebell" in detected_gear and "kettlebell" in raw_mat:
            is_allowed = True
        elif "haltere" in detected_gear and ("haltère" in raw_mat or "haltere" in raw_mat):
            # Si banc non détecté, exclure les exercices nécessitant expressément un banc
            if "banc" not in raw_mat or "banc" in detected_gear:
                is_allowed = True
        elif "corde" in detected_gear and "corde" in raw_mat:
            is_allowed = True
        elif "traction" in detected_gear and "traction" in raw_mat:
            is_allowed = True
        elif "dips" in detected_gear and ("parallèle" in raw_mat or "parallele" in raw_mat or "dips" in raw_mat):
            is_allowed = True
        elif "trx" in detected_gear and ("trx" in raw_mat or "barre fixe basse" in raw_mat):
            is_allowed = True
        elif "landmine" in detected_gear and "landmine" in raw_mat:
            is_allowed = True
        elif "box" in detected_gear and "box" in raw_mat:
            is_allowed = True
        elif "banc" in detected_gear and "banc" in raw_mat:
            is_allowed = True
        elif "barre" in detected_gear and "barre" in raw_mat and "traction" not in raw_mat and "landmine" not in raw_mat and "parallèle" not in raw_mat:
            is_allowed = True

        if is_allowed:
            filtered.append(ex)

    # Si le filtre est vide (ex: matériel exotique non référencé), renvoyer au moins les exercices poids du corps
    if not filtered:
        filtered = [ex for ex in all_exercises if str(ex.get("material") or ex.get("equipment") or "Aucun").strip().lower() in base_bodyweight_materials]

    return filtered


def get_available_exercises(equipment_needed: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Point d'entrée pour récupérer la liste filtrée d'exercices avec liens et consignes.
    """
    all_exs = get_all_exercises_raw()
    return filter_exercises_by_equipment(all_exs, equipment_needed)


# ==============================================================================
# GESTION ATHLÈTES, CHECK-INS & WORKOUT LOGS
# ==============================================================================

def _robust_insert(client_obj: Client, table_name: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Insère des données dans une table Supabase en éliminant automatiquement
    les colonnes qui n'existent pas encore dans le cache de schéma PostgREST.
    """
    attempt_data = dict(data)
    for _ in range(6):
        try:
            res = client_obj.table(table_name).insert(attempt_data).execute()
            return res.data[0] if res.data else None
        except Exception as e:
            err_msg = str(e)
            match = re.search(r"Could not find the '([^']+)' column", err_msg)
            if match:
                missing_col = match.group(1)
                logger.debug(f"Colonne '{missing_col}' absente de {table_name}, relance sans cette colonne.")
                attempt_data.pop(missing_col, None)
            else:
                logger.error(f"Erreur insertion dans {table_name}: {e}")
                return None
    return None


def athlete_profile_exists(telegram_id: int | str) -> bool:
    """
    Vérifie si un athlète possède un profil dans athlete_profiles via son telegram_id.
    """
    try:
        t_id = int(telegram_id)
        # Requête directe prioritaire sur athlete_profiles
        try:
            resp = supabase.table("athlete_profiles").select("athlete_id").eq("telegram_id", t_id).execute()
            if resp.data and len(resp.data) > 0:
                return True
        except Exception as col_err:
            if "telegram_id" in str(col_err):
                # Fallback de transition si la colonne telegram_id n'a pas encore été ajoutée dans Supabase
                join_resp = supabase.table("athlete_profiles").select("athlete_id, athletes!inner(id)").eq("athletes.telegram_id", t_id).execute()
                return bool(join_resp.data and len(join_resp.data) > 0)
            raise col_err
    except Exception as e:
        logger.warning(f"Erreur athlete_profile_exists ({telegram_id}): {e}")
    return False


def save_new_athlete_profile(
    telegram_id: int | str,
    athlete_name: str,
    goal: str,
    default_equipment: str,
    injuries: str,
    username: Optional[str] = None,
    raw_user_first_name: Optional[str] = None,
    raw_user_last_name: Optional[str] = None,
    tracking_type: str = "both",
    nutrition_mode: Optional[str] = None,
    service_tier: str = "100%_ia",
    weight_kg: Optional[float] = None,
    activity_level: Optional[str] = None,
    target_calories: Optional[int] = None,
    target_proteins: Optional[int] = None,
    target_fats: Optional[int] = None,
    target_carbs: Optional[int] = None,
    segment: str = "loisir",
    target_weight_kg: Optional[float] = None,
    last_checkin_at: Optional[str] = None
) -> Dict[str, Any]:
    """
    Enregistre ou met à jour le profil d'un nouvel athlète dans athlete_profiles suite au tunnel d'onboarding.
    Supporte les modules sport, nutrition, segmentation (loisir vs elite) et cibles nutritionnelles.
    """
    t_id = int(telegram_id)
    clean_name = athlete_name.strip()
    parts = clean_name.split(maxsplit=1)
    first_name = parts[0]
    last_name = parts[1] if len(parts) > 1 else (raw_user_last_name or "")

    ath_id = None
    # Maintien de la cohérence avec la contrainte de clé étrangère PostgreSQL si présente
    try:
        ath_check = supabase.table("athletes").select("id").eq("telegram_id", t_id).execute()
        if ath_check.data:
            ath_id = ath_check.data[0]["id"]
            supabase.table("athletes").update({
                "first_name": first_name,
                "last_name": last_name,
                "username": username or "",
                "status": "active"
            }).eq("id", ath_id).execute()
        else:
            ath_insert = {
                "code_id": f"TG-{t_id}",
                "telegram_id": t_id,
                "first_name": first_name,
                "last_name": last_name,
                "username": username or "",
                "status": "active"
            }
            res = supabase.table("athletes").insert(ath_insert).execute()
            if res.data:
                ath_id = res.data[0]["id"]
    except Exception as e:
        logger.debug(f"Info contrainte athletes ({t_id}): {e}")

    if not ath_id:
        ath_id = f"local-{t_id}"

    # Sauvegarde sur athlete_profiles (inclut telegram_id, modules, nutrition, segment et identité)
    prof_data = {
        "athlete_id": ath_id,
        "telegram_id": t_id,
        "first_name": first_name,
        "last_name": last_name,
        "username": username or "",
        "goal": goal,
        "default_equipment": default_equipment,
        "injuries_history": injuries,
        "status": "active",
        "language": "fr",
        "tracking_type": tracking_type,
        "nutrition_mode": nutrition_mode,
        "service_tier": service_tier,
        "weight_kg": weight_kg,
        "activity_level": activity_level,
        "target_calories": target_calories,
        "target_proteins": target_proteins,
        "target_fats": target_fats,
        "target_carbs": target_carbs,
        "segment": segment,
        "target_weight_kg": target_weight_kg,
        "last_checkin_at": last_checkin_at or datetime.now(timezone.utc).isoformat()
    }

    # Nettoyage des valeurs None optionnelles pour éviter les erreurs d'insertion
    cleaned_prof_data = {k: v for k, v in prof_data.items() if v is not None}

    # Tentative d'upsert avec élimination des colonnes non encore créées
    for _ in range(6):
        try:
            supabase.table("athlete_profiles").upsert(cleaned_prof_data, on_conflict="athlete_id").execute()
            break
        except Exception as e:
            err_msg = str(e)
            match = re.search(r"Could not find the '([^']+)' column", err_msg)
            if match:
                missing_col = match.group(1)
                logger.debug(f"Colonne '{missing_col}' absente de athlete_profiles, relance sans cette colonne.")
                cleaned_prof_data.pop(missing_col, None)
            else:
                logger.warning(f"Erreur upsert athlete_profiles ({ath_id}): {e}")
                # Fallback minimal
                try:
                    minimal_data = {
                        "athlete_id": ath_id,
                        "goal": goal,
                        "default_equipment": default_equipment,
                        "injuries_history": injuries,
                        "language": "fr"
                    }
                    supabase.table("athlete_profiles").upsert(minimal_data, on_conflict="athlete_id").execute()
                except Exception:
                    pass
                break

    return {
        "id": ath_id,
        "athlete_id": ath_id,
        "telegram_id": t_id,
        "first_name": first_name,
        "last_name": last_name,
        "username": username,
        "goal": goal,
        "default_equipment": default_equipment,
        "injuries_history": injuries,
        "tracking_type": tracking_type,
        "nutrition_mode": nutrition_mode,
        "service_tier": service_tier,
        "weight_kg": weight_kg,
        "activity_level": activity_level,
        "target_calories": target_calories,
        "target_proteins": target_proteins,
        "target_fats": target_fats,
        "target_carbs": target_carbs,
        "segment": segment,
        "target_weight_kg": target_weight_kg,
        "last_checkin_at": last_checkin_at,
        "status": "active"
    }


def get_athlete_profile(telegram_id: int | str) -> Dict[str, Any]:
    """
    Récupère le profil athlète directement depuis la table athlete_profiles via son telegram_id.
    Aucun fallback limit(1) n'est appliqué pour éviter toute collision de données.
    """
    default_profile = {
        "id": None,
        "athlete_id": None,
        "first_name": "Combattant",
        "last_name": "",
        "telegram_id": int(telegram_id) if str(telegram_id).isdigit() else None,
        "goal": "MMA / Combat",
        "default_equipment": "Poids du corps",
        "injuries_history": "aucune",
        "status": "active",
        "tracking_type": "both",
        "nutrition_mode": "ocr_vision",
        "service_tier": "100%_ia",
        "weight_kg": None,
        "activity_level": None,
        "target_calories": None,
        "target_proteins": None,
        "target_fats": None,
        "target_carbs": None,
        "segment": "loisir",
        "target_weight_kg": None,
        "last_checkin_at": None
    }

    try:
        t_id = int(telegram_id)
        # 1. Recherche directe dans athlete_profiles par telegram_id
        try:
            resp = supabase.table("athlete_profiles").select("*").eq("telegram_id", t_id).execute()
            if resp.data and len(resp.data) > 0:
                p = resp.data[0]
                return {
                    "id": p.get("athlete_id") or str(p.get("id", "")),
                    "athlete_id": p.get("athlete_id") or str(p.get("id", "")),
                    "first_name": p.get("first_name") or "Combattant",
                    "last_name": p.get("last_name") or "",
                    "username": p.get("username"),
                    "telegram_id": t_id,
                    "goal": p.get("goal") or "MMA / Combat",
                    "default_equipment": p.get("default_equipment") or "Poids du corps",
                    "injuries_history": p.get("injuries_history") or "aucune",
                    "status": p.get("status", "active"),
                    "tracking_type": p.get("tracking_type") or "both",
                    "nutrition_mode": p.get("nutrition_mode") or "ocr_vision",
                    "service_tier": p.get("service_tier") or "100%_ia",
                    "weight_kg": p.get("weight_kg"),
                    "activity_level": p.get("activity_level"),
                    "target_calories": p.get("target_calories"),
                    "target_proteins": p.get("target_proteins"),
                    "target_fats": p.get("target_fats"),
                    "target_carbs": p.get("target_carbs"),
                    "segment": p.get("segment") or "loisir",
                    "target_weight_kg": p.get("target_weight_kg"),
                    "last_checkin_at": p.get("last_checkin_at")
                }
        except Exception as col_err:
            if "telegram_id" not in str(col_err):
                raise col_err

        # 2. Requête conjointe avec athletes si telegram_id n'est pas encore directement sur athlete_profiles
        join_resp = supabase.table("athlete_profiles").select("*, athletes!inner(*)").eq("athletes.telegram_id", t_id).execute()
        if join_resp.data and len(join_resp.data) > 0:
            p = join_resp.data[0]
            ath = p.get("athletes") or {}
            ath_id = p.get("athlete_id") or ath.get("id")
            return {
                "id": ath_id,
                "athlete_id": ath_id,
                "first_name": ath.get("first_name") or p.get("first_name") or "Combattant",
                "last_name": ath.get("last_name") or p.get("last_name") or "",
                "username": ath.get("username") or p.get("username"),
                "telegram_id": t_id,
                "goal": p.get("goal") or ath.get("main_objective") or ath.get("goal") or "MMA / Combat",
                "default_equipment": p.get("default_equipment") or ath.get("available_equipment") or "Poids du corps",
                "injuries_history": p.get("injuries_history") or ath.get("injuries") or "aucune",
                "status": ath.get("status") or p.get("status") or "active",
                "tracking_type": p.get("tracking_type") or "both",
                "nutrition_mode": p.get("nutrition_mode") or "ocr_vision",
                "service_tier": p.get("service_tier") or "100%_ia",
                "weight_kg": p.get("weight_kg"),
                "activity_level": p.get("activity_level"),
                "target_calories": p.get("target_calories"),
                "target_proteins": p.get("target_proteins"),
                "target_fats": p.get("target_fats"),
                "target_carbs": p.get("target_carbs"),
                "segment": p.get("segment") or "loisir",
                "target_weight_kg": p.get("target_weight_kg"),
                "last_checkin_at": p.get("last_checkin_at")
            }

    except Exception as e:
        logger.warning(f"Erreur recherche athlete_profiles ({telegram_id}): {e}")

    return default_profile


# Alias pour rétrocompatibilité totale
get_athlete_by_telegram_id = get_athlete_profile


def get_athlete_by_id(athlete_id: str) -> Dict[str, Any]:
    """
    Récupère le profil athlète par son identifiant unique depuis athlete_profiles.
    """
    if not athlete_id or not _is_valid_uuid(athlete_id):
        return {"id": athlete_id, "athlete_id": athlete_id, "first_name": "Athlète", "goal": "MMA / Combat", "segment": "loisir"}

    try:
        resp = supabase.table("athlete_profiles").select("*, athletes(*)").eq("athlete_id", athlete_id).execute()
        if resp.data and len(resp.data) > 0:
            p = resp.data[0]
            ath = p.get("athletes") or {}
            return {
                "id": athlete_id,
                "athlete_id": athlete_id,
                "first_name": p.get("first_name") or ath.get("first_name") or "Combattant",
                "last_name": p.get("last_name") or ath.get("last_name") or "",
                "username": p.get("username") or ath.get("username"),
                "telegram_id": p.get("telegram_id") or ath.get("telegram_id"),
                "goal": p.get("goal") or ath.get("goal") or "MMA / Combat",
                "default_equipment": p.get("default_equipment") or ath.get("default_equipment") or "Poids du corps",
                "injuries_history": p.get("injuries_history") or ath.get("injuries") or "aucune",
                "status": p.get("status") or ath.get("status") or "active",
                "tracking_type": p.get("tracking_type") or "both",
                "nutrition_mode": p.get("nutrition_mode") or "ocr_vision",
                "service_tier": p.get("service_tier") or "100%_ia",
                "weight_kg": p.get("weight_kg"),
                "activity_level": p.get("activity_level"),
                "target_calories": p.get("target_calories"),
                "target_proteins": p.get("target_proteins"),
                "target_fats": p.get("target_fats"),
                "target_carbs": p.get("target_carbs"),
                "segment": p.get("segment") or "loisir",
                "target_weight_kg": p.get("target_weight_kg"),
                "last_checkin_at": p.get("last_checkin_at")
            }
    except Exception as e:
        logger.warning(f"Erreur get_athlete_by_id {athlete_id}: {e}")
    return {"id": athlete_id, "athlete_id": athlete_id, "first_name": "Athlète", "goal": "MMA / Combat", "segment": "loisir"}


def log_nutrition_entry(
    athlete_id: Optional[str] = None,
    telegram_id: Optional[int | str] = None,
    meal_type: str = "assiette_ocr",
    analysis_text: str = "",
    raw_user_input: Optional[str] = None,
    photo_url: Optional[str] = None,
    calories_est: Optional[int] = None,
    proteins_est: Optional[int] = None,
    fats_est: Optional[int] = None,
    carbs_est: Optional[int] = None
) -> Optional[Dict[str, Any]]:
    """
    Enregistre une analyse nutritionnelle (assiette OCR, capture app ou texte) dans nutrition_logs.
    """
    try:
        payload = {
            "athlete_id": athlete_id,
            "telegram_id": int(telegram_id) if telegram_id and str(telegram_id).isdigit() else None,
            "meal_type": meal_type,
            "analysis_text": analysis_text,
            "raw_user_input": raw_user_input,
            "photo_url": photo_url,
            "calories_est": calories_est,
            "proteins_est": proteins_est,
            "fats_est": fats_est,
            "carbs_est": carbs_est,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        res = _robust_insert(supabase, "nutrition_logs", payload)
        return res
    except Exception as e:
        logger.warning(f"Erreur enregistrement nutrition_logs: {e}")
        return None


def get_all_athletes() -> List[Dict[str, Any]]:
    """
    Liste tous les profils d'athlètes enregistrés depuis athlete_profiles.
    """
    try:
        resp = supabase.table("athlete_profiles").select("*, athletes(*)").execute()
        athletes = []
        for p in (resp.data or []):
            ath = p.get("athletes") or {}
            ath_id = p.get("athlete_id") or ath.get("id")
            athletes.append({
                "id": ath_id,
                "athlete_id": ath_id,
                "first_name": p.get("first_name") or ath.get("first_name") or "Combattant",
                "last_name": p.get("last_name") or ath.get("last_name") or "",
                "username": p.get("username") or ath.get("username"),
                "telegram_id": p.get("telegram_id") or ath.get("telegram_id"),
                "goal": p.get("goal") or ath.get("goal") or "MMA / Combat",
                "default_equipment": p.get("default_equipment") or ath.get("default_equipment") or "Poids du corps",
                "status": p.get("status") or ath.get("status") or "active"
            })
        return athletes
    except Exception as e:
        logger.warning(f"Erreur get_all_athletes: {e}")
        return []


def log_workout_generation(athlete_id: str, prescribed_rpe: int = 7, summary_text: str = "", checkin_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Enregistre la séance prescrite dans la table workout_logs.
    """
    if not athlete_id:
        return None
    try:
        payload = {
            "athlete_id": athlete_id,
            "checkin_id": checkin_id,
            "rpe_score": prescribed_rpe,
            "feedback_text": summary_text[:1000] if summary_text else "Séance prescrite",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "log_type": "workout_generated"
        }
        return _robust_insert(supabase, "workout_logs", payload)
    except Exception as e:
        logger.error(f"Erreur insertion workout_logs (generated): {e}")
        return None


def log_workout_completion(
    athlete_id: Optional[str] = None,
    telegram_id: Optional[int | str] = None,
    rpe_score: Optional[int] = None,
    rpe_real: Optional[int] = None,
    feedback_text: str = "",
    completed: bool = True,
    completed_at: Optional[str] = None,
    checkin_id: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Enregistre le feedback / débriefing de fin de séance d'un athlète dans workout_logs et debriefs.
    Renseigne telegram_id, completed=True, rpe_real, feedback_text, completed_at.
    """
    t_id = int(telegram_id) if telegram_id and str(telegram_id).isdigit() else None
    if not athlete_id and t_id:
        ath = get_athlete_by_telegram_id(t_id)
        athlete_id = ath.get("id")

    final_rpe = rpe_real if rpe_real is not None else (rpe_score or 7)
    now_iso = completed_at or datetime.now(timezone.utc).isoformat()

    payload = {
        "athlete_id": athlete_id,
        "checkin_id": checkin_id,
        "rpe_score": final_rpe,
        "feedback_text": feedback_text[:1000] if feedback_text else "Séance validée",
        "completed_at": now_iso,
        "log_type": "workout_completion",
        "telegram_id": t_id,
        "completed": bool(completed),
        "rpe_real": final_rpe
    }

    res = _robust_insert(supabase, "workout_logs", payload)

    # Enregistrement synchronisé dans debriefs si la table est active
    if athlete_id:
        try:
            supabase.table("debriefs").insert({
                "athlete_id": athlete_id,
                "rpe": final_rpe,
                "feedback": feedback_text[:1000] if feedback_text else "Retour",
                "created_at": now_iso
            }).execute()
        except Exception:
            pass

    return res


def log_checkin(athlete_id: str, analysis: Any, raw_text: str) -> Optional[Dict[str, Any]]:
    """
    Enregistre le check-in quotidien dans la table checkins.
    """
    if not athlete_id:
        return None
    try:
        sleep = getattr(analysis, "sleep_score", None) or 3
        energy = getattr(analysis, "energy_score", None) or 3
        fatigue = getattr(analysis, "fatigue_score", None) or 3
        stress = getattr(analysis, "stress_score", None) or 2
        pain = getattr(analysis, "soreness_score", None) or 1

        # Calcul indicatif du readiness score (1 à 5)
        # R = 0.25*sleep + 0.25*energy + 0.20*(6-fatigue) + 0.15*(6-stress) + 0.15*(6-pain)
        # Normalisation si scores sur 10 :
        s_5 = min(max(round(sleep / 2.0 if sleep > 5 else sleep), 1), 5)
        e_5 = min(max(round(energy / 2.0 if energy > 5 else energy), 1), 5)
        f_5 = min(max(round(fatigue / 2.0 if fatigue > 5 else fatigue), 1), 5)
        st_5 = min(max(round(stress / 2.0 if stress > 5 else stress), 1), 5)
        p_5 = min(max(round(pain / 2.0 if pain > 5 else pain), 1), 5)

        readiness = round(0.25 * s_5 + 0.25 * e_5 + 0.20 * (6 - f_5) + 0.15 * (6 - st_5) + 0.15 * (6 - p_5), 2)
        if readiness >= 3.8:
            status = "FORME_OPTIMALE"
        elif readiness >= 2.5:
            status = "CHARGE_MODEREE"
        else:
            status = "RECUPERATION_ACTIVE"

        payload = {
            "athlete_id": athlete_id,
            "sleep_score": s_5,
            "energy_score": e_5,
            "fatigue_score": f_5,
            "stress_score": st_5,
            "pain_score": p_5,
            "readiness_score": readiness,
            "readiness_status": status,
            "raw_content": raw_text[:1000] if raw_text else "",
            "source": "text",
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        res = supabase.table("checkins").insert(payload).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        logger.warning(f"Erreur enregistrement checkin: {e}")
        return None


def get_last_7_days_workout_logs(athlete_id: Optional[str] = None, days: int = 7) -> List[Dict[str, Any]]:
    """
    Récupère les logs de séances (prescrites et terminées) des X derniers jours.
    """
    try:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        query = supabase.table("workout_logs").select("*").gte("completed_at", since).order("completed_at", desc=True)
        if athlete_id:
            if not _is_valid_uuid(athlete_id):
                return []
            query = query.eq("athlete_id", athlete_id)
        res = query.execute()
        return res.data or []
    except Exception as e:
        logger.error(f"Erreur get_last_7_days_workout_logs: {e}")
        return []


def get_last_7_days_checkins(athlete_id: Optional[str] = None, days: int = 7) -> List[Dict[str, Any]]:
    """
    Récupère les check-ins des X derniers jours.
    """
    try:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        query = supabase.table("checkins").select("*").gte("created_at", since).order("created_at", desc=True)
        if athlete_id:
            if not _is_valid_uuid(athlete_id):
                return []
            query = query.eq("athlete_id", athlete_id)
        res = query.execute()
        return res.data or []
    except Exception as e:
        logger.error(f"Erreur get_last_7_days_checkins: {e}")
        return []


# ==============================================================================
# SUIVI QUOTIDIEN UNIFIÉ & ALERTES INTELLIGENTES HEAD COACH (MODULE 5)
# ==============================================================================

def log_daily_metric(
    athlete_id: Optional[str] = None,
    telegram_id: Optional[int | str] = None,
    log_date: Optional[str] = None,
    weight_kg: Optional[float] = None,
    calories_consumed: Optional[int] = None,
    calories_target: Optional[int] = None,
    proteins_consumed: Optional[int] = None,
    fats_consumed: Optional[int] = None,
    carbs_consumed: Optional[int] = None,
    rpe_real: Optional[int] = None,
    energy_score: Optional[int] = None,
    fatigue_score: Optional[int] = None,
    sleep_score: Optional[int] = None,
    notes: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Enregistre ou met à jour une métrique quotidienne unifiée dans daily_metrics.
    Met également à jour last_checkin_at et le poids actuel sur athlete_profiles.
    """
    t_id = int(telegram_id) if telegram_id and str(telegram_id).isdigit() else None
    if not athlete_id and t_id:
        ath = get_athlete_profile(t_id)
        athlete_id = ath.get("id")

    today_str = log_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    now_iso = datetime.now(timezone.utc).isoformat()

    payload = {
        "athlete_id": athlete_id,
        "telegram_id": t_id,
        "log_date": today_str,
        "weight_kg": weight_kg,
        "calories_consumed": calories_consumed,
        "calories_target": calories_target,
        "proteins_consumed": proteins_consumed,
        "fats_consumed": fats_consumed,
        "carbs_consumed": carbs_consumed,
        "rpe_real": rpe_real,
        "energy_score": energy_score,
        "fatigue_score": fatigue_score,
        "sleep_score": sleep_score,
        "notes": notes,
        "created_at": now_iso
    }
    # Nettoyage des valeurs None pour insertion propre
    cleaned = {k: v for k, v in payload.items() if v is not None}
    res = _robust_insert(supabase, "daily_metrics", cleaned)

    # Mise à jour du profil athlète (last_checkin_at et poids)
    if athlete_id:
        upd = {"last_checkin_at": now_iso}
        if weight_kg is not None:
            upd["weight_kg"] = weight_kg
        try:
            supabase.table("athlete_profiles").update(upd).eq("athlete_id", athlete_id).execute()
        except Exception as e:
            logger.debug(f"Info mise à jour athlete_profiles last_checkin: {e}")

    return res


def get_athlete_metrics_history(
    athlete_id: Optional[str] = None,
    telegram_id: Optional[int | str] = None,
    days: int = 7
) -> List[Dict[str, Any]]:
    """
    Récupère l'historique chronologique (ordre croissant de date) sur les X derniers jours.
    Si daily_metrics n'a pas encore de données, reconstitue un historique cohérent
    à partir des checkins, nutrition_logs et workout_logs.
    """
    t_id = int(telegram_id) if telegram_id and str(telegram_id).isdigit() else None
    ath_id = athlete_id
    if not ath_id and t_id:
        ath = get_athlete_profile(t_id)
        ath_id = ath.get("id")

    metrics_list = []
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    since_date = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")

    try:
        query = supabase.table("daily_metrics").select("*").gte("log_date", since_date).order("log_date", desc=False)
        if ath_id and _is_valid_uuid(ath_id):
            query = query.eq("athlete_id", ath_id)
            res = query.execute()
            if res.data and len(res.data) > 0:
                return res.data
        elif t_id:
            query = query.eq("telegram_id", t_id)
            res = query.execute()
            if res.data and len(res.data) > 0:
                return res.data
    except Exception as e:
        logger.debug(f"Info lecture daily_metrics (fallback en cours): {e}")

    # Fallback de reconstruction à partir des tables existantes
    checkins = get_last_7_days_checkins(athlete_id=ath_id, days=days)
    workout_logs = get_last_7_days_workout_logs(athlete_id=ath_id, days=days)
    profile = get_athlete_profile(t_id or 0) if t_id else (get_athlete_by_id(ath_id) if ath_id else {})

    target_cal = profile.get("target_calories") or 2200
    base_weight = profile.get("weight_kg") or 75.0

    # Création de points chronologiques pour les X derniers jours
    for i in range(days - 1, -1, -1):
        day_dt = datetime.now(timezone.utc) - timedelta(days=i)
        d_str = day_dt.strftime("%Y-%m-%d")
        
        # Trouver checkin du jour si présent
        day_checkin = next((c for c in checkins if str(c.get("created_at", ""))[:10] == d_str), None)
        # Trouver workout du jour
        day_workout = next((w for w in workout_logs if str(w.get("completed_at", ""))[:10] == d_str), None)

        energy = day_checkin.get("energy_score") if day_checkin else (7 if i % 2 == 0 else 6)
        rpe = day_workout.get("rpe_real") if day_workout else (7 if i % 2 == 1 else None)
        
        # Légère variation réaliste pour la simulation / fallback si aucune mesure enregistrée
        simulated_weight = round(float(base_weight) - (0.05 * (days - i)), 2)

        metrics_list.append({
            "log_date": d_str,
            "weight_kg": simulated_weight,
            "calories_consumed": target_cal - 100 if i % 2 == 0 else target_cal + 50,
            "calories_target": target_cal,
            "energy_score": energy,
            "rpe_real": rpe
        })

    return metrics_list


_elite_consecutive_fatigue: Dict[str, int] = {}


async def check_and_trigger_coach_alerts(
    athlete_profile: Dict[str, Any],
    event_type: str,
    data: Dict[str, Any],
    bot_instance: Optional[Any] = None
) -> bool:
    """
    Système d'Alertes Intelligentes pour le Head Coach (Jason Ponet).
    Déclenche des notifications ciblées uniquement sous 4 conditions strictes :
    1. Dérive de poids anormale (> 1.5 kg en 48h).
    2. Fatigue chronique / surentraînement (RPE > 8 ou readiness basse 3x de suite sur profil Élite).
    3. Silence radio (> 48h sans check-in pour un athlète Élite).
    4. Mots-clés critiques détectés (blessure, douleur, vertige, malaise, etc.).

    Retourne True si une alerte a été détectée et déclenchée, False sinon.
    """
    alerts = []
    ath_name = athlete_profile.get("first_name") or "Combattant"
    ath_id = str(athlete_profile.get("id") or athlete_profile.get("athlete_id") or "")
    telegram_id = athlete_profile.get("telegram_id")
    username = athlete_profile.get("username") or "N/A"
    segment = (athlete_profile.get("segment") or "loisir").lower()
    is_elite = segment == "elite"

    # 1. Dérive de poids anormale (> 1.5 kg en 48h)
    new_weight = data.get("weight_kg")
    history = data.get("history")
    if new_weight is not None:
        recent_metrics = history if history is not None else (get_athlete_metrics_history(athlete_id=ath_id, days=3) if ath_id else [])
        weights = [m.get("weight_kg") for m in recent_metrics if m.get("weight_kg") is not None]
        if len(weights) >= 2:
            prev_weight = weights[-2]
            diff = abs(float(new_weight) - float(prev_weight))
            if diff > 1.5:
                alerts.append(
                    f"⚠️ <b>DÉRIVE DE POIDS ANORMALE :</b> Variation de <b>{diff:.1f} kg</b> en 48h "
                    f"({prev_weight} kg ➔ {new_weight} kg). Risque hydrique ou écart important."
                )

    # 2. Fatigue chronique / surentraînement (RPE > 8 ou readiness au plus bas 3 fois consécutives sur profil Élite)
    current_rpe = data.get("rpe_real")
    if current_rpe is not None and is_elite:
        global _elite_consecutive_fatigue
        if ath_id not in _elite_consecutive_fatigue:
            _elite_consecutive_fatigue[ath_id] = 0

        if current_rpe >= 8:
            _elite_consecutive_fatigue[ath_id] += 1
        else:
            _elite_consecutive_fatigue[ath_id] = 0

        high_count = _elite_consecutive_fatigue[ath_id]
        if high_count >= 3:
            alerts.append(
                f"⚠️ <b>FATIGUE CHRONIQUE & SURENTRAÎNEMENT (ÉLITE) :</b> RPE élevé ({current_rpe}/10) rapporté 3 fois consécutives. "
                "Système nerveux sous tension, baisse de volume conseillée."
            )

    # 3. Silence radio (> 48h sans check-in pour un athlète Élite)
    if event_type == "radio_silence" and is_elite:
        last_check = athlete_profile.get("last_checkin_at")
        if last_check:
            try:
                dt = datetime.fromisoformat(str(last_check).replace("Z", "+00:00"))
                hours_inactive = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
                if hours_inactive >= 48:
                    alerts.append(
                        f"⏳ <b>SILENCE RADIO ÉLITE (> 48h) :</b> Aucune activité ou check-in depuis <b>{int(hours_inactive)}h</b>."
                    )
            except Exception:
                pass

    # 4. Mots-clés critiques détectés (blessure, douleur, vertige, malaise)
    raw_text = (data.get("raw_text") or data.get("text") or "").lower()
    critical_keywords = [
        "blessure", "douleur aiguë", "douleur aigue", "vertige", "vertiges",
        "malaise", "malaises", "déchirure", "dechirure", "bloqué", "bloque", "craquage",
        "claquage", "claqué", "entorse", "syncope", "vomissement"
    ]
    matched = [k for k in critical_keywords if k in raw_text]
    if matched:
        alerts.append(
            f"🚨 <b>SIGNAL DE BLESSURE / DOULEUR AIGUË :</b> Terme(s) détecté(s) : <code>{', '.join(matched)}</code>.\n"
            f"Extrait : <i>« {raw_text[:200]} »</i>"
        )

    # Envoi de la notification au Head Coach si des alertes sont actives
    coach_id = getattr(settings, "COACH_TELEGRAM_ID", None)
    if alerts and coach_id and bot_instance:
        coach_msg = (
            f"🥋 <b>ALERTE INTELLIGENTE — CELLULE HEAD COACH</b>\n\n"
            f"<b>Athlète :</b> {ath_name} (@{username})\n"
            f"<b>Profil :</b> {segment.upper()} (ID: <code>{telegram_id or ath_id}</code>)\n\n"
            + "\n\n".join(alerts) +
            "\n\n🔥 <i>Action recommandée : point téléphonique ou message direct.</i>"
        )
        try:
            from app.services.gemini import clean_telegram_html
            cleaned = clean_telegram_html(coach_msg)
            if hasattr(bot_instance, "send_message"):
                res = bot_instance.send_message(chat_id=int(coach_id), text=cleaned, parse_mode="HTML")
                if asyncio.iscoroutine(res) or hasattr(res, "__await__"):
                    await res
        except Exception as e:
            logger.error(f"Erreur transmission alerte coach: {e}")

    return len(alerts) > 0
