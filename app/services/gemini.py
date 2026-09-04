
from google import genai
from google.genai import types
from app.core.config import settings
from app.schemas.checkin import GeminiCheckinAnalysis

client = genai.Client(api_key=settings.GEMINI_API_KEY)

def analyze_checkin_with_gemini(raw_text: str) -> GeminiCheckinAnalysis:
    prompt = f"""
    Tu es le coach principal IA du Samourai Performance System.
    Analyse le message de check-in de l athlete ci-dessous :
    - Extrais les notes sur une echelle de 1 a 10 si mentionnees.
    - Reperes si l athlete mentionne un equipement specifique ou une contrainte de materiel pour aujourd hui.
    - Genere un retour court, incisif et motivant adapte a un combattant MMA.

    Message de l athlete : "{raw_text}"
    """

    response = client.models.generate_content(
        model="gemini-3.6-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=GeminiCheckinAnalysis,
            temperature=0.2,
        ),
    )
    return response.parsed



def generate_daily_workout(analysis, exercises_list: list, athlete_profile: dict) -> str:
    prompt = f"""
    Tu es le Head Coach du Samourai Performance System.
    Génère la séance de prépa physique / MMA personnalisée pour l'athlète.

    CONTRAINTES STRICTES :
    1. Tu dois MENTIONNER ET UTILISER UNIQUEMENT les exercices présents dans la liste suivante issue de notre base de données Supabase :
    {exercises_list}

    2. Ne crée AUCUN exercice qui n'est pas dans cette liste.
    3. Adapte le volume et l'intensité selon les données du check-in :
       - Énergie : {analysis.energy_score}/10
       - Fatigue : {analysis.fatigue_score}/10
       - Matériel pour la séance : {analysis.equipment_available or athlete_profile.get('default_equipment', 'Poids du corps')}
       - Objectif athlète : {athlete_profile.get('goal', 'MMA / Combat')}

    STRUCTURE DE LA RÉPONSE :
    - 🥋 **Bloc Échauffement & Mobilité**
    - 💥 **Bloc Principal (Force / Explosivité)**
    - 🥊 **Finisseur Conditionnement MMA**
    - 📊 **Consignes d'intensité (RPE visé)**
    """

    response = client.models.generate_content(
        model='gemini-3.6-flash',
        contents=prompt
    )
    
    return response.text
