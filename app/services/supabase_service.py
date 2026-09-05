import re
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional
from supabase import create_client, Client
from app.core.config import settings

logger = logging.getLogger(__name__)

# Initialisation du client Supabase (clé service_role prioritaire pour les accès backend)
supabase_key = settings.SUPABASE_SERVICE_ROLE_KEY or settings.SUPABASE_KEY
supabase: Client = create_client(settings.SUPABASE_URL, supabase_key)


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
    Vérifie si un athlète existe et possède un profil configuré dans athlete_profiles.
    """
    try:
        t_id = int(telegram_id)
        ath_resp = supabase.table("athletes").select("id").eq("telegram_id", t_id).execute()
        if not ath_resp.data:
            return False
        athlete_id = ath_resp.data[0]["id"]
        prof_resp = supabase.table("athlete_profiles").select("athlete_id").eq("athlete_id", athlete_id).execute()
        return bool(prof_resp.data and len(prof_resp.data) > 0)
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
    raw_user_last_name: Optional[str] = None
) -> Dict[str, Any]:
    """
    Enregistre ou met à jour le profil d'un nouvel athlète suite au tunnel d'onboarding.
    """
    t_id = int(telegram_id)
    clean_name = athlete_name.strip()
    parts = clean_name.split(maxsplit=1)
    first_name = parts[0]
    last_name = parts[1] if len(parts) > 1 else (raw_user_last_name or "")

    ath_id = None
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
        logger.error(f"Erreur table athletes lors de l'onboarding ({t_id}): {e}")

    if not ath_id:
        ath_id = f"local-{t_id}"

    prof_data = {
        "athlete_id": ath_id,
        "goal": goal,
        "default_equipment": default_equipment,
        "injuries_history": injuries,
        "language": "fr"
    }
    try:
        supabase.table("athlete_profiles").upsert(prof_data, on_conflict="athlete_id").execute()
    except Exception as e:
        logger.error(f"Erreur upsert athlete_profiles ({ath_id}): {e}")

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
        "status": "active"
    }


def get_athlete_by_telegram_id(telegram_id: int | str) -> Dict[str, Any]:
    """
    Récupère les données de l'athlète et son profil lié via son telegram_id.
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
        "status": "active"
    }

    try:
        t_id = int(telegram_id)
        ath_resp = supabase.table("athletes").select("*").eq("telegram_id", t_id).execute()
        if ath_resp.data and len(ath_resp.data) > 0:
            ath = ath_resp.data[0]
            ath_id = ath.get("id")
            prof_resp = supabase.table("athlete_profiles").select("*").eq("athlete_id", ath_id).execute()
            prof = prof_resp.data[0] if prof_resp.data else {}

            return {
                "id": ath_id,
                "athlete_id": ath_id,
                "first_name": ath.get("first_name") or prof.get("first_name") or "Combattant",
                "last_name": ath.get("last_name", ""),
                "username": ath.get("username"),
                "telegram_id": t_id,
                "goal": prof.get("goal") or ath.get("main_objective") or ath.get("goal") or "MMA / Combat",
                "default_equipment": prof.get("default_equipment") or ath.get("available_equipment") or "Poids du corps",
                "injuries_history": prof.get("injuries_history") or ath.get("injuries") or "aucune",
                "status": ath.get("status", "active")
            }

        # Si non trouvé par telegram_id, tente de récupérer le premier profil existant
        prof_resp = supabase.table("athlete_profiles").select("*").limit(1).execute()
        if prof_resp.data:
            p = prof_resp.data[0]
            default_profile.update(p)
            default_profile["id"] = p.get("athlete_id")
    except Exception as e:
        logger.warning(f"Erreur recherche athlète {telegram_id}: {e}")

    return default_profile


def get_athlete_by_id(athlete_id: str) -> Dict[str, Any]:
    """
    Récupère l'athlète et son profil par son identifiant UUID Supabase.
    """
    try:
        ath_resp = supabase.table("athletes").select("*").eq("id", athlete_id).execute()
        ath = ath_resp.data[0] if ath_resp.data else {}
        prof_resp = supabase.table("athlete_profiles").select("*").eq("athlete_id", athlete_id).execute()
        prof = prof_resp.data[0] if prof_resp.data else {}

        return {
            "id": athlete_id,
            "athlete_id": athlete_id,
            "first_name": ath.get("first_name", "Combattant"),
            "last_name": ath.get("last_name", ""),
            "username": ath.get("username"),
            "telegram_id": ath.get("telegram_id"),
            "goal": prof.get("goal") or ath.get("goal") or "MMA / Combat",
            "default_equipment": prof.get("default_equipment", "Poids du corps"),
            "injuries_history": prof.get("injuries_history", "aucune"),
            "status": ath.get("status", "active")
        }
    except Exception as e:
        logger.warning(f"Erreur get_athlete_by_id {athlete_id}: {e}")
        return {"id": athlete_id, "athlete_id": athlete_id, "first_name": "Athlète"}


def get_all_athletes() -> List[Dict[str, Any]]:
    """
    Liste tous les athlètes enregistrés dans Supabase.
    """
    try:
        resp = supabase.table("athletes").select("*").execute()
        return resp.data or []
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
            query = query.eq("athlete_id", athlete_id)
        res = query.execute()
        return res.data or []
    except Exception as e:
        logger.error(f"Erreur get_last_7_days_checkins: {e}")
        return []
