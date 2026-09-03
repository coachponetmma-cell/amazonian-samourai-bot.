import json
import logging
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field

from app.core.config import settings
from app.models.schemas import DailyCheckinInput

logger = logging.getLogger(__name__)


class ExtractedCheckinData(BaseModel):
    sleep_score: int = Field(default=3, ge=1, le=5, description="QualitÃ© du sommeil (1: TrÃ¨s mauvais Ã  5: Excellent)")
    energy_score: int = Field(default=3, ge=1, le=5, description="Niveau d'Ã©nergie au rÃ©veil (1: Ã€ plat Ã  5: Explosif)")
    fatigue_score: int = Field(default=3, ge=1, le=5, description="Niveau de fatigue perÃ§ue (1: Aucune Ã  5: Ã‰puisement)")
    stress_score: int = Field(default=3, ge=1, le=5, description="Niveau de stress / charge mentale (1: Zen Ã  5: ExtrÃªme)")
    soreness_score: int = Field(default=1, ge=1, le=5, description="Niveau de courbatures ou douleur (1: Aucune Ã  5: Douleur aiguÃ«)")
    soreness_locations: list[str] = Field(default_factory=list, description="Liste des zones douloureuses (ex: ['epaule_droite', 'genou_gauche'])")
    transcript: str = Field(default="", description="Transcription fidÃ¨le du message audio ou rÃ©sumÃ© du texte")
    notes: Optional[str] = Field(default="", description="Remarques additionnelles de l'athlÃ¨te")


CHECKIN_EXTRACTION_PROMPT = """
Tu es l'agent IA de Check-In pour le systÃ¨me de coaching de Jason Ponet (Amazonian Samourai).
Ton rÃ´le est d'analyser le message vocal ou textuel d'un athlÃ¨te et d'en extraire avec une rigueur scientifique les variables de Readiness (sur une Ã©chelle entiÃ¨re de 1 Ã  5) :

1. sleep_score (1=Catastrophique/trÃ¨s peu dormi, 3=Moyen/normal, 5=Sommeil rÃ©parateur parfait)
2. energy_score (1=VidÃ©/aucun jus, 3=Correct, 5=PrÃªt Ã  tout casser/explosif)
3. fatigue_score (1=Frais comme un gardon, 3=Fatigue modÃ©rÃ©e, 5=Ã‰puisement total)
4. stress_score (1=Calme total, 3=Stress normal, 5=Surmenage mental/stress pro aigu)
5. soreness_score (1=Aucune courbature, 3=Courbatures normales post-training, 4-5=Douleur aiguÃ«/blessure suspectÃ©e)
6. soreness_locations (liste des zones physiques mentionnÃ©es, ex: ["genou_gauche", "epaule_droite", "lombaires"])
7. transcript (retranscription ou rÃ©sumÃ© clair des propos)
8. notes (remarques sur les sensations, envies de sÃ©ance, ou contraintes du jour)

RÃˆGLES D'EXTRACTION :
- Si l'athlÃ¨te dit "J'ai trop mal Ã  l'Ã©paule droite, c'est bloquÃ©", soreness_score = 4 ou 5, soreness_locations = ["epaule_droite"].
- Si l'athlÃ¨te dit "Super nuit, en pleine forme, aucun bobo", sleep_score = 5, energy_score = 5, fatigue_score = 1, stress_score = 1, soreness_score = 1.
- Si une valeur n'est pas explicitement mentionnÃ©e, dÃ©duis-la logiquement du contexte (par dÃ©faut: 3).

RÃ©ponds EXCLUSIVEMENT au format JSON valide respectant le schÃ©ma attendu.
"""


class GeminiService:
    """
    Service d'interaction avec l'API Google Gemini 3.6 Flash.
    """

    @classmethod
    def get_client(cls):
        api_key = settings.GEMINI_API_KEY
        if not api_key or "your-gemini" in api_key:
            logger.warning("ClÃ© GEMINI_API_KEY non configurÃ©e.")
            return None
        try:
            from google import genai
            return genai.Client(api_key=api_key)
        except Exception as e:
            logger.error(f"Impossible d'instancier le client Gemini: {e}")
            return None

    @classmethod
    async def parse_audio_checkin(cls, audio_bytes: bytes, mime_type: str = "audio/ogg") -> DailyCheckinInput:
        """
        Transcrit un audio Telegram (.ogg) et extrait les donnÃ©es de check-in via Gemini 3.6 Flash.
        """
        client = cls.get_client()
        if client is None:
            return DailyCheckinInput(
                sleep_score=4,
                energy_score=4,
                fatigue_score=2,
                stress_score=2,
                soreness_score=1,
                raw_text="[Mode local : check-in simulÃ© en forme]"
            )

        try:
            from google.genai import types

            response = client.models.generate_content(
                model="gemini-3.6-flash",
                contents=[
                    types.Part.from_bytes(data=audio_bytes, mime_type=mime_type),
                    CHECKIN_EXTRACTION_PROMPT
                ],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=ExtractedCheckinData,
                    temperature=0.1
                )
            )

            parsed = json.loads(response.text)
            return DailyCheckinInput(
                sleep_score=parsed.get("sleep_score", 3),
                energy_score=parsed.get("energy_score", 3),
                fatigue_score=parsed.get("fatigue_score", 3),
                stress_score=parsed.get("stress_score", 3),
                soreness_score=parsed.get("soreness_score", 1),
                soreness_locations=parsed.get("soreness_locations", []),
                raw_text=parsed.get("transcript", "")
            )

        except Exception as e:
            logger.error(f"Erreur lors du traitement audio par Gemini: {e}")
            return DailyCheckinInput(
                sleep_score=3,
                energy_score=3,
                fatigue_score=3,
                stress_score=3,
                soreness_score=1,
                raw_text=f"[Erreur transcription audio: {str(e)}]"
            )

    @classmethod
    async def generate_coach_summary(cls, prompt: str) -> str:
        """GÃ©nÃ¨re un rÃ©sumÃ© structurÃ© pour le coach."""
        client = cls.get_client()
        if client is None:
            return "Service Gemini indisponible - impossible de gÃ©nÃ©rer le rapport."
            
        try:
            from google.genai import types
            
            system_instruction = """Tu es un assistant coach pour Amazonian Samourai. 
            GÃ©nÃ¨re des rapports concis et actionnables en franÃ§ais avec :
            1. Analyse des tendances (readiness, RPE)
            2. Points d'attention physique
            3. Recommandations spÃ©cifiques
            Utilise des listes Ã  puces et un style direct."""
            
            response = client.models.generate_content(
                model="gemini-3.6-flash",
                contents=[prompt],
                config=types.GenerateContentConfig(
                    response_mime_type="text/plain",
                    temperature=0.3
                ),
                system_instruction=system_instruction
            )
            return response.text
        except Exception as e:
            logger.error(f"Erreur gÃ©nÃ©ration rapport coach: {e}")
            return f"Erreur lors de la gÃ©nÃ©ration du rapport: {str(e)}"

    @classmethod
    async def parse_text_checkin(cls, text: str) -> DailyCheckinInput:
        """
        Extrait les donnÃ©es de check-in Ã  partir d'un message texte libre via Gemini 3.6 Flash.
        """
        client = cls.get_client()
        if client is None:
            return DailyCheckinInput(
                sleep_score=3,
                energy_score=3,
                fatigue_score=3,
                stress_score=3,
                soreness_score=1,
                raw_text=text
            )

        try:
            from google.genai import types

            prompt = f"{CHECKIN_EXTRACTION_PROMPT}\n\nMessage de l'athlÃ¨te :\n\"\"\"\n{text}\n\"\"\""

            response = client.models.generate_content(
                model="gemini-3.6-flash",
                contents=[prompt],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=ExtractedCheckinData,
                    temperature=0.1
                )
            )

            parsed = json.loads(response.text)
            return DailyCheckinInput(
                sleep_score=parsed.get("sleep_score", 3),
                energy_score=parsed.get("energy_score", 3),
                fatigue_score=parsed.get("fatigue_score", 3),
                stress_score=parsed.get("stress_score", 3),
                soreness_score=parsed.get("soreness_score", 1),
                soreness_locations=parsed.get("soreness_locations", []),
                raw_text=parsed.get("transcript") or text
            )

        except Exception as e:
            logger.error(f"Erreur lors du parsing texte par Gemini: {e}")
            return DailyCheckinInput(
                sleep_score=3,
                energy_score=3,
                fatigue_score=3,
                stress_score=3,
                soreness_score=1,
                raw_text=text
            )





    def generate_summary(self, profile, logs):
        first_name = getattr(profile, 'first_name', '') or 'Athlète'
        goal = getattr(profile, 'goal', 'Non spécifié') or 'Non spécifié'
        prompt = f"Tu es le coach IA principal Amazonian Samourai. Rédige une synthèse de coaching claire, motivante et dynamique pour {first_name}. Objectif: {goal}. Logs des 7 derniers entraînements: {logs}"
        
        # Liste des modèles valides affichés sur ton AI Studio par ordre de priorité
        models_to_try = ['gemini-3.5-flash-lite', 'gemini-3.1-flash-lite', 'gemini-2.5-flash-lite', 'gemini-1.5-flash']
        
        import google.generativeai as genai
        for m_name in models_to_try:
            try:
                model = genai.GenerativeModel(m_name)
                res = model.generate_content(prompt)
                if res and hasattr(res, 'text') and res.text:
                    return res.text
            except Exception:
                continue

        return f"🥊 Bilan pour {first_name}: {len(logs)} séance(s) analysée(s)."
