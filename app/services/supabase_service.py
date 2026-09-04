from app.core.config import settings
from supabase import create_client, Client

supabase: Client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)

def get_available_exercises(equipment_needed: str = None):
    """
    Récupère les exercices disponibles dans Supabase.
    Si du matériel est précisé ou si l'athlète est en poids du corps, filtre en conséquence.
    """
    query = supabase.table("exercises").select("name, category, equipment, description")
    
    if equipment_needed and ("poids du corps" in equipment_needed.lower() or "bodyweight" in equipment_needed.lower()):
        query = query.ilike("equipment", "%bodyweight%")
    
    response = query.execute()
    return response.data
