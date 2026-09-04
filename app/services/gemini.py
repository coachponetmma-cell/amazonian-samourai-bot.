
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
        model="gemini-2.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=GeminiCheckinAnalysis,
            temperature=0.2,
        ),
    )
    return response.parsed

