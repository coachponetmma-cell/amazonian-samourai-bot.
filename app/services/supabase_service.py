from app.core.config import settings
from supabase import create_client, Client

supabase: Client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)

def get_available_exercises(equipment_needed: str = None):
    """
    Récupère les exercices dans Supabase sans planter sur des noms de colonnes.
    """
    try:
        query = supabase.table("exercises").select("*")
        response = query.execute()
        return response.data if response.data else []
    except Exception as e:
        print(f"Erreur Supabase Exercises: {e}")
        return []
