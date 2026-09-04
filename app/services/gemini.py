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
    Tu es le Head Coach du Samourai Performance System.
    Analyse le message de check-in de l'athlète :
    - Extrais les notes (sommeil, énergie, fatigue) si mentionnées.
    - Repère la liste EXACTE du matériel disponible ou les contraintes de lieu (ex: chambre d'hôtel sans matériel, 2 KB, élastique, salle complète...).
    - Génère un retour coach incisif, motivant et direct.

    Message de l'athlète : "{raw_text}"
    """
    response = _call_gemini_with_retry(prompt, schema=GeminiCheckinAnalysis)
    return response.parsed

def generate_daily_workout(analysis, exercises_list: list, athlete_profile: dict) -> str:
    prompt = f"""
    Tu es le Head Coach du Samourai Performance System.
    Génère la fiche de séance de prépa physique / MMA au format HTML structuré de haute précision.

    RÈGLES D'ADAPTATION AU MATÉRIEL ET LIEU (STRICTES) :
    1. Analyse impérativement l'environnement de l'athlète ({analysis.equipment_available or 'Poids du corps'}).
    2. SI L'ATHLÈTE EST EN CHÂMBRE D'HÔTEL / SANS MATÉRIEL : Propose UNIQUEMENT des exercices au poids du corps. INTERDICTION TOTALE d'inclure des exercices nécessitant une barre, des tractions, du landmine, ou des machines s'il n'y a pas accès.

    RÈGLES DES LIENS ET FORMATAGE HTML :
    1. N'utilise JAMAIS de caractères Markdown comme '#', '##', '###' ou '**'.
    2. Utilise uniquement du HTML valide : <b>Gras</b>, <i>Italique</i>, et <a href="URL">Lien</a>.
    3. Pour chaque exercice issu de la liste JSON Supabase suivante :
       {exercises_list}
       Affiche sous l'exercice la ligne : 🔗 <a href="URL_DU_LIEN">🎬 Voir la démonstration</a>.
    4. RÈGLE DE FALLBACK (Exercice créé par l'IA si matériel non présent en BDD) : Si l'exercice est créé en fallback, affiche simplement le nom en <b>Gras</b> sans mettre de ligne de démonstration vidéo.

    STRUCTURE EXIGÉE (Respecte exactement ce design) :

    <b>🥋 COACHING AMAZONIAN SAMOURAI</b>
    <b>Athlète :</b> {athlete_profile.get('first_name', 'Combattant')} | <b>Statut :</b> {analysis.energy_score}/10 Énergie

    <b>📋 CONDITIONING COMBAT & ADAPTATION</b>
    🎯 <b>Focus :</b> Transfert MMA & Explosivité
    ⏱️ <b>Durée estimée :</b> ~40 min

    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    🔥 <b>BLOC 1 : ÉCHAUFFEMENT & MOBILITÉ DYNAMIQUE</b>
    
    1.1 - <b>Nom de l'exercice 1</b>
       📊 Volume : X séries × Y reps
       ⚡️ Intensité : RPE X/10 | Tempo : Fluide | Repos : Xs
       💡 Consigne : Detail de la consigne...
       🔗 <a href="URL">🎬 Voir la démonstration</a>

    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    🔥 <b>BLOC 2 : CORPS DE SÉANCE & CONDITIONING</b>

    2.1 - <b>Nom de l'exercice 2</b>
       📊 Volume : X séries × Y reps
       ⚡️ Intensité : RPE X/10 | Repos : Xs
       💡 Consigne : Detail de la consigne...
       🔗 <a href="URL">🎬 Voir la démonstration</a>

    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    🔥 <b>BLOC 3 : FINISSEUR CONDITIONNEMENT MMA</b>

    3.1 - <b>Format Circuit / AMRAP / Intervallaire</b>
       📊 Consignes précises du finisseur...

    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    📊 <b>CONSIGNES D'INTENSITÉ (RPE VISÉ)</b>
    • Consigne globale du coach...

    👊 Après la séance, envoie ton débriefing vocal ou texte !
    🔥 Libertad & Performance.
    """
    response = _call_gemini_with_retry(prompt)
    return response.text
