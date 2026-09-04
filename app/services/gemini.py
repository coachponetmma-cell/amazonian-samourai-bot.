import time
from google import genai
from google.genai import types
from google.genai.errors import ServerError, APIError
from app.core.config import settings
from app.schemas.checkin import GeminiCheckinAnalysis

client = genai.Client(api_key=settings.GEMINI_API_KEY)

def _call_gemini_with_retry(prompt: str, schema=None):
    models_to_try = ["gemini-3.6-flash", "gemini-2.5-flash"]
    
    for model in models_to_try:
        for attempt in range(3):
            try:
                config_args = {"temperature": 0.2}
                if schema:
                    config_args["response_mime_type"] = "application/json"
                    config_args["response_schema"] = schema
                
                config = types.GenerateContentConfig(**config_args)
                response = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=config
                )
                return response
            except (ServerError, APIError) as e:
                if attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
                else:
                    break
    raise Exception("L'API Gemini est temporairement indisponible.")

def analyze_checkin_with_gemini(raw_text: str) -> GeminiCheckinAnalysis:
    prompt = f"""
    Tu es le coach principal IA du Samourai Performance System.
    Analyse le message de check-in de l'athlète ci-dessous :
    - Extrais les notes sur une échelle de 1 à 10 si mentionnées.
    - Repère si l'athlète mentionne un équipement spécifique ou une contrainte de matériel/lieu (ex: hôtel, déplacement, chez lui).
    - Génère un retour court, incisif et motivant adapté à un combattant MMA.

    Message de l'athlète : "{raw_text}"
    """
    response = _call_gemini_with_retry(prompt, schema=GeminiCheckinAnalysis)
    return response.parsed

def generate_daily_workout(analysis, exercises_list: list, athlete_profile: dict) -> str:
    prompt = f"""
    Tu es le Head Coach du Samourai Performance System.
    Génère la séance de prépa physique / MMA personnalisée pour l'athlète.

    CONTRAINTES STRICTES ET OBLIGATOIRES :
    1. Tu dois MENTIONNER ET UTILISER UNIQUEMENT les exercices présents dans la liste JSON ci-dessous issue de notre base de données Supabase :
    {exercises_list}

    2. Pour CHAQUE exercice cité dans la séance, tu DOIS obligatoirement inclure le nom de l'exercice ET son lien vidéo YouTube présent dans le champ 'video_url' ou 'url' sous la forme : 
       [Nom de l'exercice](URL_Video) ou Lien démonstration : URL_Video.
       Si une URL est absente dans le JSON, indique simplement le nom de l'exercice.

    3. Adapte le volume, les séries et l'intensité selon le check-in :
       - Énergie : {analysis.energy_score}/10
       - Fatigue : {analysis.fatigue_score}/10
       - Matériel / Environnement détecté : {analysis.equipment_available or athlete_profile.get('default_equipment', 'Poids du corps')}
       - Objectif : {athlete_profile.get('goal', 'MMA / Combat')}

    STRUCTURE DE LA RÉPONSE :
    - 🥋 **Bloc Échauffement & Mobilité**
    - 💥 **Bloc Principal (Force / Explosivité)**
    - 🥊 **Finisseur Conditionnement MMA**
    - 📊 **Consignes d'intensité (RPE visé)**
    """
    response = _call_gemini_with_retry(prompt)
    return response.text
