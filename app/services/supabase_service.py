from app.core.config import settings
from supabase import create_client, Client

supabase: Client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)

def get_available_exercises(equipment_needed: str = None):
    """
    Récupère la liste des exercices dans Supabase en filtrant selon le matériel disponible.
    """
    try:
        query = supabase.table("exercises").select("*")
        response = query.execute()
        all_exercises = response.data if response.data else []
        
        if not equipment_needed:
            return all_exercises

        eq_lower = equipment_needed.lower()
        
        # Filtre souple pour inclure le poids du corps + le matériel spécifié
        filtered = []
        for ex in all_exercises:
            ex_eq = str(ex.get("equipment", "")).lower()
            # Si l'exercice est poids du corps OR si son matériel est mentionné dans le check-in
            if "bodyweight" in ex_eq or "poids du corps" in ex_eq or ex_eq in eq_lower:
                filtered.append(ex)
                
        return filtered if filtered else all_exercises
    except Exception as e:
        print(f"Erreur Supabase Exercises: {e}")
        return []
