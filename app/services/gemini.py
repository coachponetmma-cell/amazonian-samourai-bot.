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
    Tu es le coach principal du Samourai Performance System.
    Analyse le message de check-in de l'athlète :
    - Extrais les notes (sommeil, énergie, fatigue) si mentionnées.
    - Repère le matériel disponible ou le contexte (hôtel, déplacement, KB, élastiques...).
    - Génère un retour humain, incisif et motivant adapté à un combattant MMA.

    Message de l'athlète : "{raw_text}"
    """
    response = _call_gemini_with_retry(prompt, schema=GeminiCheckinAnalysis)
    return response.parsed

def generate_daily_workout(analysis, exercises_list: list, athlete_profile: dict) -> str:
    prompt = f"""
    Tu es le Head Coach du Samourai Performance System.
    Génère la séance de prépa physique / MMA personnalisée pour l'athlète.

    INTERDICTIONS STRICTES DE FORMATAGE :
    - N'utilise JAMAIS les caractères '#', '##', '###' ni '**' pour le texte.
    - N'utilise QUE du HTML valide pour Telegram : <b>texte gras</b>, <i>texte italique</i>, et <a href="URL">Texte du lien</a>.

    GESTION DES EXERCICES ET DES LIENS VIDÉO :
    1. Utilise en priorité la liste d'exercices JSON Supabase suivante :
    {exercises_list}
    
    2. Pour CHAQUE exercice de la liste Supabase intégré à la séance, inclus obligatoirement son lien vidéo cliquable sous la forme :
       <a href="URL_PRESENTE_DANS_JSON">Nom de l'exercice</a>
       (Exemple : <a href="https://youtube.com/watch?v=xyz">Sprawls</a>)

    3. RÈGLE DE FALLBACK (MATÉRIEL PAS DANS LA BDD) :
       Si l'athlète mentionne du matériel absent de la BDD, crée l'exercice adapté mais N'INCLUS AUCUN LIEN (écris simplement le nom en gras : <b>Nom de l'exercice</b>).

    STRUCTURE SOUHAITÉE :
    <b>🥋 BLOC ÉCHAUFFEMENT & MOBILITÉ</b>
    • Détail des exercices avec leurs liens HTML...

    <b>💥 BLOC PRINCIPAL (FORCE / EXPLOSIVITÉ)</b>
    • Détail des exercices avec leurs liens HTML...

    <b>🥊 FINISSEUR CONDITIONNEMENT MMA</b>
    • Détail du circuit...

    <b>📊 CONSIGNES D'INTENSITÉ</b>
    • RPE et consignes...
    """
    response = _call_gemini_with_retry(prompt)
    return response.text
